"""
Step 7 — Secrets Manager  (stdlib unittest version)
=====================================================
Identical coverage to test_manager.py but runnable with:

    python -m unittest tests.secrets.test_manager_unittest -v

Also collected by pytest automatically when pytest is available.

The KDF is patched to N=2^10 via unittest.mock.patch so tests run fast.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make bossbox importable from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import bossbox.secrets.manager as _m
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
# Helpers
# ---------------------------------------------------------------------------

_FAST_KDF_PATCHES = [
    patch.object(_m, "SCRYPT_N", 1 << 10),
    patch.object(_m, "ARGON2_TIME_COST", 1),
    patch.object(_m, "ARGON2_MEMORY_COST", 8),
    patch.object(_m, "ARGON2_PARALLELISM", 1),
]


def _start_fast_kdf():
    for p in _FAST_KDF_PATCHES:
        p.start()


def _stop_fast_kdf():
    for p in _FAST_KDF_PATCHES:
        p.stop()


def _make_mock_keyring(store=None):
    backing = store if store is not None else {}
    mock = MagicMock()

    def get_password(service, username):
        return backing.get((service, username))

    def set_password(service, username, password):
        backing[(service, username)] = password

    mock.get_password.side_effect = get_password
    mock.set_password.side_effect = set_password
    return mock


class _Base(unittest.TestCase):
    """Base with fast-KDF patches and a temp secrets file."""

    def setUp(self):
        _start_fast_kdf()
        self._tmpdir = tempfile.mkdtemp()
        self.secrets_file = Path(self._tmpdir) / "secrets.enc"
        self.sm = SecretsManager(secrets_file=self.secrets_file)

    def tearDown(self):
        _stop_fast_kdf()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# TestPasswordUnlock
# ---------------------------------------------------------------------------

class TestPasswordUnlock(_Base):

    def test_first_run_creates_file(self):
        self.assertFalse(self.secrets_file.exists())
        self.assertTrue(self.sm.unlock("password", password="hunter2"))
        self.assertTrue(self.secrets_file.exists())

    def test_correct_password_returns_true(self):
        self.sm.unlock("password", password="correct-horse")
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        self.assertTrue(sm2.unlock("password", password="correct-horse"))

    def test_wrong_password_returns_false(self):
        self.sm.unlock("password", password="correct-horse")
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        self.assertFalse(sm2.unlock("password", password="wrong-horse"))

    def test_is_unlocked_false_initially(self):
        self.assertFalse(self.sm.is_unlocked)

    def test_is_unlocked_true_after_unlock(self):
        self.sm.unlock("password", password="pw")
        self.assertTrue(self.sm.is_unlocked)

    def test_is_unlocked_false_after_lock(self):
        self.sm.unlock("password", password="pw")
        self.sm.lock()
        self.assertFalse(self.sm.is_unlocked)

    def test_get_raises_when_locked(self):
        with self.assertRaises(SecretsLockError):
            self.sm.get("anything")

    def test_set_raises_when_locked(self):
        with self.assertRaises(SecretsLockError):
            self.sm.set("key", "value")

    def test_set_and_get_round_trip(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("ANTHROPIC_API_KEY", "sk-ant-test-value")
        self.assertEqual(self.sm.get("ANTHROPIC_API_KEY"), "sk-ant-test-value")

    def test_set_persists_across_instances(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("OPENAI_API_KEY", "sk-openai-persist")
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        sm2.unlock("password", password="pw")
        self.assertEqual(sm2.get("OPENAI_API_KEY"), "sk-openai-persist")

    def test_multiple_secrets_persist(self):
        self.sm.unlock("password", password="pw")
        for i in range(10):
            self.sm.set(f"KEY_{i}", f"value_{i}")
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        sm2.unlock("password", password="pw")
        for i in range(10):
            self.assertEqual(sm2.get(f"KEY_{i}"), f"value_{i}")

    def test_smtp_credentials_round_trip(self):
        self.sm.unlock("password", password="smtp-pw")
        self.sm.set("SMTP_HOST", "smtp.example.com")
        self.sm.set("SMTP_PASS", "correct-horse-battery-staple")
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        sm2.unlock("password", password="smtp-pw")
        self.assertEqual(sm2.get("SMTP_HOST"), "smtp.example.com")
        self.assertEqual(sm2.get("SMTP_PASS"), "correct-horse-battery-staple")

    def test_api_key_round_trip(self):
        self.sm.unlock("password", password="api-pw")
        self.sm.set("ANTHROPIC_API_KEY", "sk-ant-api03-real-key-here")
        self.sm.set("OPENAI_API_KEY", "sk-proj-openai-key-here")
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        sm2.unlock("password", password="api-pw")
        self.assertEqual(sm2.get("ANTHROPIC_API_KEY"), "sk-ant-api03-real-key-here")
        self.assertEqual(sm2.get("OPENAI_API_KEY"), "sk-proj-openai-key-here")

    def test_missing_password_arg_raises(self):
        with self.assertRaises(ValueError):
            self.sm.unlock("password")

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            self.sm.unlock("magic-method")

    def test_wrong_password_leaves_store_locked(self):
        self.sm.unlock("password", password="correct-horse")
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        sm2.unlock("password", password="wrong-horse")
        self.assertFalse(sm2.is_unlocked)


# ---------------------------------------------------------------------------
# TestKeychainUnlock
# ---------------------------------------------------------------------------

class TestKeychainUnlock(_Base):

    def test_first_run_generates_and_stores_key(self):
        mock_kr = _make_mock_keyring()
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm = SecretsManager(secrets_file=self.secrets_file)
            self.assertTrue(sm.unlock("keychain"))
            self.assertTrue(self.secrets_file.exists())
            stored_hex = mock_kr.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USERNAME)
            self.assertIsNotNone(stored_hex)
            self.assertEqual(len(bytes.fromhex(stored_hex)), KEY_SIZE)

    def test_second_unlock_reuses_stored_key(self):
        store = {}
        mock_kr = _make_mock_keyring(store)
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm1 = SecretsManager(secrets_file=self.secrets_file)
            sm1.unlock("keychain")
            sm1.set("X", "hello")
            key1 = bytes(sm1._session_key)
            sm1.lock()
            sm2 = SecretsManager(secrets_file=self.secrets_file)
            sm2.unlock("keychain")
            self.assertEqual(bytes(sm2._session_key), key1)
            self.assertEqual(sm2.get("X"), "hello")

    def test_missing_keychain_entry_raises_decrypt_error(self):
        store = {}
        mock_kr = _make_mock_keyring(store)
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm = SecretsManager(secrets_file=self.secrets_file)
            sm.unlock("keychain")
            sm.lock()
        empty_kr = _make_mock_keyring({})
        with patch("bossbox.secrets.manager._import_keyring", return_value=empty_kr):
            sm2 = SecretsManager(secrets_file=self.secrets_file)
            with self.assertRaises(SecretsDecryptError):
                sm2._unlock_keychain()

    def test_keyring_import_failure_raises_method_error(self):
        with patch(
            "bossbox.secrets.manager._import_keyring",
            side_effect=SecretsMethodError("keyring not installed"),
        ):
            with self.assertRaises(SecretsMethodError):
                self.sm.unlock("keychain")

    def test_keychain_set_get_round_trip(self):
        mock_kr = _make_mock_keyring()
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm = SecretsManager(secrets_file=self.secrets_file)
            sm.unlock("keychain")
            sm.set("ANTHROPIC_API_KEY", "sk-ant-keychain-test")
            self.assertEqual(sm.get("ANTHROPIC_API_KEY"), "sk-ant-keychain-test")

    def test_keychain_persists_across_instances(self):
        store = {}
        mock_kr = _make_mock_keyring(store)
        with patch("bossbox.secrets.manager._import_keyring", return_value=mock_kr):
            sm1 = SecretsManager(secrets_file=self.secrets_file)
            sm1.unlock("keychain")
            sm1.set("PERSIST_ME", "value-survives-restart")
            sm1.lock()
            sm2 = SecretsManager(secrets_file=self.secrets_file)
            sm2.unlock("keychain")
            self.assertEqual(sm2.get("PERSIST_ME"), "value-survives-restart")


# ---------------------------------------------------------------------------
# TestLockAndMemory
# ---------------------------------------------------------------------------

class TestLockAndMemory(_Base):

    def test_lock_clears_session_key(self):
        self.sm.unlock("password", password="pw")
        self.assertIsNotNone(self.sm._session_key)
        self.sm.lock()
        self.assertIsNone(self.sm._session_key)

    def test_lock_clears_secrets_dict(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("K", "v")
        self.sm.lock()
        self.assertEqual(self.sm._secrets, {})

    def test_lock_clears_method(self):
        self.sm.unlock("password", password="pw")
        self.sm.lock()
        self.assertIsNone(self.sm._method)

    def test_get_after_lock_raises(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("K", "v")
        self.sm.lock()
        with self.assertRaises(SecretsLockError):
            self.sm.get("K")

    def test_set_after_lock_raises(self):
        self.sm.unlock("password", password="pw")
        self.sm.lock()
        with self.assertRaises(SecretsLockError):
            self.sm.set("K", "v")

    def test_re_unlock_after_lock(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("K", "v")
        self.sm.lock()
        self.sm.unlock("password", password="pw")
        self.assertEqual(self.sm.get("K"), "v")

    def test_multiple_lock_calls_idempotent(self):
        self.sm.unlock("password", password="pw")
        self.sm.lock()
        self.sm.lock()  # must not raise

    def test_session_key_not_written_to_disk(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("SECRET", "canary-value")
        key_bytes = bytes(self.sm._session_key)
        for f in Path(self._tmpdir).iterdir():
            if f.is_file():
                content = f.read_bytes()
                self.assertNotIn(key_bytes, content,
                    f"Session key bytes found verbatim in {f.name}")


# ---------------------------------------------------------------------------
# TestPersistence
# ---------------------------------------------------------------------------

class TestPersistence(_Base):

    def test_file_does_not_contain_secret_in_plaintext(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("SECRET_VALUE", "this-must-not-appear-in-plaintext")
        raw = self.secrets_file.read_bytes()
        self.assertNotIn(b"this-must-not-appear-in-plaintext", raw)

    def test_file_is_not_json_parseable(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("API_KEY", "sk-secret")
        raw = self.secrets_file.read_bytes()
        with self.assertRaises((json.JSONDecodeError, UnicodeDecodeError)):
            json.loads(raw)

    def test_magic_bytes_present(self):
        self.sm.unlock("password", password="pw")
        self.assertEqual(self.secrets_file.read_bytes()[:4], MAGIC)

    def test_version_byte_correct(self):
        self.sm.unlock("password", password="pw")
        self.assertEqual(self.secrets_file.read_bytes()[4], FORMAT_VERSION)

    def test_file_size_grows_with_secrets(self):
        self.sm.unlock("password", password="pw")
        size_empty = self.secrets_file.stat().st_size
        self.sm.set("LONG_KEY", "x" * 1000)
        size_full = self.secrets_file.stat().st_size
        self.assertGreater(size_full, size_empty)

    @unittest.skipIf(os.name == "nt", "chmod not applicable on Windows")
    def test_file_permissions_unix(self):
        self.sm.unlock("password", password="pw")
        mode = self.secrets_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"Expected 0o600, got {oct(mode)}")

    def test_ciphertext_byte_flip_detected(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("K", "v")
        raw = bytearray(self.secrets_file.read_bytes())
        raw[HEADER_SIZE + 8] ^= 0xFF
        self.secrets_file.write_bytes(bytes(raw))
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        self.assertFalse(sm2.unlock("password", password="pw"))

    def test_aad_tampering_detected(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("K", "v")
        raw = bytearray(self.secrets_file.read_bytes())
        raw[5] ^= 0x01   # method byte at offset 5 is part of AAD
        self.secrets_file.write_bytes(bytes(raw))
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        self.assertFalse(sm2.unlock("password", password="pw"))

    def test_bad_magic_returns_false(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("K", "v")
        raw = bytearray(self.secrets_file.read_bytes())
        raw[0:4] = b"XXXX"
        self.secrets_file.write_bytes(bytes(raw))
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        self.assertFalse(sm2.unlock("password", password="pw"))

    def test_salt_reused_across_saves(self):
        self.sm.unlock("password", password="pw")
        salt_before = self.sm._read_header()["salt"]
        self.sm.set("A", "1")
        self.sm.set("B", "2")
        self.sm.delete("A")
        salt_after = self.sm._read_header()["salt"]
        self.assertEqual(salt_before, salt_after)

    def test_nonce_changes_on_each_save(self):
        self.sm.unlock("password", password="pw")
        nonce1 = self.sm._read_header()["nonce"]
        self.sm.set("X", "1")
        nonce2 = self.sm._read_header()["nonce"]
        self.assertNotEqual(nonce1, nonce2)

    def test_parent_directory_created_automatically(self):
        deep = Path(self._tmpdir) / "a" / "b" / "c" / "secrets.enc"
        sm2 = SecretsManager(secrets_file=deep)
        sm2.unlock("password", password="pw")
        self.assertTrue(deep.exists())


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases(_Base):

    def test_get_missing_key_raises_key_error(self):
        self.sm.unlock("password", password="pw")
        with self.assertRaises(KeyError):
            self.sm.get("NONEXISTENT_KEY")

    def test_delete_missing_key_raises_key_error(self):
        self.sm.unlock("password", password="pw")
        with self.assertRaises(KeyError):
            self.sm.delete("NONEXISTENT_KEY")

    def test_overwrite_existing_key(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("K", "original")
        self.sm.set("K", "updated")
        self.assertEqual(self.sm.get("K"), "updated")

    def test_empty_string_value(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("EMPTY", "")
        self.assertEqual(self.sm.get("EMPTY"), "")

    def test_unicode_value_round_trip(self):
        self.sm.unlock("password", password="pw")
        val = "Japanese: \u65e5\u672c\u8a9e\U0001f510"
        self.sm.set("UNICODE", val)
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        sm2.unlock("password", password="pw")
        self.assertEqual(sm2.get("UNICODE"), val)

    def test_long_value_round_trip(self):
        self.sm.unlock("password", password="pw")
        long_val = "x" * 10_000
        self.sm.set("LONG", long_val)
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        sm2.unlock("password", password="pw")
        self.assertEqual(sm2.get("LONG"), long_val)

    def test_truncated_file_returns_false(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("K", "v")
        self.secrets_file.write_bytes(self.secrets_file.read_bytes()[:10])
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        self.assertFalse(sm2.unlock("password", password="pw"))

    def test_token_missing_pkcs11_lib_raises(self):
        with self.assertRaises(ValueError):
            self.sm.unlock("token")

    def test_token_missing_pin_raises(self):
        with self.assertRaises(ValueError):
            self.sm.unlock("token", pkcs11_lib="/usr/lib/libsofthsm2.so")

    def test_token_import_failure_raises_method_error(self):
        with patch(
            "bossbox.secrets.manager._import_pkcs11",
            side_effect=SecretsMethodError("python-pkcs11 not installed"),
        ):
            with self.assertRaises(SecretsMethodError):
                self.sm.unlock(
                    "token", pkcs11_lib="/usr/lib/lib.so", pkcs11_pin="1234"
                )

    def test_empty_secrets_on_first_run(self):
        self.sm.unlock("password", password="pw")
        self.assertEqual(self.sm.list_keys(), [])

    def test_delete_persists_across_instances(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("TEMP", "temp-value")
        self.sm.delete("TEMP")
        self.sm.lock()
        sm2 = SecretsManager(secrets_file=self.secrets_file)
        sm2.unlock("password", password="pw")
        self.assertNotIn("TEMP", sm2.list_keys())


# ---------------------------------------------------------------------------
# TestReprAndHelpers
# ---------------------------------------------------------------------------

class TestReprAndHelpers(_Base):

    def test_repr_shows_locked(self):
        self.assertIn("locked", repr(self.sm))

    def test_repr_shows_unlocked(self):
        self.sm.unlock("password", password="pw")
        self.assertIn("unlocked", repr(self.sm))

    def test_list_keys_returns_correct_names(self):
        self.sm.unlock("password", password="pw")
        self.sm.set("A", "1")
        self.sm.set("B", "2")
        self.sm.set("C", "3")
        self.assertEqual(set(self.sm.list_keys()), {"A", "B", "C"})

    def test_list_keys_raises_when_locked(self):
        with self.assertRaises(SecretsLockError):
            self.sm.list_keys()

    def test_delete_raises_when_locked(self):
        with self.assertRaises(SecretsLockError):
            self.sm.delete("K")

    def test_header_size_constant_is_correct(self):
        self.assertEqual(struct.calcsize(HEADER_STRUCT), HEADER_SIZE)
        self.assertEqual(HEADER_SIZE, 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
