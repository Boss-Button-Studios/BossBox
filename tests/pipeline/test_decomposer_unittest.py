"""
Task Decomposer Tests (unittest) — BossBox Atomic Step 14
==========================================================
Stdlib unittest mirror of test_decomposer.py.
Runnable with: python -m unittest tests.pipeline.test_decomposer_unittest -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

import yaml

from bossbox.pipeline.decomposer import (
    DecompositionResult,
    Subtask,
    _extract_yaml_block,
    _fail_safe,
    _parse_markdown_tasks,
    _parse_response,
    decompose,
)
from bossbox.pipeline.envelope import create_envelope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_provider(response: str) -> MagicMock:
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=response)
    return provider


def _yaml_response(
    reasoning: str = "The goal has two clear parts.",
    core_tasks=None,
    suggested_tasks=None,
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


def _make_envelope(goal: str = "Build a thing"):
    return create_envelope(goal)


# ---------------------------------------------------------------------------
# _extract_yaml_block
# ---------------------------------------------------------------------------

class TestExtractYamlBlockUnittest(unittest.TestCase):

    def test_raw_yaml(self):
        result = _extract_yaml_block("decomposition:\n  reasoning: hi\n")
        self.assertIn("decomposition", result)

    def test_fenced_yaml(self):
        result = _extract_yaml_block("```yaml\ndecomposition:\n  reasoning: hi\n```")
        self.assertIn("decomposition", result)

    def test_raises_on_unknown_response(self):
        with self.assertRaises(ValueError):
            _extract_yaml_block("I cannot help.")


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------

class TestParseResponseUnittest(unittest.TestCase):

    def test_returns_decomposition_result(self):
        self.assertIsInstance(_parse_response(_yaml_response()), DecompositionResult)

    def test_core_tasks_parsed(self):
        result = _parse_response(_yaml_response())
        self.assertEqual(len(result.core_tasks), 2)
        self.assertEqual(result.core_tasks[0].title, "Task A")

    def test_suggested_tasks_parsed(self):
        result = _parse_response(_yaml_response())
        self.assertEqual(len(result.suggested_tasks), 1)

    def test_reasoning_parsed(self):
        result = _parse_response(_yaml_response(reasoning="My reasoning."))
        self.assertEqual(result.reasoning, "My reasoning.")

    def test_empty_suggested_tasks(self):
        result = _parse_response(_yaml_response(suggested_tasks=[]))
        self.assertEqual(result.suggested_tasks, [])

    def test_raises_on_no_yaml_block(self):
        with self.assertRaises(ValueError):
            _parse_response("I cannot help with this.")

    def test_malformed_items_skipped(self):
        data = {
            "decomposition": {
                "reasoning": "r",
                "core_tasks": [{"title": "Good", "description": "ok"}, "bad", None],
                "suggested_tasks": [],
            }
        }
        result = _parse_response(yaml.dump(data))
        self.assertEqual(len(result.core_tasks), 1)


# ---------------------------------------------------------------------------
# _fail_safe
# ---------------------------------------------------------------------------

class TestFailSafeUnittest(unittest.TestCase):

    def test_returns_decomposition_result(self):
        self.assertIsInstance(_fail_safe("g", "r"), DecompositionResult)

    def test_single_core_task(self):
        self.assertEqual(len(_fail_safe("g", "r").core_tasks), 1)

    def test_goal_in_description(self):
        self.assertIn("my goal", _fail_safe("my goal", "r").core_tasks[0].description)

    def test_suggested_empty(self):
        self.assertEqual(_fail_safe("g", "r").suggested_tasks, [])

    def test_reasoning_empty_on_fail_safe(self):
        self.assertEqual(_fail_safe("g", "net err").reasoning, "")

    def test_title_truncated(self):
        self.assertLessEqual(len(_fail_safe("x" * 200, "r").core_tasks[0].title), 120)


# ---------------------------------------------------------------------------
# decompose() — acceptance criteria
# ---------------------------------------------------------------------------

class TestDecomposeAcceptanceUnittest(unittest.TestCase):

    def test_multi_part_goal_returns_at_least_two_core_tasks(self):
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("Build and test")
        result = _run(decompose("Build and test", provider, envelope))
        self.assertGreaterEqual(len(result.core_tasks), 2)

    def test_suggested_tasks_separate(self):
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("Do the thing")
        result = _run(decompose("Do the thing", provider, envelope))
        self.assertIsInstance(result.core_tasks, list)
        self.assertIsInstance(result.suggested_tasks, list)
        self.assertIsNot(result.core_tasks, result.suggested_tasks)

    def test_reasoning_in_thought_stream(self):
        provider = _make_provider(_yaml_response(reasoning="Because reasons."))
        envelope = _make_envelope("Do stuff")
        _run(decompose("Do stuff", provider, envelope))
        sources = [t["source"] for t in envelope.thought_stream]
        contents = [t["content"] for t in envelope.thought_stream]
        self.assertIn("reasoning", sources)
        self.assertTrue(any("Because reasons." in c for c in contents))

    def test_output_is_dataclass(self):
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("goal")
        result = _run(decompose("goal", provider, envelope))
        self.assertIsInstance(result, DecompositionResult)
        self.assertTrue(all(isinstance(t, Subtask) for t in result.core_tasks))

    def test_provider_failure_returns_fail_safe(self):
        from bossbox.providers.base import ProviderUnavailableError
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=ProviderUnavailableError("down"))
        envelope = _make_envelope("goal")
        result = _run(decompose("goal", provider, envelope))
        self.assertIsInstance(result, DecompositionResult)
        self.assertGreaterEqual(len(result.core_tasks), 1)

    def test_fail_safe_adds_progress_thought_to_stream(self):
        from bossbox.providers.base import ProviderUnavailableError
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=ProviderUnavailableError("down"))
        envelope = _make_envelope("goal")
        _run(decompose("goal", provider, envelope))
        self.assertTrue(any(t["source"] == "progress" for t in envelope.thought_stream))

    def test_unparseable_response_returns_fail_safe(self):
        provider = _make_provider("No idea what you want.")
        envelope = _make_envelope("goal")
        result = _run(decompose("goal", provider, envelope))
        self.assertIsInstance(result, DecompositionResult)

    def test_model_override_forwarded(self):
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("goal")
        _run(decompose("goal", provider, envelope, model="qwen2.5:1.5b"))
        kwargs = provider.complete.call_args[1]
        self.assertEqual(kwargs.get("model"), "qwen2.5:1.5b")

    def test_core_tasks_ordered(self):
        provider = _make_provider(_yaml_response(core_tasks=[
            {"title": "First", "description": "1"},
            {"title": "Second", "description": "2"},
            {"title": "Third", "description": "3"},
        ]))
        envelope = _make_envelope("goal")
        result = _run(decompose("goal", provider, envelope))
        self.assertEqual([t.title for t in result.core_tasks], ["First", "Second", "Third"])

    def test_thought_stream_entry_has_timestamp(self):
        provider = _make_provider(_yaml_response())
        envelope = _make_envelope("goal")
        _run(decompose("goal", provider, envelope))
        entries = [t for t in envelope.thought_stream if t["source"] == "reasoning"]
        self.assertTrue(entries)
        self.assertIn("ts", entries[0])


# ---------------------------------------------------------------------------
# Dataclass contracts
# ---------------------------------------------------------------------------

class TestDataclassesUnittest(unittest.TestCase):

    def test_subtask_fields(self):
        st = Subtask(title="Do X", description="Because Y")
        self.assertEqual(st.title, "Do X")
        self.assertEqual(st.description, "Because Y")

    def test_decomposition_result_defaults(self):
        dr = DecompositionResult(core_tasks=[Subtask("T", "D")])
        self.assertEqual(dr.suggested_tasks, [])
        self.assertEqual(dr.reasoning, "")

    def test_decomposition_result_all_fields(self):
        dr = DecompositionResult(
            core_tasks=[Subtask("A", "a"), Subtask("B", "b")],
            suggested_tasks=[Subtask("C", "c")],
            reasoning="why",
        )
        self.assertEqual(len(dr.core_tasks), 2)
        self.assertEqual(len(dr.suggested_tasks), 1)
        self.assertEqual(dr.reasoning, "why")



# ---------------------------------------------------------------------------
# Markdown fallback parser
# ---------------------------------------------------------------------------

class TestMarkdownFallbackUnittest(unittest.TestCase):

    def test_bold_task_headings_parsed(self):
        response = "**Task 1: Write the script**\nSome prose.\n**Task 2: Test the script**\nMore prose."
        result = _parse_markdown_tasks(response)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.core_tasks), 2)
        self.assertEqual(result.core_tasks[0].title, "Write the script")
        self.assertEqual(result.core_tasks[1].title, "Test the script")

    def test_bold_step_headings_parsed(self):
        response = "**Step 1: Gather data**\n**Step 2: Analyse results**\n**Step 3: Write report**"
        result = _parse_markdown_tasks(response)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.core_tasks), 3)

    def test_numbered_list_parsed(self):
        response = "Here are the sub-goals:\n1. Research the topic\n2. Draft the outline\n3. Write the content"
        result = _parse_markdown_tasks(response)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.core_tasks), 3)
        self.assertEqual(result.core_tasks[0].title, "Research the topic")

    def test_single_item_returns_none(self):
        response = "**Task 1: Do the thing**\nJust one task."
        self.assertIsNone(_parse_markdown_tasks(response))

    def test_prose_only_returns_none(self):
        self.assertIsNone(_parse_markdown_tasks("Just do the thing. It is simple."))

    def test_reasoning_is_placeholder(self):
        response = "**Task 1: Step one**\n**Task 2: Step two**"
        result = _parse_markdown_tasks(response)
        self.assertIsNotNone(result)
        self.assertIn("markdown", result.reasoning.lower())

    def test_max_ten_tasks_extracted(self):
        lines = "\n".join(f"**Task {i}: Task title {i}**" for i in range(1, 15))
        result = _parse_markdown_tasks(lines)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result.core_tasks), 10)

    def test_decompose_falls_back_to_markdown(self):
        md_response = "**Task 1: Write it**\nDo the writing.\n**Task 2: Test it**\nRun tests."
        provider = _make_provider(md_response)
        envelope = _make_envelope("Write and test a script")
        result = _run(decompose("Write and test a script", provider, envelope))
        self.assertEqual(len(result.core_tasks), 2)
        self.assertEqual(result.core_tasks[0].title, "Write it")

    def test_decompose_markdown_appends_thought(self):
        md_response = "**Task 1: Alpha**\n**Task 2: Beta**"
        provider = _make_provider(md_response)
        envelope = _make_envelope("goal")
        _run(decompose("goal", provider, envelope))
        contents = [t["content"] for t in envelope.thought_stream]
        self.assertTrue(any("2 tasks" in c for c in contents))


if __name__ == "__main__":
    unittest.main()
