"""Background polling + out-of-band delivery for long-running A2A tasks."""

import asyncio
import logging

from slack_agents.a2a.client import classify
from slack_agents.storage.base import BaseStorageProvider

logger = logging.getLogger(__name__)


class AsyncTaskManager:
    """Owns one provider's in-flight A2A tasks: poll to completion, then deliver."""

    def __init__(
        self,
        server_key,
        client,
        storage: BaseStorageProvider,
        deliver,
        poll_interval: float = 5,
        max_lifetime: float = 3600,
        client_factory=None,
        framework_ctx=None,
    ):
        self._ns = f"a2a:inflight:{server_key}"
        self._client = client
        self._client_factory = client_factory
        self._storage = storage
        # ASYNC callable(**{channel_id, thread_id, user_id, text, is_error, files})
        self._deliver = deliver
        self._poll_interval = poll_interval
        self._max_lifetime = max_lifetime
        self._framework_ctx = framework_ctx
        self._tasks: dict[str, asyncio.Task] = {}

    async def track(self, record: dict) -> None:
        await self._storage.set(self._ns, record["task_id"], record)
        self._spawn(record)

    async def resume(self) -> None:
        records = await self._storage.query(self._ns, {})
        for record in records:
            if record.get("task_id") and record["task_id"] not in self._tasks:
                self._spawn(record)
        if records:
            logger.info("A2A %s: resumed %d in-flight task(s)", self._ns, len(records))

    def _spawn(self, record: dict) -> None:
        tid = record["task_id"]
        self._tasks[tid] = asyncio.create_task(self._run(record))

    async def _run(self, record: dict) -> None:
        tid = record["task_id"]
        elapsed = 0.0
        client = self._client
        owns_client = False
        try:
            if client is None and self._client_factory is not None:
                client = await self._client_factory(record)
                owns_client = True
            while True:
                r = await client.get_task(tid)
                bucket = classify(r.state)
                if bucket != "non_terminal":
                    is_error = r.state in ("failed", "canceled", "rejected")
                    text = r.text or (
                        "(task ended without output)"
                        if not is_error
                        else f"task ended in state {r.state}"
                    )
                    if r.files:
                        from slack_agents.a2a.artifacts import files_to_llm_text  # noqa: PLC0415

                        registry = getattr(self._framework_ctx, "file_registry", None)
                        ucc = {
                            "user_id": record["user_id"],
                            "channel_id": record["channel_id"],
                            "thread_id": record["thread_id"],
                        }
                        extra = await files_to_llm_text(r.files, registry, ucc, self._storage)
                        if extra:
                            text = f"{text}\n\n{extra}"
                    await self._deliver(
                        channel_id=record["channel_id"],
                        thread_id=record["thread_id"],
                        user_id=record["user_id"],
                        text=text,
                        is_error=is_error,
                        files=r.files or [],
                    )
                    return
                if elapsed >= self._max_lifetime:
                    await self._deliver(
                        channel_id=record["channel_id"],
                        thread_id=record["thread_id"],
                        user_id=record["user_id"],
                        text="The long-running task did not finish in time and was abandoned.",
                        is_error=True,
                        files=[],
                    )
                    return
                await asyncio.sleep(self._poll_interval)
                elapsed += self._poll_interval
        except asyncio.CancelledError:
            raise
        except Exception as e:
            from slack_agents.oauth.errors import (  # noqa: PLC0415
                ReauthRequired,
                flatten_exceptions,
            )

            if any(isinstance(x, ReauthRequired) for x in flatten_exceptions(e)):
                logger.info(
                    "A2A %s: task %s needs re-auth; delivering session-expired", self._ns, tid
                )
                await self._deliver(
                    channel_id=record["channel_id"],
                    thread_id=record["thread_id"],
                    user_id=record["user_id"],
                    text=(
                        "Your session for this task has expired, so I couldn't finish it. "
                        "Please ask again and re-authenticate when prompted."
                    ),
                    is_error=True,
                    files=[],
                )
            else:
                logger.exception("A2A %s: poller for task %s crashed", self._ns, tid)
        finally:
            if owns_client and client is not None:
                try:
                    await client.close()
                except Exception:
                    logger.debug("A2A %s: per-user client close failed", self._ns, exc_info=True)
            await self._storage.delete(self._ns, tid)
            self._tasks.pop(tid, None)

    async def wait_idle(self) -> None:
        """Test helper: await all currently-running pollers."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def stop(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
        self._tasks.clear()
