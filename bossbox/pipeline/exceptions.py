"""
Exceptions for the BossBox pipeline subsystem — BossBox Atomic Step 13
=======================================================================
"""
from __future__ import annotations


class PipelineError(Exception):
    """Base class for all pipeline errors."""


class OutsideWorkAreaError(PipelineError):
    """
    Raised when an operation targets a path outside the sandboxed work area.

    BossBox writes only to ``~/.bossbox/workspace/``.  Any attempt to read
    from or write to a path outside this boundary raises this error.
    """
