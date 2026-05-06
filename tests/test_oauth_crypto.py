"""Tests for oauth.crypto."""

import secrets

import pytest

from slack_agents.oauth.crypto import (
    decrypt_token,
    derive_subkeys,
    encrypt_token,
)


class TestDeriveSubkeys:
    def test_returns_two_distinct_32_byte_keys(self):
        root = secrets.token_bytes(32)
        state_key, token_key = derive_subkeys(root)
        assert len(state_key) == 32
        assert len(token_key) == 32
        assert state_key != token_key

    def test_deterministic_for_same_root(self):
        root = secrets.token_bytes(32)
        a = derive_subkeys(root)
        b = derive_subkeys(root)
        assert a == b

    def test_different_roots_yield_different_subkeys(self):
        root_a = secrets.token_bytes(32)
        root_b = secrets.token_bytes(32)
        assert derive_subkeys(root_a) != derive_subkeys(root_b)


class TestEncryptDecryptToken:
    def test_roundtrip(self):
        _, token_key = derive_subkeys(secrets.token_bytes(32))
        plaintext = b"refresh_token_xyz"
        ciphertext = encrypt_token(plaintext, token_key)
        assert decrypt_token(ciphertext, token_key) == plaintext

    def test_ciphertext_differs_from_plaintext(self):
        _, token_key = derive_subkeys(secrets.token_bytes(32))
        plaintext = b"refresh_token_xyz"
        ciphertext = encrypt_token(plaintext, token_key)
        assert plaintext.decode() not in ciphertext

    def test_ciphertext_differs_each_call(self):
        """Random nonce per encryption — same plaintext yields different ciphertexts."""
        _, token_key = derive_subkeys(secrets.token_bytes(32))
        plaintext = b"refresh_token_xyz"
        a = encrypt_token(plaintext, token_key)
        b = encrypt_token(plaintext, token_key)
        assert a != b

    def test_tampered_ciphertext_raises(self):
        from cryptography.exceptions import InvalidTag

        _, token_key = derive_subkeys(secrets.token_bytes(32))
        ciphertext = encrypt_token(b"plaintext", token_key)
        # Flip a byte in the middle of the base64 ciphertext (after the nonce).
        tampered = ciphertext[:-2] + ("A" if ciphertext[-2] != "A" else "B") + ciphertext[-1]
        with pytest.raises(InvalidTag):
            decrypt_token(tampered, token_key)

    def test_wrong_key_raises(self):
        from cryptography.exceptions import InvalidTag

        _, key_a = derive_subkeys(secrets.token_bytes(32))
        _, key_b = derive_subkeys(secrets.token_bytes(32))
        ciphertext = encrypt_token(b"plaintext", key_a)
        with pytest.raises(InvalidTag):
            decrypt_token(ciphertext, key_b)
