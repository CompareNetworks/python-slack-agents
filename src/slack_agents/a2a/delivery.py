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
    ):
        self._ns = f"a2a:inflight:{server_key}"
        self._client = client
        self._storage = storage
        # ASYNC callable(**{channel_id, thread_id, user_id, text, is_error})
        self._deliver = deliver
        self._poll_interval = poll_interval
        self._max_lifetime = max_lifetime
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
        try:
            while True:
                r = await self._client.get_task(tid)
                bucket = classify(r.state)
                if bucket != "non_terminal":
                    is_error = r.state in ("failed", "canceled", "rejected")
                    text = r.text or (
                        "(task ended without output)"
                        if not is_error
                        else f"task ended in state {r.state}"
                    )
                    await self._deliver(
                        channel_id=record["channel_id"],
                        thread_id=record["thread_id"],
                        user_id=record["user_id"],
                        text=text,
                        is_error=is_error,
                    )
                    return
                if elapsed >= self._max_lifetime:
                    await self._deliver(
                        channel_id=record["channel_id"],
                        thread_id=record["thread_id"],
                        user_id=record["user_id"],
                        text="The long-running task did not finish in time and was abandoned.",
                        is_error=True,
                    )
                    return
                await asyncio.sleep(self._poll_interval)
                elapsed += self._poll_interval
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("A2A %s: poller for task %s crashed", self._ns, tid)
        finally:
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
