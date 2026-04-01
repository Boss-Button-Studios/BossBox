"""
Exceptions for the BossBox ingest subsystem — BossBox Atomic Step 9
====================================================================
"""
from __future__ import annotations


class SanitizerError(Exception):
    """Base class for all physical-sanitizer errors."""


class SanitizerFormatError(SanitizerError):
    """Raised when the document format cannot be determined or is unsupported."""


class SanitizerDeepModeError(SanitizerError):
    """Raised when deep-mode (OCR) sanitization is requested but tesseract is unavailable."""
