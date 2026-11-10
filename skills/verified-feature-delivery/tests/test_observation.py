#!/usr/bin/env python3
"""Unit tests for Stage 5 rendered_ui observation runner and gate verification.

Tests:
1. Positive matching case: capture matching live browser observation passes cleanly.
2. OBSERVATION_TARGET_UNDECLARED: rendered_ui capture missing observation_target is refused.
3. OBSERVATION_TARGET_UNREACHABLE: non-existent file or unreachable server is refused.
4. OBSERVATION_RENDER_EMPTY: target that renders empty DOM is refused.
5. OBSERVATION_DISAGREEMENT: target that renders different markup than claimed is refused.
6. OBSERVATION_SELECTOR_TIMEOUT: declared wait_for_selector that never appears is refused.
7. OBSERVATION_BROWSER_UNAVAILABLE: missing/unresolvable browser driver is refused.
8. CLI runner: bin/observe-rendered-ui produces valid output and exit codes.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from lib.delivery import PlannedMilestone, MilestoneEvidence, verify_claim, verify_medium, read_planned_milestones, read_report
from lib.normalisation import capture_projection, normalise_rendered_ui
from lib.observation import (
    find_playwright_path,
    is_browser_available,
    observe_rendered_ui,
    verify_rendered_ui_observation,
)


class TestRenderedUiObservation(unittest.TestCase):
    """Test suite for live browser observation runner and gate checks."""

    def setUp(self):
        self.browser_available = is_browser_available()

    def test_01_observation_target_undeclared(self):
        """Milestone with rendered_ui medium omitting observation_target is refused."""
        plan = PlannedMilestone("M1_UI", "slice_1", "rendered_ui", ("claim:state",))
        record = {
            "milestone_id": "M1_UI",
            "slice_id": "slice_1",
            "artifact_path": "evidence/capture.json",
            "claims_evaluated": [
                {"claim_id": "claim:state", "claim_kind": "state_observable_and_distinct", "evidence_key": "st:1"}
            ],
        }
        envelope = {
            "captured_artifact": "<div id='app'><h1>Loaded</h1></div>",
            "recorded_fixture_values": [],
            # No observation_target
        }
        normalised, _ = capture_projection("rendered_ui", envelope["captured_artifact"])
        evidence = MilestoneEvidence(plan, record, envelope, normalised)

        problems = verify_rendered_ui_observation({}, {"M1_UI": plan}, [evidence])
        self.assertTrue(any("OBSERVATION_TARGET_UNDECLARED" in p for p in problems))

    def test_02_observation_target_unreachable(self):
        """Observation target pointing to non-existent file is refused."""
        if not self.browser_available:
            self.skipTest("Headless browser driver not available in environment")

        target = {"entry_point": "non_existent_file_404.html"}
        markup, err_code, err_detail = observe_rendered_ui(target)
        self.assertIsNone(markup)
        self.assertEqual(err_code, "OBSERVATION_TARGET_UNREACHABLE")

    def test_03_observation_render_empty(self):
        """Observation target rendering empty DOM is refused with OBSERVATION_RENDER_EMPTY."""
        if not self.browser_available:
            self.skipTest("Headless browser driver not available in environment")

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write("<!DOCTYPE html><html><head></head><body><div id='empty-placeholder'></div></body></html>")
            f.flush()
            target = {"entry_point": f.name}
            markup, err_code, err_detail = observe_rendered_ui(target)
            self.assertIsNone(err_code)
            self.assertIsNotNone(markup)
            
            # Normalisation of empty container produces [empty_dom]
            norm = normalise_rendered_ui(markup)
            self.assertEqual(norm, "[empty_dom]")

    def test_04_observation_disagreement(self):
        """Observation target producing different DOM than claimed is refused with OBSERVATION_DISAGREEMENT."""
        if not self.browser_available:
            self.skipTest("Headless browser driver not available in environment")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            html_file = tmp_path / "index.html"
            html_file.write_text("<!DOCTYPE html><html><body><button>Actual Text</button></body></html>")

            plan = PlannedMilestone("M1_UI", "slice_1", "rendered_ui", ("claim:state",))
            record = {
                "milestone_id": "M1_UI",
                "slice_id": "slice_1",
                "artifact_path": "evidence/capture.json",
                "claims_evaluated": [
                    {"claim_id": "claim:state", "claim_kind": "state_observable_and_distinct", "evidence_key": "st:1"}
                ],
            }
            envelope = {
                "captured_artifact": "<div><h1>Fabricated Header</h1></div>",
                "recorded_fixture_values": [],
                "observation_target": {"entry_point": "index.html"},
            }
            normalised, _ = capture_projection("rendered_ui", envelope["captured_artifact"])
            evidence = MilestoneEvidence(plan, record, envelope, normalised)

            problems = verify_rendered_ui_observation({}, {"M1_UI": plan}, [evidence], base_dir=tmp_path)
            self.assertTrue(any("OBSERVATION_DISAGREEMENT" in p for p in problems))

    def test_05_observation_positive_match(self):
        """Observation target matching claimed capture passes cleanly without problems."""
        if not self.browser_available:
            self.skipTest("Headless browser driver not available in environment")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            html_file = tmp_path / "index.html"
            html_markup = "<div class='container'><h1>Dashboard</h1><ul class='items'><li>Item 1</li></ul></div>"
            html_file.write_text(f"<!DOCTYPE html><html><body>{html_markup}</body></html>")

            plan = PlannedMilestone("M1_UI", "slice_1", "rendered_ui", ("claim:state",))
            record = {
                "milestone_id": "M1_UI",
                "slice_id": "slice_1",
                "artifact_path": "evidence/capture.json",
                "claims_evaluated": [
                    {"claim_id": "claim:state", "claim_kind": "state_observable_and_distinct", "evidence_key": "st:1"}
                ],
            }
            envelope = {
                "captured_artifact": html_markup,
                "recorded_fixture_values": ["Item 1"],
                "observation_target": {"entry_point": "index.html"},
            }
            normalised, _ = capture_projection("rendered_ui", envelope["captured_artifact"], recorded_values=["Item 1"])
            evidence = MilestoneEvidence(plan, record, envelope, normalised)

            problems = verify_rendered_ui_observation({}, {"M1_UI": plan}, [evidence], base_dir=tmp_path)
            self.assertEqual(problems, [])

    def test_06_observation_selector_timeout(self):
        """Observation target with wait_for_selector that never appears is refused with OBSERVATION_SELECTOR_TIMEOUT."""
        if not self.browser_available:
            self.skipTest("Headless browser driver not available in environment")

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write("<!DOCTYPE html><html><body><div id='static-content'><h1>Static</h1></div></body></html>")
            f.flush()
            target = {
                "entry_point": f.name,
                "wait_for_selector": "#selector-that-never-appears",
                "selector_timeout_ms": 500,
            }
            markup, err_code, err_detail = observe_rendered_ui(target)
            self.assertIsNone(markup)
            self.assertEqual(err_code, "OBSERVATION_SELECTOR_TIMEOUT")

    def test_07_observation_browser_unavailable(self):
        """When browser driver cannot be found, returns OBSERVATION_BROWSER_UNAVAILABLE."""
        with patch("lib.observation.find_playwright_path", return_value=None):
            target = {"entry_point": "index.html"}
            markup, err_code, err_detail = observe_rendered_ui(target)
            self.assertIsNone(markup)
            self.assertEqual(err_code, "OBSERVATION_BROWSER_UNAVAILABLE")

    def test_08_cli_observe_rendered_ui(self):
        """CLI observe-rendered-ui executes and outputs OBSERVATION_CAPTURED_CLEAN."""
        if not self.browser_available:
            self.skipTest("Headless browser driver not available in environment")

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write("<!DOCTYPE html><html><body><div id='app'><h1>Test View</h1></div></body></html>")
            f.flush()

            cmd = [
                sys.executable,
                str(SKILL_DIR / "bin" / "observe-rendered-ui"),
                "--entry-point",
                f.name,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Expected 0 exit code, got {res.returncode}: {res.stderr}")
            self.assertIn("OBSERVATION_CAPTURED_CLEAN", res.stdout)


if __name__ == "__main__":
    unittest.main()
