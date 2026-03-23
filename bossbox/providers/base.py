"""
bossbox/providers/base.py

Abstract base class for all model providers and the two exceptions that
callers must handle.  Every concrete provider (Ollama, Anthropic, OpenAI)
implements this interface so the Supervisor can treat them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Root for all provider-level failures."""


class ProviderUnavailableError(ProviderError):
    """
    Raised when the provider endpoint cannot be reached or returns an
    unexpected error that is not attributable to a specific model.

    Examples
    --------
    - Connection refused / DNS failure
    - HTTP 5xx from the provider
    - Request timeout
    - Malformed / unexpected response shape
    """


class ModelNotFoundError(ProviderError):
    """
    Raised when the requested model is not available on the provider.

    Examples
    --------
    - Ollama: model not pulled (HTTP 404 with 'model not found' body)
    - Anthropic/OpenAI: model identifier unknown to the API
    """

    def __init__(self, model: str, provider: str, detail: str = "") -> None:
        self.model = model
        self.provider = provider
        msg = f"Model '{model}' not found on provider '{provider}'."
        if detail:
            msg += f" {detail}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ModelProvider(ABC):
    """
    Uniform async interface that every concrete provider must implement.

    The Supervisor calls ``complete()`` for every model invocation and
    ``is_available()`` during health checks and onboarding.  Providers are
    responsible for translating their wire-protocol differences into this
    contract; callers never touch HTTP directly.
    """

    # Subclasses should set this to a short identifier, e.g. "ollama".
    provider_name: str = "unknown"

    @abstractmethod
    async def complete(self, messages: list[dict], **kwargs) -> str:
        """
        Send a list of chat messages and return the assistant reply as a
        plain string.

        Parameters
        ----------
        messages:
            OpenAI-style list of ``{"role": ..., "content": ...}`` dicts.
        **kwargs:
            Provider-specific overrides such as ``model``, ``temperature``,
            ``max_tokens``, ``top_p``.  Keys that a provider does not
            understand should be silently ignored rather than raised.

        Returns
        -------
        str
            The assistant's reply, stripped of leading/trailing whitespace.
            Never empty on success; raises on any failure.

        Raises
        ------
        ProviderUnavailableError
            The provider could not be reached or returned an unexpected error.
        ModelNotFoundError
            The requested model is not available on this provider.
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """
        Return True if the provider endpoint is reachable.

        This is a lightweight probe (e.g. GET /api/tags for Ollama) used
        during onboarding and health checks.  It must not raise; network
        failures should be caught internally and return False.
        """
