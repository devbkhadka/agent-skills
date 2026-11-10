import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

BIN_DIR = SKILL_DIR / "bin"
PLANNER_STATE_PATH = str(BIN_DIR / "planner-state")
loader = SourceFileLoader("planner_state", PLANNER_STATE_PATH)
spec = importlib.util.spec_from_loader("planner_state", loader)
planner_state = importlib.util.module_from_spec(spec)
loader.exec_module(planner_state)

from lib.capsule import (
    create_capsule,
    evaluate_capsule,
    get_diagnostics,
    save_diagnostics,
    CAPSULE_SCHEMA_VERSION,
)
from lib.sidecar import build_slice_sidecar, load_slice_sidecar



class TestWorkerHandoffAndSidecar(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test-vfd-handoff-")
        self.feature_dir = Path(self.temp_dir) / "test-feature"
        self.feature_dir.mkdir(parents=True, exist_ok=True)

        # Initialize minimal state.json
        self.state_data = {
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
            "history": [],
        }
        with open(self.feature_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(self.state_data, f, indent=2)

        # Minimal execution-dag.json
        self.dag_data = {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "slices": [
                {
                    "slice_id": "slice_query",
                    "name": "Query Slice",
                    "role": "PRIMARY_FLOW",
                    "verification_medium": "api_response",
                    "pure_modules": ["lib/query.py"],
                    "triggers": [{"name": "search", "modality": "api"}],
                    "domain_entities": ["QueryEntity"],
                    "external_dependencies": [{"fixture_ref": "contract-fixtures/search.json"}],
                    "milestones": [{"milestone_id": "M1_QUERY"}],
                }
            ],
        }
        with open(self.feature_dir / "execution-dag.json", "w", encoding="utf-8") as f:
            json.dump(self.dag_data, f, indent=2)

        # Minimal domain-lexicon.json
        self.lex_data = {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "entities": [
                {
                    "name": "QueryEntity",
                    "provenance": "FEATURE_INTRODUCED",
                    "plain_language_summary": "Query entity summary",
                }
            ],
        }
        with open(self.feature_dir / "domain-lexicon.json", "w", encoding="utf-8") as f:
            json.dump(self.lex_data, f, indent=2)

        # Minimal slice-invariants.json
        self.inv_data = {
            "schema_version": "1.0.0",
            "invariants": [
                {
                    "invariant_id": "INV_QUERY_BOUNDS",
                    "slice_id": "slice_query",
                    "description": "Query bounds must not exceed limit",
                }
            ],
        }
        with open(self.feature_dir / "slice-invariants.json", "w", encoding="utf-8") as f:
            json.dump(self.inv_data, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_backed_diagnostics_and_compact_capsule(self):
        """Raw output is persisted to disk; capsule references it and stays compact."""
        huge_raw_output = "DEBUG TRACE LOG\n" + ("line output content verbose log data\n" * 200)
        rel_diag = save_diagnostics(self.feature_dir, "slice_query", huge_raw_output)
        self.assertTrue((self.feature_dir / rel_diag).is_file())

        retrieved = get_diagnostics(self.feature_dir, rel_diag)
        self.assertIn("DEBUG TRACE LOG", retrieved)

        # Create compact capsule
        capsule = create_capsule(
            feature_slug="test-feature",
            task_id="slice_query",
            slice_id="slice_query",
            status="completed",
            outcome="Query slice implemented and verified against search contract",
            changed_artifacts=["lib/query.py", "tests/test_query.py"],
            verification=[
                {"command": "pytest tests/test_query.py", "exit_code": 0, "output": "1 passed"}
            ],
            evidence_paths=["evidence/slice_query_capture.json"],
            raw_output=huge_raw_output,
            feature_dir=self.feature_dir,
            next_action="Advance to next vertical slice",
        )

        capsule_json = json.dumps(capsule)
        # Verify capsule payload is compact (well under 4000 characters)
        self.assertLess(len(capsule_json), 2000)
        self.assertEqual(capsule["schema_version"], CAPSULE_SCHEMA_VERSION)
        self.assertEqual(capsule["status"], "completed")
        self.assertTrue(capsule["diagnostics_ref"].startswith("diagnostics/"))

    def test_capsule_evaluation_success_with_evidence(self):
        """Capsule with valid verification and existing evidence files is accepted."""
        evidence_file = self.feature_dir / "evidence" / "slice_query_capture.json"
        evidence_file.parent.mkdir(parents=True, exist_ok=True)
        evidence_file.write_text('{"captured_artifact": "ok"}', encoding="utf-8")

        capsule = create_capsule(
            feature_slug="test-feature",
            task_id="slice_query",
            slice_id="slice_query",
            status="completed",
            outcome="Slice verified clean",
            changed_artifacts=["lib/query.py"],
            verification=[
                {"command": "pytest tests/test_query.py", "exit_code": 0, "output": "1 passed"}
            ],
            evidence_paths=["evidence/slice_query_capture.json"],
            feature_dir=self.feature_dir,
            next_action="Advance to Stage 5",
        )

        is_valid, token, details = evaluate_capsule(self.feature_dir, capsule)
        self.assertTrue(is_valid)
        self.assertEqual(token, "CAPSULE_VERIFIED")
        self.assertEqual(details["slice_id"], "slice_query")

    def test_capsule_evaluation_missing_evidence_rejected(self):
        """Capsule claiming completion without evidence file on disk is rejected with MISSING_EVIDENCE."""
        capsule = create_capsule(
            feature_slug="test-feature",
            task_id="slice_query",
            slice_id="slice_query",
            status="completed",
            outcome="Claimed complete but evidence not written",
            changed_artifacts=["lib/query.py"],
            verification=[
                {"command": "pytest tests/test_query.py", "exit_code": 0, "output": "1 passed"}
            ],
            evidence_paths=["evidence/absent_capture.json"],
            feature_dir=self.feature_dir,
            next_action="Check capture output",
        )

        is_valid, token, details = evaluate_capsule(self.feature_dir, capsule)
        self.assertFalse(is_valid)
        self.assertEqual(token, "MISSING_EVIDENCE")
        self.assertIn("evidence/absent_capture.json", details["missing_evidence_paths"])

    def test_capsule_evaluation_verification_failure_rejected(self):
        """Capsule with nonzero exit or unexpected output is rejected with VERIFICATION_FAILED."""
        evidence_file = self.feature_dir / "evidence" / "slice_query_capture.json"
        evidence_file.parent.mkdir(parents=True, exist_ok=True)
        evidence_file.write_text('{"captured_artifact": "fail"}', encoding="utf-8")

        capsule = create_capsule(
            feature_slug="test-feature",
            task_id="slice_query",
            slice_id="slice_query",
            status="completed",
            outcome="Failed test run",
            changed_artifacts=["lib/query.py"],
            verification=[
                {"command": "pytest tests/test_query.py", "exit_code": 1, "output": "1 failed"}
            ],
            evidence_paths=["evidence/slice_query_capture.json"],
            feature_dir=self.feature_dir,
            next_action="Fix test failure",
        )

        is_valid, token, details = evaluate_capsule(self.feature_dir, capsule)
        self.assertFalse(is_valid)
        self.assertEqual(token, "VERIFICATION_FAILED")
        self.assertEqual(details["exit_code"], 1)

    def test_capsule_evaluation_blocked_or_failure(self):
        """Capsule reporting blocked status returns CAPSULE_BLOCKED with blockers."""
        capsule = create_capsule(
            feature_slug="test-feature",
            task_id="slice_query",
            slice_id="slice_query",
            status="blocked",
            outcome="Regression detected: upstream contract drifted",
            changed_artifacts=[],
            verification=[],
            evidence_paths=[],
            blockers=["CONTRACT_DRIFT"],
            feature_dir=self.feature_dir,
            next_action="Roll back to STAGE_1_CONTRACT_PROBING",
        )

        is_valid, token, details = evaluate_capsule(self.feature_dir, capsule)
        self.assertFalse(is_valid)
        self.assertEqual(token, "CAPSULE_BLOCKED")
        self.assertIn("CONTRACT_DRIFT", details["blockers"])

    def test_sidecar_generation_and_loading(self):
        """Slice sidecar correctly extracts only relevant slice context."""
        sidecar = build_slice_sidecar(self.feature_dir, "slice_query")
        self.assertEqual(sidecar["feature_slug"], "test-feature")
        self.assertEqual(sidecar["slice_id"], "slice_query")
        self.assertEqual(len(sidecar["relevant_invariants"]), 1)
        self.assertEqual(sidecar["relevant_invariants"][0]["invariant_id"], "INV_QUERY_BOUNDS")
        self.assertEqual(len(sidecar["relevant_entities"]), 1)
        self.assertEqual(sidecar["relevant_entities"][0]["name"], "QueryEntity")

        # Load sidecar from file
        sidecar_file = self.feature_dir / "sidecars" / "slice_query.json"
        self.assertTrue(sidecar_file.is_file())
        loaded = load_slice_sidecar(sidecar_file)
        self.assertEqual(loaded["slice_id"], "slice_query")

    def test_sidecar_unknown_slice_raises(self):
        """Unknown slice raises ValueError."""
        with self.assertRaises(ValueError):
            build_slice_sidecar(self.feature_dir, "non_existent_slice")

    def test_deterministic_status_and_next_action(self):
        """planner-state status and next-action compute deterministic outputs."""
        class DummyArgs:
            def __init__(self, feature, dir_path, json_out=True):
                self.feature = feature
                self.dir = str(dir_path)
                self.root = None
                self.json = json_out

        # Test STAGE_4 status
        args = DummyArgs("test-feature", self.feature_dir, json_out=True)
        ret = planner_state.cmd_status(args)
        self.assertEqual(ret, 0)

        # Test STAGE_4 next-action
        ret = planner_state.cmd_next_action(args)
        self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
