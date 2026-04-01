"""
Injection Detector Skill Profile Tests — BossBox Atomic Step 10
===============================================================
Validates that injection_detector.yaml and document_analysis.yaml are
well-formed and contain all fields required by spec Section 9.2.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SKILLS_ROOT = Path(__file__).parents[2] / "skills" / "default"
PROFILE_PATH = SKILLS_ROOT / "injection_detector.yaml"
SCHEMA_PATH = SKILLS_ROOT / "schemas" / "document_analysis.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# injection_detector.yaml
# ---------------------------------------------------------------------------

class TestInjectionDetectorProfile:
    def test_file_exists(self):
        assert PROFILE_PATH.exists(), f"Missing: {PROFILE_PATH}"

    def test_parses_as_valid_yaml(self):
        data = load_yaml(PROFILE_PATH)
        assert isinstance(data, dict)

    def test_required_top_level_keys(self):
        data = load_yaml(PROFILE_PATH)
        for key in ("name", "version", "tier", "description", "system_prompt",
                    "output_schema", "injection_categories", "coherence_threshold"):
            assert key in data, f"Missing key: {key}"

    def test_name_is_injection_detector(self):
        data = load_yaml(PROFILE_PATH)
        assert data["name"] == "injection_detector"

    def test_tier_is_micro(self):
        data = load_yaml(PROFILE_PATH)
        assert data["tier"] == "micro"

    def test_output_schema_points_to_document_analysis(self):
        data = load_yaml(PROFILE_PATH)
        assert "document_analysis" in data["output_schema"]

    def test_system_prompt_is_non_empty_string(self):
        data = load_yaml(PROFILE_PATH)
        prompt = data["system_prompt"]
        assert isinstance(prompt, str)
        assert len(prompt.strip()) > 50, "System prompt is too short to be useful"

    def test_system_prompt_mentions_injection(self):
        data = load_yaml(PROFILE_PATH)
        assert "injection" in data["system_prompt"].lower()

    def test_system_prompt_is_human_readable(self):
        """Prompt must contain plain English sentences (not just field names)."""
        data = load_yaml(PROFILE_PATH)
        prompt = data["system_prompt"]
        # Heuristic: contains at least one sentence with a verb
        assert any(word in prompt.lower() for word in ("you are", "your job", "must", "should"))

    def test_all_five_injection_categories_present(self):
        data = load_yaml(PROFILE_PATH)
        categories = data["injection_categories"]
        expected = {
            "direct_instruction",
            "role_reassignment",
            "context_escape",
            "authority_spoofing",
            "urgency_override",
        }
        assert set(categories) == expected

    def test_coherence_threshold_is_float_between_0_and_1(self):
        data = load_yaml(PROFILE_PATH)
        threshold = data["coherence_threshold"]
        assert isinstance(threshold, float)
        assert 0.0 < threshold < 1.0

    def test_version_is_string(self):
        data = load_yaml(PROFILE_PATH)
        assert isinstance(data["version"], str)


# ---------------------------------------------------------------------------
# document_analysis.yaml (schema)
# ---------------------------------------------------------------------------

class TestDocumentAnalysisSchema:
    def test_file_exists(self):
        assert SCHEMA_PATH.exists(), f"Missing: {SCHEMA_PATH}"

    def test_parses_as_valid_yaml(self):
        data = load_yaml(SCHEMA_PATH)
        assert isinstance(data, dict)

    def test_required_top_level_keys(self):
        data = load_yaml(SCHEMA_PATH)
        for key in ("schema_name", "version", "fields"):
            assert key in data, f"Missing key: {key}"

    def test_schema_name(self):
        data = load_yaml(SCHEMA_PATH)
        assert data["schema_name"] == "document_analysis"

    def test_document_analysis_wrapper_present(self):
        data = load_yaml(SCHEMA_PATH)
        assert "document_analysis" in data["fields"]

    def _doc_fields(self) -> dict:
        data = load_yaml(SCHEMA_PATH)
        return data["fields"]["document_analysis"]["fields"]

    def test_declared_type_field_present(self):
        assert "declared_type" in self._doc_fields()

    def test_assessed_type_field_present(self):
        assert "assessed_type" in self._doc_fields()

    def test_type_match_field_present(self):
        assert "type_match" in self._doc_fields()

    def test_coherence_score_field_present(self):
        assert "coherence_score" in self._doc_fields()

    def test_injection_verdict_field_present(self):
        assert "injection_verdict" in self._doc_fields()

    def test_flagged_passages_field_present(self):
        assert "flagged_passages" in self._doc_fields()

    def test_overall_verdict_field_present(self):
        assert "overall_verdict" in self._doc_fields()

    def test_all_section_9_2_output_fields_present(self):
        """Spec Section 9.2 lists these exact output fields."""
        required = {
            "declared_type",
            "assessed_type",
            "type_match",
            "coherence_score",
            "injection_verdict",
            "flagged_passages",
            "overall_verdict",
        }
        assert required.issubset(set(self._doc_fields()))

    def test_injection_verdict_enum_values(self):
        fields = self._doc_fields()
        enum_vals = set(fields["injection_verdict"]["enum"])
        assert enum_vals == {"pass", "warn", "block"}

    def test_overall_verdict_enum_values(self):
        fields = self._doc_fields()
        enum_vals = set(fields["overall_verdict"]["enum"])
        assert enum_vals == {"pass", "warn", "block"}

    def test_coherence_score_has_min_max(self):
        fields = self._doc_fields()
        score = fields["coherence_score"]
        assert score["minimum"] == 0.0
        assert score["maximum"] == 1.0

    def test_flagged_passages_is_array(self):
        fields = self._doc_fields()
        assert fields["flagged_passages"]["type"] == "array"

    def test_flagged_passage_items_have_required_fields(self):
        fields = self._doc_fields()
        item_fields = fields["flagged_passages"]["items"]["fields"]
        for key in ("text", "category", "location"):
            assert key in item_fields, f"flagged_passages item missing field: {key}"

    def test_flagged_passage_category_enum_matches_profile(self):
        schema_data = load_yaml(SCHEMA_PATH)
        profile_data = load_yaml(PROFILE_PATH)
        schema_cats = set(
            schema_data["fields"]["document_analysis"]["fields"]
            ["flagged_passages"]["items"]["fields"]["category"]["enum"]
        )
        profile_cats = set(profile_data["injection_categories"])
        assert schema_cats == profile_cats

    def test_all_required_fields_marked_required(self):
        fields = self._doc_fields()
        for name in ("declared_type", "assessed_type", "type_match",
                     "coherence_score", "injection_verdict",
                     "flagged_passages", "overall_verdict"):
            assert fields[name].get("required") is True, f"{name} should be required"

    def test_decision_rules_present(self):
        data = load_yaml(SCHEMA_PATH)
        assert "decision_rules" in data
        assert len(data["decision_rules"]) == 3
