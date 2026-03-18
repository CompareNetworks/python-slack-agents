"""Tests for LLM provider loading via load_plugin."""

import pytest

from slack_agents.config import load_plugin
from slack_agents.llm.base import BaseLLMProvider


def test_create_anthropic_provider():
    provider = load_plugin(
        "slack_agents.llm.anthropic",
        model="claude-sonnet-4-6",
        api_key="sk-test",
        max_tokens=4096,
        max_input_tokens=200_000,
    )
    assert isinstance(provider, BaseLLMProvider)
    assert provider.max_input_tokens == 200_000


def test_create_openai_provider():
    provider = load_plugin(
        "slack_agents.llm.openai",
        model="gpt-4.1",
        api_key="sk-test",
        max_tokens=4096,
        max_input_tokens=200_000,
    )
    assert isinstance(provider, BaseLLMProvider)
    assert provider.max_input_tokens == 200_000


def test_unknown_provider_raises():
    with pytest.raises(ModuleNotFoundError):
        load_plugin("slack_agents.llm.gemini", model="gemini-pro", api_key="sk-test")


def test_custom_max_input_tokens():
    provider = load_plugin(
        "slack_agents.llm.anthropic",
        model="claude-sonnet-4-6",
        api_key="sk-test",
        max_tokens=4096,
        max_input_tokens=100_000,
    )
    assert provider.max_input_tokens == 100_000
