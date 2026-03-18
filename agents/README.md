# Agents

Each agent is a directory with two files:

```
my-agent/
├── config.yaml          # LLM, tools, storage, access control
└── system_prompt.txt    # what the agent should do
```

To create a new agent, copy `hello-world/` and edit the config and prompt.

```bash
cp -r agents/hello-world agents/my-agent
# edit agents/my-agent/config.yaml and system_prompt.txt
slack-agents run agents/my-agent
```

Agents don't have to live here — you can keep them in any directory, a gitignored local folder, or a separate private repository. See [Organizing your agents](../docs/private-repo.md) for alternatives.

Full guide: [Creating an Agent](../docs/agents.md)
