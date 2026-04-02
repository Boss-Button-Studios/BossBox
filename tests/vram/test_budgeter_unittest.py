"""
Step 8 — VRAM Budgeter test suite (stdlib unittest)
=====================================================
Identical coverage to test_budgeter.py; runnable with no network access.

    python -m unittest tests.vram.test_budgeter_unittest -v

All HTTP calls are replaced by unittest.mock so this file never touches a
real Ollama instance.
"""
from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

import httpx as _httpx

from bossbox.vram.budgeter import (
    DEFAULT_VRAM_BYTES,
    EVICTION_ORDER,
    MODEL_LAYER_ESTIMATES,
    MODEL_SIZE_ESTIMATES,
    LoadStrategy,
    VRAMBudgeter,
    _DEFAULT_LAYER_COUNT,
    _UNKNOWN_MODEL_ESTIMATE,
    _detect_vram_bytes,
    strip_provider,
)
from bossbox.vram.exceptions import VRAMDetectionError


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

TIER_MAP: dict[str, str] = {
    "nano":       "smollm:360m",
    "micro":      "smollm:1.7b",
    "specialist": "qwen2.5-coder:1.5b",
    "reasoner":   "deepseek-r1:7b",
}

NANO_SIZE       = MODEL_SIZE_ESTIMATES["smollm:360m"]
MICRO_SIZE      = MODEL_SIZE_ESTIMATES["smollm:1.7b"]
SPECIALIST_SIZE = MODEL_SIZE_ESTIMATES["qwen2.5-coder:1.5b"]
REASONER_SIZE   = MODEL_SIZE_ESTIMATES["deepseek-r1:7b"]


def _budgeter(
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


def _mock_http_response(status: int = 200, body: dict | None = None):
    """Return a mock httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):
    def test_strip_provider_removes_prefix(self):
        self.assertEqual(strip_provider("ollama/smollm:1.7b"), "smollm:1.7b")

    def test_strip_provider_no_prefix(self):
        self.assertEqual(strip_provider("smollm:1.7b"), "smollm:1.7b")

    def test_strip_provider_anthropic(self):
        self.assertEqual(strip_provider("anthropic/claude-haiku-4-5"), "claude-haiku-4-5")

    def test_eviction_order_constant(self):
        self.assertEqual(EVICTION_ORDER, ["reasoner", "specialist", "micro"])
        self.assertNotIn("nano", EVICTION_ORDER)

    def test_model_size_estimates_present(self):
        for name in ("smollm:360m", "smollm:1.7b", "qwen2.5-coder:1.5b", "deepseek-r1:7b"):
            self.assertGreater(MODEL_SIZE_ESTIMATES[name], 0)

    def test_detect_vram_falls_back_without_pynvml(self):
        with patch.dict("sys.modules", {"pynvml": None}):
            result = _detect_vram_bytes()
        self.assertEqual(result, DEFAULT_VRAM_BYTES)

    def test_detect_vram_uses_pynvml_when_available(self):
        mock_pynvml = MagicMock()
        mock_info = MagicMock()
        mock_info.total = int(4 * 1024**3)
        mock_pynvml.nvmlDeviceGetMemoryInfo.return_value = mock_info
        with patch.dict("sys.modules", {"pynvml": mock_pynvml}):
            result = _detect_vram_bytes()
        self.assertEqual(result, float(4 * 1024**3))

    def test_detect_vram_raises_on_unexpected_pynvml_error(self):
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlInit.side_effect = RuntimeError("GPU exploded")
        with patch.dict("sys.modules", {"pynvml": mock_pynvml}):
            with self.assertRaises(VRAMDetectionError):
                _detect_vram_bytes()


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

class TestAllocation(unittest.TestCase):
    def test_available_equals_budget_when_nothing_loaded(self):
        b = _budgeter(budget=2 * 1024**3)
        self.assertEqual(b.available(), 2 * 1024**3)

    def test_current_allocation_empty_initially(self):
        b = _budgeter()
        self.assertEqual(b.current_allocation(), {})

    def test_available_reduces_with_loaded_model(self):
        b = _budgeter(budget=2 * 1024**3, loaded={"smollm:360m": NANO_SIZE})
        self.assertAlmostEqual(b.available(), 2 * 1024**3 - NANO_SIZE)

    def test_current_allocation_returns_copy(self):
        b = _budgeter(loaded={"smollm:360m": NANO_SIZE})
        alloc = b.current_allocation()
        alloc["injected"] = 99.0
        self.assertNotIn("injected", b.current_allocation())

    def test_available_returns_float(self):
        self.assertIsInstance(_budgeter().available(), float)


# ---------------------------------------------------------------------------
# RequestLoad
# ---------------------------------------------------------------------------

class TestRequestLoad(unittest.TestCase):
    def test_returns_gpu_strategy_when_fits(self):
        b = _budgeter(budget=2 * 1024**3)
        result = b.request_load("smollm:360m")
        self.assertIsInstance(result, LoadStrategy)
        self.assertEqual(result.mode, "gpu")
        self.assertEqual(result.num_gpu, -1)

    def test_strips_provider_prefix(self):
        b = _budgeter(budget=2 * 1024**3)
        result = b.request_load("ollama/smollm:360m")
        self.assertEqual(result.mode, "gpu")

    def test_returns_offload_strategy_when_nothing_to_evict(self):
        b = _budgeter(budget=100.0)
        result = b.request_load("deepseek-r1:7b")
        self.assertIsInstance(result, LoadStrategy)
        self.assertIn(result.mode, ("cpu", "mixed"))

    def test_evicts_reasoner_to_make_room(self):
        budget = 6.0 * 1024**3
        b = _budgeter(budget=budget, loaded={"deepseek-r1:7b": REASONER_SIZE})

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_http_response()

        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mock_client):
            result = b.request_load("qwen2.5-coder:1.5b")

        self.assertEqual(result.mode, "gpu")
        self.assertNotIn("deepseek-r1:7b", b._loaded)

    def test_nano_never_evicted(self):
        budget = NANO_SIZE + 1.0
        b = _budgeter(budget=budget, loaded={"smollm:360m": NANO_SIZE})
        thoughts: list[str] = []
        b._thought_cb = thoughts.append

        result = b.request_load("deepseek-r1:7b")

        self.assertIn(result.mode, ("cpu", "mixed"))
        self.assertIn("smollm:360m", b._loaded)
        self.assertFalse(any("smollm:360m" in t for t in thoughts))

    def test_already_loaded_returns_gpu_strategy(self):
        budget = 6.0 * 1024**3
        b = _budgeter(budget=budget, loaded={"deepseek-r1:7b": REASONER_SIZE})
        result = b.request_load("deepseek-r1:7b")
        self.assertEqual(result.mode, "gpu")
        self.assertEqual(result.num_gpu, -1)

    def test_evicts_specialist_when_reasoner_absent(self):
        budget = 2.0 * 1024**3
        b = _budgeter(budget=budget, loaded={"qwen2.5-coder:1.5b": SPECIALIST_SIZE})

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_http_response()

        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mock_client):
            result = b.request_load("smollm:1.7b")

        self.assertEqual(result.mode, "gpu")
        self.assertNotIn("qwen2.5-coder:1.5b", b._loaded)


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------

class TestEviction(unittest.TestCase):
    def _make_mock_client(self):
        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.post.return_value = _mock_http_response()
        return mc

    def test_eviction_emits_correct_thought(self):
        budget = 6.0 * 1024**3
        thoughts: list[str] = []
        b = _budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
            thought_cb=thoughts.append,
        )
        mc = self._make_mock_client()
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            b.request_load("qwen2.5-coder:1.5b")

        self.assertEqual(len(thoughts), 1)
        self.assertIn(
            "Offloading deepseek-r1:7b to free VRAM for qwen2.5-coder:1.5b.",
            thoughts[0],
        )

    def test_eviction_posts_keep_alive_zero(self):
        budget = 6.0 * 1024**3
        b = _budgeter(budget=budget, loaded={"deepseek-r1:7b": REASONER_SIZE})
        mc = self._make_mock_client()
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            b.request_load("qwen2.5-coder:1.5b")

        mc.post.assert_called_once()
        call_kwargs = mc.post.call_args
        body = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
        self.assertEqual(body["model"], "deepseek-r1:7b")
        self.assertEqual(body["keep_alive"], 0)

    def test_eviction_removes_from_loaded(self):
        budget = 6.0 * 1024**3
        b = _budgeter(budget=budget, loaded={"deepseek-r1:7b": REASONER_SIZE})
        mc = self._make_mock_client()
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            b.request_load("qwen2.5-coder:1.5b")

        self.assertNotIn("deepseek-r1:7b", b._loaded)

    def test_thought_cb_exception_does_not_propagate(self):
        def bad_cb(msg):
            raise RuntimeError("boom")

        budget = 6.0 * 1024**3
        b = _budgeter(
            budget=budget,
            loaded={"deepseek-r1:7b": REASONER_SIZE},
            thought_cb=bad_cb,
        )
        mc = self._make_mock_client()
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            result = b.request_load("qwen2.5-coder:1.5b")
        self.assertEqual(result.mode, "gpu")


# ---------------------------------------------------------------------------
# RefreshLoaded
# ---------------------------------------------------------------------------

class TestRefreshLoaded(unittest.TestCase):
    def _mock_ps(self, models: list[dict]):
        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        resp = _mock_http_response(body={"models": models})
        mc.get.return_value = resp
        return mc

    def test_refresh_updates_loaded_state(self):
        b = _budgeter()
        mc = self._mock_ps([
            {"name": "smollm:360m",  "size_vram": int(NANO_SIZE)},
            {"name": "smollm:1.7b",  "size_vram": int(MICRO_SIZE)},
        ])
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            b._refresh_loaded()

        alloc = b.current_allocation()
        self.assertIn("smollm:360m", alloc)
        self.assertIn("smollm:1.7b", alloc)

    def test_refresh_strips_provider_prefix(self):
        b = _budgeter()
        mc = self._mock_ps([{"name": "ollama/smollm:360m", "size_vram": int(NANO_SIZE)}])
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            b._refresh_loaded()
        self.assertIn("smollm:360m", b.current_allocation())

    def test_refresh_replaces_stale_state(self):
        b = _budgeter(loaded={"deepseek-r1:7b": REASONER_SIZE})
        mc = self._mock_ps([{"name": "smollm:360m", "size_vram": int(NANO_SIZE)}])
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            b._refresh_loaded()
        alloc = b.current_allocation()
        self.assertNotIn("deepseek-r1:7b", alloc)
        self.assertIn("smollm:360m", alloc)

    def test_refresh_silent_when_ollama_unreachable(self):
        b = _budgeter(loaded={"smollm:360m": NANO_SIZE})
        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.get.side_effect = _httpx.ConnectError("refused")
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            b._refresh_loaded()
        self.assertIn("smollm:360m", b.current_allocation())


# ---------------------------------------------------------------------------
# ThreadLifecycle
# ---------------------------------------------------------------------------

class TestThreadLifecycle(unittest.TestCase):
    def test_auto_start_false_no_thread(self):
        b = VRAMBudgeter(vram_budget_bytes=2 * 1024**3, auto_start=False)
        self.assertIsNone(b._thread)

    def test_start_spawns_daemon_thread(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            auto_start=False,
            poll_interval=60.0,
        )
        b.start()
        self.assertIsNotNone(b._thread)
        self.assertTrue(b._thread.is_alive())
        self.assertTrue(b._thread.daemon)
        b.stop()

    def test_start_idempotent(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            auto_start=False,
            poll_interval=60.0,
        )
        b.start()
        t1 = b._thread
        b.start()
        self.assertIs(b._thread, t1)
        b.stop()

    def test_stop_joins_thread(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            auto_start=False,
            poll_interval=60.0,
        )
        b.start()
        b.stop()
        self.assertFalse(b._thread.is_alive())


# ---------------------------------------------------------------------------
# EdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_unknown_model_uses_fallback_estimate(self):
        b = _budgeter()
        self.assertEqual(b._size_for("mystery:model"), _UNKNOWN_MODEL_ESTIMATE)

    def test_size_for_prefers_live_loaded_value(self):
        live = 999_000.0
        b = _budgeter(loaded={"smollm:1.7b": live})
        self.assertEqual(b._size_for("smollm:1.7b"), live)

    def test_request_load_returns_load_strategy_with_empty_tier_map(self):
        b = VRAMBudgeter(
            vram_budget_bytes=2 * 1024**3,
            tier_to_model={},
            auto_start=False,
        )
        self.assertIsInstance(b.request_load("smollm:360m"), LoadStrategy)

    def test_no_thought_cb_does_not_raise(self):
        budget = 6.0 * 1024**3
        b = _budgeter(budget=budget, loaded={"deepseek-r1:7b": REASONER_SIZE})

        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.post.return_value = _mock_http_response()

        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            result = b.request_load("qwen2.5-coder:1.5b")
        self.assertEqual(result.mode, "gpu")


# ---------------------------------------------------------------------------
# LoadStrategy
# ---------------------------------------------------------------------------

class TestLoadStrategy(unittest.TestCase):
    def test_gpu_strategy(self):
        s = LoadStrategy(model="smollm:360m", num_gpu=-1, mode="gpu")
        self.assertEqual(s.mode, "gpu")
        self.assertEqual(s.num_gpu, -1)

    def test_cpu_strategy(self):
        s = LoadStrategy(model="llama3.2:3b", num_gpu=0, mode="cpu")
        self.assertEqual(s.mode, "cpu")
        self.assertEqual(s.num_gpu, 0)

    def test_mixed_strategy(self):
        s = LoadStrategy(model="llama3.2:3b", num_gpu=10, mode="mixed")
        self.assertEqual(s.mode, "mixed")
        self.assertEqual(s.num_gpu, 10)


# ---------------------------------------------------------------------------
# FetchLayerCount
# ---------------------------------------------------------------------------

class TestFetchLayerCount(unittest.TestCase):
    def test_known_model_uses_estimate(self):
        b = _budgeter()
        self.assertEqual(
            b._fetch_layer_count("smollm:360m"),
            MODEL_LAYER_ESTIMATES["smollm:360m"],
        )

    def test_result_is_cached(self):
        b = _budgeter()
        b._fetch_layer_count("smollm:1.7b")
        self.assertIn("smollm:1.7b", b._layer_cache)

    def test_unknown_model_queries_api_show(self):
        b = _budgeter()
        show_body = {"modelinfo": {"llama.block_count": 28}}
        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.post.return_value = _mock_http_response(body=show_body)
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            count = b._fetch_layer_count("llama3.2:3b")
        self.assertEqual(count, 28)

    def test_fallback_on_http_error(self):
        b = _budgeter()
        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.post.side_effect = _httpx.ConnectError("refused")
        with patch("bossbox.vram.budgeter.httpx.Client", return_value=mc):
            count = b._fetch_layer_count("mystery:7b")
        self.assertEqual(count, _DEFAULT_LAYER_COUNT)


# ---------------------------------------------------------------------------
# OffloadStrategy
# ---------------------------------------------------------------------------

class TestOffloadStrategy(unittest.TestCase):
    def test_cpu_when_no_vram(self):
        b = _budgeter(budget=100.0)
        s = b._compute_offload_strategy("deepseek-r1:7b", REASONER_SIZE, 1.0)
        self.assertEqual(s.mode, "cpu")
        self.assertEqual(s.num_gpu, 0)

    def test_mixed_when_some_vram(self):
        b = _budgeter()
        s = b._compute_offload_strategy(
            "deepseek-r1:7b", REASONER_SIZE, 2.0 * 1024**3
        )
        self.assertEqual(s.mode, "mixed")
        self.assertGreater(s.num_gpu, 0)
        self.assertLess(s.num_gpu, 32)

    def test_layer_estimates_cover_all_standard_models(self):
        for model in MODEL_SIZE_ESTIMATES:
            self.assertIn(model, MODEL_LAYER_ESTIMATES)


if __name__ == "__main__":
    unittest.main()
