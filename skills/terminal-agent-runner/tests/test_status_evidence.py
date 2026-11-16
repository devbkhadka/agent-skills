"""Status classification, turn-bound completion evidence, and pane-close safety.

Each test drives real fleet functions against a temporary state directory. Herdr (reached
through `fleet.herdr`) and the resource pool subprocess (`fleet.pool`) are the only things
faked; process discovery for the pane-close guard runs against real child processes.

Covered recorded bugs:
- sug_20261109_132501_403c26: AGY `○ Running (cmd)` / `● [hh:mm:ss] ... running` /
  `N task(s)` and Claude `✶ Verb… (Ns` frames read as done/idle; an earlier FLEET_HANDOFF
  on screen marked a re-prompted, working pane done (and completable).
- sug_20261109_132501_316d56: a worker whose FLEET_HANDOFF says blocked showed as idle.
- sug_20261110_045021_fa68e7: complete/reset closed a pane and killed the background
  processes the worker had started from it.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
_real_sleep = time.sleep


def _load_fleet():
    spec = importlib.util.spec_from_file_location("tar_fleet_status_evidence", ROOT / "scripts" / "fleet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fleet = _load_fleet()

AGENT = "worker-1"
SESSION = "status-evidence-test"


def handoff(**extra) -> dict:
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


def handoff_line(**extra) -> str:
    return "FLEET_HANDOFF: " + json.dumps(handoff(**extra))


class FakeHerdr:
    """One agent's pane with a settable Herdr status and optional process info."""

    def __init__(self, pane: str, status: str = "idle", process_info: dict | None = None):
        self.pane = pane
        self.status = status
        self.process_info = process_info
        self.calls: list[list[str]] = []

    def closed(self) -> bool:
        return any(c[:2] == ["pane", "close"] for c in self.calls)

    def __call__(self, args, session=None, check=False, timeout=None):
        self.calls.append(list(args))
        head = args[:2]
        if head == ["agent", "list"]:
            return {"result": {"agents": [{"name": AGENT, "pane_id": "p-1", "agent": "agy",
                                           "agent_status": self.status}]}}
        if head in (["agent", "read"], ["pane", "read"]):
            return {"result": {"read": {"text": self.pane}}}
        if head == ["pane", "process-info"]:
            if self.process_info is None:
                return {"error": {"code": "unsupported"}}
            return {"result": {"process_info": self.process_info}}
        if head == ["agent", "prompt"]:
            self.pane = self.pane + "\n❯ " + args[3] + "\n"
            return {"result": "ok"}
        if head in (["pane", "send-keys"], ["agent", "send-keys"], ["pane", "close"],
                    ["tab", "close"], ["agent", "wait"]):
            return {"result": "ok"}
        raise AssertionError(f"unexpected herdr call: {args}")


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="tar-status-")
        self.state = Path(self._td.name)
        self.repo = self.state / "repo"
        self.repo.mkdir()
        for p in (
            mock.patch.object(fleet, "STATE_DIR", self.state / "state"),
            mock.patch.object(fleet, "PROFILES_FILE", self.state / "no-profiles.json"),
            mock.patch.object(fleet, "pool", side_effect=self._pool),
            mock.patch.object(fleet.time, "sleep"),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    @staticmethod
    def _pool(*args):
        if args and args[0] == "release-agent":
            return {"status": "ok", "freed": ["file:a"]}
        return {"status": "ok"}

    def assign(self, task="implement feature"):
        fleet.set_task(AGENT, task, SESSION, "repo:test", ["file:a"], False, "auto",
                       cwd=str(self.repo), bead_id="bd-1")
        return fleet.get_assignment(AGENT, SESSION)

    def rows(self, herdr):
        with mock.patch.object(fleet, "herdr", side_effect=herdr), \
             contextlib.redirect_stderr(io.StringIO()):
            return fleet.fleet_rows(SESSION, settle_samples=1)

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


# ---------------------------------------------------------------- foreground frames

AGY_RUNNING_FRAME = (
    "● Read(src/observation.ts)\n"
    "○ Running (agent-desk wait ask_5 --session ses_6)\n"
    "\n"
    "● [19:05:15] agent-desk wait ask_5 --session ses_6 running\n"
    " gemini-3-pro · 1 task(s) · /tasks\n"
)
CLAUDE_RUNNING_FRAME = (
    "⏺ Bash(agent-desk wait ask_4 --session ses_6)\n"
    "  ⎿  Running…\n"
    "\n"
    "✶ Gallivanting… (57s · ↓ 114 tokens)\n"
    "  ⎿  Press up to edit queued messages\n"
    "\n"
    "❯ \n"
)


class ForegroundFrameTests(unittest.TestCase):
    def test_each_agy_and_claude_working_line_alone_is_foreground_execution(self):
        lines = [
            "○ Running (agent-desk wait ask_5 --session ses_6)",
            "● [19:05:15] agent-desk wait ask_5 --session ses_6 running",
            " gemini-3-pro · 1 task(s) · /tasks",
            "✶ Gallivanting… (57s · ↓ 114 tokens)",
            "✻ Pondering… (3s · esc)",
            "✽ Brewing… (120s)",
            "✢ Noodling… (1s)",
            "  ⎿  Press up to edit queued messages",
            "Thinking…",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertTrue(fleet.has_foreground_execution(line + "\n"))
                self.assertFalse(fleet.is_settled_frame(line + "\n"))

    def test_settled_claude_glyph_lines_are_not_foreground_execution(self):
        settled = [
            "✻ Welcome to Claude Code!\n",
            "✻ Worked for 1m 3s\n\n❯ \n",
            "✻ Cooked for 57s\n",
            " gemini-3-pro · 0 task(s) · /tasks\n",
            "● [19:05:15] agent-desk wait ask_5 finished\n",
            "Ran the suite; 3 tasks remain in the backlog.\n",
        ]
        for text in settled:
            with self.subTest(text=text):
                self.assertFalse(fleet.has_foreground_execution(text))

    def test_full_frames_are_foreground_execution(self):
        self.assertTrue(fleet.has_foreground_execution(AGY_RUNNING_FRAME))
        self.assertTrue(fleet.has_foreground_execution(CLAUDE_RUNNING_FRAME))


class StatusClassificationTests(StateTestCase):
    def test_herdr_done_agy_pane_inside_long_tool_call_is_working(self):
        self.assign()
        rows = self.rows(FakeHerdr(AGY_RUNNING_FRAME, status="done"))
        self.assertEqual(rows[0]["state"], "working")
        self.assertTrue(rows[0]["foreground"])

    def test_herdr_idle_claude_pane_with_spinner_is_working(self):
        self.assign()
        rows = self.rows(FakeHerdr(CLAUDE_RUNNING_FRAME, status="idle"))
        self.assertEqual(rows[0]["state"], "working")

    def test_foreground_wins_over_an_earlier_handoff_still_on_screen(self):
        self.assign()
        pane = handoff_line() + "\n\n❯ follow-up\n" + AGY_RUNNING_FRAME
        rows = self.rows(FakeHerdr(pane, status="done"))
        self.assertEqual(rows[0]["state"], "working")

    def test_words_inside_the_handoff_json_do_not_read_as_running(self):
        self.assign()
        pane = ("● All done.\n" + handoff_line(decisions=["Running the suite (running 3 files)"])
                + "\n\n❯ \n")
        rows = self.rows(FakeHerdr(pane, status="idle"))
        self.assertEqual(rows[0]["state"], "done")
        self.assertFalse(rows[0]["foreground"])


# ---------------------------------------------------------------- turn-bound evidence

class StaleHandoffTests(StateTestCase):
    def prompt(self, herdr, text="also update the docs"):
        return self.run_cmd(fleet.cmd_prompt, herdr, text=text, related=True, group=None,
                            resource=[], read_only=False, lock_timeout=0)

    def test_reprompted_pane_is_not_done_on_the_previous_turns_handoff(self):
        self.assign()
        herdr = FakeHerdr("● Finished.\n" + handoff_line() + "\n\n❯ \n")
        with mock.patch.object(fleet, "herdr", side_effect=herdr):
            self.assertTrue(fleet.has_completion_evidence(AGENT, SESSION))
        code, _, err = self.prompt(herdr)
        self.assertEqual(code, 0, err)
        with mock.patch.object(fleet, "herdr", side_effect=herdr), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(fleet.has_completion_evidence(AGENT, SESSION))
        self.assertNotEqual(self.rows(herdr)[0]["state"], "done")

    def test_complete_refuses_the_previous_turns_pane_handoff(self):
        self.assign()
        herdr = FakeHerdr("● Finished.\n" + handoff_line() + "\n\n❯ \n")
        self.assertEqual(self.prompt(herdr)[0], 0)
        code, _, err = self.run_cmd(fleet.cmd_complete, herdr, group=None, lines=100,
                                    max_chars=fleet.DEFAULT_MAX_HANDOFF_CHARS, handoff=None)
        self.assertNotEqual(code, 0)
        self.assertIn("predates the current task", err)
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["lifecycle"], "active")
        self.assertFalse(herdr.closed())

    def test_a_new_handoff_printed_after_the_prompt_is_accepted(self):
        self.assign()
        herdr = FakeHerdr("● Finished.\n" + handoff_line() + "\n\n❯ \n")
        self.assertEqual(self.prompt(herdr)[0], 0)
        herdr.pane += "● Docs updated.\n" + handoff_line(changed_files=["README.md"]) + "\n\n❯ \n"
        with mock.patch.object(fleet, "herdr", side_effect=herdr), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(fleet.has_completion_evidence(AGENT, SESSION))
        self.assertEqual(self.rows(herdr)[0]["state"], "done")

    def test_tasks_json_without_stale_digest_still_accepts_pane_handoff(self):
        self.assign()
        self.assertNotIn("stale_handoff_digest", fleet.get_assignment(AGENT, SESSION))
        herdr = FakeHerdr("● Finished.\n" + handoff_line() + "\n\n❯ \n")
        with mock.patch.object(fleet, "herdr", side_effect=herdr):
            self.assertTrue(fleet.has_completion_evidence(AGENT, SESSION))


# ---------------------------------------------------------------- blocked handoff

class BlockedHandoffTests(StateTestCase):
    def test_pane_blocked_handoff_reports_handoff_blocked_with_first_blocker(self):
        self.assign()
        pane = ("● Stopping.\n" + handoff_line(status="blocked", verification=[],
                                                blockers=["No Reply for ask_4 after five waits",
                                                          "second"]) + "\n\n❯ \n")
        rows = self.rows(FakeHerdr(pane, status="idle"))
        self.assertEqual(rows[0]["state"], "handoff-blocked")
        self.assertEqual(rows[0]["pending"], "No Reply for ask_4 after five waits")
        self.assertFalse(rows[0]["has_handoff"])
        self.assertTrue(fleet.fleet_actionable(rows))
        self.assertFalse(fleet.fleet_terminal(rows))

    def test_file_blocked_handoff_reports_handoff_blocked(self):
        self.assign()
        fleet.handoff_path(AGENT, SESSION).write_text(json.dumps(
            handoff(status="blocked", blockers=["lease too narrow for spec files"])))
        rows = self.rows(FakeHerdr("● Stopping.\n\n❯ \n", status="idle"))
        self.assertEqual(rows[0]["state"], "handoff-blocked")
        self.assertEqual(rows[0]["pending"], "lease too narrow for spec files")

    def test_status_without_blockers_names_the_status(self):
        self.assign()
        pane = "● Stopping.\n" + handoff_line(status="partial") + "\n\n❯ \n"
        rows = self.rows(FakeHerdr(pane, status="idle"))
        self.assertEqual(rows[0]["state"], "handoff-blocked")
        self.assertIn("partial", rows[0]["pending"])


# ---------------------------------------------------------------- pane-close guard

class PaneChildProcessTests(StateTestCase):
    def setUp(self):
        super().setUp()
        # A stand-in for the pane's shell that has started a background job.
        self.shell = subprocess.Popen(["bash", "-c", "sleep 300 & wait"],
                                      start_new_session=True)
        self.addCleanup(self._reap)
        self.info = {"shell_pid": self.shell.pid,
                     "foreground_process_group_id": 2 ** 30,
                     "foreground_processes": []}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with mock.patch.object(fleet, "herdr", side_effect=FakeHerdr("", process_info=self.info)):
                live = fleet.pane_child_processes("p-1")
            if live:
                break
            _real_sleep(0.05)
        self.assertTrue(live, "background child never appeared")

    def _reap(self):
        try:
            os.killpg(self.shell.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.shell.wait(timeout=5)

    def complete(self, herdr, **kw):
        kw.setdefault("group", None)
        kw.setdefault("lines", 100)
        kw.setdefault("max_chars", fleet.DEFAULT_MAX_HANDOFF_CHARS)
        kw.setdefault("handoff", None)
        return self.run_cmd(fleet.cmd_complete, herdr, **kw)

    def test_lists_the_background_job_but_not_the_shell(self):
        with mock.patch.object(fleet, "herdr", side_effect=FakeHerdr("", process_info=self.info)):
            live = fleet.pane_child_processes("p-1")
        pids = [p["pid"] for p in live]
        self.assertNotIn(self.shell.pid, pids)
        self.assertTrue(any("sleep 300" in p["command"] for p in live))

    def test_foreground_group_processes_belong_to_the_agent_and_are_ignored(self):
        info = dict(self.info, foreground_process_group_id=self.shell.pid)
        with mock.patch.object(fleet, "herdr", side_effect=FakeHerdr("", process_info=info)):
            self.assertEqual(fleet.pane_child_processes("p-1"), [])

    def test_complete_refuses_and_keeps_state_when_children_are_live(self):
        self.assign()
        fleet.handoff_path(AGENT, SESSION).write_text(json.dumps(handoff()))
        herdr = FakeHerdr("● Done.\n\n❯ \n", process_info=self.info)
        code, out, err = self.complete(herdr)
        self.assertEqual(code, 2, err)
        self.assertIn("sleep 300", out)
        self.assertIn("--allow-kill-children", err)
        self.assertFalse(herdr.closed())
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["lifecycle"], "active")
        self.assertTrue(fleet.handoff_path(AGENT, SESSION).exists())
        self.assertEqual(fleet.load_json(fleet.state_path("handoffs.json", SESSION), {}), {})

    def test_complete_with_allow_kill_children_closes_the_pane(self):
        self.assign()
        fleet.handoff_path(AGENT, SESSION).write_text(json.dumps(handoff()))
        herdr = FakeHerdr("● Done.\n\n❯ \n", process_info=self.info)
        code, _, err = self.complete(herdr, allow_kill_children=True)
        self.assertEqual(code, 0, err)
        self.assertTrue(herdr.closed())
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["lifecycle"], "completed")

    def test_reset_refuses_before_closing_the_old_pane(self):
        before = self.assign()
        herdr = FakeHerdr("● Done.\n\n❯ \n", process_info=self.info)
        code, out, _ = self.run_cmd(
            fleet.cmd_reset, herdr, cli="agy", task="next task", handoff="facts",
            old_group=None, group=None, resource=[], read_only=False, lock_timeout=0,
            approval_mode="auto", cwd=str(self.repo), model=None, profile=None,
            workspace_id=None, allow_kill_children=False)
        self.assertEqual(code, 2)
        self.assertIn("sleep 300", out)
        self.assertFalse(herdr.closed())
        self.assertEqual(fleet.get_assignment(AGENT, SESSION)["task"], before["task"])


class PaneChildProcessUnknownTests(StateTestCase):
    def test_complete_proceeds_with_a_note_when_herdr_gives_no_shell_pid(self):
        self.assign()
        fleet.handoff_path(AGENT, SESSION).write_text(json.dumps(handoff()))
        herdr = FakeHerdr("● Done.\n\n❯ \n", process_info=None)
        code, _, err = self.run_cmd(fleet.cmd_complete, herdr, group=None, lines=100,
                                    max_chars=fleet.DEFAULT_MAX_HANDOFF_CHARS, handoff=None)
        self.assertEqual(code, 0, err)
        self.assertIn("could not list processes", err)
        self.assertTrue(herdr.closed())


if __name__ == "__main__":
    unittest.main()
