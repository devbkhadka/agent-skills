import json
import os
import re
import socket
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "preflight.sh"


def strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class PreflightJsonAndProbeTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.td.name)
        self.bin_dir = self.temp_dir / "bin"
        self.bin_dir.mkdir()
        (self.bin_dir / "python3").symlink_to(sys.executable)

        # Create a mock herdr binary
        self._create_mock_herdr()

    def tearDown(self):
        self.td.cleanup()

    def _create_mock_herdr(self, agents_count=0):
        herdr_bin = self.bin_dir / "herdr"
        herdr_bin.write_text(f"""#!/usr/bin/env bash
if [ "$1" = "--version" ]; then
    echo "herdr 0.8.2"
    exit 0
fi
if [ "$1" = "agent" ] && [ "$2" = "list" ]; then
    agents='{json.dumps([{"id": f"a{i}", "name": f"agent-{i}"} for i in range(agents_count)])}'
    echo '{{"result": {{"agents": '"$agents"'}}}}'
    exit 0
fi
if [ "$1" = "pane" ] && [ "$2" = "layout" ]; then
    echo '{{"result": {{"layout": {{"area": {{"height": 30, "width": 100}}}}}}}}'
    exit 0
fi
exit 0
""")
        herdr_bin.chmod(herdr_bin.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)

    def _create_mock_cli(self, name, probe_output="", exit_code=0, flag_support=True):
        cli_bin = self.bin_dir / name
        if name == "codex":
            help_text = "--sandbox --ask-for-approval" if flag_support else "standard help without sandbox"
        elif name == "claude":
            help_text = "--permission-mode" if flag_support else "standard help without permission mode"
        else:
            help_text = "help text"

        cli_bin.write_text(f"""#!/usr/bin/env bash
if [ "$1" = "--help" ]; then
    echo "{help_text}"
    exit 0
fi
if [ "$1" = "--approve-for-me" ] || [ "$1" = "--permission-mode" ] || [ "$1" = "--sandbox" ]; then
    if [ "{str(flag_support).lower()}" = "true" ]; then
        exit 0
    else
        exit 1
    fi
fi
if [ "$1" = "exec" ] || [ "$1" = "-p" ]; then
    echo "{probe_output}"
    exit {exit_code}
fi
echo "{probe_output}"
exit {exit_code}
""")
        cli_bin.chmod(cli_bin.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)

    def _run_preflight(self, args, extra_env=None):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin_dir}:{env.get('PATH', '')}"
        env["HERDR_ENV"] = "1"
        env["HERDR_SOCKET_PATH"] = "/tmp/mock.sock"
        if extra_env:
            env.update(extra_env)

        cmd = ["bash", str(PREFLIGHT)] + args
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            env=env
        )
        return proc

    def test_json_output_success_schema_and_types(self):
        self._create_mock_cli("claude", probe_output="ready")
        self._create_mock_cli("codex", probe_output="ready")

        res = self._run_preflight(["--cli", "claude,codex", "--json"])
        self.assertEqual(res.returncode, 0, f"Expected 0, got {res.returncode}. stderr: {res.stderr}")

        data = json.loads(res.stdout)
        self.assertEqual(data["status"], "passed")
        self.assertEqual(data["exit_code"], 0)

        # Environment
        env = data["environment"]
        self.assertEqual(env["mode"], "attached")
        self.assertIsInstance(env["cwd"], str)
        self.assertTrue(env["cwd_writable"])
        self.assertTrue(env["herdr_present"])
        self.assertEqual(env["herdr_version"], "herdr 0.8.2")

        # Session
        sess = data["session"]
        self.assertIsNone(sess["target"])
        self.assertEqual(sess["socket"], "/tmp/mock.sock")
        self.assertIsInstance(sess["agents_running"], int)

        # Resource pool
        pool = data["resource_pool"]
        self.assertTrue(pool["valid"])
        self.assertIsInstance(pool["stale_leases"], int)

        # CLIs
        clis = data["clis"]
        self.assertIn("claude", clis)
        self.assertIn("codex", clis)

        claude = clis["claude"]
        self.assertTrue(claude["present"])
        self.assertEqual(claude["path"], str(self.bin_dir / "claude"))
        self.assertTrue(claude["usable"])
        self.assertEqual(claude["probe_status"], "ready")
        self.assertIsNone(claude["reason"])
        self.assertIsNone(claude["retry_after"])
        self.assertIsNone(claude["retry_after_seconds"])

    def test_quota_exhausted_with_hms_reset_duration(self):
        # Specific defect report: "Individual quota reached. Resets in 3h6m49s"
        self._create_mock_cli("agy", probe_output="Individual quota reached. Resets in 3h6m49s")
        self._create_mock_cli("claude", probe_output="ready")

        # 1. JSON mode verification
        res = self._run_preflight(["--cli", "agy,claude", "--json"])
        self.assertEqual(res.returncode, 2)
        data = json.loads(res.stdout)

        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["exit_code"], 2)

        agy = data["clis"]["agy"]
        self.assertTrue(agy["present"])
        self.assertFalse(agy["usable"])
        self.assertEqual(agy["probe_status"], "quota_exhausted")
        self.assertIn("Individual quota reached", agy["reason"])
        self.assertEqual(agy["retry_after"], "3h6m49s")
        self.assertEqual(agy["retry_after_seconds"], 11209)

        claude = data["clis"]["claude"]
        self.assertTrue(claude["present"])
        self.assertTrue(claude["usable"])
        self.assertEqual(claude["probe_status"], "ready")

        # 2. Text mode verification: Suppress misleading "OK agy present"
        text_res = self._run_preflight(["--cli", "agy,claude"])
        self.assertEqual(text_res.returncode, 2)
        clean_stdout = strip_ansi(text_res.stdout)
        self.assertNotIn("OK    agy present", clean_stdout)
        self.assertNotIn("OK agy present", clean_stdout)
        self.assertIn("FAIL  agy QUOTA EXHAUSTED", clean_stdout)
        self.assertIn("OK    claude present", clean_stdout)
        self.assertIn("PREFLIGHT PARTIAL", clean_stdout)

    def test_quota_reset_durations_various_formats(self):
        cases = [
            ("Usage limit reached. Resets in 45m.", "45m", 2700),
            ("Rate limit exceeded, retry after 30s", "30s", 30),
            ("Quota reached. Resets in 2h", "2h", 7200),
            ("Usage limit reached. Resets in 1d2h", "1d2h", 93600),
            ("Quota reached. Resets in 3 hours, 6 minutes, 49 seconds", "3 hours, 6 minutes, 49 seconds", 11209),
            ("Rate limit reached. Please upgrade your subscription.", None, None),
        ]
        for msg, exp_str, exp_secs in cases:
            with self.subTest(msg=msg):
                self._create_mock_cli("omp", probe_output=msg)
                res = self._run_preflight(["--cli", "omp", "--json"])
                self.assertEqual(res.returncode, 2)
                data = json.loads(res.stdout)
                omp = data["clis"]["omp"]
                self.assertEqual(omp["probe_status"], "quota_exhausted")
                self.assertFalse(omp["usable"])
                self.assertEqual(omp["retry_after"], exp_str)
                self.assertEqual(omp["retry_after_seconds"], exp_secs)

    def test_unauthenticated_probe_handling(self):
        self._create_mock_cli("claude", probe_output="Not logged in. Please sign in with /login.")

        res = self._run_preflight(["--cli", "claude", "--json"])
        self.assertEqual(res.returncode, 2)
        data = json.loads(res.stdout)

        self.assertEqual(data["status"], "partial")
        claude = data["clis"]["claude"]
        self.assertTrue(claude["present"])
        self.assertFalse(claude["usable"])
        self.assertEqual(claude["probe_status"], "unauthenticated")
        self.assertIn("Not logged in", claude["reason"])
        self.assertIsNone(claude["retry_after"])
        self.assertIsNone(claude["retry_after_seconds"])

        # Text mode: No misleading OK line
        text_res = self._run_preflight(["--cli", "claude"])
        self.assertEqual(text_res.returncode, 2)
        clean_stdout = strip_ansi(text_res.stdout)
        self.assertNotIn("OK    claude present", clean_stdout)
        self.assertIn("FAIL  claude NOT AUTHENTICATED", clean_stdout)

    def test_missing_cli_on_path(self):
        # codex is not created in bin_dir
        isolated_env = {"PATH": f"{self.bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin"}
        res = self._run_preflight(["--cli", "codex", "--json"], extra_env=isolated_env)
        self.assertEqual(res.returncode, 2)
        data = json.loads(res.stdout)

        self.assertEqual(data["status"], "partial")
        codex = data["clis"]["codex"]
        self.assertFalse(codex["present"])
        self.assertIsNone(codex["path"])
        self.assertFalse(codex["usable"])
        self.assertEqual(codex["probe_status"], "missing")
        self.assertIn("not on PATH", codex["reason"])

        text_res = self._run_preflight(["--cli", "codex"], extra_env=isolated_env)
        self.assertEqual(text_res.returncode, 2)
        clean_stdout = strip_ansi(text_res.stdout)
        self.assertNotIn("OK    codex present", clean_stdout)
        self.assertIn("FAIL  codex not on PATH", clean_stdout)

    def test_missing_required_controls_claude(self):
        self._create_mock_cli("claude", probe_output="ready", flag_support=False)

        res = self._run_preflight(["--cli", "claude", "--json"])
        self.assertEqual(res.returncode, 2)
        data = json.loads(res.stdout)

        claude = data["clis"]["claude"]
        self.assertTrue(claude["present"])
        self.assertFalse(claude["usable"])
        self.assertIn("lacks required --permission-mode", claude["reason"])

        text_res = self._run_preflight(["--cli", "claude"])
        self.assertEqual(text_res.returncode, 2)
        clean_stdout = strip_ansi(text_res.stdout)
        self.assertNotIn("OK    claude present", clean_stdout)
        self.assertIn("FAIL  claude lacks required --permission-mode control", clean_stdout)

    def test_missing_required_controls_codex(self):
        self._create_mock_cli("codex", probe_output="ready", flag_support=False)

        res = self._run_preflight(["--cli", "codex", "--json"])
        self.assertEqual(res.returncode, 2)
        data = json.loads(res.stdout)

        codex = data["clis"]["codex"]
        self.assertTrue(codex["present"])
        self.assertFalse(codex["usable"])
        self.assertIn("lacks required --sandbox", codex["reason"])

        text_res = self._run_preflight(["--cli", "codex"])
        self.assertEqual(text_res.returncode, 2)
        clean_stdout = strip_ansi(text_res.stdout)
        self.assertNotIn("OK    codex present", clean_stdout)
        self.assertIn("FAIL  codex lacks required --sandbox/--ask-for-approval controls", clean_stdout)

    def test_no_quota_flag_marks_probe_unverified(self):
        self._create_mock_cli("claude", probe_output="quota reached")

        res = self._run_preflight(["--cli", "claude", "--no-quota", "--json"])
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)

        self.assertEqual(data["status"], "passed")
        claude = data["clis"]["claude"]
        self.assertTrue(claude["present"])
        self.assertTrue(claude["usable"])
        self.assertEqual(claude["probe_status"], "unverified")
        self.assertIsNone(claude["reason"])

    def test_session_target_and_agents_running_in_json(self):
        self._create_mock_herdr(agents_count=3)
        self._create_mock_cli("claude", probe_output="ready")

        session_name = f"test-preflight-{os.getpid()}"
        session_sock_dir = Path.home() / ".config" / "herdr" / "sessions" / session_name
        session_sock_dir.mkdir(parents=True, exist_ok=True)
        session_sock = session_sock_dir / "herdr.sock"
        if session_sock.exists():
            session_sock.unlink()

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(str(session_sock))

        try:
            res = self._run_preflight(["--cli", "claude", "--session", session_name, "--allow-cross-session", "--json"])
            self.assertEqual(res.returncode, 0, f"Expected 0, got {res.returncode}. stderr: {res.stderr}")
            data = json.loads(res.stdout)
            self.assertEqual(data["session"]["target"], session_name)
            self.assertEqual(data["session"]["socket"], str(session_sock))
            self.assertEqual(data["session"]["agents_running"], 3)
        finally:
            server_sock.close()
            if session_sock.exists():
                session_sock.unlink()
            if session_sock_dir.exists():
                shutil.rmtree(session_sock_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
