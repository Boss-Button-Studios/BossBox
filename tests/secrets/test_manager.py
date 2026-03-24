"""
Step 7 — Secrets Manager test suite
=====================================

Test classes
------------
TestPasswordUnlock   — password/KDF path (scrypt stdlib, no argon2-cffi needed)
TestKeychainUnlock   — OS keychain path; keyring is mocked
TestLockAndMemory    — session-key lifecycle and zeroing behaviour
TestPersistence      — on-disk format, plaintext scan, AAD tamper detection
TestEdgeCases        — error paths, missing keys, large/unicode values
TestReprAndHelpers   — __repr__, list_keys, delete, header constant

The autouse fixture `fast_kdf` patches scrypt parameters to minimal values so
password-based tests finish in milliseconds.  Security properties are unchanged;
only the work factor is reduced.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bossbox.secrets.exceptions import (
    SecretsDecryptError,
    SecretsLockError,
    SecretsMethodError,
)
from bossbox.secrets.manager import (
    FORMAT_VERSION,
    HEADER_SIZE,
    HEADER_STRUCT,
    KEY_SIZE,
    KEYCHAIN_SERVICE,
    KEYCHAIN_USERNAME,
    MAGIC,
    SecretsManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """
    Reduce scrypt work factor to N=2^10 so tests run fast.
    Also patches argon2-cffi path in case it's installed.
    """
    import bossbox.secrets.manager as m
    monkeypatch.setattr(m, "SCRYPT_N", 1 << 10)
    monkeypatch.setattr(m, "ARGON2_TIME_COST", 1)
    monkeypatch.setattr(m, "ARGON2_MEMORY_COST", 8)
    monkeypatch.setattr(m, "ARGON2_PARALLELISM", 1)


@pytest.fixture
def tmp_secrets_file(tmp_path: Path) -> Path:
    return tmp_path / "secrets.enc"


@pytest.fixture
def sm(tmp_secrets_file: Path) -> SecretsManager:
    return SecretsManager(secrets_file=tmp_secrets_file)


def _make_mock_keyring(store: dict | None = None) -> MagicMock:
    backing: dict = store if store is not None else {}
    mock = MagicMock()

    def get_password(service, username):
        return backing.get((service, username))

    def set_password(service, username, password):
        backing[(service, username)] = password

    mock.get_password.side_effect = get_password
    mock.set_password.side_effect = set_password
    return mock


# ---------------------------------------------------------------------------
# TestPasswordUnlock
# ---------------------------------------------------------------------------


class TestPasswordUnlock:

    def test_first_run_creates_file(self, sm, tmp_secrets_file):
        assert not tmp_secrets_file.exists()
        result = sm.unlock("password", password="hunter2")
        assert result is True
        assert tmp_secrets_file.exists()

    def test_returns_true_on_correct_password(self, sm):
        sm.unlock("password", password="correct-horse")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        assert sm2.unlock("password", password="correct-horse") is True

    def test_returns_false_on_wrong_password(self, sm):
        sm.unlock("password", password="correct-horse")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        assert sm2.unlock("password", password="wrong-horse") is False

    def test_is_unlocked_false_before_unlock(self, sm):
        assert sm.is_unlocked is False

    def test_is_unlocked_true_after_unlock(self, sm):
        sm.unlock("password", password="pw")
        assert sm.is_unlocked is True

    def test_is_unlocked_false_after_lock(self, sm):
        sm.unlock("password", password="pw")
        sm.lock()
        assert sm.is_unlocked is False

    def test_get_raises_when_locked(self, sm):
        with pytest.raises(SecretsLockError):
            sm.get("anything")

    def test_set_raises_when_locked(self, sm):
        with pytest.raises(SecretsLockError):
            sm.set("key", "value")

    def test_set_and_get_round_trip(self, sm):
        sm.unlock("password", password="pw")
        sm.set("ANTHROPIC_API_KEY", "sk-ant-test-value")
        assert sm.get("ANTHROPIC_API_KEY") == "sk-ant-test-value"

    def test_set_persists_across_instances(self, sm):
        sm.unlock("password", password="pw")
        sm.set("OPENAI_API_KEY", "sk-openai-persist")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        sm2.unlock("password", password="pw")
        assert sm2.get("OPENAI_API_KEY") == "sk-openai-persist"

    def test_multiple_secrets_persist(self, sm):
        sm.unlock("password", password="pw")
        for i in range(10):
            sm.set(f"KEY_{i}", f"value_{i}")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        sm2.unlock("password", password="pw")
        for i in range(10):
            assert sm2.get(f"KEY_{i}") == f"value_{i}"

    def test_smtp_credentials_round_trip(self, sm):
        sm.unlock("password", password="smtp-test-pw")
        sm.set("SMTP_HOST", "smtp.example.com")
        sm.set("SMTP_PORT", "587")
        sm.set("SMTP_USER", "user@example.com")
        sm.set("SMTP_PASS", "correct-horse-battery-staple")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        sm2.unlock("password", password="smtp-test-pw")
        assert sm2.get("SMTP_HOST") == "smtp.example.com"
        assert sm2.get("SMTP_PASS") == "correct-horse-battery-staple"

    def test_api_key_round_trip(self, sm):
        sm.unlock("password", password="api-test-pw")
        sm.set("ANTHROPIC_API_KEY", "sk-ant-api03-real-key-here")
        sm.set("OPENAI_API_KEY", "sk-proj-openai-key-here")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        sm2.unlock("password", password="api-test-pw")
        assert sm2.get("ANTHROPIC_API_KEY") == "sk-ant-api03-real-key-here"
        assert sm2.get("OPENAI_API_KEY") == "sk-proj-openai-key-here"

    def test_missing_password_arg_raises_value_error(self, sm):
        with pytest.raises(ValueError, match="password"):
            sm.unlock("password")

    def test_unknown_method_raises_value_error(self, sm):
        with pytest.raises(ValueError, match="Unknown unlock method"):
            sm.unlock("magic-method")

    def test_wrong_password_leaves_store_locked(self, sm):
        sm.unlock("password", password="correct-horse")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        sm2.unlock("password", password="wrong-horse")
        assert not sm2.is_unlocked


# ---------------------------------------------------------------------------
# TestKeychainUnlock
# ---------------------------------------------------------------------------


class TestKeychainUnlock:

    def test_first_run_generates_and_stores_key(self, tmp_secrets_file):
        mock_kr = _make_mock_keyring()
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm = SecretsManager(secrets_file=tmp_secrets_file)
            assert sm.unlock("keychain") is True
            assert tmp_secrets_file.exists()
            stored_hex = mock_kr.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USERNAME)
            assert stored_hex is not None
            assert len(bytes.fromhex(stored_hex)) == KEY_SIZE

    def test_second_unlock_reuses_stored_key(self, tmp_secrets_file):
        store: dict = {}
        mock_kr = _make_mock_keyring(store)
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm1 = SecretsManager(secrets_file=tmp_secrets_file)
            sm1.unlock("keychain")
            sm1.set("X", "hello")
            key1 = bytes(sm1._session_key)
            sm1.lock()

            sm2 = SecretsManager(secrets_file=tmp_secrets_file)
            sm2.unlock("keychain")
            assert bytes(sm2._session_key) == key1
            assert sm2.get("X") == "hello"

    def test_missing_keychain_entry_raises_decrypt_error(self, tmp_secrets_file):
        store: dict = {}
        mock_kr = _make_mock_keyring(store)
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm = SecretsManager(secrets_file=tmp_secrets_file)
            sm.unlock("keychain")
            sm.lock()

        empty_kr = _make_mock_keyring({})
        with patch("bossbox.secrets.manager._import_keyring", return_value=empty_kr):
            sm2 = SecretsManager(secrets_file=tmp_secrets_file)
            with pytest.raises(SecretsDecryptError, match="keychain entry is missing"):
                sm2._unlock_keychain()

    def test_keyring_import_failure_raises_method_error(self, sm):
        with patch(
            "bossbox.secrets.manager._import_keyring",
            side_effect=SecretsMethodError("keyring not installed"),
        ):
            with pytest.raises(SecretsMethodError):
                sm.unlock("keychain")

    def test_keychain_set_get_round_trip(self, tmp_secrets_file):
        mock_kr = _make_mock_keyring()
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm = SecretsManager(secrets_file=tmp_secrets_file)
            sm.unlock("keychain")
            sm.set("ANTHROPIC_API_KEY", "sk-ant-keychain-test")
            assert sm.get("ANTHROPIC_API_KEY") == "sk-ant-keychain-test"

    def test_keychain_persists_across_instances(self, tmp_secrets_file):
        store: dict = {}
        mock_kr = _make_mock_keyring(store)
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm1 = SecretsManager(secrets_file=tmp_secrets_file)
            sm1.unlock("keychain")
            sm1.set("PERSIST_ME", "value-survives-restart")
            sm1.lock()

            sm2 = SecretsManager(secrets_file=tmp_secrets_file)
            sm2.unlock("keychain")
            assert sm2.get("PERSIST_ME") == "value-survives-restart"


# ---------------------------------------------------------------------------
# TestLockAndMemory
# ---------------------------------------------------------------------------


class TestLockAndMemory:

    def test_lock_clears_session_key(self, sm):
        sm.unlock("password", password="pw")
        assert sm._session_key is not None
        sm.lock()
        assert sm._session_key is None

    def test_lock_clears_secrets_dict(self, sm):
        sm.unlock("password", password="pw")
        sm.set("K", "v")
        sm.lock()
        assert sm._secrets == {}

    def test_lock_clears_method(self, sm):
        sm.unlock("password", password="pw")
        sm.lock()
        assert sm._method is None

    def test_get_after_lock_raises(self, sm):
        sm.unlock("password", password="pw")
        sm.set("K", "v")
        sm.lock()
        with pytest.raises(SecretsLockError):
            sm.get("K")

    def test_set_after_lock_raises(self, sm):
        sm.unlock("password", password="pw")
        sm.lock()
        with pytest.raises(SecretsLockError):
            sm.set("K", "v")

    def test_re_unlock_after_lock(self, sm):
        sm.unlock("password", password="pw")
        sm.set("K", "v")
        sm.lock()
        sm.unlock("password", password="pw")
        assert sm.get("K") == "v"

    def test_multiple_lock_calls_are_idempotent(self, sm):
        sm.unlock("password", password="pw")
        sm.lock()
        sm.lock()   # must not raise

    def test_session_key_not_written_to_disk(self, sm, tmp_path):
        sm.unlock("password", password="pw")
        sm.set("SECRET", "canary-value")
        key_bytes = bytes(sm._session_key)
        for f in tmp_path.iterdir():
            if f.is_file():
                content = f.read_bytes()
                assert key_bytes not in content, (
                    f"Session key bytes found verbatim in {f.name}"
                )


# ---------------------------------------------------------------------------
# TestPersistence
# ---------------------------------------------------------------------------


class TestPersistence:

    def test_file_does_not_contain_secret_in_plaintext(self, sm):
        sm.unlock("password", password="pw")
        sm.set("SECRET_VALUE", "this-must-not-appear-in-plaintext")
        raw = sm._secrets_file.read_bytes()
        assert b"this-must-not-appear-in-plaintext" not in raw

    def test_file_is_not_json_parseable(self, sm):
        sm.unlock("password", password="pw")
        sm.set("API_KEY", "sk-secret")
        raw = sm._secrets_file.read_bytes()
        try:
            json.loads(raw)
            pytest.fail("Secrets file parsed as JSON — plaintext leak suspected")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    def test_magic_bytes_present(self, sm):
        sm.unlock("password", password="pw")
        assert sm._secrets_file.read_bytes()[:4] == MAGIC

    def test_version_byte_correct(self, sm):
        sm.unlock("password", password="pw")
        assert sm._secrets_file.read_bytes()[4] == FORMAT_VERSION

    def test_file_size_grows_with_secrets(self, sm):
        sm.unlock("password", password="pw")
        size_empty = sm._secrets_file.stat().st_size
        sm.set("LONG_KEY", "x" * 1000)
        size_full = sm._secrets_file.stat().st_size
        assert size_full > size_empty

    def test_file_permissions_unix(self, sm):
        if os.name == "nt":
            pytest.skip("chmod not applicable on Windows")
        sm.unlock("password", password="pw")
        mode = sm._secrets_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_ciphertext_byte_flip_detected(self, sm):
        sm.unlock("password", password="pw")
        sm.set("K", "v")
        raw = bytearray(sm._secrets_file.read_bytes())
        raw[HEADER_SIZE + 8] ^= 0xFF
        sm._secrets_file.write_bytes(bytes(raw))
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        assert sm2.unlock("password", password="pw") is False

    def test_aad_tampering_detected(self, sm):
        sm.unlock("password", password="pw")
        sm.set("K", "v")
        raw = bytearray(sm._secrets_file.read_bytes())
        raw[5] ^= 0x01   # method byte is at offset 5
        sm._secrets_file.write_bytes(bytes(raw))
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        assert sm2.unlock("password", password="pw") is False

    def test_bad_magic_returns_false(self, sm):
        sm.unlock("password", password="pw")
        sm.set("K", "v")
        raw = bytearray(sm._secrets_file.read_bytes())
        raw[0:4] = b"XXXX"
        sm._secrets_file.write_bytes(bytes(raw))
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        assert sm2.unlock("password", password="pw") is False

    def test_salt_reused_across_saves(self, sm):
        sm.unlock("password", password="pw")
        salt_before = sm._read_header()["salt"]
        sm.set("A", "1")
        sm.set("B", "2")
        sm.delete("A")
        salt_after = sm._read_header()["salt"]
        assert salt_before == salt_after

    def test_nonce_changes_on_each_save(self, sm):
        sm.unlock("password", password="pw")
        nonce1 = sm._read_header()["nonce"]
        sm.set("X", "1")
        nonce2 = sm._read_header()["nonce"]
        assert nonce1 != nonce2

    def test_parent_directory_created_automatically(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "secrets.enc"
        sm2 = SecretsManager(secrets_file=deep_path)
        sm2.unlock("password", password="pw")
        assert deep_path.exists()


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_get_missing_key_raises_key_error(self, sm):
        sm.unlock("password", password="pw")
        with pytest.raises(KeyError):
            sm.get("NONEXISTENT_KEY")

    def test_delete_missing_key_raises_key_error(self, sm):
        sm.unlock("password", password="pw")
        with pytest.raises(KeyError):
            sm.delete("NONEXISTENT_KEY")

    def test_overwrite_existing_key(self, sm):
        sm.unlock("password", password="pw")
        sm.set("K", "original")
        sm.set("K", "updated")
        assert sm.get("K") == "updated"

    def test_empty_string_value(self, sm):
        sm.unlock("password", password="pw")
        sm.set("EMPTY", "")
        assert sm.get("EMPTY") == ""

    def test_unicode_value_round_trip(self, sm):
        sm.unlock("password", password="pw")
        sm.set("UNICODE", "Japanese: \u65e5\u672c\u8a9e\U0001f510")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        sm2.unlock("password", password="pw")
        assert sm2.get("UNICODE") == "Japanese: \u65e5\u672c\u8a9e\U0001f510"

    def test_long_value_round_trip(self, sm):
        sm.unlock("password", password="pw")
        long_val = "x" * 10_000
        sm.set("LONG", long_val)
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        sm2.unlock("password", password="pw")
        assert sm2.get("LONG") == long_val

    def test_truncated_file_returns_false(self, sm, tmp_secrets_file):
        sm.unlock("password", password="pw")
        sm.set("K", "v")
        tmp_secrets_file.write_bytes(tmp_secrets_file.read_bytes()[:10])
        sm2 = SecretsManager(secrets_file=tmp_secrets_file)
        assert sm2.unlock("password", password="pw") is False

    def test_token_missing_pkcs11_lib_raises(self, sm):
        with pytest.raises(ValueError, match="pkcs11_lib"):
            sm.unlock("token")

    def test_token_missing_pin_raises(self, sm):
        with pytest.raises(ValueError, match="pkcs11_lib"):
            sm.unlock("token", pkcs11_lib="/usr/lib/libsofthsm2.so")

    def test_token_import_failure_raises_method_error(self, sm):
        with patch(
            "bossbox.secrets.manager._import_pkcs11",
            side_effect=SecretsMethodError("python-pkcs11 not installed"),
        ):
            with pytest.raises(SecretsMethodError):
                sm.unlock("token", pkcs11_lib="/usr/lib/lib.so", pkcs11_pin="1234")

    def test_empty_secrets_on_first_run(self, sm):
        sm.unlock("password", password="pw")
        assert sm.list_keys() == []

    def test_delete_persists_across_instances(self, sm):
        sm.unlock("password", password="pw")
        sm.set("TEMP", "temp-value")
        sm.delete("TEMP")
        sm.lock()
        sm2 = SecretsManager(secrets_file=sm._secrets_file)
        sm2.unlock("password", password="pw")
        assert "TEMP" not in sm2.list_keys()


# ---------------------------------------------------------------------------
# TestReprAndHelpers
# ---------------------------------------------------------------------------


class TestReprAndHelpers:

    def test_repr_shows_locked(self, sm):
        assert "locked" in repr(sm)
        assert "unlocked" not in repr(sm)

    def test_repr_shows_unlocked(self, sm):
        sm.unlock("password", password="pw")
        assert "unlocked" in repr(sm)

    def test_list_keys_returns_correct_names(self, sm):
        sm.unlock("password", password="pw")
        sm.set("A", "1")
        sm.set("B", "2")
        sm.set("C", "3")
        assert set(sm.list_keys()) == {"A", "B", "C"}

    def test_list_keys_raises_when_locked(self, sm):
        with pytest.raises(SecretsLockError):
            sm.list_keys()

    def test_delete_raises_when_locked(self, sm):
        with pytest.raises(SecretsLockError):
            sm.delete("K")

    def test_header_size_constant_is_correct(self):
        assert struct.calcsize(HEADER_STRUCT) == HEADER_SIZE == 50
