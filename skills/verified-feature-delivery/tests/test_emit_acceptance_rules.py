#!/usr/bin/env python3
"""Behavioural tests for bin/emit-acceptance-rules (feature-gaps skill)."""

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
VALID_MATRIX = TESTS_DIR / "cases" / "stage-2" / "valid" / "scenario-matrix.json"

_loader = SourceFileLoader("emit_acceptance_rules_bin", str(BIN_DIR / "emit-acceptance-rules"))
_spec = importlib.util.spec_from_loader("emit_acceptance_rules_bin", _loader)
emit = importlib.util.module_from_spec(_spec)
_loader.exec_module(emit)


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["emit-acceptance-rules"] + argv), \
            patch("sys.stdout", out), patch("sys.stderr", err):
        code = emit.main()
    return code, out.getvalue()


class GherkinTest(unittest.TestCase):
    def setUp(self):
        self.matrix = json.loads(VALID_MATRIX.read_text(encoding="utf-8"))
        self.feature = emit.emit_gherkin(self.matrix)

    def test_one_feature_per_flow_group_and_one_scenario_per_flow(self):
        groups = {f["group_id"] for f in self.matrix["flows"]}
        self.assertEqual(self.feature.count("Feature: "), len(groups))
        self.assertEqual(self.feature.count("  Scenario: "), len(self.matrix["flows"]))

    def test_scenario_reads_given_when_then_down_to_the_observer(self):
        scenario = self.feature.split("Scenario: Search Error Flow", 1)[1].split("Scenario:", 1)[0]
        lines = [l.strip() for l in scenario.strip().splitlines()]
        self.assertTrue(lines[0].startswith("Given network_unstable"))
        self.assertIn("And the system is in state IDLE", lines)
        self.assertIn("When operator performs query_settled_error", lines)
        self.assertIn("Then the system transitions to ERROR_SHOWN", lines)
        self.assertTrue(any(l.startswith("And system_telemetry sees via agent_transcript") for l in lines))

    def test_no_keyword_repeats_on_consecutive_steps(self):
        previous = None
        for line in self.feature.splitlines():
            keyword = line.strip().split(" ", 1)[0]
            if keyword in ("Given", "When", "Then"):
                self.assertNotEqual(keyword, previous, line)
                previous = keyword
            elif keyword != "And":
                previous = None

    def test_output_is_deterministic(self):
        reordered = json.loads(VALID_MATRIX.read_text(encoding="utf-8"))
        reordered["flows"].reverse()
        self.assertEqual(emit.emit_gherkin(reordered), self.feature)


class CliTest(unittest.TestCase):
    def test_writes_feature_file_and_rerun_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run([str(VALID_MATRIX), "--out", tmp])[0], 0)
            first = (Path(tmp) / "acceptance-rules.feature").read_bytes()
            self.assertEqual(_run([str(VALID_MATRIX), "--out", tmp])[0], 0)
            self.assertEqual((Path(tmp) / "acceptance-rules.feature").read_bytes(), first)

    def test_markdown_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = _run([str(VALID_MATRIX), "--out", tmp, "--format", "markdown"])
            self.assertEqual(code, 0)
            md = (Path(tmp) / "acceptance-rules.md").read_text(encoding="utf-8")
            self.assertIn("# Acceptance Rules: search-console-web-ui", md)
            self.assertIn("| Step | Action | From | To | Guard |", md)

    def test_matrix_without_flows_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            matrix = json.loads(VALID_MATRIX.read_text(encoding="utf-8"))
            matrix["flows"] = []
            path = Path(tmp) / "m.json"
            path.write_text(json.dumps(matrix), encoding="utf-8")
            code, out = _run([str(path), "--out", tmp])
            self.assertEqual(code, 1)
            self.assertIn("NO_FLOWS", out)


if __name__ == "__main__":
    unittest.main()
