"""CLI subcommand: healthcheck.

Checks whether this agent's Socket Mode WebSocket is healthy by reading the
heartbeat timestamp from the storage backend (written every 10s by
_heartbeat_loop in agent.py).

Exits 0 if the heartbeat is fresh (<60s), exits 1 otherwise.
"""

import sys
import time
from pathlib import Path

from slack_agents.config import load_agent_config, load_plugin


def register(subparsers):
    p = subparsers.add_parser("healthcheck", help="Check agent liveness")
    p.add_argument("agent_dir", help="Path to agent directory (e.g. agents/hello-world)")
    p.set_defaults(handler=execute)


async def check(agent_dir_arg: str) -> bool:
    agent_dir = Path(agent_dir_arg).resolve()
    config, _system_prompt, agent_name = load_agent_config(agent_dir)

    storage_config = dict(config.storage)
    type_path = storage_config.pop("type")

    try:
        storage = load_plugin(type_path, **storage_config)
        await storage.initialize()
    except Exception as exc:
        print(f"UNHEALTHY: storage init failed: {exc}", file=sys.stderr)
        return False

    try:
        if not storage.supports_export:
            print(
                "UNHEALTHY: healthcheck requires persistent storage "
                "(use a file-based SQLite path or PostgreSQL)",
                file=sys.stderr,
            )
            return False

        heartbeat = await storage.get_heartbeat(agent_name)
        if heartbeat is not None:
            age = time.time() - heartbeat["last_ping_pong_time"]
            if age > 60:
                print(f"UNHEALTHY: heartbeat is {age:.0f}s old", file=sys.stderr)
                return False
            return True

        print(f"UNHEALTHY: no heartbeat for {agent_name}", file=sys.stderr)
        return False
    finally:
        await storage.close()


def execute(args):
    import asyncio

    from slack_agents.main import setup_environment

    setup_environment()
    healthy = asyncio.run(check(args.agent_dir))
    sys.exit(0 if healthy else 1)
