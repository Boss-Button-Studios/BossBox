"""
tests/config/test_loader.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Full test coverage for bossbox.config.loader.

Test groups
-----------
TestExpandValue     – unit tests for the internal env-var expansion helper
TestLoadProviders   – load_providers() with various YAML shapes
TestLoadTiers       – load_tiers() with various YAML shapes
TestLoadConfig      – load_config() integration tests (both files, missing
                      files, empty dir, str vs Path argument)
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from bossbox.config.loader import (
    AnthropicProviderConfig,
    BossBoxConfig,
    OllamaProviderConfig,
    OpenAIProviderConfig,
    ProvidersConfig,
    TierConfig,
    TiersConfig,
    _expand_value,
    load_config,
    load_providers,
    load_tiers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yaml(tmp_path: Path, name: str, content: str) -> Path:
    """Write *content* to *tmp_path/name* and return the path."""
    p = tmp_path / name
    p.write_text(dedent(content), encoding="utf-8")
    return p


# ===========================================================================
# _expand_value
# ===========================================================================

class TestExpandValue:
    """Unit tests for the internal env-var expansion helper."""

    # -- plain types --------------------------------------------------------

    def test_plain_string_unchanged(self):
        assert _expand_value("hello world") == "hello world"

    def test_integer_passthrough(self):
        assert _expand_value(42) == 42

    def test_float_passthrough(self):
        assert _expand_value(3.14) == pytest.approx(3.14)

    def test_bool_passthrough(self):
        assert _expand_value(True) is True

    def test_none_passthrough(self):
        assert _expand_value(None) is None

    # -- whole-value single reference ---------------------------------------

    def test_single_ref_present(self, monkeypatch):
        monkeypatch.setenv("BB_TEST_VAR", "secret-value")
        assert _expand_value("${BB_TEST_VAR}") == "secret-value"

    def test_single_ref_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("BB_ABSENT", raising=False)
        assert _expand_value("${BB_ABSENT}") is None

    def test_single_ref_empty_string_var(self, monkeypatch):
        """An env var set to '' is not the same as absent."""
        monkeypatch.setenv("BB_EMPTY", "")
        assert _expand_value("${BB_EMPTY}") == ""

    # -- embedded references ------------------------------------------------

    def test_embedded_ref_present(self, monkeypatch):
        monkeypatch.setenv("BB_HOST", "localhost")
        assert _expand_value("http://${BB_HOST}:11434") == "http://localhost:11434"

    def test_embedded_ref_missing_replaced_with_empty(self, monkeypatch):
        monkeypatch.delenv("BB_HOST", raising=False)
        assert _expand_value("http://${BB_HOST}:11434") == "http://:11434"

    def test_two_embedded_refs(self, monkeypatch):
        monkeypatch.setenv("BB_SCHEME", "https")
        monkeypatch.setenv("BB_PORT", "443")
        result = _expand_value("${BB_SCHEME}://host:${BB_PORT}")
        assert result == "https://host:443"

    # -- containers ---------------------------------------------------------

    def test_dict_recursion(self, monkeypatch):
        monkeypatch.setenv("BB_KEY", "val")
        result = _expand_value({"a": "${BB_KEY}", "b": "static"})
        assert result == {"a": "val", "b": "static"}

    def test_list_recursion(self, monkeypatch):
        monkeypatch.setenv("BB_ITEM", "x")
        result = _expand_value(["${BB_ITEM}", "y", 3])
        assert result == ["x", "y", 3]

    def test_nested_dict_in_list(self, monkeypatch):
        monkeypatch.setenv("BB_SECRET", "abc123")
        data = [{"key": "${BB_SECRET}"}]
        assert _expand_value(data) == [{"key": "abc123"}]

    def test_nested_structure(self, monkeypatch):
        monkeypatch.setenv("BB_DEEP", "deep-val")
        data = {"outer": {"inner": "${BB_DEEP}"}}
        assert _expand_value(data) == {"outer": {"inner": "deep-val"}}

    def test_absent_nested_var(self, monkeypatch):
        monkeypatch.delenv("BB_MISSING", raising=False)
        data = {"level": {"key": "${BB_MISSING}"}}
        assert _expand_value(data) == {"level": {"key": None}}


# ===========================================================================
# load_providers
# ===========================================================================

class TestLoadProviders:
    """Tests for load_providers() with various YAML shapes."""

    def test_full_config_with_env_vars_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key-xyz")
        monkeypatch.setenv("OPENAI_API_KEY", "oai-key-xyz")
        p = _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://localhost:11434
              anthropic:
                api_key: ${ANTHROPIC_API_KEY}
                default_model: claude-haiku-4-5
              openai:
                api_key: ${OPENAI_API_KEY}
                default_model: gpt-4o-mini
        """)
        cfg = load_providers(p)

        assert isinstance(cfg, ProvidersConfig)
        assert isinstance(cfg.ollama, OllamaProviderConfig)
        assert cfg.ollama.base_url == "http://localhost:11434"

        assert isinstance(cfg.anthropic, AnthropicProviderConfig)
        assert cfg.anthropic.api_key == "ant-key-xyz"
        assert cfg.anthropic.default_model == "claude-haiku-4-5"

        assert isinstance(cfg.openai, OpenAIProviderConfig)
        assert cfg.openai.api_key == "oai-key-xyz"
        assert cfg.openai.default_model == "gpt-4o-mini"

    def test_missing_api_keys_resolve_to_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        p = _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://localhost:11434
              anthropic:
                api_key: ${ANTHROPIC_API_KEY}
                default_model: claude-haiku-4-5
              openai:
                api_key: ${OPENAI_API_KEY}
                default_model: gpt-4o-mini
        """)
        cfg = load_providers(p)

        # Sections present; keys absent → None, not a raised error
        assert cfg.anthropic is not None
        assert cfg.anthropic.api_key is None
        assert cfg.anthropic.default_model == "claude-haiku-4-5"  # literal, not env

        assert cfg.openai is not None
        assert cfg.openai.api_key is None

    def test_ollama_only_no_cloud_sections(self, tmp_path):
        p = _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://localhost:11434
        """)
        cfg = load_providers(p)
        assert cfg.ollama.base_url == "http://localhost:11434"
        assert cfg.anthropic is None
        assert cfg.openai is None

    def test_ollama_default_base_url_when_key_absent(self, tmp_path):
        p = _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama: {}
        """)
        cfg = load_providers(p)
        assert cfg.ollama.base_url == "http://localhost:11434"

    def test_custom_ollama_base_url(self, tmp_path):
        p = _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://192.168.1.100:11434
        """)
        cfg = load_providers(p)
        assert cfg.ollama.base_url == "http://192.168.1.100:11434"

    def test_empty_providers_section_returns_defaults(self, tmp_path):
        p = _yaml(tmp_path, "providers.yaml", """
            providers: {}
        """)
        cfg = load_providers(p)
        assert isinstance(cfg.ollama, OllamaProviderConfig)
        assert cfg.anthropic is None
        assert cfg.openai is None

    def test_anthropic_without_model_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        p = _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://localhost:11434
              anthropic:
                api_key: ${ANTHROPIC_API_KEY}
        """)
        cfg = load_providers(p)
        assert cfg.anthropic.api_key == "key"
        assert cfg.anthropic.default_model is None

    def test_path_argument_as_path_object(self, tmp_path):
        p = _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://localhost:11434
        """)
        cfg = load_providers(Path(p))
        assert isinstance(cfg, ProvidersConfig)


# ===========================================================================
# load_tiers
# ===========================================================================

class TestLoadTiers:
    """Tests for load_tiers() with various YAML shapes."""

    def test_full_five_tier_config(self, tmp_path):
        p = _yaml(tmp_path, "tiers.yaml", """
            tiers:
              nano:
                primary: ollama/smollm:360m
                fallback: []
              micro:
                primary: ollama/smollm:1.7b
              specialist:
                primary: ollama/qwen2.5-coder:1.5b
              reasoner:
                primary: ollama/deepseek-r1:7b
                fallback:
                  - anthropic/claude-haiku-4-5
                  - openai/gpt-4o-mini
              cloud:
                primary: anthropic/claude-haiku-4-5
                fallback:
                  - openai/gpt-4o-mini
        """)
        cfg = load_tiers(p)

        assert isinstance(cfg, TiersConfig)
        assert isinstance(cfg.nano, TierConfig)
        assert cfg.nano.primary == "ollama/smollm:360m"
        assert cfg.nano.fallback == []

        assert cfg.micro.primary == "ollama/smollm:1.7b"
        assert cfg.micro.fallback == []  # absent fallback → empty list

        assert cfg.specialist.primary == "ollama/qwen2.5-coder:1.5b"

        assert cfg.reasoner.primary == "ollama/deepseek-r1:7b"
        assert cfg.reasoner.fallback == [
            "anthropic/claude-haiku-4-5",
            "openai/gpt-4o-mini",
        ]

        assert cfg.cloud.primary == "anthropic/claude-haiku-4-5"
        assert cfg.cloud.fallback == ["openai/gpt-4o-mini"]

    def test_absent_tiers_resolve_to_none(self, tmp_path):
        p = _yaml(tmp_path, "tiers.yaml", """
            tiers:
              nano:
                primary: ollama/smollm:360m
        """)
        cfg = load_tiers(p)
        assert cfg.nano is not None
        assert cfg.micro is None
        assert cfg.specialist is None
        assert cfg.reasoner is None
        assert cfg.cloud is None

    def test_empty_tiers_section(self, tmp_path):
        p = _yaml(tmp_path, "tiers.yaml", """
            tiers: {}
        """)
        cfg = load_tiers(p)
        assert cfg.nano is None
        assert cfg.reasoner is None

    def test_fallback_scalar_coerced_to_list(self, tmp_path):
        """A bare scalar fallback string should be wrapped in a list."""
        p = _yaml(tmp_path, "tiers.yaml", """
            tiers:
              reasoner:
                primary: ollama/deepseek-r1:7b
                fallback: anthropic/claude-haiku-4-5
        """)
        cfg = load_tiers(p)
        assert cfg.reasoner.fallback == ["anthropic/claude-haiku-4-5"]

    def test_tier_config_type(self, tmp_path):
        p = _yaml(tmp_path, "tiers.yaml", """
            tiers:
              nano:
                primary: ollama/smollm:360m
        """)
        cfg = load_tiers(p)
        assert isinstance(cfg.nano, TierConfig)

    def test_path_argument_as_path_object(self, tmp_path):
        p = _yaml(tmp_path, "tiers.yaml", """
            tiers:
              nano:
                primary: ollama/smollm:360m
        """)
        cfg = load_tiers(Path(p))
        assert isinstance(cfg, TiersConfig)


# ===========================================================================
# load_config  (integration)
# ===========================================================================

class TestLoadConfig:
    """Integration tests for load_config() combining both files."""

    def test_both_files_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "integration-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://localhost:11434
              anthropic:
                api_key: ${ANTHROPIC_API_KEY}
                default_model: claude-haiku-4-5
              openai:
                api_key: ${OPENAI_API_KEY}
                default_model: gpt-4o-mini
        """)
        _yaml(tmp_path, "tiers.yaml", """
            tiers:
              nano:
                primary: ollama/smollm:360m
              reasoner:
                primary: ollama/deepseek-r1:7b
                fallback:
                  - anthropic/claude-haiku-4-5
        """)
        cfg = load_config(tmp_path)

        assert isinstance(cfg, BossBoxConfig)
        assert cfg.providers.anthropic.api_key == "integration-key"
        assert cfg.providers.openai.api_key is None          # env var absent
        assert cfg.tiers.nano.primary == "ollama/smollm:360m"
        assert cfg.tiers.reasoner.fallback == ["anthropic/claude-haiku-4-5"]
        assert cfg.tiers.micro is None

    def test_missing_providers_file_returns_defaults(self, tmp_path):
        _yaml(tmp_path, "tiers.yaml", """
            tiers:
              nano:
                primary: ollama/smollm:360m
        """)
        cfg = load_config(tmp_path)
        assert isinstance(cfg.providers, ProvidersConfig)
        assert cfg.providers.ollama.base_url == "http://localhost:11434"
        assert cfg.providers.anthropic is None
        assert cfg.tiers.nano.primary == "ollama/smollm:360m"

    def test_missing_tiers_file_returns_defaults(self, tmp_path):
        _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://localhost:11434
        """)
        cfg = load_config(tmp_path)
        assert isinstance(cfg.tiers, TiersConfig)
        assert cfg.tiers.nano is None
        assert cfg.providers.ollama.base_url == "http://localhost:11434"

    def test_empty_directory_returns_full_defaults(self, tmp_path):
        cfg = load_config(tmp_path)
        assert isinstance(cfg, BossBoxConfig)
        assert isinstance(cfg.providers, ProvidersConfig)
        assert isinstance(cfg.tiers, TiersConfig)
        assert cfg.providers.ollama.base_url == "http://localhost:11434"
        assert cfg.tiers.nano is None

    def test_config_dir_as_string(self, tmp_path):
        """config_dir accepts a plain string as well as a Path."""
        cfg = load_config(str(tmp_path))
        assert isinstance(cfg, BossBoxConfig)

    def test_config_dir_as_none_uses_default(self):
        """
        Passing None must not raise.  The default config/ dir may or may not
        exist in the test environment; either outcome is acceptable as long as
        the return type is BossBoxConfig.
        """
        cfg = load_config(None)
        assert isinstance(cfg, BossBoxConfig)

    def test_env_vars_isolated_between_tests(self, tmp_path, monkeypatch):
        """Confirm monkeypatch does not leak between tests."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _yaml(tmp_path, "providers.yaml", """
            providers:
              ollama:
                base_url: http://localhost:11434
              anthropic:
                api_key: ${ANTHROPIC_API_KEY}
                default_model: claude-haiku-4-5
        """)
        cfg = load_config(tmp_path)
        assert cfg.providers.anthropic.api_key is None
