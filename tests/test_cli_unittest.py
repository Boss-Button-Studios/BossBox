"""
CLI Tests (unittest) — BossBox Atomic Step 16
===============================================
Stdlib unittest mirror of test_cli.py.
Runnable with: python -m unittest tests.test_cli_unittest -v
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

import bossbox.cli as cli_module
from bossbox.cli import _build_parser, _run


def _run_async(coro):
    return asyncio.run(coro)


def _decomp_yaml():
    return yaml.dump({
        "decomposition": {
            "reasoning": "Two clear steps.",
            "core_tasks": [
                {"title": "Write function", "description": "Create the code."},
                {"title": "Add tests", "description": "Write pytest tests."},
            ],
            "suggested_tasks": [],
        }
    })


def _make_ollama(exec_resp="Here is the result."):
    provider = MagicMock()
    provider.complete = AsyncMock(side_effect=[_decomp_yaml(), exec_resp])
    return provider


class TestArgumentParserUnittest(unittest.TestCase):

    def test_goal_required(self):
        with self.assertRaises(SystemExit):
            _build_parser().parse_args([])

    def test_goal_parsed(self):
        args = _build_parser().parse_args(["do something"])
        self.assertEqual(args.goal, "do something")

    def test_auto_flag(self):
        args = _build_parser().parse_args(["goal", "--auto"])
        self.assertTrue(args.auto)

    def test_auto_default_false(self):
        args = _build_parser().parse_args(["goal"])
        self.assertFalse(args.auto)

    def test_redirect_flag(self):
        args = _build_parser().parse_args(["goal", "--redirect", "focus on X"])
        self.assertEqual(args.redirect, "focus on X")

    def test_redirect_default_none(self):
        self.assertIsNone(_build_parser().parse_args(["goal"]).redirect)

    def test_model_flag(self):
        args = _build_parser().parse_args(["goal", "--model", "smollm:360m"])
        self.assertEqual(args.model, "smollm:360m")

    def test_model_default(self):
        self.assertEqual(_build_parser().parse_args(["goal"]).model, "smollm:1.7b")

    def test_no_color_flag(self):
        args = _build_parser().parse_args(["goal", "--no-color"])
        self.assertTrue(args.no_color)


class TestRunAutoModeUnittest(unittest.TestCase):

    def _run(self, tmp_path, **kwargs):
        defaults = dict(goal="do something", auto=True, redirect=None, model="smollm:1.7b")
        defaults.update(kwargs)
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(tmp_path) / "audit.log"), \
             patch("sys.stdout", new_callable=StringIO):
            return _run_async(_run(**defaults))

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_auto_mode_returns_zero(self):
        self.assertEqual(self._run(self._tmpdir.name), 0)

    def test_auto_mode_does_not_prompt(self):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("bossbox.cli._async_input") as mock_input, \
             patch("sys.stdout", new_callable=StringIO):
            _run_async(_run(goal="g", auto=True, redirect=None, model="smollm:1.7b"))
        mock_input.assert_not_called()


class TestRunOutputUnittest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_stage_transitions_printed(self):
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("sys.stdout", out):
            _run_async(_run(goal="goal", auto=True, redirect=None, model="smollm:1.7b"))
        output = out.getvalue()
        for stage in ("ingest", "decompose", "execute", "review", "complete"):
            self.assertIn(stage, output)

    def test_result_printed(self):
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama(exec_resp="My answer.")), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("sys.stdout", out):
            _run_async(_run(goal="goal", auto=True, redirect=None, model="smollm:1.7b"))
        self.assertIn("My answer.", out.getvalue())

    def test_goal_in_header(self):
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("sys.stdout", out):
            _run_async(_run(goal="unique-goal-xyz", auto=True, redirect=None, model="smollm:1.7b"))
        self.assertIn("unique-goal-xyz", out.getvalue())


class TestRunCheckpointUnittest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_checkpoint_yes_completes(self):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("bossbox.cli._async_input", new_callable=AsyncMock, return_value="y"), \
             patch("sys.stdout", new_callable=StringIO):
            code = _run_async(_run(goal="goal", auto=False, redirect=None, model="smollm:1.7b"))
        self.assertEqual(code, 0)

    def test_checkpoint_no_aborts(self):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("bossbox.cli._async_input", new_callable=AsyncMock, return_value="n"), \
             patch("sys.stdout", new_callable=StringIO):
            code = _run_async(_run(goal="goal", auto=False, redirect=None, model="smollm:1.7b"))
        self.assertEqual(code, 1)

    def test_redirect_flag_no_prompt(self):
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("bossbox.cli._async_input") as mock_input, \
             patch("sys.stdout", new_callable=StringIO):
            code = _run_async(_run(goal="goal", auto=False,
                                   redirect="focus on speed", model="smollm:1.7b"))
        self.assertEqual(code, 0)
        mock_input.assert_not_called()

    def test_plan_printed_at_checkpoint(self):
        out = StringIO()
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("bossbox.cli._async_input", new_callable=AsyncMock, return_value="y"), \
             patch("sys.stdout", out):
            _run_async(_run(goal="goal", auto=False, redirect=None, model="smollm:1.7b"))
        self.assertIn("Write function", out.getvalue())


class TestRunAuditLogUnittest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_audit_log_written(self):
        audit_path = Path(self._tmpdir.name) / "audit.log"
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", audit_path), \
             patch("sys.stdout", new_callable=StringIO):
            _run_async(_run(goal="goal", auto=True, redirect=None, model="smollm:1.7b"))
        self.assertTrue(audit_path.exists())
        self.assertIn("stage_transition", audit_path.read_text())

    def test_audit_single_task_id(self):
        audit_path = Path(self._tmpdir.name) / "audit.log"
        with patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", audit_path), \
             patch("sys.stdout", new_callable=StringIO):
            _run_async(_run(goal="goal", auto=True, redirect=None, model="smollm:1.7b"))
        entries = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        ids = {e.get("task_id") for e in entries if e.get("task_id")}
        self.assertEqual(len(ids), 1)


class TestMainUnittest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_main_exits_zero(self):
        with patch("sys.argv", ["bossbox", "do something", "--auto"]), \
             patch("bossbox.cli.OllamaProvider", return_value=_make_ollama()), \
             patch("bossbox.cli._DEFAULT_AUDIT_PATH", Path(self._tmpdir.name) / "a.log"), \
             patch("sys.stdout", new_callable=StringIO), \
             self.assertRaises(SystemExit) as ctx:
            cli_module.main()
        self.assertEqual(ctx.exception.code, 0)

    def test_main_no_goal_exits_nonzero(self):
        with patch("sys.argv", ["bossbox"]), \
             patch("sys.stderr", new_callable=StringIO), \
             self.assertRaises(SystemExit) as ctx:
            cli_module.main()
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
