"""
bossbox.config.loader
~~~~~~~~~~~~~~~~~~~~~
Load, validate, and return typed configuration from YAML files.

Design contract
---------------
* ``load_config(config_dir)`` is the primary entry point.  Pass the path to
  the project's ``config/`` directory, or omit it to use the default
  location (the ``config/`` folder at the project root, relative to this
  file).
* Missing *optional* keys always resolve to ``None`` — they never raise.
* ``${VAR}`` references in YAML values are expanded from the environment.
  A reference whose env var is absent resolves to ``None`` (for whole-value
  references) or to an empty string (when the reference is embedded inside
  a larger string), matching common shell behaviour.
* Missing env var references never raise.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Default config directory: <project_root>/config/
# File layout:  bossbox/bossbox/config/loader.py
#               → parent  bossbox/bossbox/config/
#               → parent  bossbox/bossbox/
#               → parent  bossbox/             (project root)
#               → child   bossbox/config/
_DEFAULT_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


# ---------------------------------------------------------------------------
# Environment variable expansion
# ---------------------------------------------------------------------------

def _expand_value(value: Any) -> Any:
    """
    Recursively expand ``${VAR}`` references throughout *value*.

    Rules
    -----
    * **Whole-value reference** – a string that is *exactly* ``${VAR}``:
      resolves to the env var's value, or ``None`` when the var is absent.
    * **Embedded reference** – ``${VAR}`` appears inside a larger string:
      missing vars are replaced with ``""`` (empty string).
    * Dicts and lists are processed recursively.
    * All other types (int, float, bool, None) are returned unchanged.
    """
    if isinstance(value, str):
        # Whole-value single reference → preserve None identity when absent
        full_match = _ENV_VAR_RE.fullmatch(value)
        if full_match:
            return os.environ.get(full_match.group(1))  # None when missing

        # Embedded reference → missing vars become empty string
        return _ENV_VAR_RE.sub(
            lambda m: os.environ.get(m.group(1), ""),
            value,
        )

    if isinstance(value, dict):
        return {k: _expand_value(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_expand_value(item) for item in value]

    # int, float, bool, None, etc.
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read *path* as YAML and expand all ``${VAR}`` references."""
    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    return _expand_value(raw)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Typed dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OllamaProviderConfig:
    """Configuration for the local Ollama provider."""
    base_url: str = "http://localhost:11434"


@dataclass
class AnthropicProviderConfig:
    """Configuration for the Anthropic cloud provider."""
    api_key: str | None = None
    default_model: str | None = None


@dataclass
class OpenAIProviderConfig:
    """Configuration for the OpenAI cloud provider."""
    api_key: str | None = None
    default_model: str | None = None


@dataclass
class ProvidersConfig:
    """Aggregated provider configuration."""
    ollama: OllamaProviderConfig = field(default_factory=OllamaProviderConfig)
    anthropic: AnthropicProviderConfig | None = None
    openai: OpenAIProviderConfig | None = None


@dataclass
class TierConfig:
    """Primary model and ordered fallback chain for a single tier."""
    primary: str
    fallback: list[str] = field(default_factory=list)


@dataclass
class TiersConfig:
    """Tier assignments across all five model tiers."""
    nano: TierConfig | None = None
    micro: TierConfig | None = None
    specialist: TierConfig | None = None
    reasoner: TierConfig | None = None
    cloud: TierConfig | None = None


@dataclass
class BossBoxConfig:
    """Root configuration object returned by :func:`load_config`."""
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    tiers: TiersConfig = field(default_factory=TiersConfig)


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

def _build_providers(data: dict[str, Any]) -> ProvidersConfig:
    providers_raw: dict[str, Any] = data.get("providers") or {}

    # Ollama — always present, has a default base_url
    ollama_raw: dict[str, Any] = providers_raw.get("ollama") or {}
    ollama = OllamaProviderConfig(
        base_url=ollama_raw.get("base_url") or "http://localhost:11434",
    )

    # Anthropic — present only when the section exists in YAML
    anthropic: AnthropicProviderConfig | None = None
    if "anthropic" in providers_raw:
        a: dict[str, Any] = providers_raw["anthropic"] or {}
        anthropic = AnthropicProviderConfig(
            api_key=a.get("api_key"),          # None when env var missing
            default_model=a.get("default_model"),
        )

    # OpenAI — same pattern
    openai: OpenAIProviderConfig | None = None
    if "openai" in providers_raw:
        o: dict[str, Any] = providers_raw["openai"] or {}
        openai = OpenAIProviderConfig(
            api_key=o.get("api_key"),
            default_model=o.get("default_model"),
        )

    return ProvidersConfig(ollama=ollama, anthropic=anthropic, openai=openai)


def _build_tier(raw: dict[str, Any]) -> TierConfig:
    fallback = raw.get("fallback") or []
    # Tolerate a bare scalar string instead of a list (YAML authoring error)
    if isinstance(fallback, str):
        fallback = [fallback]
    return TierConfig(primary=raw["primary"], fallback=list(fallback))


def _build_tiers(data: dict[str, Any]) -> TiersConfig:
    tiers_raw: dict[str, Any] = data.get("tiers") or {}

    def _maybe(name: str) -> TierConfig | None:
        section = tiers_raw.get(name)
        if section and isinstance(section, dict):
            return _build_tier(section)
        return None

    return TiersConfig(
        nano=_maybe("nano"),
        micro=_maybe("micro"),
        specialist=_maybe("specialist"),
        reasoner=_maybe("reasoner"),
        cloud=_maybe("cloud"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_providers(path: Path) -> ProvidersConfig:
    """
    Load and parse a providers YAML file at *path*.

    Useful when you want to load providers config independently of the full
    :func:`load_config` call, or when testing with a custom file path.
    """
    return _build_providers(_load_yaml(Path(path)))


def load_tiers(path: Path) -> TiersConfig:
    """
    Load and parse a tiers YAML file at *path*.

    Useful when you want to load tier config independently of the full
    :func:`load_config` call, or when testing with a custom file path.
    """
    return _build_tiers(_load_yaml(Path(path)))


def load_config(config_dir: Path | str | None = None) -> BossBoxConfig:
    """
    Load the full BossBox configuration from *config_dir*.

    Parameters
    ----------
    config_dir:
        Directory containing ``providers.yaml`` and ``tiers.yaml``.
        Defaults to the project-root ``config/`` directory when ``None``.

    Returns
    -------
    BossBoxConfig
        Fully populated config object.  Missing optional keys resolve to
        ``None``; missing env var references resolve to ``None``; missing
        files resolve to default dataclass instances.  Nothing raises for
        ordinary absent-but-optional configuration.
    """
    directory = Path(config_dir) if config_dir is not None else _DEFAULT_CONFIG_DIR

    providers_path = directory / "providers.yaml"
    tiers_path = directory / "tiers.yaml"

    providers = (
        load_providers(providers_path)
        if providers_path.exists()
        else ProvidersConfig()
    )
    tiers = (
        load_tiers(tiers_path)
        if tiers_path.exists()
        else TiersConfig()
    )

    return BossBoxConfig(providers=providers, tiers=tiers)
