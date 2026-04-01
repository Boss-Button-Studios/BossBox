"""
Document Type Coherence Profile Tests (unittest) — BossBox Atomic Step 11
=========================================================================
Stdlib unittest mirror of test_coherence_profiles.py.
Runnable with: python -m unittest tests.skills.test_coherence_profiles_unittest -v
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

COHERENCE_DIR = Path(__file__).parents[2] / "skills" / "default" / "coherence"
EXPECTED_PROFILES = ["invoice", "contract", "code_file", "email", "report"]


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def profile_path(name: str) -> Path:
    return COHERENCE_DIR / f"{name}.yaml"


def _make_profile_test_class(profile_name: str):
    """Dynamically generate a TestCase class for one coherence profile."""

    class ProfileTestCase(unittest.TestCase):

        name = profile_name

        def _data(self):
            return load_yaml(profile_path(self.name))

        def test_file_exists(self):
            self.assertTrue(profile_path(self.name).exists())

        def test_parses_as_valid_yaml(self):
            self.assertIsInstance(self._data(), dict)

        def test_required_top_level_keys(self):
            data = self._data()
            for key in ("document_type", "version", "description",
                        "expected_elements", "suspicious_if_present",
                        "coherence_threshold"):
                self.assertIn(key, data)

        def test_document_type_matches_filename(self):
            self.assertEqual(self._data()["document_type"], self.name)

        def test_at_least_five_expected_elements(self):
            self.assertGreaterEqual(len(self._data()["expected_elements"]), 5)

        def test_expected_elements_are_non_empty_strings(self):
            for elem in self._data()["expected_elements"]:
                self.assertIsInstance(elem, str)
                self.assertTrue(elem.strip())

        def test_at_least_three_suspicious_patterns(self):
            self.assertGreaterEqual(len(self._data()["suspicious_if_present"]), 3)

        def test_suspicious_patterns_are_non_empty_strings(self):
            for pat in self._data()["suspicious_if_present"]:
                self.assertIsInstance(pat, str)
                self.assertTrue(pat.strip())

        def test_coherence_threshold_is_float_in_range(self):
            t = self._data()["coherence_threshold"]
            self.assertIsInstance(t, float)
            self.assertGreater(t, 0.0)
            self.assertLess(t, 1.0)

        def test_version_is_string(self):
            self.assertIsInstance(self._data()["version"], str)

        def test_description_is_non_empty_string(self):
            desc = self._data()["description"]
            self.assertIsInstance(desc, str)
            self.assertGreater(len(desc.strip()), 10)

        def test_no_duplicate_expected_elements(self):
            elements = self._data()["expected_elements"]
            self.assertEqual(len(elements), len(set(elements)))

        def test_no_duplicate_suspicious_patterns(self):
            patterns = self._data()["suspicious_if_present"]
            self.assertEqual(len(patterns), len(set(patterns)))

        def test_suspicious_patterns_include_ai_instruction_signal(self):
            combined = " ".join(self._data()["suspicious_if_present"]).lower()
            has_signal = any(
                term in combined
                for term in ("ai", "model", "prompt", "instruction", "override", "ignore")
            )
            self.assertTrue(has_signal)

    ProfileTestCase.__name__ = f"Test{profile_name.title().replace('_', '')}Profile"
    ProfileTestCase.__qualname__ = ProfileTestCase.__name__
    return ProfileTestCase


# Register one TestCase class per profile
for _name in EXPECTED_PROFILES:
    _cls = _make_profile_test_class(_name)
    globals()[_cls.__name__] = _cls


class TestCoherenceProfileSetUnittest(unittest.TestCase):

    def test_all_five_profiles_exist(self):
        missing = [n for n in EXPECTED_PROFILES if not profile_path(n).exists()]
        self.assertFalse(missing)

    def test_all_document_types_are_unique(self):
        types = [load_yaml(profile_path(n))["document_type"] for n in EXPECTED_PROFILES]
        self.assertEqual(len(types), len(set(types)))

    def test_coherence_dir_contains_only_expected_files(self):
        yaml_files = {p.stem for p in COHERENCE_DIR.glob("*.yaml")}
        unexpected = yaml_files - set(EXPECTED_PROFILES)
        self.assertFalse(unexpected)


if __name__ == "__main__":
    unittest.main()
