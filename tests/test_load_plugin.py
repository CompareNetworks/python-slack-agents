"""Tests for load_plugin's framework_ctx injection."""

import sys
import types

from slack_agents import FrameworkContext
from slack_agents.config import load_plugin


def _install_module(name: str, body: str) -> None:
    mod = types.ModuleType(name)
    exec(body, mod.__dict__)
    sys.modules[name] = mod


class TestLoadPluginFrameworkCtx:
    def test_provider_without_param_does_not_receive_ctx(self):
        _install_module(
            "slack_agents._test_no_ctx",
            "class Provider:\n    def __init__(self, x): self.x = x; self.ctx = None\n",
        )
        ctx = FrameworkContext(bot_token="t", agent_name="a")
        p = load_plugin("slack_agents._test_no_ctx", x=1, framework_ctx=ctx)
        assert p.x == 1
        assert p.ctx is None

    def test_provider_with_param_receives_ctx(self):
        _install_module(
            "slack_agents._test_with_ctx",
            "class Provider:\n"
            "    def __init__(self, x, *, framework_ctx=None):\n"
            "        self.x = x; self.ctx = framework_ctx\n",
        )
        ctx = FrameworkContext(bot_token="t", agent_name="a")
        p = load_plugin("slack_agents._test_with_ctx", x=1, framework_ctx=ctx)
        assert p.x == 1
        assert p.ctx is ctx

    def test_no_ctx_passed_means_no_ctx_kwarg(self):
        _install_module(
            "slack_agents._test_with_ctx_optional",
            "class Provider:\n"
            "    def __init__(self, x, *, framework_ctx=None):\n"
            "        self.ctx = framework_ctx\n",
        )
        p = load_plugin("slack_agents._test_with_ctx_optional", x=1)
        assert p.ctx is None
