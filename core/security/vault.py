"""
AEGIS - Vault: Secret management for E.V.A.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger


class Vault:
    def __init__(self, secrets_path: str = "config/secrets.env"):
        self._secrets_path = Path(secrets_path)
        self._loaded = False
        self._keys: set[str] = set()

    def load(self) -> None:
        if not self._secrets_path.exists():
            raise FileNotFoundError(
                f"Secrets file not found: {self._secrets_path}. "
                "Create config/secrets.env with your API keys."
            )
        load_dotenv(self._secrets_path, override=True)
        self._loaded = True
        with open(self._secrets_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key = line.split("=", 1)[0].strip()
                    self._keys.add(key)
        logger.info(f"AEGIS Vault loaded {len(self._keys)} secrets")

    def get(self, key: str) -> str:
        if not self._loaded:
            self.load()
        value = os.getenv(key)
        if value is None or value == "":
            raise ValueError(f"Secret '{key}' is not set in vault")
        return value

    def get_optional(self, key: str) -> str | None:
        if not self._loaded:
            self.load()
        value = os.getenv(key)
        return value if value and value.strip() else None

    def has(self, key: str) -> bool:
        if not self._loaded:
            self.load()
        value = os.getenv(key)
        return value is not None and value.strip() != ""


vault = Vault()