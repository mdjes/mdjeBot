"""Symmetric encryption for bot tokens and database passwords stored by the builder."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("stored secret cannot be decrypted (BUILDER_SECRET_KEY changed?)") from exc
