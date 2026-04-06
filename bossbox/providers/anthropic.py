"""
Anthropic Provider — BossBox Atomic Step 5
===========================================
Wraps the Anthropic Messages API.

The anthropic SDK is an optional dependency (installed via the [cloud] extra).
If it is not installed, or if no API key is supplied, the provider raises
ProviderUnavailableError at construction time so the registry can catch it
and register None silently.
"""
from __future__ import annotations

from bossbox.providers.base import ModelProvider, ProviderUnavailableError

# Lazy import — the SDK is optional.
# _anthropic is always bound at module level so patch() can find it.
try:
    import anthropic as _anthropic  # type: ignore[import]

    _SDK_AVAILABLE = True
except ModuleNotFoundError:
    _anthropic = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False


# Matches the model string used in the providers.yaml default.
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicProvider(ModelProvider):
    """Calls the Anthropic Messages API.

    Message format follows the OpenAI-compatible convention used throughout
    BossBox (list of {"role": ..., "content": ...} dicts).  A leading message
    with role "system" is extracted and passed as Anthropic's top-level
    ``system`` parameter; all other messages are forwarded as-is.
    """

    def __init__(self, api_key: str, default_model: str | None = None) -> None:
        if not _SDK_AVAILABLE:
            raise ProviderUnavailableError(
                "anthropic SDK not installed — run: pip install 'bossbox[cloud]'"
            )
        if not api_key:
            raise ProviderUnavailableError(
                "Anthropic API key not set (ANTHROPIC_API_KEY)"
            )
        self._client = _anthropic.AsyncAnthropic(api_key=api_key)
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
        """Send *messages* to Anthropic and return the assistant text."""
        system_text, filtered = _split_system(messages)

        create_kwargs: dict = {
            "model": model or self._default_model,
            "max_tokens": int(kwargs.get("max_tokens", 1024)),
            "messages": filtered,
        }
        if "temperature" in kwargs:
            create_kwargs["temperature"] = float(kwargs["temperature"])
        if system_text:
            create_kwargs["system"] = system_text

        response = await self._client.messages.create(**create_kwargs)
        # content is a list of ContentBlock; we want the first text block.
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""  # pragma: no cover

    async def is_available(self) -> bool:
        """Return True if the Anthropic API is reachable with current credentials.

        For cloud providers this is a lightweight check — we attempt to list
        models or simply return True if the client was constructed successfully,
        since there is no free ping endpoint.  A failed ``complete()`` call
        will surface credential errors at invocation time.
        """
        # Construction already validated that the SDK is present and a key
        # exists.  We return True optimistically — actual reachability is
        # confirmed on the first real call.
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def default_model(self) -> str:
        return self._default_model

    def __repr__(self) -> str:  # pragma: no cover
        return f"AnthropicProvider(model={self._default_model!r})"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_system(
    messages: list[dict],
) -> tuple[str | None, list[dict]]:
    """Separate the optional leading system message from the rest.

    Anthropic's API takes ``system`` at the top level, not inside the
    messages array.  We extract the first system message (if present) and
    return it separately.  Any subsequent system-role entries are left in
    place — the Anthropic SDK will reject them, which is the correct
    behaviour (callers shouldn't produce multi-system prompts).
    """
    if messages and messages[0].get("role") == "system":
        return messages[0]["content"], messages[1:]
    return None, messages
