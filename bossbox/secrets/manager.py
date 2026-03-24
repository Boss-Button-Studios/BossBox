"""
Secrets Manager — BossBox Atomic Step 7
========================================

Three-factor unlock strategy
------------------------------
keychain  — OS keychain (Windows Credential Manager / macOS Keychain / libsecret).
            32-byte AES key generated on first run; stored in the keychain.

password  — User passphrase.  Key derived with Argon2id (preferred) or stdlib
            scrypt (fallback).  Salt lives in the secrets file header.
            Derived key held in process memory only.

token     — PKCS#11 hardware token.  Requires python-pkcs11.

On-disk format  (50-byte header, all fields big-endian)
--------------
  Offset  Size  Field
       0     4  Magic b'BBOX'
       4     1  Format version (1)
       5     1  Method byte  (0=keychain, 1=password, 2=token)
       6    32  Salt
      38    12  AES-GCM nonce
      50     N  Ciphertext (JSON secrets + 16-byte GCM tag)

Header is included as AES-GCM AAD — tampering with it is detected on decrypt.
Session key never written to disk.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bossbox.secrets.exceptions import (
    SecretsDecryptError,
    SecretsLockError,
    SecretsMethodError,
)

# ---------------------------------------------------------------------------
# File-format constants
# ---------------------------------------------------------------------------

MAGIC          = b"BBOX"
FORMAT_VERSION = 1
SALT_SIZE      = 32
NONCE_SIZE     = 12
KEY_SIZE       = 32       # AES-256 -> 32 bytes

METHOD_KEYCHAIN = 0
METHOD_PASSWORD = 1
METHOD_TOKEN    = 2

_METHOD_MAP: dict[str, int] = {
    "keychain": METHOD_KEYCHAIN,
    "password": METHOD_PASSWORD,
    "token":    METHOD_TOKEN,
}

HEADER_STRUCT = f"!4sBB{SALT_SIZE}s{NONCE_SIZE}s"
HEADER_SIZE   = struct.calcsize(HEADER_STRUCT)   # 50 bytes
assert HEADER_SIZE == 50, f"Header size mismatch: {HEADER_SIZE}"

KEYCHAIN_SERVICE  = "boss-button-studios.bossbox"
KEYCHAIN_USERNAME = "session_key"

DEFAULT_SECRETS_DIR  = Path.home() / ".bossbox" / "secrets"
DEFAULT_SECRETS_FILE = DEFAULT_SECRETS_DIR / "secrets.enc"

# KDF tuning — Argon2id (OWASP 2023 interactive-login minimum)
ARGON2_TIME_COST   = 3
ARGON2_MEMORY_COST = 65_536   # 64 MiB
ARGON2_PARALLELISM = 4
ARGON2_HASH_LEN    = KEY_SIZE

# scrypt fallback (N=2^17 -> 128 MiB working set)
SCRYPT_N = 1 << 17
SCRYPT_R = 8
SCRYPT_P = 1


# ---------------------------------------------------------------------------
# Lazy optional-dependency importers
# ---------------------------------------------------------------------------

def _import_keyring():
    try:
        import keyring  # type: ignore[import]
        return keyring
    except ImportError as exc:
        raise SecretsMethodError(
            "OS-keychain unlock requires keyring.  pip install keyring"
        ) from exc


def _import_pkcs11():
    try:
        import pkcs11  # type: ignore[import]
        return pkcs11
    except ImportError as exc:
        raise SecretsMethodError(
            "Hardware-token unlock requires python-pkcs11.  pip install python-pkcs11"
        ) from exc


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def _derive_key(password: str, salt: bytes) -> bytes:
    """Argon2id if argon2-cffi is installed; stdlib scrypt otherwise."""
    try:
        from argon2.low_level import hash_secret_raw, Type  # type: ignore[import]
        return hash_secret_raw(
            secret=password.encode("utf-8"),
            salt=salt,
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST,
            parallelism=ARGON2_PARALLELISM,
            hash_len=ARGON2_HASH_LEN,
            type=Type.ID,
        )
    except ImportError:
        pass
    import hashlib
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_SIZE,
    )


# ---------------------------------------------------------------------------
# SecretsManager
# ---------------------------------------------------------------------------

class SecretsManager:
    """
    Manages BossBox credentials.

    Usage::

        sm = SecretsManager()
        sm.unlock("password", password="passphrase")
        sm.set("ANTHROPIC_API_KEY", "sk-ant-...")
        key = sm.get("ANTHROPIC_API_KEY")
        sm.lock()
    """

    def __init__(self, secrets_file: Path = DEFAULT_SECRETS_FILE) -> None:
        self._secrets_file = Path(secrets_file)
        self._session_key: Optional[bytearray] = None
        self._secrets: dict[str, str] = {}
        self._method: Optional[str] = None

    @property
    def is_unlocked(self) -> bool:
        return self._session_key is not None

    def unlock(
        self,
        method: str,
        *,
        password: Optional[str] = None,
        pkcs11_lib: Optional[str] = None,
        pkcs11_pin: Optional[str] = None,
    ) -> bool:
        if method not in _METHOD_MAP:
            raise ValueError(
                f"Unknown unlock method {method!r}. "
                "Valid choices: 'keychain', 'password', 'token'."
            )
        if method == "password" and password is None:
            raise ValueError(
                "The 'password' method requires the 'password' keyword argument."
            )
        if method == "token" and (pkcs11_lib is None or pkcs11_pin is None):
            raise ValueError(
                "The 'token' method requires both 'pkcs11_lib' and 'pkcs11_pin' arguments."
            )
        if method == "keychain":
            return self._unlock_keychain()
        if method == "password":
            return self._unlock_password(password)  # type: ignore[arg-type]
        return self._unlock_token(pkcs11_lib, pkcs11_pin)  # type: ignore[arg-type]

    def lock(self) -> None:
        if self._session_key is not None:
            for i in range(len(self._session_key)):
                self._session_key[i] = 0
        self._session_key = None
        self._secrets.clear()
        self._method = None

    def get(self, key: str) -> str:
        self._require_unlocked()
        return self._secrets[key]

    def set(self, key: str, value: str) -> None:
        self._require_unlocked()
        self._secrets[key] = value
        self._save()

    def delete(self, key: str) -> None:
        self._require_unlocked()
        del self._secrets[key]
        self._save()

    def list_keys(self) -> list[str]:
        self._require_unlocked()
        return list(self._secrets.keys())

    # ------------------------------------------------------------------
    # Unlock implementations
    # ------------------------------------------------------------------

    def _unlock_keychain(self) -> bool:
        keyring = _import_keyring()
        if self._secrets_file.exists():
            raw_key_hex: Optional[str] = keyring.get_password(
                KEYCHAIN_SERVICE, KEYCHAIN_USERNAME
            )
            if raw_key_hex is None:
                raise SecretsDecryptError(
                    "Secrets file found but keychain entry is missing.  "
                    "The file cannot be decrypted without the original key."
                )
            self._session_key = bytearray(bytes.fromhex(raw_key_hex))
            self._method = "keychain"
            try:
                self._load()
            except SecretsDecryptError:
                self._session_key = None
                self._method = None
                return False
        else:
            raw_key = os.urandom(KEY_SIZE)
            keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_USERNAME, raw_key.hex())
            self._session_key = bytearray(raw_key)
            self._method = "keychain"
            self._secrets = {}
            self._save()
        return True

    def _unlock_password(self, password: str) -> bool:
        if self._secrets_file.exists():
            try:
                header = self._read_header()
            except SecretsDecryptError:
                return False
            salt    = header["salt"]
            derived = _derive_key(password, salt)
            self._session_key = bytearray(derived)
            self._method = "password"
            try:
                self._load()
            except SecretsDecryptError:
                self.lock()
                return False
        else:
            salt    = os.urandom(SALT_SIZE)
            derived = _derive_key(password, salt)
            self._session_key = bytearray(derived)
            self._method = "password"
            self._secrets = {}
            self._save(salt=salt)
        return True

    def _unlock_token(  # pragma: no cover
        self,
        pkcs11_lib: str,
        pkcs11_pin: str,
    ) -> bool:
        """PKCS#11 path.  Excluded from unit tests — requires physical hardware."""
        pkcs11 = _import_pkcs11()
        lib    = pkcs11.lib(pkcs11_lib)
        slot   = next(iter(lib.get_slots(token_present=True)))
        token  = slot.get_token()
        with token.open(user_pin=pkcs11_pin) as session:
            try:
                key_obj = next(session.get_objects({
                    pkcs11.Attribute.LABEL: "bossbox_secrets_key",
                    pkcs11.Attribute.CLASS: pkcs11.ObjectClass.SECRET_KEY,
                }))
                raw_key = bytes(key_obj[pkcs11.Attribute.VALUE])
            except StopIteration:
                key_obj = session.generate_key(
                    pkcs11.KeyType.AES, 256,
                    label="bossbox_secrets_key", store=True,
                    capabilities=(
                        pkcs11.MechanismFlag.ENCRYPT | pkcs11.MechanismFlag.DECRYPT
                    ),
                )
                raw_key = bytes(key_obj[pkcs11.Attribute.VALUE])
        if len(raw_key) != KEY_SIZE:
            from bossbox.secrets.exceptions import SecretsError
            raise SecretsError(
                f"Token returned {len(raw_key)*8}-bit key; expected 256-bit."
            )
        self._session_key = bytearray(raw_key)
        self._method = "token"
        if self._secrets_file.exists():
            try:
                self._load()
            except SecretsDecryptError:
                self.lock()
                return False
        else:
            self._secrets = {}
            self._save()
        return True

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _save(self, salt: Optional[bytes] = None) -> None:
        self._secrets_file.parent.mkdir(parents=True, exist_ok=True)
        if salt is None:
            if self._secrets_file.exists():
                try:
                    salt = self._read_header()["salt"]
                except SecretsDecryptError:
                    salt = os.urandom(SALT_SIZE)
            else:
                salt = os.urandom(SALT_SIZE)

        nonce       = os.urandom(NONCE_SIZE)
        method_byte = _METHOD_MAP.get(self._method or "password", METHOD_PASSWORD)
        header_bytes = struct.pack(
            HEADER_STRUCT, MAGIC, FORMAT_VERSION, method_byte, salt, nonce
        )
        plaintext  = json.dumps(self._secrets, separators=(",", ":")).encode("utf-8")
        aesgcm     = AESGCM(bytes(self._session_key))   # type: ignore[arg-type]
        ciphertext = aesgcm.encrypt(nonce, plaintext, header_bytes)

        tmp_path = self._secrets_file.with_suffix(".tmp")
        tmp_path.write_bytes(header_bytes + ciphertext)
        tmp_path.replace(self._secrets_file)

        try:
            os.chmod(self._secrets_file, 0o600)
        except (AttributeError, NotImplementedError, OSError):
            pass

    def _load(self) -> None:
        raw = self._secrets_file.read_bytes()
        if len(raw) < HEADER_SIZE + 16:
            raise SecretsDecryptError("Secrets file is truncated or corrupt.")

        header_bytes = raw[:HEADER_SIZE]
        ciphertext   = raw[HEADER_SIZE:]
        magic, version, _method_byte, _salt, nonce = struct.unpack(
            HEADER_STRUCT, header_bytes
        )
        if magic != MAGIC:
            raise SecretsDecryptError(
                "Unexpected magic header — file may be corrupt or from another app."
            )
        if version != FORMAT_VERSION:
            raise SecretsDecryptError(
                f"Unsupported secrets-file format version {version}."
            )
        aesgcm = AESGCM(bytes(self._session_key))   # type: ignore[arg-type]
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, header_bytes)
        except InvalidTag as exc:
            raise SecretsDecryptError(
                "Decryption failed — wrong password or tampered file."
            ) from exc
        self._secrets = json.loads(plaintext.decode("utf-8"))

    def _read_header(self) -> dict:
        raw = self._secrets_file.read_bytes()
        if len(raw) < HEADER_SIZE:
            raise SecretsDecryptError(
                f"Secrets file too short ({len(raw)} bytes; "
                f"expected >= {HEADER_SIZE})."
            )
        magic, version, method_byte, salt, nonce = struct.unpack(
            HEADER_STRUCT, raw[:HEADER_SIZE]
        )
        return {
            "magic": magic, "version": version,
            "method": method_byte, "salt": salt, "nonce": nonce,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_unlocked(self) -> None:
        if not self.is_unlocked:
            raise SecretsLockError(
                "SecretsManager is locked.  Call unlock() before accessing secrets."
            )

    def __repr__(self) -> str:
        status = "unlocked" if self.is_unlocked else "locked"
        return f"<SecretsManager [{status}] path={self._secrets_file}>"

    def __del__(self) -> None:
        try:
            self.lock()
        except Exception:
            pass
