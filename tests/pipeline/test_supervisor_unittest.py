"""
Supervisor State Machine Tests (unittest) — BossBox Atomic Step 15
===================================================================
Stdlib unittest mirror of test_supervisor.py.
Runnable with: python -m unittest tests.pipeline.test_supervisor_unittest -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

import yaml

from bossbox.audit.logger import AuditLogger
from bossbox.pipeline.envelope import create_envelope
from bossbox.pipeline.supervisor import PassthroughShield, ShieldProtocol, Supervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _decomp_response():
    return yaml.dump({
        "decomposition": {
            "reasoning": "Two steps.",
            "core_tasks": [
                {"title": "Step 1", "description": "Do first thing."},
                {"title": "Step 2", "description": "Do second thing."},
            ],
            "suggested_tasks": [],
        }
    })


def _make_provider(exec_resp="Done."):
    provider = MagicMock()
    provider.complete = AsyncMock(side_effect=[_decomp_response(), exec_resp])
    return provider


def _make_audit(tmp_dir):
    import tempfile
    from pathlib import Path
    return AuditLogger(log_path=Path(tmp_dir) / "audit.log")


class _Base(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.audit = _make_audit(self._tmpdir.name)
        self.envelope = create_envelope("Write and test a script.")
        self.envelope_auto = create_envelope("Quick task.", auto_approve=True)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _sv(self, envelope=None, **kwargs):
        env = envelope or self.envelope
        return Supervisor(
            envelope=env,
            provider=_make_provider(),
            audit_logger=self.audit,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# All stages traversed
# ---------------------------------------------------------------------------

class TestAllStagesUnittest(_Base):

    def test_status_complete(self):
        sv = self._sv(envelope=self.envelope_auto)
        _run(sv.run())
        self.assertEqual(self.envelope_auto.status, "complete")

    def test_result_populated(self):
        sv = self._sv(envelope=self.envelope_auto)
        _run(sv.run())
        self.assertIsNotNone(self.envelope_auto.result)

    def test_thought_stream_has_entries(self):
        sv = self._sv(envelope=self.envelope_auto)
        _run(sv.run())
        self.assertGreater(len(self.envelope_auto.thought_stream), 0)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestAuditLogUnittest(_Base):

    def test_all_stage_transitions_logged(self):
        sv = self._sv(envelope=self.envelope_auto)
        _run(sv.run())
        entries = self.audit.read_all()
        transition_stages = {
            e["data"]["stage"]
            for e in entries
            if e["event_type"] == "stage_transition"
        }
        self.assertEqual(transition_stages, set(Supervisor.STAGES))

    def test_audit_entries_carry_task_id(self):
        sv = self._sv(envelope=self.envelope_auto)
        _run(sv.run())
        for entry in self.audit.read_all():
            self.assertEqual(entry["task_id"], self.envelope_auto.task_id)

    def test_shield_events_in_audit(self):
        sv = self._sv(envelope=self.envelope_auto)
        _run(sv.run())
        event_types = {e["event_type"] for e in self.audit.read_all()}
        self.assertIn("input_shield", event_types)
        self.assertIn("action_shield", event_types)


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

class TestCheckpointUnittest(_Base):

    def test_checkpoint_pauses_then_resumes(self):
        sv = self._sv()

        async def _scenario():
            run_task = asyncio.create_task(sv.run())
            await asyncio.sleep(0.05)
            self.assertEqual(self.envelope.status, "paused")
            await sv.approve_checkpoint()
            await run_task
            self.assertEqual(self.envelope.status, "complete")

        _run(_scenario())

    def test_checkpoint_events_logged(self):
        sv = self._sv()

        async def _scenario():
            run_task = asyncio.create_task(sv.run())
            await asyncio.sleep(0.05)
            await sv.approve_checkpoint()
            await run_task

        _run(_scenario())
        event_types = {e["event_type"] for e in self.envelope.events}
        self.assertIn("checkpoint_wait", event_types)
        self.assertIn("checkpoint_approved", event_types)


# ---------------------------------------------------------------------------
# auto_approve
# ---------------------------------------------------------------------------

class TestAutoApproveUnittest(_Base):

    def test_auto_approve_completes(self):
        sv = self._sv(self.envelope_auto)
        _run(sv.run())
        self.assertEqual(self.envelope_auto.status, "complete")

    def test_checkpoint_skipped_event(self):
        sv = self._sv(self.envelope_auto)
        _run(sv.run())
        event_types = {e["event_type"] for e in self.envelope_auto.events}
        self.assertIn("checkpoint_skipped", event_types)
        self.assertNotIn("checkpoint_wait", event_types)


# ---------------------------------------------------------------------------
# redirect
# ---------------------------------------------------------------------------

class TestRedirectUnittest(_Base):

    def test_redirect_resumes_and_completes(self):
        sv = self._sv()

        async def _scenario():
            run_task = asyncio.create_task(sv.run())
            await asyncio.sleep(0.05)
            await sv.redirect("Focus only on core tasks.")
            await run_task
            self.assertEqual(self.envelope.status, "complete")

        _run(_scenario())

    def test_redirect_appends_to_context(self):
        sv = self._sv()

        async def _scenario():
            run_task = asyncio.create_task(sv.run())
            await asyncio.sleep(0.05)
            await sv.redirect("New direction.")
            await run_task

        _run(_scenario())
        redirects = [e for e in self.envelope.context if e.get("type") == "redirect"]
        self.assertEqual(len(redirects), 1)

    def test_redirect_no_stage_repeated(self):
        sv = self._sv()

        async def _scenario():
            run_task = asyncio.create_task(sv.run())
            await asyncio.sleep(0.05)
            await sv.redirect("pivot.")
            await run_task

        _run(_scenario())
        entries = self.audit.read_all()
        stages = [e["data"]["stage"] for e in entries if e["event_type"] == "stage_transition"]
        self.assertEqual(len(stages), len(set(stages)))


# ---------------------------------------------------------------------------
# Security checkpoints
# ---------------------------------------------------------------------------

class TestSecurityUnittest(_Base):

    def test_input_shield_block_fails_pipeline(self):
        shield = MagicMock(spec=ShieldProtocol)
        shield.evaluate_input = AsyncMock(return_value=False)
        shield.evaluate_action = AsyncMock(return_value=True)
        sv = Supervisor(
            envelope=self.envelope,
            provider=_make_provider(),
            audit_logger=self.audit,
            input_shield=shield,
        )
        _run(sv.run())
        self.assertEqual(self.envelope.status, "failed")

    def test_action_shield_block_fails_pipeline(self):
        shield = MagicMock(spec=ShieldProtocol)
        shield.evaluate_action = AsyncMock(return_value=False)
        sv = Supervisor(
            envelope=self.envelope_auto,
            provider=_make_provider(),
            audit_logger=self.audit,
            action_shield=shield,
        )
        _run(sv.run())
        self.assertEqual(self.envelope_auto.status, "failed")

    def test_action_shield_called_at_least_twice(self):
        shield = MagicMock(spec=ShieldProtocol)
        shield.evaluate_action = AsyncMock(return_value=True)
        sv = Supervisor(
            envelope=self.envelope_auto,
            provider=_make_provider(),
            audit_logger=self.audit,
            action_shield=shield,
        )
        _run(sv.run())
        self.assertGreaterEqual(shield.evaluate_action.call_count, 2)


# ---------------------------------------------------------------------------
# abort / pause
# ---------------------------------------------------------------------------

class TestAbortPauseUnittest(_Base):

    def test_abort_sets_failed(self):
        sv = self._sv()
        sv.abort()
        self.assertEqual(self.envelope.status, "failed")

    def test_abort_during_checkpoint(self):
        sv = self._sv()

        async def _scenario():
            run_task = asyncio.create_task(sv.run())
            await asyncio.sleep(0.05)
            sv.abort()
            await run_task

        _run(_scenario())
        self.assertEqual(self.envelope.status, "failed")

    def test_pause_sets_paused(self):
        sv = self._sv()
        sv.pause()
        self.assertEqual(self.envelope.status, "paused")


# ---------------------------------------------------------------------------
# VRAM budgeter
# ---------------------------------------------------------------------------

class TestVRAMUnittest(_Base):

    def test_vram_consulted(self):
        vram = MagicMock()
        vram.request_load = MagicMock(return_value=True)
        sv = Supervisor(
            envelope=self.envelope_auto,
            provider=_make_provider(),
            audit_logger=self.audit,
            vram_budgeter=vram,
            model="smollm:1.7b",
        )
        _run(sv.run())
        self.assertGreaterEqual(vram.request_load.call_count, 1)

    def test_run_without_vram(self):
        sv = self._sv(self.envelope_auto)
        _run(sv.run())
        self.assertEqual(self.envelope_auto.status, "complete")


# ---------------------------------------------------------------------------
# PassthroughShield
# ---------------------------------------------------------------------------

class TestPassthroughUnittest(unittest.TestCase):

    def test_input_always_true(self):
        s = PassthroughShield()
        self.assertTrue(_run(s.evaluate_input("g", "c")))

    def test_action_always_true(self):
        s = PassthroughShield()
        self.assertTrue(_run(s.evaluate_action("g", "a")))


if __name__ == "__main__":
    unittest.main()
