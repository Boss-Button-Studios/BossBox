"""
Supervisor State Machine — BossBox Atomic Step 15
==================================================
Orchestrates a task through the full pipeline:
  ingest → decompose → human_checkpoint → execute → review → complete

Security checkpoints are mandatory code paths in every execution.  They are
not optional integrations — no pipeline execution completes without passing
through the input shield (ingest) and action shield (execute, review).  The
default PassthroughShield always passes; it is replaced by the real Hypervisor
client in Step 21.

Public API
----------
Supervisor(envelope, provider, audit_logger, *, vram_budgeter, input_shield,
           action_shield, model)

  async run()             → TaskEnvelope   drive all stages to completion
  pause()                                  mark envelope paused (non-blocking)
  abort()                                  stop run; envelope status = failed
  async redirect(direction)                append direction, resume checkpoint
  async approve_checkpoint()               unblock the human checkpoint wait
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

from bossbox.audit.logger import AuditLogger
from bossbox.pipeline.decomposer import DecompositionResult, decompose
from bossbox.pipeline.envelope import TaskEnvelope
from bossbox.providers.base import ModelProvider

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shield protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ShieldProtocol(Protocol):
    """Interface satisfied by both PassthroughShield and the real HypervisorClient."""

    async def evaluate_input(self, goal: str, content: str) -> bool:
        """Return True to allow content into the pipeline, False to block."""
        ...

    async def evaluate_action(self, goal: str, action: str) -> bool:
        """Return True to allow the proposed action, False to block."""
        ...


class PassthroughShield:
    """
    Placeholder shield — always passes.

    Replaced by the real Hypervisor process client in Step 21.
    The Supervisor always calls through this interface so the security
    code paths are in place before the Hypervisor exists.
    """

    async def evaluate_input(self, goal: str, content: str) -> bool:  # noqa: ARG002
        return True

    async def evaluate_action(self, goal: str, action: str) -> bool:  # noqa: ARG002
        return True


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

_STAGES = ("ingest", "decompose", "human_checkpoint", "execute", "review", "complete")


class Supervisor:
    """
    Async state machine that drives a TaskEnvelope through the pipeline.

    Parameters
    ----------
    envelope:
        The task to execute.  Mutated in place as stages advance.
    provider:
        Micro-tier ModelProvider used for decomposition and execution.
    audit_logger:
        Append-only audit log.  Every stage transition is recorded here.
    vram_budgeter:
        Optional VRAMBudgeter.  When supplied, ``request_load`` is called
        before every provider invocation.  Absent in tests that don't need it.
    input_shield:
        Evaluated at ingest on the raw document/goal.  Defaults to
        PassthroughShield.  Mandatory code path regardless of implementation.
    action_shield:
        Evaluated before execute and review.  Defaults to PassthroughShield.
        Mandatory code path regardless of implementation.
    model:
        Optional model identifier forwarded to provider calls.
    """

    STAGES = _STAGES

    def __init__(
        self,
        envelope: TaskEnvelope,
        provider: ModelProvider,
        audit_logger: AuditLogger,
        *,
        vram_budgeter: Any = None,
        input_shield: ShieldProtocol | None = None,
        action_shield: ShieldProtocol | None = None,
        model: str | None = None,
    ) -> None:
        self._envelope = envelope
        self._provider = provider
        self._audit = audit_logger
        self._vram = vram_budgeter
        self._input_shield: ShieldProtocol = input_shield or PassthroughShield()
        self._action_shield: ShieldProtocol = action_shield or PassthroughShield()
        self._model = model

        self._aborted: bool = False
        self._decomposition: DecompositionResult | None = None
        self._checkpoint_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Public control interface
    # ------------------------------------------------------------------

    @property
    def envelope(self) -> TaskEnvelope:
        return self._envelope

    async def run(self) -> TaskEnvelope:
        """
        Drive the envelope through all pipeline stages and return it.

        Always returns — failures set ``envelope.status = "failed"`` rather
        than raising.  The caller should inspect the envelope status and
        thought stream for details.
        """
        self._checkpoint_event = asyncio.Event()
        self._envelope.status = "running"

        for stage in _STAGES:
            if self._aborted:
                break
            self._envelope.current_stage = stage
            self._log_transition(stage)
            try:
                await getattr(self, f"_stage_{stage}")()
            except Exception as exc:  # noqa: BLE001
                log.error("Supervisor caught unexpected error in stage %s: %s", stage, exc)
                self._envelope.log_event("pipeline_error", str(exc))
                self._envelope.status = "failed"
                return self._envelope
            if self._aborted:
                break

        if not self._aborted:
            self._envelope.status = "complete"
        return self._envelope

    def pause(self) -> None:
        """Mark the envelope paused.  Does not interrupt run() mid-stage."""
        self._envelope.status = "paused"
        self._envelope.log_event("pipeline_pause", "Supervisor paused by caller.")

    def abort(self) -> None:
        """Immediately stop the run.  Unblocks any pending checkpoint wait."""
        self._aborted = True
        self._envelope.status = "failed"
        self._envelope.log_event("pipeline_abort", "Supervisor aborted by caller.")
        if self._checkpoint_event is not None:
            self._checkpoint_event.set()

    async def redirect(self, new_direction: str) -> None:
        """
        Append *new_direction* to the envelope and resume without restarting.

        If the supervisor is waiting at the human checkpoint, this unblocks it.
        """
        self._envelope.context.append({"redirect": new_direction, "type": "redirect"})
        self._envelope.add_thought("progress", f"Redirected: {new_direction}")
        self._envelope.log_event("redirect", f"New direction: {new_direction}")
        self._audit.log(
            "redirect",
            data={"direction": new_direction},
            task_id=self._envelope.task_id,
        )
        if self._checkpoint_event is not None:
            self._checkpoint_event.set()

    async def approve_checkpoint(self) -> None:
        """Unblock the human checkpoint wait."""
        if self._checkpoint_event is not None:
            self._checkpoint_event.set()

    # ------------------------------------------------------------------
    # Internal: stage implementations
    # ------------------------------------------------------------------

    async def _stage_ingest(self) -> None:
        """
        Validate the envelope and run the input shield on the raw goal.

        The input shield is a mandatory security checkpoint — it fires on every
        execution regardless of privilege level.  A False return aborts the run.
        """
        goal = self._envelope.original_input

        # Mandatory input shield — always called.
        allowed = await self._input_shield.evaluate_input(goal, goal)
        self._audit.log(
            "input_shield",
            data={"allowed": allowed, "stage": "ingest"},
            task_id=self._envelope.task_id,
        )
        if not allowed:
            self._envelope.log_event("security_block", "Input shield blocked the goal.")
            self._envelope.add_thought("progress", "Input blocked by security shield.")
            self.abort()
            return

        self._envelope.add_thought("progress", "Ingest complete.")

    async def _stage_decompose(self) -> None:
        """Check VRAM budget, then decompose the goal into subtasks."""
        model = self._model
        provider_kwargs: dict = {}
        if model and self._vram is not None:
            strategy = self._vram.request_load(model)
            if strategy.num_gpu != -1:
                provider_kwargs["num_gpu"] = strategy.num_gpu

        self._decomposition = await decompose(
            self._envelope.original_input,
            self._provider,
            self._envelope,
            model=model,
            **provider_kwargs,
        )

    async def _stage_human_checkpoint(self) -> None:
        """
        Pause for human review of the decomposition plan.

        Skipped entirely when ``envelope.auto_approve`` is True (Trust Mode).
        Security checkpoints in later stages are unaffected by auto_approve.
        """
        if self._envelope.auto_approve:
            self._envelope.log_event(
                "checkpoint_skipped",
                "Decomposition checkpoint skipped (auto_approve=True).",
            )
            self._audit.log(
                "checkpoint_skipped",
                data={"reason": "auto_approve"},
                task_id=self._envelope.task_id,
            )
            self._envelope.add_thought("progress", "Checkpoint skipped (Trust Mode).")
            return

        # Pause and wait for human approval or redirect.
        self._envelope.status = "paused"
        self._envelope.log_event("checkpoint_wait", "Waiting for human approval.")
        self._envelope.add_thought("progress", "Waiting at human checkpoint…")
        assert self._checkpoint_event is not None
        self._checkpoint_event.clear()
        await self._checkpoint_event.wait()

        if self._aborted:
            return

        self._envelope.status = "running"
        self._checkpoint_event.clear()
        self._envelope.log_event("checkpoint_approved", "Human checkpoint passed.")
        self._envelope.add_thought("progress", "Checkpoint approved.")

    async def _stage_execute(self) -> None:
        """
        Run the action shield then invoke the provider to execute the goal.

        The action shield is a mandatory security checkpoint.  A False return
        aborts the pipeline immediately.
        """
        goal = self._envelope.original_input

        # Mandatory action shield — always called.
        action_desc = (
            f"Execute goal: {goal[:200]}"
            if not self._decomposition
            else f"Execute {len(self._decomposition.core_tasks)} decomposed tasks for: {goal[:120]}"
        )
        allowed = await self._action_shield.evaluate_action(goal, action_desc)
        self._audit.log(
            "action_shield",
            data={"allowed": allowed, "stage": "execute", "action": action_desc[:200]},
            task_id=self._envelope.task_id,
        )
        if not allowed:
            self._envelope.log_event("security_block", "Action shield blocked execution.")
            self._envelope.add_thought("progress", "Execution blocked by security shield.")
            self.abort()
            return

        # VRAM check before model call.
        provider_kwargs: dict = {}
        if self._model and self._vram is not None:
            strategy = self._vram.request_load(self._model)
            if strategy.num_gpu != -1:
                provider_kwargs["num_gpu"] = strategy.num_gpu

        model_label = self._model or "model"
        redirects = [
            e["redirect"] for e in self._envelope.context if e.get("type") == "redirect"
        ]
        kwargs: dict = {}
        if self._model:
            kwargs["model"] = self._model
        kwargs.update(provider_kwargs)

        # Execute as individual subtasks when decomposition produced 2+ tasks.
        if self._decomposition and len(self._decomposition.core_tasks) > 1:
            await self._execute_decomposed(goal, model_label, redirects, kwargs)
        else:
            await self._execute_single(goal, model_label, redirects, kwargs)

    async def _execute_single(
        self,
        goal: str,
        model_label: str,
        redirects: list[str],
        kwargs: dict,
    ) -> None:
        """Execute the goal as a single provider call (no decomposition)."""
        self._envelope.add_thought("progress", f"Asking {model_label} to execute…")

        user_content = goal
        if redirects:
            user_content = goal + "\n\nAdditional direction: " + "; ".join(redirects)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a capable assistant. Complete the user's goal "
                    "concisely and accurately."
                ),
            },
            {"role": "user", "content": user_content},
        ]

        try:
            result = await self._provider.complete(messages, **kwargs)
        except Exception as exc:
            self._envelope.log_event("execute_error", str(exc))
            self._envelope.add_thought("progress", f"Execution failed: {exc}")
            self._envelope.status = "failed"
            self._aborted = True
            return

        self._envelope.result = result
        self._envelope.add_thought("progress", "Execution complete.")

    async def _execute_decomposed(
        self,
        goal: str,
        model_label: str,
        redirects: list[str],
        kwargs: dict,
    ) -> None:
        """Execute each core subtask individually and aggregate results."""
        assert self._decomposition is not None
        tasks = self._decomposition.core_tasks
        n = len(tasks)
        self._envelope.add_thought("progress", f"Executing {n} tasks with {model_label}…")

        redirect_suffix = (
            "\n\nAdditional direction: " + "; ".join(redirects) if redirects else ""
        )
        task_results: list[str] = []

        task_list = "\n".join(
            f"  {j}. {t.title}" for j, t in enumerate(tasks, 1)
        )

        for i, task in enumerate(tasks, 1):
            if self._aborted:
                break
            self._envelope.add_thought(
                "progress", f"Task {i}/{n}: {task.title}"
            )
            task_prompt = (
                f"Overall goal: {goal}{redirect_suffix}\n\n"
                f"Full plan ({n} tasks):\n{task_list}\n\n"
                f"Your task ({i}/{n}): {task.title}"
            )
            if task.description and task.description != goal:
                task_prompt += f"\n{task.description}"
            task_prompt += (
                f"\n\nFocus ONLY on this task. "
                f"The other {n - 1} tasks will be handled separately."
            )

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a capable assistant. Complete the specific task "
                        "concisely and accurately."
                    ),
                },
                {"role": "user", "content": task_prompt},
            ]

            try:
                result = await self._provider.complete(messages, **kwargs)
                task_results.append(f"## {task.title}\n{result}")
                self._envelope.add_thought("progress", f"Task {i}/{n} complete.")
            except Exception as exc:
                self._envelope.log_event("execute_error", f"Task {i} '{task.title}': {exc}")
                self._envelope.add_thought("progress", f"Task {i}/{n} failed: {exc}")
                task_results.append(f"## {task.title}\n[Failed: {exc}]")

        if task_results:
            self._envelope.result = "\n\n".join(task_results)
            self._envelope.add_thought("progress", f"All {n} tasks complete.")
        else:
            self._envelope.status = "failed"
            self._aborted = True

    async def _stage_review(self) -> None:
        """
        Run the action shield on the result before marking the task complete.

        The action shield is mandatory here too — it validates that the
        produced output is consistent with the original goal before the
        pipeline considers the work done.
        """
        goal = self._envelope.original_input
        result_preview = (self._envelope.result or "")[:200]

        # Mandatory action shield — always called.
        allowed = await self._action_shield.evaluate_action(
            goal, f"Review result: {result_preview}"
        )
        self._audit.log(
            "action_shield",
            data={"allowed": allowed, "stage": "review", "result_preview": result_preview},
            task_id=self._envelope.task_id,
        )
        if not allowed:
            self._envelope.log_event("security_block", "Action shield blocked result.")
            self._envelope.add_thought("progress", "Result blocked by security shield.")
            self.abort()
            return

        self._envelope.add_thought("progress", "Review complete.")

    async def _stage_complete(self) -> None:
        """Finalise the envelope."""
        self._envelope.add_thought("progress", "Pipeline complete.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_transition(self, stage: str) -> None:
        self._audit.log(
            "stage_transition",
            data={"stage": stage},
            task_id=self._envelope.task_id,
        )
        self._envelope.log_event("stage_transition", f"Entering stage: {stage}")
