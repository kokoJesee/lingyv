"""Content-at-rest encryption for canvas records.

Only content columns are encrypted; ids, dates and status stay plaintext so the
database can still be queried and indexed. Threat model: disk theft, another OS
account, leaked backups. It does NOT protect against someone who controls the
running process or reads the key file.

Format: ``enc:v1:<base64(nonce(12) + ciphertext)>``; aad binds the row so a
ciphertext cannot be swapped between rows. Plaintext rows written before the
migration are recognisable by the missing prefix and are re-encrypted on read.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc:v1:"
KEY_ID = "k1"
KEY_BYTES = 32


def generate_key() -> bytes:
    return os.urandom(KEY_BYTES)


def load_or_create_key(path: Path) -> bytes:
    if path.exists():
        raw = path.read_bytes().strip()
        if len(raw) != KEY_BYTES:
            raise RuntimeError(f"canvas key file has unexpected length: {path}")
        return raw
    key = generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows may not support POSIX modes
        pass
    return key


class ContentCipher:
    def __init__(self, key: bytes, key_id: str = KEY_ID, enabled: bool = True):
        if enabled and len(key) != KEY_BYTES:
            raise RuntimeError("content key must be 32 bytes")
        self._cipher = AESGCM(key) if enabled else None
        self.key_id = key_id
        self.enabled = enabled

    @classmethod
    def from_settings(cls, settings) -> ContentCipher:
        if not settings.canvas_encryption:
            return cls(b"", key_id=KEY_ID, enabled=False)
        if settings.canvas_enc_key:
            key = base64.b64decode(settings.canvas_enc_key.get_secret_value())
        else:
            key = load_or_create_key(Path(settings.canvas_key_path))
        return cls(key)

    def encrypt(self, text: str, aad: str) -> str:
        if not self.enabled:
            return text
        nonce = os.urandom(12)
        sealed = self._cipher.encrypt(nonce, text.encode("utf-8"), aad.encode("utf-8"))
        return PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, token: str, aad: str) -> str:
        if not self.enabled or not token.startswith(PREFIX):
            return token
        raw = base64.b64decode(token[len(PREFIX):])
        return self._cipher.decrypt(raw[:12], raw[12:], aad.encode("utf-8")).decode("utf-8")

    def encrypt_json(self, value: object, aad: str) -> str:
        return self.encrypt(json.dumps(value, ensure_ascii=False, sort_keys=True), aad)

    def decrypt_json(self, token: str, aad: str) -> object:
        return json.loads(self.decrypt(token, aad))
