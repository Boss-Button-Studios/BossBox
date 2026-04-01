"""
Injection Detector Skill Profile Tests (unittest) — BossBox Atomic Step 10
==========================================================================
Stdlib unittest mirror of test_injection_detector_profile.py.
Runnable with: python -m unittest tests.skills.test_injection_detector_profile_unittest -v
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

SKILLS_ROOT = Path(__file__).parents[2] / "skills" / "default"
PROFILE_PATH = SKILLS_ROOT / "injection_detector.yaml"
SCHEMA_PATH = SKILLS_ROOT / "schemas" / "document_analysis.yaml"


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


class TestInjectionDetectorProfileUnittest(unittest.TestCase):

    def test_profile_file_exists(self):
        self.assertTrue(PROFILE_PATH.exists())

    def test_profile_parses_as_valid_yaml(self):
        data = load_yaml(PROFILE_PATH)
        self.assertIsInstance(data, dict)

    def test_profile_required_top_level_keys(self):
        data = load_yaml(PROFILE_PATH)
        for key in ("name", "version", "tier", "description", "system_prompt",
                    "output_schema", "injection_categories", "coherence_threshold"):
            self.assertIn(key, data)

    def test_profile_name(self):
        self.assertEqual(load_yaml(PROFILE_PATH)["name"], "injection_detector")

    def test_profile_tier_is_micro(self):
        self.assertEqual(load_yaml(PROFILE_PATH)["tier"], "micro")

    def test_profile_output_schema_reference(self):
        data = load_yaml(PROFILE_PATH)
        self.assertIn("document_analysis", data["output_schema"])

    def test_profile_system_prompt_non_empty(self):
        prompt = load_yaml(PROFILE_PATH)["system_prompt"]
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt.strip()), 50)

    def test_profile_system_prompt_mentions_injection(self):
        prompt = load_yaml(PROFILE_PATH)["system_prompt"]
        self.assertIn("injection", prompt.lower())

    def test_profile_system_prompt_human_readable(self):
        prompt = load_yaml(PROFILE_PATH)["system_prompt"].lower()
        self.assertTrue(
            any(word in prompt for word in ("you are", "your job", "must", "should"))
        )

    def test_profile_five_injection_categories(self):
        cats = set(load_yaml(PROFILE_PATH)["injection_categories"])
        expected = {
            "direct_instruction", "role_reassignment", "context_escape",
            "authority_spoofing", "urgency_override",
        }
        self.assertEqual(cats, expected)

    def test_profile_coherence_threshold(self):
        threshold = load_yaml(PROFILE_PATH)["coherence_threshold"]
        self.assertIsInstance(threshold, float)
        self.assertGreater(threshold, 0.0)
        self.assertLess(threshold, 1.0)


class TestDocumentAnalysisSchemaUnittest(unittest.TestCase):

    def _schema(self) -> dict:
        return load_yaml(SCHEMA_PATH)

    def _doc_fields(self) -> dict:
        return self._schema()["fields"]["document_analysis"]["fields"]

    def test_schema_file_exists(self):
        self.assertTrue(SCHEMA_PATH.exists())

    def test_schema_parses_as_valid_yaml(self):
        self.assertIsInstance(self._schema(), dict)

    def test_schema_required_top_level_keys(self):
        data = self._schema()
        for key in ("schema_name", "version", "fields"):
            self.assertIn(key, data)

    def test_schema_name(self):
        self.assertEqual(self._schema()["schema_name"], "document_analysis")

    def test_document_analysis_wrapper(self):
        self.assertIn("document_analysis", self._schema()["fields"])

    def test_all_section_9_2_fields_present(self):
        required = {
            "declared_type", "assessed_type", "type_match",
            "coherence_score", "injection_verdict",
            "flagged_passages", "overall_verdict",
        }
        self.assertTrue(required.issubset(set(self._doc_fields())))

    def test_injection_verdict_enum(self):
        enum_vals = set(self._doc_fields()["injection_verdict"]["enum"])
        self.assertEqual(enum_vals, {"pass", "warn", "block"})

    def test_overall_verdict_enum(self):
        enum_vals = set(self._doc_fields()["overall_verdict"]["enum"])
        self.assertEqual(enum_vals, {"pass", "warn", "block"})

    def test_coherence_score_bounds(self):
        score = self._doc_fields()["coherence_score"]
        self.assertEqual(score["minimum"], 0.0)
        self.assertEqual(score["maximum"], 1.0)

    def test_flagged_passages_is_array(self):
        self.assertEqual(self._doc_fields()["flagged_passages"]["type"], "array")

    def test_flagged_passage_items_fields(self):
        item_fields = self._doc_fields()["flagged_passages"]["items"]["fields"]
        for key in ("text", "category", "location"):
            self.assertIn(key, item_fields)

    def test_category_enum_matches_profile_categories(self):
        schema_cats = set(
            self._doc_fields()["flagged_passages"]["items"]["fields"]["category"]["enum"]
        )
        profile_cats = set(load_yaml(PROFILE_PATH)["injection_categories"])
        self.assertEqual(schema_cats, profile_cats)

    def test_all_required_fields_marked_required(self):
        for name in ("declared_type", "assessed_type", "type_match",
                     "coherence_score", "injection_verdict",
                     "flagged_passages", "overall_verdict"):
            self.assertTrue(self._doc_fields()[name].get("required"))

    def test_decision_rules_present(self):
        data = self._schema()
        self.assertIn("decision_rules", data)
        self.assertEqual(len(data["decision_rules"]), 3)


if __name__ == "__main__":
    unittest.main()
