"""Unit tests for risk- and impact-based verification policy and execution tiers."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from lib.verification_policy import (
    TIER_1,
    TIER_2,
    TIER_3,
    SKIP_CODE_UI_ONLY,
    SKIP_CODE_SLICE_LOCAL_SUFFICIENT,
    SKIP_CODE_FEATURE_SCOPED,
    classify_file_diff,
    determine_escalation_tier,
    generate_verification_plan,
    validate_verification_plan,
)
from lib.sidecar import build_slice_sidecar, load_slice_sidecar
from lib.primitives import is_reason_meaningful


class TestVerificationPolicy(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test-vfd-policy-"))
        self.feature_dir = self.tmp_dir / "test-feature"
        self.feature_dir.mkdir(parents=True, exist_ok=True)

        # Write minimal state.json
        state_data = {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "current_state": "STAGE_4_MILESTONE_EXECUTION",
            "updated_at": "2026-08-30T10:00:00Z",
            "artifacts": {
                "stage_0_lexicon": "domain-lexicon.json",
                "stage_1_manifest": "contract-fixtures/manifest.json",
                "stage_2_scenario_matrix": "flow-simulation-matrix.json",
                "stage_3_execution_dag": "execution-dag.json",
                "stage_3_invariants": "slice-invariants.json",
                "stage_4_milestone_evidence": "milestones/evidence-summary.json",
                "stage_5_delivery_report": "delivery-report.md",
            },
        }
        with open(self.feature_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)

        # Write execution-dag.json
        dag_data = {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "slices": [
                {
                    "slice_id": "slice_ui",
                    "name": "UI Layout Slice",
                    "dependencies": [],
                    "verification_medium": "rendered_ui",
                    "legs": {
                        "trigger": {"type": "ui_interaction", "entry_point": "components/view.tsx"},
                        "core_pure_logic": {"pure_module_files": ["styles/app.css"]},
                        "output_verification": {"medium": "rendered_ui"},
                    },
                },
                {
                    "slice_id": "slice_query",
                    "name": "Query Logic Slice",
                    "dependencies": [],
                    "verification_medium": "process_exit",
                    "legs": {
                        "trigger": {"type": "cli_entry", "entry_point": "src/query_cli.py"},
                        "core_pure_logic": {"pure_module_files": ["src/query_filter.py"]},
                        "output_verification": {"medium": "process_exit"},
                    },
                },
                {
                    "slice_id": "slice_adapter",
                    "name": "External Adapter Slice",
                    "dependencies": [],
                    "verification_medium": "api_response",
                    "legs": {
                        "trigger": {"type": "api_call", "entry_point": "adapters/client.py"},
                        "core_pure_logic": {"pure_module_files": ["src/client_pure.py"]},
                        "adapter_persistence": {
                            "optional_leg_present": True,
                            "boundary_class": "NETWORK_BOUNDED",
                            "adapter_files": ["adapters/client.py"],
                        },
                        "output_verification": {"medium": "api_response"},
                    },
                },
            ],
        }
        with open(self.feature_dir / "execution-dag.json", "w", encoding="utf-8") as f:
            json.dump(dag_data, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_file_diff_classification(self):
        """Diff paths are deterministically classified into appropriate architectural categories."""
        self.assertEqual(classify_file_diff("styles/app.css"), "UI_ONLY")
        self.assertEqual(classify_file_diff("components/modal.module.scss"), "UI_ONLY")
        self.assertEqual(classify_file_diff("ui/mockups/index.html"), "UI_ONLY")

        self.assertEqual(classify_file_diff("domain-lexicon.json"), "FEATURE_SCHEMA")
        self.assertEqual(classify_file_diff("contract-fixtures/api/manifest.json"), "FEATURE_SCHEMA")
        self.assertEqual(classify_file_diff("adapters/payment_adapter.py"), "FEATURE_SCHEMA")

        self.assertEqual(classify_file_diff("lib/primitives.py"), "CORE_SHARED")
        self.assertEqual(classify_file_diff("migrations/0001_init.sql"), "CORE_SHARED")

        self.assertEqual(classify_file_diff("src/slices/query.py"), "SLICE_LOCAL")
        self.assertEqual(classify_file_diff("tests/test_query.py"), "SLICE_LOCAL")

        self.assertEqual(classify_file_diff("unknown/path/config.yaml"), "UNMAPPED")

    def test_ui_only_diff_triggers_tier_1_with_reasoned_skips(self):
        """UI/CSS-only changes select render checks and bypass backend/adversarial checks with SKIP_UI_ONLY."""
        diff_files = ["styles/app.css", "components/theme.module.css"]
        tier, tokens, warnings = determine_escalation_tier(diff_files=diff_files)

        self.assertEqual(tier, TIER_1)
        self.assertIn("UI_LAYOUT_CSS_ONLY", tokens)
        self.assertEqual(warnings, [])

        plan = generate_verification_plan(
            feature_dir=self.feature_dir,
            slice_id="slice_ui",
            diff_files=diff_files,
        )

        is_valid, token, errors = validate_verification_plan(plan)
        self.assertTrue(is_valid, f"Plan validation errors: {errors}")
        self.assertEqual(plan["escalation_tier"], TIER_1)

        # Selected checks must contain render checks
        categories = {chk["category"] for chk in plan["selected_checks"]}
        self.assertIn("render_check", categories)
        self.assertIn("browser_snapshot", categories)

        # Skipped checks must cite SKIP_UI_ONLY
        skip_codes = {sk["reason_code"] for sk in plan["skipped_checks"]}
        self.assertIn(SKIP_CODE_UI_ONLY, skip_codes)
        for sk in plan["skipped_checks"]:
            self.assertTrue(is_reason_meaningful(sk["reason_description"]))

    def test_slice_business_logic_triggers_tier_1(self):
        """Slice business logic changes select slice unit tests and BDD, bypassing full regression."""
        diff_files = ["src/slices/query.py", "tests/test_query.py"]
        tier, tokens, warnings = determine_escalation_tier(diff_files=diff_files)

        self.assertEqual(tier, TIER_1)
        self.assertIn("SLICE_BUSINESS_LOGIC_LOCAL", tokens)
        self.assertEqual(warnings, [])

        plan = generate_verification_plan(
            feature_dir=self.feature_dir,
            slice_id="slice_query",
            diff_files=diff_files,
        )
        is_valid, token, errors = validate_verification_plan(plan)
        self.assertTrue(is_valid, f"Plan validation errors: {errors}")

        categories = {chk["category"] for chk in plan["selected_checks"]}
        self.assertIn("unit_test", categories)
        self.assertIn("bdd_scenario", categories)

        skip_codes = {sk["reason_code"] for sk in plan["skipped_checks"]}
        self.assertIn(SKIP_CODE_SLICE_LOCAL_SUFFICIENT, skip_codes)

    def test_domain_schema_and_fixtures_trigger_tier_2(self):
        """Schema / lexicon / fixture edits trigger Tier 2 feature-wide verification."""
        diff_files = ["domain-lexicon.json", "contract-fixtures/manifest.json"]
        tier, tokens, warnings = determine_escalation_tier(diff_files=diff_files)

        self.assertEqual(tier, TIER_2)
        self.assertIn("FEATURE_SCHEMA_MUTATION", tokens)

        plan = generate_verification_plan(
            feature_dir=self.feature_dir,
            diff_files=diff_files,
        )
        is_valid, token, errors = validate_verification_plan(plan)
        self.assertTrue(is_valid, f"Plan validation errors: {errors}")

        categories = {chk["category"] for chk in plan["selected_checks"]}
        self.assertIn("stage_validator", categories)
        self.assertIn("projection_verifier", categories)

        skip_codes = {sk["reason_code"] for sk in plan["skipped_checks"]}
        self.assertIn(SKIP_CODE_FEATURE_SCOPED, skip_codes)

    def test_core_shared_library_triggers_tier_3(self):
        """Edits to lib/ or migrations trigger Tier 3 full regression."""
        diff_files = ["lib/primitives.py", "migrations/0002_migration.sql"]
        tier, tokens, warnings = determine_escalation_tier(diff_files=diff_files)

        self.assertEqual(tier, TIER_3)
        self.assertIn("CORE_SHARED_LIBRARY_MUTATION", tokens)

        plan = generate_verification_plan(
            feature_dir=self.feature_dir,
            diff_files=diff_files,
        )
        is_valid, token, errors = validate_verification_plan(plan)
        self.assertTrue(is_valid, f"Plan validation errors: {errors}")
        self.assertEqual(plan["escalation_tier"], TIER_3)
        self.assertEqual(plan["skipped_checks"], [])

    def test_stage_5_delivery_exit_mandatory_tier_3(self):
        """Stage 5 delivery exit strictly enforces mandatory Tier 3 gatekeeper verification."""
        plan = generate_verification_plan(
            feature_dir=self.feature_dir,
            is_stage_5_exit=True,
        )
        is_valid, token, errors = validate_verification_plan(plan)
        self.assertTrue(is_valid, f"Plan validation errors: {errors}")

        self.assertEqual(plan["escalation_tier"], TIER_3)
        self.assertTrue(plan["mandatory_gatekeeper"])

        categories = {chk["category"] for chk in plan["selected_checks"]}
        self.assertIn("gatekeeper", categories)
        self.assertIn("ui_observation", categories)
        self.assertEqual(plan["skipped_checks"], [])

    def test_unmapped_diff_conservative_escalation(self):
        """Unmapped or ambiguous file paths trigger conservative escalation to Tier 3 with recorded warning."""
        diff_files = ["scripts/untracked_patch.sh", "unknown_folder/foo.txt"]
        tier, tokens, warnings = determine_escalation_tier(diff_files=diff_files)

        self.assertEqual(tier, TIER_3)
        self.assertIn("UNMAPPED_DIFF_CONSERVATIVE_ESCALATION", tokens)
        self.assertTrue(len(warnings) > 0)
        self.assertTrue(any("untracked_patch.sh" in w for w in warnings))

        plan = generate_verification_plan(
            feature_dir=self.feature_dir,
            diff_files=diff_files,
        )
        is_valid, token, errors = validate_verification_plan(plan)
        self.assertTrue(is_valid, f"Plan validation errors: {errors}")
        self.assertEqual(plan["escalation_tier"], TIER_3)
        self.assertTrue(len(plan["warnings"]) > 0)

    def test_validate_plan_rejection_of_vacuous_skip_reasons(self):
        """validate_verification_plan rejects vacuous or non-meaningful skip reasons."""
        plan = generate_verification_plan(
            feature_dir=self.feature_dir,
            diff_files=["styles/app.css"],
        )
        # Mutate skip reason to a vacuous string
        plan["skipped_checks"][0]["reason_description"] = "ok"

        is_valid, token, errors = validate_verification_plan(plan)
        self.assertFalse(is_valid)
        self.assertEqual(token, "VERIFICATION_PLAN_INVALID")
        self.assertTrue(any("vacuous" in e for e in errors))

    def test_sidecar_embeds_verification_plan(self):
        """build_slice_sidecar correctly embeds a valid verification_plan into the sidecar."""
        sidecar = build_slice_sidecar(
            self.feature_dir,
            slice_id="slice_query",
            diff_files=["src/slices/query.py"],
        )

        self.assertIn("verification_plan", sidecar)
        vplan = sidecar["verification_plan"]
        self.assertEqual(vplan["escalation_tier"], TIER_1)

        is_valid, token, errors = validate_verification_plan(vplan)
        self.assertTrue(is_valid, f"Embedded verification plan invalid: {errors}")

        # Test loading from disk
        sidecar_file = self.feature_dir / "sidecars" / "slice_query.json"
        self.assertTrue(sidecar_file.is_file())
        loaded = load_slice_sidecar(sidecar_file)
        self.assertIn("verification_plan", loaded)
        self.assertEqual(loaded["verification_plan"]["escalation_tier"], TIER_1)


if __name__ == "__main__":
    unittest.main()
