import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERD_SH = ROOT / "scripts" / "herd.sh"
CLEANUP_SH = ROOT / "scripts" / "cleanup_fleet.sh"


class DaemonLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.session = f"test-lifecycle-{os.getpid()}"
        self.temp_dir = tempfile.mkdtemp()
        self.repo = Path(self.temp_dir)

    def tearDown(self):
        subprocess.run(
            ["bash", str(CLEANUP_SH), "--session", self.session],
            capture_output=True,
            text=True,
        )
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_daemon_detaches_into_new_session_and_survives_init_exit(self):
        # 1. Run herd.sh init in a child process
        init_cmd = [
            "bash", str(HERD_SH),
            "--session", self.session,
            "--emit-daemon-evidence",
            "init", "--cwd", str(self.repo),
        ]
        proc = subprocess.run(init_cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"herd.sh init failed: {proc.stderr or proc.stdout}")
        
        evidence = json.loads(proc.stdout)
        daemon_pid = evidence.get("daemon_pid")
        self.assertIsNotNone(daemon_pid)
        self.assertIsInstance(daemon_pid, int)

        # 2. Verify daemon is running and has its own session (setsid)
        daemon_sid = os.getsid(daemon_pid)
        self.assertEqual(daemon_sid, daemon_pid, f"Daemon PID {daemon_pid} should be session leader (SID {daemon_sid})")

        # 3. Verify status through wrapper succeeds
        status_cmd = [
            "bash", str(HERD_SH),
            "--session", self.session,
            "status", "--json",
        ]
        proc_status = subprocess.run(status_cmd, capture_output=True, text=True)
        self.assertEqual(proc_status.returncode, 0, f"herd.sh status failed: {proc_status.stderr or proc_status.stdout}")
        status_data = json.loads(proc_status.stdout)
        self.assertEqual(status_data.get("session"), self.session)


if __name__ == "__main__":
    unittest.main()
