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
