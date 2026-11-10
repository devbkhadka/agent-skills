"""The in-project handoff route, for a CLI that cannot write outside its project root.

Round 2 of the agentic-feature-planner build found that an AGY worker's writes to the
state-directory handoff path vanish with no error: the pane reports the edit as successful and
no file appears anywhere on the machine. The pane route was independently unusable for the same
worker, because AGY wraps its output mid-token and the scraped `FLEET_HANDOFF:` payload comes
back with a control character inside the JSON. Between the two, that worker could not hand off
at all, and a verified bead was only closable by a lead moving the file by hand.

These tests pin the third route: `<cwd>/.herder/handoff-<agent>.json`, discovered by the same
two functions that look at the state path, cleared once accepted, and named in the task
contract so the worker knows it exists.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

AGENT = "bead-round2-probe"
SESSION = "test-repo-handoff"


def valid_handoff():
    return {
        "status": "completed",
        "decisions": ["kept the closed key set"],
        "changed_files": ["skills/x/bin/y"],
        "verification": [{"command": "python3 -m py_compile x.py", "exit_code": 0,
                          "output": "", "expected_empty": True}],
        "blockers": [],
        "next_inputs": [],
    }


def make_large_handoff(size: int = 10000) -> dict:
    base = valid_handoff()
    padding = "x" * max(0, size - 300)
    base["decisions"] = [padding]
    return base


class RepoHandoffPathTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="tar-state-")
        os.environ["AGENT_HERDER_STATE_DIR"] = self.state
        for mod in [m for m in list(sys.modules) if m == "fleet"]:
            del sys.modules[mod]
        import fleet
        self.fleet = fleet
        self.repo = Path(tempfile.mkdtemp(prefix="tar-repo-"))

    def test_path_is_dot_herder_under_cwd(self):
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        self.assertEqual(p, self.repo / ".herder" / f"handoff-{AGENT}.json")

    def test_no_cwd_means_no_path(self):
        self.assertIsNone(self.fleet.repo_handoff_path(AGENT, None))

    def test_filename_still_belongs_to_its_agent(self):
        """The agent check in parse_handoff_file must apply on this route too, or one worker
        could complete against another worker's evidence."""
        p = self.repo / ".herder" / "handoff-someone-else.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(valid_handoff()))
        assignment = {"task_time": 0, "cwd": str(self.repo)}
        with self.assertRaises(SystemExit):
            self.fleet.parse_handoff_file(p, assignment, expected_agent=AGENT)

    def test_stale_file_is_refused(self):
        """A handoff written before the current task was assigned is the previous context's
        evidence, and accepting it would let a reset worker close on work it did not do."""
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(valid_handoff()))
        old = os.stat(p).st_mtime
        assignment = {"task_time": old + 600, "cwd": str(self.repo)}
        with self.assertRaises(SystemExit):
            self.fleet.parse_handoff_file(p, assignment, expected_agent=AGENT)

    def test_fresh_file_parses(self):
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(valid_handoff()))
        assignment = {"task_time": time.time() - 600, "cwd": str(self.repo)}
        data, canonical = self.fleet.parse_handoff_file(p, assignment, expected_agent=AGENT)
        self.assertEqual(data["status"], "completed")
        self.assertTrue(canonical.startswith("FLEET_HANDOFF: "))

    def test_contract_names_both_routes(self):
        text = self.fleet.task_contract("do the thing", ["file:a"], False,
                                        agent=AGENT, session=SESSION, cwd=self.repo)
        self.assertIn(str(self.fleet.handoff_path(AGENT, SESSION)), text)
        self.assertIn(str(self.fleet.repo_handoff_path(AGENT, self.repo)), text)
        self.assertIn("FLEET_HANDOFF:", text)

    def test_contract_without_cwd_names_only_the_state_route(self):
        text = self.fleet.task_contract("do the thing", ["file:a"], False,
                                        agent=AGENT, session=SESSION)
        self.assertIn(str(self.fleet.handoff_path(AGENT, SESSION)), text)
        self.assertNotIn(".herder", text)


class HandoffGateAndOverrideTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="tar-state-")
        os.environ["AGENT_HERDER_STATE_DIR"] = self.state
        for mod in [m for m in list(sys.modules) if m == "fleet"]:
            del sys.modules[mod]
        import fleet
        self.fleet = fleet
        self.repo = Path(tempfile.mkdtemp(prefix="tar-repo-"))

    def test_staleness_tolerance_widened_to_2_seconds(self):
        self.assertEqual(self.fleet.HANDOFF_STALENESS_TOLERANCE, 2.0)
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(valid_handoff()))
        old = os.stat(p).st_mtime

        # 1.0s clock skew / delay before task_time should be accepted (tolerance is 2.0s)
        assignment_1s = {"task_time": old + 1.0, "cwd": str(self.repo)}
        data, _ = self.fleet.parse_handoff_file(p, assignment_1s, expected_agent=AGENT)
        self.assertEqual(data["status"], "completed")

        # 1.9s clock skew should also be accepted
        assignment_1_9s = {"task_time": old + 1.9, "cwd": str(self.repo)}
        data, _ = self.fleet.parse_handoff_file(p, assignment_1_9s, expected_agent=AGENT)
        self.assertEqual(data["status"], "completed")

        # 2.5s is beyond the 2.0s tolerance window and must be refused as stale
        assignment_2_5s = {"task_time": old + 2.5, "cwd": str(self.repo)}
        with self.assertRaisesRegex(SystemExit, "is stale"):
            self.fleet.parse_handoff_file(p, assignment_2_5s, expected_agent=AGENT)

    def test_default_max_handoff_chars_is_16k(self):
        self.assertEqual(self.fleet.DEFAULT_MAX_HANDOFF_CHARS, 16000)

    def test_16k_handoff_in_has_completion_evidence(self):
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.parent.mkdir(parents=True)
        large_ho = make_large_handoff(10000)
        p.write_text(json.dumps(large_ho))
        self.fleet.set_task(AGENT, "big task", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))

        # 10k chars > 4k, so under 16k default it is valid
        self.assertTrue(self.fleet.has_completion_evidence(AGENT, SESSION))

        # Oversized > 16k chars is rejected
        oversized_ho = make_large_handoff(17000)
        p.write_text(json.dumps(oversized_ho))
        self.assertFalse(self.fleet.has_completion_evidence(AGENT, SESSION))

    def test_16k_handoff_in_cmd_complete(self):
        large_ho = make_large_handoff(12000)
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(large_ho))
        self.fleet.set_task(AGENT, "big task", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))

        args = argparse.Namespace(agent=AGENT, session=SESSION, lines=100,
                                  max_chars=self.fleet.DEFAULT_MAX_HANDOFF_CHARS, group="repo:test",
                                  handoff=None)
        with mock.patch.object(self.fleet, "read_text", return_value=""), \
             mock.patch.object(self.fleet, "release_agent_strict", return_value={"freed": ["file:a"]}), \
             mock.patch.object(self.fleet, "find_agent", return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            self.fleet.cmd_complete(args)

        records = self.fleet.load_json(self.fleet.state_path("handoffs.json", SESSION), {})
        self.assertIn(AGENT, records)

    def test_cmd_complete_refuses_oversized_handoff_beyond_16k(self):
        oversized_ho = make_large_handoff(17000)
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(oversized_ho))
        self.fleet.set_task(AGENT, "oversized task", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))

        args = argparse.Namespace(agent=AGENT, session=SESSION, lines=100,
                                  max_chars=self.fleet.DEFAULT_MAX_HANDOFF_CHARS, group="repo:test",
                                  handoff=None)
        with mock.patch.object(self.fleet, "read_text", return_value=""), \
             mock.patch.object(self.fleet, "find_agent", return_value=None):
            with self.assertRaisesRegex(SystemExit, "structured handoff exceeds 16000 characters"):
                self.fleet.cmd_complete(args)

    def test_lead_override_with_inline_json(self):
        self.fleet.set_task(AGENT, "task with corrupted pane", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))
        override_ho = valid_handoff()
        override_ho["decisions"] = ["lead override applied"]
        override_json = json.dumps(override_ho)

        args = argparse.Namespace(agent=AGENT, session=SESSION, lines=100,
                                  max_chars=self.fleet.DEFAULT_MAX_HANDOFF_CHARS, group="repo:test",
                                  handoff=override_json)
        with mock.patch.object(self.fleet, "read_text", return_value="corrupted pane output"), \
             mock.patch.object(self.fleet, "release_agent_strict", return_value={"freed": ["file:a"]}), \
             mock.patch.object(self.fleet, "find_agent", return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            self.fleet.cmd_complete(args)

        records = self.fleet.load_json(self.fleet.state_path("handoffs.json", SESSION), {})
        self.assertIn(AGENT, records)
        self.assertEqual(records[AGENT]["handoff"]["decisions"], ["lead override applied"])

    def test_lead_override_with_at_file(self):
        self.fleet.set_task(AGENT, "task needing correction", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))
        override_ho = valid_handoff()
        override_ho["decisions"] = ["lead file override applied"]
        override_file = self.repo / "lead_override.json"
        override_file.write_text("FLEET_HANDOFF: " + json.dumps(override_ho))

        args = argparse.Namespace(agent=AGENT, session=SESSION, lines=100,
                                  max_chars=self.fleet.DEFAULT_MAX_HANDOFF_CHARS, group="repo:test",
                                  handoff=f"@{override_file}")
        with mock.patch.object(self.fleet, "read_text", return_value="corrupted pane output"), \
             mock.patch.object(self.fleet, "release_agent_strict", return_value={"freed": ["file:a"]}), \
             mock.patch.object(self.fleet, "find_agent", return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            self.fleet.cmd_complete(args)

        records = self.fleet.load_json(self.fleet.state_path("handoffs.json", SESSION), {})
        self.assertIn(AGENT, records)
        self.assertEqual(records[AGENT]["handoff"]["decisions"], ["lead file override applied"])

    def test_lead_override_nonexistent_file_refused(self):
        self.fleet.set_task(AGENT, "task", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))
        args = argparse.Namespace(agent=AGENT, session=SESSION, lines=100,
                                  max_chars=self.fleet.DEFAULT_MAX_HANDOFF_CHARS, group="repo:test",
                                  handoff="@/nonexistent/file.json")
        with self.assertRaisesRegex(SystemExit, "handoff override file not found"):
            self.fleet.cmd_complete(args)

    def test_lead_override_schema_validation_failure_refused(self):
        self.fleet.set_task(AGENT, "task", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))
        bad_ho = {"status": "blocked", "blockers": ["something broken"]}
        args = argparse.Namespace(agent=AGENT, session=SESSION, lines=100,
                                  max_chars=self.fleet.DEFAULT_MAX_HANDOFF_CHARS, group="repo:test",
                                  handoff=json.dumps(bad_ho))
        with self.assertRaisesRegex(SystemExit, "missing structured fields"):
            self.fleet.cmd_complete(args)

    def test_failed_handoff_persists_raw_pane_evidence(self):
        self.fleet.set_task(AGENT, "task with malformed output", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))
        corrupted_pane = "Some log line\nFLEET_HANDOFF: {bad json\nMore trailing output"

        args = argparse.Namespace(agent=AGENT, session=SESSION, lines=100,
                                  max_chars=self.fleet.DEFAULT_MAX_HANDOFF_CHARS, group="repo:test",
                                  handoff=None)
        with mock.patch.object(self.fleet, "read_text", return_value=corrupted_pane), \
             mock.patch.object(self.fleet, "find_agent", return_value=None):
            with self.assertRaises(SystemExit):
                self.fleet.cmd_complete(args)

        failed_file = self.fleet.failed_handoff_raw_path(AGENT, self.repo)
        self.assertTrue(failed_file.is_file())
        self.assertEqual(failed_file.read_text(encoding="utf-8"), corrupted_pane)

    def test_successful_complete_cleans_up_failed_handoff_raw(self):
        self.fleet.set_task(AGENT, "task with recovery", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))
        failed_file = self.fleet.failed_handoff_raw_path(AGENT, self.repo)
        failed_file.parent.mkdir(parents=True, exist_ok=True)
        failed_file.write_text("old corrupted pane evidence")
        self.assertTrue(failed_file.is_file())

        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.write_text(json.dumps(valid_handoff()))

        args = argparse.Namespace(agent=AGENT, session=SESSION, lines=100,
                                  max_chars=self.fleet.DEFAULT_MAX_HANDOFF_CHARS, group="repo:test",
                                  handoff=None)
        with mock.patch.object(self.fleet, "read_text", return_value=""), \
             mock.patch.object(self.fleet, "release_agent_strict", return_value={"freed": ["file:a"]}), \
             mock.patch.object(self.fleet, "find_agent", return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            self.fleet.cmd_complete(args)

        self.assertFalse(failed_file.exists())

    def test_fleet_rows_classifies_done_on_valid_repo_handoff_even_with_foreground_patterns(self):
        """When a valid repo handoff is present, fleet_rows reports done even if pane text contains 'Running pytest...'"""
        self.fleet.set_task(AGENT, "task with verification output", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(valid_handoff()))

        foreground_pane = "Running pytest tests/test_feature.py\n5 passed\n"
        with mock.patch.object(self.fleet, "agents", return_value=[{"name": AGENT, "agent": "agy", "agent_status": "working"}]), \
             mock.patch.object(self.fleet, "read_text", return_value=foreground_pane):
            rows = self.fleet.fleet_rows(session=SESSION)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["state"], "done")
            self.assertTrue(rows[0]["has_handoff"])

    def test_cmd_spawn_and_prompt_clean_up_repo_handoff_and_failed_raw(self):
        """Spawning or prompting an agent cleans up any stale repo handoff and failed raw files."""
        # 1. Setup stale files
        p = self.fleet.repo_handoff_path(AGENT, self.repo)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(valid_handoff()))
        failed_raw = self.fleet.failed_handoff_raw_path(AGENT, self.repo)
        failed_raw.write_text("stale raw")
        st_hf = self.fleet.handoff_path(AGENT, SESSION)
        st_hf.parent.mkdir(parents=True, exist_ok=True)
        st_hf.write_text(json.dumps(valid_handoff()))

        # 2. Test cmd_prompt cleanup
        self.fleet.set_task(AGENT, "old task", SESSION, "repo:test", ["file:a"], False, cwd=str(self.repo))
        prompt_args = argparse.Namespace(
            agent=AGENT, session=SESSION, text="follow-up task",
            resource=[], read_only=False, lock_timeout=0, group=None, related=True
        )
        with mock.patch.object(self.fleet, "pane_of", return_value="pane-1"), \
             mock.patch.object(self.fleet, "_submit", return_value=True):
            self.fleet.cmd_prompt(prompt_args)

        self.assertFalse(p.exists())
        self.assertFalse(failed_raw.exists())
        self.assertFalse(st_hf.exists())


if __name__ == "__main__":
    unittest.main()
