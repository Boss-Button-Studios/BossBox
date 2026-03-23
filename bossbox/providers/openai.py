"""OpenAIProvider — wraps the OpenAI Chat Completions API.

The openai SDK is an optional dependency (installed via the [cloud] extra).
If it is not installed, or if no API key is supplied, the provider raises
ProviderUnavailableError at construction time so the registry can catch it
and register None silently.
"""
from __future__ import annotations

from bossbox.providers.base import ModelProvider, ProviderUnavailableError

# Lazy import — the SDK is optional.
# _openai is always bound at module level so patch() can find it.
try:
    import openai as _openai  # type: ignore[import]

    _SDK_AVAILABLE = True
except ModuleNotFoundError:
    _openai = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False


_DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(ModelProvider):
    """Calls the OpenAI Chat Completions API.

    Message format is the standard OpenAI-compatible list of
    {"role": ..., "content": ...} dicts — no conversion required.
    """

    def __init__(self, api_key: str, default_model: str | None = None) -> None:
        if not _SDK_AVAILABLE:
            raise ProviderUnavailableError(
                "openai SDK not installed — run: pip install 'bossbox[cloud]'"
            )
        if not api_key:
            raise ProviderUnavailableError(
                "OpenAI API key not set (OPENAI_API_KEY)"
            )
        self._client = _openai.AsyncOpenAI(api_key=api_key)
        self._default_model: str = default_model or _DEFAULT_MODEL

    # ------------------------------------------------------------------
    # ModelProvider interface
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        **kwargs,
    ) -> str:
        """Send *messages* to OpenAI and return the assistant text."""
        create_kwargs: dict = {
            "model": model or self._default_model,
            "messages": messages,
            "max_tokens": int(kwargs.get("max_tokens", 1024)),
        }
        if "temperature" in kwargs:
            create_kwargs["temperature"] = float(kwargs["temperature"])
        if "top_p" in kwargs:
            create_kwargs["top_p"] = float(kwargs["top_p"])

        response = await self._client.chat.completions.create(**create_kwargs)
        return response.choices[0].message.content or ""

    async def is_available(self) -> bool:
        """Return True optimistically — construction validated SDK and key presence."""
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def default_model(self) -> str:
        return self._default_model

    def __repr__(self) -> str:  # pragma: no cover
        return f"OpenAIProvider(model={self._default_model!r})"
