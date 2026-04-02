"""
Task Decomposer Tests — BossBox Atomic Step 14
===============================================
All provider calls are mocked — no Ollama instance required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from bossbox.pipeline.decomposer import (
    DecompositionResult,
    Subtask,
    _extract_yaml_block,
    _fail_safe,
    _parse_response,
    decompose,
)
from bossbox.pipeline.envelope import create_envelope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(response: str) -> MagicMock:
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=response)
    return provider


def _yaml_response(
    reasoning: str = "The goal has two clear parts.",
    core_tasks: list[dict] | None = None,
    suggested_tasks: list[dict] | None = None,
) -> str:
    data = {
        "decomposition": {
            "reasoning": reasoning,
            "core_tasks": core_tasks or [
                {"title": "Task A", "description": "Do the first thing."},
                {"title": "Task B", "description": "Do the second thing."},
            ],
            "suggested_tasks": [
                {"title": "Optional cleanup", "description": "Nice to have."},
            ] if suggested_tasks is None else suggested_tasks,
        }
    }
    return yaml.dump(data, default_flow_style=False)


def _make_envelope(goal: str = "Build a thing") -> object:
    return create_envelope(goal)


# ---------------------------------------------------------------------------
# _extract_yaml_block
# ---------------------------------------------------------------------------

class TestExtractYamlBlock:
    def test_raw_yaml(self):
        raw = "decomposition:\n  reasoning: hi\n"
        assert "decomposition" in _extract_yaml_block(raw)

    def test_fenced_yaml(self):
        raw = "```yaml\ndecomposition:\n  reasoning: hi\n```"
        assert "decomposition" in _extract_yaml_block(raw)

    def test_fenced_no_language_tag(self):
        raw = "```\ndecomposition:\n  reasoning: hi\n```"
        assert "decomposition" in _extract_yaml_block(raw)

    def test_raises_on_unrecognised_response(self):
        with pytest.raises(ValueError):
            _extract_yaml_block("Sorry, I cannot help.")


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_returns_decomposition_result(self):
        result = _parse_response(_yaml_response())
        assert isinstance(result, DecompositionResult)

    def test_core_tasks_parsed(self):
        result = _parse_response(_yaml_response())
        assert len(result.core_tasks) == 2
        assert result.core_tasks[0].title == "Task A"

    def test_suggested_tasks_parsed(self):
        result = _parse_response(_yaml_response())
        assert len(result.suggested_tasks) == 1
        assert result.suggested_tasks[0].title == "Optional cleanup"

    def test_reasoning_parsed(self):
        result = _parse_response(_yaml_response(reasoning="My reasoning."))
        assert result.reasoning == "My reasoning."

    def test_empty_suggested_tasks_allowed(self):
        result = _parse_response(_yaml_response(suggested_tasks=[]))
        assert result.suggested_tasks == []

    def test_subtask_has_title_and_description(self):
        result = _parse_response(_yaml_response())
        st = result.core_tasks[0]
        assert st.title == "Task A"
        assert st.description == "Do the first thing."

    def test_raises_on_invalid_yaml(self):
        with pytest.raises(ValueError):
            _parse_response("decomposition: [unclosed")

    def test_raises_on_no_yaml_block(self):
        with pytest.raises(ValueError):
            _parse_response("I cannot help with this.")

    def test_malformed_task_items_skipped(self):
        data = {
            "decomposition": {
                "reasoning": "r",
                "core_tasks": [
                    {"title": "Good task", "description": "ok"},
                    "not a dict",
                    None,
                ],
                "suggested_tasks": [],
            }
        }
        result = _parse_response(yaml.dump(data))
        assert len(result.core_tasks) == 1
        assert result.core_tasks[0].title == "Good task"


# ---------------------------------------------------------------------------
# _fail_safe
# ---------------------------------------------------------------------------

class TestFailSafe:
    def test_returns_decomposition_result(self):
        result = _fail_safe("my goal", "reason")
        assert isinstance(result, DecompositionResult)

    def test_single_core_task(self):
        result = _fail_safe("my goal", "reason")
        assert len(result.core_tasks) == 1

    def test_core_task_contains_goal(self):
        result = _fail_safe("my goal", "reason")
        assert "my goal" in result.core_tasks[0].description

    def test_suggested_tasks_empty(self):
        result = _fail_safe("my goal", "reason")
        assert result.suggested_tasks == []

    def test_reasoning_contains_failure_note(self):
        result = _fail_safe("g", "network error")
        assert "network error" in result.reasoning

    def test_long_goal_title_truncated(self):
        long_goal = "x" * 200
        result = _fail_safe(long_goal, "r")
        assert len(result.core_tasks[0].title) <= 120


# ---------------------------------------------------------------------------
# decompose() — acceptance criteria
# ---------------------------------------------------------------------------

class TestDecomposeAcceptanceCriteria:

    async def test_multi_part_goal_returns_at_least_two_core_tasks(self):
        """Spec: Multi-part goal returns at least two core tasks."""
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("Build and test the widget")
        result = await decompose("Build and test the widget", provider, envelope)
        assert len(result.core_tasks) >= 2

    async def test_suggested_tasks_are_separate(self):
        """Spec: Suggested tasks separate from core tasks."""
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("Do the thing")
        result = await decompose("Do the thing", provider, envelope)
        assert isinstance(result.core_tasks, list)
        assert isinstance(result.suggested_tasks, list)
        # The two lists must be independent objects
        assert result.core_tasks is not result.suggested_tasks

    async def test_reasoning_appended_to_thought_stream(self):
        """Spec: Reasoning in thought stream."""
        provider = _make_provider(_yaml_response(reasoning="Because reasons."))
        envelope = _make_envelope("Do stuff")
        await decompose("Do stuff", provider, envelope)
        thought_sources = [t["source"] for t in envelope.thought_stream]
        thought_contents = [t["content"] for t in envelope.thought_stream]
        assert "reasoning" in thought_sources
        assert any("Because reasons." in c for c in thought_contents)

    async def test_output_is_decomposition_result_dataclass(self):
        """Spec: Output is dataclass, not raw text."""
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("goal")
        result = await decompose("goal", provider, envelope)
        assert isinstance(result, DecompositionResult)
        assert isinstance(result.core_tasks, list)
        assert all(isinstance(t, Subtask) for t in result.core_tasks)

    async def test_provider_failure_returns_fail_safe(self):
        from bossbox.providers.base import ProviderUnavailableError
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=ProviderUnavailableError("down"))
        envelope = _make_envelope("goal")
        result = await decompose("goal", provider, envelope)
        assert isinstance(result, DecompositionResult)
        assert len(result.core_tasks) >= 1

    async def test_fail_safe_still_adds_reasoning_to_thought_stream(self):
        from bossbox.providers.base import ProviderUnavailableError
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=ProviderUnavailableError("down"))
        envelope = _make_envelope("goal")
        await decompose("goal", provider, envelope)
        assert any(t["source"] == "reasoning" for t in envelope.thought_stream)

    async def test_unparseable_response_returns_fail_safe(self):
        provider = _make_provider("I have no idea what you want.")
        envelope = _make_envelope("goal")
        result = await decompose("goal", provider, envelope)
        assert isinstance(result, DecompositionResult)
        assert len(result.core_tasks) >= 1

    async def test_model_override_forwarded_to_provider(self):
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("goal")
        await decompose("goal", provider, envelope, model="qwen2.5-coder:1.5b")
        kwargs = provider.complete.call_args[1]
        assert kwargs.get("model") == "qwen2.5-coder:1.5b"

    async def test_subtasks_have_title_and_description(self):
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("goal")
        result = await decompose("goal", provider, envelope)
        for task in result.core_tasks:
            assert isinstance(task.title, str) and task.title
            assert isinstance(task.description, str)

    async def test_core_tasks_ordered(self):
        """Core tasks should come back in the order the model returned them."""
        provider = _make_provider(_yaml_response(
            core_tasks=[
                {"title": "First", "description": "Step 1"},
                {"title": "Second", "description": "Step 2"},
                {"title": "Third", "description": "Step 3"},
            ]
        ))
        envelope = _make_envelope("goal")
        result = await decompose("goal", provider, envelope)
        titles = [t.title for t in result.core_tasks]
        assert titles == ["First", "Second", "Third"]

    async def test_thought_stream_entry_has_timestamp(self):
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("goal")
        await decompose("goal", provider, envelope)
        reasoning_entries = [t for t in envelope.thought_stream if t["source"] == "reasoning"]
        assert reasoning_entries
        assert "ts" in reasoning_entries[0]

    async def test_no_reasoning_in_response_still_adds_thought(self):
        data = {
            "decomposition": {
                "core_tasks": [
                    {"title": "T1", "description": "d1"},
                    {"title": "T2", "description": "d2"},
                ],
                "suggested_tasks": [],
            }
        }
        provider = _make_provider(yaml.dump(data))
        envelope = _make_envelope("goal")
        result = await decompose("goal", provider, envelope)
        assert any(t["source"] == "reasoning" for t in envelope.thought_stream)


# ---------------------------------------------------------------------------
# Dataclass contracts
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_subtask_fields(self):
        st = Subtask(title="Do X", description="Because Y")
        assert st.title == "Do X"
        assert st.description == "Because Y"

    def test_decomposition_result_defaults(self):
        dr = DecompositionResult(core_tasks=[Subtask("T", "D")])
        assert dr.suggested_tasks == []
        assert dr.reasoning == ""

    def test_decomposition_result_with_all_fields(self):
        dr = DecompositionResult(
            core_tasks=[Subtask("A", "a"), Subtask("B", "b")],
            suggested_tasks=[Subtask("C", "c")],
            reasoning="why",
        )
        assert len(dr.core_tasks) == 2
        assert len(dr.suggested_tasks) == 1
        assert dr.reasoning == "why"
