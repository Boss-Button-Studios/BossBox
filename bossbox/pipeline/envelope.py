"""
bossbox/pipeline/envelope.py

TaskEnvelope — the central data structure that carries a task through every
stage of the BossBox pipeline. Every piece of state the supervisor needs lives
here. Two fields have invariants enforced at this layer:

  original_input  — write-once. Set at creation; raises AttributeError on any
                    subsequent assignment. This is the ground truth the
                    hypervisor's goal store is initialised from; it must not
                    drift as the pipeline accumulates context.

  privilege_level — validated 0–4. Assignment outside that range raises
                    ValueError immediately so callers never silently store a
                    nonsense privilege level.

Design notes
------------
* thought_stream entries are dicts with a 'source' key so the GUI can route
  them to the correct display lane (progress vs model reasoning).
* events is a separate list from thought_stream: events are internal pipeline
  transitions and anomaly flags; thoughts are content surfaced to the user.
* to_dict() produces a fully JSON-serialisable representation — no datetime
  objects, no non-serialisable types. The audit logger (Step 4) depends on
  this guarantee.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Status and privilege constants
# ---------------------------------------------------------------------------

VALID_STATUSES = frozenset({"pending", "running", "paused", "complete", "failed"})
PRIVILEGE_RANGE = range(0, 5)   # 0–4 inclusive


# ---------------------------------------------------------------------------
# TaskEnvelope
# ---------------------------------------------------------------------------

@dataclass
class TaskEnvelope:
    """
    Carries a single task through the full pipeline lifecycle.

    Fields map directly to Section 8.1 of the BossBox spec. Two additional
    internal fields are included here that are implicit in the spec:

        events      — timestamped pipeline-internal log entries (not surfaced
                      to the user directly; written to the audit trail).
        _input_locked — private sentinel that enforces write-once on
                       original_input.
    """

    # ------------------------------------------------------------------
    # Identity and provenance
    # ------------------------------------------------------------------
    task_id: str
    created_at: datetime

    # Write-once — enforced in __setattr__; see below.
    original_input: str

    declared_document_type: str | None
    routing_decision: str
    provenance_chain: list[dict[str, Any]]
    human_initiated: bool

    # ------------------------------------------------------------------
    # Execution context
    # ------------------------------------------------------------------
    context: list[dict[str, Any]]
    current_stage: str
    privilege_level: int                    # 0–4; validated in __setattr__

    # ------------------------------------------------------------------
    # Security state
    # ------------------------------------------------------------------
    hostile_content_acknowledged: bool

    # ------------------------------------------------------------------
    # Pipeline control
    # ------------------------------------------------------------------
    thought_stream: list[dict[str, Any]]   # surfaced to GUI
    auto_approve: bool                      # Trust Mode — skips decomp checkpoint

    # ------------------------------------------------------------------
    # Outcome
    # ------------------------------------------------------------------
    result: str | None
    status: str                             # pending|running|paused|complete|failed

    # ------------------------------------------------------------------
    # Internal event log (not in spec field list but required by methods)
    # ------------------------------------------------------------------
    events: list[dict[str, Any]] = field(default_factory=list)

    # Private sentinel — must be last so __post_init__ can set it safely.
    _input_locked: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------------
    # Post-init validation and lock
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status {self.status!r}. Must be one of {sorted(VALID_STATUSES)}."
            )
        if self.privilege_level not in PRIVILEGE_RANGE:
            raise ValueError(
                f"privilege_level must be 0–4, got {self.privilege_level!r}."
            )
        # Lock original_input after all field assignments are done.
        object.__setattr__(self, "_input_locked", True)

    # ------------------------------------------------------------------
    # Write-once and privilege enforcement
    # ------------------------------------------------------------------

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "original_input" and getattr(self, "_input_locked", False):
            raise AttributeError(
                "original_input is write-once and cannot be modified after creation."
            )
        if name == "privilege_level":
            if not isinstance(value, int) or value not in PRIVILEGE_RANGE:
                raise ValueError(
                    f"privilege_level must be an integer 0–4, got {value!r}."
                )
        if name == "status":
            if value not in VALID_STATUSES:
                raise ValueError(
                    f"Invalid status {value!r}. Must be one of {sorted(VALID_STATUSES)}."
                )
        object.__setattr__(self, name, value)

    # ------------------------------------------------------------------
    # Public mutators
    # ------------------------------------------------------------------

    def log_event(self, event_type: str, detail: str, extra: dict | None = None) -> None:
        """
        Append a timestamped event to the internal events list.

        These entries are written to the audit trail by the audit logger.
        They are *not* shown in the thought stream — they are pipeline
        bookkeeping (stage transitions, anomaly flags, privilege requests).

        Parameters
        ----------
        event_type:
            Short category string, e.g. "stage_transition", "anomaly_flag",
            "privilege_request", "checkpoint".
        detail:
            Human-readable description of what happened.
        extra:
            Optional dict of additional structured data. Must be
            JSON-serialisable; not validated here.
        """
        entry: dict[str, Any] = {
            "ts": _utcnow_iso(),
            "task_id": self.task_id,
            "event_type": event_type,
            "detail": detail,
        }
        if extra:
            entry["extra"] = extra
        self.events.append(entry)

    def add_thought(self, source: str, content: str) -> None:
        """
        Append an entry to the thought stream.

        The thought stream is the user-visible reasoning lane in the pipeline
        view. Two source categories are defined by the GUI:

          "progress"   — deterministic stage-transition messages; always shown.
          "reasoning"  — model chain-of-thought or summarised stage output;
                         shown when the thought stream panel is expanded.

        Any other source string is accepted without error; the GUI will render
        it in a default lane.

        Parameters
        ----------
        source:
            Origin label. Conventionally "progress", "reasoning", or a model
            tier name such as "nano", "micro", "reasoner".
        content:
            The text to display.
        """
        self.thought_stream.append(
            {
                "ts": _utcnow_iso(),
                "source": source,
                "content": content,
            }
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return a fully JSON-serialisable representation of the envelope.

        datetime objects are converted to ISO 8601 UTC strings. The private
        _input_locked sentinel is excluded. All other fields are included.

        This is the canonical output consumed by the audit logger and the
        task state persistence layer.
        """
        return {
            "task_id": self.task_id,
            "created_at": _dt_to_iso(self.created_at),
            "original_input": self.original_input,
            "declared_document_type": self.declared_document_type,
            "routing_decision": self.routing_decision,
            "provenance_chain": self.provenance_chain,
            "human_initiated": self.human_initiated,
            "context": self.context,
            "current_stage": self.current_stage,
            "privilege_level": self.privilege_level,
            "hostile_content_acknowledged": self.hostile_content_acknowledged,
            "thought_stream": self.thought_stream,
            "auto_approve": self.auto_approve,
            "result": self.result,
            "status": self.status,
            "events": self.events,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_envelope(
    original_input: str,
    *,
    human_initiated: bool = True,
    declared_document_type: str | None = None,
    auto_approve: bool = False,
    task_id: str | None = None,
) -> TaskEnvelope:
    """
    Factory for TaskEnvelope. Generates a task_id and timestamps the envelope.

    All fields not supplied here have safe defaults appropriate for the start
    of a new task. The caller may mutate them as the pipeline advances —
    subject to write-once and validation constraints.

    Parameters
    ----------
    original_input:
        The user's raw goal statement. Write-once after creation.
    human_initiated:
        True when the task was submitted by a human via the GUI or CLI.
        False for tasks spawned programmatically by the pipeline.
    declared_document_type:
        Optional document type hint supplied by the user at task submission.
    auto_approve:
        When True, the decomposition human-checkpoint is skipped (Trust Mode).
        Security checkpoints are unaffected.
    task_id:
        Override the generated UUID. Intended for testing and deterministic
        audit replay only.
    """
    now = datetime.now(tz=timezone.utc)
    tid = task_id if task_id is not None else str(uuid.uuid4())

    envelope = TaskEnvelope(
        task_id=tid,
        created_at=now,
        original_input=original_input,
        declared_document_type=declared_document_type,
        routing_decision="",
        provenance_chain=[],
        human_initiated=human_initiated,
        context=[],
        current_stage="pending",
        privilege_level=0,
        hostile_content_acknowledged=False,
        thought_stream=[],
        auto_approve=auto_approve,
        result=None,
        status="pending",
        events=[],
    )

    envelope.log_event(
        "envelope_created",
        f"Task envelope created. human_initiated={human_initiated}, "
        f"auto_approve={auto_approve}.",
    )
    return envelope


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _dt_to_iso(dt: datetime) -> str:
    """Convert a datetime to an ISO 8601 string. Attaches UTC if naive."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
