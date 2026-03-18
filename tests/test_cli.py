"""Tests for CLI arg parsing and dispatch."""

from datetime import datetime

import pytest

from slack_agents.cli import build_parser


class TestBuildParser:
    def test_run_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["run", "agents/hello-world"])
        assert args.command == "run"
        assert args.agent_dir == "agents/hello-world"

    def test_run_requires_agent_dir(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run"])

    def test_no_subcommand_shows_help(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


class TestHealthcheckParser:
    def test_healthcheck_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["healthcheck", "agents/hello-world"])
        assert args.command == "healthcheck"
        assert args.agent_dir == "agents/hello-world"

    def test_healthcheck_requires_agent_dir(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["healthcheck"])


class TestExportConversationsParser:
    def test_export_minimal(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "export-conversations",
                "agents/hello-world",
                "--format",
                "html",
                "--output",
                "/tmp/export",
            ]
        )
        assert args.command == "export-conversations"
        assert args.agent_dir == "agents/hello-world"
        assert args.format == "html"
        assert args.handle is None
        assert args.date_from is None
        assert args.date_to is None
        assert args.output == "/tmp/export"

    def test_export_all_options(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "export-conversations",
                "agents/docs-assistant",
                "--format",
                "html",
                "--handle",
                "alice",
                "--date-from",
                "2026-01-01T00:00:00+00:00",
                "--date-to",
                "2026-03-01T00:00:00+00:00",
                "--output",
                "/tmp/export",
            ]
        )
        assert args.agent_dir == "agents/docs-assistant"
        assert args.format == "html"
        assert args.handle == "alice"
        assert isinstance(args.date_from, datetime)
        assert isinstance(args.date_to, datetime)
        assert args.output == "/tmp/export"

    def test_export_requires_format(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["export-conversations", "agents/hello-world"])

    def test_export_rejects_unknown_format(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "export-conversations",
                    "agents/hello-world",
                    "--format",
                    "csv",
                ]
            )

    def test_export_rejects_naive_datetime(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "export-conversations",
                    "agents/hello-world",
                    "--format",
                    "html",
                    "--date-from",
                    "2026-01-01T00:00:00",
                ]
            )


class TestExportUsageParser:
    def test_export_usage_minimal(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "export-usage",
                "agents/hello-world",
                "--format",
                "csv",
                "--output",
                "/tmp/usage.csv",
            ]
        )
        assert args.command == "export-usage"
        assert args.agent_dir == "agents/hello-world"
        assert args.format == "csv"
        assert args.output == "/tmp/usage.csv"
        assert args.handle is None
        assert args.date_from is None
        assert args.date_to is None

    def test_export_usage_all_options(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "export-usage",
                "agents/docs-assistant",
                "--format",
                "csv",
                "--handle",
                "alice",
                "--date-from",
                "2026-01-01T00:00:00+00:00",
                "--date-to",
                "2026-03-01T00:00:00+00:00",
                "--output",
                "/tmp/usage.csv",
            ]
        )
        assert args.format == "csv"
        assert args.handle == "alice"
        assert isinstance(args.date_from, datetime)
        assert isinstance(args.date_to, datetime)
        assert args.output == "/tmp/usage.csv"

    def test_export_usage_requires_format(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["export-usage", "agents/hello-world", "--output", "/tmp/usage.csv"])

    def test_export_usage_requires_output(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["export-usage", "agents/hello-world", "--format", "csv"])

    def test_export_usage_rejects_unknown_format(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "export-usage",
                    "agents/hello-world",
                    "--format",
                    "html",
                    "--output",
                    "/tmp/usage.csv",
                ]
            )


class TestBuildDockerParser:
    def test_build_docker_minimal(self):
        parser = build_parser()
        args = parser.parse_args(["build-docker", "agents/hello-world"])
        assert args.command == "build-docker"
        assert args.agent_dir == "agents/hello-world"
        assert args.push is None
        assert args.platform == "linux/amd64"

    def test_build_docker_with_push(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "build-docker",
                "agents/hello-world",
                "--push",
                "registry.example.com",
            ]
        )
        assert args.push == "registry.example.com"

    def test_build_docker_custom_platform(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "build-docker",
                "agents/hello-world",
                "--platform",
                "linux/arm64",
            ]
        )
        assert args.platform == "linux/arm64"

    def test_build_docker_with_image_name(self):
        parser = build_parser()
        args = parser.parse_args(["build-docker", "agents/hello-world", "--image-name", "my-bot"])
        assert args.image_name == "my-bot"

    def test_build_docker_image_name_defaults_none(self):
        parser = build_parser()
        args = parser.parse_args(["build-docker", "agents/hello-world"])
        assert args.image_name is None

    def test_build_docker_requires_agent_dir(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["build-docker"])


class TestDockerImageNameValidation:
    @pytest.mark.parametrize(
        "name",
        ["my-agent", "slack-agents-hello-world", "my.app", "my_app", "app123"],
    )
    def test_valid_names(self, name):
        from slack_agents.cli.build_docker import _is_valid_docker_name

        assert _is_valid_docker_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "My-Agent",
            "UPPERCASE",
            "has spaces",
            "special@char",
            "-leading-dash",
            "trailing-",
            "",
        ],
    )
    def test_invalid_names(self, name):
        from slack_agents.cli.build_docker import _is_valid_docker_name

        assert not _is_valid_docker_name(name)
