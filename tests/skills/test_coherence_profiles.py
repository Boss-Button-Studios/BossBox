"""
Document Type Coherence Profile Tests — BossBox Atomic Step 11
==============================================================
Validates all five default coherence profiles against acceptance criteria:
- Valid YAML
- At least five expected_elements
- At least three suspicious_if_present patterns
- Required fields present
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COHERENCE_DIR = Path(__file__).parents[2] / "skills" / "default" / "coherence"

EXPECTED_PROFILES = ["invoice", "contract", "code_file", "email", "report"]


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def profile_path(name: str) -> Path:
    return COHERENCE_DIR / f"{name}.yaml"


# ---------------------------------------------------------------------------
# Parametrised tests — apply to all five profiles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", EXPECTED_PROFILES)
class TestCoherenceProfileStructure:

    def test_file_exists(self, name):
        assert profile_path(name).exists(), f"Missing coherence profile: {name}.yaml"

    def test_parses_as_valid_yaml(self, name):
        data = load_yaml(profile_path(name))
        assert isinstance(data, dict)

    def test_required_top_level_keys(self, name):
        data = load_yaml(profile_path(name))
        for key in ("document_type", "version", "description",
                    "expected_elements", "suspicious_if_present",
                    "coherence_threshold"):
            assert key in data, f"{name}.yaml missing key: {key}"

    def test_document_type_matches_filename(self, name):
        data = load_yaml(profile_path(name))
        assert data["document_type"] == name

    def test_expected_elements_is_list(self, name):
        data = load_yaml(profile_path(name))
        assert isinstance(data["expected_elements"], list)

    def test_at_least_five_expected_elements(self, name):
        data = load_yaml(profile_path(name))
        count = len(data["expected_elements"])
        assert count >= 5, (
            f"{name}.yaml has only {count} expected_elements (need ≥ 5)"
        )

    def test_expected_elements_are_non_empty_strings(self, name):
        data = load_yaml(profile_path(name))
        for element in data["expected_elements"]:
            assert isinstance(element, str) and element.strip()

    def test_suspicious_if_present_is_list(self, name):
        data = load_yaml(profile_path(name))
        assert isinstance(data["suspicious_if_present"], list)

    def test_at_least_three_suspicious_patterns(self, name):
        data = load_yaml(profile_path(name))
        count = len(data["suspicious_if_present"])
        assert count >= 3, (
            f"{name}.yaml has only {count} suspicious_if_present patterns (need ≥ 3)"
        )

    def test_suspicious_patterns_are_non_empty_strings(self, name):
        data = load_yaml(profile_path(name))
        for pattern in data["suspicious_if_present"]:
            assert isinstance(pattern, str) and pattern.strip()

    def test_coherence_threshold_is_float_in_range(self, name):
        data = load_yaml(profile_path(name))
        threshold = data["coherence_threshold"]
        assert isinstance(threshold, float)
        assert 0.0 < threshold < 1.0

    def test_version_is_string(self, name):
        data = load_yaml(profile_path(name))
        assert isinstance(data["version"], str)

    def test_description_is_non_empty_string(self, name):
        data = load_yaml(profile_path(name))
        desc = data["description"]
        assert isinstance(desc, str) and len(desc.strip()) > 10

    def test_no_duplicate_expected_elements(self, name):
        data = load_yaml(profile_path(name))
        elements = data["expected_elements"]
        assert len(elements) == len(set(elements)), \
            f"{name}.yaml has duplicate expected_elements"

    def test_no_duplicate_suspicious_patterns(self, name):
        data = load_yaml(profile_path(name))
        patterns = data["suspicious_if_present"]
        assert len(patterns) == len(set(patterns)), \
            f"{name}.yaml has duplicate suspicious_if_present entries"

    def test_suspicious_patterns_include_ai_instruction_signal(self, name):
        """Every profile must flag some form of AI instruction language."""
        data = load_yaml(profile_path(name))
        combined = " ".join(data["suspicious_if_present"]).lower()
        has_signal = any(
            term in combined
            for term in ("ai", "model", "prompt", "instruction", "override", "ignore")
        )
        assert has_signal, (
            f"{name}.yaml suspicious_if_present should flag AI/instruction language"
        )


# ---------------------------------------------------------------------------
# Cross-profile tests
# ---------------------------------------------------------------------------

class TestCoherenceProfileSet:

    def test_all_five_profiles_exist(self):
        missing = [n for n in EXPECTED_PROFILES if not profile_path(n).exists()]
        assert not missing, f"Missing profiles: {missing}"

    def test_all_document_types_are_unique(self):
        types = [load_yaml(profile_path(n))["document_type"] for n in EXPECTED_PROFILES]
        assert len(types) == len(set(types))

    def test_coherence_dir_contains_only_expected_files(self):
        yaml_files = {p.stem for p in COHERENCE_DIR.glob("*.yaml")}
        unexpected = yaml_files - set(EXPECTED_PROFILES)
        assert not unexpected, f"Unexpected files in coherence/: {unexpected}"
