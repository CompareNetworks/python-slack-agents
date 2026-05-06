"""Tests for oauth.state — signed-state encode/decode."""

import time

from slack_agents.oauth.state import (
    NonceReplayCache,
    StatePayload,
    decode,
    encode,
)

KEY = b"\x01" * 32


class TestEncodeDecodeRoundtrip:
    def test_roundtrip(self):
        cache = NonceReplayCache()
        payload = StatePayload(
            user_id="U123",
            server_id="my-mcp",
            authorize_url="https://idp.example.com/authorize?x=1",
            exp=int(time.time()) + 60,
        )
        token = encode(payload, KEY)
        decoded = decode(token, KEY, cache)
        assert decoded.user_id == "U123"
        assert decoded.server_id == "my-mcp"
        assert decoded.authorize_url == "https://idp.example.com/authorize?x=1"


class TestTampering:
    def test_bit_flip_returns_none(self):
        cache = NonceReplayCache()
        payload = StatePayload(
            user_id="U", server_id="s", authorize_url="https://a", exp=int(time.time()) + 60
        )
        token = encode(payload, KEY)
        # Flip a character in the middle.
        mid = len(token) // 2
        tampered = token[:mid] + ("A" if token[mid] != "A" else "B") + token[mid + 1 :]
        assert decode(tampered, KEY, cache) is None

    def test_wrong_key_returns_none(self):
        cache = NonceReplayCache()
        payload = StatePayload(
            user_id="U", server_id="s", authorize_url="https://a", exp=int(time.time()) + 60
        )
        token = encode(payload, KEY)
        assert decode(token, b"\x02" * 32, cache) is None


class TestExpiry:
    def test_expired_returns_none(self):
        cache = NonceReplayCache()
        payload = StatePayload(
            user_id="U",
            server_id="s",
            authorize_url="https://a",
            exp=int(time.time()) - 1,
        )
        token = encode(payload, KEY)
        assert decode(token, KEY, cache) is None


class TestReplay:
    def test_second_decode_returns_none(self):
        cache = NonceReplayCache()
        payload = StatePayload(
            user_id="U",
            server_id="s",
            authorize_url="https://a",
            exp=int(time.time()) + 60,
        )
        token = encode(payload, KEY)
        first = decode(token, KEY, cache)
        second = decode(token, KEY, cache)
        assert first is not None
        assert second is None
