"""Tests for bossbox.providers.openai.

The openai SDK is mocked throughout — no real network calls are made.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bossbox.providers.base import ProviderUnavailableError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_response(text: str) -> MagicMock:
    response = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# SDK availability
# ---------------------------------------------------------------------------


class TestSDKAvailability:
    def test_raises_when_sdk_not_installed(self, monkeypatch):
        import bossbox.providers.openai as mod

        original_flag = mod._SDK_AVAILABLE
        mod._SDK_AVAILABLE = False
        try:
            with pytest.raises(ProviderUnavailableError, match="openai SDK"):
                mod.OpenAIProvider(api_key="sk-test")
        finally:
            mod._SDK_AVAILABLE = original_flag


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_raises_on_empty_api_key(self):
        with patch("bossbox.providers.openai._SDK_AVAILABLE", True):
            with patch("bossbox.providers.openai._openai"):
                from bossbox.providers.openai import OpenAIProvider

                with pytest.raises(ProviderUnavailableError, match="API key"):
                    OpenAIProvider(api_key="")

    def test_raises_on_none_api_key(self):
        with patch("bossbox.providers.openai._SDK_AVAILABLE", True):
            with patch("bossbox.providers.openai._openai"):
                from bossbox.providers.openai import OpenAIProvider

                with pytest.raises(ProviderUnavailableError):
                    OpenAIProvider(api_key=None)  # type: ignore[arg-type]

    def test_uses_provided_default_model(self):
        mock_sdk = MagicMock()
        with patch("bossbox.providers.openai._SDK_AVAILABLE", True):
            with patch("bossbox.providers.openai._openai", mock_sdk):
                from bossbox.providers.openai import OpenAIProvider

                p = OpenAIProvider(api_key="sk-test", default_model="gpt-4o")
                assert p.default_model == "gpt-4o"

    def test_falls_back_to_default_model_when_none_given(self):
        mock_sdk = MagicMock()
        with patch("bossbox.providers.openai._SDK_AVAILABLE", True):
            with patch("bossbox.providers.openai._openai", mock_sdk):
                from bossbox.providers.openai import OpenAIProvider

                p = OpenAIProvider(api_key="sk-test")
                assert p.default_model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.fixture()
    def provider(self):
        mock_sdk = MagicMock()
        mock_client = MagicMock()
        mock_sdk.AsyncOpenAI.return_value = mock_client
        with patch("bossbox.providers.openai._SDK_AVAILABLE", True):
            with patch("bossbox.providers.openai._openai", mock_sdk):
                from bossbox.providers.openai import OpenAIProvider

                p = OpenAIProvider(api_key="sk-test")
        p._client = mock_client
        return p, mock_client

    @pytest.mark.asyncio
    async def test_returns_assistant_text(self, provider):
        p, mock_client = provider
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_api_response("Hello from OpenAI")
        )

        result = await p.complete([{"role": "user", "content": "Hi"}])
        assert result == "Hello from OpenAI"

    @pytest.mark.asyncio
    async def test_messages_passed_through_unmodified(self, provider):
        p, mock_client = provider
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        await p.complete(messages)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        # OpenAI accepts system messages inline — no extraction needed.
        assert call_kwargs["messages"] == messages

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_default(self, provider):
        p, mock_client = provider
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}], model="gpt-4o")

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_max_tokens_forwarded(self, provider):
        p, mock_client = provider
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}], max_tokens=256)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 256

    @pytest.mark.asyncio
    async def test_temperature_forwarded_when_present(self, provider):
        p, mock_client = provider
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}], temperature=0.5)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["temperature"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_top_p_forwarded_when_present(self, provider):
        p, mock_client = provider
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}], top_p=0.9)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["top_p"] == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_temperature_absent_when_not_supplied(self, provider):
        p, mock_client = provider
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_api_response("ok")
        )

        await p.complete([{"role": "user", "content": "Hi"}])

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty_string(self, provider):
        p, mock_client = provider
        response = MagicMock()
        choice = MagicMock()
        choice.message.content = None  # OpenAI can return None for content
        response.choices = [choice]
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        result = await p.complete([{"role": "user", "content": "Hi"}])
        assert result == ""
