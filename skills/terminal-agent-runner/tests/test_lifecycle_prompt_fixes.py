"""Lifecycle and prompt-delivery behaviour of the runner's lead commands.

Each test drives a real fleet command against a temporary state directory. Herdr (the
terminal multiplexer, reached through `fleet.herdr`) and the resource pool subprocess
(`fleet.pool`) are the only things faked: `FakeHerdr` models one agent's pane so the
commands read, prompt and send keys the way they would against a live session.

Covered recorded bugs:
- Esc is only sent to dismiss a dialog; a working pane is never interrupted implicitly
  (cancel/reject refuse, park --cancel skips), and reject reports a failed follow-up.
- `complete` leaves a `completed` assignment with its task and bead_id intact.
- `park` records an honest reason without a dialog and refuses when there is nothing to park.
- `prompt` keeps handoff evidence when submission cannot be verified.
- `prompt` tolerates an assignment whose task is None; spawn keeps an unconfirmed pane.
- `complete --handoff` measures only the override against --max-chars.
- A spawn readiness failure captures the pane before closing it.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_fleet():
    spec = importlib.util.spec_from_file_location("tar_fleet_lifecycle", ROOT / "scripts" / "fleet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fleet = _load_fleet()

AGENT = "worker-1"
SESSION = "lifecycle-test"

DIALOG_PANE = (
    "Bash command\n\n  rm -rf build\n\nDo you want to proceed?\n"
    "❯ 1. Yes\n  2. No\n"
)
WORKING_PANE = "● Bash(pytest -q)\n⣷ Running pytest... (esc to interrupt)\n"
IDLE_PANE = "● Done editing.\n\n❯ \n"


def valid_handoff(**extra) -> dict:
    data = {
        "status": "completed",
        "decisions": ["kept the schema"],
        "changed_files": ["src/a.py"],
        "verification": [{"command": "python3 -m py_compile src/a.py", "exit_code": 0,
                          "output": "", "expected_empty": True}],
        "blockers": [],
        "next_inputs": [],
    }
    data.update(extra)
    return data


class FakeHerdr:
    """One agent's pane. `agent prompt` / `pane send-text` append to the pane unless
    `drop_prompts` is set, which models a CLI that silently discards the text."""

    def __init__(self, pane: str, present: bool = True):
        self.pane = pane
        self.present = present
        self.drop_prompts = False
        self.calls: list[list[str]] = []

    @property
    def esc_sent(self) -> bool:
        return any(c[:2] in (["agent", "send-keys"], ["pane", "send-keys"]) and "esc" in c
                   for c in self.calls)

    def __call__(self, args, session=None, check=False, timeout=None):
        self.calls.append(list(args))
        head = args[:2]
        if head == ["agent", "list"]:
            agents = [{"name": AGENT, "pane_id": "p-1", "agent": "claude",
                       "agent_status": "working", "interactive_ready": True}] if self.present else []
            return {"result": {"agents": agents}}
        if head in (["agent", "read"], ["pane", "read"]):
            if not self.present and head == ["agent", "read"]:
                return {"error": {"code": "agent_not_found"}}
            return {"result": {"read": {"text": self.pane}}}
        if head == ["agent", "send-keys"]:
            if "esc" in args and self.pane is DIALOG_PANE:
                self.pane = IDLE_PANE
            return {"result": "ok"}
        if head == ["agent", "prompt"]:
            if not self.drop_prompts:
                self.pane = self.pane + "\n❯ " + args[3] + "\n"
            return {"result": "ok"}
        if head == ["pane", "send-text"]:
            if not self.drop_prompts:
                self.pane = self.pane + "\n❯ " + args[3] + "\n"
            return {"result": "ok"}
        if head in (["pane", "send-keys"], ["pane", "close"], ["tab", "close"]):
            return {"result": "ok"}
        if head == ["agent", "wait"]:
            return {"result": "ok"}
        raise AssertionError(f"unexpected herdr call: {args}")


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="tar-lifecycle-")
        self.state = Path(self._td.name)
        self.repo = self.state / "repo"
        self.repo.mkdir()
        patches = [
            mock.patch.object(fleet, "STATE_DIR", self.state / "state"),
            mock.patch.object(fleet, "PROFILES_FILE", self.state / "no-profiles.json"),
            mock.patch.object(fleet, "pool", side_effect=self._pool),
            mock.patch.object(fleet.time, "sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    @staticmethod
    def _pool(*args):
        if args and args[0] == "release-agent":
            return {"status": "ok", "freed": ["file:a"]}
        if args and args[0] == "acquire":
            return {"status": "acquired"}
        return {"status": "ok"}

    def assign(self, task="implement feature", **extra):
        fleet.set_task(AGENT, task, SESSION, "repo:test", ["file:a"], False, "auto",
                       cwd=str(self.repo), bead_id="bd-1", **extra)
        return fleet.get_assignment(AGENT, SESSION)

    def run_cmd(self, fn, herdr, **kwargs):
        args = argparse.Namespace(agent=AGENT, session=SESSION, **kwargs)
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(fleet, "herdr", side_effect=herdr), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                fn(args)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
                if isinstance(e.code, str):
                    err.write(e.code)
        return code, out.getvalue(), err.getvalue()

    def write_state_handoff(self, data=None) -> Path:
        hf = fleet.handoff_path(AGENT, SESSION)
        hf.write_text(json.dumps(data or valid_handoff()))
        return hf


# ---------------------------------------------------------------- Esc only for dialogs

class EscOnlyDismissesDialogsTests(StateTestCase):
    def test_cancel_refuses_on_working_pane_without_dialog(self):
        self.assign()
        herdr = FakeHerdr(WORKING_PANE)
        code, out, _ = self.run_cmd(fleet.cmd_cancel, herdr)
        self.assertEqual(code, 2)
        self.assertFalse(herdr.esc_sent)
        self.assertIn("prompt --related", out)

    def test_cancel_dismisses_a_pending_dialog(self):
        self.assign()
        herdr = FakeHerdr(DIALOG_PANE)
        code, out, _ = self.run_cmd(fleet.cmd_cancel, herdr)
        self.assertEqual(code, 0)
        self.assertTrue(herdr.esc_sent)
        self.assertTrue(json.loads(out)["cancelled"])

    def test_cancel_interrupt_flag_aborts_a_working_turn_deliberately(self):
        self.assign()
        herdr = FakeHerdr(WORKING_PANE)
        code, out, _ = self.run_cmd(fleet.cmd_cancel, herdr, interrupt=True)
        self.assertEqual(code, 0)
        self.assertTrue(herdr.esc_sent)
        self.assertTrue(json.loads(out)["interrupted"])

    def test_reject_refuses_on_working_pane_and_leaves_task_alone(self):
        before = self.assign()
        herdr = FakeHerdr(WORKING_PANE)
        code, out, _ = self.run_cmd(fleet.cmd_reject, herdr, reason=None,
                                    prompt="use git rm instead", halt=False, group=None)
        self.assertEqual(code, 2)
        self.assertFalse(herdr.esc_sent)
        self.assertFalse(any(c[:2] == ["agent", "prompt"] for c in herdr.calls))
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["task"], before["task"])

    def test_reject_with_prompt_records_follow_up_and_clears_handoff(self):
        before = self.assign()
        hf = self.write_state_handoff()
        herdr = FakeHerdr(DIALOG_PANE)
        code, out, _ = self.run_cmd(fleet.cmd_reject, herdr, reason="unsafe",
                                    prompt="use git rm instead", halt=False, group=None)
        self.assertEqual(code, 0)
        self.assertTrue(herdr.esc_sent)
        result = json.loads(out)
        self.assertTrue(result["prompt_submitted"])
        self.assertTrue(result["task_recorded"])
        after = fleet.get_assignment(AGENT, SESSION)
        self.assertIn("Related follow-up:\nuse git rm instead", after["task"])
        self.assertTrue(after["task"].startswith(before["task"]))
        self.assertGreaterEqual(after["task_time"], before["task_time"])
        self.assertEqual(after["bead_id"], "bd-1")
        self.assertFalse(hf.exists())

    def test_reject_exits_nonzero_when_follow_up_does_not_land(self):
        before = self.assign()
        hf = self.write_state_handoff()
        herdr = FakeHerdr(DIALOG_PANE)
        herdr.drop_prompts = True
        code, out, err = self.run_cmd(fleet.cmd_reject, herdr, reason="unsafe",
                                      prompt="use git rm instead", halt=False, group=None)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["prompt_submitted"])
        self.assertIn("could not be verified", err)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["task"], before["task"])
        self.assertTrue(hf.exists(), "handoff evidence must survive a failed follow-up")

    def test_park_cancel_never_escapes_when_handoff_file_exists(self):
        self.assign()
        self.write_state_handoff()
        herdr = FakeHerdr(WORKING_PANE)
        code, out, _ = self.run_cmd(fleet.cmd_park, herdr, reason=None, cancel=True,
                                    group=None, interrupt=True)
        self.assertEqual(code, 0)
        self.assertFalse(herdr.esc_sent)
        result = json.loads(out)
        self.assertFalse(result["esc_sent"])
        self.assertIn("handoff evidence present", result["esc_skipped"])
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["lifecycle"], "parked")

    def test_park_cancel_never_escapes_when_pane_shows_fleet_handoff(self):
        self.assign()
        herdr = FakeHerdr(WORKING_PANE + "FLEET_HANDOFF: {\"status\": \"completed\"}\n")
        code, out, _ = self.run_cmd(fleet.cmd_park, herdr, reason="lead decision",
                                    cancel=True, group=None)
        self.assertEqual(code, 0)
        self.assertFalse(herdr.esc_sent)

    def test_park_cancel_skips_esc_on_working_pane_without_dialog(self):
        self.assign()
        herdr = FakeHerdr(WORKING_PANE)
        code, out, _ = self.run_cmd(fleet.cmd_park, herdr, reason="lead decision",
                                    cancel=True, group=None)
        self.assertEqual(code, 0)
        self.assertFalse(herdr.esc_sent)
        self.assertIn("no pending dialog", json.loads(out)["esc_skipped"])

    def test_park_cancel_dismisses_a_pending_dialog(self):
        self.assign()
        herdr = FakeHerdr(DIALOG_PANE)
        code, out, _ = self.run_cmd(fleet.cmd_park, herdr, reason=None, cancel=True, group=None)
        self.assertEqual(code, 0)
        self.assertTrue(herdr.esc_sent)
        self.assertTrue(json.loads(out)["esc_sent"])


# ---------------------------------------------------------------- completed lifecycle

class CompletedLifecycleTests(StateTestCase):
    def complete(self, herdr, **kw):
        kw.setdefault("group", None)
        kw.setdefault("lines", 100)
        kw.setdefault("max_chars", fleet.DEFAULT_MAX_HANDOFF_CHARS)
        kw.setdefault("handoff", None)
        return self.run_cmd(fleet.cmd_complete, herdr, **kw)

    def test_complete_keeps_assignment_as_completed_with_task_and_bead(self):
        self.assign(task="ship it")
        self.write_state_handoff()
        code, out, err = self.complete(FakeHerdr(IDLE_PANE))
        self.assertEqual(code, 0, err)
        record = fleet.get_assignment(AGENT, SESSION)
        self.assertEqual(record["lifecycle"], "completed")
        self.assertEqual(record["task"], "ship it")
        self.assertEqual(record["bead_id"], "bd-1")
        self.assertEqual(record["cwd"], str(self.repo))
        self.assertIn("completed_at", record)

    def test_tasks_json_without_completed_lifecycle_still_loads(self):
        p = fleet.state_path("tasks.json", SESSION)
        p.write_text(json.dumps({
            "old-a": {"task": "x", "group": None, "resources": [], "lifecycle": "parked"},
            "old-b": {"task": "y", "group": None, "resources": []},
        }))
        self.assertEqual(fleet.get_assignment("old-b", SESSION)["task"], "y")

    def test_terminal_assignment_refuses_prompt_and_park(self):
        self.assign()
        fleet.annotate_assignment(AGENT, SESSION, lifecycle="completed", completed_at=time.time())
        herdr = FakeHerdr(IDLE_PANE)
        code, _, err = self.run_cmd(fleet.cmd_prompt, herdr, text="more", related=True,
                                    group=None, resource=[], read_only=False, lock_timeout=0)
        self.assertNotEqual(code, 0)
        self.assertIn("completed (terminal)", err)
        code, out, _ = self.run_cmd(fleet.cmd_park, herdr, reason=None, cancel=True, group=None)
        self.assertEqual(code, 2)
        self.assertIn("nothing to park", out)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["lifecycle"], "completed")
        self.assertEqual(fleet.load_json(fleet.state_path("escalations.json", SESSION), []), [])

    def test_status_reports_lingering_completed_pane_as_completed(self):
        self.assign()
        fleet.annotate_assignment(AGENT, SESSION, lifecycle="completed", completed_at=time.time())
        with mock.patch.object(fleet, "herdr", side_effect=FakeHerdr(IDLE_PANE)):
            rows = fleet.fleet_rows(SESSION, settle_samples=1)
        self.assertEqual(rows[0]["state"], "completed")
        self.assertFalse(fleet.fleet_actionable(rows))
        self.assertTrue(fleet.fleet_terminal(rows))


# ---------------------------------------------------------------- park reasons

class ParkReasonTests(StateTestCase):
    def test_park_without_dialog_or_reason_records_lead_park_not_policy_denial(self):
        self.assign()
        code, out, _ = self.run_cmd(fleet.cmd_park, FakeHerdr(IDLE_PANE), reason=None,
                                    cancel=False, group=None)
        self.assertEqual(code, 0)
        esc = fleet.load_json(fleet.state_path("escalations.json", SESSION), [])
        self.assertEqual(len(esc), 1)
        self.assertEqual(esc[0]["reason"], "parked by lead (no pending dialog)")
        self.assertNotIn("default-deny", esc[0]["reason"])
        self.assertNotEqual(esc[0]["subject"], "(no prompt captured)")

    def test_park_with_dialog_still_classifies_it(self):
        self.assign()
        code, _, _ = self.run_cmd(fleet.cmd_park, FakeHerdr(DIALOG_PANE), reason=None,
                                  cancel=False, group=None)
        self.assertEqual(code, 0)
        esc = fleet.load_json(fleet.state_path("escalations.json", SESSION), [])
        self.assertIn("rm -rf build", esc[0]["subject"])
        self.assertNotEqual(esc[0]["reason"], "parked by lead (no pending dialog)")

    def test_park_refuses_agent_without_assignment(self):
        code, out, _ = self.run_cmd(fleet.cmd_park, FakeHerdr(IDLE_PANE, present=False),
                                    reason=None, cancel=True, group="repo:test")
        self.assertEqual(code, 2)
        self.assertIn("nothing to park", out)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION), {})

    def test_park_refuses_dead_assignment(self):
        self.assign()
        fleet.annotate_assignment(AGENT, SESSION, lifecycle="dead", lost_at=time.time())
        code, out, _ = self.run_cmd(fleet.cmd_park, FakeHerdr(IDLE_PANE), reason="x",
                                    cancel=False, group=None)
        self.assertEqual(code, 2)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["lifecycle"], "dead")


# ---------------------------------------------------------------- prompt ordering & None task

class PromptDeliveryTests(StateTestCase):
    def prompt(self, herdr, text="also add tests"):
        return self.run_cmd(fleet.cmd_prompt, herdr, text=text, related=True, group=None,
                            resource=[], read_only=False, lock_timeout=0)

    def test_failed_submission_keeps_handoff_files_and_task(self):
        before = self.assign()
        hf = self.write_state_handoff()
        repo_hf = fleet.repo_handoff_path(AGENT, self.repo)
        repo_hf.parent.mkdir(parents=True, exist_ok=True)
        repo_hf.write_text(json.dumps(valid_handoff()))
        herdr = FakeHerdr(IDLE_PANE)
        herdr.drop_prompts = True
        code, _, err = self.prompt(herdr)
        self.assertNotEqual(code, 0)
        self.assertIn("could not be verified", err)
        self.assertIn("handoff files were restored", err)
        self.assertTrue(hf.exists())
        self.assertTrue(repo_hf.exists())
        self.assertFalse(hf.with_name(hf.name + ".prev").exists())
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["task"], before["task"])

    def test_successful_submission_clears_handoff_and_records_follow_up(self):
        self.assign(task="original")
        hf = self.write_state_handoff()
        code, out, err = self.prompt(FakeHerdr(IDLE_PANE))
        self.assertEqual(code, 0, err)
        self.assertFalse(hf.exists())
        self.assertFalse(hf.with_name(hf.name + ".prev").exists())
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["task"],
                         "original\n\nRelated follow-up:\nalso add tests")

    def test_prompt_after_spawn_without_task_does_not_crash(self):
        fleet.set_task(AGENT, None, SESSION, None, [], True, "plan", cwd=str(self.repo))
        code, out, err = self.prompt(FakeHerdr(IDLE_PANE), text="inspect the logs")
        self.assertEqual(code, 0, err)
        self.assertNotIn("TypeError", err)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["task"], "inspect the logs")


# ---------------------------------------------------------------- complete --handoff cap

class HandoffOverrideCapTests(StateTestCase):
    def test_override_is_measured_alone_not_the_stale_pane_or_appended_records(self):
        self.assign()
        approvals = [{"who": "lead", "subject": "s" * 2000, "rule": "default-deny",
                      "at": "2026-11-09", "choice": "1"} for _ in range(10)]
        fleet.annotate_assignment(AGENT, SESSION, reviewed_approvals=approvals)
        stale = "FLEET_HANDOFF: " + json.dumps(valid_handoff(decisions=["x" * 30000]))
        override = json.dumps(valid_handoff())
        code, out, err = self.run_cmd(fleet.cmd_complete, FakeHerdr(stale), group=None,
                                      lines=100, max_chars=fleet.DEFAULT_MAX_HANDOFF_CHARS,
                                      handoff=override)
        self.assertEqual(code, 0, err)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["lifecycle"], "completed")

    def test_oversized_override_refusal_names_the_override(self):
        self.assign()
        override = json.dumps(valid_handoff(decisions=["y" * 500]))
        code, _, err = self.run_cmd(fleet.cmd_complete, FakeHerdr(IDLE_PANE), group=None,
                                    lines=100, max_chars=200, handoff=override)
        self.assertNotEqual(code, 0)
        self.assertIn("exceeds 200 characters", err)
        self.assertIn("--handoff override", err)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["lifecycle"], "active")


# ---------------------------------------------------------------- spawn evidence

class SpawnEvidenceTests(StateTestCase):
    def spawn_args(self, **kw):
        base = dict(name=AGENT, cli="codex", cwd=str(self.repo), task="inspect the repo",
                    group=None, resource=[], read_only=True, lock_timeout=0,
                    approval_mode="plan", session=SESSION, quiet=True,
                    workspace_id="ws-1", settle_samples=1, settle_delay=0)
        base.update(kw)
        return argparse.Namespace(**base)

    def fake_spawn_herdr(self, pane: str, registered=True):
        herdr = FakeHerdr(pane, present=False)
        inner = herdr.__call__

        def call(args, session=None, check=False, timeout=None):
            if args[:2] == ["tab", "create"]:
                herdr.calls.append(list(args))
                return {"result": {"tab": {"tab_id": "t-1"}, "root_pane": {"pane_id": "p-1"}}}
            if args[:2] == ["agent", "start"]:
                herdr.calls.append(list(args))
                herdr.present = registered
                return {"result": "ok"}
            return inner(args, session, check, timeout)
        return herdr, call

    def test_readiness_timeout_captures_pane_and_writes_event_before_close(self):
        herdr, call = self.fake_spawn_herdr("⣷ Thinking... (esc to interrupt)\nTRUST-STATE-XYZ\n")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fleet, "herdr", side_effect=call), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(RuntimeError) as ctx:
                fleet.cmd_spawn(self.spawn_args())
        self.assertIn("never became ready", str(ctx.exception))
        self.assertIn("failed predicate", str(ctx.exception))
        self.assertIn("foreground execution", str(ctx.exception))
        self.assertIn("TRUST-STATE-XYZ", err.getvalue())
        events = fleet.drain_events(SESSION)
        failed = [e for e in events if e["kind"] == "spawn_failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("TRUST-STATE-XYZ", failed[0]["payload"]["pane_text"])
        self.assertIn(["pane", "close", "p-1"], herdr.calls)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION), {})

    def test_unconfirmed_initial_task_keeps_pane_alive(self):
        herdr, call = self.fake_spawn_herdr(IDLE_PANE)
        herdr.drop_prompts = True
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fleet, "herdr", side_effect=call), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = fleet.cmd_spawn(self.spawn_args())
        self.assertFalse(result["task_confirmed"])
        self.assertIn("could not be confirmed", err.getvalue())
        self.assertNotIn(["pane", "close", "p-1"], herdr.calls)
        self.assertNotIn(["tab", "close", "t-1"], herdr.calls)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["task"], "inspect the repo")


if __name__ == "__main__":
    unittest.main()
