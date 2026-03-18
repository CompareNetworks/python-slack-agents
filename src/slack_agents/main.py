"""Entry point: load config and start the Slack agent."""

import asyncio
import logging
import os
import sys
from pathlib import Path


def setup_environment() -> None:
    """Load .env and configure logging. Safe to call multiple times."""
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


setup_environment()
logger = logging.getLogger(__name__)

from slack_agents.config import load_agent_config  # noqa: E402
from slack_agents.observability import initialize as init_observability  # noqa: E402


async def run(agent_dir_arg: str) -> None:
    agent_dir = Path(agent_dir_arg).resolve()
    if not agent_dir.exists():
        print(f"Error: agent directory not found: {agent_dir}", file=sys.stderr)
        sys.exit(1)
    config, system_prompt, agent_name = load_agent_config(agent_dir)
    logger.info("Loaded config for agent: %s", agent_name)
    if config.observability:
        init_observability(config.observability)
    llm_type = config.llm.get("type", "unknown")
    llm_model = config.llm.get("model", "unknown")
    logger.info("LLM provider: %s, model: %s", llm_type, llm_model)
    logger.info("Tools: %s", list(config.tools.keys()))
    from slack_agents.slack.agent import SlackAgent

    agent = SlackAgent(config, system_prompt, agent_name)
    await agent.start()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m slack_agents.main <agent-dir>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))


if __name__ == "__main__":
    main()
