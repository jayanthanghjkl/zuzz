"""Corpus Callosum — API Vault.

Encrypted storage for provider credentials, using Fernet symmetric encryption
from `cryptography`. Keys still come from `.env` on first use, but can be
sealed into `storage/vault.json` so the web dashboard can accept new keys in
Phase 2 without a plaintext file.

The encryption key lives in `storage/.vault_key` (git-ignored). If that file is
lost, the vault is unrecoverable and every key must be re-entered — which is
the intended behaviour for a credential store.

This module is registered as unmodifiable in PROJECT.md section 13: the AI may
not change the encryption scheme.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from core.config import ROOT

VAULT_PATH = ROOT / "storage" / "vault.json"
KEY_PATH = ROOT / "storage" / ".vault_key"


class VaultError(RuntimeError):
    """Raised when the vault cannot be opened or decrypted."""


class ApiVault:
    """Encrypted credential storage.

    Attributes:
        path: Where sealed credentials are written.
    """

    def __init__(self, path: Optional[Path] = None, key_path: Optional[Path] = None) -> None:
        """Initialise the vault, creating the key file on first use.

        Args:
            path: Vault file location.
            key_path: Encryption key location.
        """
        self.path = Path(path) if path else VAULT_PATH
        self.key_path = Path(key_path) if key_path else KEY_PATH

    @property
    def available(self) -> bool:
        """Return True when the `cryptography` package is importable."""
        try:
            import cryptography  # noqa: F401
        except ImportError:
            return False
        return True

    def _fernet(self):
        """Return a Fernet instance for the vault key.

        Returns:
            A configured Fernet object.

        Raises:
            VaultError: If cryptography is missing or the key cannot be read.
        """
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise VaultError(
                "cryptography is not installed - run: pip install cryptography"
            ) from exc

        try:
            if self.key_path.is_file():
                key = self.key_path.read_bytes().strip()
            else:
                self.key_path.parent.mkdir(parents=True, exist_ok=True)
                key = Fernet.generate_key()
                self.key_path.write_bytes(key)
                self.key_path.chmod(0o600)
        except OSError as exc:
            raise VaultError(f"Cannot read or create vault key: {exc}") from exc
        return Fernet(key)

    def _read(self) -> Dict[str, str]:
        """Return the raw sealed payload, or an empty mapping."""
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise VaultError(f"Cannot read vault at {self.path}: {exc}") from exc
        return {str(k): str(v) for k, v in data.get("keys", {}).items()}

    def _write(self, sealed: Dict[str, str]) -> None:
        """Persist the sealed payload."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "keys": sealed}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def seal(self, provider: str, secret: str) -> bool:
        """Encrypt one credential and store it.

        Args:
            provider: Provider identifier, e.g. ``groq``.
            secret: The plaintext key.

        Returns:
            True when the value was stored.

        Raises:
            VaultError: If encryption or writing fails.
        """
        if not secret:
            return False
        fernet = self._fernet()
        sealed = fernet.encrypt(secret.encode("utf-8")).decode("ascii")
        data = self._read()
        data[provider] = sealed
        self._write(data)
        return True

    def reveal(self, provider: str) -> Optional[str]:
        """Decrypt one stored credential.

        Args:
            provider: Provider identifier.

        Returns:
            The plaintext key, or None when nothing is stored.

        Raises:
            VaultError: If the stored value cannot be decrypted.
        """
        sealed = self._read().get(provider)
        if not sealed:
            return None
        fernet = self._fernet()
        try:
            return fernet.decrypt(sealed.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise VaultError(
                f"Cannot decrypt '{provider}' - the vault key may have been regenerated"
            ) from exc

    def seal_from_env(self, provider: str, env_name: str) -> bool:
        """Import a credential from the environment into the vault.

        Args:
            provider: Provider identifier to store it under.
            env_name: Environment variable holding the plaintext key.

        Returns:
            True when a value was found and sealed.
        """
        secret = os.getenv(env_name) or ""
        return self.seal(provider, secret)

    def remove(self, provider: str) -> bool:
        """Delete one stored credential.

        Args:
            provider: Provider identifier.

        Returns:
            True when an entry was deleted.
        """
        data = self._read()
        if provider not in data:
            return False
        del data[provider]
        self._write(data)
        return True

    def providers(self) -> List[str]:
        """Return the provider identifiers with sealed credentials."""
        return sorted(self._read().keys())
