"""CLI subcommand: run."""


def register(subparsers):
    p = subparsers.add_parser("run", help="Run a Slack agent")
    p.add_argument("agent_dir", help="Path to agent directory (e.g. agents/hello-world)")
    p.set_defaults(handler=execute)


def execute(args):
    import asyncio

    from slack_agents.main import run, setup_environment

    setup_environment()
    asyncio.run(run(args.agent_dir))
