#!/usr/bin/env python3
"""Unit tests for Stage 3 Decision Rules CLI and DAG validator."""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN_DIR = SKILL_DIR / "bin"

DECISION_CLI_PATH = str(BIN_DIR / "decision-rules-cli")
VALIDATE_DAG_PATH = str(BIN_DIR / "validate-execution-dag")

loader_cli = SourceFileLoader("decision_rules_cli", DECISION_CLI_PATH)
spec_cli = importlib.util.spec_from_loader("decision_rules_cli", loader_cli)
decision_rules_cli = importlib.util.module_from_spec(spec_cli)
loader_cli.exec_module(decision_rules_cli)

loader_dag = SourceFileLoader("validate_execution_dag", VALIDATE_DAG_PATH)
spec_dag = importlib.util.spec_from_loader("validate_execution_dag", loader_dag)
validate_execution_dag = importlib.util.module_from_spec(spec_dag)
loader_dag.exec_module(validate_execution_dag)


class TestStage3Validators(unittest.TestCase):
    def setUp(self):
        self.cases_dir = SKILL_DIR / "tests" / "cases" / "stage-3"

    def load_case_json(self, case_dir, filename="execution-dag.json"):
        target_path = self.cases_dir / case_dir / filename
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data, target_path

    # --- Positive Cases ---
    def test_valid_dag_passes(self):
        data, path = self.load_case_json("s3-valid-dag")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertIn("EXECUTION_DAG_VALIDATED_CLEAN", msg)

    def test_substrate_exemption_derived_passes(self):
        data, path = self.load_case_json("s3-substrate-derived")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertIn("SUBSTRATE_EXEMPTION_DERIVED_FROM_DAG", msg)

    def test_no_persistence_slice_passes(self):
        data, path = self.load_case_json("s3-no-persistence-slice")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertIn("OPTIONAL_ADAPTER_LEG_OMITTED_VALID", msg)

    def test_substrate_work_without_full_legs_accepted_passes(self):
        data, path = self.load_case_json("substrate-work-accepted")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertIn("EXECUTION_DAG_VALIDATED_CLEAN", msg)

    # --- Error Code Cases ---
    def test_dag_cycle_rejection(self):
        data, path = self.load_case_json("dag-cycle")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "DAG_CYCLE")

    def test_dimension_unresolved_rejection(self):
        data, path = self.load_case_json("dimension-unresolved", "slice-invariants.json")
        is_valid, code, msg = decision_rules_cli.validate_decision_rules_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "DIMENSION_UNRESOLVED")

    def test_dimension_4_inapplicable_rejection(self):
        data, path = self.load_case_json("dimension-4-inapplicable", "slice-invariants.json")
        is_valid, code, msg = decision_rules_cli.validate_decision_rules_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "DIMENSION_4_INAPPLICABLE_ON_REPEATABLE_TRIGGER")

    def test_slice_1_composition_violation_rejection(self):
        data, path = self.load_case_json("slice-1-composition-violation")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "SLICE_1_COMPOSITION_VIOLATION")

    def test_horizontal_layer_rejected(self):
        data, path = self.load_case_json("horizontal-layer-rejected")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "HORIZONTAL_LAYER_REJECTED")

    def test_horizontal_layer_rejected_by_missing_legs(self):
        data, path = self.load_case_json("horizontal-missing-legs")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "HORIZONTAL_LAYER_REJECTED")

    def test_shared_substrate_declared_rejection(self):
        data, path = self.load_case_json("shared-substrate-declared")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "SHARED_SUBSTRATE_DECLARED")

    def test_impure_pure_module_rejection(self):
        data, path = self.load_case_json("impure-pure-module")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "IMPURE_PURE_MODULE")

    def test_impure_test_import_rejection(self):
        data, path = self.load_case_json("impure-test-import")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "IMPURE_TEST_IMPORT")

    def test_missing_verification_medium_rejection(self):
        data, path = self.load_case_json("missing-verification-medium")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_VERIFICATION_MEDIUM")

    def test_slice_missing_verification_script_rejection(self):
        data, path = self.load_case_json("slice-missing-verification-script")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "SLICE_MISSING_VERIFICATION_SCRIPT")

    def test_dangling_attention_return_rejection(self):
        data, path = self.load_case_json("dangling-attention-return")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "DANGLING_ATTENTION_RETURN")

    def test_concurrency_over_capacity_rejection(self):
        data, path = self.load_case_json("concurrency-over-capacity")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "CONCURRENCY_OVER_PROBED_CAPACITY")

    def test_transaction_boundary_split_rejection(self):
        data, path = self.load_case_json("transaction-boundary-split")
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "TRANSACTION_BOUNDARY_SPLIT")

    def test_milestones_validation_duplicate_id(self):
        data, path = self.load_case_json("s3-valid-dag")
        data["slices"][0]["milestones"] = [
            {"milestone_id": "M1_DUP", "verification_medium": "rendered_ui", "claims": ["c1"]},
            {"milestone_id": "M1_DUP", "verification_medium": "rendered_ui", "claims": ["c2"]}
        ]
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "DUPLICATE_MILESTONE_ID")

    def test_milestones_validation_valid(self):
        data, path = self.load_case_json("s3-valid-dag")
        data["slices"][0]["milestones"] = [
            {"milestone_id": "M1_QUERY", "verification_medium": "rendered_ui", "claims": ["c1"]}
        ]
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertTrue(is_valid)

    def test_milestones_validation_invalid_medium(self):
        data, path = self.load_case_json("s3-valid-dag")
        data["slices"][0]["milestones"] = [
            {"milestone_id": "M1_QUERY", "verification_medium": "invalid_medium_type", "claims": ["c1"]}
        ]
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "UNKNOWN_VERIFICATION_MEDIUM")

    def test_milestones_validation_empty_claims(self):
        data, path = self.load_case_json("s3-valid-dag")
        data["slices"][0]["milestones"] = [
            {"milestone_id": "M1_QUERY", "verification_medium": "rendered_ui", "claims": []}
        ]
        is_valid, code, msg = validate_execution_dag.validate_execution_dag_dict(data, path)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_GATE_ARTIFACT")


if __name__ == "__main__":
    unittest.main()
