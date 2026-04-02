"""
Supervisor State Machine Tests — BossBox Atomic Step 15
========================================================
All provider calls are mocked — no Ollama instance required.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from bossbox.audit.logger import AuditLogger
from bossbox.pipeline.envelope import create_envelope
from bossbox.pipeline.supervisor import PassthroughShield, ShieldProtocol, Supervisor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_logger(tmp_path):
    return AuditLogger(log_path=tmp_path / "audit.log")


@pytest.fixture
def envelope():
    return create_envelope("Write a hello-world script and test it.")


@pytest.fixture
def envelope_auto():
    return create_envelope("Do a quick task.", auto_approve=True)


def _decomp_response():
    return yaml.dump({
        "decomposition": {
            "reasoning": "Two clear steps.",
            "core_tasks": [
                {"title": "Write script", "description": "Create hello.py"},
                {"title": "Test script", "description": "Run pytest"},
            ],
            "suggested_tasks": [{"title": "Add docs", "description": "Write README"}],
        }
    })


def _make_provider(decomp_resp=None, exec_resp="Done."):
    provider = MagicMock()
    responses = [decomp_resp or _decomp_response(), exec_resp]
    provider.complete = AsyncMock(side_effect=responses)
    return provider


def _make_supervisor(envelope, audit_logger, *, auto_provider=True,
                     input_shield=None, action_shield=None,
                     vram_budgeter=None, model=None):
    provider = _make_provider() if auto_provider else None
    return Supervisor(
        envelope=envelope,
        provider=provider,
        audit_logger=audit_logger,
        input_shield=input_shield,
        action_shield=action_shield,
        vram_budgeter=vram_budgeter,
        model=model,
    ), provider


# ---------------------------------------------------------------------------
# Acceptance: all stages traversed
# ---------------------------------------------------------------------------

class TestAllStagesTraversed:

    async def test_run_returns_envelope(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        result = await sv.run()
        assert result is envelope_auto

    async def test_status_complete_after_run(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        assert envelope_auto.status == "complete"

    async def test_final_stage_is_complete(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        assert envelope_auto.current_stage == "complete"

    async def test_result_populated_after_run(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        assert envelope_auto.result is not None

    async def test_thought_stream_has_entries(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        assert len(envelope_auto.thought_stream) > 0


# ---------------------------------------------------------------------------
# Acceptance: transitions in audit log
# ---------------------------------------------------------------------------

class TestAuditLog:

    async def test_all_stage_transitions_logged(self, envelope_auto, audit_logger):
        """Every stage must have a stage_transition entry in the audit log."""
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        entries = audit_logger.read_all()
        transition_stages = {
            e["data"]["stage"]
            for e in entries
            if e["event_type"] == "stage_transition"
        }
        assert transition_stages == set(Supervisor.STAGES)

    async def test_audit_entries_carry_task_id(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        for entry in audit_logger.read_all():
            assert entry["task_id"] == envelope_auto.task_id

    async def test_security_shield_events_logged(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        event_types = {e["event_type"] for e in audit_logger.read_all()}
        assert "input_shield" in event_types
        assert "action_shield" in event_types


# ---------------------------------------------------------------------------
# Acceptance: checkpoint pauses without auto_approve
# ---------------------------------------------------------------------------

class TestCheckpoint:

    async def test_checkpoint_pauses_envelope(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        assert envelope.status == "paused"
        await sv.approve_checkpoint()
        await run_task

    async def test_checkpoint_resumes_after_approve(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        await sv.approve_checkpoint()
        await run_task
        assert envelope.status == "complete"

    async def test_checkpoint_event_in_envelope_events(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        await sv.approve_checkpoint()
        await run_task
        event_types = {e["event_type"] for e in envelope.events}
        assert "checkpoint_wait" in event_types
        assert "checkpoint_approved" in event_types


# ---------------------------------------------------------------------------
# Acceptance: auto_approve skips checkpoint
# ---------------------------------------------------------------------------

class TestAutoApprove:

    async def test_auto_approve_completes_without_waiting(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        # Should complete without any external approval
        await asyncio.wait_for(sv.run(), timeout=2.0)
        assert envelope_auto.status == "complete"

    async def test_checkpoint_skipped_event_logged(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        event_types = {e["event_type"] for e in envelope_auto.events}
        assert "checkpoint_skipped" in event_types
        assert "checkpoint_wait" not in event_types

    async def test_checkpoint_skip_in_audit_log(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        entries = audit_logger.read_all()
        assert any(e["event_type"] == "checkpoint_skipped" for e in entries)


# ---------------------------------------------------------------------------
# Acceptance: redirect appends direction and resumes
# ---------------------------------------------------------------------------

class TestRedirect:

    async def test_redirect_resumes_from_checkpoint(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        assert envelope.status == "paused"
        await sv.redirect("Focus only on the script, skip tests.")
        await run_task
        assert envelope.status == "complete"

    async def test_redirect_appends_to_context(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        await sv.redirect("New direction here.")
        await run_task
        redirects = [e for e in envelope.context if e.get("type") == "redirect"]
        assert len(redirects) == 1
        assert redirects[0]["redirect"] == "New direction here."

    async def test_redirect_adds_thought_stream_entry(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        await sv.redirect("Go a different way.")
        await run_task
        contents = [t["content"] for t in envelope.thought_stream]
        assert any("Go a different way." in c for c in contents)

    async def test_redirect_does_not_restart_pipeline(self, envelope, audit_logger):
        """Redirect should not cause any stage to run twice."""
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        await sv.redirect("New direction.")
        await run_task
        entries = audit_logger.read_all()
        stage_transitions = [
            e["data"]["stage"]
            for e in entries
            if e["event_type"] == "stage_transition"
        ]
        # Each stage appears exactly once
        assert len(stage_transitions) == len(set(stage_transitions))

    async def test_redirect_logged_in_audit(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        await sv.redirect("pivot.")
        await run_task
        entries = audit_logger.read_all()
        assert any(e["event_type"] == "redirect" for e in entries)


# ---------------------------------------------------------------------------
# Acceptance: security checkpoints are mandatory code paths
# ---------------------------------------------------------------------------

class TestSecurityCheckpoints:

    async def test_input_shield_called_every_run(self, envelope_auto, audit_logger):
        shield = MagicMock(spec=ShieldProtocol)
        shield.evaluate_input = AsyncMock(return_value=True)
        shield.evaluate_action = AsyncMock(return_value=True)
        sv = Supervisor(
            envelope=envelope_auto,
            provider=_make_provider(),
            audit_logger=audit_logger,
            input_shield=shield,
            action_shield=PassthroughShield(),
        )
        await sv.run()
        shield.evaluate_input.assert_called()

    async def test_action_shield_called_at_execute_and_review(self, envelope_auto, audit_logger):
        action_shield = MagicMock(spec=ShieldProtocol)
        action_shield.evaluate_action = AsyncMock(return_value=True)
        provider = _make_provider()
        sv = Supervisor(
            envelope=envelope_auto,
            provider=provider,
            audit_logger=audit_logger,
            action_shield=action_shield,
        )
        await sv.run()
        # Called for both execute and review stages
        assert action_shield.evaluate_action.call_count >= 2

    async def test_input_shield_block_aborts_pipeline(self, envelope, audit_logger):
        blocking_shield = MagicMock(spec=ShieldProtocol)
        blocking_shield.evaluate_input = AsyncMock(return_value=False)
        blocking_shield.evaluate_action = AsyncMock(return_value=True)
        sv = Supervisor(
            envelope=envelope,
            provider=_make_provider(),
            audit_logger=audit_logger,
            input_shield=blocking_shield,
        )
        await sv.run()
        assert envelope.status == "failed"

    async def test_input_shield_block_logs_security_block(self, envelope, audit_logger):
        blocking_shield = MagicMock(spec=ShieldProtocol)
        blocking_shield.evaluate_input = AsyncMock(return_value=False)
        blocking_shield.evaluate_action = AsyncMock(return_value=True)
        sv = Supervisor(
            envelope=envelope,
            provider=_make_provider(),
            audit_logger=audit_logger,
            input_shield=blocking_shield,
        )
        await sv.run()
        event_types = {e["event_type"] for e in envelope.events}
        assert "security_block" in event_types

    async def test_action_shield_block_at_execute_aborts(self, envelope_auto, audit_logger):
        action_shield = MagicMock(spec=ShieldProtocol)
        action_shield.evaluate_action = AsyncMock(return_value=False)
        sv = Supervisor(
            envelope=envelope_auto,
            provider=_make_provider(),
            audit_logger=audit_logger,
            action_shield=action_shield,
        )
        await sv.run()
        assert envelope_auto.status == "failed"

    async def test_passthrough_shield_always_passes(self, envelope_auto, audit_logger):
        """Default PassthroughShield should never block."""
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        assert envelope_auto.status == "complete"

    async def test_security_shield_events_in_audit_log(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        event_types = {e["event_type"] for e in audit_logger.read_all()}
        assert "input_shield" in event_types
        assert "action_shield" in event_types


# ---------------------------------------------------------------------------
# abort()
# ---------------------------------------------------------------------------

class TestAbort:

    async def test_abort_before_run_sets_failed(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        sv.abort()
        assert envelope.status == "failed"

    async def test_abort_during_checkpoint_unblocks_run(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        run_task = asyncio.create_task(sv.run())
        await asyncio.sleep(0.05)
        sv.abort()
        await run_task
        assert envelope.status == "failed"

    async def test_abort_event_logged(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        sv.abort()
        event_types = {e["event_type"] for e in envelope.events}
        assert "pipeline_abort" in event_types


# ---------------------------------------------------------------------------
# pause()
# ---------------------------------------------------------------------------

class TestPause:

    def test_pause_marks_envelope_paused(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        sv.pause()
        assert envelope.status == "paused"

    def test_pause_event_logged(self, envelope, audit_logger):
        sv, _ = _make_supervisor(envelope, audit_logger)
        sv.pause()
        event_types = {e["event_type"] for e in envelope.events}
        assert "pipeline_pause" in event_types


# ---------------------------------------------------------------------------
# VRAM budgeter
# ---------------------------------------------------------------------------

class TestVRAMBudgeter:

    async def test_vram_budgeter_consulted_before_model_calls(self, envelope_auto, audit_logger):
        vram = MagicMock()
        vram.request_load = MagicMock(return_value=True)
        sv = Supervisor(
            envelope=envelope_auto,
            provider=_make_provider(),
            audit_logger=audit_logger,
            vram_budgeter=vram,
            model="smollm:1.7b",
        )
        await sv.run()
        assert vram.request_load.call_count >= 1
        vram.request_load.assert_called_with("smollm:1.7b")

    async def test_run_succeeds_without_vram_budgeter(self, envelope_auto, audit_logger):
        sv, _ = _make_supervisor(envelope_auto, audit_logger)
        await sv.run()
        assert envelope_auto.status == "complete"


# ---------------------------------------------------------------------------
# PassthroughShield
# ---------------------------------------------------------------------------

class TestPassthroughShield:

    async def test_evaluate_input_always_true(self):
        shield = PassthroughShield()
        assert await shield.evaluate_input("goal", "content") is True

    async def test_evaluate_action_always_true(self):
        shield = PassthroughShield()
        assert await shield.evaluate_action("goal", "action") is True
