import pytest

from slack_agents.a2a.artifacts import files_to_llm_text
from slack_agents.files import FileHandlerRegistry
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


async def test_empty_files_returns_empty_string(reg):
    assert await files_to_llm_text([], reg, UCC, None) == ""


async def test_csv_artifact_is_tagged_and_extracted(reg):
    files = [{"data": b"id,intent\n1,hello\n", "filename": "p.csv", "mimeType": "text/csv"}]
    out = await files_to_llm_text(files, reg, UCC, None)
    assert "Do NOT repeat" in out  # the "already shown" tag
    assert "p.csv" in out
    assert "hello" in out  # extracted content


async def test_already_shown_false_omits_tag(reg):
    files = [{"data": b"id\n1\n", "filename": "p.csv", "mimeType": "text/csv"}]
    out = await files_to_llm_text(files, reg, UCC, None, already_shown=False)
    assert "Do NOT repeat" not in out
    assert "p.csv" in out


async def test_unparseable_artifact_is_descriptor(reg):
    files = [{"data": b"PK\x03\x04", "filename": "a.zip", "mimeType": "application/zip"}]
    out = await files_to_llm_text(files, reg, UCC, None)
    assert "could not be read" in out
    assert "a.zip" in out


async def test_no_registry_still_describes():
    files = [{"data": b"x", "filename": "a.bin", "mimeType": "application/octet-stream"}]
    out = await files_to_llm_text(files, None, UCC, None)
    assert "a.bin" in out
    assert "could not be read" in out
