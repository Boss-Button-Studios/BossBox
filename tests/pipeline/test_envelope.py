"""
tests/pipeline/test_envelope.py

Full test suite for bossbox/pipeline/envelope.py — Step 3.

Coverage targets
----------------
* create_envelope() factory — defaults, overrides, field values
* original_input write-once invariant
* privilege_level range validation (constructor and assignment)
* status validation (constructor and assignment)
* log_event() — appends correct shape, timestamps present
* add_thought() — appends correct shape, source preserved
* to_dict() — JSON-serialisable, datetime fields are strings, _input_locked absent
* Round-trip: to_dict() then json.dumps() raises no TypeError
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from bossbox.pipeline.envelope import (
    VALID_STATUSES,
    TaskEnvelope,
    create_envelope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_envelope(**kwargs) -> TaskEnvelope:
    """Convenience wrapper that fills required args and merges overrides."""
    defaults = dict(original_input="Summarise the attached report.")
    defaults.update(kwargs)
    return create_envelope(**defaults)


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

class TestCreateEnvelope:
    def test_returns_task_envelope(self):
        env = create_envelope("Do something useful.")
        assert isinstance(env, TaskEnvelope)

    def test_task_id_is_uuid(self):
        env = create_envelope("test")
        # Should not raise
        uuid.UUID(env.task_id)

    def test_task_id_override(self):
        fixed = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        env = create_envelope("test", task_id=fixed)
        assert env.task_id == fixed

    def test_created_at_is_utc_datetime(self):
        env = create_envelope("test")
        assert isinstance(env.created_at, datetime)
        assert env.created_at.tzinfo is not None

    def test_original_input_stored(self):
        goal = "Translate the document into French."
        env = create_envelope(goal)
        assert env.original_input == goal

    def test_default_status_is_pending(self):
        env = create_envelope("test")
        assert env.status == "pending"

    def test_default_privilege_level_is_zero(self):
        env = create_envelope("test")
        assert env.privilege_level == 0

    def test_default_human_initiated_is_true(self):
        env = create_envelope("test")
        assert env.human_initiated is True

    def test_human_initiated_false(self):
        env = create_envelope("test", human_initiated=False)
        assert env.human_initiated is False

    def test_default_auto_approve_is_false(self):
        env = create_envelope("test")
        assert env.auto_approve is False

    def test_auto_approve_true(self):
        env = create_envelope("test", auto_approve=True)
        assert env.auto_approve is True

    def test_declared_document_type_none_by_default(self):
        env = create_envelope("test")
        assert env.declared_document_type is None

    def test_declared_document_type_set(self):
        env = create_envelope("test", declared_document_type="invoice")
        assert env.declared_document_type == "invoice"

    def test_lists_are_empty_on_creation(self):
        env = create_envelope("test")
        assert env.context == []
        assert env.provenance_chain == []
        assert env.thought_stream == []

    def test_factory_logs_creation_event(self):
        env = create_envelope("test")
        assert len(env.events) >= 1
        assert env.events[0]["event_type"] == "envelope_created"

    def test_result_is_none_on_creation(self):
        env = create_envelope("test")
        assert env.result is None

    def test_hostile_content_acknowledged_is_false(self):
        env = create_envelope("test")
        assert env.hostile_content_acknowledged is False

    def test_routing_decision_is_empty_string(self):
        env = create_envelope("test")
        assert env.routing_decision == ""


# ---------------------------------------------------------------------------
# Write-once invariant — original_input
# ---------------------------------------------------------------------------

class TestOriginalInputWriteOnce:
    def test_cannot_reassign_original_input(self):
        env = create_envelope("Initial goal.")
        with pytest.raises(AttributeError, match="write-once"):
            env.original_input = "Hijacked goal."

    def test_original_input_unchanged_after_attempted_write(self):
        env = create_envelope("Initial goal.")
        try:
            env.original_input = "Hijacked goal."
        except AttributeError:
            pass
        assert env.original_input == "Initial goal."

    def test_other_fields_remain_mutable(self):
        env = create_envelope("test")
        env.routing_decision = "nano_classified"
        assert env.routing_decision == "nano_classified"


# ---------------------------------------------------------------------------
# privilege_level validation
# ---------------------------------------------------------------------------

class TestPrivilegeLevelValidation:
    @pytest.mark.parametrize("level", [0, 1, 2, 3, 4])
    def test_valid_levels_accepted_at_construction(self, level):
        env = make_envelope()
        env.privilege_level = level
        assert env.privilege_level == level

    @pytest.mark.parametrize("bad", [-1, 5, 10, 100, "2", 2.5, None])
    def test_invalid_levels_rejected_at_construction(self, bad):
        with pytest.raises((ValueError, TypeError)):
            TaskEnvelope(
                task_id="x",
                created_at=datetime.now(tz=timezone.utc),
                original_input="test",
                declared_document_type=None,
                routing_decision="",
                provenance_chain=[],
                human_initiated=True,
                context=[],
                current_stage="pending",
                privilege_level=bad,
                hostile_content_acknowledged=False,
                thought_stream=[],
                auto_approve=False,
                result=None,
                status="pending",
            )

    @pytest.mark.parametrize("bad", [-1, 5, 999])
    def test_invalid_levels_rejected_on_assignment(self, bad):
        env = make_envelope()
        with pytest.raises(ValueError):
            env.privilege_level = bad

    def test_privilege_level_escalation_valid(self):
        env = make_envelope()
        env.privilege_level = 2
        assert env.privilege_level == 2

    def test_privilege_level_not_float(self):
        env = make_envelope()
        with pytest.raises(ValueError):
            env.privilege_level = 1.0  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# status validation
# ---------------------------------------------------------------------------

class TestStatusValidation:
    @pytest.mark.parametrize("s", VALID_STATUSES)
    def test_valid_statuses_accepted(self, s):
        env = make_envelope()
        env.status = s
        assert env.status == s

    @pytest.mark.parametrize("bad", ["done", "error", "RUNNING", "", "complete "])
    def test_invalid_statuses_rejected_on_assignment(self, bad):
        env = make_envelope()
        with pytest.raises(ValueError):
            env.status = bad

    def test_invalid_status_rejected_at_construction(self):
        with pytest.raises(ValueError):
            TaskEnvelope(
                task_id="x",
                created_at=datetime.now(tz=timezone.utc),
                original_input="test",
                declared_document_type=None,
                routing_decision="",
                provenance_chain=[],
                human_initiated=True,
                context=[],
                current_stage="pending",
                privilege_level=0,
                hostile_content_acknowledged=False,
                thought_stream=[],
                auto_approve=False,
                result=None,
                status="done",         # invalid
            )


# ---------------------------------------------------------------------------
# log_event()
# ---------------------------------------------------------------------------

class TestLogEvent:
    def test_appends_to_events(self):
        env = make_envelope()
        before = len(env.events)
        env.log_event("stage_transition", "Moved to decompose stage.")
        assert len(env.events) == before + 1

    def test_event_has_required_keys(self):
        env = make_envelope()
        env.log_event("anomaly_flag", "Scope violation detected.")
        last = env.events[-1]
        assert "ts" in last
        assert "task_id" in last
        assert "event_type" in last
        assert "detail" in last

    def test_event_type_preserved(self):
        env = make_envelope()
        env.log_event("privilege_request", "Level 3 requested by decomposer.")
        assert env.events[-1]["event_type"] == "privilege_request"

    def test_detail_preserved(self):
        env = make_envelope()
        env.log_event("checkpoint", "Human checkpoint reached.")
        assert env.events[-1]["detail"] == "Human checkpoint reached."

    def test_task_id_matches_envelope(self):
        env = make_envelope()
        env.log_event("test_event", "detail")
        assert env.events[-1]["task_id"] == env.task_id

    def test_ts_is_string(self):
        env = make_envelope()
        env.log_event("test_event", "detail")
        assert isinstance(env.events[-1]["ts"], str)

    def test_extra_included_when_provided(self):
        env = make_envelope()
        env.log_event("test", "detail", extra={"model": "nano", "score": 0.9})
        assert env.events[-1]["extra"]["model"] == "nano"

    def test_extra_absent_when_not_provided(self):
        env = make_envelope()
        env.log_event("test", "detail")
        assert "extra" not in env.events[-1]

    def test_multiple_events_ordered(self):
        env = make_envelope()
        env.log_event("a", "first")
        env.log_event("b", "second")
        env.log_event("c", "third")
        types = [e["event_type"] for e in env.events if e["event_type"] in ("a", "b", "c")]
        assert types == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# add_thought()
# ---------------------------------------------------------------------------

class TestAddThought:
    def test_appends_to_thought_stream(self):
        env = make_envelope()
        env.add_thought("progress", "Task received.")
        assert len(env.thought_stream) == 1

    def test_thought_has_required_keys(self):
        env = make_envelope()
        env.add_thought("reasoning", "Breaking the task into subtasks.")
        entry = env.thought_stream[-1]
        assert "ts" in entry
        assert "source" in entry
        assert "content" in entry

    def test_source_preserved(self):
        env = make_envelope()
        env.add_thought("nano", "Routing to micro tier.")
        assert env.thought_stream[-1]["source"] == "nano"

    def test_content_preserved(self):
        env = make_envelope()
        content = "I need to decompose this into three subtasks."
        env.add_thought("reasoning", content)
        assert env.thought_stream[-1]["content"] == content

    def test_ts_is_string(self):
        env = make_envelope()
        env.add_thought("progress", "done")
        assert isinstance(env.thought_stream[-1]["ts"], str)

    def test_multiple_thoughts_ordered(self):
        env = make_envelope()
        env.add_thought("progress", "first")
        env.add_thought("reasoning", "second")
        env.add_thought("progress", "third")
        contents = [t["content"] for t in env.thought_stream]
        assert contents == ["first", "second", "third"]

    def test_thought_stream_independent_of_events(self):
        env = make_envelope()
        env.add_thought("progress", "thinking")
        env.log_event("stage_transition", "advanced")
        assert len(env.thought_stream) == 1
        # events has factory entry + 1 more
        assert sum(1 for e in env.events if e["event_type"] == "stage_transition") == 1


# ---------------------------------------------------------------------------
# to_dict()
# ---------------------------------------------------------------------------

class TestToDict:
    def test_returns_dict(self):
        env = make_envelope()
        assert isinstance(env.to_dict(), dict)

    def test_all_public_fields_present(self):
        env = make_envelope()
        d = env.to_dict()
        required_keys = {
            "task_id", "created_at", "original_input", "declared_document_type",
            "routing_decision", "provenance_chain", "human_initiated", "context",
            "current_stage", "privilege_level", "hostile_content_acknowledged",
            "thought_stream", "auto_approve", "result", "status", "events",
        }
        assert required_keys.issubset(d.keys())

    def test_private_input_locked_not_in_dict(self):
        env = make_envelope()
        d = env.to_dict()
        assert "_input_locked" not in d

    def test_created_at_is_string_not_datetime(self):
        env = make_envelope()
        d = env.to_dict()
        assert isinstance(d["created_at"], str)

    def test_created_at_is_iso_format(self):
        env = make_envelope()
        d = env.to_dict()
        # Should parse back to a datetime without error
        parsed = datetime.fromisoformat(d["created_at"])
        assert parsed is not None

    def test_json_serialisable(self):
        env = make_envelope()
        env.add_thought("progress", "Testing serialisation.")
        env.log_event("stage_transition", "Running.")
        env.status = "running"
        env.privilege_level = 1
        d = env.to_dict()
        # Must not raise
        serialised = json.dumps(d)
        assert isinstance(serialised, str)

    def test_round_trip_preserves_original_input(self):
        goal = "Analyse the quarterly report and extract key figures."
        env = create_envelope(goal)
        d = env.to_dict()
        assert d["original_input"] == goal

    def test_round_trip_preserves_task_id(self):
        tid = "deadbeef-dead-beef-dead-beefdeadbeef"
        env = create_envelope("test", task_id=tid)
        assert env.to_dict()["task_id"] == tid

    def test_result_none_by_default(self):
        env = make_envelope()
        assert env.to_dict()["result"] is None

    def test_result_string_serialises(self):
        env = make_envelope()
        env.result = "Here is the summary."
        d = env.to_dict()
        assert d["result"] == "Here is the summary."

    def test_thought_stream_in_dict(self):
        env = make_envelope()
        env.add_thought("progress", "hello")
        d = env.to_dict()
        assert any(t["content"] == "hello" for t in d["thought_stream"])

    def test_events_in_dict(self):
        env = make_envelope()
        env.log_event("test_event", "checking")
        d = env.to_dict()
        assert any(e["event_type"] == "test_event" for e in d["events"])

    def test_naive_datetime_gets_utc_in_dict(self):
        """Naive datetimes should be treated as UTC in serialisation."""
        naive = datetime(2026, 1, 15, 12, 0, 0)   # no tzinfo
        env = TaskEnvelope(
            task_id="test-id",
            created_at=naive,
            original_input="test",
            declared_document_type=None,
            routing_decision="",
            provenance_chain=[],
            human_initiated=True,
            context=[],
            current_stage="pending",
            privilege_level=0,
            hostile_content_acknowledged=False,
            thought_stream=[],
            auto_approve=False,
            result=None,
            status="pending",
        )
        d = env.to_dict()
        assert isinstance(d["created_at"], str)
        assert "2026-01-15" in d["created_at"]


# ---------------------------------------------------------------------------
# Edge cases and integration
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_two_envelopes_have_distinct_task_ids(self):
        a = create_envelope("task a")
        b = create_envelope("task b")
        assert a.task_id != b.task_id

    def test_events_list_is_not_shared_between_instances(self):
        a = create_envelope("task a")
        b = create_envelope("task b")
        a.log_event("only_in_a", "detail")
        assert not any(e["event_type"] == "only_in_a" for e in b.events)

    def test_thought_stream_not_shared_between_instances(self):
        a = create_envelope("task a")
        b = create_envelope("task b")
        a.add_thought("progress", "only a")
        assert len(b.thought_stream) == 0

    def test_complete_lifecycle_state_machine(self):
        env = create_envelope("Full lifecycle test.")
        assert env.status == "pending"

        env.status = "running"
        env.current_stage = "decompose"
        env.add_thought("progress", "Decomposing task.")
        env.log_event("stage_transition", "pending → running")

        env.status = "paused"
        env.log_event("checkpoint", "Awaiting user approval.")
        env.add_thought("progress", "Waiting for your go-ahead.")

        env.status = "running"
        env.privilege_level = 1
        env.current_stage = "execute"

        env.result = "Done."
        env.status = "complete"
        env.log_event("stage_transition", "running → complete")

        d = env.to_dict()
        assert d["status"] == "complete"
        assert d["result"] == "Done."
        assert d["privilege_level"] == 1
        # Must still be JSON-serialisable after full lifecycle
        json.dumps(d)
