"""
CLI Tests — BossBox Atomic Step 16
====================================
All Ollama calls are mocked.  Interactive input is patched.
"""
from __future__ import annotations

import asyncio
import sys
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import bossbox.cli as cli_module
from bossbox.cli import _build_parser, _run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decomp_yaml():
    return yaml.dump({
        "decomposition": {
            "reasoning": "Two clear steps.",
            "core_tasks": [
                {"title": "Write function", "description": "Create the code."},
                {"title": "Add tests", "description": "Write pytest tests."},
            ],
            "suggested_tasks": [{"title": "Add docs", "description": "Docstring."}],
        }
    })


def _make_ollama(decomp=None, exec_resp="Here is the result."):
    provider = MagicMock()
    provider.complete = AsyncMock(side_effect=[decomp or _decomp_yaml(), exec_resp])
    return provider


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

class TestArgumentParser:

    def test_goal_required(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_goal_parsed(self):
        args = _build_parser().parse_args(["do something"])
        assert args.goal == "do something"

    def test_auto_flag(self):
        args = _build_parser().parse_args(["goal", "--auto"])
        assert args.auto is True

    def test_auto_default_false(self):
        args = _build_parser().parse_args(["goal"])
        assert args.auto is False

    def test_redirect_flag(self):
        args = _build_parser().parse_args(["goal", "--redirect", "focus on X"])
        assert args.redirect == "focus on X"

    def test_redirect_default_none(self):
        args = _build_parser().parse_args(["goal"])
        assert args.redirect is None

    def test_model_flag(self):
        args = _build_parser().parse_args(["goal", "--model", "smollm:360m"])
        assert args.model == "smollm:360m"

    def test_model_default(self):
        args = _build_parser().parse_args(["goal"])
        assert args.model == "smollm:1.7b"

    def test_no_color_flag(self):
        args = _build_parser().parse_args(["goal", "--no-color"])
        assert args.no_color is True


# ---------------------------------------------------------------------------
# _run() — auto mode (no interactive input needed)
# ---------------------------------------------------------------------------

class TestRunAutoMode:

    async def test_auto_mode_returns_zero_on_success(self, tmp_path):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("sys.stdout", new_callable=StringIO):
            code = await _run("do something", auto=True, redirect=None, model="smollm:1.7b")
        assert code == 0

    async def test_auto_mode_completes_pipeline(self, tmp_path):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("sys.stdout", new_callable=StringIO):
            code = await _run("write a function", auto=True, redirect=None, model="smollm:1.7b")
        assert code == 0

    async def test_auto_mode_does_not_prompt(self, tmp_path):
        """--auto should never call input()."""
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("bossbox.cli._async_input") as mock_input, \
             patch("sys.stdout", new_callable=StringIO):
            await _run("goal", auto=True, redirect=None, model="smollm:1.7b")
        mock_input.assert_not_called()


# ---------------------------------------------------------------------------
# _run() — output content
# ---------------------------------------------------------------------------

class TestRunOutput:

    async def test_stage_transitions_printed(self, tmp_path):
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("sys.stdout", out):
            await _run("goal", auto=True, redirect=None, model="smollm:1.7b")
        output = out.getvalue()
        for stage in ("ingest", "decompose", "execute", "review", "complete"):
            assert stage in output, f"Stage '{stage}' not found in output"

    async def test_result_printed(self, tmp_path):
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama(exec_resp="My result.")), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("sys.stdout", out):
            await _run("goal", auto=True, redirect=None, model="smollm:1.7b")
        assert "My result." in out.getvalue()

    async def test_goal_printed_in_header(self, tmp_path):
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("sys.stdout", out):
            await _run("my specific goal", auto=True, redirect=None, model="smollm:1.7b")
        assert "my specific goal" in out.getvalue()

    async def test_thought_stream_printed(self, tmp_path):
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("sys.stdout", out):
            await _run("goal", auto=True, redirect=None, model="smollm:1.7b")
        # The supervisor adds "progress" thoughts; at least one should appear
        assert "→" in out.getvalue() or "Stage" in out.getvalue() or "complete" in out.getvalue()


# ---------------------------------------------------------------------------
# _run() — checkpoint interaction
# ---------------------------------------------------------------------------

class TestRunCheckpoint:

    async def test_checkpoint_prompts_user(self, tmp_path):
        """Without --auto, the CLI should prompt at the checkpoint."""
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("bossbox.cli._async_input", new_callable=AsyncMock, return_value="y"), \
             patch("sys.stdout", new_callable=StringIO):
            code = await _run("goal", auto=False, redirect=None, model="smollm:1.7b")
        assert code == 0

    async def test_checkpoint_yes_completes(self, tmp_path):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("bossbox.cli._async_input", new_callable=AsyncMock, return_value="yes"), \
             patch("sys.stdout", new_callable=StringIO):
            code = await _run("goal", auto=False, redirect=None, model="smollm:1.7b")
        assert code == 0

    async def test_checkpoint_no_aborts(self, tmp_path):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("bossbox.cli._async_input", new_callable=AsyncMock, return_value="n"), \
             patch("sys.stdout", new_callable=StringIO):
            code = await _run("goal", auto=False, redirect=None, model="smollm:1.7b")
        assert code == 1

    async def test_checkpoint_redirect_input_completes(self, tmp_path):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("bossbox.cli._async_input", new_callable=AsyncMock,
                   return_value="r focus on the function only"), \
             patch("sys.stdout", new_callable=StringIO):
            code = await _run("goal", auto=False, redirect=None, model="smollm:1.7b")
        assert code == 0

    async def test_checkpoint_plan_printed(self, tmp_path):
        """The decomposition plan should appear at the checkpoint."""
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("bossbox.cli._async_input", new_callable=AsyncMock, return_value="y"), \
             patch("sys.stdout", out):
            await _run("goal", auto=False, redirect=None, model="smollm:1.7b")
        assert "Write function" in out.getvalue()

    async def test_redirect_flag_applied_at_checkpoint(self, tmp_path):
        """--redirect applies the direction without interactive prompt."""
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("bossbox.cli._async_input") as mock_input, \
             patch("sys.stdout", new_callable=StringIO):
            code = await _run("goal", auto=False,
                               redirect="focus on speed", model="smollm:1.7b")
        assert code == 0
        mock_input.assert_not_called()


# ---------------------------------------------------------------------------
# _run() — audit log
# ---------------------------------------------------------------------------

class TestRunAuditLog:

    async def test_audit_log_contains_run(self, tmp_path):
        audit_path = tmp_path / "audit.log"
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", audit_path), \
             patch("sys.stdout", new_callable=StringIO):
            await _run("my goal", auto=True, redirect=None, model="smollm:1.7b")
        assert audit_path.exists()
        content = audit_path.read_text()
        assert "stage_transition" in content

    async def test_audit_log_contains_task_id(self, tmp_path):
        import json
        audit_path = tmp_path / "audit.log"
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", audit_path), \
             patch("sys.stdout", new_callable=StringIO):
            await _run("my goal", auto=True, redirect=None, model="smollm:1.7b")
        entries = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        task_ids = {e.get("task_id") for e in entries if e.get("task_id")}
        assert len(task_ids) == 1  # all entries share one task_id


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------

class TestMain:

    def test_main_exits_zero_auto(self, tmp_path):
        with patch("sys.argv", ["bossbox", "do something", "--auto"]), \
             patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("sys.stdout", new_callable=StringIO), \
             pytest.raises(SystemExit) as exc_info:
            cli_module.main()
        assert exc_info.value.code == 0

    def test_main_exits_nonzero_no_goal(self, tmp_path):
        with patch("sys.argv", ["bossbox"]), \
             patch("sys.stderr", new_callable=StringIO), \
             pytest.raises(SystemExit) as exc_info:
            cli_module.main()
        assert exc_info.value.code != 0

    def test_no_color_flag_disables_ansi(self, tmp_path):
        out = StringIO()
        with patch("sys.argv", ["bossbox", "goal", "--auto", "--no-color"]), \
             patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", tmp_path / "audit.log"), \
             patch("sys.stdout", out), \
             pytest.raises(SystemExit):
            cli_module.main()
        assert "\033[" not in out.getvalue()
