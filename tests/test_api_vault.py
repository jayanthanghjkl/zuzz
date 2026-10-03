"""Tests for the encrypted API vault.

Uses a temporary directory so the real credential store is never touched.
"""

from __future__ import annotations

import pytest

from brain.corpus_callosum.api_vault import ApiVault, VaultError



# --------------------------------------------------------------------------
# API vault
# --------------------------------------------------------------------------


def test_vault_roundtrip(tmp_path) -> None:
    """A sealed credential can be revealed again."""
    vault = ApiVault(tmp_path / "vault.json", tmp_path / ".vault_key")
    assert vault.seal("groq", "gsk-secret") is True
    assert vault.reveal("groq") == "gsk-secret"
    assert vault.providers() == ["groq"]


def test_vault_stores_only_ciphertext(tmp_path) -> None:
    """The plaintext key must never appear on disk."""
    path = tmp_path / "vault.json"
    vault = ApiVault(path, tmp_path / ".vault_key")
    vault.seal("groq", "gsk-secret")
    assert "gsk-secret" not in path.read_text(encoding="utf-8")


def test_vault_missing_entry_returns_none(tmp_path) -> None:
    """Revealing an unknown provider returns None."""
    vault = ApiVault(tmp_path / "vault.json", tmp_path / ".vault_key")
    assert vault.reveal("nothing") is None


def test_vault_remove(tmp_path) -> None:
    """Credentials can be deleted."""
    vault = ApiVault(tmp_path / "vault.json", tmp_path / ".vault_key")
    vault.seal("groq", "gsk-secret")
    assert vault.remove("groq") is True
    assert vault.remove("groq") is False
    assert vault.providers() == []


def test_vault_ignores_empty_secret(tmp_path) -> None:
    """Empty credentials are not stored."""
    vault = ApiVault(tmp_path / "vault.json", tmp_path / ".vault_key")
    assert vault.seal("groq", "") is False


def test_vault_lost_key_makes_secrets_unreadable(tmp_path) -> None:
    """Losing the key file makes old ciphertext unrecoverable."""
    vault = ApiVault(tmp_path / "vault.json", tmp_path / ".vault_key")
    vault.seal("groq", "gsk-secret")

    vault.key_path.unlink()
    with pytest.raises(VaultError):
        vault.reveal("groq")


def test_vault_imports_from_environment(tmp_path, monkeypatch) -> None:
    """Credentials can be pulled out of `.env` and sealed."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-from-env")
    vault = ApiVault(tmp_path / "vault.json", tmp_path / ".vault_key")
    assert vault.seal_from_env("groq", "GROQ_API_KEY") is True
    assert vault.reveal("groq") == "gsk-from-env"
