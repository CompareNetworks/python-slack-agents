"""File handling at the client/protobuf boundary (real a2a-sdk protobuf objects)."""

import a2a.types.a2a_pb2 as pb

from slack_agents.a2a.client import A2AClient, _parts_files


def _raw_part(data, name, mt):
    p = pb.Part(raw=data)
    p.filename = name
    p.media_type = mt
    return p


def test_parts_files_extracts_only_raw_parts():
    parts = [pb.Part(text="hi"), _raw_part(b"abc", "x.csv", "text/csv")]
    assert _parts_files(parts) == [{"data": b"abc", "filename": "x.csv", "mimeType": "text/csv"}]


def test_task_to_result_pulls_artifact_text_and_files():
    c = A2AClient(url="http://x")
    task = pb.Task(id="t1", context_id="c1")
    task.status.state = pb.TaskState.TASK_STATE_COMPLETED
    art = task.artifacts.add()
    art.parts.append(_raw_part(b"data", "report.csv", "text/csv"))
    # status message carries an ack; artifact carries the file
    task.status.message.parts.append(pb.Part(text="done"))
    r = c._task_to_result(task)
    assert r.state == "completed"
    assert r.text == "done"
    assert r.files == [{"data": b"data", "filename": "report.csv", "mimeType": "text/csv"}]


def test_build_message_appends_raw_part_per_file():
    c = A2AClient(url="http://x")
    msg = c._build_message(
        "hello",
        "ctx",
        "task",
        [{"data": b"zz", "filename": "f.bin", "mimeType": "application/octet-stream"}],
    )
    assert [p.WhichOneof("content") for p in msg.parts] == ["text", "raw"]
    assert msg.parts[1].raw == b"zz"
    assert msg.parts[1].filename == "f.bin"
    assert msg.context_id == "ctx"
    assert msg.task_id == "task"
