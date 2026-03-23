"""
tests/providers/test_base.py

Tests for bossbox.providers.base — the abstract ModelProvider contract
and exception hierarchy.  No network calls; no Ollama required.
"""

from __future__ import annotations

import pytest

from bossbox.providers.base import (
    ModelNotFoundError,
    ModelProvider,
    ProviderError,
    ProviderUnavailableError,
)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_provider_unavailable_is_provider_error(self):
        exc = ProviderUnavailableError("down")
        assert isinstance(exc, ProviderError)
        assert isinstance(exc, Exception)

    def test_model_not_found_is_provider_error(self):
        exc = ModelNotFoundError(model="foo", provider="bar")
        assert isinstance(exc, ProviderError)
        assert isinstance(exc, Exception)

    def test_model_not_found_stores_model_and_provider(self):
        exc = ModelNotFoundError(model="llama3", provider="ollama")
        assert exc.model == "llama3"
        assert exc.provider == "ollama"

    def test_model_not_found_message_contains_model_name(self):
        exc = ModelNotFoundError(model="llama3", provider="ollama")
        assert "llama3" in str(exc)

    def test_model_not_found_message_contains_provider_name(self):
        exc = ModelNotFoundError(model="llama3", provider="ollama")
        assert "ollama" in str(exc)

    def test_model_not_found_includes_detail_when_provided(self):
        exc = ModelNotFoundError(model="x", provider="y", detail="try pulling it first")
        assert "try pulling it first" in str(exc)

    def test_model_not_found_omits_detail_section_when_empty(self):
        exc = ModelNotFoundError(model="x", provider="y")
        # Just ensure no crash and basic format holds
        assert "x" in str(exc)

    def test_provider_unavailable_preserves_message(self):
        exc = ProviderUnavailableError("service is down")
        assert "service is down" in str(exc)


# ---------------------------------------------------------------------------
# Abstract base enforcement
# ---------------------------------------------------------------------------


class TestModelProviderAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            ModelProvider()  # type: ignore[abstract]

    def test_concrete_without_complete_raises(self):
        """Subclass that only implements is_available cannot be instantiated."""

        class IncompleteProvider(ModelProvider):
            async def is_available(self) -> bool:
                return True

        with pytest.raises(TypeError):
            IncompleteProvider()  # type: ignore[abstract]

    def test_concrete_without_is_available_raises(self):
        """Subclass that only implements complete cannot be instantiated."""

        class IncompleteProvider(ModelProvider):
            async def complete(self, messages, **kwargs):
                return "hi"

        with pytest.raises(TypeError):
            IncompleteProvider()  # type: ignore[abstract]

    def test_fully_concrete_subclass_instantiates(self):
        class MinimalProvider(ModelProvider):
            provider_name = "minimal"

            async def complete(self, messages, **kwargs):
                return "ok"

            async def is_available(self) -> bool:
                return True

        p = MinimalProvider()
        assert p.provider_name == "minimal"

    def test_provider_name_default_on_base(self):
        """provider_name class attribute exists on the base class."""
        assert hasattr(ModelProvider, "provider_name")
        assert ModelProvider.provider_name == "unknown"
