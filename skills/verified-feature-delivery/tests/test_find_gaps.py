#!/usr/bin/env python3
"""Behavioural tests for bin/find-gaps (feature-gaps skill).

The gap finder must report every gap the Stage 2 gate would refuse on, under the gate's own
reason codes, never stop at the first one, and treat a project baseline as optional.
"""

import copy
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
BIN_DIR = TESTS_DIR.parent / "bin"
STAGE_2 = TESTS_DIR / "cases" / "stage-2"
BASELINE_DIR = TESTS_DIR / "cases" / "feature-gaps" / "baseline"
VALID_MATRIX = STAGE_2 / "valid" / "scenario-matrix.json"


def _load(name, file_name):
    loader = SourceFileLoader(name, str(BIN_DIR / file_name))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


find_gaps = _load("find_gaps_bin", "find-gaps")
validate_scenario_matrix = _load("vsm_for_find_gaps", "validate-scenario-matrix")


def _matrix(case="valid"):
    return json.loads((STAGE_2 / case / "scenario-matrix.json").read_text(encoding="utf-8"))


def _run(argv):
    out = io.StringIO()
    with patch.object(sys, "argv", ["find-gaps"] + argv), patch("sys.stdout", out), \
            patch("sys.stderr", io.StringIO()):
        code = find_gaps.main()
    return code, out.getvalue()


class CollectGapsTest(unittest.TestCase):
    def test_valid_matrix_has_no_gaps(self):
        self.assertEqual(find_gaps.collect_gaps(_matrix()), [])

    def test_every_broken_fixture_reports_the_gate_refusal_code(self):
        cases = sorted(p.parent.name for p in STAGE_2.glob("*/scenario-matrix.json"))
        self.assertGreater(len(cases), 10)
        for case in cases:
            matrix = _matrix(case)
            ok, gate_code, _ = validate_scenario_matrix.validate_scenario_matrix_dict(matrix)
            blocking = {g["code"] for g in find_gaps.collect_gaps(matrix) if g["severity"] == "blocking"}
            with self.subTest(case=case):
                if ok:
                    self.assertEqual(blocking, set())
                else:
                    self.assertIn(gate_code, blocking)

    def test_reports_all_gaps_not_only_the_first(self):
        matrix = _matrix()
        matrix["transitions"] = [t for t in matrix["transitions"] if t["from_state"] != "ERROR_SHOWN"]
        for state in matrix["states"]:
            if state["state_id"] == "RESULTS_EMPTY":
                state["evidence_key"] = ""
        codes = [g["code"] for g in find_gaps.collect_gaps(matrix)]
        self.assertIn("HANGING_STATE", codes)
        self.assertIn("MISSING_EVIDENCE_KEY", codes)

    def test_discriminator_collision_is_advisory(self):
        gaps = find_gaps.collect_gaps(_matrix("discriminator-collision-advisory"))
        advisory = [g for g in gaps if g["code"] == "DISCRIMINATOR_COLLISION_ADVISORY"]
        self.assertTrue(advisory)
        self.assertTrue(all(g["severity"] == "advisory" for g in advisory))

    def test_baseline_flags_only_unknown_terms(self):
        lexicon = json.loads((BASELINE_DIR / "domain-lexicon.json").read_text(encoding="utf-8"))
        gaps = find_gaps.collect_gaps(_matrix(), lexicon)
        self.assertEqual(
            [(g["code"], g["severity"]) for g in gaps], [("UNVERIFIED_TERM", "advisory")])
        self.assertIn("RESULTS_EMPTY", gaps[0]["message"])

    def test_output_is_deterministic(self):
        matrix = _matrix("discriminator-collision-advisory")
        shuffled = copy.deepcopy(matrix)
        shuffled["states"].reverse()
        shuffled["transitions"].reverse()
        self.assertEqual(find_gaps.collect_gaps(matrix), find_gaps.collect_gaps(matrix))
        self.assertEqual(
            sorted(g["code"] for g in find_gaps.collect_gaps(matrix)),
            sorted(g["code"] for g in find_gaps.collect_gaps(shuffled)))


class FindGapsCliTest(unittest.TestCase):
    def test_gaps_exit_zero_without_strict_and_one_with_strict(self):
        broken = str(STAGE_2 / "hanging-state" / "scenario-matrix.json")
        code, out = _run([broken])
        self.assertEqual(code, 0)
        self.assertIn("HANGING_STATE", out)
        code, _ = _run([broken, "--strict"])
        self.assertEqual(code, 1)

    def test_clean_matrix_passes_strict_and_notes_unverified_terms(self):
        code, out = _run([str(VALID_MATRIX), "--strict"])
        self.assertEqual(code, 0)
        self.assertIn("GAPS_CLEAN", out)
        self.assertIn("inline/unverified", out)

    def test_writes_markdown_and_json_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = _run([str(STAGE_2 / "hanging-state" / "scenario-matrix.json"), "--out", tmp])
            self.assertEqual(code, 0)
            report = json.loads((Path(tmp) / "gap-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["blocking_count"], 1)
            self.assertEqual(report["gaps"][0]["code"], "HANGING_STATE")
            md = (Path(tmp) / "gap-report.md").read_text(encoding="utf-8")
            self.assertIn("Dead-end states", md)
            self.assertIn("`HANGING_STATE`", md)

    def test_baseline_dir_enables_term_verification(self):
        code, out = _run([str(VALID_MATRIX), "--baseline-dir", str(BASELINE_DIR), "--json"])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["advisory_count"], 1)
        self.assertEqual(report["blocking_count"], 0)

    def test_unreadable_matrix_exits_two(self):
        code, _ = _run(["/nonexistent/scenario-matrix.json"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
