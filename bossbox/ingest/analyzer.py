"""
Linguistic Analysis Agent — BossBox Atomic Step 12
===================================================
Layer 2 of the document trust pipeline (spec Section 9.2).

Receives sanitized text from the Physical Sanitizer (Step 9) and a declared
document type.  Invokes the Micro model using the injection_detector skill
profile to detect prompt injection patterns and assess document type coherence.
Returns a typed DocumentAnalysis result.

Fail-safe principle: any model response that cannot be parsed into a valid
DocumentAnalysis defaults to overall_verdict='block'.  The pipeline must never
proceed on ambiguous analysis output.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bossbox.ingest.exceptions import AnalyzerError, AnalyzerParseError
from bossbox.providers.base import ModelProvider

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception scrubbing
# ---------------------------------------------------------------------------

# Redact URL credentials and absolute paths before they reach the Python log.
# Why: httpx exceptions from provider.complete() may include connection URLs
# that could contain embedded credentials or expose local filesystem structure.
_URL_CRED_RE = re.compile(r"//[^@\s]+@")
_ABS_PATH_RE = re.compile(r"/(?:home|root|usr|var|etc|tmp|opt|proc)/\S+")


def _scrub_exc(exc: Exception) -> str:
    """Return a loggable string with URL credentials and paths redacted."""
    msg = str(exc)
    msg = _URL_CRED_RE.sub("//[redacted]@", msg)
    msg = _ABS_PATH_RE.sub("[path redacted]", msg)
    return msg


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SKILLS_ROOT = Path(__file__).parents[2] / "skills" / "default"
_PROFILE_PATH = _SKILLS_ROOT / "injection_detector.yaml"

# ---------------------------------------------------------------------------
# Valid enum values
# ---------------------------------------------------------------------------

_VALID_VERDICTS = frozenset(["pass", "warn", "block"])
_VALID_CATEGORIES = frozenset([
    "direct_instruction",
    "role_reassignment",
    "context_escape",
    "authority_spoofing",
    "urgency_override",
])

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FlaggedPassage:
    """A single passage flagged by the injection detector."""
    text: str
    category: str
    location: str


@dataclass
class DocumentAnalysis:
    """
    Typed result of linguistic analysis.

    Mirrors the document_analysis schema (skills/default/schemas/document_analysis.yaml).
    """
    declared_type: str
    assessed_type: str
    type_match: bool
    coherence_score: float
    injection_verdict: str          # pass | warn | block
    flagged_passages: list[FlaggedPassage] = field(default_factory=list)
    overall_verdict: str = "block"  # pass | warn | block


# ---------------------------------------------------------------------------
# Skill profile loader
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """Load the system prompt from injection_detector.yaml."""
    if not _PROFILE_PATH.exists():
        raise AnalyzerError(
            f"Injection detector skill profile not found at {_PROFILE_PATH}. "
            "Ensure Step 10 has been completed."
        )
    with _PROFILE_PATH.open() as fh:
        profile = yaml.safe_load(fh)
    prompt = profile.get("system_prompt", "")
    if not prompt or not prompt.strip():
        raise AnalyzerError("injection_detector.yaml has an empty system_prompt.")
    return prompt.strip()


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_yaml_block(response: str) -> str:
    """
    Extract YAML content from the model response.

    Handles both raw YAML and YAML fenced in ```yaml ... ``` blocks.
    Returns the YAML string, or raises AnalyzerParseError if nothing found.
    """
    # Try fenced code block first
    fenced = re.search(r"```(?:yaml)?\s*\n(.*?)```", response, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    # Fall back: look for a 'document_analysis:' key anywhere in the response
    idx = response.find("document_analysis:")
    if idx != -1:
        return response[idx:].strip()

    raise AnalyzerParseError(
        "Model response did not contain a recognisable YAML block. "
        f"Response preview: {response[:200]!r}"
    )


def _parse_flagged_passages(raw: list[Any]) -> list[FlaggedPassage]:
    """Convert raw YAML list of passage dicts to FlaggedPassage dataclasses."""
    passages: list[FlaggedPassage] = []
    if not isinstance(raw, list):
        return passages
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", ""))
        category = str(item.get("category", ""))
        location = str(item.get("location", ""))
        # Normalise unknown categories to direct_instruction rather than dropping
        if category not in _VALID_CATEGORIES:
            log.warning("Unknown flagged_passages category %r — keeping as-is", category)
        passages.append(FlaggedPassage(text=text, category=category, location=location))
    return passages


def _parse_response(response: str, declared_type: str) -> DocumentAnalysis:
    """
    Parse the model's YAML response into a DocumentAnalysis.

    On any parsing failure returns a fail-safe block result so the pipeline
    never proceeds on ambiguous output.
    """
    try:
        yaml_str = _extract_yaml_block(response)
        data = yaml.safe_load(yaml_str)
    except AnalyzerParseError:
        raise
    except Exception as exc:
        raise AnalyzerParseError(f"YAML parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise AnalyzerParseError(f"Expected a YAML mapping, got {type(data).__name__}")

    # Unwrap the top-level 'document_analysis' key if present
    inner = data.get("document_analysis", data)
    if not isinstance(inner, dict):
        raise AnalyzerParseError("document_analysis value is not a mapping")

    # ── Extract and validate each required field ──────────────────────────

    assessed_type = str(inner.get("assessed_type", declared_type))
    type_match_raw = inner.get("type_match", True)
    type_match = bool(type_match_raw)

    try:
        coherence_score = float(inner.get("coherence_score", 0.0))
        coherence_score = max(0.0, min(1.0, coherence_score))
    except (TypeError, ValueError):
        coherence_score = 0.0

    injection_verdict = str(inner.get("injection_verdict", "block")).lower()
    if injection_verdict not in _VALID_VERDICTS:
        log.warning("Unrecognised injection_verdict %r — defaulting to block", injection_verdict)
        injection_verdict = "block"

    flagged_passages = _parse_flagged_passages(inner.get("flagged_passages", []))

    overall_verdict = str(inner.get("overall_verdict", "block")).lower()
    if overall_verdict not in _VALID_VERDICTS:
        log.warning("Unrecognised overall_verdict %r — defaulting to block", overall_verdict)
        overall_verdict = "block"

    return DocumentAnalysis(
        declared_type=declared_type,
        assessed_type=assessed_type,
        type_match=type_match,
        coherence_score=coherence_score,
        injection_verdict=injection_verdict,
        flagged_passages=flagged_passages,
        overall_verdict=overall_verdict,
    )


def _fail_safe(declared_type: str, reason: str) -> DocumentAnalysis:
    """Return a conservative block result when analysis cannot complete."""
    log.error("Linguistic analysis fail-safe triggered: %s", reason)
    return DocumentAnalysis(
        declared_type=declared_type,
        assessed_type="unknown",
        type_match=False,
        coherence_score=0.0,
        injection_verdict="block",
        flagged_passages=[],
        overall_verdict="block",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def analyze(
    sanitized_text: str,
    declared_type: str,
    provider: ModelProvider,
    model: str | None = None,
) -> DocumentAnalysis:
    """
    Run Layer 2 linguistic and coherence analysis on *sanitized_text*.

    Parameters
    ----------
    sanitized_text:
        Clean text produced by the Physical Sanitizer (Step 9).
    declared_type:
        The document type asserted by the submitter
        (e.g. 'invoice', 'contract', 'email').
    provider:
        A configured ModelProvider (Micro tier).
        The caller is responsible for supplying the correct tier.
    model:
        Optional model override forwarded to provider.complete().

    Returns
    -------
    DocumentAnalysis
        Typed analysis result.  Always returns (never raises) — failures
        produce a fail-safe block verdict.
    """
    try:
        system_prompt = _load_system_prompt()
    except AnalyzerError as exc:
        return _fail_safe(declared_type, str(exc))

    user_content = (
        f"declared_type: {declared_type}\n\n"
        f"--- BEGIN DOCUMENT ---\n{sanitized_text}\n--- END DOCUMENT ---"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    kwargs: dict = {}
    if model is not None:
        kwargs["model"] = model

    try:
        response = await provider.complete(messages, **kwargs)
    except Exception as exc:
        # Scrub before logging — provider exceptions may contain connection URLs.
        return _fail_safe(declared_type, f"Provider call failed: {_scrub_exc(exc)}")

    try:
        return _parse_response(response, declared_type)
    except AnalyzerParseError as exc:
        return _fail_safe(declared_type, str(exc))
