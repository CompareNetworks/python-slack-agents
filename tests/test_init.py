"""Tests for the `slack-agents init` scaffolder."""

from pathlib import Path

import pytest

from slack_agents.cli.init import execute


class _Args:
    """argparse-like object."""

    def __init__(self, project_name: str):
        self.project_name = project_name


@pytest.fixture
def run_init(tmp_path, monkeypatch):
    def _run(project_name: str) -> Path:
        monkeypatch.chdir(tmp_path)
        execute(_Args(project_name))
        return tmp_path

    return _run


class TestInitScaffoldsExpectedFiles:
    def test_creates_requirements_txt(self, run_init):
        d = run_init("my-agents")
        assert (d / "requirements.txt").exists()

    def test_requirements_txt_pins_framework(self, run_init):
        d = run_init("my-agents")
        content = (d / "requirements.txt").read_text()
        assert "python-slack-agents" in content

    def test_creates_src_package_stub(self, run_init):
        d = run_init("my-agents")
        init_file = d / "src" / "my_agents" / "__init__.py"
        assert init_file.exists()

    def test_package_name_is_snake_case(self, run_init):
        d = run_init("My-Cool-Agents")
        assert (d / "src" / "my_cool_agents" / "__init__.py").exists()

    def test_creates_env_example(self, run_init):
        d = run_init("my-agents")
        assert (d / ".env.example").exists()

    def test_creates_gitignore(self, run_init):
        d = run_init("my-agents")
        assert (d / ".gitignore").exists()

    def test_creates_hello_world_agent(self, run_init):
        d = run_init("my-agents")
        assert (d / "agents" / "hello-world" / "config.yaml").exists()
        assert (d / "agents" / "hello-world" / "system_prompt.txt").exists()

    def test_does_not_create_pyproject(self, run_init):
        d = run_init("my-agents")
        assert not (d / "pyproject.toml").exists()


class TestInitIsIdempotent:
    def test_second_run_skips_existing(self, run_init, capsys):
        d = run_init("my-agents")
        (d / "requirements.txt").write_text("# user-edited\n")
        run_init("my-agents")
        assert (d / "requirements.txt").read_text() == "# user-edited\n"
        captured = capsys.readouterr()
        assert "Skipping requirements.txt" in captured.out


class TestInitNextSteps:
    def test_next_steps_mentions_requirements_install(self, run_init, capsys):
        run_init("my-agents")
        out = capsys.readouterr().out
        assert "pip install -r requirements.txt" in out

    def test_next_steps_does_not_mention_editable_install(self, run_init, capsys):
        run_init("my-agents")
        out = capsys.readouterr().out
        assert "pip install -e" not in out

    def test_next_steps_mentions_run_hello_world(self, run_init, capsys):
        run_init("my-agents")
        out = capsys.readouterr().out
        assert "slack-agents run agents/hello-world" in out
