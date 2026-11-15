#!/usr/bin/env python3
"""The change lane inside the pipeline.

- `planner-state init --lane` records the lane (default NEW_CAPABILITY, today's behaviour) and
  `--baseline-dir` seeds the feature lexicon and only the contract fixtures Stage 1 may reuse.
- The Stage 5 exit tier follows the lane; NEW_CAPABILITY keeps the mandatory Tier 3 gatekeeper.
- Live browser observation covers only the slices new or invalidated in a TWEAK/EXTENSION delivery.
- Scoped rollbacks before a delivery accumulate.
"""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN_DIR = SKILL_DIR / "bin"
SEED = TESTS_DIR / "cases" / "baseline" / "seed-feature"
VALID_MILESTONE = TESTS_DIR / "cases" / "stage-4" / "positive" / "valid-milestone"

sys.path.insert(0, str(SKILL_DIR))
from lib.baseline import reusable_boundary_classes  # noqa: E402
from lib.delivery import live_observation_scope  # noqa: E402
from lib.observation import verify_rendered_ui_observation  # noqa: E402
from lib.verification_policy import generate_verification_plan  # noqa: E402


def run(tool, *args, cwd=None):
    result = subprocess.run([sys.executable, str(BIN_DIR / tool)] + list(args), cwd=cwd,
                            capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout, result.stderr


class InitLaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.feature_dir = self.tmp / "feature"
        self.baseline_dir = self.tmp / "docs" / "domain"

    def init(self, *args):
        return run("planner-state", "init", "--feature", "f1", "--dir", str(self.feature_dir), *args,
                   cwd=str(self.tmp))

    def state(self):
        return json.loads((self.feature_dir / "state.json").read_text(encoding="utf-8"))

    def promote_seed_baseline(self):
        base = ["--baseline-dir", str(self.baseline_dir)]
        self.assertEqual(run("baseline", *base, "init")[0], 0)
        code, out, err = run("baseline", *base, "promote", "--feature-dir", str(SEED))
        self.assertEqual(code, 0, out + err)

    def test_default_lane_is_new_capability_without_seeding(self):
        code, out, err = self.init()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.state()["lane"], "NEW_CAPABILITY")
        self.assertNotIn("baseline", self.state())
        self.assertFalse((self.feature_dir / "domain-lexicon.json").exists())

    def test_missing_baseline_is_refused_and_writes_nothing(self):
        code, out, _ = self.init("--baseline-dir", "docs/domain")
        self.assertEqual(code, 1)
        self.assertIn("BASELINE_NOT_FOUND", out)
        self.assertFalse((self.feature_dir / "state.json").exists())

    def test_baseline_seeds_lexicon_and_only_reusable_fixtures(self):
        self.promote_seed_baseline()
        code, out, err = self.init("--lane", "TWEAK", "--baseline-dir", "docs/domain")
        self.assertEqual(code, 0, out + err)
        self.assertIn("BASELINE_SEEDED", out)
        state = self.state()
        self.assertEqual(state["lane"], "TWEAK")
        self.assertEqual(state["baseline"]["reused_boundary_classes"], ["NETWORK_API"])

        self.assertEqual(
            json.loads((self.feature_dir / "domain-lexicon.json").read_text(encoding="utf-8")),
            json.loads((self.baseline_dir / "domain-lexicon.json").read_text(encoding="utf-8")))
        manifest = json.loads(
            (self.feature_dir / "contract-fixtures" / "manifest.json").read_text(encoding="utf-8"))
        # UI_RUNTIME is PRESENT in the baseline but its fixture was never recorded, and the
        # placeholder NOT_PRESENT rows were never probed: neither is reused.
        self.assertEqual([r["class_name"] for r in manifest["boundary_classes"]], ["NETWORK_API"])
        self.assertTrue((self.feature_dir / "contract-fixtures" / "network_api-search_endpoint.json").is_file())

    def test_seeding_never_overwrites_the_feature_lexicon(self):
        self.promote_seed_baseline()
        self.feature_dir.mkdir(parents=True)
        (self.feature_dir / "domain-lexicon.json").write_text('{"mine": true}', encoding="utf-8")
        code, out, err = self.init("--baseline-dir", "docs/domain")
        self.assertEqual(code, 0, out + err)
        self.assertIn("lexicon kept", out)
        self.assertEqual(json.loads((self.feature_dir / "domain-lexicon.json").read_text()), {"mine": True})


class ReusableBoundaryClassTest(unittest.TestCase):
    MANIFEST = {"boundary_classes": [
        {"class_name": "NETWORK_API", "verdict": "PRESENT", "properties": [
            {"property_name": "status_codes", "verdict": "PROBED", "fixture_ref": "api.json"},
            {"property_name": "auth_expiry", "verdict": "NOT_PRESENT"}]},
        {"class_name": "DATASTORE", "verdict": "PRESENT", "properties": [
            {"property_name": "isolation", "verdict": "PROBED", "fixture_ref": "db.json"}]},
        {"class_name": "SUBPROCESS_CLI", "verdict": "NOT_PRESENT",
         "absence_reason": "Baseline initialized unprobed."},
    ]}

    def test_only_live_or_replayed_fixtures_are_reused(self):
        fixtures = {"api.json": {"fixture_status": "REPLAYED_FROM_RECORDING"},
                    "db.json": {"fixture_status": "PROVISIONAL_FROM_SCHEMA"}}
        self.assertEqual(reusable_boundary_classes(self.MANIFEST, fixtures), ["NETWORK_API"])

    def test_missing_fixture_is_not_reused(self):
        self.assertEqual(reusable_boundary_classes(self.MANIFEST, {}), [])


class StageFiveTierTest(unittest.TestCase):
    def plan(self, lane):
        with tempfile.TemporaryDirectory() as tmp:
            state = {"feature_slug": "f1", "current_state": "STAGE_5_DELIVERY"}
            if lane:
                state["lane"] = lane
            (Path(tmp) / "state.json").write_text(json.dumps(state), encoding="utf-8")
            return generate_verification_plan(Path(tmp), is_stage_5_exit=True)

    def test_exit_tier_follows_the_lane(self):
        for lane, tier in (("TWEAK", "TIER_1"), ("EXTENSION", "TIER_2"), ("NEW_CAPABILITY", "TIER_3")):
            with self.subTest(lane=lane):
                plan = self.plan(lane)
                self.assertEqual(plan["escalation_tier"], tier)
                self.assertEqual(plan["mandatory_gatekeeper"], tier == "TIER_3")

    def test_feature_without_a_lane_keeps_the_mandatory_gatekeeper(self):
        plan = self.plan(None)
        self.assertEqual(plan["escalation_tier"], "TIER_3")
        self.assertTrue(plan["mandatory_gatekeeper"])


class LiveObservationScopeTest(unittest.TestCase):
    def evidence(self, milestone_id):
        # A rendered_ui capture declaring no observation target: refused whenever it is observed.
        return SimpleNamespace(
            planned=SimpleNamespace(verification_medium="rendered_ui", milestone_id=milestone_id),
            envelope={})

    def test_scope_by_lane(self):
        scoped = {"milestones": ["M3"]}
        self.assertIsNone(live_observation_scope({"lane": "NEW_CAPABILITY", "rollback_scope": scoped}))
        self.assertIsNone(live_observation_scope({"rollback_scope": scoped}))
        self.assertIsNone(live_observation_scope({"lane": "TWEAK"}))
        self.assertEqual(live_observation_scope({"lane": "TWEAK", "rollback_scope": scoped}), {"M3"})

    def test_preserved_slices_are_not_observed_again(self):
        verified = [self.evidence("M1"), self.evidence("M3")]
        everything = verify_rendered_ui_observation({}, {}, verified)
        self.assertEqual(len(everything), 2)
        scoped = verify_rendered_ui_observation({}, {}, verified, observe_milestones={"M3"})
        self.assertEqual(len(scoped), 1)
        self.assertIn("M3", scoped[0])


class AccumulatedRollbackScopeTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.feature_dir = tmp / "f1"
        shutil.copytree(VALID_MILESTONE, self.feature_dir)
        dag = {"schema_version": "1.0.0", "feature_slug": "f1", "slices": [
            {"slice_id": "s1", "dependencies": [], "milestones": [{"milestone_id": "M1"}]},
            {"slice_id": "s2", "dependencies": [], "milestones": [{"milestone_id": "M2"}]},
            {"slice_id": "s3", "dependencies": [], "milestones": [{"milestone_id": "M3"}]},
        ]}
        (self.feature_dir / "execution-dag.json").write_text(json.dumps(dag), encoding="utf-8")

    def scoped_rollback(self, milestone_id):
        state = json.loads((self.feature_dir / "state.json").read_text(encoding="utf-8"))
        state["current_state"] = "STAGE_4_MILESTONE_EXECUTION"
        (self.feature_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        trace = self.feature_dir / "failure.json"
        trace.write_text(json.dumps({"milestone_id": milestone_id,
                                     "trace": {"description": "slice regressed"}}), encoding="utf-8")
        code, out, err = run("planner-state", "rollback", "--feature", "f1", "--dir",
                             str(self.feature_dir), "--to", "STAGE_2_SCENARIO_SIMULATION",
                             "--cause", "BROKEN_INVARIANT", "--trace", str(trace))
        self.assertEqual(code, 0, out + err)
        return json.loads((self.feature_dir / "state.json").read_text(encoding="utf-8"))

    def test_second_scoped_rollback_keeps_the_first_scope(self):
        self.scoped_rollback("M1")
        state = self.scoped_rollback("M3")
        self.assertEqual(state["rollback_scope"]["slices"], ["s1", "s3"])
        self.assertEqual(state["rollback_scope"]["milestones"], ["M1", "M3"])


if __name__ == "__main__":
    unittest.main()
