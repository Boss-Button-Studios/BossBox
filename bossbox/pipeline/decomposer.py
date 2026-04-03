"""
Task Decomposer — BossBox Atomic Step 14
=========================================
Micro-tier module that breaks a user goal into an ordered list of subtasks.

Separates subtasks into two categories:
  core_tasks      — must be completed to satisfy the goal
  suggested_tasks — optional enhancements, out-of-scope but potentially useful

The decomposer appends its reasoning to the TaskEnvelope thought stream so the
user can review the model's planning logic at the human checkpoint (Step 15).

Fail-safe principle: any provider or parse failure returns a single-task
DecompositionResult wrapping the original goal, so the pipeline can always
proceed — the user will see the reasoning failure in the thought stream and
decide whether to abort or continue at the checkpoint.

Public API
----------
decompose(goal, provider, envelope, model=None) -> DecompositionResult
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from bossbox.pipeline.envelope import TaskEnvelope
from bossbox.providers.base import ModelProvider

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a task planner. Output ONLY valid YAML — no prose, no code, no explanation outside the YAML.

Example output for goal "Write a hello-world script and test it":

decomposition:
  reasoning: "Two clear steps: write the script, then verify it."
  core_tasks:
    - title: "Write hello.py"
      description: "Create hello.py that prints Hello World."
    - title: "Test hello.py"
      description: "Run the script and verify the output."
  suggested_tasks:
    - title: "Add a docstring"
      description: "Document the script with a module-level docstring."

Use that exact structure. suggested_tasks may be an empty list [].
Do NOT answer the question — only output the YAML decomposition.
"""

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Subtask:
    """A single decomposed subtask."""
    title: str
    description: str


@dataclass
class DecompositionResult:
    """
    Typed result of task decomposition.

    core_tasks      — ordered list of tasks required to complete the goal.
    suggested_tasks — optional tasks separated from core; may be empty.
    reasoning       — the model's explanation of its decomposition choices.
    """
    core_tasks: list[Subtask]
    suggested_tasks: list[Subtask] = field(default_factory=list)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_yaml_block(response: str) -> str:
    """Extract YAML from the model response (fenced or raw)."""
    fenced = re.search(r"```(?:yaml)?\s*\n(.*?)```", response, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    idx = response.find("decomposition:")
    if idx != -1:
        return response[idx:].strip()

    raise ValueError(
        f"No decomposition YAML block found in response. "
        f"Preview: {response[:200]!r}"
    )


def _parse_subtask_list(raw: Any) -> list[Subtask]:
    """Convert a raw YAML list to Subtask objects; silently skip malformed items."""
    if not isinstance(raw, list):
        return []
    result: list[Subtask] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        description = str(item.get("description", "")).strip()
        if title:
            result.append(Subtask(title=title, description=description))
    return result


def _parse_response(response: str) -> DecompositionResult:
    """
    Parse the model's YAML response into a DecompositionResult.

    Raises ValueError on any unrecoverable parse problem so the caller can
    apply the fail-safe.
    """
    yaml_str = _extract_yaml_block(response)

    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping, got {type(data).__name__}")

    inner = data.get("decomposition", data)
    if not isinstance(inner, dict):
        raise ValueError("decomposition value is not a mapping")

    reasoning = str(inner.get("reasoning", "")).strip()
    core_tasks = _parse_subtask_list(inner.get("core_tasks", []))
    suggested_tasks = _parse_subtask_list(inner.get("suggested_tasks", []))

    return DecompositionResult(
        core_tasks=core_tasks,
        suggested_tasks=suggested_tasks,
        reasoning=reasoning,
    )


def _parse_markdown_tasks(response: str) -> DecompositionResult | None:
    """
    Secondary parser for markdown-formatted task lists.

    Handles the natural output of small models that follow the decomposition
    intent but ignore the YAML format constraint.  Recognises two patterns:

    1. ``**Task N: Title**`` or ``**Step N: Title**`` headings.
    2. Numbered list items (``1. Title`` or ``1. **Title**``) after a
       "sub-goals", "steps", or "tasks" heading.

    Returns None when fewer than two tasks are found, so the caller can
    fall through to the fail-safe rather than treating a prose reply as a
    valid decomposition.
    """
    # Pattern 1: **Task N: ...** or **Step N: ...**
    task_heading = re.compile(
        r"\*\*(?:Task|Step)\s+\d+\s*:?\s*([^*\n]{3,80})\*\*",
        re.IGNORECASE,
    )
    titles = [m.strip().rstrip("*").strip() for m in task_heading.findall(response)]

    if len(titles) < 2:
        # Pattern 2: numbered list items (possibly after a "sub-goals:" line)
        numbered = re.compile(r"^\d+\.\s+\*?\*?([^*\n]{5,120})\*?\*?", re.MULTILINE)
        titles = [m.strip() for m in numbered.findall(response) if m.strip()]

    if len(titles) < 2:
        return None

    tasks = [Subtask(title=t[:120], description="") for t in titles[:10]]
    return DecompositionResult(
        core_tasks=tasks,
        suggested_tasks=[],
        reasoning="[Decomposition extracted from markdown-format response]",
    )


def _fail_safe(goal: str, reason: str) -> DecompositionResult:
    """Return a single-task result when decomposition cannot complete."""
    log.warning("Decomposition fall-back (treating goal as single task): %s", reason)
    return DecompositionResult(
        core_tasks=[Subtask(title=goal[:120], description=goal)],
        suggested_tasks=[],
        reasoning="",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def decompose(
    goal: str,
    provider: ModelProvider,
    envelope: TaskEnvelope,
    model: str | None = None,
    **provider_kwargs,
) -> DecompositionResult:
    """
    Break *goal* into an ordered subtask list using the Micro model.

    Appends reasoning to *envelope*'s thought stream regardless of whether
    decomposition succeeds or falls back to the fail-safe.

    Parameters
    ----------
    goal:
        The user's goal statement (typically ``envelope.original_input``).
    provider:
        A configured Micro-tier ModelProvider.
    envelope:
        The active TaskEnvelope.  Reasoning is appended here via
        ``add_thought("reasoning", ...)``.
    model:
        Optional model identifier override forwarded to provider.complete().

    Returns
    -------
    DecompositionResult
        Always returns — never raises.  Failures produce a single-task
        fail-safe result.
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Break this goal into tasks: {goal}"},
    ]

    kwargs: dict = {}
    if model is not None:
        kwargs["model"] = model
    kwargs.update(provider_kwargs)

    model_label = model or "model"
    envelope.add_thought("progress", f"Asking {model_label} to decompose goal…")

    try:
        response = await provider.complete(messages, **kwargs)
    except Exception as exc:
        result = _fail_safe(goal, f"Provider call failed: {exc}")
        envelope.add_thought("progress", "Decomposition unavailable — proceeding as single task.")
        return result

    try:
        result = _parse_response(response)
    except ValueError as exc:
        # YAML parse failed — try markdown fallback before giving up.
        md_result = _parse_markdown_tasks(response)
        if md_result is not None:
            log.info("Decomposition: YAML failed, markdown fallback succeeded (%d tasks).",
                     len(md_result.core_tasks))
            envelope.add_thought(
                "progress",
                f"Decomposed into {len(md_result.core_tasks)} tasks (markdown format).",
            )
            return md_result
        result = _fail_safe(goal, str(exc))
        envelope.add_thought("progress", "Decomposition unavailable — proceeding as single task.")
        return result

    # Append reasoning to thought stream — visible at human checkpoint
    if result.reasoning:
        envelope.add_thought("reasoning", result.reasoning)
    return result
