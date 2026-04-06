"""
Provider Registry — BossBox Atomic Step 6
==========================================
Instantiates and holds all configured providers.

The registry is the single point of truth for which providers are available
in a given BossBox session.  It is built once from the loaded config and
consulted by the supervisor when it needs to invoke a model.

Design rules
------------
* Missing credentials → provider registered as None, no exception raised.
* SDK not installed → same silent None registration.
* Ollama has no credentials; it is always attempted (falls back to None only
  if the config section is absent entirely).
* ``resolve(provider_model)`` parses the "provider/model" strings used in the
  tier fallback chain config (Section 6.3) and returns the live provider
  instance paired with the model name, or None if that provider is
  unavailable.
* ``resolve_with_fallback(primary, fallbacks)`` walks the chain and returns
  the first pair that can actually be used.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from bossbox.providers.base import ModelProvider, ProviderUnavailableError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal entry type
# ---------------------------------------------------------------------------


@dataclass
class ProviderEntry:
    """A live provider instance plus its configured default model (if any)."""

    provider: ModelProvider
    default_model: Optional[str] = field(default=None)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """Holds all configured providers keyed by their short name.

    Keys are ``"ollama"``, ``"anthropic"``, and ``"openai"``.  A key is
    present in the internal dict if the section exists in the config; the
    *value* is either a :class:`ProviderEntry` (available) or ``None``
    (configured but credentials missing / SDK absent).
    """

    def __init__(self) -> None:
        # Private; build via from_config().
        self._providers: dict[str, Optional[ProviderEntry]] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config) -> "ProviderRegistry":
        """Build a registry from the providers config dataclass.

        *config* is the object returned by ``config.loader.load_providers_config()``.
        It must support attribute access; missing attributes return ``None``
        (consistent with the Step 2 config loader contract).

        All exceptions from provider constructors (ProviderUnavailableError,
        ModuleNotFoundError) are caught and logged at DEBUG level; the
        provider is registered as None.
        """
        registry = cls()

        registry._register_ollama(getattr(config, "ollama", None))
        registry._register_anthropic(getattr(config, "anthropic", None))
        registry._register_openai(getattr(config, "openai", None))

        log.debug("ProviderRegistry built: %r", registry)
        return registry

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[ModelProvider]:
        """Return the live provider for *name*, or None if unavailable."""
        entry = self._providers.get(name)
        return entry.provider if entry is not None else None

    def get_entry(self, name: str) -> Optional[ProviderEntry]:
        """Return the full :class:`ProviderEntry` for *name*, or None."""
        return self._providers.get(name)

    def resolve(self, provider_model: str) -> Optional[tuple[ModelProvider, str]]:
        """Parse a ``"provider/model"`` string and return ``(provider, model)``.

        Returns ``None`` if:
        * the string does not contain a ``/``
        * the named provider is unavailable

        Example::

            registry.resolve("ollama/deepseek-r1:7b")
            # → (OllamaProvider(...), "deepseek-r1:7b")

            registry.resolve("anthropic/claude-haiku-4-5")
            # → None  (if Anthropic is not configured)
        """
        if "/" not in provider_model:
            log.debug("resolve: no '/' in %r, skipping", provider_model)
            return None

        provider_name, model = provider_model.split("/", 1)
        provider = self.get(provider_name)
        if provider is None:
            log.debug("resolve: provider %r is unavailable", provider_name)
            return None

        return provider, model

    def resolve_with_fallback(
        self,
        primary: str,
        fallbacks: list[str],
    ) -> Optional[tuple[ModelProvider, str]]:
        """Return the first resolvable ``(provider, model)`` from a tier chain.

        Walks *primary* then *fallbacks* in order and returns the first pair
        where the provider is available.  Returns ``None`` if nothing in the
        chain is reachable — the supervisor should surface this as a
        configuration error.

        Corresponds directly to the ``tiers.<tier>.primary`` /
        ``tiers.<tier>.fallback`` structure in ``config/tiers.yaml``.
        """
        for candidate in [primary, *fallbacks]:
            result = self.resolve(candidate)
            if result is not None:
                return result
        return None

    def available(self) -> list[str]:
        """Short names of providers that are actually instantiated."""
        return [
            name
            for name, entry in self._providers.items()
            if entry is not None
        ]

    def unavailable(self) -> list[str]:
        """Short names of providers that are configured but unavailable."""
        return [
            name
            for name, entry in self._providers.items()
            if entry is None
        ]

    def __repr__(self) -> str:
        return (
            f"ProviderRegistry("
            f"available={self.available()}, "
            f"unavailable={self.unavailable()})"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _register_ollama(self, ollama_cfg) -> None:
        if ollama_cfg is None:
            # Config section absent — Ollama is not configured at all.
            self._providers["ollama"] = None
            return

        from bossbox.providers.ollama import OllamaProvider  # local import to avoid circular

        base_url: str = getattr(ollama_cfg, "base_url", None) or "http://localhost:11434"
        try:
            provider = OllamaProvider(base_url=base_url)
            self._providers["ollama"] = ProviderEntry(provider=provider)
        except Exception as exc:  # pragma: no cover
            log.debug("Ollama provider failed to initialise: %s", exc)
            self._providers["ollama"] = None

    def _register_anthropic(self, anthropic_cfg) -> None:
        if anthropic_cfg is None:
            self._providers["anthropic"] = None
            return

        api_key: str | None = getattr(anthropic_cfg, "api_key", None)
        default_model: str | None = getattr(anthropic_cfg, "default_model", None)

        if not api_key:
            log.debug("Anthropic: api_key absent — registering as unavailable")
            self._providers["anthropic"] = None
            return

        try:
            from bossbox.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider(api_key=api_key, default_model=default_model)
            self._providers["anthropic"] = ProviderEntry(
                provider=provider, default_model=default_model
            )
        except (ProviderUnavailableError, Exception) as exc:
            log.debug("Anthropic provider unavailable: %s", exc)
            self._providers["anthropic"] = None

    def _register_openai(self, openai_cfg) -> None:
        if openai_cfg is None:
            self._providers["openai"] = None
            return

        api_key: str | None = getattr(openai_cfg, "api_key", None)
        default_model: str | None = getattr(openai_cfg, "default_model", None)

        if not api_key:
            log.debug("OpenAI: api_key absent — registering as unavailable")
            self._providers["openai"] = None
            return

        try:
            from bossbox.providers.openai import OpenAIProvider

            provider = OpenAIProvider(api_key=api_key, default_model=default_model)
            self._providers["openai"] = ProviderEntry(
                provider=provider, default_model=default_model
            )
        except (ProviderUnavailableError, Exception) as exc:
            log.debug("OpenAI provider unavailable: %s", exc)
            self._providers["openai"] = None
