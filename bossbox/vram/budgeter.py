"""
VRAM Budgeter — BossBox Atomic Step 8
=======================================
Background thread that tracks VRAM allocation across loaded Ollama models
and prevents out-of-memory crashes by proactively evicting lower-priority
models before loading higher-priority ones.

Eviction priority (lowest first): Reasoner → Specialist → Micro.
Nano is never evicted — it must always remain hot in memory.

Public API
----------
VRAMBudgeter(...)
    .request_load(model)   -> bool   check budget; evict if needed; True = safe to load
    .current_allocation()  -> dict   loaded model → estimated VRAM bytes
    .available()           -> float  remaining VRAM bytes
    .start()                         start background polling thread (idempotent)
    .stop()                          signal thread to exit and join
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

import httpx

from bossbox.vram.exceptions import VRAMBudgetError, VRAMDetectionError  # noqa: F401

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Eviction order: index 0 is the first model to evict (lowest priority).
# Nano is intentionally absent — it is never evicted.
EVICTION_ORDER: list[str] = ["reasoner", "specialist", "micro"]

# Fallback VRAM budget when platform detection is unavailable (2 GiB in bytes).
DEFAULT_VRAM_BYTES: float = 2.0 * 1024**3

# Conservative size estimates (bytes) keyed by bare Ollama model name.
# Used when Ollama does not report a size_vram value for a model.
MODEL_SIZE_ESTIMATES: dict[str, float] = {
    "smollm:360m":          400.0 * 1024**2,
    "smollm:1.7b":        1_200.0 * 1024**2,
    "qwen2.5-coder:1.5b": 1_100.0 * 1024**2,
    "deepseek-r1:7b":     5_500.0 * 1024**2,
}

# Default fallback when no estimate is available (half the default budget).
_UNKNOWN_MODEL_ESTIMATE: float = DEFAULT_VRAM_BYTES * 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_vram_bytes() -> float:
    """
    Detect total VRAM on the primary GPU.

    Tries pynvml first; falls back to DEFAULT_VRAM_BYTES if the library is
    not installed.  Raises VRAMDetectionError only when pynvml is present
    but reports an unexpected error.
    """
    try:
        import pynvml  # optional dependency — not required for operation

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        pynvml.nvmlShutdown()
        detected = float(info.total)
        log.debug(
            "VRAM detected via pynvml: %.2f GiB", detected / 1024**3
        )
        return detected
    except ImportError:
        log.debug(
            "pynvml not installed; using default VRAM budget %.1f GiB",
            DEFAULT_VRAM_BYTES / 1024**3,
        )
        return DEFAULT_VRAM_BYTES
    except Exception as exc:
        raise VRAMDetectionError(f"VRAM detection failed: {exc}") from exc


def strip_provider(model: str) -> str:
    """
    Normalise a model string by stripping a leading 'provider/' prefix.

    Examples
    --------
    "ollama/smollm:1.7b"  → "smollm:1.7b"
    "smollm:1.7b"         → "smollm:1.7b"
    """
    if "/" in model:
        _, _, bare = model.partition("/")
        return bare
    return model


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class VRAMBudgeter:
    """
    Tracks VRAM allocation and manages model eviction for Ollama.

    Parameters
    ----------
    vram_budget_bytes:
        Total VRAM to manage in bytes.  Auto-detected when None.
    ollama_base_url:
        Base URL for the Ollama HTTP API.
    tier_to_model:
        Mapping of tier name → bare Ollama model name, e.g.
        ``{"nano": "smollm:360m", "micro": "smollm:1.7b", ...}``.
        Used to determine which tier a loaded model belongs to so the correct
        eviction priority can be applied.
    thought_cb:
        Optional callable invoked with a human-readable string whenever the
        budgeter emits a thought-stream event (e.g. eviction notices).
    poll_interval:
        Seconds between background polls to Ollama's /api/ps endpoint.
    auto_start:
        Whether to start the background polling thread in ``__init__``.
        Set to False in tests to avoid spawning threads unnecessarily.
    """

    def __init__(
        self,
        vram_budget_bytes: float | None = None,
        ollama_base_url: str = "http://localhost:11434",
        tier_to_model: dict[str, str] | None = None,
        thought_cb: Callable[[str], None] | None = None,
        poll_interval: float = 5.0,
        auto_start: bool = True,
    ) -> None:
        self._budget: float = (
            vram_budget_bytes
            if vram_budget_bytes is not None
            else _detect_vram_bytes()
        )
        self._base_url = ollama_base_url.rstrip("/")
        self._tier_to_model: dict[str, str] = tier_to_model or {}
        self._thought_cb = thought_cb
        self._poll_interval = poll_interval

        # loaded bare model name → estimated VRAM bytes in use
        self._loaded: dict[str, float] = {}
        self._lock = threading.RLock()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        if auto_start:
            self.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_load(self, model: str) -> bool:
        """
        Request that *model* be loaded.

        Checks whether loading would exceed the VRAM budget.  If so, evicts
        the lowest-priority loaded model(s) in EVICTION_ORDER until there is
        sufficient room.  Nano is never a candidate for eviction.

        Returns True when loading is safe to proceed, False when the model
        cannot fit even after all evictable models have been removed.
        """
        bare = strip_provider(model)
        size = self._size_for(bare)

        with self._lock:
            # Already loaded — no additional VRAM needed.
            if bare in self._loaded:
                return True
            if self._fits(size):
                return True

            for tier in EVICTION_ORDER:
                tier_model = self._tier_to_model.get(tier)
                if (
                    tier_model
                    and tier_model in self._loaded
                    and tier_model != bare
                ):
                    self._evict(tier_model, bare)
                    if self._fits(size):
                        return True

            # Model cannot fit even after all evictable models are removed.
            return False

    def current_allocation(self) -> dict[str, float]:
        """Return a snapshot of ``{model_name: estimated_bytes}`` for all loaded models."""
        with self._lock:
            return dict(self._loaded)

    def available(self) -> float:
        """Return estimated available VRAM in bytes."""
        with self._lock:
            return self._budget - sum(self._loaded.values())

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="vram-budgeter",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the polling thread to exit and wait for it to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fits(self, size_bytes: float) -> bool:
        """True when *size_bytes* can be added without exceeding the budget.

        Caller must hold ``self._lock``.
        """
        return (sum(self._loaded.values()) + size_bytes) <= self._budget

    def _size_for(self, bare_model: str) -> float:
        """
        Return the size estimate in bytes for *bare_model*.

        Prefers the live value already tracked in ``_loaded`` (reported by
        Ollama), then ``MODEL_SIZE_ESTIMATES``, then ``_UNKNOWN_MODEL_ESTIMATE``.
        """
        with self._lock:
            if bare_model in self._loaded:
                return self._loaded[bare_model]
        return MODEL_SIZE_ESTIMATES.get(bare_model, _UNKNOWN_MODEL_ESTIMATE)

    def _evict(self, model: str, loading_model: str) -> None:
        """
        Unload *model* from Ollama and remove it from the tracked set.

        Emits a thought-stream message and calls Ollama's generate endpoint
        with ``keep_alive: 0`` to trigger immediate unloading.

        Caller must hold ``self._lock``.
        """
        message = f"Offloading {model} to free VRAM for {loading_model}."
        log.info(message)
        self._emit_thought(message)

        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    f"{self._base_url}/api/generate",
                    json={"model": model, "keep_alive": 0},
                )
        except httpx.RequestError as exc:
            log.warning("Could not signal Ollama to evict %s: %s", model, exc)

        self._loaded.pop(model, None)

    def _emit_thought(self, message: str) -> None:
        """Deliver *message* to the thought-stream callback if one is registered."""
        if self._thought_cb is not None:
            try:
                self._thought_cb(message)
            except Exception:
                log.exception("thought_cb raised an exception")

    def _poll_loop(self) -> None:
        """Background thread body: refresh loaded-model state on each interval."""
        while not self._stop_event.is_set():
            try:
                self._refresh_loaded()
            except Exception as exc:
                log.debug("VRAM poll error (will retry): %s", exc)
            self._stop_event.wait(self._poll_interval)

    def _refresh_loaded(self) -> None:
        """
        Query Ollama's ``/api/ps`` endpoint and update the loaded-model map.

        Silently skips the update when Ollama is unreachable so the budgeter
        degrades gracefully on systems without Ollama running.
        """
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self._base_url}/api/ps")
                resp.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            log.debug("Ollama /api/ps unavailable (skipping poll): %s", exc)
            return

        data = resp.json()
        fresh: dict[str, float] = {}
        for entry in data.get("models", []):
            raw_name: str = entry.get("name", "")
            bare = strip_provider(raw_name)
            # Prefer size_vram; fall back to total size; then use estimate.
            size = float(
                entry.get("size_vram")
                or entry.get("size")
                or MODEL_SIZE_ESTIMATES.get(bare, 0.0)
            )
            if bare:
                fresh[bare] = size

        with self._lock:
            self._loaded = fresh
