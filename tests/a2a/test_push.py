"""A2A push handler: parsing, dedup, token validation, correlation, delivery.

Body fixtures mirror the real protobuf-JSON shapes captured from the live
guessing-game agent.
"""

import base64

import pytest

from slack_agents.a2a import push
from slack_agents.storage.sqlite import Provider as Sqlite

CSV_B64 = base64.b64encode(b"attempt,guess,result\r\n1,5,higher\r\n").decode()


def status_body(task_id="T", message_id="M", text="hi", state="TASK_STATE_INPUT_REQUIRED"):
    return {
        "statusUpdate": {
            "taskId": task_id,
            "contextId": "C",
            "status": {
                "state": state,
                "message": {
                    "messageId": message_id,
                    "role": "ROLE_AGENT",
                    "parts": [{"text": text}],
                },
            },
        }
    }


def artifact_body(task_id="T", artifact_id="A", b64=CSV_B64, name="file_report.csv"):
    return {
        "artifactUpdate": {
            "taskId": task_id,
            "contextId": "C",
            "artifact": {
                "artifactId": artifact_id,
                "name": name,
                "parts": [{"raw": b64, "filename": name, "mediaType": "text/csv"}],
            },
        }
    }


def task_body(task_id="T"):
    return {"task": {"id": task_id, "contextId": "C", "status": {"state": "TASK_STATE_SUBMITTED"}}}


# --- pure parsing ---------------------------------------------------------


def test_push_task_id_for_each_kind():
    assert push.push_task_id(status_body(task_id="T1")) == "T1"
    assert push.push_task_id(artifact_body(task_id="T2")) == "T2"
    assert push.push_task_id(task_body(task_id="T3")) == "T3"
    assert push.push_task_id({"junk": {}}) is None


def test_extract_status_message_item():
    items = push.extract_items(status_body(message_id="M9", text="lower"))
    assert items == [{"id": "M9", "kind": "text", "text": "lower"}]


def test_extract_artifact_file_item_decodes_base64():
    items = push.extract_items(artifact_body(artifact_id="A9"))
    assert len(items) == 1
    it = items[0]
    assert it["id"] == "A9:0" and it["kind"] == "file"
    assert it["file"]["filename"] == "file_report.csv"
    assert it["file"]["mimeType"] == "text/csv"
    assert it["file"]["data"].startswith(b"attempt,guess,result")


def test_extract_task_snapshot_with_no_message_yields_nothing():
    assert push.extract_items(task_body()) == []


# --- handler --------------------------------------------------------------


class FakeSlack:
    def __init__(self):
        self.posts = []
        self.uploads = []

    async def chat_postMessage(self, **kw):
        self.posts.append(kw)

    async def files_upload_v2(self, **kw):
        self.uploads.append(kw)


class Ctx:
    def __init__(self, storage, slack):
        self.storage = storage
        self.slack_client = slack


class FakeReq:
    def __init__(self, body, app, token=None):
        self._body = body
        self.app = app
        self.headers = {"X-A2A-Notification-Token": token} if token else {}
        self.remote = "127.0.0.1"

    async def json(self):
        return self._body


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


async def _app(store):
    slack = FakeSlack()
    return {"a2a_push_ctx": Ctx(store, slack)}, slack


async def test_unknown_task_is_silent_noop(store):
    app, slack = await _app(store)
    resp = await push.handle_push(FakeReq(status_body(task_id="nope"), app, token="t"))
    assert resp.status == 200
    assert slack.posts == []


async def test_bad_token_rejected(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="right")
    app, slack = await _app(store)
    resp = await push.handle_push(FakeReq(status_body(task_id="T"), app, token="wrong"))
    assert resp.status == 401
    assert slack.posts == []


async def test_already_delivered_id_is_skipped(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    rec = await store.get(push.PUSH_NS, "T")
    await push.mark_delivered(store, "T", rec, ["Msync"])
    app, slack = await _app(store)
    # the server re-pushes the immediate reply with the SAME messageId
    resp = await push.handle_push(
        FakeReq(status_body(task_id="T", message_id="Msync"), app, token="t")
    )
    assert resp.status == 200
    assert slack.posts == []  # not re-delivered


async def test_new_status_text_delivered_once(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    app, slack = await _app(store)
    body = status_body(task_id="T", message_id="Mnew", text="received 1 file(s)")
    await push.handle_push(FakeReq(body, app, token="t"))
    await push.handle_push(FakeReq(body, app, token="t"))  # duplicate push
    assert len(slack.posts) == 1
    assert slack.posts[0]["text"] == "received 1 file(s)"
    assert slack.posts[0]["thread_ts"] == "Th"


async def test_artifact_file_uploaded(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    app, slack = await _app(store)
    await push.handle_push(FakeReq(artifact_body(task_id="T", artifact_id="A1"), app, token="t"))
    assert len(slack.uploads) == 1
    up = slack.uploads[0]
    assert up["filename"] == "file_report.csv"
    assert up["content"].startswith(b"attempt,guess,result")
    assert up["thread_ts"] == "Th"


# --- terminal-state detection + completion reaction -----------------------


class ReactCtx(Ctx):
    """Ctx that records LLM completion reactions and exposes a file registry."""

    def __init__(self, storage, slack, file_registry=None):
        super().__init__(storage, slack)
        self.file_registry = file_registry
        self.reacted = []

    async def deliver_async_result(self, **kw):
        self.reacted.append(kw)


def test_push_is_terminal_detects_terminal_states():
    assert push.push_is_terminal(status_body(state="TASK_STATE_COMPLETED"))
    assert push.push_is_terminal({"task": {"status": {"state": "TASK_STATE_FAILED"}}})
    assert not push.push_is_terminal(status_body(state="TASK_STATE_WORKING"))
    assert not push.push_is_terminal(artifact_body())


def test_push_is_actionable_includes_interrupted_not_progress():
    assert push.push_is_actionable(status_body(state="TASK_STATE_COMPLETED"))
    assert push.push_is_actionable(status_body(state="TASK_STATE_FAILED"))
    assert push.push_is_actionable(status_body(state="TASK_STATE_INPUT_REQUIRED"))
    assert push.push_is_actionable(status_body(state="TASK_STATE_AUTH_REQUIRED"))
    assert not push.push_is_actionable(status_body(state="TASK_STATE_WORKING"))
    assert not push.push_is_actionable(status_body(state="TASK_STATE_SUBMITTED"))
    assert not push.push_is_actionable(artifact_body())  # stateless


async def test_terminal_push_fires_one_reaction(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    ctx = ReactCtx(store, FakeSlack())
    app = {"a2a_push_ctx": ctx}
    body = status_body(
        task_id="T", message_id="Mdone", text="all done", state="TASK_STATE_COMPLETED"
    )
    resp = await push.handle_push(FakeReq(body, app, token="t"))
    assert resp.status == 200
    assert len(ctx.reacted) == 1  # exactly one wrap-up reaction
    assert "all done" in ctx.reacted[0]["text"]
    assert ctx.reacted[0]["is_error"] is False
    assert ctx.reacted[0]["files"] == []  # already uploaded by the loop; not re-sent
    assert "Mdone" in (await store.get(push.PUSH_NS, "T"))["reacted_ids"]


async def test_failed_terminal_push_marks_is_error(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    ctx = ReactCtx(store, FakeSlack())
    app = {"a2a_push_ctx": ctx}
    body = status_body(task_id="T", message_id="Mf", text="boom", state="TASK_STATE_FAILED")
    await push.handle_push(FakeReq(body, app, token="t"))
    assert ctx.reacted[0]["is_error"] is True


async def test_terminal_push_skips_reaction_when_already_reacted(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    rec = await store.get(push.PUSH_NS, "T")
    rec["reacted_ids"] = ["Magain"]  # this status was already reacted to
    await store.set(push.PUSH_NS, "T", rec)
    ctx = ReactCtx(store, FakeSlack())
    app = {"a2a_push_ctx": ctx}
    body = status_body(task_id="T", message_id="Magain", text="dup", state="TASK_STATE_COMPLETED")
    await push.handle_push(FakeReq(body, app, token="t"))
    assert ctx.reacted == []  # guarded: no double-react on the same status


async def test_content_free_terminal_push_still_reacts(store):
    # A push agent streams content, then closes with a bare COMPLETED status (no
    # message, no artifact). extract_items() -> [] so there are no new items, but the
    # wrap-up reaction must still fire exactly once.
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    ctx = ReactCtx(store, FakeSlack())
    app = {"a2a_push_ctx": ctx}
    body = {"statusUpdate": {"taskId": "T", "status": {"state": "TASK_STATE_COMPLETED"}}}
    resp = await push.handle_push(FakeReq(body, app, token="t"))
    assert resp.status == 200
    assert len(ctx.reacted) == 1
    assert ctx.reacted[0]["text"] == "(task complete)"
    assert "state:TASK_STATE_COMPLETED" in (await store.get(push.PUSH_NS, "T"))["reacted_ids"]


async def test_working_push_does_not_react_but_accumulates(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    ctx = ReactCtx(store, FakeSlack())
    app = {"a2a_push_ctx": ctx}
    body = status_body(
        task_id="T", message_id="P1", text="Generating: 1/4", state="TASK_STATE_WORKING"
    )
    await push.handle_push(FakeReq(body, app, token="t"))
    assert ctx.reacted == []  # progress never triggers an LLM turn
    assert ctx.slack_client.posts[0]["text"] == "Generating: 1/4"  # still shown to the user
    assert "Generating: 1/4" in (await store.get(push.PUSH_NS, "T"))["llm_context"]  # accumulated


async def test_input_required_push_reacts(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    ctx = ReactCtx(store, FakeSlack())
    app = {"a2a_push_ctx": ctx}
    body = status_body(
        task_id="T",
        message_id="Q1",
        text="Which competitor should I focus on?",
        state="TASK_STATE_INPUT_REQUIRED",
    )
    await push.handle_push(FakeReq(body, app, token="t"))
    assert len(ctx.reacted) == 1
    assert ctx.reacted[0]["is_error"] is False
    assert "Which competitor" in ctx.reacted[0]["text"]


async def test_input_required_then_completed_react_twice(store):
    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    ctx = ReactCtx(store, FakeSlack())
    app = {"a2a_push_ctx": ctx}
    # 1) agent blocks mid-task asking for input -> first reaction
    await push.handle_push(
        FakeReq(
            status_body(
                task_id="T", message_id="Q1", text="pick one?", state="TASK_STATE_INPUT_REQUIRED"
            ),
            app,
            token="t",
        )
    )
    # 2) later it finishes -> second, distinct reaction (one-shot guard would have suppressed this)
    await push.handle_push(
        FakeReq(
            status_body(
                task_id="T", message_id="Done", text="report ready", state="TASK_STATE_COMPLETED"
            ),
            app,
            token="t",
        )
    )
    assert len(ctx.reacted) == 2
    assert "pick one?" in ctx.reacted[0]["text"]
    assert "report ready" in ctx.reacted[1]["text"]
    assert "pick one?" not in ctx.reacted[1]["text"]  # flushed; not re-fed


async def test_pushed_artifact_accumulates_into_terminal_reaction(store):
    # The core fix: an artifact pushed BEFORE the terminal status must still reach the
    # LLM as context on the wrap-up turn (artifact arrives in its own push, separate
    # from the terminal status body).
    from slack_agents.files import FileHandlerRegistry
    from slack_agents.tools.file_importer import Provider as FileImporter

    await push.save_record(store, "T", channel_id="C1", thread_id="Th", user_id="U", token="t")
    ctx = ReactCtx(store, FakeSlack(), file_registry=FileHandlerRegistry([FileImporter([".*"])]))
    app = {"a2a_push_ctx": ctx}
    # 1) the report artifact (stateless artifactUpdate) — uploaded, accumulated, no reaction
    await push.handle_push(FakeReq(artifact_body(task_id="T", artifact_id="A1"), app, token="t"))
    assert ctx.reacted == []
    assert len(ctx.slack_client.uploads) == 1  # report file delivered to the thread
    # 2) the terminal completed status (no file in its body) — reaction flushes the buffer
    await push.handle_push(
        FakeReq(
            status_body(
                task_id="T", message_id="Done", text="Report ready.", state="TASK_STATE_COMPLETED"
            ),
            app,
            token="t",
        )
    )
    assert len(ctx.reacted) == 1
    text = ctx.reacted[0]["text"]
    assert "Report ready." in text  # the terminal status text
    assert "attempt,guess,result" in text  # the artifact's extracted content (from earlier push)
    assert "Do NOT repeat" in text  # tagged 'already shown'
    assert ctx.reacted[0]["files"] == []  # not re-uploaded
