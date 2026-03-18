"""Unified CLI for slack-agents."""

import argparse
from datetime import datetime

from slack_agents.cli import (
    build_docker,
    export_conversations,
    export_usage,
    healthcheck,
    run,
)


def _parse_tz_aware(value: str) -> datetime:
    """Parse an ISO datetime string, rejecting naive (tz-unaware) values."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"datetime must include a timezone offset (e.g. +00:00), got: {value}"
        )
    return dt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slack-agents",
        description="CLI for running and managing slack-agents.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run.register(subparsers)
    healthcheck.register(subparsers)
    export_conversations.register(subparsers, _parse_tz_aware)
    export_usage.register(subparsers, _parse_tz_aware)
    build_docker.register(subparsers)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
