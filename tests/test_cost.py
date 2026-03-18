"""Tests for cost estimation logic (now in each LLM provider)."""

import pytest

from slack_agents.config import load_plugin


def test_anthropic_known_model_basic():
    provider = load_plugin(
        "slack_agents.llm.anthropic",
        model="claude-sonnet-4-6",
        api_key="sk-test",
        max_tokens=4096,
        max_input_tokens=200_000,
    )
    # $3/M input, $15/M output
    cost = provider.estimate_cost(input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(3.0)

    cost = provider.estimate_cost(input_tokens=0, output_tokens=1_000_000)
    assert cost == pytest.approx(15.0)


def test_anthropic_unknown_model_returns_none():
    provider = load_plugin(
        "slack_agents.llm.anthropic",
        model="unknown-model",
        api_key="sk-test",
        max_tokens=4096,
        max_input_tokens=200_000,
    )
    assert provider.estimate_cost(100, 100) is None


def test_anthropic_cache_pricing():
    provider = load_plugin(
        "slack_agents.llm.anthropic",
        model="claude-sonnet-4-6",
        api_key="sk-test",
        max_tokens=4096,
        max_input_tokens=200_000,
    )
    # Anthropic: cache writes 1.25x, cache reads 0.1x
    cost = provider.estimate_cost(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=1_000_000,
    )
    assert cost == pytest.approx(3.0 * 1.25)

    cost = provider.estimate_cost(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=1_000_000,
    )
    assert cost == pytest.approx(3.0 * 0.1)


def test_openai_cache_pricing():
    provider = load_plugin(
        "slack_agents.llm.openai",
        model="gpt-4.1",
        api_key="sk-test",
        max_tokens=4096,
        max_input_tokens=200_000,
    )
    # OpenAI: cache writes 1.0x (no penalty), cache reads 0.5x
    cost = provider.estimate_cost(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=1_000_000,
    )
    assert cost == pytest.approx(2.0 * 1.0)

    cost = provider.estimate_cost(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=1_000_000,
    )
    assert cost == pytest.approx(2.0 * 0.5)


def test_combined_cost():
    provider = load_plugin(
        "slack_agents.llm.anthropic",
        model="claude-sonnet-4-6",
        api_key="sk-test",
        max_tokens=4096,
        max_input_tokens=200_000,
    )
    # 100k input + 50k cached read + 10k output for claude-sonnet-4-6
    cost = provider.estimate_cost(
        input_tokens=100_000,
        output_tokens=10_000,
        cache_read_input_tokens=50_000,
    )
    expected = (100_000 * 3.0 + 50_000 * 3.0 * 0.1 + 10_000 * 15.0) / 1_000_000
    assert cost == pytest.approx(expected)
