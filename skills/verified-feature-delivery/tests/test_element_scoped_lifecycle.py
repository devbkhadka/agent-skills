#!/usr/bin/env python3
"""Element-scoped approvals and dependency-scoped rollback, driven through the CLIs.

- An approval stores per-element digests; editing one element after approval stales the record
  and names only that element; re-approval reports only that element as re-reviewed.
- A record written before element digests existed stays whole-section.
- A Stage 4 rollback with a failure trace keeps upstream artifacts and invalidates only the
  evidence of the slices the trace touches (and their dependents); the harness re-grades the
  preserved evidence unchanged.
- A rollback whose scope cannot be derived falls back to full invalidation and says so.
"""

import copy
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN_DIR = SKILL_DIR / "bin"
SEED_LEXICON = TESTS_DIR / "cases" / "baseline" / "seed-feature" / "domain-lexicon.json"
VALID_MILESTONE = TESTS_DIR / "cases" / "stage-4" / "positive" / "valid-milestone"

sys.path.insert(0, str(SKILL_DIR))
from lib.rollback import rollback_scope  # noqa: E402

_loader = SourceFileLoader("milestone_harness_scoped", str(BIN_DIR / "milestone-harness"))
_spec = importlib.util.spec_from_loader("milestone_harness_scoped", _loader)
milestone_harness = importlib.util.module_from_spec(_spec)
_loader.exec_module(milestone_harness)

FEATURE = "scoped-feature"


def run(tool, *args):
    result = subprocess.run([sys.executable, str(BIN_DIR / tool)] + list(args),
                            capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout, result.stderr


class FeatureDir(object):
    def __init__(self, path: Path):
        self.path = path

    def state(self):
        return json.loads((self.path / "state.json").read_text(encoding="utf-8"))

    def set_state(self, current_state, artifacts=None):
        state = {"schema_version": "1.0.0", "feature_slug": FEATURE, "current_state": current_state,
                 "updated_at": "2026-11-15T00:00:00Z", "artifacts": artifacts or {}, "history": []}
        (self.path / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def write(self, rel, data):
        (self.path / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.path / rel).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read(self, rel):
        return json.loads((self.path / rel).read_text(encoding="utf-8"))

    def approve(self, milestone):
        return run("record-approval", "--feature", FEATURE, "--dir", str(self.path),
                   "--milestone", milestone, "--decided-by", "reviewer")

    def advance(self, to_state):
        return run("planner-state", "advance", "--feature", FEATURE, "--dir", str(self.path),
                   "--to", to_state)

    def rollback(self, to_state, cause, *extra):
        return run("planner-state", "rollback", "--feature", FEATURE, "--dir", str(self.path),
                   "--to", to_state, "--cause", cause, *extra)

    def record(self, milestone):
        for rec in self.read("approvals.json")["approvals"]:
            if rec["milestone_id"] == milestone:
                return rec
        return None


def two_boundary_lexicon():
    lexicon = json.loads(SEED_LEXICON.read_text(encoding="utf-8"))
    second = copy.deepcopy(lexicon["entity_boundaries"][0])
    second["boundary_id"] = "identity_and_access"
    second["name"] = "Identity and Access Boundary"
    lexicon["entity_boundaries"].append(second)
    return lexicon


class ElementScopedApprovalTest(unittest.TestCase):
    def setUp(self):
        self.feature = FeatureDir(Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.feature.path, ignore_errors=True)
        self.lexicon = two_boundary_lexicon()
        self.feature.write("domain-lexicon.json", self.lexicon)
        self.feature.set_state("AWAITING_ENTITY_BOUNDARIES_APPROVAL")
        code, out, err = self.feature.approve("ENTITY_BOUNDARIES")
        self.assertEqual(code, 0, out + err)
        code, out, err = self.feature.advance("STAGE_3_VERTICAL_SLICING")
        self.assertEqual(code, 0, out + err)

    def edit_second_boundary(self):
        edited = copy.deepcopy(self.lexicon)
        edited["entity_boundaries"][1]["entities"].append("Workstream")
        self.feature.write("domain-lexicon.json", edited)

    def test_approval_records_a_digest_per_element(self):
        digests = self.feature.record("ENTITY_BOUNDARIES")["element_digests"]
        self.assertIn("boundary:billing_and_entitlements", digests)
        self.assertIn("boundary:identity_and_access", digests)
        self.assertIn("simulated_dependency:NETWORK_API", digests)

    def test_one_element_edit_stales_only_that_element(self):
        self.edit_second_boundary()
        code, out, err = self.feature.advance("AWAITING_UI_LAYOUT_APPROVAL")
        self.assertEqual(code, 1)
        self.assertIn("STALE_APPROVAL_INVALIDATED", out)
        self.assertIn("STALE_ELEMENTS: boundary:identity_and_access", out)
        self.assertNotIn("billing_and_entitlements", out)
        record = self.feature.record("ENTITY_BOUNDARIES")
        self.assertEqual(record["status"], "STALE")
        self.assertEqual(record["stale_elements"], ["boundary:identity_and_access"])
        self.assertEqual(self.feature.state()["current_state"], "AWAITING_ENTITY_BOUNDARIES_APPROVAL")

    def test_re_approval_covers_only_the_changed_element(self):
        self.edit_second_boundary()
        self.feature.advance("AWAITING_UI_LAYOUT_APPROVAL")
        code, out, err = self.feature.approve("ENTITY_BOUNDARIES")
        self.assertEqual(code, 0, out + err)
        self.assertIn("REAPPROVED_ELEMENTS: boundary:identity_and_access", out)
        record = self.feature.record("ENTITY_BOUNDARIES")
        self.assertEqual(record["status"], "APPROVED")
        self.assertNotIn("stale_elements", record)
        code, out, err = self.feature.advance("STAGE_3_VERTICAL_SLICING")
        self.assertEqual(code, 0, out + err)

    def test_legacy_record_without_element_digests_stays_whole_section(self):
        ledger = self.feature.read("approvals.json")
        del ledger["approvals"][0]["element_digests"]
        self.feature.write("approvals.json", ledger)
        self.edit_second_boundary()
        code, out, err = self.feature.advance("AWAITING_UI_LAYOUT_APPROVAL")
        self.assertEqual(code, 1)
        self.assertIn("STALE_APPROVAL_INVALIDATED", out)
        self.assertNotIn("STALE_ELEMENTS", out)
        self.assertIn("whole section", err)
        self.assertNotIn("stale_elements", self.feature.record("ENTITY_BOUNDARIES"))


def three_slice_dag():
    """slice_1 owns the verified M1/M2; slice_2 enforces INV_SORT_STABLE; slice_3 builds on it."""
    return {
        "schema_version": "1.0.0",
        "feature_slug": FEATURE,
        "slices": [
            {"slice_id": "slice_1_query_entry", "dependencies": [],
             "milestones": [{"milestone_id": "M1_QUERY_PANE_RENDERS"},
                            {"milestone_id": "M2_DEFENSIVE_METADATA_CONSUMPTION"}]},
            {"slice_id": "slice_2_sort", "dependencies": ["slice_1_query_entry"],
             "user_visible_outcome": "Results keep order (INV_SORT_STABLE)",
             "milestones": [{"milestone_id": "M3_SORTED_RESULTS"}]},
            {"slice_id": "slice_3_export", "dependencies": ["slice_2_sort"],
             "milestones": [{"milestone_id": "M4_EXPORT"}]},
        ],
    }


class DependencyScopedRollbackTest(unittest.TestCase):
    ARTIFACTS = {
        "stage_0_lexicon": "domain-lexicon.json",
        "stage_1_manifest": "contract-fixtures/manifest.json",
        "stage_2_scenario_matrix": "flow-simulation-matrix.json",
        "stage_3_execution_dag": "execution-dag.json",
        "stage_4_milestone_evidence": "milestones/evidence-summary.json",
    }

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.feature = FeatureDir(tmp / FEATURE)
        shutil.copytree(VALID_MILESTONE, self.feature.path)
        self.feature.write("execution-dag.json", three_slice_dag())
        executions = self.feature.read("milestones/executions.json")
        sorted_run = copy.deepcopy(executions["milestones"][1])
        sorted_run.update(milestone_id="M3_SORTED_RESULTS", slice_id="slice_2_sort")
        executions["milestones"].append(sorted_run)
        self.feature.write("milestones/executions.json", executions)
        self.feature.write("milestones/evidence-summary.json", {"milestones_verified": []})
        self.feature.set_state("STAGE_4_MILESTONE_EXECUTION", dict(self.ARTIFACTS))

    def executed_milestones(self):
        return [r["milestone_id"] for r in self.feature.read("milestones/executions.json")["milestones"]]

    def test_scoped_rollback_preserves_unaffected_slice_evidence(self):
        trace_file = self.feature.path / "failure.json"
        trace_file.write_text(json.dumps({
            "milestone_id": "M3_SORTED_RESULTS",
            "trace": {"broken_invariant": "INV_SORT_STABLE",
                      "description": "results reordered after refresh"},
        }), encoding="utf-8")
        code, out, err = self.feature.rollback("STAGE_2_SCENARIO_SIMULATION", "BROKEN_INVARIANT",
                                               "--trace", str(trace_file))
        self.assertEqual(code, 0, out + err)
        self.assertIn("ROLLBACK_SCOPE: elements=", out)
        self.assertNotIn("ROLLBACK_SCOPE_UNDERIVABLE", out)

        state = self.feature.state()
        self.assertEqual(state["current_state"], "STAGE_2_SCENARIO_SIMULATION")
        self.assertEqual(state["rollback_scope"]["slices"], ["slice_2_sort", "slice_3_export"])
        self.assertIn("broken_invariant:INV_SORT_STABLE", state["rollback_scope"]["elements"])
        # Upstream artifacts stay registered; only the evidence is invalidated.
        self.assertEqual(state["artifacts"]["stage_3_execution_dag"], "execution-dag.json")
        self.assertIsNone(state["artifacts"]["stage_4_milestone_evidence"])

        self.assertEqual(self.executed_milestones(),
                         ["M1_QUERY_PANE_RENDERS", "M2_DEFENSIVE_METADATA_CONSUMPTION"])
        moved = self.feature.read("milestones/invalidated-executions.json")["invalidated"][0]
        self.assertEqual([r["milestone_id"] for r in moved["milestones"]], ["M3_SORTED_RESULTS"])
        self.assertFalse((self.feature.path / "milestones" / "evidence-summary.json").exists())

        # The harness re-grades the preserved evidence of the unaffected slice as-is.
        code, message = milestone_harness.validate_milestone_execution(self.feature.path, write=False)
        self.assertEqual(code, 0, message)
        self.assertIn("MILESTONES_VERIFIED_STAGE_4", message)

    def test_rollback_without_a_trace_falls_back_to_full_invalidation(self):
        code, out, err = self.feature.rollback("STAGE_2_SCENARIO_SIMULATION", "BROKEN_INVARIANT")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ROLLBACK_SCOPE_UNDERIVABLE", out)
        state = self.feature.state()
        self.assertIsNone(state["artifacts"]["stage_3_execution_dag"])
        self.assertNotIn("rollback_scope", state)
        self.assertEqual(len(self.executed_milestones()), 3)

    def test_trace_naming_nothing_any_slice_references_falls_back(self):
        trace_file = self.feature.path / "failure.json"
        trace_file.write_text(json.dumps({
            "milestone_id": "M9_UNPLANNED",
            "trace": {"broken_invariant": "INV_NOWHERE", "description": "unplanned failure"},
        }), encoding="utf-8")
        code, out, err = self.feature.rollback("STAGE_2_SCENARIO_SIMULATION", "BROKEN_INVARIANT",
                                               "--trace", str(trace_file))
        self.assertEqual(code, 0, out + err)
        self.assertIn("ROLLBACK_SCOPE_UNDERIVABLE", out)
        self.assertIsNone(self.feature.state()["artifacts"]["stage_3_execution_dag"])


class RollbackScopeTest(unittest.TestCase):
    def test_dependents_of_an_affected_slice_are_affected(self):
        scope = rollback_scope("M3_SORTED_RESULTS", {}, None, three_slice_dag())
        self.assertEqual(scope["slices"], ["slice_2_sort", "slice_3_export"])
        self.assertEqual(scope["milestones"], ["M3_SORTED_RESULTS", "M4_EXPORT"])

    def test_domain_term_resolves_to_its_declared_unit_precision(self):
        lexicon = {"unit_precisions": [{"id": "money_cents"}],
                   "entities": [{"name": "Invoice",
                                 "attributes": [{"name": "total", "unit_ref": "money_cents"}]}]}
        dag = {"slices": [{"slice_id": "s1", "dependencies": [], "name": "Formats money_cents"},
                          {"slice_id": "s2", "dependencies": [], "name": "Unrelated"}]}
        scope = rollback_scope("", {"domain_term": "total"}, lexicon, dag)
        self.assertEqual(scope["elements"], ["domain_term:total", "unit_precision:money_cents"])
        self.assertEqual(scope["slices"], ["s1"])

    def test_no_dag_is_underivable(self):
        self.assertIsNone(rollback_scope("M1", {"broken_invariant": "X"}, None, None))


if __name__ == "__main__":
    unittest.main()
