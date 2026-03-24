"""
Exceptions for the BossBox secrets subsystem.
"""


class SecretsError(Exception):
    """Base class for all secrets-related errors."""


class SecretsLockError(SecretsError):
    """Raised when a secrets operation requires the store to be unlocked."""


class SecretsDecryptError(SecretsError):
    """Raised when decryption fails (wrong password, corrupt or tampered file)."""


class SecretsMethodError(SecretsError):
    """Raised when a required optional dependency is missing for a given method."""
