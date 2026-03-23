"""
tests/providers/test_ollama.py

Tests for bossbox.providers.ollama.OllamaProvider.

Unit tests use respx to mock httpx — no running Ollama required.
Integration tests (marked ``ollama``) require a live Ollama instance
and are skipped automatically when one is not reachable.

Run unit tests only:
    pytest tests/providers/test_ollama.py -m "not ollama" -v

Run everything (requires Ollama):
    pytest tests/providers/test_ollama.py -v
"""

from __future__ import annotations

import pytest
import respx
import httpx
import pytest_asyncio  # noqa: F401 — registers asyncio mode

from bossbox.providers.ollama import OllamaProvider, _DEFAULT_BASE_URL
from bossbox.providers.base import ModelNotFoundError, ProviderUnavailableError

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

CHAT_URL = f"{_DEFAULT_BASE_URL}/api/chat"
TAGS_URL = f"{_DEFAULT_BASE_URL}/api/tags"

GOOD_CHAT_RESPONSE = {
    "model": "smollm:360m",
    "message": {"role": "assistant", "content": "Hello, world!"},
    "done": True,
}

GOOD_MESSAGES = [{"role": "user", "content": "Say hello."}]


@pytest.fixture
def provider() -> OllamaProvider:
    return OllamaProvider()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_base_url(self, provider):
        assert provider.base_url == "http://localhost:11434"

    def test_trailing_slash_stripped(self):
        p = OllamaProvider(base_url="http://localhost:11434/")
        assert p.base_url == "http://localhost:11434"

    def test_default_model(self, provider):
        assert provider.model == "smollm:360m"

    def test_custom_model(self):
        p = OllamaProvider(model="deepseek-r1:7b")
        assert p.model == "deepseek-r1:7b"

    def test_provider_name(self, provider):
        assert provider.provider_name == "ollama"

    def test_custom_timeout(self):
        p = OllamaProvider(completion_timeout=30.0)
        assert p.completion_timeout == 30.0


# ---------------------------------------------------------------------------
# complete() — success paths
# ---------------------------------------------------------------------------


class TestCompleteSuccess:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_assistant_content(self, provider):
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=GOOD_CHAT_RESPONSE))
        result = await provider.complete(GOOD_MESSAGES)
        assert result == "Hello, world!"

    @pytest.mark.asyncio
    @respx.mock
    async def test_result_is_non_empty_string(self, provider):
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=GOOD_CHAT_RESPONSE))
        result = await provider.complete(GOOD_MESSAGES)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_strips_leading_trailing_whitespace(self, provider):
        response_data = {
            "message": {"role": "assistant", "content": "  trimmed  "},
            "done": True,
        }
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=response_data))
        result = await provider.complete(GOOD_MESSAGES)
        assert result == "trimmed"

    @pytest.mark.asyncio
    @respx.mock
    async def test_model_kwarg_overrides_default(self, provider):
        """Passing model= kwarg should use that model, not the provider default."""
        captured = {}

        def capture(request):
            captured["body"] = request.content
            return httpx.Response(200, json=GOOD_CHAT_RESPONSE)

        respx.post(CHAT_URL).mock(side_effect=capture)
        await provider.complete(GOOD_MESSAGES, model="deepseek-r1:7b")
        import json
        body = json.loads(captured["body"])
        assert body["model"] == "deepseek-r1:7b"

    @pytest.mark.asyncio
    @respx.mock
    async def test_temperature_forwarded_as_option(self, provider):
        captured = {}

        def capture(request):
            import json
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=GOOD_CHAT_RESPONSE)

        respx.post(CHAT_URL).mock(side_effect=capture)
        await provider.complete(GOOD_MESSAGES, temperature=0.7)
        assert captured["body"]["options"]["temperature"] == 0.7

    @pytest.mark.asyncio
    @respx.mock
    async def test_max_tokens_forwarded_as_num_predict(self, provider):
        captured = {}

        def capture(request):
            import json
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=GOOD_CHAT_RESPONSE)

        respx.post(CHAT_URL).mock(side_effect=capture)
        await provider.complete(GOOD_MESSAGES, max_tokens=512)
        assert captured["body"]["options"]["num_predict"] == 512

    @pytest.mark.asyncio
    @respx.mock
    async def test_stream_always_false_in_payload(self, provider):
        captured = {}

        def capture(request):
            import json
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=GOOD_CHAT_RESPONSE)

        respx.post(CHAT_URL).mock(side_effect=capture)
        await provider.complete(GOOD_MESSAGES)
        assert captured["body"]["stream"] is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_fallback_generate_response_shape(self, provider):
        """Older /api/generate shape with top-level 'response' key."""
        old_shape = {"response": "Legacy reply.", "done": True}
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=old_shape))
        result = await provider.complete(GOOD_MESSAGES)
        assert result == "Legacy reply."


# ---------------------------------------------------------------------------
# complete() — error paths
# ---------------------------------------------------------------------------


class TestCompleteErrors:
    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_error_raises_provider_unavailable(self, provider):
        respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(ProviderUnavailableError, match="Cannot reach Ollama"):
            await provider.complete(GOOD_MESSAGES)

    @pytest.mark.asyncio
    @respx.mock
    async def test_timeout_raises_provider_unavailable(self, provider):
        respx.post(CHAT_URL).mock(side_effect=httpx.TimeoutException("timed out"))
        with pytest.raises(ProviderUnavailableError, match="timed out"):
            await provider.complete(GOOD_MESSAGES)

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_404_raises_model_not_found(self, provider):
        body = {"error": "model 'smollm:360m' not found, try pulling it first"}
        respx.post(CHAT_URL).mock(return_value=httpx.Response(404, json=body))
        with pytest.raises(ModelNotFoundError) as exc_info:
            await provider.complete(GOOD_MESSAGES)
        assert exc_info.value.model == "smollm:360m"
        assert exc_info.value.provider == "ollama"

    @pytest.mark.asyncio
    @respx.mock
    async def test_model_not_found_includes_ollama_detail(self, provider):
        body = {"error": "model 'x' not found, try pulling it first"}
        respx.post(CHAT_URL).mock(return_value=httpx.Response(404, json=body))
        with pytest.raises(ModelNotFoundError) as exc_info:
            await provider.complete(GOOD_MESSAGES)
        assert "try pulling" in str(exc_info.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_500_raises_provider_unavailable(self, provider):
        respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="internal error"))
        with pytest.raises(ProviderUnavailableError, match="500"):
            await provider.complete(GOOD_MESSAGES)

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_json_body_raises_provider_unavailable(self, provider):
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text="not json"))
        with pytest.raises(ProviderUnavailableError):
            await provider.complete(GOOD_MESSAGES)

    @pytest.mark.asyncio
    @respx.mock
    async def test_unexpected_response_shape_raises_provider_unavailable(self, provider):
        """A 200 with a JSON body that has neither 'message' nor 'response'."""
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={"done": True}))
        with pytest.raises(ProviderUnavailableError, match="Unexpected Ollama response shape"):
            await provider.complete(GOOD_MESSAGES)

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_raises_provider_unavailable(self, provider):
        respx.post(CHAT_URL).mock(side_effect=httpx.HTTPError("generic http error"))
        with pytest.raises(ProviderUnavailableError):
            await provider.complete(GOOD_MESSAGES)


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------


class TestIsAvailable:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_true_when_tags_200(self, provider):
        respx.get(TAGS_URL).mock(return_value=httpx.Response(200, json={"models": []}))
        assert await provider.is_available() is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_false_when_connect_error(self, provider):
        respx.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
        assert await provider.is_available() is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_false_when_timeout(self, provider):
        respx.get(TAGS_URL).mock(side_effect=httpx.TimeoutException("timeout"))
        assert await provider.is_available() is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_false_on_non_200_status(self, provider):
        respx.get(TAGS_URL).mock(return_value=httpx.Response(503))
        assert await provider.is_available() is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_does_not_raise_on_any_network_failure(self, provider):
        """is_available() must never propagate an exception."""
        respx.get(TAGS_URL).mock(side_effect=httpx.ConnectError("down"))
        result = await provider.is_available()   # must not raise
        assert result is False


# ---------------------------------------------------------------------------
# Integration tests — require a live Ollama instance
# ---------------------------------------------------------------------------


def _ollama_reachable() -> bool:
    """Synchronous probe used by the skipif marker."""
    import httpx as _httpx
    try:
        r = _httpx.get(f"{_DEFAULT_BASE_URL}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.ollama
@pytest.mark.skipif(not _ollama_reachable(), reason="Ollama not reachable at localhost:11434")
class TestOllamaIntegration:
    """
    Live tests against a running Ollama instance.

    These are skipped automatically in CI unless Ollama is present.
    To run locally:
        pytest tests/providers/test_ollama.py -m ollama -v
    """

    @pytest.mark.asyncio
    async def test_complete_returns_non_empty_string(self):
        """The most basic acceptance criterion from the spec."""
        provider = OllamaProvider(model="smollm:360m")
        result = await provider.complete([{"role": "user", "content": "Reply with one word: hello"}])
        assert isinstance(result, str)
        assert len(result.strip()) > 0

    @pytest.mark.asyncio
    async def test_is_available_returns_true(self):
        provider = OllamaProvider()
        assert await provider.is_available() is True

    @pytest.mark.asyncio
    async def test_model_not_found_for_nonexistent_model(self):
        provider = OllamaProvider(model="this-model-does-not-exist:latest")
        with pytest.raises(ModelNotFoundError):
            await provider.complete([{"role": "user", "content": "hi"}])
