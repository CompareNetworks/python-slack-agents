import pytest

from slack_agents.files import FileHandlerRegistry, describe_file
from slack_agents.tools.file_importer import Provider as FileImporter

UCC = {
    "user_id": "U1",
    "user_name": "u",
    "user_handle": "u",
    "channel_id": "C1",
    "channel_name": "c",
    "thread_id": "T1",
}


@pytest.fixture
def reg():
    return FileHandlerRegistry([FileImporter([".*"])])


def test_describe_file_carries_metadata_and_reason():
    block = describe_file("report.zip", "application/zip", 2_100_000, "unsupported type")
    assert block["type"] == "text"
    assert "report.zip" in block["text"]
    assert "application/zip" in block["text"]
    assert "2100000" in block["text"]
    assert "unsupported type" in block["text"]


async def test_handled_csv_returns_extracted_text(reg):
    block = await reg.process_file(b"a,b\n1,2\n", "text/csv", "x.csv", UCC, None)
    assert block["type"] == "text"
    assert "x.csv" in block["text"]
    assert "1" in block["text"] and "2" in block["text"]


async def test_unsupported_mime_returns_descriptor(reg):
    block = await reg.process_file(b"PK\x03\x04", "application/zip", "x.zip", UCC, None)
    assert block["type"] == "text"
    assert "could not be read" in block["text"]
    assert "unsupported type" in block["text"]


async def test_empty_registry_returns_no_capability_descriptor():
    reg = FileHandlerRegistry([])
    block = await reg.process_file(b"hi", "text/csv", "x.csv", UCC, None)
    assert "no import capability configured" in block["text"]


async def test_oversized_returns_descriptor(reg):
    # text/csv handler has a 10MB cap; force it tiny.
    for mime in list(reg._mime_map):
        provider, name, _ = reg._mime_map[mime]
        reg._mime_map[mime] = (provider, name, 3)
    block = await reg.process_file(b"way too long", "text/csv", "x.csv", UCC, None)
    assert "exceeds" in block["text"]


async def test_process_files_message_makes_placeholder_for_unsupported(monkeypatch):
    from slack_agents.slack import files as slack_files
    from slack_agents.tools.file_importer import Provider as FileImporter

    async def fake_download(url, bot_token):
        return b"PK\x03\x04zip-bytes"

    monkeypatch.setattr(slack_files, "download_file", fake_download)
    reg = FileHandlerRegistry([FileImporter([".*"])])
    files = [{"name": "x.zip", "mimetype": "application/zip", "url_private": "http://s/x.zip"}]
    results = await slack_files.process_files_for_message(files, "tok", reg, UCC, None)
    assert len(results) == 1
    block, meta = results[0]
    assert "could not be read" in block["text"]
    assert meta["raw_bytes"] == b"PK\x03\x04zip-bytes"  # raw bytes kept for forwarding


def test_framework_context_has_file_registry_field():
    from slack_agents import FrameworkContext

    ctx = FrameworkContext(bot_token="t", agent_name="a")
    assert ctx.file_registry is None  # default; bound at startup
