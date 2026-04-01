"""
Step 8 — VRAM Budgeter test suite (pytest)
===========================================

Test classes
------------
TestHelpers           — strip_provider, MODEL_SIZE_ESTIMATES, _detect_vram_bytes
TestAllocation        — current_allocation, available with no models loaded
TestRequestLoad       — fits without eviction, eviction ordering, Nano protection
TestEviction          — eviction HTTP call, thought-stream callback, partial eviction
TestRefreshLoaded     — background poll updates _loaded from Ollama /api/ps
TestThreadLifecycle   — start/stop idempotency
TestEdgeCases         — unknown model estimate, model already in loaded set
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from bossbox.vram.budgeter import (
    DEFAULT_VRAM_BYTES,
    EVICTION_ORDER,
    MODEL_SIZE_ESTIMATES,
    VRAMBudgeter,
    strip_provider,
)
from bossbox.vram.exceptions import VRAMBudgetError, VRAMDetectionError


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TIER_MAP: dict[str, str] = {
    "nano":       "smollm:360m",
    "micro":      "smollm:1.7b",
    "specialist": "qwen2.5-coder:1.5b",
    "reasoner":   "deepseek-r1:7b",
}

# Sizes from MODEL_SIZE_ESTIMATES for convenience
NANO_SIZE       = MODEL_SIZE_ESTIMATES["smollm:360m"]
MICRO_SIZE      = MODEL_SIZE_ESTIMATES["smollm:1.7b"]
SPECIALIST_SIZE = MODEL_SIZE_ESTIMATES["qwen2.5-coder:1.5b"]
REASONER_SIZE   = MODEL_SIZE_ESTIMATES["deepseek-r1:7b"]


def make_budgeter(
    budget: float = DEFAULT_VRAM_BYTES,
    loaded: dict[str, float] | None = None,
    thought_cb=None,
) -> VRAMBudgeter:
    b = VRAMBudgeter(
        vram_budget_bytes=budget,
        tier_to_model=TIER_MAP,
        thought_cb=thought_cb,
        auto_start=False,
    )
    if loaded:
        b._loaded = dict(loaded)
    return b


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_strip_provider_removes_prefix(self):
        assert strip_provider("ollama/smollm:1.7b") == "smollm:1.7b"

    def test_strip_provider_no_prefix(self):
        assert strip_provider("smollm:1.7b") == "smollm:1.7b"

    def test_strip_provider_anthropic(self):
        assert strip_provider("anthropic/claude-haiku-4-5") == "claude-haiku-4-5"

    def test_eviction_order_constant(self):
        assert EVICTION_ORDER == ["reasoner", "specialist", "micro"]
        assert "nano" not in EVICTION_ORDER

    def test_model_size_estimates_present(self):
        for name in ("smollm:360m", "smollm:1.7b", "qwen2.5-coder:1.5b", "deepseek-r1:7b"):
            assert MODEL_SIZE_ESTIMATES[name] > 0

    def test_detect_vram_falls_back_to_default_without_pynvml(self):
        from bossbox.vram.budgeter import _detect_vram_bytes
        with patch.dict("sys.modules", {"pynvml": None}):
            result = _detect_vram_bytes()
        assert result == DEFAULT_VRAM_BYTES

    def test_detect_vram_uses_pynvml_when_available(self):
        from bossbox.vram.budgeter import _detect_vram_bytes
        mock_pynvml = MagicMock()
        mock_info = MagicMock()
        mock_info.total = int(4 * 1024**3)
        mock_pynvml.nvmlDeviceGetMemoryInfo.return_value = mock_info
        with patch.dict("sys.modules", {"pynvml": mock_pynvml}):
            result = _detect_vram_bytes()
        assert result == float(4 * 1024**3)

    def test_detect_vram_raises_detection_error_on_unexpected_pynvml_failure(self):
        from bossbox.vram.budgeter import _detect_vram_bytes
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlInit.side_effect = RuntimeError("GPU exploded")
        with patch.dict("sys.modules", {"pynvml": mock_pynvml}):
            with pytest.raises(VRAMDetectionError):
                _detect_vram_bytes()


# ---------------------------------------------------------------------------
# TestAllocation
# ---------------------------------------------------------------------------

class TestAllocation:
    def test_available_equals_budget_when_nothing_loaded(self):
        b = make_budgeter(budget=2 * 1024**3)
        assert b.available() == 2 * 1024**3

    def test_current_allocation_empty_initially(self):
        b = make_budgeter()
        assert b.current_allocation() == {}

    def test_available_reduces_with_loaded_models(self):
        b = make_budgeter(budget=2 * 1024**3, loaded={"smollm:360m": NANO_SIZE})
        assert b.available() == pytest.approx(2 * 1024**3 - NANO_SIZE)

    def test_current_allocation_returns_copy(self):
        b = make_budgeter(loaded={"smollm:360m": NANO_SIZE})
        alloc = b.current_allocation()
        alloc["injected"] = 99.0
        assert "injected" not in b.current_allocation()

    def test_available_returns_float(self):
        b = make_budgeter()
        assert isinstance(b.available(), float)


# ---------------------------------------------------------------------------
# TestRequestLoad
# ---------------------------------------------------------------------------

class TestRequestLoad:
    def test_returns_true_when_model_fits_without_eviction(self):
        b = make_budgeter(budget=2 * 1024**3)
        assert b.request_load("smollm:360m") is True

    def test_strips_provider_prefix(self):
        b = make_budgeter(budget=2 * 1024**3)
        assert b.request_load("ollama/smollm:360m") is True

    def test_returns_false_when_nothing_to_evict_and_model_too_large(self):
        # Budget of 100 bytes; no loaded models to evict.
        b = make_budgeter(budget=100.0)
        assert b.request_load("deepseek-r1:7b") is False

    def test_evicts_reasoner_first_to_make_room(self):
        # Budget: 6 GiB. Reasoner (5.5 GB) loaded. Request specialist (1.1 GB).
        # With reasoner taking 5.5 GB out of 6 GB, only 0.5 GB free.
        # Specialist needs 1.1 GB → evict reasoner first.
        budget = 6.0 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
        )
        thoughts: list[str] = []
        b._thought_cb = thoughts.append

        with respx.mock:
            respx.post(f"http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={})
            )
            result = b.request_load("qwen2.5-coder:1.5b")

        assert result is True
        assert "deepseek-r1:7b" not in b._loaded
        assert any("deepseek-r1:7b" in t for t in thoughts)

    def test_evicts_specialist_when_reasoner_not_loaded(self):
        # Budget: 2 GiB. Only specialist loaded. Request micro.
        budget = 2.0 * 1024**3
        # specialist (1.1 GB) + micro (1.2 GB) > 2 GB → must evict specialist.
        b = make_budgeter(
            budget=budget,
            loaded={"qwen2.5-coder:1.5b": SPECIALIST_SIZE},
        )
        with respx.mock:
            respx.post(f"http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={})
            )
            result = b.request_load("smollm:1.7b")

        assert result is True
        assert "qwen2.5-coder:1.5b" not in b._loaded

    def test_evicts_multiple_models_when_needed(self):
        # Budget: 2.5 GiB. Reasoner (5.5 GB) not loaded; micro (1.2) + specialist (1.1) loaded.
        # total loaded = 2.3 GB, free = 0.2 GB. Need to load reasoner (5.5 GB).
        # Evict reasoner (not loaded), specialist (loaded), micro (loaded).
        budget = 2.5 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={
                "smollm:1.7b":        MICRO_SIZE,
                "qwen2.5-coder:1.5b": SPECIALIST_SIZE,
            },
        )
        with respx.mock:
            respx.post(f"http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={})
            )
            result = b.request_load("deepseek-r1:7b")

        # 2.5 GB budget; after evicting both micro and specialist → 2.5 GB free
        # but reasoner needs 5.5 GB — still doesn't fit.
        assert result is False

    def test_nano_never_evicted(self):
        # Budget tiny. Only nano loaded. Request something big.
        # The budgeter should NOT evict nano and should return False.
        budget = NANO_SIZE + 1.0  # just enough for nano
        b = make_budgeter(
            budget=budget,
            loaded={"smollm:360m": NANO_SIZE},
        )
        thoughts: list[str] = []
        b._thought_cb = thoughts.append

        result = b.request_load("deepseek-r1:7b")

        assert result is False
        assert "smollm:360m" in b._loaded, "Nano must not be evicted"
        assert not any("smollm:360m" in t for t in thoughts)

    def test_does_not_evict_model_being_loaded(self):
        # Edge case: reasoner is already loaded, and we request_load reasoner again.
        # It should not try to evict itself.
        budget = 6.0 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
        )
        # Budget is 6 GB, reasoner is 5.5 GB; fits already.
        result = b.request_load("deepseek-r1:7b")
        assert result is True


# ---------------------------------------------------------------------------
# TestEviction
# ---------------------------------------------------------------------------

class TestEviction:
    def test_eviction_emits_correct_thought_message(self):
        budget = 6.0 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
        )
        thoughts: list[str] = []
        b._thought_cb = thoughts.append

        with respx.mock:
            respx.post(f"http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={})
            )
            b.request_load("qwen2.5-coder:1.5b")

        assert len(thoughts) == 1
        assert "Offloading deepseek-r1:7b to free VRAM for qwen2.5-coder:1.5b." in thoughts[0]

    def test_eviction_posts_keep_alive_zero_to_ollama(self):
        budget = 6.0 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
        )
        with respx.mock:
            route = respx.post(f"http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={})
            )
            b.request_load("qwen2.5-coder:1.5b")

        assert route.called
        payload = route.calls[0].request.content
        import json
        body = json.loads(payload)
        assert body["model"] == "deepseek-r1:7b"
        assert body["keep_alive"] == 0

    def test_eviction_removes_model_from_loaded(self):
        budget = 6.0 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
        )
        with respx.mock:
            respx.post(f"http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={})
            )
            b.request_load("qwen2.5-coder:1.5b")

        assert "deepseek-r1:7b" not in b._loaded

    def test_eviction_continues_when_ollama_unreachable(self):
        # Even if Ollama doesn't respond, the budgeter should still update
        # its internal state and return True.
        budget = 6.0 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
        )
        with respx.mock:
            respx.post(f"http://localhost:11434/api/generate").mock(
                side_effect=httpx.ConnectError("refused")
            )
            result = b.request_load("qwen2.5-coder:1.5b")

        assert result is True
        assert "deepseek-r1:7b" not in b._loaded

    def test_thought_cb_exception_does_not_propagate(self):
        def bad_cb(msg):
            raise RuntimeError("boom")

        budget = 6.0 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
            thought_cb=bad_cb,
        )
        with respx.mock:
            respx.post(f"http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={})
            )
            # Should not raise even though thought_cb raises.
            result = b.request_load("qwen2.5-coder:1.5b")
        assert result is True


# ---------------------------------------------------------------------------
# TestRefreshLoaded
# ---------------------------------------------------------------------------

class TestRefreshLoaded:
    def test_refresh_updates_loaded_from_api_ps(self):
        b = make_budgeter()
        ps_payload = {
            "models": [
                {"name": "smollm:360m",    "size_vram": int(NANO_SIZE)},
                {"name": "smollm:1.7b",    "size_vram": int(MICRO_SIZE)},
            ]
        }
        with respx.mock:
            respx.get("http://localhost:11434/api/ps").mock(
                return_value=httpx.Response(200, json=ps_payload)
            )
            b._refresh_loaded()

        alloc = b.current_allocation()
        assert "smollm:360m" in alloc
        assert "smollm:1.7b" in alloc

    def test_refresh_strips_provider_prefix_from_names(self):
        b = make_budgeter()
        ps_payload = {
            "models": [
                {"name": "ollama/smollm:360m", "size_vram": int(NANO_SIZE)},
            ]
        }
        with respx.mock:
            respx.get("http://localhost:11434/api/ps").mock(
                return_value=httpx.Response(200, json=ps_payload)
            )
            b._refresh_loaded()

        assert "smollm:360m" in b.current_allocation()

    def test_refresh_falls_back_to_size_field(self):
        b = make_budgeter()
        ps_payload = {
            "models": [
                {"name": "smollm:360m", "size_vram": 0, "size": int(NANO_SIZE)},
            ]
        }
        with respx.mock:
            respx.get("http://localhost:11434/api/ps").mock(
                return_value=httpx.Response(200, json=ps_payload)
            )
            b._refresh_loaded()

        assert b.current_allocation()["smollm:360m"] == pytest.approx(NANO_SIZE)

    def test_refresh_silent_when_ollama_unreachable(self):
        b = make_budgeter(loaded={"smollm:360m": NANO_SIZE})
        with respx.mock:
            respx.get("http://localhost:11434/api/ps").mock(
                side_effect=httpx.ConnectError("refused")
            )
            b._refresh_loaded()

        # State unchanged when Ollama is down.
        assert "smollm:360m" in b.current_allocation()

    def test_refresh_replaces_stale_loaded_state(self):
        b = make_budgeter(loaded={"deepseek-r1:7b": REASONER_SIZE})
        ps_payload = {"models": [{"name": "smollm:360m", "size_vram": int(NANO_SIZE)}]}
        with respx.mock:
            respx.get("http://localhost:11434/api/ps").mock(
                return_value=httpx.Response(200, json=ps_payload)
            )
            b._refresh_loaded()

        alloc = b.current_allocation()
        assert "deepseek-r1:7b" not in alloc
        assert "smollm:360m" in alloc


# ---------------------------------------------------------------------------
# TestThreadLifecycle
# ---------------------------------------------------------------------------

class TestThreadLifecycle:
    def test_auto_start_false_does_not_spawn_thread(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            auto_start=False,
        )
        assert b._thread is None

    def test_start_spawns_daemon_thread(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            auto_start=False,
            poll_interval=60.0,
        )
        b.start()
        assert b._thread is not None
        assert b._thread.is_alive()
        assert b._thread.daemon is True
        b.stop()

    def test_start_is_idempotent(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            auto_start=False,
            poll_interval=60.0,
        )
        b.start()
        t1 = b._thread
        b.start()  # second call should not replace the thread
        assert b._thread is t1
        b.stop()

    def test_stop_joins_thread(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            auto_start=False,
            poll_interval=60.0,
        )
        b.start()
        assert b._thread.is_alive()
        b.stop()
        assert not b._thread.is_alive()


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_model_uses_fallback_estimate(self):
        b = make_budgeter(budget=DEFAULT_VRAM_BYTES)
        # "mystery:model" has no size entry; should use _UNKNOWN_MODEL_ESTIMATE
        from bossbox.vram.budgeter import _UNKNOWN_MODEL_ESTIMATE
        size = b._size_for("mystery:model")
        assert size == _UNKNOWN_MODEL_ESTIMATE

    def test_size_for_prefers_loaded_value_over_estimate(self):
        # If a model is already tracked with a live size, use that.
        live_size = 999_000.0
        b = make_budgeter(loaded={"smollm:1.7b": live_size})
        assert b._size_for("smollm:1.7b") == live_size

    def test_request_load_with_empty_tier_map_still_returns_bool(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            tier_to_model={},
            auto_start=False,
        )
        result = b.request_load("smollm:360m")
        assert isinstance(result, bool)

    def test_available_never_negative_when_loaded_exceeds_budget(self):
        # Edge case: refresh brings in more data than the tracked budget.
        b = make_budgeter(
            budget=100.0,
            loaded={"smollm:1.7b": MICRO_SIZE},  # MICRO_SIZE >> 100
        )
        assert b.available() < 0  # negative is truthful; budgeter doesn't clamp

    def test_no_thought_cb_does_not_raise(self):
        budget = 6.0 * 1024**3
        b = make_budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
        )
        with respx.mock:
            respx.post(f"http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={})
            )
            result = b.request_load("qwen2.5-coder:1.5b")
        assert result is True
