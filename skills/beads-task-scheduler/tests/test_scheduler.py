#!/usr/bin/env python3
"""Comprehensive test suite for beads-task-scheduler helper (scheduler.py)."""

import contextlib
from datetime import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import scheduler


class TestWorkspaceAndRepoResolution(unittest.TestCase):
    def test_workspace_base_with_dot_bv(self):
        ws_path = Path("/path/to/project/.bv/workspace.yaml")
        self.assertEqual(scheduler.workspace_base(ws_path), Path("/path/to/project"))

    def test_workspace_base_without_dot_bv(self):
        ws_path = Path("/path/to/project/workspace.yaml")
        self.assertEqual(scheduler.workspace_base(ws_path), Path("/path/to/project"))

    def test_parse_workspace_repos_valid(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            bv_dir = base / ".bv"
            bv_dir.mkdir()
            r1 = base / "services" / "api"
            r2 = base / "apps" / "web"
            (r1 / ".beads").mkdir(parents=True)
            (r2 / ".beads").mkdir(parents=True)

            ws_file = bv_dir / "workspace.yaml"
            ws_file.write_text(
                "repos:\n"
                "  - path: services/api # backend API\n"
                "  - path: 'apps/web'\n"
            )

            repos = scheduler.parse_workspace_repos(ws_file)
            self.assertEqual(repos, [r1.resolve(), r2.resolve()])

    def test_parse_workspace_repos_missing_file(self):
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_workspace_repos("/nonexistent/workspace.yaml")
        self.assertIn("workspace config not found", str(ctx.exception))

    def test_parse_workspace_repos_no_repos_entries(self):
        with tempfile.TemporaryDirectory() as td:
            ws_file = Path(td) / "workspace.yaml"
            ws_file.write_text("other_key: 123\n")
            with self.assertRaises(RuntimeError) as ctx:
                scheduler.parse_workspace_repos(ws_file)
            self.assertIn("has no explicit 'repos: - path:' entries", str(ctx.exception))

    def test_parse_workspace_repos_missing_beads_dir(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            r1 = base / "api"
            r1.mkdir()
            # api missing .beads/
            ws_file = base / "workspace.yaml"
            ws_file.write_text("repos:\n  - path: api\n")
            with self.assertRaises(RuntimeError) as ctx:
                scheduler.parse_workspace_repos(ws_file)
            self.assertIn("workspace repos missing .beads/", str(ctx.exception))

    def test_find_repos_both_project_and_workspace_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.find_repos(project="/repo1", workspace="/ws.yaml")
        self.assertIn("choose exactly one", str(ctx.exception))

    def test_find_repos_neither_and_cwd_no_beads_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("pathlib.Path.cwd", return_value=Path(td)):
                with self.assertRaises(RuntimeError) as ctx:
                    scheduler.find_repos(project=None, workspace=None)
                self.assertIn("choose exactly one", str(ctx.exception))

    def test_find_repos_project_missing_beads_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError) as ctx:
                scheduler.find_repos(project=td, workspace=None)
            self.assertIn("project has no .beads workspace", str(ctx.exception))

    def test_find_repos_repo_filter(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            r1 = base / "api"
            r2 = base / "web"
            (r1 / ".beads").mkdir(parents=True)
            (r2 / ".beads").mkdir(parents=True)
            ws_file = base / "workspace.yaml"
            ws_file.write_text("repos:\n  - path: api\n  - path: web\n")

            repos = scheduler.find_repos(project=None, workspace=ws_file, repo_filter="api")
            self.assertEqual(repos, [r1.resolve()])

            with self.assertRaises(RuntimeError) as ctx:
                scheduler.find_repos(project=None, workspace=ws_file, repo_filter="unknown")
            self.assertIn("--repo matched no configured repository", str(ctx.exception))


class TestAgentContextValidation(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/tmp/repo")

    def test_valid_mutating_context(self):
        raw = {
            "herder": {
                "resources": ["file:src/api.py"],
                "read_only": False,
                "cwd": "src",
                "verification": ["pytest -q"],
            }
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(res["resources"], ["file:src/api.py"])
        self.assertFalse(res["read_only"])
        self.assertEqual(res["cwd"], str((self.repo / "src").resolve()))
        self.assertEqual(res["verification"], ["pytest -q"])

    def test_valid_read_only_context(self):
        raw = {
            "read_only": True,
            "verification": ["make lint"],
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(res["resources"], [])
        self.assertTrue(res["read_only"])
        self.assertEqual(res["cwd"], str(self.repo.resolve()))
        self.assertEqual(res["verification"], ["make lint"])

    def test_runner_namespace_fallback(self):
        raw = {
            "runner": {
                "resources": ["file:app.js"],
                "verification": ["npm test"],
            }
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(res["resources"], ["file:app.js"])
        self.assertEqual(res["verification"], ["npm test"])

    def test_string_json_agent_context(self):
        raw = json.dumps({
            "herder": {
                "resources": ["api:db"],
                "verification": ["./test.sh"],
            }
        })
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(res["resources"], ["api:db"])
        self.assertEqual(res["verification"], ["./test.sh"])

    def test_mutating_without_resources_raises(self):
        raw = {"read_only": False, "verification": ["test"]}
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_agent_context(raw, self.repo)
        self.assertIn("mutating task must specify non-empty resources", str(ctx.exception))

    def test_invalid_resources_type_raises(self):
        raw = {"resources": 123, "verification": ["test"]}
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_agent_context(raw, self.repo)
        self.assertIn("resources must be a list", str(ctx.exception))

    def test_invalid_read_only_type_raises(self):
        raw = {"resources": ["file:a"], "read_only": "yes", "verification": ["test"]}
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_agent_context(raw, self.repo)
        self.assertIn("read_only must be a boolean", str(ctx.exception))

    def test_cwd_escaping_repo_raises(self):
        raw = {"resources": ["file:a"], "cwd": "../../escape", "verification": ["test"]}
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_agent_context(raw, self.repo)
        self.assertIn("cwd escapes repository root", str(ctx.exception))

    def test_invalid_cwd_type_raises(self):
        raw = {"resources": ["file:a"], "cwd": 999, "verification": ["test"]}
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_agent_context(raw, self.repo)
        self.assertIn("cwd must be a path string", str(ctx.exception))

    def test_empty_verification_raises(self):
        raw = {"resources": ["file:a"], "verification": []}
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_agent_context(raw, self.repo)
        self.assertIn("verification must be a list of non-empty strings", str(ctx.exception))

    def test_invalid_json_string_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_agent_context("not-json", self.repo)
        self.assertIn("invalid agent_context JSON", str(ctx.exception))


class TestHandoffValidation(unittest.TestCase):
    def valid_payload(self, **kwargs):
        base = {
            "status": "completed",
            "decisions": ["Approved approach"],
            "changed_files": ["src/api.py"],
            "verification": [{"command": "pytest -q", "exit_code": 0}],
            "blockers": [],
            "next_inputs": ["Proceed to step 2"],
        }
        base.update(kwargs)
        return base

    def test_valid_handoff_dict(self):
        payload = self.valid_payload()
        handoff, canonical = scheduler.parse_and_validate_handoff(payload)
        self.assertEqual(handoff["status"], "completed")
        self.assertTrue(canonical.startswith("FLEET_HANDOFF: {"))

    def test_valid_handoff_string_with_marker(self):
        payload = self.valid_payload()
        raw = f"Work done.\n\nFLEET_HANDOFF: {json.dumps(payload)}\n"
        handoff, canonical = scheduler.parse_and_validate_handoff(raw)
        self.assertEqual(handoff["status"], "completed")

    def test_valid_handoff_string_with_bullet_marker(self):
        payload = self.valid_payload()
        raw = f"Summary\n• FLEET_HANDOFF: {json.dumps(payload)}\n"
        handoff, canonical = scheduler.parse_and_validate_handoff(raw)
        self.assertEqual(handoff["status"], "completed")

    def test_valid_handoff_string_with_terminal_boundary(self):
        payload = self.valid_payload()
        raw = f"FLEET_HANDOFF: {json.dumps(payload)}\n──────── Worked for 42s ────────\n"
        handoff, canonical = scheduler.parse_and_validate_handoff(raw)
        self.assertEqual(handoff["status"], "completed")

    def test_valid_handoff_from_file_reference(self):
        payload = self.valid_payload()
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump(payload, f)
            f_path = f.name
        try:
            handoff, canonical = scheduler.parse_and_validate_handoff(f"@{f_path}")
            self.assertEqual(handoff["status"], "completed")
        finally:
            if os.path.exists(f_path):
                os.unlink(f_path)

    def test_missing_fields_raises(self):
        payload = {"status": "completed"}
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("handoff missing required fields", str(ctx.exception))

    def test_non_completed_status_raises(self):
        payload = self.valid_payload(status="blocked")
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("handoff status is 'blocked', expected 'completed'", str(ctx.exception))

    def test_active_blockers_raises(self):
        payload = self.valid_payload(blockers=["Waiting on auth"])
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("completed handoff lists active blockers", str(ctx.exception))

    def test_empty_verification_raises(self):
        payload = self.valid_payload(verification=[])
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("verification evidence list is empty", str(ctx.exception))

    def test_non_zero_exit_code_raises(self):
        payload = self.valid_payload(verification=[{"command": "pytest", "exit_code": 1}])
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("verification exits do not match expectations", str(ctx.exception))

    def test_declared_non_zero_expected_exit_succeeds(self):
        """A leak-check grep exits 1 when it finds nothing, which is the passing case."""
        leak_check = "grep -rn claude/skills skills"
        payload = self.valid_payload(
            verification=[{"command": leak_check, "exit_code": 1, "expected_exit": 1}]
        )
        handoff, _ = scheduler.parse_and_validate_handoff(
            payload, required_verification=[leak_check]
        )
        self.assertEqual(handoff["status"], "completed")

    def test_expected_exit_is_an_equality_check_not_a_free_pass(self):
        leak_check = "grep -rn claude/skills skills"
        payload = self.valid_payload(
            verification=[{"command": leak_check, "exit_code": 2, "expected_exit": 1}]
        )
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("verification exits do not match expectations", str(ctx.exception))

    def test_non_integer_expected_exit_raises(self):
        payload = self.valid_payload(
            verification=[{"command": "pytest", "exit_code": 1, "expected_exit": "1"}]
        )
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("'expected_exit' when present", str(ctx.exception))

    def test_trailing_prose_is_surfaced_not_refused(self):
        payload = self.valid_payload()
        raw = f"FLEET_HANDOFF: {json.dumps(payload)}\nI also changed another file."
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            handoff, _ = scheduler.parse_and_validate_handoff(raw)
        self.assertEqual(handoff["status"], "completed")
        self.assertIn("I also changed another file.", stderr.getvalue())

    def parse_capturing_stderr(self, raw, **kwargs):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            handoff, _ = scheduler.parse_and_validate_handoff(raw, **kwargs)
        return handoff, stderr.getvalue()

    def test_wrap_that_ate_a_space_keeps_the_tight_join_and_warns(self):
        """`git status --short` wrapped on its space rejoins as `git status--short`.

        The pane strips the trailing space, so it cannot be recovered. The tight join
        stays authoritative -- putting the space back would fabricate evidence the worker
        never printed -- and the rendering is flagged as inexact instead.
        """
        payload = self.valid_payload(verification=[
            {"command": "git status --short", "exit_code": 0},
        ])
        raw = "FLEET_HANDOFF: " + json.dumps(payload).replace(
            "git status --short", "git status\n  --short"
        )
        handoff, warnings = self.parse_capturing_stderr(raw)
        self.assertEqual(handoff["verification"][0]["command"], "git status--short")
        self.assertIn("may be missing a space", warnings)

    def test_mid_token_wrap_warns_although_its_join_was_exact(self):
        """The known false positive: a mid-token wrap is indistinguishable from a lost space.

        Recorded rather than fixed -- the captured text carries no evidence separating the
        two cases, so the warning errs toward reporting.
        """
        payload = self.valid_payload()
        raw = "FLEET_HANDOFF: " + json.dumps(payload).replace('"completed"', '"comple\n  ted"')
        handoff, warnings = self.parse_capturing_stderr(raw)
        self.assertEqual(handoff, payload)
        self.assertIn("may be missing a space", warnings)

    def test_wrap_between_json_tokens_does_not_warn(self):
        """Pretty-printed handoffs wrap outside string values, so they must stay quiet."""
        payload = self.valid_payload()
        raw = f"FLEET_HANDOFF: {json.dumps(payload, indent=2)}\n"
        handoff, warnings = self.parse_capturing_stderr(raw)
        self.assertEqual(handoff, payload)
        self.assertEqual(warnings, "")

    def test_unwrapped_handoff_does_not_warn(self):
        _, warnings = self.parse_capturing_stderr(
            f"FLEET_HANDOFF: {json.dumps(self.valid_payload())}\n"
        )
        self.assertEqual(warnings, "")

    def test_missing_required_verification_commands_raises(self):
        payload = self.valid_payload(verification=[{"command": "pytest", "exit_code": 0}])
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload, required_verification=["pytest", "mypy"])
        self.assertIn("required verification commands missing from evidence", str(ctx.exception))

    def test_matching_required_verification_commands_succeeds(self):
        payload = self.valid_payload(verification=[
            {"command": "pytest", "exit_code": 0},
            {"command": "mypy", "exit_code": 0},
        ])
        handoff, _ = scheduler.parse_and_validate_handoff(payload, required_verification=["pytest", "mypy"])
        self.assertEqual(handoff["status"], "completed")


class TestViewerAndTaskExtraction(unittest.TestCase):
    def test_extract_bead_task_text_all_fields(self):
        issue = {
            "id": "task-1",
            "title": "Build feature",
            "description": "Desc text",
            "design": "Design text",
            "acceptance_criteria": "Must pass tests",
            "notes": "Some notes",
        }
        text = scheduler.extract_bead_task_text(issue)
        self.assertIn("Complete Beads task task-1: Build feature", text)
        self.assertIn("Objective:\nDesc text", text)
        self.assertIn("Design:\nDesign text", text)
        self.assertIn("Acceptance criteria:\nMust pass tests", text)
        self.assertIn("Notes:\nSome notes", text)

    def test_extract_bead_task_text_minimal(self):
        issue = {"id": "task-2", "title": "Minimal"}
        text = scheduler.extract_bead_task_text(issue)
        self.assertEqual(text, "Complete Beads task task-2: Minimal")

    @patch("scheduler.viewer_executable", return_value="bv")
    @patch("scheduler.run_command_json")
    def test_query_viewer_ranking_with_tracks(self, mock_run, mock_exec):
        mock_run.return_value = {
            "plan": {
                "tracks": [
                    {"items": [{"id": "item-1"}, {"id": "item-2"}]},
                    {"items": [{"id": "item-3"}]},
                ]
            }
        }
        ids, viewer = scheduler.query_viewer_ranking("/path/to/.bv/workspace.yaml")
        self.assertEqual(ids, ["item-1", "item-2", "item-3"])
        self.assertEqual(viewer, "bv")

    @patch("scheduler.viewer_executable", return_value="bv")
    @patch("scheduler.run_command_json")
    def test_query_viewer_ranking_with_phases(self, mock_run, mock_exec):
        mock_run.return_value = {
            "plan": {
                "phases": [
                    {"issues": [{"id": "phase-1"}, "phase-2"]},
                ]
            }
        }
        ids, viewer = scheduler.query_viewer_ranking("/path/to/.bv/workspace.yaml")
        self.assertEqual(ids, ["phase-1", "phase-2"])
        self.assertEqual(viewer, "bv")

    @patch("scheduler.viewer_executable", return_value=None)
    def test_query_viewer_ranking_no_viewer(self, mock_exec):
        ids, viewer = scheduler.query_viewer_ranking("/path/to/.bv/workspace.yaml")
        self.assertEqual(ids, [])
        self.assertIsNone(viewer)


class TestCloseOrConfirm(unittest.TestCase):
    """`br close` failing is not proof the close did not land -- ask the bead."""

    CLOSE = ["br", "close", "sim-1", "--reason", "done", "--actor", "A", "--json"]

    @patch("scheduler.run_command_json")
    def test_error_on_an_already_closed_bead_is_reported_as_closed(self, mock_run):
        """Observed on br 0.2.11: NOTHING_TO_DO on a bead the herder had to close.

        Treating it as a refusal makes the caller roll back finished work, so the bead's own
        status decides. No retry is attempted once it reads as closed.
        """
        mock_run.side_effect = [
            RuntimeError("br close failed (3): NOTHING_TO_DO"),
            [{"id": "sim-1", "status": "closed"}],
        ]
        result = scheduler._close_or_confirm(self.CLOSE, "sim-1", Path("/repo"))
        self.assertEqual(result["status"], "closed")
        self.assertIn("NOTHING_TO_DO", result["confirmed_after_error"])
        self.assertEqual(mock_run.call_count, 2)

    @patch("scheduler.run_command_json")
    def test_still_open_after_an_error_is_retried_once(self, mock_run):
        mock_run.side_effect = [
            RuntimeError("br close failed (3): NOTHING_TO_DO"),
            [{"id": "sim-1", "status": "in_progress"}],
            [{"id": "sim-1", "status": "closed", "close_reason": "done"}],
        ]
        result = scheduler._close_or_confirm(self.CLOSE, "sim-1", Path("/repo"))
        self.assertEqual(result[0]["status"], "closed")

    @patch("scheduler.run_command_json")
    def test_two_failed_closes_on_an_open_bead_raise(self, mock_run):
        """The retry is one attempt, not a loop: a bead that will not close must be parked."""
        mock_run.side_effect = [
            RuntimeError("close failed: first"),
            [{"id": "sim-1", "status": "in_progress"}],
            RuntimeError("close failed: second"),
            [{"id": "sim-1", "status": "in_progress"}],
        ]
        with self.assertRaises(RuntimeError) as ctx:
            scheduler._close_or_confirm(self.CLOSE, "sim-1", Path("/repo"))
        self.assertIn("failed twice", str(ctx.exception))
        self.assertIn("second", str(ctx.exception))

    @patch("scheduler.run_command_json")
    def test_unreadable_bead_after_an_error_does_not_claim_success(self, mock_run):
        mock_run.side_effect = [
            RuntimeError("close failed: first"),
            RuntimeError("br show failed"),
            RuntimeError("close failed: second"),
            RuntimeError("br show failed"),
        ]
        with self.assertRaises(RuntimeError):
            scheduler._close_or_confirm(self.CLOSE, "sim-1", Path("/repo"))


class TestBeadsLifecycleIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("br"):
            raise unittest.SkipTest("br executable not available")

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        subprocess.run(["br", "init", "--prefix", "schedtest"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_full_claim_rollback_and_close_lifecycle(self):
        context = {
            "herder": {
                "resources": ["file:src/test.py"],
                "read_only": False,
                "cwd": ".",
                "verification": ["pytest -q"],
            }
        }

        # 1. Create a bead
        create_res = subprocess.run(
            [
                "br", "create",
                "--title", "Test Bead Lifecycle",
                "--description", "Verify scheduler helper functions",
                "--json",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        created_issue = json.loads(create_res.stdout)
        issue_id = created_issue.get("id") or (created_issue[0].get("id") if isinstance(created_issue, list) else None)
        self.assertTrue(issue_id)

        # Attach agent context via br update
        subprocess.run(
            [
                "br", "update", issue_id,
                "--agent-context", json.dumps(context),
                "--json",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

        # 2. List ready candidates
        candidates, unresolvable_errors, meta = scheduler.list_ready_candidates(project=self.repo, workspace=None)
        self.assertEqual(meta["count"], 1)
        self.assertEqual(meta["unresolvable_count"], 0)
        self.assertEqual(unresolvable_errors, [])
        self.assertEqual(candidates[0]["id"], issue_id)
        self.assertEqual(candidates[0]["context"]["resources"], ["file:src/test.py"])
        self.assertEqual(candidates[0]["chain_depth"], 0)
        self.assertEqual(candidates[0]["dependent_count"], 0)

        # 3. Claim bead
        claim_res = scheduler.claim_bead(issue_id, actor="agent-test", repo=self.repo)
        self.assertIsNotNone(claim_res)

        # Verify status is in_progress
        shown = scheduler.run_command_json(["br", "show", issue_id, "--json"], cwd=self.repo)
        issue_obj = shown[0] if isinstance(shown, list) else shown
        self.assertEqual(issue_obj.get("status"), "in_progress")
        self.assertEqual(issue_obj.get("assignee"), "agent-test")

        # 4. Rollback claim
        roll_res = scheduler.rollback_claim(issue_id, actor="agent-test", repo=self.repo)
        self.assertIsNotNone(roll_res)

        shown = scheduler.run_command_json(["br", "show", issue_id, "--json"], cwd=self.repo)
        issue_obj = shown[0] if isinstance(shown, list) else shown
        self.assertEqual(issue_obj.get("status"), "open")
        self.assertEqual(issue_obj.get("assignee") or "", "")

        # 5. Re-claim and close with evidence
        scheduler.claim_bead(issue_id, actor="agent-test", repo=self.repo)

        handoff_payload = {
            "status": "completed",
            "decisions": ["Completed task successfully"],
            "changed_files": ["src/test.py"],
            "verification": [{"command": "pytest -q", "exit_code": 0}],
            "blockers": [],
            "next_inputs": [],
        }

        close_res = scheduler.close_bead_with_evidence(
            issue_id=issue_id,
            actor="agent-test",
            handoff_raw=handoff_payload,
            repo=self.repo,
            reason="Verified by test suite",
        )
        self.assertEqual(close_res["status"], "closed")

        # Verify issue is closed
        shown = scheduler.run_command_json(["br", "show", issue_id, "--json"], cwd=self.repo)
        issue_obj = shown[0] if isinstance(shown, list) else shown
        self.assertEqual(issue_obj.get("status"), "closed")

        # Verify close ledger was written
        ledger_path = scheduler.close_ledger_path(self.repo)
        self.assertTrue(ledger_path.is_file())
        records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], issue_id)
        self.assertEqual(records[0]["bead_id"], issue_id)
        self.assertEqual(records[0]["actor"], "agent-test")
        self.assertEqual(records[0]["verification"], [{"command": "pytest -q", "exit_code": 0}])
        self.assertIsNotNone(datetime.fromisoformat(records[0]["timestamp"]))

    def test_compute_chain_depth_with_blocking_dependents(self):
        ctx = {"resources": ["file:src/test.py"], "verification": [{"command": "true"}]}
        res_a = subprocess.run(["br", "create", "Task Chain A", "--json"], cwd=self.repo, check=True, capture_output=True, text=True)
        res_b = subprocess.run(["br", "create", "Task Chain B", "--json"], cwd=self.repo, check=True, capture_output=True, text=True)
        data_a = json.loads(res_a.stdout)
        data_b = json.loads(res_b.stdout)
        id_a = data_a.get("id") or data_a[0].get("id")
        id_b = data_b.get("id") or data_b[0].get("id")

        subprocess.run(["br", "update", id_a, "--agent-context", json.dumps(ctx), "--json"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["br", "update", id_b, "--agent-context", json.dumps(ctx), "--json"], cwd=self.repo, check=True, capture_output=True)
        # Add dependency: id_a blocks id_b
        subprocess.run(["br", "dep", "add", id_b, id_a, "--type", "blocks", "--json"], cwd=self.repo, check=True, capture_output=True)

        candidates, _, _ = scheduler.list_ready_candidates(project=self.repo, workspace=None)
        cand_map = {c["id"]: c for c in candidates}
        if id_a in cand_map:
            self.assertGreaterEqual(cand_map[id_a]["chain_depth"], 1)
            self.assertEqual(cand_map[id_a]["dependent_count"], 1)

    def test_multi_repo_workspace_and_duplicate_detection(self):
        # Create second repo
        repo2_dir = Path(self.tmpdir.name) / "repo2"
        repo2_dir.mkdir()
        subprocess.run(["br", "init", "--prefix", "repo2"], cwd=repo2_dir, check=True, capture_output=True)

        # Create workspace.yaml
        ws_file = self.repo / "workspace.yaml"
        ws_file.write_text(f"repos:\n  - path: {self.repo}\n  - path: {repo2_dir}\n")

        # Create bead in repo2 with agent context
        create_res = subprocess.run(
            ["br", "create", "--title", "Task in Repo 2", "--json"],
            cwd=repo2_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        issue2 = json.loads(create_res.stdout)
        issue2_id = issue2.get("id") or (issue2[0].get("id") if isinstance(issue2, list) else None)

        subprocess.run(
            [
                "br", "update", issue2_id,
                "--agent-context", json.dumps({
                    "herder": {
                        "resources": ["file:repo2.txt"],
                        "verification": ["echo ok"],
                    }
                }),
                "--json",
            ],
            cwd=repo2_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        candidates, unresolvable_errors, meta = scheduler.list_ready_candidates(project=None, workspace=ws_file)
        self.assertGreaterEqual(meta["count"], 1)
        self.assertEqual(meta["unresolvable_count"], 0)
        found_ids = [c["id"] for c in candidates]
        self.assertIn(issue2_id, found_ids)


class TestCloseLedger(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        (self.repo / ".beads").mkdir(parents=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def valid_handoff(self, **kwargs):
        base = {
            "status": "completed",
            "decisions": ["Completed task successfully"],
            "changed_files": ["src/api.py"],
            "verification": [{"command": "pytest -q", "exit_code": 0}],
            "blockers": [],
            "next_inputs": [],
        }
        base.update(kwargs)
        return base

    @patch("scheduler.run_command_json")
    def test_record_appended_on_successful_close_with_all_fields_and_parseable_timestamp(self, mock_run):
        mock_run.side_effect = [
            [{"id": "task-100", "title": "Fix bug", "agent_context": {}}],  # br show
            {"id": "task-100", "comment_id": 1},                           # br comments add
            [{"id": "task-100", "status": "closed"}],                       # br close
        ]
        handoff = self.valid_handoff(
            verification=[{"command": "pytest -q", "exit_code": 0, "expected_exit": 0}]
        )
        result = scheduler.close_bead_with_evidence(
            issue_id="task-100",
            actor="worker-alpha",
            handoff_raw=handoff,
            repo=self.repo,
        )
        self.assertEqual(result["status"], "closed")

        ledger_file = scheduler.close_ledger_path(self.repo)
        self.assertTrue(ledger_file.is_file())

        lines = [line.strip() for line in ledger_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)

        record = json.loads(lines[0])
        self.assertEqual(record["id"], "task-100")
        self.assertEqual(record["bead_id"], "task-100")
        self.assertEqual(record["actor"], "worker-alpha")
        self.assertEqual(record["repo"], str(self.repo.resolve()))
        self.assertEqual(
            record["verification"],
            [{"command": "pytest -q", "exit_code": 0, "expected_exit": 0}],
        )
        self.assertEqual(record["handoff"]["status"], "completed")

        # Verify timestamp is valid ISO-8601 UTC
        ts = datetime.fromisoformat(record["timestamp"])
        self.assertIsNotNone(ts)

    @patch("scheduler.run_command_json")
    def test_two_successive_closes_produce_two_records_and_first_survives_intact(self, mock_run):
        mock_run.side_effect = [
            # First close: bead-1
            [{"id": "bead-1", "title": "Bead 1", "agent_context": {}}],
            {"id": "bead-1", "comment_id": 1},
            [{"id": "bead-1", "status": "closed"}],
            # Second close: bead-2
            [{"id": "bead-2", "title": "Bead 2", "agent_context": {}}],
            {"id": "bead-2", "comment_id": 2},
            [{"id": "bead-2", "status": "closed"}],
        ]

        scheduler.close_bead_with_evidence(
            issue_id="bead-1",
            actor="worker-1",
            handoff_raw=self.valid_handoff(),
            repo=self.repo,
        )

        ledger_file = scheduler.close_ledger_path(self.repo)
        first_raw_content = ledger_file.read_text(encoding="utf-8")
        first_lines = [l for l in first_raw_content.splitlines() if l.strip()]
        self.assertEqual(len(first_lines), 1)

        scheduler.close_bead_with_evidence(
            issue_id="bead-2",
            actor="worker-2",
            handoff_raw=self.valid_handoff(changed_files=["src/other.py"]),
            repo=self.repo,
        )

        second_raw_content = ledger_file.read_text(encoding="utf-8")
        second_lines = [l for l in second_raw_content.splitlines() if l.strip()]
        self.assertEqual(len(second_lines), 2)

        # First line survived completely intact
        self.assertEqual(second_lines[0], first_lines[0])

        record_1 = json.loads(second_lines[0])
        record_2 = json.loads(second_lines[1])
        self.assertEqual(record_1["id"], "bead-1")
        self.assertEqual(record_2["id"], "bead-2")
        self.assertEqual(record_2["actor"], "worker-2")

    @patch("scheduler.append_close_ledger")
    @patch("scheduler.run_command_json")
    def test_close_whose_ledger_write_fails_still_reports_success_and_warns(self, mock_run, mock_append):
        mock_run.side_effect = [
            [{"id": "bead-err", "title": "Bead Err", "agent_context": {}}],
            {"id": "bead-err", "comment_id": 1},
            [{"id": "bead-err", "status": "closed"}],
        ]
        mock_append.side_effect = OSError("Read-only file system")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = scheduler.close_bead_with_evidence(
                issue_id="bead-err",
                actor="worker-safe",
                handoff_raw=self.valid_handoff(),
                repo=self.repo,
            )

        # Must report success without raising
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["id"], "bead-err")

        # Must warn on stderr
        self.assertIn("warning: failed to write closure ledger for bead-err", stderr.getvalue())
        self.assertIn("Read-only file system", stderr.getvalue())

    @patch("scheduler.run_command_json")
    def test_no_record_written_when_handoff_rejected(self, mock_run):
        mock_run.return_value = [{"id": "bead-reject", "title": "Reject", "agent_context": {}}]
        invalid_handoff = self.valid_handoff(status="blocked", blockers=["Blocked on network"])

        with self.assertRaises(RuntimeError) as ctx:
            scheduler.close_bead_with_evidence(
                issue_id="bead-reject",
                actor="worker-1",
                handoff_raw=invalid_handoff,
                repo=self.repo,
            )
        self.assertIn("handoff status is 'blocked'", str(ctx.exception))

        ledger_file = scheduler.close_ledger_path(self.repo)
        self.assertFalse(ledger_file.exists())

    @patch("scheduler.run_command_json")
    def test_no_record_written_when_close_fails(self, mock_run):
        mock_run.side_effect = [
            [{"id": "bead-fail", "title": "Fail Close", "agent_context": {}}],  # br show
            {"id": "bead-fail", "comment_id": 1},                              # br comments add
            RuntimeError("br close failed (1): lock contention"),               # br close try 1
            [{"id": "bead-fail", "status": "in_progress"}],                    # _issue_state check 1
            RuntimeError("br close failed (1): lock contention"),               # br close try 2
            [{"id": "bead-fail", "status": "in_progress"}],                    # _issue_state check 2
        ]

        with self.assertRaises(RuntimeError) as ctx:
            scheduler.close_bead_with_evidence(
                issue_id="bead-fail",
                actor="worker-1",
                handoff_raw=self.valid_handoff(),
                repo=self.repo,
            )
        self.assertIn("br close failed twice", str(ctx.exception))

        ledger_file = scheduler.close_ledger_path(self.repo)
        self.assertFalse(ledger_file.exists())

    @patch("scheduler.run_command_json")
    def test_milestone_blocked_close_retries_with_force(self, mock_run):
        mock_run.side_effect = [
            [{"id": "bead-child", "title": "Child Task", "agent_context": {}}],
            {"id": "bead-child", "comment_id": 1},
            RuntimeError("br close failed (1): blocked by: milestone-1"),
            [{"id": "bead-child", "status": "in_progress"}],
            [{"id": "bead-child", "status": "closed"}],
        ]

        result = scheduler.close_bead_with_evidence(
            issue_id="bead-child",
            actor="worker-1",
            handoff_raw=self.valid_handoff(),
            repo=self.repo,
        )
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["id"], "bead-child")
        close_calls = [c for c in mock_run.call_args_list if len(c[0][0]) > 1 and c[0][0][1] == "close"]
        self.assertEqual(len(close_calls), 2)
        self.assertIn("--force", close_calls[1][0][0])

    @patch("scheduler.run_command_json")
    def test_custom_ledger_path(self, mock_run):
        mock_run.side_effect = [
            [{"id": "bead-custom", "title": "Custom", "agent_context": {}}],
            {"id": "bead-custom", "comment_id": 1},
            [{"id": "bead-custom", "status": "closed"}],
        ]
        custom_ledger = self.repo / "custom-ledger" / "closed.jsonl"

        scheduler.close_bead_with_evidence(
            issue_id="bead-custom",
            actor="worker-custom",
            handoff_raw=self.valid_handoff(),
            repo=self.repo,
            ledger_path=custom_ledger,
        )

        self.assertTrue(custom_ledger.is_file())
        self.assertFalse(scheduler.close_ledger_path(self.repo).exists())
        data = json.loads(custom_ledger.read_text(encoding="utf-8").strip())
        self.assertEqual(data["id"], "bead-custom")


class TestSchedulerCliSubcommands(unittest.TestCase):
    def test_cli_help(self):
        res = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "scheduler.py"), "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Beads Task Scheduler", res.stdout)
        self.assertIn("ready", res.stdout)
        self.assertIn("claim", res.stdout)
        self.assertIn("rollback", res.stdout)
        self.assertIn("close", res.stdout)
        self.assertIn("show", res.stdout)
        self.assertIn("coordination", res.stdout)

    def test_cli_subcommands_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["br", "init", "--prefix", "clitest"], cwd=repo, check=True, capture_output=True)

            # Create task with agent context
            create_res = subprocess.run(
                ["br", "create", "--title", "CLI Task", "--json"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            created_issue = json.loads(create_res.stdout)
            issue_id = created_issue.get("id") or (created_issue[0].get("id") if isinstance(created_issue, list) else None)
            subprocess.run(
                [
                    "br", "update", issue_id,
                    "--agent-context", json.dumps({
                        "herder": {
                            "resources": ["file:cli.txt"],
                            "verification": ["echo ok"],
                        }
                    }),
                    "--json",
                ],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )

            # 1. ready CLI
            res_ready = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "scheduler.py"), "ready", "--project", str(repo), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(res_ready.stdout)
            self.assertEqual(data["planning"]["count"], 1)
            self.assertEqual(data["planning"]["unresolvable_count"], 0)
            self.assertEqual(data["unresolvable_errors"], [])
            target_id = data["ready"][0]["id"]

            # 2. show CLI
            res_show = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "scheduler.py"), "show", target_id, "--project", str(repo), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            show_data = json.loads(res_show.stdout)
            self.assertEqual(show_data["issue"]["id"], target_id)

            # 3. claim CLI
            res_claim = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "scheduler.py"), "claim", target_id, "--actor", "worker-cli", "--project", str(repo), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn(target_id, res_claim.stdout)

            # 4. rollback CLI
            res_roll = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "scheduler.py"), "rollback", target_id, "--actor", "worker-cli", "--project", str(repo), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn(target_id, res_roll.stdout)

            # 5. coordination CLI
            res_coord = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "scheduler.py"), "coordination", "--project", str(repo), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("{", res_coord.stdout)

            # 6. claim and close CLI
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "scheduler.py"), "claim", target_id, "--actor", "worker-cli", "--project", str(repo), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            handoff = {
                "status": "completed",
                "decisions": ["Closed via CLI"],
                "changed_files": ["cli.txt"],
                "verification": [{"command": "echo ok", "exit_code": 0}],
                "blockers": [],
                "next_inputs": [],
            }
            res_close = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "scheduler.py"),
                    "close",
                    target_id,
                    "--actor",
                    "worker-cli",
                    "--handoff",
                    json.dumps(handoff),
                    "--project",
                    str(repo),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            close_json = json.loads(res_close.stdout)
            self.assertEqual(close_json["status"], "closed")

            # Check ledger
            ledger_file = scheduler.close_ledger_path(repo)
            self.assertTrue(ledger_file.is_file())
            ledger_lines = [l for l in ledger_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(len(ledger_lines), 1)
            self.assertEqual(json.loads(ledger_lines[0])["id"], target_id)


class TestDurablePaneIdMapping(unittest.TestCase):
    def test_valid_bead_id_has_no_runner_length_limit(self):
        scheduler.validate_bead_id("bead-short")
        scheduler.validate_bead_id("a" * 128)

    def test_long_id_auto_derives_short_mapping(self):
        long_id = "a" * 28
        pane_id = scheduler.pane_id_for_issue(long_id, {"herder": {}})
        self.assertTrue(pane_id.startswith("bead-"))
        self.assertLessEqual(len(pane_id), 32)
        self.assertTrue(scheduler.HERDR_AGENT_NAME_RE.fullmatch(pane_id))

    def test_long_id_uses_legal_durable_mapping(self):
        self.assertEqual(
            scheduler.pane_id_for_issue("a" * 64, {"herder": {"pane_id": "bead-short-a"}}),
            "bead-short-a",
        )

    def test_invalid_or_duplicate_mapping_is_refused_before_claim(self):
        long_id = "a" * 28
        with self.assertRaises(RuntimeError):
            scheduler.pane_id_for_issue(long_id, {"herder": {"pane_id": "Bead-Bad"}})

        issue = {"id": long_id, "agent_context": {"herder": {"pane_id": "bead-short-a"}}}
        duplicate = {"id": "other-bead", "agent_context": {"herder": {"pane_id": "bead-short-a"}}}
        calls = []
        def fake_run(command, cwd=None):
            calls.append(command)
            if command[1] == "show":
                return [issue]
            if command[1] == "list":
                return [issue, duplicate]
            self.fail(f"claim must not run after duplicate mapping: {command}")
        with patch.object(scheduler, "run_command_json", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                scheduler.claim_bead(long_id, "bead-short-a", "/tmp")
        self.assertIn("already mapped", str(ctx.exception))
        self.assertFalse(any(call[1] == "update" for call in calls))

    def test_resolve_pane_uses_durable_metadata_not_truncation(self):
        long_id = "agent-skills-this-is-a-long-durable-id"
        issues = [{"id": long_id, "agent_context": {"herder": {"pane_id": "bead-gsl4-a"}}}]
        with patch.object(scheduler, "run_command_json", return_value=issues):
            self.assertEqual(scheduler.resolve_bead_id_for_pane("bead-gsl4-a", "/tmp"), long_id)

    def test_long_mapping_binds_rollback_and_closure_to_the_pane(self):
        long_id = "agent-skills-this-is-a-long-durable-id"
        issue = {"id": long_id, "agent_context": {"herder": {"pane_id": "bead-gsl4-a"}}}
        with patch.object(scheduler, "run_command_json", return_value=[issue]):
            with self.assertRaisesRegex(RuntimeError, "must be rolled back by mapped pane"):
                scheduler.rollback_claim(long_id, "bead-wrong", "/tmp")
        with patch.object(scheduler, "run_command_json", return_value=[issue]):
            with self.assertRaisesRegex(RuntimeError, "must be closed by mapped pane"):
                scheduler.close_bead_with_evidence(long_id, "bead-wrong", {}, "/tmp")

    def test_bead_id_empty_or_whitespace_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.validate_bead_id("")
        self.assertIn("bead ID must be a non-empty string", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            scheduler.validate_bead_id("   ")
        self.assertIn("bead ID must be a non-empty string", str(ctx.exception))

    def test_bead_id_non_string_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.validate_bead_id(123)  # type: ignore[arg-type]
        self.assertIn("bead ID must be a non-empty string", str(ctx.exception))

    def test_claim_bead_validates_id(self):
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.claim_bead("", actor="test", repo="/tmp")
        self.assertIn("bead ID must be a non-empty string", str(ctx.exception))

    def test_claim_bead_with_unrelated_unmapped_bead_in_repo(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            def fake_run(cmd, cwd=None):
                if cmd[:2] == ["br", "show"] and cmd[2] == "long-bead-with-valid-mapping-1":
                    return [{
                        "id": "long-bead-with-valid-mapping-1",
                        "agent_context": {"herder": {"pane_id": "bead-valid-1"}},
                    }]
                if cmd[:2] == ["br", "list"]:
                    return [
                        {
                            "id": "long-bead-with-valid-mapping-1",
                            "agent_context": {"herder": {"pane_id": "bead-valid-1"}},
                        },
                        {
                            "id": "long-bead-with-unmapped-missing-pane-id-2",
                            "agent_context": {"herder": {}},
                        },
                    ]
                if cmd[:2] == ["br", "update"]:
                    return {"id": "long-bead-with-valid-mapping-1", "status": "in_progress"}
                raise AssertionError(f"unexpected command: {cmd}")

            with patch.object(scheduler, "run_command_json", side_effect=fake_run):
                res = scheduler.claim_bead("long-bead-with-valid-mapping-1", actor="bead-valid-1", repo=repo)
                self.assertEqual(res["status"], "in_progress")

    def test_resolve_bead_id_for_pane_with_unrelated_unmapped_bead(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            def fake_run(cmd, cwd=None):
                if cmd[:2] == ["br", "list"]:
                    return [
                        {
                            "id": "long-bead-with-valid-mapping-1",
                            "agent_context": {"herder": {"pane_id": "bead-valid-1"}},
                        },
                        {
                            "id": "long-bead-with-unmapped-missing-pane-id-2",
                            "agent_context": {"herder": {}},
                        },
                    ]
                raise AssertionError(f"unexpected command: {cmd}")

            with patch.object(scheduler, "run_command_json", side_effect=fake_run):
                resolved = scheduler.resolve_bead_id_for_pane("bead-valid-1", repo=repo)
                self.assertEqual(resolved, "long-bead-with-valid-mapping-1")

    def test_rollback_claim_validates_id(self):
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.rollback_claim("", actor="test", repo="/tmp")
        self.assertIn("bead ID must be a non-empty string", str(ctx.exception))

    def test_close_bead_validates_id(self):
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.close_bead_with_evidence("", actor="test", handoff_raw={}, repo="/tmp")
        self.assertIn("bead ID must be a non-empty string", str(ctx.exception))


class TestExpectedExitAndFailuresValidation(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/tmp/repo")

    def test_parse_context_top_level_expected_exit_and_failures(self):
        raw = {
            "herder": {
                "resources": ["file:src/test.py"],
                "expected_exit": 1,
                "expected_failures": 3,
                "verification": ["pytest -q"],
            }
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(res["expected_exit"], 1)
        self.assertEqual(res["expected_failures"], 3)
        self.assertEqual(res["verification_specs"], [{"command": "pytest -q", "expected_exit": 1, "expected_failures": 3}])

    def test_parse_context_root_level_expected_exit_and_failures(self):
        raw = {
            "expected_exit": 1,
            "expected_failures": 2,
            "herder": {
                "resources": ["file:src/test.py"],
                "verification": ["pytest -q"],
            }
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(res["expected_exit"], 1)
        self.assertEqual(res["expected_failures"], 2)
        self.assertEqual(res["verification_specs"], [{"command": "pytest -q", "expected_exit": 1, "expected_failures": 2}])

    def test_parse_context_verification_dict_expected_exit_and_failures(self):
        raw = {
            "herder": {
                "resources": ["file:src/test.py"],
                "verification": {
                    "expected_exit": 1,
                    "expected_failures": 4,
                    "commands": ["pytest -q tests/test_a.py", "pytest -q tests/test_b.py"],
                },
            }
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(res["expected_exit"], 1)
        self.assertEqual(res["expected_failures"], 4)
        self.assertEqual(len(res["verification_specs"]), 2)
        self.assertEqual(res["verification_specs"][0], {"command": "pytest -q tests/test_a.py", "expected_exit": 1, "expected_failures": 4})
        self.assertEqual(res["verification_specs"][1], {"command": "pytest -q tests/test_b.py", "expected_exit": 1, "expected_failures": 4})

    def test_parse_context_per_command_override(self):
        raw = {
            "herder": {
                "resources": ["file:src/test.py"],
                "expected_exit": 1,
                "expected_failures": 2,
                "verification": [
                    "pytest -q tests/known_red.py",
                    {"command": "pytest -q tests/green.py", "expected_exit": 0, "expected_failures": 0},
                ],
            }
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(res["verification_specs"][0], {"command": "pytest -q tests/known_red.py", "expected_exit": 1, "expected_failures": 2})
        self.assertEqual(res["verification_specs"][1], {"command": "pytest -q tests/green.py", "expected_exit": 0, "expected_failures": 0})

    def test_parse_context_invalid_expected_exit_type_raises(self):
        for invalid in ("1", 1.5, True, False, []):
            raw = {
                "herder": {
                    "resources": ["file:src/test.py"],
                    "expected_exit": invalid,
                    "verification": ["pytest -q"],
                }
            }
            with self.assertRaises(RuntimeError) as ctx:
                scheduler.parse_agent_context(raw, self.repo)
            self.assertIn("expected_exit must be an integer", str(ctx.exception))

    def test_parse_context_invalid_expected_failures_type_raises(self):
        for invalid in ("2", 2.5, True, False, -1, []):
            raw = {
                "herder": {
                    "resources": ["file:src/test.py"],
                    "expected_failures": invalid,
                    "verification": ["pytest -q"],
                }
            }
            with self.assertRaises(RuntimeError) as ctx:
                scheduler.parse_agent_context(raw, self.repo)
            self.assertIn("expected_failures must be a non-negative integer", str(ctx.exception))

    def test_handoff_declared_expected_failures_succeeds(self):
        payload = {
            "status": "completed",
            "decisions": ["Known-red baseline matches expected failure count"],
            "changed_files": ["src/test.py"],
            "verification": [
                {"command": "pytest -q", "exit_code": 1, "expected_exit": 1, "expected_failures": 3, "failures": 3}
            ],
            "blockers": [],
            "next_inputs": [],
        }
        handoff, _ = scheduler.parse_and_validate_handoff(payload)
        self.assertEqual(handoff["status"], "completed")

    def test_handoff_mismatched_expected_failures_raises(self):
        payload = {
            "status": "completed",
            "decisions": ["Different failure count than expected"],
            "changed_files": ["src/test.py"],
            "verification": [
                {"command": "pytest -q", "exit_code": 1, "expected_exit": 1, "expected_failures": 3, "failures": 4}
            ],
            "blockers": [],
            "next_inputs": [],
        }
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("verification exits do not match expectations", str(ctx.exception))

    def test_handoff_missing_expected_failures_raises_when_required(self):
        payload = {
            "status": "completed",
            "decisions": ["No failures reported"],
            "changed_files": ["src/test.py"],
            "verification": [
                {"command": "pytest -q", "exit_code": 1, "expected_exit": 1}
            ],
            "blockers": [],
            "next_inputs": [],
        }
        req = {"expected_exit": 1, "expected_failures": 3, "verification": ["pytest -q"]}
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload, required_verification=req)
        self.assertIn("verification exits do not match expectations", str(ctx.exception))

    def test_handoff_unexpected_failures_raises_when_not_expected(self):
        payload = {
            "status": "completed",
            "decisions": ["Unexpected failures reported"],
            "changed_files": ["src/test.py"],
            "verification": [
                {"command": "pytest -q", "exit_code": 0, "failures": 2}
            ],
            "blockers": [],
            "next_inputs": [],
        }
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload)
        self.assertIn("verification exits do not match expectations", str(ctx.exception))

    def test_handoff_with_parsed_context_dict_specs(self):
        req_ctx = {
            "expected_exit": 1,
            "expected_failures": 2,
            "verification_specs": [
                {"command": "pytest -q tests/known_red.py", "expected_exit": 1, "expected_failures": 2},
                {"command": "pytest -q tests/green.py", "expected_exit": 0, "expected_failures": 0},
            ],
        }
        payload = {
            "status": "completed",
            "decisions": ["Both test suites passed expectations"],
            "changed_files": ["src/test.py"],
            "verification": [
                {"command": "pytest -q tests/known_red.py", "exit_code": 1, "failures": 2},
                {"command": "pytest -q tests/green.py", "exit_code": 0},
            ],
            "blockers": [],
            "next_inputs": [],
        }
        handoff, _ = scheduler.parse_and_validate_handoff(payload, required_verification=req_ctx)
        self.assertEqual(handoff["status"], "completed")


class TestUnresolvableReadyCandidates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("br"):
            raise unittest.SkipTest("br executable not available")

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        subprocess.run(["br", "init", "--prefix", "unres"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_ready_excludes_escaping_cwd_into_unresolvable_errors(self):
        create_res = subprocess.run(
            ["br", "create", "--title", "Escaping CWD Task", "--json"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        issue = json.loads(create_res.stdout)
        issue_id = issue.get("id") or (issue[0].get("id") if isinstance(issue, list) else None)

        subprocess.run(
            [
                "br", "update", issue_id,
                "--agent-context", json.dumps({
                    "herder": {
                        "resources": ["file:src/test.py"],
                        "cwd": "../../outside",
                        "verification": ["pytest -q"],
                    }
                }),
                "--json",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

        candidates, unresolvable_errors, meta = scheduler.list_ready_candidates(project=self.repo, workspace=None)
        self.assertEqual(meta["count"], 0)
        self.assertEqual(meta["unresolvable_count"], 1)
        self.assertEqual(len(candidates), 0)
        self.assertEqual(len(unresolvable_errors), 1)
        self.assertEqual(unresolvable_errors[0]["id"], issue_id)
        self.assertIn("cwd escapes repository root", unresolvable_errors[0]["error"])

    def test_ready_excludes_invalid_resources_into_unresolvable_errors(self):
        create_res = subprocess.run(
            ["br", "create", "--title", "Missing Resources Task", "--json"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        issue = json.loads(create_res.stdout)
        issue_id = issue.get("id") or (issue[0].get("id") if isinstance(issue, list) else None)

        # Mutating task without resources
        subprocess.run(
            [
                "br", "update", issue_id,
                "--agent-context", json.dumps({
                    "herder": {
                        "read_only": False,
                        "resources": [],
                        "verification": ["pytest -q"],
                    }
                }),
                "--json",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

        candidates, unresolvable_errors, meta = scheduler.list_ready_candidates(project=self.repo, workspace=None)
        self.assertEqual(meta["count"], 0)
        self.assertEqual(meta["unresolvable_count"], 1)
        self.assertEqual(len(candidates), 0)
        self.assertEqual(len(unresolvable_errors), 1)
        self.assertEqual(unresolvable_errors[0]["id"], issue_id)
        self.assertIn("mutating task must specify non-empty resources", unresolvable_errors[0]["error"])

    def test_ready_separates_valid_and_invalid_candidates(self):
        # 1. Create valid task
        res_valid = subprocess.run(
            ["br", "create", "--title", "Valid Task", "--json"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        issue_valid = json.loads(res_valid.stdout)
        valid_id = issue_valid.get("id") or (issue_valid[0].get("id") if isinstance(issue_valid, list) else None)
        subprocess.run(
            [
                "br", "update", valid_id,
                "--agent-context", json.dumps({
                    "herder": {
                        "resources": ["file:src/valid.py"],
                        "verification": ["pytest -q"],
                    }
                }),
                "--json",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

        # 2. Create invalid task (invalid JSON)
        res_invalid = subprocess.run(
            ["br", "create", "--title", "Invalid Task", "--json"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        issue_invalid = json.loads(res_invalid.stdout)
        invalid_id = issue_invalid.get("id") or (issue_invalid[0].get("id") if isinstance(issue_invalid, list) else None)
        subprocess.run(
            [
                "br", "update", invalid_id,
                "--agent-context", json.dumps({
                    "herder": {
                        "resources": ["file:src/invalid.py"],
                        "cwd": "../../escaping",
                        "verification": ["pytest -q"],
                    }
                }),
                "--json",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

        candidates, unresolvable_errors, meta = scheduler.list_ready_candidates(project=self.repo, workspace=None)
        self.assertEqual(meta["count"], 1)
        self.assertEqual(meta["unresolvable_count"], 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], valid_id)
        self.assertEqual(len(unresolvable_errors), 1)
        self.assertEqual(unresolvable_errors[0]["id"], invalid_id)


class TestCreateBeadHelper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("br"):
            raise unittest.SkipTest("br executable not available")

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        subprocess.run(["br", "init", "--prefix", "createtest"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_bead_with_agent_context(self):
        ctx = {
            "herder": {
                "resources": ["file:src/lib.py"],
                "verification": ["pytest -q"],
            }
        }
        res = scheduler.create_bead(
            title="Helper Created Bead",
            repo=self.repo,
            description="Created via create_bead helper",
            agent_context=ctx,
            priority=1,
            actor="creator",
        )
        self.assertTrue(res.get("id"))
        self.assertLessEqual(len(res["id"]), 27)
        self.assertEqual(res["title"], "Helper Created Bead")
        self.assertIn("parsed_context", res)
        self.assertEqual(res["parsed_context"]["resources"], ["file:src/lib.py"])

    @patch("scheduler.run_command_json")
    def test_create_bead_allows_long_durable_id_for_later_mapping(self, mock_run):
        mock_run.return_value = [{"id": "createtest-12345678901234567890", "title": "Overlong"}]
        res = scheduler.create_bead("Overlong Task", repo=self.repo)
        self.assertEqual(res["id"], "createtest-12345678901234567890")

    def test_cli_create_subcommand(self):
        ctx_str = json.dumps({
            "herder": {
                "resources": ["file:cli_create.py"],
                "verification": ["echo created"],
            }
        })
        res = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "scheduler.py"),
                "create",
                "--title", "CLI Created Task",
                "--agent-context", ctx_str,
                "--priority", "2",
                "--project", str(self.repo),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertTrue(data.get("id"))
        self.assertEqual(data["title"], "CLI Created Task")
        self.assertIn("parsed_context", data)

    @patch("scheduler.run_command_json")
    def test_create_bead_folding_fallback_when_update_flags_unsupported(self, mock_run):
        # 1. br create succeeds
        # 2. br update with --design/--notes fails (e.g. unsupported flag)
        # 3. br update with --description (folded) succeeds
        mock_run.side_effect = [
            [{"id": "createtest-xyz", "title": "Fallback Task"}],
            RuntimeError("error: unexpected argument '--design' found"),
            [{"id": "createtest-xyz", "title": "Fallback Task"}],
        ]
        res = scheduler.create_bead(
            title="Fallback Task",
            repo=self.repo,
            description="Initial objective",
            design="Architecture diagram",
            acceptance_criteria="Must pass tests",
            notes="Caveats apply",
        )
        self.assertEqual(res["id"], "createtest-xyz")
        self.assertIn("## Acceptance Criteria\nMust pass tests", res["description"])
        self.assertIn("## Design\nArchitecture diagram", res["description"])
        self.assertIn("## Notes\nCaveats apply", res["description"])


class TestCommandNormalizationAndMatching(unittest.TestCase):
    def valid_payload(self, **kwargs):
        base = {
            "status": "completed",
            "decisions": ["Approved"],
            "changed_files": ["src/api.py"],
            "verification": [{"command": "pytest -q", "exit_code": 0}],
            "blockers": [],
            "next_inputs": [],
        }
        base.update(kwargs)
        return base

    def test_tilde_path_normalization(self):
        home = str(Path.home())
        req_cmd = f"python3 {home}/.claude/skills/vfd/bin/planner-state show --feature port-store"
        ev_cmd = "python3 ~/.claude/skills/vfd/bin/planner-state show --feature port-store"
        self.assertTrue(scheduler.is_command_match(ev_cmd, req_cmd))

        payload = self.valid_payload(
            verification=[{"command": ev_cmd, "exit_code": 0}]
        )
        handoff, _ = scheduler.parse_and_validate_handoff(
            payload, required_verification=[req_cmd]
        )
        self.assertEqual(handoff["status"], "completed")

    def test_env_var_and_whitespace_normalization(self):
        os.environ["TEST_TOOL_DIR"] = "/opt/tools"
        try:
            req_cmd = "python3 /opt/tools/test.py  --mode=fast"
            ev_cmd = "python3   $TEST_TOOL_DIR/test.py --mode=fast"
            self.assertTrue(scheduler.is_command_match(ev_cmd, req_cmd))

            payload = self.valid_payload(
                verification=[{"command": ev_cmd, "exit_code": 0}]
            )
            handoff, _ = scheduler.parse_and_validate_handoff(
                payload, required_verification=[req_cmd]
            )
            self.assertEqual(handoff["status"], "completed")
        finally:
            os.environ.pop("TEST_TOOL_DIR", None)

    def test_relative_path_normalization(self):
        req_cmd = "scripts/test.sh --flag"
        ev_cmd = "./scripts/test.sh --flag"
        self.assertTrue(scheduler.is_command_match(ev_cmd, req_cmd))

    def test_flag_with_tilde_value_normalization(self):
        home = str(Path.home())
        req_cmd = f"tool --output={home}/data.txt"
        ev_cmd = "tool --output=~/data.txt"
        self.assertTrue(scheduler.is_command_match(ev_cmd, req_cmd))

    def test_near_match_error_reporting(self):
        payload = self.valid_payload(
            verification=[{"command": "pytest -q tests/actual.py", "exit_code": 0}]
        )
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(
                payload, required_verification=["pytest -q tests/expected.py"]
            )
        err = str(ctx.exception)
        self.assertIn("required verification commands missing from evidence", err)
        self.assertIn("pytest -q tests/expected.py", err)
        self.assertIn("reported commands: ['pytest -q tests/actual.py']", err)


class TestVerificationDictMappingAndBaseline(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/tmp/repo")

    def valid_payload(self, **kwargs):
        base = {
            "status": "completed",
            "decisions": ["Baseline test passed as expected"],
            "changed_files": ["src/api.py"],
            "verification": [{"command": "npm run test:scripts", "exit_code": 1}],
            "blockers": [],
            "next_inputs": [],
        }
        base.update(kwargs)
        return base

    def test_parse_agent_context_dict_mapping_commands_to_exit_codes(self):
        raw = {
            "herder": {
                "resources": ["file:src/api.py"],
                "verification": {
                    "npm run test:scripts": 1,
                    "pytest -q": 0,
                },
            }
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(len(res["verification_specs"]), 2)
        self.assertEqual(res["verification_specs"][0], {"command": "npm run test:scripts", "expected_exit": 1})
        self.assertEqual(res["verification_specs"][1], {"command": "pytest -q", "expected_exit": 0})

    def test_parse_agent_context_dict_mapping_commands_to_spec_objects(self):
        raw = {
            "herder": {
                "resources": ["file:src/api.py"],
                "verification": {
                    "npm run test:scripts": {"expected_exit": 1, "expected_failures": 1},
                    "npm run lint": None,
                },
            }
        }
        res = scheduler.parse_agent_context(raw, self.repo)
        self.assertEqual(len(res["verification_specs"]), 2)
        self.assertEqual(res["verification_specs"][0], {"command": "npm run test:scripts", "expected_exit": 1, "expected_failures": 1})
        self.assertEqual(res["verification_specs"][1], {"command": "npm run lint"})

    def test_handoff_passes_known_red_baseline_from_agent_context_without_worker_expected_exit(self):
        req_ctx = {
            "expected_exit": 1,
            "verification_specs": [
                {"command": "npm run test:scripts", "expected_exit": 1}
            ],
        }
        # Worker honest evidence reports exit_code: 1 without explicit expected_exit
        payload = self.valid_payload(
            verification=[{"command": "npm run test:scripts", "exit_code": 1}]
        )
        handoff, _ = scheduler.parse_and_validate_handoff(payload, required_verification=req_ctx)
        self.assertEqual(handoff["status"], "completed")

    def test_handoff_fails_when_exit_code_does_not_match_agent_context_baseline(self):
        req_ctx = {
            "verification_specs": [
                {"command": "npm run test:scripts", "expected_exit": 1}
            ],
        }
        # Test unexpectedly exited 0 or 2
        payload = self.valid_payload(
            verification=[{"command": "npm run test:scripts", "exit_code": 0}]
        )
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(payload, required_verification=req_ctx)
        self.assertIn("verification exits do not match expectations", str(ctx.exception))


class TestJsonCliErrorOutput(unittest.TestCase):
    def test_cli_error_outputs_json_when_json_flag_passed(self):
        res = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "scheduler.py"),
                "claim",
                "nonexistent-id",
                "--actor", "worker-1",
                "--project", "/nonexistent/repo",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertIn("error", data)


class TestVerificationLoggingAndClamping(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        (self.repo / ".beads").mkdir(parents=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_clamp_traceback_under_15_lines_unchanged(self):
        short_text = "\n".join(f"line {i}" for i in range(12))
        clamped = scheduler.clamp_traceback_output(short_text, issue_id="bead-test")
        self.assertEqual(clamped, short_text)

    def test_clamp_traceback_over_15_lines_clamped_top5_bottom10(self):
        lines = [f"line {i}" for i in range(30)]
        long_text = "\n".join(lines)
        clamped = scheduler.clamp_traceback_output(long_text, issue_id="bead-123")
        clamped_lines = clamped.splitlines()

        # Top 5 lines
        self.assertEqual(clamped_lines[:5], lines[:5])
        # Notice line: 30 - 15 = 15 truncated
        self.assertEqual(
            clamped_lines[5],
            "... [15 lines truncated. Full trace: .beads/logs/bead-123-verification.log] ...",
        )
        # Bottom 10 lines
        self.assertEqual(clamped_lines[6:], lines[-10:])
        self.assertEqual(len(clamped_lines), 16)

    def test_log_verification_output_writes_full_output_to_beads_logs(self):
        entries = [
            {"command": "pytest -q", "exit_code": 0, "output": "test passed\n1 passed in 0.01s"},
            {"command": "mypy .", "exit_code": 0, "output": "Success: no issues found in 1 source file"},
        ]
        log_file = scheduler.log_verification_output(self.repo, "task-xyz", entries)
        self.assertTrue(log_file.is_file())
        self.assertEqual(log_file.resolve(), (self.repo / ".beads" / "logs" / "task-xyz-verification.log").resolve())

        content = log_file.read_text(encoding="utf-8")
        self.assertIn("=== Verification Log for task-xyz at", content)
        self.assertIn("--- [1] Command: pytest -q (exit_code: 0) ---", content)
        self.assertIn("test passed\n1 passed in 0.01s", content)
        self.assertIn("--- [2] Command: mypy . (exit_code: 0) ---", content)
        self.assertIn("Success: no issues found in 1 source file", content)

    @patch("scheduler.run_command_json")
    def test_close_bead_with_evidence_writes_verification_log(self, mock_run):
        mock_run.side_effect = [
            [{"id": "bead-log-test", "title": "Log Test", "agent_context": {}}],
            {"id": "bead-log-test", "comment_id": 1},
            [{"id": "bead-log-test", "status": "closed"}],
        ]
        handoff = {
            "status": "completed",
            "decisions": ["Fixed verification logging"],
            "changed_files": ["skills/beads-task-scheduler/scripts/scheduler.py"],
            "verification": [
                {
                    "command": "python3 -m unittest",
                    "exit_code": 0,
                    "output": "Ran 50 tests\n\nOK",
                }
            ],
            "blockers": [],
            "next_inputs": [],
        }
        res = scheduler.close_bead_with_evidence(
            issue_id="bead-log-test",
            actor="worker-1",
            handoff_raw=handoff,
            repo=self.repo,
        )
        self.assertEqual(res["status"], "closed")

        verif_log = scheduler.verification_log_path(self.repo, "bead-log-test")
        self.assertTrue(verif_log.is_file())
        content = verif_log.read_text(encoding="utf-8")
        self.assertIn("--- [1] Command: python3 -m unittest (exit_code: 0) ---", content)
        self.assertIn("Ran 50 tests\n\nOK", content)

    def test_parse_and_validate_handoff_clamps_failed_output_and_logs(self):
        long_traceback = "\n".join(f"Traceback frame {i}: in test_fn_{i}" for i in range(25))
        payload = {
            "status": "completed",
            "decisions": ["Attempted fix"],
            "changed_files": ["src/main.py"],
            "verification": [
                {
                    "command": "pytest -q",
                    "exit_code": 1,
                    "output": long_traceback,
                }
            ],
            "blockers": [],
            "next_inputs": [],
        }
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.parse_and_validate_handoff(
                payload,
                issue_id="bead-failed-test",
                repo=self.repo,
            )
        err_msg = str(ctx.exception)
        self.assertIn("verification exits do not match expectations", err_msg)
        self.assertIn("... [10 lines truncated. Full trace: .beads/logs/bead-failed-test-verification.log] ...", err_msg)

        verif_log = scheduler.verification_log_path(self.repo, "bead-failed-test")
        self.assertTrue(verif_log.is_file())
        content = verif_log.read_text(encoding="utf-8")
        self.assertIn("Traceback frame 24: in test_fn_24", content)

    def test_execute_verification_success_and_failure(self):
        # Success case
        results = scheduler.execute_verification(
            commands=["echo 'verification pass line 1'", "echo 'verification pass line 2'"],
            repo=self.repo,
            issue_id="bead-exec-ok",
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["exit_code"], 0)
        self.assertEqual(results[1]["exit_code"], 0)

        log_ok = scheduler.verification_log_path(self.repo, "bead-exec-ok")
        self.assertTrue(log_ok.is_file())
        self.assertIn("verification pass line 1", log_ok.read_text(encoding="utf-8"))

        # Failure case with multiline output
        multiline_fail_cmd = "python3 -c \"import sys; [print(f'error line {i}') for i in range(30)]; sys.exit(1)\""
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.execute_verification(
                commands=[multiline_fail_cmd],
                repo=self.repo,
                issue_id="bead-exec-fail",
            )
        err_msg = str(ctx.exception)
        self.assertIn("failed with exit code 1 (expected 0)", err_msg)
        self.assertIn("... [15 lines truncated. Full trace: .beads/logs/bead-exec-fail-verification.log] ...", err_msg)

        log_fail = scheduler.verification_log_path(self.repo, "bead-exec-fail")
        self.assertTrue(log_fail.is_file())
        self.assertIn("error line 29", log_fail.read_text(encoding="utf-8"))


class TestRollbackReasonAndCanonicalResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("br"):
            raise unittest.SkipTest("br executable not available")

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        subprocess.run(["br", "init", "--prefix", "resolvetest"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_resolve_canonical_bead_id_exact_match(self):
        res = subprocess.run(
            ["br", "create", "--title", "Exact Match Bead", "--json"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        issue = json.loads(res.stdout)
        issue_id = issue.get("id") or (issue[0].get("id") if isinstance(issue, list) else None)
        resolved = scheduler.resolve_canonical_bead_id(issue_id, self.repo)
        self.assertEqual(resolved, issue_id)

    @patch("scheduler.run_command_json")
    def test_resolve_canonical_bead_id_dot_hyphen_variant(self, mock_run):
        # When querying 'bead-e16-in2468-parity-n64-8', fallback queries 'e16-in2468-parity-n64.8'
        mock_run.side_effect = [
            RuntimeError("issue not found"),  # direct 'e16-in2468-parity-n64-8'
            [{"id": "e16-in2468-parity-n64.8", "title": "Subtask"}],  # dotted variant check
        ]
        resolved = scheduler.resolve_canonical_bead_id("bead-e16-in2468-parity-n64-8", self.repo)
        self.assertEqual(resolved, "e16-in2468-parity-n64.8")

    @patch("scheduler.run_command_json")
    def test_rollback_claim_records_reason_comment(self, mock_run):
        mock_run.side_effect = [
            {"id": "comment-1", "message": "Rollback (worker-1): Policy refusal on destructive cmd"},  # comment
            {"id": "test-123", "status": "open", "assignee": ""},  # update
        ]
        res = scheduler.rollback_claim("test-123", actor="worker-1", repo=self.repo, reason="Policy refusal on destructive cmd")
        self.assertEqual(res["status"], "open")
        self.assertEqual(res["reason"], "Policy refusal on destructive cmd")

        comment_call = mock_run.call_args_list[0][0][0]
        self.assertEqual(comment_call[0:3], ["br", "comments", "add"])
        self.assertEqual(comment_call[3], "test-123")
        self.assertIn("Policy refusal on destructive cmd", comment_call[5])


if __name__ == "__main__":
    unittest.main()
