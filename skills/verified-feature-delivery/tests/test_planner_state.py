#!/usr/bin/env python3
"""Unit tests for planner-state state management and transition engine."""

import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# Import planner-state executable via SourceFileLoader
TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN_DIR = SKILL_DIR / "bin"
PLANNER_STATE_PATH = str(BIN_DIR / "planner-state")

loader = SourceFileLoader("planner_state", PLANNER_STATE_PATH)
spec = importlib.util.spec_from_loader("planner_state", loader)
planner_state = importlib.util.module_from_spec(spec)
loader.exec_module(planner_state)


class TestPlannerState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.feature_dir = Path(self.tmpdir.name) / "test-feature"
        self.feature_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_init_creates_state_json_at_uninitialized(self):
        parser = planner_state.build_parser()
        args = parser.parse_args(["init", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        ret = planner_state.cmd_init(args)
        self.assertEqual(ret, 0)

        state_file = self.feature_dir / "state.json"
        self.assertTrue(state_file.is_file())
        with open(state_file, "r") as f:
            data = json.load(f)
        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertEqual(data["feature_slug"], "test-feature")
        self.assertEqual(data["current_state"], "UNINITIALIZED")
        self.assertEqual(len(data["history"]), 1)
        self.assertEqual(data["history"][0]["transition_type"], "INIT")

    def test_show_displays_state(self):
        parser = planner_state.build_parser()
        init_args = parser.parse_args(["init", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        planner_state.cmd_init(init_args)

        show_args = parser.parse_args(["show", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            ret = planner_state.cmd_show(show_args)
            self.assertEqual(ret, 0)
            output = mock_stdout.getvalue()
            self.assertIn("UNINITIALIZED", output)

    def test_advance_legal_transition_with_exit_artifact(self):
        parser = planner_state.build_parser()
        init_args = parser.parse_args(["init", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        planner_state.cmd_init(init_args)

        # Advance UNINITIALIZED -> STAGE_0_DOMAIN_INGESTION (no artifact required)
        adv_0 = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_0_DOMAIN_INGESTION"])
        ret_0 = planner_state.cmd_advance(adv_0)
        self.assertEqual(ret_0, 0)

        state = planner_state.load_state(self.feature_dir)
        self.assertEqual(state["current_state"], "STAGE_0_DOMAIN_INGESTION")

        # Create required Stage 0 exit artifact: domain-lexicon.json and required docs
        (self.feature_dir / "domain-lexicon.json").write_text("{}", encoding="utf-8")
        docs_dir = self.feature_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "ubiquitous-language-glossary.md").write_text("# Glossary", encoding="utf-8")
        (docs_dir / "domain-entity-map.md").write_text("# Entity Map", encoding="utf-8")

        # Advance STAGE_0_DOMAIN_INGESTION -> STAGE_1_CONTRACT_PROBING
        adv_1 = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_1_CONTRACT_PROBING"])
        ret_1 = planner_state.cmd_advance(adv_1)
        self.assertEqual(ret_1, 0)

        state = planner_state.load_state(self.feature_dir)
        self.assertEqual(state["current_state"], "STAGE_1_CONTRACT_PROBING")
        self.assertEqual(state["history"][-1]["gate_artifact_verified"], "domain-lexicon.json")

    def test_advance_missing_stage_document_raises_code(self):
        parser = planner_state.build_parser()
        init_args = parser.parse_args(["init", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        planner_state.cmd_init(init_args)

        # Advance to STAGE_0
        adv_0 = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_0_DOMAIN_INGESTION"])
        planner_state.cmd_advance(adv_0)

        # Write JSON artifact but omit docs
        (self.feature_dir / "domain-lexicon.json").write_text("{}", encoding="utf-8")

        adv_1 = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_1_CONTRACT_PROBING"])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret = planner_state.cmd_advance(adv_1)
            self.assertNotEqual(ret, 0)
            combined = mock_stdout.getvalue() + mock_stderr.getvalue()
            self.assertIn("MISSING_STAGE_DOCUMENT", combined)

    def test_advance_stale_stage_document_raises_code(self):
        import time
        parser = planner_state.build_parser()
        init_args = parser.parse_args(["init", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        planner_state.cmd_init(init_args)

        # Advance to STAGE_0
        adv_0 = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_0_DOMAIN_INGESTION"])
        planner_state.cmd_advance(adv_0)

        # Write older docs
        docs_dir = self.feature_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        g_doc = docs_dir / "ubiquitous-language-glossary.md"
        e_doc = docs_dir / "domain-entity-map.md"
        g_doc.write_text("# Glossary", encoding="utf-8")
        e_doc.write_text("# Entity Map", encoding="utf-8")

        old_time = time.time() - 100
        os.utime(g_doc, (old_time, old_time))
        os.utime(e_doc, (old_time, old_time))

        # Write newer JSON artifact
        json_file = self.feature_dir / "domain-lexicon.json"
        json_file.write_text("{}", encoding="utf-8")
        now_time = time.time()
        os.utime(json_file, (now_time, now_time))

        adv_1 = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_1_CONTRACT_PROBING"])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret = planner_state.cmd_advance(adv_1)
            self.assertNotEqual(ret, 0)
            combined = mock_stdout.getvalue() + mock_stderr.getvalue()
            self.assertIn("STALE_STAGE_DOCUMENT", combined)


    def test_advance_missing_gate_artifact_raises_code(self):
        parser = planner_state.build_parser()
        init_args = parser.parse_args(["init", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        planner_state.cmd_init(init_args)

        # Move to STAGE_0
        adv_0 = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_0_DOMAIN_INGESTION"])
        planner_state.cmd_advance(adv_0)

        # Ensure domain-lexicon.json does NOT exist
        lexicon_file = self.feature_dir / "domain-lexicon.json"
        if lexicon_file.exists():
            lexicon_file.unlink()

        # Try to advance to STAGE_1 without exit artifact
        adv_1 = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_1_CONTRACT_PROBING"])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret_1 = planner_state.cmd_advance(adv_1)
            self.assertNotEqual(ret_1, 0)
            combined = mock_stdout.getvalue() + mock_stderr.getvalue()
            self.assertIn("MISSING_GATE_ARTIFACT", combined)

    def test_advance_illegal_transition_raises_code(self):
        parser = planner_state.build_parser()
        init_args = parser.parse_args(["init", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        planner_state.cmd_init(init_args)

        # Try illegal transition: UNINITIALIZED -> STAGE_3_VERTICAL_SLICING
        adv_illegal = parser.parse_args(["advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", "STAGE_3_VERTICAL_SLICING"])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret = planner_state.cmd_advance(adv_illegal)
            self.assertNotEqual(ret, 0)
            combined = mock_stdout.getvalue() + mock_stderr.getvalue()
            self.assertIn("INVALID_STAGE_TRANSITION", combined)

    def test_state_assertion_mismatch_on_advance_raises_code(self):
        parser = planner_state.build_parser()
        init_args = parser.parse_args(["init", "--feature", "test-feature", "--dir", str(self.feature_dir)])
        planner_state.cmd_init(init_args)

        # Recorded state is UNINITIALIZED. Assert --from STAGE_1_CONTRACT_PROBING
        adv_mismatch = parser.parse_args([
            "advance",
            "--feature", "test-feature",
            "--dir", str(self.feature_dir),
            "--from", "STAGE_1_CONTRACT_PROBING",
            "--to", "STAGE_2_SCENARIO_SIMULATION"
        ])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret = planner_state.cmd_advance(adv_mismatch)
            self.assertNotEqual(ret, 0)
            combined = mock_stdout.getvalue() + mock_stderr.getvalue()
            self.assertIn("STATE_ASSERTION_MISMATCH", combined)

    def test_uninitialized_feature_refused_on_advance(self):
        parser = planner_state.build_parser()
        non_existent_dir = self.feature_dir / "never-init"
        adv_no_init = parser.parse_args([
            "advance",
            "--feature", "never-init",
            "--dir", str(non_existent_dir),
            "--to", "STAGE_0_DOMAIN_INGESTION"
        ])
        ret = planner_state.cmd_advance(adv_no_init)
        self.assertNotEqual(ret, 0)

    def test_rollback_stage_4_regression_edges(self):
        parser = planner_state.build_parser()

        # Set up state at STAGE_4_MILESTONE_EXECUTION
        state_data = {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "current_state": "STAGE_4_MILESTONE_EXECUTION",
            "updated_at": planner_state.now_iso_utc(),
            "artifacts": dict(planner_state.DEFAULT_ARTIFACT_MAP),
            "history": [],
        }
        planner_state.save_state(self.feature_dir, state_data)

        # 1. Rollback to STAGE_2 with PROJECTION_COLLISION
        rb_args = parser.parse_args([
            "rollback",
            "--feature", "test-feature",
            "--dir", str(self.feature_dir),
            "--to", "STAGE_2_SCENARIO_SIMULATION",
            "--cause", "PROJECTION_COLLISION"
        ])
        ret = planner_state.cmd_rollback(rb_args)
        self.assertEqual(ret, 0)

        state = planner_state.load_state(self.feature_dir)
        self.assertEqual(state["current_state"], "STAGE_2_SCENARIO_SIMULATION")
        self.assertEqual(state["history"][-1]["rollback_cause"], "PROJECTION_COLLISION")
        self.assertIsNone(state["artifacts"]["stage_4_milestone_evidence"])

    def test_state_assertion_mismatch_on_rollback_raises_code(self):
        parser = planner_state.build_parser()
        state_data = {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "current_state": "STAGE_4_MILESTONE_EXECUTION",
            "updated_at": planner_state.now_iso_utc(),
            "artifacts": dict(planner_state.DEFAULT_ARTIFACT_MAP),
            "history": [],
        }
        planner_state.save_state(self.feature_dir, state_data)

        rb_mismatch = parser.parse_args([
            "rollback",
            "--feature", "test-feature",
            "--dir", str(self.feature_dir),
            "--from", "STAGE_3_VERTICAL_SLICING",
            "--to", "STAGE_2_SCENARIO_SIMULATION",
            "--cause", "PROJECTION_COLLISION"
        ])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret = planner_state.cmd_rollback(rb_mismatch)
            self.assertNotEqual(ret, 0)
            combined = mock_stdout.getvalue() + mock_stderr.getvalue()
            self.assertIn("STATE_ASSERTION_MISMATCH", combined)

    def test_rollback_invalid_cause_raises_code(self):
        parser = planner_state.build_parser()
        state_data = {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "current_state": "STAGE_4_MILESTONE_EXECUTION",
            "updated_at": planner_state.now_iso_utc(),
            "artifacts": dict(planner_state.DEFAULT_ARTIFACT_MAP),
            "history": [],
        }
        planner_state.save_state(self.feature_dir, state_data)

        rb_invalid = parser.parse_args([
            "rollback",
            "--feature", "test-feature",
            "--dir", str(self.feature_dir),
            "--to", "STAGE_2_SCENARIO_SIMULATION",
            "--cause", "UNKNOWN_REASON"
        ])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret = planner_state.cmd_rollback(rb_invalid)
            self.assertNotEqual(ret, 0)
            combined = mock_stdout.getvalue() + mock_stderr.getvalue()
            self.assertIn("INVALID_STAGE_TRANSITION", combined)

    def test_advance_stage_5_delivery_to_completed_gated_on_evidence_verification(self):
        parser = planner_state.build_parser()
        state_data = {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "current_state": "STAGE_5_DELIVERY",
            "updated_at": planner_state.now_iso_utc(),
            "artifacts": dict(planner_state.DEFAULT_ARTIFACT_MAP),
            "history": [],
        }
        planner_state.save_state(self.feature_dir, state_data)

        # Write delivery-report.md exit artifact
        (self.feature_dir / "delivery-report.md").write_text("# Delivery Report", encoding="utf-8")

        # Write invalid delivery-report.json and execution-dag.json
        (self.feature_dir / "delivery-report.json").write_text("{}", encoding="utf-8")
        (self.feature_dir / "execution-dag.json").write_text("{}", encoding="utf-8")

        adv_completed = parser.parse_args([
            "advance",
            "--feature", "test-feature",
            "--dir", str(self.feature_dir),
            "--to", "COMPLETED"
        ])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret = planner_state.cmd_advance(adv_completed)
            self.assertNotEqual(ret, 0)
            combined = mock_stdout.getvalue() + mock_stderr.getvalue()
            self.assertIn("MISSING_GATE_ARTIFACT", combined)


RECORD_APPROVAL_PATH = str(BIN_DIR / "record-approval")
EMPTY_SCOPE_DIGEST = __import__("hashlib").sha256(b"{}").hexdigest()


class TestEntityBoundariesApproval(unittest.TestCase):
    """ENTITY_BOUNDARIES guards the lexicon's boundaries, and an interrupted invalidation recovers."""

    LEXICON = {
        "schema_version": "1.0.0",
        "feature_slug": "test-feature",
        "entity_boundaries": [{"boundary_id": "b_desk", "name": "Desk", "entities": ["Session"]}],
        "entity_relationships": [{"from_entity": "Session", "to_entity": "Desk", "kind": "belongs_to"}],
        "simulated_dependencies": [],
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.feature_dir = Path(self.tmpdir.name) / "test-feature"
        self.feature_dir.mkdir(parents=True)
        self.parser = planner_state.build_parser()

    def tearDown(self):
        self.tmpdir.cleanup()

    def set_state(self, current_state):
        planner_state.save_state(self.feature_dir, {
            "schema_version": "1.0.0",
            "feature_slug": "test-feature",
            "current_state": current_state,
            "updated_at": planner_state.now_iso_utc(),
            "artifacts": dict(planner_state.DEFAULT_ARTIFACT_MAP),
            "history": [],
        })

    def write_lexicon(self, lexicon):
        (self.feature_dir / "domain-lexicon.json").write_text(json.dumps(lexicon), encoding="utf-8")

    def approve(self):
        import subprocess
        return subprocess.run(
            [sys.executable, RECORD_APPROVAL_PATH, "--feature", "test-feature",
             "--dir", str(self.feature_dir), "--milestone", "ENTITY_BOUNDARIES",
             "--decided-by", "reviewer@example.com"],
            capture_output=True, text=True,
        )

    def advance(self, to_state):
        args = self.parser.parse_args([
            "advance", "--feature", "test-feature", "--dir", str(self.feature_dir), "--to", to_state,
        ])
        with patch("sys.stdout", new_callable=io.StringIO) as out, \
             patch("sys.stderr", new_callable=io.StringIO) as err:
            ret = planner_state.cmd_advance(args)
        return ret, out.getvalue() + err.getvalue()

    def ledger_record(self):
        ledger = json.loads((self.feature_dir / "approvals.json").read_text(encoding="utf-8"))
        return planner_state.find_approval(ledger, "ENTITY_BOUNDARIES")

    def current_state(self):
        return planner_state.load_state(self.feature_dir)["current_state"]

    def test_approval_digests_the_lexicon_boundaries(self):
        self.set_state("AWAITING_ENTITY_BOUNDARIES_APPROVAL")
        self.write_lexicon(self.LEXICON)
        res = self.approve()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        record = self.ledger_record()
        self.assertEqual(record["artifact_path"], "domain-lexicon.json")
        self.assertEqual(record["status"], "APPROVED")
        self.assertNotEqual(record["approved_digest"], EMPTY_SCOPE_DIGEST)

    def test_an_empty_scope_is_refused_and_records_nothing(self):
        self.set_state("AWAITING_ENTITY_BOUNDARIES_APPROVAL")
        # The guarded keys live in the lexicon; a matrix carrying them does not count.
        self.write_lexicon({"schema_version": "1.0.0", "feature_slug": "test-feature",
                            "entity_boundaries": [], "entity_relationships": []})
        (self.feature_dir / "flow-simulation-matrix.json").write_text(
            json.dumps({"entity_boundaries": self.LEXICON["entity_boundaries"]}), encoding="utf-8")
        res = self.approve()
        self.assertEqual(res.returncode, 1)
        self.assertIn("EMPTY_APPROVAL_SCOPE", res.stdout)
        self.assertFalse((self.feature_dir / "approvals.json").exists())

    def test_a_lexicon_boundary_edit_after_approval_goes_stale(self):
        self.set_state("AWAITING_ENTITY_BOUNDARIES_APPROVAL")
        self.write_lexicon(self.LEXICON)
        self.assertEqual(self.approve().returncode, 0)
        ret, output = self.advance("STAGE_3_VERTICAL_SLICING")
        self.assertEqual(ret, 0, output)

        edited = json.loads(json.dumps(self.LEXICON))
        edited["entity_boundaries"][0]["entities"].append("Workstream")
        self.write_lexicon(edited)
        ret, output = self.advance("AWAITING_UI_LAYOUT_APPROVAL")
        self.assertEqual(ret, 1)
        self.assertIn("STALE_APPROVAL_INVALIDATED", output)
        self.assertEqual(self.current_state(), "AWAITING_ENTITY_BOUNDARIES_APPROVAL")
        self.assertEqual(self.ledger_record()["status"], "STALE")

    def test_a_stale_record_left_past_its_gate_is_recovered_by_re_advancing(self):
        # The state a crash between "ledger saved STALE" and "state saved" used to leave behind.
        self.set_state("AWAITING_ENTITY_BOUNDARIES_APPROVAL")
        self.write_lexicon(self.LEXICON)
        self.assertEqual(self.approve().returncode, 0)
        ledger = json.loads((self.feature_dir / "approvals.json").read_text(encoding="utf-8"))
        ledger["approvals"][0]["status"] = "STALE"
        (self.feature_dir / "approvals.json").write_text(json.dumps(ledger), encoding="utf-8")
        self.set_state("STAGE_3_VERTICAL_SLICING")

        ret, output = self.advance("AWAITING_UI_LAYOUT_APPROVAL")
        self.assertEqual(ret, 1)
        self.assertIn("STALE_APPROVAL_INVALIDATED", output)
        self.assertEqual(self.current_state(), "AWAITING_ENTITY_BOUNDARIES_APPROVAL")

        res = self.approve()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        ret, output = self.advance("STAGE_3_VERTICAL_SLICING")
        self.assertEqual(ret, 0, output)
        self.assertEqual(self.current_state(), "STAGE_3_VERTICAL_SLICING")

    def test_an_undeletable_derived_doc_still_leaves_the_feature_at_its_gate(self):
        self.set_state("AWAITING_ENTITY_BOUNDARIES_APPROVAL")
        self.write_lexicon(self.LEXICON)
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.advance("STAGE_3_VERTICAL_SLICING")[0], 0)
        # A directory where the derived document should be (left by a mis-aimed --out).
        (self.feature_dir / "docs" / "domain-entity-map.md").mkdir(parents=True)

        edited = json.loads(json.dumps(self.LEXICON))
        edited["simulated_dependencies"] = [{"boundary_class": "HTTP"}]
        self.write_lexicon(edited)
        ret, output = self.advance("AWAITING_UI_LAYOUT_APPROVAL")
        self.assertEqual(ret, 1)
        self.assertIn("STALE_APPROVAL_INVALIDATED", output)
        self.assertIn("DERIVED_DOCUMENT_NOT_DELETED", output)
        self.assertEqual(self.current_state(), "AWAITING_ENTITY_BOUNDARIES_APPROVAL")
        self.assertEqual(self.ledger_record()["status"], "STALE")
        self.assertEqual(self.approve().returncode, 0)


if __name__ == "__main__":
    unittest.main()
