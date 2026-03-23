"""Tests for bossbox.providers.anthropic.

The anthropic SDK is mocked throughout — no real network calls are made.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bossbox.providers.base import ProviderUnavailableError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_api_response(text: str) -> MagicMock:
    response = MagicMock()
    response.content = [_make_text_block(text)]
    return response


# ---------------------------------------------------------------------------
# SDK availability
# ---------------------------------------------------------------------------


class TestSDKAvailability:
    def test_raises_when_sdk_not_installed(self, monkeypatch):
        """ProviderUnavailableError raised if anthropic SDK is missing."""
        # Hide the SDK
        monkeypatch.setitem(sys.modules, "anthropic", None)

        # Force module reload so the import guard re-runs.
        import bossbox.providers.anthropic as mod

        original_flag = mod._SDK_AVAILABLE
        mod._SDK_AVAILABLE = False
        try:
            with pytest.raises(ProviderUnavailableError, match="anthropic SDK"):
                mod.AnthropicProvider(api_key="sk-test")
        finally:
            mod._SDK_AVAILABLE = original_flag


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_raises_on_empty_api_key(self):
        with patch("bossbox.providers.anthropic._SDK_AVAILABLE", True):
            with patch("bossbox.providers.anthropic._anthropic"):
                from bossbox.providers.anthropic import AnthropicProvider

                with pytest.raises(ProviderUnavailableError, match="API key"):
                    AnthropicProvider(api_key="")

    def test_raises_on_none_api_key(self):
        with patch("bossbox.providers.anthropic._SDK_AVAILABLE", True):
            with patch("bossbox.providers.anthropic._anthropic"):
                from bossbox.providers.anthropic import AnthropicProvider

                with pytest.raises(ProviderUnavailableError):
                    AnthropicProvider(api_key=None)  # type: ignore[arg-type]

    def test_uses_provided_default_model(self):
        mock_sdk = MagicMock()
        with patch("bossbox.providers.anthropic._SDK_AVAILABLE", True):
            with patch("bossbox.providers.anthropic._anthropic", mock_sdk):
                from bossbox.providers.anthropic import AnthropicProvider

                p = AnthropicProvider(api_key="sk-test", default_model="claude-opus-4-6")
                assert p.default_model == "claude-opus-4-6"

    def test_falls_back_to_default_model_when_none_given(self):
        mock_sdk = MagicMock()
        with patch("bossbox.providers.anthropic._SDK_AVAILABLE", True):
            with patch("bossbox.providers.anthropic._anthropic", mock_sdk):
                from bossbox.providers.anthropic import AnthropicProvider

                p = AnthropicProvider(api_key="sk-test")
                assert p.default_model == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.fixture()
    def provider(self):
        mock_sdk = MagicMock()
        mock_client = MagicMock()
        mock_sdk.AsyncAnthropic.return_value = mock_client
        with patch("bossbox.providers.anthropic._SDK_AVAILABLE", True):
            with patch("bossbox.providers.anthropic._anthropic", mock_sdk):
                from bossbox.providers.anthropic import AnthropicProvider

                p = AnthropicProvider(api_key="sk-test")
        p._client = mock_client
        return p, mock_client

    @pytest.mark.asyncio
    async def test_returns_assistant_text(self, provider):
        p, mock_client = provider
        mock_client.messages.create = AsyncMock(
            return_value=_make_api_response("Hello from Anthropic")
        )

        result = await p.complete([{"role": "user", "content": "Hi"}])
        assert result == "Hello from Anthropic"

    @pytest.mark.asyncio
    async def test_system_message_extracted_and_sent_separately(self, provider):
        p, mock_client = provider
        mock_client.messages.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        await p.complete(messages)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "You are a helpful assistant."
        # System message should not appear in the messages array.
        for msg in call_kwargs["messages"]:
            assert msg.get("role") != "system"

    @pytest.mark.asyncio
    async def test_no_system_message_no_system_kwarg(self, provider):
        p, mock_client = provider
        mock_client.messages.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}])

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "system" not in call_kwargs

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_default(self, provider):
        p, mock_client = provider
        mock_client.messages.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}], model="claude-opus-4-6")

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_max_tokens_forwarded(self, provider):
        p, mock_client = provider
        mock_client.messages.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}], max_tokens=512)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 512

    @pytest.mark.asyncio
    async def test_temperature_forwarded_when_present(self, provider):
        p, mock_client = provider
        mock_client.messages.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}], temperature=0.2)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["temperature"] == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_temperature_absent_when_not_supplied(self, provider):
        p, mock_client = provider
        mock_client.messages.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}])

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "temperature" not in call_kwargs


# ---------------------------------------------------------------------------
# _split_system helper
# ---------------------------------------------------------------------------


class TestSplitSystem:
    def test_extracts_leading_system_message(self):
        from bossbox.providers.anthropic import _split_system

        msgs = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, filtered = _split_system(msgs)
        assert system == "Be helpful."
        assert len(filtered) == 1
        assert filtered[0]["role"] == "user"

    def test_no_system_message_returns_none(self):
        from bossbox.providers.anthropic import _split_system

        msgs = [{"role": "user", "content": "Hello"}]
        system, filtered = _split_system(msgs)
        assert system is None
        assert filtered == msgs

    def test_empty_messages_list(self):
        from bossbox.providers.anthropic import _split_system

        system, filtered = _split_system([])
        assert system is None
        assert filtered == []
