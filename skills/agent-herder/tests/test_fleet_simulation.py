import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUN_SIMULATION = ROOT / "resources" / "fleet-simulation" / "run_simulation.py"
LAUNCH_SIMULATION = ROOT / "resources" / "fleet-simulation" / "launch_simulation.py"


def load_simulation():
    spec = importlib.util.spec_from_file_location("agent_herder_simulation", RUN_SIMULATION)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_launcher():
    spec = importlib.util.spec_from_file_location("agent_herder_simulation_launcher", LAUNCH_SIMULATION)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SessionDaemonTests(unittest.TestCase):
    def setUp(self):
        self.sim = load_simulation()

    def test_liveness_failure_reports_bounded_daemon_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "server.log"
            log_path.write_text("server crashed after lead startup\n", encoding="utf-8")
            daemon = self.sim.SessionDaemon("sim-test", 23, log_path, "w1")
            stopped = subprocess.CompletedProcess([], 1, stdout=json.dumps({"status": "stopped"}))
            with mock.patch.object(self.sim, "run", return_value=stopped):
                with self.assertRaisesRegex(AssertionError, "lead startup.*daemon_pid=23.*server crashed"):
                    self.sim.assert_session_live({}, daemon, "lead startup")

    def test_open_session_uses_wrapper_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "server.log"
            log_path.write_text("daemon ready\n", encoding="utf-8")
            init = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                "session": "agent-herder-sim-99", "status": "ready",
                "workspace_id": "w1", "socket_path": "/tmp/herdr.sock",
                "daemon_pid": 99, "daemon_log_path": str(log_path),
            }))
            running = subprocess.CompletedProcess([], 0, stdout=json.dumps({"status": "running"}))
            with mock.patch.object(self.sim.os, "getpid", return_value=99), \
                 mock.patch.object(self.sim, "run", side_effect=[init, running]) as run:
                env = {"HERDR_PANE_ID": "stale-pane"}
                daemon = self.sim.open_session(env, root)

            self.assertEqual(daemon.session, "agent-herder-sim-99")
            self.assertEqual(daemon.pid, 99)
            self.assertEqual(daemon.workspace_id, "w1")
            self.assertNotIn("HERDR_PANE_ID", env)
            self.assertEqual(env["HERDR_ACTIVE_WORKSPACE_ID"], "w1")
            self.assertEqual(
                run.call_args_list[0].args[0],
                ["bash", str(self.sim.HERD), "--session", "agent-herder-sim-99",
                 "--emit-daemon-evidence", "init", "--cwd", str(root)],
            )

    def test_failure_evidence_preserves_bounded_daemon_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "server.log"
            log_path.write_text("daemon stopped while the lead was waiting\n", encoding="utf-8")
            daemon = self.sim.SessionDaemon("sim-test", 23, log_path, "w1")
            evidence_dir = root / "evidence"
            with mock.patch.object(
                self.sim, "run",
                side_effect=[
                    subprocess.CompletedProcess([], 1, stdout="", stderr="stopped"),
                    subprocess.CompletedProcess([], 1, stdout="", stderr="stopped"),
                    subprocess.CompletedProcess([], 0, stdout="{}", stderr=""),
                ],
            ), \
                 mock.patch.object(self.sim, "get_fleet_assignments", return_value={}), \
                 mock.patch.object(self.sim, "herdr_json", return_value={"result": {"agents": []}}):
                captured = self.sim.capture_simulation_evidence(
                    {}, "sim-test", dest_dir=evidence_dir, daemon=daemon)
            saved = captured["session-daemon"].read_text(encoding="utf-8")
            self.assertIn("Daemon PID: 23", saved)
            self.assertIn("daemon stopped while the lead was waiting", saved)

    def test_preflight_targets_the_disposable_session(self):
        result = subprocess.CompletedProcess([], 0, stdout="")
        with mock.patch.object(self.sim, "run", return_value=result) as run:
            self.sim.preflight({}, "agy", "sim-test")

        self.assertEqual(
            run.call_args.args[0],
            ["bash", str(self.sim.PREFLIGHT), "--session", "sim-test",
             "--allow-cross-session", "--cli", "agy"],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 600.0)

    def test_failure_evidence_saves_session_agent_and_daemon_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "server.log"
            log_path.write_text("daemon failure\n", encoding="utf-8")
            daemon = self.sim.SessionDaemon("sim-test", 23, log_path, "w1")
            status = subprocess.CompletedProcess(
                [], 1, stdout='{"status":"stopped"}', stderr="socket closed")
            agents = subprocess.CompletedProcess([], 1, stdout="", stderr="socket closed")
            leases = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
            evidence_dir = root / "evidence"
            with mock.patch.object(self.sim, "run", side_effect=[status, agents, leases]), \
                 mock.patch.object(
                     self.sim, "herdr_json", return_value={"result": {"agents": []}}):
                captured = self.sim.capture_simulation_evidence(
                    {}, "sim-test", dest_dir=evidence_dir, daemon=daemon)

            self.assertTrue(
                {"session-status", "agent-list", "session-daemon"} <= captured.keys())
            self.assertIn("socket closed",
                          captured["session-status"].read_text(encoding="utf-8"))
            self.assertIn("daemon failure",
                          captured["session-daemon"].read_text(encoding="utf-8"))

    def test_capture_failure_evidence_refuses_cleanup_without_all_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "server.log"
            log_path.write_text("", encoding="utf-8")
            daemon = self.sim.SessionDaemon(
                "sim-test", 23, log_path, "w1")
            with mock.patch.object(
                self.sim, "capture_simulation_evidence",
                return_value={"session-daemon": log_path},
            ):
                with self.assertRaisesRegex(RuntimeError, "incomplete.*agent-list"):
                    self.sim.capture_failure_evidence({}, "sim-test", daemon)

    def test_sigterm_handler_raises_catchable_failure(self):
        with self.assertRaisesRegex(
            self.sim.SimulationTerminated,
            r"SIGTERM.*capturing failure evidence",
        ):
            self.sim._handle_sigterm(signal.SIGTERM, None)

    def test_watch_for_action_accepts_runner_timeout(self):
        timed_out = subprocess.CompletedProcess(
            [], 1, stdout=json.dumps({"event": "timeout"}), stderr="")
        with mock.patch.object(self.sim, "run", return_value=timed_out) as run:
            self.sim.watch_for_action({}, "sim-test")

        self.assertEqual(
            run.call_args.args[0],
            ["bash", str(self.sim.HERD), "--session", "sim-test",
             "watch", "--until", "actionable",
             "--timeout", str(self.sim.WATCH_TIMEOUT)],
        )
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            self.sim.WATCH_TIMEOUT + 30.0,
        )

    def test_watch_for_action_rejects_non_timeout_failure(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="server stopped")
        with mock.patch.object(self.sim, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "watch failed.*server stopped"):
                self.sim.watch_for_action({}, "sim-test")

    def test_close_session_rejects_failed_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "server.log"
            log_path.write_text("", encoding="utf-8")
            daemon = self.sim.SessionDaemon(
                "sim-test", 23, log_path, "w1")
            failed = subprocess.CompletedProcess([], 1, stdout="", stderr="cleanup failed")
            with mock.patch.object(self.sim, "run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "cleanup failed.*sim-test"):
                    self.sim.close_session({}, daemon)

    def test_main_refuses_unsupported_lead_before_starting_a_session(self):
        with mock.patch.object(self.sim, "HERDER_CLI", "unsupported_cli"), \
             mock.patch.object(self.sim.shutil, "which", return_value="/mock/tool"):
            with self.assertRaisesRegex(AssertionError, "unsupported lead CLI|is refused"):
                self.sim.main()

    def test_check_rejection_and_interrupted_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool_file = root / "pool.json"
            state_dir = root / "fleet-state"
            env = dict(os.environ)
            env.update({
                "AGENT_HERDER_POOL_STATE_FILE": str(pool_file),
                "AGENT_HERDER_STATE_DIR": str(state_dir),
                "AGENT_HERDER_SIM_SESSION": "sim-test",
            })
            subprocess.run([sys.executable, str(self.sim.POOL), "define", "--group", "shared:staging",
                            "--resource", "api:staging", "--capacity", "2"], env=env, check=True)
            self.sim.check_rejection_and_interrupted_flow(env, "sim-test")

    def test_report_requires_complete_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps({"closed": []}), encoding="utf-8")
            self.assertEqual(self.sim.read_report_file(path), {})

            expected = {
                "closed": ["done"],
                "parked": [{"slug": "blocked", "reason": "refused", "detail": "policy"}],
                "errors": [{
                    "phase": "spawn", "slug": "blocked", "command": "spawn",
                    "stderr": "refused", "what_i_did": "parked",
                }],
            }
            path.write_text(json.dumps(expected), encoding="utf-8")
            self.assertEqual(self.sim.read_report_file(path), expected)

    def test_topology_requires_configured_lead_agy_workers_and_no_claude_workers(self):
        agents = {
            "result": {
                "agents": [
                    {"name": "sim-herder-1", "agent": self.sim.HERDER_CLI},
                    {"name": "bead-one", "agent": "agy"},
                    {"name": "bead-two", "agent": "agy"},
                ]
            }
        }
        with mock.patch.object(self.sim, "herdr_json", return_value=agents):
            self.sim.check_topology(
                {}, "sim-herder-1", {"bead-one", "bead-two"})

        agents["result"]["agents"][1]["agent"] = "codex" if self.sim.HERDER_CLI == "claude" else "claude"
        with mock.patch.object(self.sim, "herdr_json", return_value=agents):
            with self.assertRaisesRegex(AssertionError, "workers did not use only|Claude"):
                self.sim.check_topology(
                    {}, "sim-herder-1", {"bead-one", "bead-two"})

    def test_lifecycle_record_is_updated_after_session_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "lifecycle.json"
            record.write_text("{}\n", encoding="utf-8")
            self.sim.update_lifecycle({"AGENT_HERDER_SIM_LIFECYCLE_FILE": str(record)},
                                      status="session-ready", session="sim-test", daemon_pid=23)
            saved = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "session-ready")
            self.assertEqual(saved["session"], "sim-test")
            self.assertEqual(saved["daemon_pid"], 23)

    def test_main_records_failure_before_session_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "lifecycle.json"
            record.write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(self.sim.os.environ, {
                "AGENT_HERDER_SIM_LIFECYCLE_FILE": str(record),
            }, clear=True), \
                 mock.patch.object(self.sim.shutil, "which", return_value="/mock/tool"), \
                 mock.patch.object(self.sim, "open_session", side_effect=RuntimeError("bootstrap refused")):
                with self.assertRaisesRegex(RuntimeError, "bootstrap refused"):
                    self.sim.main()
            saved = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "failed-before-session")
            self.assertIn("bootstrap refused", saved["failure"])

    def test_simulation_brief_marks_its_session_preprovisioned(self):
        brief = (ROOT / "resources" / "fleet-simulation" / "herder_brief.md").read_text(
            encoding="utf-8")
        self.assertIn("**pre-provisioned** harness-owned session", brief)
        self.assertIn("Do not\nrun `init`, `attach`, or recovery", brief)
        self.assertIn("`ready` → `claim` → `spawn`", brief)


class DetachedLauncherTests(unittest.TestCase):
    def test_launcher_detaches_exact_simulation_command_and_strips_herdr_environment(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            with mock.patch.object(launcher.tempfile, "mkdtemp", return_value=str(run_dir)), \
                 mock.patch.object(launcher.subprocess, "Popen") as popen:
                popen.return_value.pid = 987
                with mock.patch.dict(launcher.os.environ, {
                    "HERDR_ENV": "1", "HERDR_SOCKET_PATH": "old", "HERDR_PANE_ID": "pane",
                }, clear=True):
                    result = launcher.launch()

            kwargs = popen.call_args.kwargs
            self.assertEqual(popen.call_args.args[0], [sys.executable, str(launcher.RUN_SIMULATION)])
            self.assertTrue(kwargs["start_new_session"])
            self.assertNotIn("HERDR_ENV", kwargs["env"])
            self.assertNotIn("HERDR_SOCKET_PATH", kwargs["env"])
            self.assertNotIn("HERDR_PANE_ID", kwargs["env"])
            self.assertEqual(result["pid"], 987)
            record = json.loads(Path(result["lifecycle"]).read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "running")
            self.assertEqual(record["simulation_pid"], 987)


if __name__ == "__main__":
    unittest.main()
