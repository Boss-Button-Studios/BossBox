"""
bossbox.secrets
===============

Encrypted-at-rest credential store for BossBox.

Three unlock methods: OS keychain, passphrase (Argon2id), PKCS#11 hardware token.
Session key held in process memory only — never written to disk.
"""

from bossbox.secrets.exceptions import (
    SecretsDecryptError,
    SecretsError,
    SecretsLockError,
    SecretsMethodError,
)
from bossbox.secrets.manager import SecretsManager

__all__ = [
    "SecretsManager",
    "SecretsError",
    "SecretsLockError",
    "SecretsDecryptError",
    "SecretsMethodError",
]
