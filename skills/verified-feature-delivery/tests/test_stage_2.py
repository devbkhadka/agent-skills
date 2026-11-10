#!/usr/bin/env python3
"""Unit tests for Stage 2 scenario simulation validator and projection verifier."""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN_DIR = SKILL_DIR / "bin"

VALIDATE_MATRIX_PATH = str(BIN_DIR / "validate-scenario-matrix")
VERIFY_PROJ_PATH = str(BIN_DIR / "verify-projections")

loader_matrix = SourceFileLoader("validate_scenario_matrix", VALIDATE_MATRIX_PATH)
spec_matrix = importlib.util.spec_from_loader("validate_scenario_matrix", loader_matrix)
validate_scenario_matrix = importlib.util.module_from_spec(spec_matrix)
loader_matrix.exec_module(validate_scenario_matrix)

loader_proj = SourceFileLoader("verify_projections", VERIFY_PROJ_PATH)
spec_proj = importlib.util.spec_from_loader("verify_projections", loader_proj)
verify_projections = importlib.util.module_from_spec(spec_proj)
loader_proj.exec_module(verify_projections)


class TestStage2Validators(unittest.TestCase):
    def setUp(self):
        self.cases_dir = SKILL_DIR / "tests" / "cases" / "stage-2"

    def load_case_matrix(self, case_name):
        matrix_path = self.cases_dir / case_name / "scenario-matrix.json"
        with open(matrix_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data

    def test_valid_matrix_passes(self):
        data = self.load_case_matrix("valid")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "SCENARIO_MATRIX_VALIDATED_CLEAN")

    def test_branch_class_declared_inapplicable_passes(self):
        data = self.load_case_matrix("branch-class-inapplicable")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "SCENARIO_MATRIX_VALIDATED_CLEAN")

    def test_hanging_state_rejection(self):
        data = self.load_case_matrix("hanging-state")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "HANGING_STATE")

    def test_missing_discriminator_rejection(self):
        data = self.load_case_matrix("missing-discriminator")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_DISCRIMINATOR")

    def test_missing_evidence_key_rejection(self):
        data = self.load_case_matrix("missing-evidence-key")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_EVIDENCE_KEY")

    def test_branch_class_unaddressed_rejection(self):
        data = self.load_case_matrix("branch-class-unaddressed")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "BRANCH_CLASS_UNADDRESSED")

    def test_branch_inapplicable_unreasoned_rejection(self):
        data = self.load_case_matrix("branch-inapplicable-unreasoned")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "BRANCH_INAPPLICABLE_UNREASONED")

    def test_group_b_required_on_repeatable_trigger_rejection(self):
        data = self.load_case_matrix("group-b-required-on-repeatable-trigger")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "GROUP_B_REQUIRED_ON_REPEATABLE_TRIGGER")

    def test_supersession_not_materialised_rejection(self):
        data = self.load_case_matrix("supersession-not-materialised")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "SUPERSESSION_NOT_MATERIALISED")

    def test_discriminator_collision_advisory_exits_zero(self):
        data = self.load_case_matrix("discriminator-collision-advisory")
        exit_code, output_msg = verify_projections.check_stage_2_projections(data)
        self.assertEqual(exit_code, 0)
        self.assertIn("DISCRIMINATOR_COLLISION_ADVISORY", output_msg)
        self.assertIn("[Decision Needed]", output_msg)

    def test_projections_clean_verified(self):
        data = self.load_case_matrix("valid")
        exit_code, output_msg = verify_projections.check_stage_2_projections(data)
        self.assertEqual(exit_code, 0)
        self.assertIn("PROJECTIONS_VERIFIED_STAGE_2", output_msg)


    def test_unknown_state_attribute_rejection(self):
        data = self.load_case_matrix("unknown-state-attribute")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "UNKNOWN_STATE_ATTRIBUTE")

    def test_state_optional_keys_accepted_passes(self):
        data = self.load_case_matrix("state-optional-keys-accepted")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "SCENARIO_MATRIX_VALIDATED_CLEAN")

    def test_dangling_transition_target_rejection(self):
        data = self.load_case_matrix("dangling-transition-target")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "DANGLING_TRANSITION_TARGET")

    def test_self_loop_only_egress_rejection(self):
        data = self.load_case_matrix("self-loop-only-egress")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "SELF_LOOP_ONLY_EGRESS")

    def test_terminal_state_has_egress_rejection(self):
        data = self.load_case_matrix("terminal-state-has-egress")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "TERMINAL_STATE_HAS_EGRESS")

    def test_invalid_branch_status_token_rejection(self):
        data = self.load_case_matrix("invalid-branch-status-token")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "INVALID_BRANCH_STATUS_TOKEN")

    def test_unknown_actor_attribute_rejection(self):
        data = self.load_case_matrix("unknown-actor-attribute")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "UNKNOWN_ACTOR_ATTRIBUTE")

    def test_missing_discriminator_empty_values_rejection(self):
        data = self.load_case_matrix("s2-missing-discriminator-empty-values")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_DISCRIMINATOR")

    def test_branch_inapplicable_unreasoned_single_char_rejection(self):
        data = self.load_case_matrix("s2-branch-inapplicable-unreasoned-single-char")
        is_valid, code, msg = validate_scenario_matrix.validate_scenario_matrix_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "BRANCH_INAPPLICABLE_UNREASONED")


if __name__ == "__main__":
    unittest.main()

