"""CLI subcommand: build-docker."""


def register(subparsers):
    p = subparsers.add_parser("build-docker", help="Build a Docker image for an agent")
    p.add_argument(
        "agent_dir",
        help="Path to agent directory containing config.yaml and system_prompt.txt",
    )
    p.add_argument(
        "--push",
        metavar="REGISTRY",
        help="Push image to registry after building (e.g. registry.example.com)",
    )
    p.add_argument(
        "--image-name",
        metavar="NAME",
        help="Custom image name (default: slack-agents-<agent-dir-name>)",
    )
    p.add_argument(
        "--platform",
        default="linux/amd64",
        help="Target platform (default: linux/amd64)",
    )
    p.set_defaults(handler=execute)


def _is_valid_docker_name(name: str) -> bool:
    """Check if a string is a valid Docker image name component."""
    import re

    return bool(re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", name))


def execute(args):
    import re
    import subprocess
    import sys
    from pathlib import Path

    from slack_agents.config import load_agent_config
    from slack_agents.main import setup_environment

    setup_environment()

    agent_dir = Path(args.agent_dir)
    if not agent_dir.is_dir():
        print(f"Error: agent directory not found: {agent_dir}", file=sys.stderr)
        sys.exit(1)

    config, _system_prompt, agent_name = load_agent_config(agent_dir)
    version = config.version
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    image_name = args.image_name or f"slack-agents-{agent_name}"

    if not _is_valid_docker_name(image_name):
        print(
            f"Error: '{image_name}' is not a valid Docker image name. "
            "Names must be lowercase alphanumeric, optionally separated by "
            "'.', '-', or '_'. Use --image-name to provide a valid name.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.push:
        image_tag = f"{args.push}/{image_name}:{version}"
    else:
        image_tag = f"{image_name}:{version}"

    req_files = sorted(Path(".").glob("req*.txt"))
    if req_files:
        names = ", ".join(f.name for f in req_files)
        print(
            f"Error: found {names} in the project root.\n"
            "Docker builds install dependencies from pyproject.toml, not\n"
            "requirements files. Move your dependencies into pyproject.toml\n"
            "under [project] dependencies, then remove the requirements file(s).",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Building {image_tag} ...")
    result = subprocess.run(
        [
            "docker",
            "build",
            "--platform",
            args.platform,
            "--build-arg",
            f"AGENT_PATH={agent_dir}",
            "-f",
            str(dockerfile),
            "-t",
            image_tag,
            ".",
        ]
    )
    if result.returncode != 0:
        sys.exit(result.returncode)

    if args.push:
        print(f"Pushing {image_tag} ...")
        result = subprocess.run(["docker", "push", image_tag])
        if result.returncode != 0:
            sys.exit(result.returncode)

    print(f"Done: {image_tag}")

    # Show required env vars last so they're visible without scrolling
    raw_config = (agent_dir / "config.yaml").read_text()
    active_config = re.sub(r"(?m)^(\s*)#.*$", r"\1", raw_config)
    env_vars = sorted(set(re.findall(r"\{([A-Z_][A-Z0-9_]*)\}", active_config)))
    if env_vars:
        print(f"\nRequired environment variables ({len(env_vars)}):")
        for var in env_vars:
            print(f"  {var}")
