"""HKDF subkey derivation and AES-GCM helpers for OAuth secrets."""

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_STATE_LABEL = b"slack-agents/oauth/state/v1"
_TOKEN_LABEL = b"slack-agents/oauth/token/v1"
_SUBKEY_LEN = 32
_NONCE_LEN = 12


def derive_subkeys(root_key: bytes) -> tuple[bytes, bytes]:
    """Derive (state_key, token_key) from a root key via HKDF-SHA256.

    Distinct labels guarantee key separation; both subkeys are 32 bytes.
    """
    state_key = HKDF(
        algorithm=hashes.SHA256(),
        length=_SUBKEY_LEN,
        salt=None,
        info=_STATE_LABEL,
    ).derive(root_key)
    token_key = HKDF(
        algorithm=hashes.SHA256(),
        length=_SUBKEY_LEN,
        salt=None,
        info=_TOKEN_LABEL,
    ).derive(root_key)
    return state_key, token_key


def encrypt_token(plaintext: bytes, token_key: bytes) -> str:
    """Encrypt with AES-GCM. Output: base64(nonce || ciphertext_with_tag)."""
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(token_key).encrypt(nonce, plaintext, associated_data=None)
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_token(ciphertext_b64: str, token_key: bytes) -> bytes:
    """Decrypt the output of encrypt_token. Raises InvalidTag on tamper / wrong key."""
    raw = base64.urlsafe_b64decode(ciphertext_b64.encode("ascii"))
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return AESGCM(token_key).decrypt(nonce, ct, associated_data=None)
