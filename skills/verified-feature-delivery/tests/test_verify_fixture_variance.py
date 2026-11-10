#!/usr/bin/env python3
"""Unit tests for Stage 1 boundary manifest and fixture variance validator."""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN_DIR = SKILL_DIR / "bin"
VERIFY_FIXTURE_PATH = str(BIN_DIR / "verify-fixture-variance")

loader = SourceFileLoader("verify_fixture_variance", VERIFY_FIXTURE_PATH)
spec = importlib.util.spec_from_loader("verify_fixture_variance", loader)
verify_fixture_variance = importlib.util.module_from_spec(spec)
loader.exec_module(verify_fixture_variance)


class TestVerifyFixtureVariance(unittest.TestCase):
    def setUp(self):
        self.cases_dir = SKILL_DIR / "tests" / "cases" / "stage-1"

    def load_case_manifest(self, case_name):
        manifest_path = self.cases_dir / case_name / "manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data, manifest_path.parent

    def test_valid_manifest_passes(self):
        data, manifest_dir = self.load_case_manifest("valid")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "BOUNDARY_MANIFEST_VALIDATED_CLEAN")

    def test_provisional_reprobe_passes(self):
        data, manifest_dir = self.load_case_manifest("provisional-reprobe")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "BOUNDARY_MANIFEST_VALIDATED_CLEAN")

    def test_partition_cell_of_one_sample_passes(self):
        data, manifest_dir = self.load_case_manifest("partition-cell-of-one-sample")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "BOUNDARY_MANIFEST_VALIDATED_CLEAN")

    def test_partition_derived_without_declared_key_passes(self):
        data, manifest_dir = self.load_case_manifest("partition-derived-without-declared-key")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "BOUNDARY_MANIFEST_VALIDATED_CLEAN")

    def test_manifest_class_missing_rejection(self):
        data, manifest_dir = self.load_case_manifest("manifest-class-missing")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MANIFEST_CLASS_MISSING")

    def test_manifest_absence_unreasoned_rejection(self):
        data, manifest_dir = self.load_case_manifest("manifest-absence-unreasoned")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MANIFEST_ABSENCE_UNREASONED")

    def test_property_verdict_missing_rejection(self):
        data, manifest_dir = self.load_case_manifest("property-verdict-missing")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "PROPERTY_VERDICT_MISSING")

    def test_missing_success_sample_rejection(self):
        data, manifest_dir = self.load_case_manifest("missing-success-sample")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_SUCCESS_SAMPLE")

    def test_empty_failure_envelope_census_rejection(self):
        data, manifest_dir = self.load_case_manifest("empty-failure-envelope-census")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "EMPTY_FAILURE_ENVELOPE_CENSUS")

    def test_missing_fixture_status_rejection(self):
        data, manifest_dir = self.load_case_manifest("missing-fixture-status")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_FIXTURE_STATUS")

    def test_provisional_without_reprobe_rejection(self):
        data, manifest_dir = self.load_case_manifest("provisional-without-reprobe")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "PROVISIONAL_WITHOUT_REPROBE")

    def test_unbounded_region_unpartitioned_rejection(self):
        data, manifest_dir = self.load_case_manifest("unbounded-region-unpartitioned")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "UNBOUNDED_REGION_UNPARTITIONED")

    def test_variance_census_homogeneous_rejection(self):
        data, manifest_dir = self.load_case_manifest("variance-census-homogeneous")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "VARIANCE_CENSUS_HOMOGENEOUS")

    def test_partition_key_incomplete_rejection(self):
        data, manifest_dir = self.load_case_manifest("partition-key-incomplete")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "PARTITION_KEY_INCOMPLETE")

    def test_partition_key_unjustified_rejection(self):
        data, manifest_dir = self.load_case_manifest("partition-key-unjustified")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "PARTITION_KEY_UNJUSTIFIED")

    def test_harness_fidelity_drift_rejection(self):
        data, manifest_dir = self.load_case_manifest("harness-fidelity-drift")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "HARNESS_FIDELITY_DRIFT")

    def test_variance_cell_uncovered_rejection(self):
        data, manifest_dir = self.load_case_manifest("variance-cell-uncovered")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "VARIANCE_CELL_UNCOVERED")

    def test_variance_sample_floor_unmet_rejection(self):
        data, manifest_dir = self.load_case_manifest("variance-sample-floor-unmet")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "VARIANCE_SAMPLE_FLOOR_UNMET")

    def test_homogeneous_inside_declared_region_rejection(self):
        data, manifest_dir = self.load_case_manifest("s1-homogeneous-inside-declared-region")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "VARIANCE_CENSUS_HOMOGENEOUS")

    def test_manifest_absence_unreasoned_single_char_rejection(self):
        data, manifest_dir = self.load_case_manifest("s1-manifest-absence-unreasoned-single-char")
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MANIFEST_ABSENCE_UNREASONED")

    def test_meaningful_prose_absence_reason_passes(self):
        data, manifest_dir = self.load_case_manifest("valid")
        # Ensure a NOT_PRESENT row with proper prose passes
        is_valid, code, msg = verify_fixture_variance.validate_manifest_and_fixtures(data, manifest_dir)
        self.assertTrue(is_valid)
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
