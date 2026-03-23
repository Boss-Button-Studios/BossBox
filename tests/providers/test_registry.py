"""Tests for bossbox.providers.registry.

These tests exercise the registry in full isolation — no real Ollama, no real
API keys.  Provider constructors are patched so the tests verify wiring
logic, not provider-level networking.

Fixtures
--------
``mock_ollama_provider``   — MagicMock standing in for a live OllamaProvider instance
``mock_anthropic_provider`` — MagicMock standing in for AnthropicProvider
``mock_openai_provider``   — MagicMock standing in for OpenAIProvider

Patch strategy
--------------
OllamaProvider is imported inside _register_ollama() via a local import, so
it must be patched at its definition site: bossbox.providers.ollama.OllamaProvider.
AnthropicProvider and OpenAIProvider are similarly patched at their definition
sites.  See _patch_ollama / _patch_anthropic / _patch_openai helpers below.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bossbox.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    ollama_url: str | None = "http://localhost:11434",
    anthropic_key: str | None = None,
    anthropic_model: str | None = "claude-haiku-4-5-20251001",
    openai_key: str | None = None,
    openai_model: str | None = "gpt-4o-mini",
) -> SimpleNamespace:
    """Build a minimal config namespace that mimics the Step 2 dataclass."""
    cfg = SimpleNamespace()

    if ollama_url is not None:
        cfg.ollama = SimpleNamespace(base_url=ollama_url)
    else:
        cfg.ollama = None

    cfg.anthropic = SimpleNamespace(
        api_key=anthropic_key,
        default_model=anthropic_model,
    )
    cfg.openai = SimpleNamespace(
        api_key=openai_key,
        default_model=openai_model,
    )
    return cfg


def _make_ollama_only_config() -> SimpleNamespace:
    return _make_config()  # cloud keys are None by default


def _make_full_config() -> SimpleNamespace:
    return _make_config(
        anthropic_key="sk-ant-test-key",
        openai_key="sk-openai-test-key",
    )


def _make_no_ollama_config() -> SimpleNamespace:
    cfg = _make_config(
        anthropic_key="sk-ant-test-key",
        openai_key="sk-openai-test-key",
    )
    cfg.ollama = None
    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_ollama_provider():
    """A MagicMock standing in for a live OllamaProvider instance."""
    return MagicMock(name="OllamaProvider")


@pytest.fixture()
def mock_anthropic_provider():
    return MagicMock(name="AnthropicProvider")


@pytest.fixture()
def mock_openai_provider():
    return MagicMock(name="OpenAIProvider")


# ---------------------------------------------------------------------------
# Acceptance criterion: Ollama-only config
# ---------------------------------------------------------------------------


class TestOllamaOnly:
    """Ollama-only config returns Ollama provider and None for cloud."""

    def test_ollama_is_available(self, mock_ollama_provider):
        # OllamaProvider is imported inside _register_ollama() so we patch
        # at the definition site, not at the registry module level.
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry.get("ollama") is not None

    def test_anthropic_is_none_when_key_missing(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry.get("anthropic") is None

    def test_openai_is_none_when_key_missing(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry.get("openai") is None

    def test_no_exception_raised_for_missing_cloud_keys(self, mock_ollama_provider):
        """The acceptance criterion: no exception for missing cloud keys."""
        with _patch_ollama(mock_ollama_provider):
            # Must not raise.
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry is not None

    def test_available_list_contains_only_ollama(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry.available() == ["ollama"]

    def test_unavailable_list_contains_cloud_providers(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert "anthropic" in registry.unavailable()
        assert "openai" in registry.unavailable()


# ---------------------------------------------------------------------------
# Full config (all three providers)
# ---------------------------------------------------------------------------


class TestFullConfig:
    def test_all_three_providers_available(
        self,
        mock_ollama_provider,
        mock_anthropic_provider,
        mock_openai_provider,
    ):
        with (
            _patch_ollama(mock_ollama_provider),
            _patch_anthropic(mock_anthropic_provider),
            _patch_openai(mock_openai_provider),
        ):
            registry = ProviderRegistry.from_config(_make_full_config())

        assert registry.get("ollama") is not None
        assert registry.get("anthropic") is not None
        assert registry.get("openai") is not None

    def test_available_returns_all_three(
        self,
        mock_ollama_provider,
        mock_anthropic_provider,
        mock_openai_provider,
    ):
        with (
            _patch_ollama(mock_ollama_provider),
            _patch_anthropic(mock_anthropic_provider),
            _patch_openai(mock_openai_provider),
        ):
            registry = ProviderRegistry.from_config(_make_full_config())

        assert set(registry.available()) == {"ollama", "anthropic", "openai"}

    def test_unavailable_is_empty(
        self,
        mock_ollama_provider,
        mock_anthropic_provider,
        mock_openai_provider,
    ):
        with (
            _patch_ollama(mock_ollama_provider),
            _patch_anthropic(mock_anthropic_provider),
            _patch_openai(mock_openai_provider),
        ):
            registry = ProviderRegistry.from_config(_make_full_config())

        assert registry.unavailable() == []


# ---------------------------------------------------------------------------
# Missing Ollama section
# ---------------------------------------------------------------------------


class TestNoOllamaSection:
    def test_ollama_none_when_section_absent(
        self,
        mock_anthropic_provider,
        mock_openai_provider,
    ):
        with (
            _patch_anthropic(mock_anthropic_provider),
            _patch_openai(mock_openai_provider),
        ):
            registry = ProviderRegistry.from_config(_make_no_ollama_config())

        assert registry.get("ollama") is None


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


class TestResolve:
    def test_resolve_ollama_model_string(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        result = registry.resolve("ollama/deepseek-r1:7b")
        assert result is not None
        provider, model = result
        assert provider is mock_ollama_provider
        assert model == "deepseek-r1:7b"

    def test_resolve_unavailable_provider_returns_none(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry.resolve("anthropic/claude-haiku-4-5-20251001") is None

    def test_resolve_string_without_slash_returns_none(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry.resolve("deepseek-r1:7b") is None

    def test_resolve_unknown_provider_name_returns_none(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry.resolve("mistral/mistral-7b") is None

    def test_resolve_anthropic_when_available(
        self, mock_ollama_provider, mock_anthropic_provider
    ):
        with (
            _patch_ollama(mock_ollama_provider),
            _patch_anthropic(mock_anthropic_provider),
        ):
            registry = ProviderRegistry.from_config(_make_full_config())

        result = registry.resolve("anthropic/claude-haiku-4-5-20251001")
        assert result is not None
        provider, model = result
        assert provider is mock_anthropic_provider
        assert model == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# resolve_with_fallback()
# ---------------------------------------------------------------------------


class TestResolveWithFallback:
    def test_primary_used_when_available(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        result = registry.resolve_with_fallback(
            primary="ollama/deepseek-r1:7b",
            fallbacks=["anthropic/claude-haiku-4-5-20251001"],
        )
        assert result is not None
        provider, model = result
        assert provider is mock_ollama_provider
        assert model == "deepseek-r1:7b"

    def test_falls_back_when_primary_unavailable(
        self, mock_anthropic_provider
    ):
        # No Ollama configured — primary should fail; anthropic fallback used.
        with _patch_anthropic(mock_anthropic_provider):
            registry = ProviderRegistry.from_config(_make_no_ollama_config())

        result = registry.resolve_with_fallback(
            primary="ollama/deepseek-r1:7b",
            fallbacks=["anthropic/claude-haiku-4-5-20251001"],
        )
        assert result is not None
        provider, model = result
        assert provider is mock_anthropic_provider

    def test_returns_none_when_entire_chain_unavailable(self, mock_ollama_provider):
        # Cloud providers not configured; fallbacks all point to cloud.
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        result = registry.resolve_with_fallback(
            primary="anthropic/claude-haiku-4-5-20251001",
            fallbacks=["openai/gpt-4o-mini"],
        )
        assert result is None

    def test_second_fallback_used_when_first_unavailable(
        self, mock_openai_provider
    ):
        # Ollama absent, Anthropic absent, OpenAI present.
        cfg = SimpleNamespace(
            ollama=None,
            anthropic=SimpleNamespace(api_key=None, default_model=None),
            openai=SimpleNamespace(api_key="sk-openai", default_model="gpt-4o-mini"),
        )
        with _patch_openai(mock_openai_provider):
            registry = ProviderRegistry.from_config(cfg)

        result = registry.resolve_with_fallback(
            primary="ollama/deepseek-r1:7b",
            fallbacks=["anthropic/claude-haiku-4-5-20251001", "openai/gpt-4o-mini"],
        )
        assert result is not None
        provider, model = result
        assert provider is mock_openai_provider
        assert model == "gpt-4o-mini"

    def test_empty_fallbacks_list(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        result = registry.resolve_with_fallback(
            primary="ollama/smollm:360m",
            fallbacks=[],
        )
        assert result is not None
        _, model = result
        assert model == "smollm:360m"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_api_key_string_registers_as_none(self, mock_ollama_provider):
        cfg = _make_config(anthropic_key="", openai_key="")
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(cfg)

        assert registry.get("anthropic") is None
        assert registry.get("openai") is None

    def test_whitespace_only_api_key_registers_as_none(self, mock_ollama_provider):
        # Whitespace key — registry catches the ProviderUnavailableError and
        # registers None.  Assert no exception is raised regardless.
        cfg = _make_config(anthropic_key="   ", openai_key="   ")
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(cfg)

        assert registry is not None

    def test_repr_does_not_raise(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        rep = repr(registry)
        assert "ollama" in rep

    def test_get_unknown_key_returns_none(self, mock_ollama_provider):
        with _patch_ollama(mock_ollama_provider):
            registry = ProviderRegistry.from_config(_make_ollama_only_config())

        assert registry.get("nonexistent_provider") is None


# ---------------------------------------------------------------------------
# Patch helpers (DRY)
# ---------------------------------------------------------------------------


def _patch_ollama(mock_instance):
    """Patch OllamaProvider at its definition site.

    The registry imports OllamaProvider inside _register_ollama() so patching
    bossbox.providers.registry.OllamaProvider would fail — the name doesn't
    exist at the registry module level.  Patching the definition site
    (bossbox.providers.ollama.OllamaProvider) intercepts the local import.
    """
    return patch(
        "bossbox.providers.ollama.OllamaProvider",
        return_value=mock_instance,
    )


def _patch_anthropic(mock_instance):
    return patch(
        "bossbox.providers.anthropic.AnthropicProvider",
        return_value=mock_instance,
    )


def _patch_openai(mock_instance):
    return patch(
        "bossbox.providers.openai.OpenAIProvider",
        return_value=mock_instance,
    )
