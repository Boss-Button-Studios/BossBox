"""
Test configuration — BossBox
==============================
Shared fixtures for the full test suite.

VRAMBudgeter is patched for all tests that exercise the CLI (_run).
The real budgeter starts a background polling thread and makes live HTTP calls
to Ollama, which breaks sys.stdout patching in isolated unit tests.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bossbox.vram.budgeter import LoadStrategy


def _make_vram_mock() -> MagicMock:
    vram = MagicMock()
    vram.request_load.return_value = LoadStrategy(
        model="smollm:1.7b", num_gpu=-1, mode="gpu"
    )
    return vram


@pytest.fixture(autouse=True)
def _patch_vram_budgeter():
    """Replace VRAMBudgeter in the CLI with a no-op mock for every test."""
    with patch("bossbox.cli.VRAMBudgeter", return_value=_make_vram_mock()):
        yield
