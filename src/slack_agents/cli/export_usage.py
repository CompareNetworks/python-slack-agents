"""CLI subcommand: export-usage."""


def register(subparsers, parse_tz_aware):
    p = subparsers.add_parser("export-usage", help="Export per-conversation usage data as CSV")
    p.add_argument("agent_dir", help="Path to agent directory (e.g. agents/hello-world)")
    p.add_argument(
        "--format",
        required=True,
        choices=["csv"],
        help="Export format (currently: csv)",
    )
    p.add_argument("--handle", help="Filter by Slack user handle")
    p.add_argument(
        "--date-from",
        type=parse_tz_aware,
        help="Filter start datetime (ISO format with tz, e.g. 2026-01-01T00:00:00+00:00)",
    )
    p.add_argument(
        "--date-to",
        type=parse_tz_aware,
        help="Filter end datetime (ISO format with tz)",
    )
    p.add_argument("--output", required=True, help="Output CSV file path")
    p.set_defaults(handler=execute)


def execute(args):
    import asyncio
    import sys
    from pathlib import Path

    from slack_agents.config import load_agent_config, load_plugin
    from slack_agents.conversations import ConversationManager
    from slack_agents.main import setup_environment

    setup_environment()

    agent_dir = Path(args.agent_dir).resolve()
    if not agent_dir.exists():
        print(f"Error: agent directory not found: {agent_dir}", file=sys.stderr)
        sys.exit(1)

    config, _system_prompt, agent_name = load_agent_config(agent_dir)

    storage_config = dict(config.storage)
    type_path = storage_config.pop("type")

    async def run() -> None:
        storage = load_plugin(type_path, **storage_config)
        await storage.initialize()
        try:
            conversations = ConversationManager(storage)

            if not conversations.supports_export:
                print(
                    "Error: export-usage requires persistent storage"
                    " (file-based SQLite or PostgreSQL).\n"
                    "The current storage backend does not support conversation export.",
                    file=sys.stderr,
                )
                sys.exit(1)

            from slack_agents.cli.export_usage_csv import export_usage_csv

            count = await export_usage_csv(
                conversations,
                agent_name,
                args.output,
                handle=args.handle,
                date_from=args.date_from.isoformat() if args.date_from else None,
                date_to=args.date_to.isoformat() if args.date_to else None,
            )
            if count == 0:
                print("No conversations found matching the filters.")
            else:
                print(f"Exported {count} conversation(s) to {args.output}")
        finally:
            await storage.close()

    asyncio.run(run())
