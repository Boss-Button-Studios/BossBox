"""
Exceptions for the BossBox VRAM subsystem — BossBox Atomic Step 8
==================================================================
"""
from __future__ import annotations


class VRAMException(Exception):
    """Base class for all VRAM-budgeter errors."""


class VRAMBudgetError(VRAMException):
    """Raised when a model cannot be loaded even after all evictable models are removed."""


class VRAMDetectionError(VRAMException):
    """Raised when platform VRAM detection fails with an unexpected error."""
