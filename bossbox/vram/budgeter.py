"""
VRAM Budgeter — BossBox Atomic Step 8
=======================================
Background thread that tracks VRAM allocation across loaded Ollama models
and prevents out-of-memory crashes by proactively evicting lower-priority
models before loading higher-priority ones.

Eviction priority (lowest first): Reasoner → Specialist → Micro.
Nano is never evicted — it must always remain hot in memory.

When a model cannot fit in VRAM even after eviction, the Budgeter computes a
mixed CPU/GPU offload strategy rather than refusing the load outright.  This
allows larger models to run on constrained hardware by splitting layers between
GPU and system RAM, matching the behaviour of llama.cpp-backed runtimes such as
LM Studio.

Public API
----------
VRAMBudgeter(...)
    .request_load(model)   -> LoadStrategy   check budget; evict if needed; return load plan
    .current_allocation()  -> dict           loaded model → estimated VRAM bytes
    .available()           -> float          remaining VRAM bytes
    .start()                                 start background polling thread (idempotent)
    .stop()                                  signal thread to exit and join
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

import httpx

from bossbox.vram.exceptions import VRAMBudgetError, VRAMDetectionError  # noqa: F401

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class LoadStrategy:
    """
    Computed load plan returned by :meth:`VRAMBudgeter.request_load`.

    Attributes
    ----------
    model:
        Bare Ollama model name (provider prefix stripped).
    num_gpu:
        Number of model layers to place on GPU.
        ``-1`` means load all layers on GPU (Ollama default behaviour).
        ``0``  means CPU-only inference.
        ``N``  means split: N layers on GPU, remainder on CPU (mixed offload).
    mode:
        Human-readable strategy label: ``"gpu"``, ``"cpu"``, or ``"mixed"``.
    """
    model: str
    num_gpu: int
    mode: str   # "gpu" | "cpu" | "mixed"


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

# Layer count estimates keyed by bare model name.
# Used by the mixed offload calculator.  For unknown models, Ollama's
# /api/show endpoint is queried; this table avoids that round-trip for
# commonly-used models and allows tests to run without a live Ollama instance.
MODEL_LAYER_ESTIMATES: dict[str, int] = {
    "smollm:360m":          24,
    "smollm:1.7b":          24,
    "qwen2.5-coder:1.5b":   28,
    "deepseek-r1:7b":       32,
}

# Fallback layer count for completely unknown model architectures.
_DEFAULT_LAYER_COUNT: int = 32

# Default fallback when no size estimate is available (half the default budget).
_UNKNOWN_MODEL_ESTIMATE: float = DEFAULT_VRAM_BYTES * 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_vram_bytes() -> float:
    """
    Detect currently free VRAM on the primary GPU.

    Uses free VRAM (not total) so the budget reflects what is actually
    available after display drivers, kernel buffers, and other GPU consumers.
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
        detected = float(info.free)
        log.info(
            "VRAM: total=%.0f MiB, free=%.0f MiB; budget set to free.",
            info.total / 1024**2, info.free / 1024**2,
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
        # bare model name → layer count (populated on first request_load for unknown models)
        self._layer_cache: dict[str, int] = {}

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        if auto_start:
            self.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_load(self, model: str) -> LoadStrategy:
        """
        Request that *model* be loaded and return a :class:`LoadStrategy`.

        Checks whether loading would exceed the VRAM budget.  If so, evicts
        the lowest-priority loaded model(s) in EVICTION_ORDER until there is
        sufficient room.  Nano is never a candidate for eviction.

        When the model still does not fit after all eligible evictions, a mixed
        CPU/GPU offload strategy is computed: as many layers as possible are
        placed on GPU, the remainder on CPU.  This mirrors the behaviour of
        llama.cpp-backed runtimes (e.g. LM Studio) on constrained hardware.

        Returns
        -------
        LoadStrategy
            ``mode="gpu"``   — all layers on GPU (num_gpu=-1, Ollama default).
            ``mode="mixed"`` — partial GPU offload (num_gpu=N layers on GPU).
            ``mode="cpu"``   — no GPU layers (num_gpu=0, full CPU inference).
        """
        bare = strip_provider(model)
        size = self._size_for(bare)
        avail_after_eviction: float = 0.0

        with self._lock:
            # Already loaded — no additional VRAM needed.
            if bare in self._loaded:
                return LoadStrategy(model=bare, num_gpu=-1, mode="gpu")
            if self._fits(size):
                return LoadStrategy(model=bare, num_gpu=-1, mode="gpu")

            for tier in EVICTION_ORDER:
                tier_model = self._tier_to_model.get(tier)
                if (
                    tier_model
                    and tier_model in self._loaded
                    and tier_model != bare
                ):
                    self._evict(tier_model, bare)
                    if self._fits(size):
                        return LoadStrategy(model=bare, num_gpu=-1, mode="gpu")

            # Model cannot fit even after all eligible evictions.
            # Capture available VRAM before releasing the lock.
            avail_after_eviction = self._budget - sum(self._loaded.values())

        # Lock released — safe to call _fetch_layer_count (may make HTTP request
        # for unknown models; uses estimate table for known ones).
        return self._compute_offload_strategy(bare, size, avail_after_eviction)

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

    def _compute_offload_strategy(
        self, model: str, size_bytes: float, avail_bytes: float
    ) -> LoadStrategy:
        """
        Compute a mixed or CPU-only strategy when *model* does not fit in VRAM.

        Calculates how many layers can be placed on GPU given *avail_bytes*,
        and returns the appropriate :class:`LoadStrategy`.
        """
        total_layers = self._fetch_layer_count(model)

        if avail_bytes <= 0 or size_bytes <= 0 or total_layers <= 0:
            return LoadStrategy(model=model, num_gpu=0, mode="cpu")

        bytes_per_layer = size_bytes / total_layers
        gpu_layers = int(avail_bytes / bytes_per_layer)
        gpu_layers = max(0, min(gpu_layers, total_layers))

        if gpu_layers == 0:
            return LoadStrategy(model=model, num_gpu=0, mode="cpu")

        log.info(
            "Mixed offload for %s: %d/%d layers on GPU (%.0f MiB available).",
            model, gpu_layers, total_layers, avail_bytes / 1024**2,
        )
        return LoadStrategy(model=model, num_gpu=gpu_layers, mode="mixed")

    def _fetch_layer_count(self, model: str) -> int:
        """
        Return the number of transformer layers in *model*.

        Resolution order:
        1. In-memory cache (populated on first call per model).
        2. ``MODEL_LAYER_ESTIMATES`` table (covers standard models; no HTTP needed).
        3. Ollama ``/api/show`` endpoint (for unknown models only).
        4. ``_DEFAULT_LAYER_COUNT`` if all else fails.
        """
        if model in self._layer_cache:
            return self._layer_cache[model]

        # Fast path: known model in estimate table.
        if model in MODEL_LAYER_ESTIMATES:
            count = MODEL_LAYER_ESTIMATES[model]
            self._layer_cache[model] = count
            return count

        # Unknown model: query Ollama for architecture details.
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    f"{self._base_url}/api/show", json={"name": model}
                )
                resp.raise_for_status()
                data = resp.json()
            modelinfo: dict = data.get("modelinfo", {})
            for key, value in modelinfo.items():
                if key.endswith("block_count") and isinstance(value, int):
                    self._layer_cache[model] = value
                    return value
        except Exception as exc:
            log.debug("Could not fetch layer count for %s: %s", model, exc)

        count = _DEFAULT_LAYER_COUNT
        self._layer_cache[model] = count
        return count

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

        # Recalibrate budget from pynvml free VRAM so available() tracks
        # reality (display drivers etc. consume VRAM outside our accounting).
        # Formula: budget = info.free + sum(loaded)  →  available() = info.free
        try:
            import pynvml

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            pynvml.nvmlShutdown()
            recalibrated = float(mem.free) + sum(fresh.values())
            with self._lock:
                self._loaded = fresh
                self._budget = recalibrated
            log.debug(
                "VRAM poll: free=%.0f MiB, loaded=%s; budget recalibrated to %.0f MiB.",
                mem.free / 1024**2,
                {k: f"{v/1024**2:.0f}MiB" for k, v in fresh.items()},
                recalibrated / 1024**2,
            )
        except Exception:
            with self._lock:
                self._loaded = fresh
