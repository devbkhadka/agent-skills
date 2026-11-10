import os
import sys
import json
import time
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from resource_pool import (
    ResourcePoolManager,
    StateError,
    DEFAULT_TTL,
    WAITER_TTL,
    SCHEMA_VERSION,
)


class ResourceValidationTests(unittest.TestCase):
    def test_resource_validation_rejects_zero_capacity(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = ResourcePoolManager(Path(td) / "state.json")
            with self.assertRaises(ValueError):
                mgr.define("g", "r", 0)

    def test_resource_validation_rejects_negative_capacity(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = ResourcePoolManager(Path(td) / "state.json")
            with self.assertRaises(ValueError):
                mgr.define("g", "r", -1)

    def test_resource_validation_rejects_zero_or_negative_ttl(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = ResourcePoolManager(Path(td) / "state.json")
            with self.assertRaises(ValueError):
                mgr.define("g", "r", 1, ttl=0)
            with self.assertRaises(ValueError):
                mgr.define("g", "r", 1, ttl=-10.0)
            with self.assertRaises(ValueError):
                mgr.acquire("g", "r", "agent-1", ttl=0)
            with self.assertRaises(ValueError):
                mgr.renew("g", "r", "agent-1", ttl=-5.0)

    def test_resource_state_corruption_fails_closed_and_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            path.write_text("not-json")
            mgr = ResourcePoolManager(path)
            with self.assertRaises(StateError):
                mgr.status()
            self.assertEqual(path.read_text(), "not-json")

    def test_resource_state_unsupported_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            path.write_text(json.dumps({"version": 999, "groups": {}}))
            mgr = ResourcePoolManager(path)
            with self.assertRaises(StateError):
                mgr.status()


class ResourceLockConcurrencyTests(unittest.TestCase):
    def test_resource_file_lock_allows_only_one_concurrent_holder(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            env = dict(
                os.environ,
                AGENT_HERDER_POOL_STATE_FILE=str(state),
                PYTHONDONTWRITEBYTECODE="1",
            )
            script = str(ROOT / "scripts" / "resource_pool.py")
            argv = [
                sys.executable,
                script,
                "acquire",
                "--group",
                "g",
                "--resource",
                "file:x",
                "--timeout",
                "0",
            ]
            first = subprocess.Popen(
                [*argv, "--agent", "Agent-1"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            second = subprocess.Popen(
                [*argv, "--agent", "Agent-2"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out1, err1 = first.communicate(timeout=10)
            out2, err2 = second.communicate(timeout=10)
            self.assertEqual(
                sorted([first.returncode, second.returncode]),
                [0, 1],
                msg=f"one holder expected: {out1} {err1} {out2} {err2}",
            )
            status = ResourcePoolManager(state).status("g", reap=False)
            self.assertEqual(status["g"]["resources"]["file:x"]["held"], 1)


class ResourceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state_file = Path(self.td.name) / "state.json"
        self.mgr = ResourcePoolManager(self.state_file)

    def tearDown(self):
        self.td.cleanup()

    def test_define_and_acquire(self):
        def_res = self.mgr.define("repo:/app", "file:main.py", capacity=1, ttl=60.0)
        self.assertEqual(def_res["status"], "ok")
        self.assertEqual(def_res["capacity"], 1)
        self.assertEqual(def_res["ttl"], 60.0)

        acq_res = self.mgr.acquire("repo:/app", "file:main.py", "worker-1")
        self.assertEqual(acq_res["status"], "acquired")
        self.assertFalse(acq_res["renewed"])
        self.assertEqual(acq_res["held"], 1)
        self.assertEqual(acq_res["capacity"], 1)

    def test_acquire_conflict_on_capacity(self):
        self.mgr.define("repo:/app", "file:main.py", capacity=1)
        r1 = self.mgr.acquire("repo:/app", "file:main.py", "worker-1", timeout=0)
        self.assertEqual(r1["status"], "acquired")

        r2 = self.mgr.acquire("repo:/app", "file:main.py", "worker-2", timeout=0)
        self.assertEqual(r2["status"], "locked")
        self.assertEqual(r2["queue_position"], 1)
        self.assertIn("worker-1", r2["holders"])

    def test_reacquire_renews_lease(self):
        r1 = self.mgr.acquire("repo:/app", "file:main.py", "worker-1", ttl=100.0)
        self.assertEqual(r1["status"], "acquired")
        self.assertFalse(r1["renewed"])

        r2 = self.mgr.acquire("repo:/app", "file:main.py", "worker-1", ttl=200.0)
        self.assertEqual(r2["status"], "acquired")
        self.assertTrue(r2["renewed"])
        self.assertEqual(r2["held"], 1)

    def test_renew_held_lease(self):
        self.mgr.acquire("repo:/app", "file:main.py", "worker-1", ttl=100.0)
        renew_res = self.mgr.renew("repo:/app", "file:main.py", "worker-1", ttl=300.0)
        self.assertEqual(renew_res["status"], "renewed")
        self.assertGreater(renew_res["expires_at"], time.time() + 200.0)

    def test_renew_unheld_lease_returns_lost(self):
        renew_res = self.mgr.renew("repo:/app", "file:main.py", "worker-1")
        self.assertEqual(renew_res["status"], "lost")

    def test_release_held_lease(self):
        self.mgr.acquire("repo:/app", "file:main.py", "worker-1")
        rel_res = self.mgr.release("repo:/app", "file:main.py", "worker-1")
        self.assertEqual(rel_res["status"], "released")

        # Releasing again returns not_found
        rel_again = self.mgr.release("repo:/app", "file:main.py", "worker-1")
        self.assertEqual(rel_again["status"], "not_found")

    def test_release_agent_drops_all_leases_in_group(self):
        self.mgr.acquire("repo:/app", "file:a.py", "worker-1")
        self.mgr.define("repo:/app", "file:b.py", capacity=2)
        self.mgr.acquire("repo:/app", "file:b.py", "worker-1")
        self.mgr.acquire("repo:/app", "file:b.py", "worker-2")

        res = self.mgr.release_agent("repo:/app", "worker-1")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(sorted(res["freed"]), ["file:a.py", "file:b.py"])

        status = self.mgr.status("repo:/app")
        self.assertEqual(status["repo:/app"]["resources"]["file:a.py"]["held"], 0)
        self.assertEqual(status["repo:/app"]["resources"]["file:b.py"]["held"], 1)
        self.assertEqual(
            status["repo:/app"]["resources"]["file:b.py"]["holders"][0]["agent"],
            "worker-2",
        )


class QueueAndFifoTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state_file = Path(self.td.name) / "state.json"
        self.mgr = ResourcePoolManager(self.state_file)

    def tearDown(self):
        self.td.cleanup()

    def test_fifo_queue_ordering(self):
        self.mgr.define("g", "res", capacity=1)
        # Agent-1 holds
        self.mgr.acquire("g", "res", "Agent-1")
        # Agent-2 queues first
        r2 = self.mgr.acquire("g", "res", "Agent-2", timeout=0)
        self.assertEqual(r2["status"], "locked")
        self.assertEqual(r2["queue_position"], 1)

        time.sleep(0.01)
        # Agent-3 queues second
        r3 = self.mgr.acquire("g", "res", "Agent-3", timeout=0)
        self.assertEqual(r3["status"], "locked")
        self.assertEqual(r3["queue_position"], 2)

        # Release Agent-1
        self.mgr.release("g", "res", "Agent-1")

        # Agent-3 tries to acquire before Agent-2: with FIFO, Agent-3 should be blocked because Agent-2 has priority
        r3_retry = self.mgr.acquire("g", "res", "Agent-3", timeout=0, fifo=True)
        self.assertEqual(r3_retry["status"], "locked")

        # Agent-2 acquires successfully
        r2_retry = self.mgr.acquire("g", "res", "Agent-2", timeout=0, fifo=True)
        self.assertEqual(r2_retry["status"], "acquired")

    def test_no_fifo_allows_barging(self):
        self.mgr.define("g", "res", capacity=1)
        self.mgr.acquire("g", "res", "Agent-1")
        self.mgr.acquire("g", "res", "Agent-2", timeout=0)

        # Release Agent-1
        self.mgr.release("g", "res", "Agent-1")

        # Agent-3 barges with fifo=False
        r3 = self.mgr.acquire("g", "res", "Agent-3", timeout=0, fifo=False)
        self.assertEqual(r3["status"], "acquired")

    def test_release_dequeues_waiter(self):
        self.mgr.define("g", "res", capacity=1)
        self.mgr.acquire("g", "res", "Agent-1")
        self.mgr.acquire("g", "res", "Agent-2", timeout=0)

        rel = self.mgr.release("g", "res", "Agent-2")
        self.assertEqual(rel["status"], "dequeued")


class ReapAndLivenessTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state_file = Path(self.td.name) / "state.json"
        self.mgr = ResourcePoolManager(self.state_file)

    def tearDown(self):
        self.td.cleanup()

    def test_reap_expired_lease(self):
        # Acquire with a very short TTL
        self.mgr.acquire("g", "res", "Agent-1", ttl=0.1)
        time.sleep(0.2)

        reap_res = self.mgr.reap("g")
        self.assertEqual(reap_res["status"], "ok")
        self.assertEqual(reap_res["reaped_count"], 1)
        self.assertEqual(reap_res["reaped"][0]["agent"], "Agent-1")
        self.assertEqual(reap_res["reaped"][0]["reason"], "expired")

        # Resource is now free
        status = self.mgr.status("g")
        self.assertEqual(status["g"]["resources"]["res"]["held"], 0)

    def test_reap_dead_pid_on_same_host(self):
        # Spawn a dummy process and wait for it to exit
        proc = subprocess.Popen(["true"])
        proc.wait()
        dead_pid = proc.pid

        # Acquire lease with dead_pid
        self.mgr.acquire("g", "res", "Agent-Dead", ttl=900.0, pid=dead_pid)

        # Reaping should clean it immediately based on PID liveness
        reap_res = self.mgr.reap("g")
        self.assertEqual(reap_res["reaped_count"], 1)
        self.assertEqual(reap_res["reaped"][0]["reason"], "process_gone")

    def test_keep_live_pid_on_same_host(self):
        # Current process is alive
        my_pid = os.getpid()
        self.mgr.acquire("g", "res", "Agent-Live", ttl=900.0, pid=my_pid)

        reap_res = self.mgr.reap("g")
        self.assertEqual(reap_res["reaped_count"], 0)

        status = self.mgr.status("g")
        self.assertEqual(status["g"]["resources"]["res"]["held"], 1)

    def test_dry_run_reap_does_not_mutate_state(self):
        self.mgr.acquire("g", "res", "Agent-1", ttl=0.1)
        time.sleep(0.2)

        dry = self.mgr.reap("g", dry_run=True)
        self.assertEqual(dry["reaped_count"], 1)
        self.assertTrue(dry["dry_run"])

        # Without auto-reap, status shows it still in raw state
        raw_status = self.mgr.status("g", reap=False)
        self.assertEqual(raw_status["g"]["resources"]["res"]["held"], 1)


class GroupAndStatusTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state_file = Path(self.td.name) / "state.json"
        self.mgr = ResourcePoolManager(self.state_file)

    def tearDown(self):
        self.td.cleanup()

    def test_list_and_clear_groups(self):
        self.mgr.acquire("group-a", "res1", "Agent-1")
        self.mgr.acquire("group-b", "res2", "Agent-2")

        groups = self.mgr.list_groups()
        self.assertIn("group-a", groups)
        self.assertIn("group-b", groups)

        clr = self.mgr.clear_group("group-a")
        self.assertEqual(clr["status"], "cleared")
        self.assertNotIn("group-a", self.mgr.list_groups())
        self.assertIn("group-b", self.mgr.list_groups())

    def test_schema_v1_migration(self):
        v1_state = {
            "version": 1,
            "groups": {
                "g1": {
                    "resources": {
                        "r1": {
                            "capacity": 1,
                            "locked_by": ["LegacyAgent"],
                            "waiting": ["WaitingAgent"],
                        }
                    }
                }
            },
        }
        self.state_file.write_text(json.dumps(v1_state))

        # Status in-memory loads and migrates transparently
        status = self.mgr.status("g1", reap=False)
        res = status["g1"]["resources"]["r1"]
        self.assertEqual(res["held"], 1)
        self.assertEqual(res["holders"][0]["agent"], "LegacyAgent")
        self.assertEqual(res["waiting"], ["WaitingAgent"])

        # Mutation persists migrated schema
        self.mgr.release("g1", "r1", "LegacyAgent")
        raw = json.loads(self.state_file.read_text())
        self.assertEqual(raw["version"], SCHEMA_VERSION)


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state_file = Path(self.td.name) / "state.json"
        self.env = dict(
            os.environ,
            AGENT_HERDER_POOL_STATE_FILE=str(self.state_file),
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.script = str(ROOT / "scripts" / "resource_pool.py")

    def tearDown(self):
        self.td.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, self.script, *args],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_cli_full_workflow(self):
        # 1. define
        p = self.run_cli(
            "define", "--group", "g", "--resource", "file:a.py", "--capacity", "1", "--ttl", "120"
        )
        self.assertEqual(p.returncode, 0)
        data = json.loads(p.stdout)
        self.assertEqual(data["status"], "ok")

        # 2. acquire
        p = self.run_cli(
            "acquire", "--group", "g", "--resource", "file:a.py", "--agent", "agent-1"
        )
        self.assertEqual(p.returncode, 0)
        data = json.loads(p.stdout)
        self.assertEqual(data["status"], "acquired")

        # 3. acquire conflict (exit code 1)
        p = self.run_cli(
            "acquire", "--group", "g", "--resource", "file:a.py", "--agent", "agent-2"
        )
        self.assertEqual(p.returncode, 1)
        data = json.loads(p.stdout)
        self.assertEqual(data["status"], "locked")

        # 4. renew
        p = self.run_cli(
            "renew", "--group", "g", "--resource", "file:a.py", "--agent", "agent-1"
        )
        self.assertEqual(p.returncode, 0)
        data = json.loads(p.stdout)
        self.assertEqual(data["status"], "renewed")

        # 5. status
        p = self.run_cli("status", "--group", "g")
        self.assertEqual(p.returncode, 0)
        data = json.loads(p.stdout)
        self.assertEqual(data["g"]["resources"]["file:a.py"]["held"], 1)

        # 6. release
        p = self.run_cli(
            "release", "--group", "g", "--resource", "file:a.py", "--agent", "agent-1"
        )
        self.assertEqual(p.returncode, 0)
        data = json.loads(p.stdout)
        self.assertEqual(data["status"], "released")

        # 7. list-groups & clear-group
        p = self.run_cli("list-groups")
        self.assertEqual(p.returncode, 0)
        self.assertIn("g", json.loads(p.stdout))

        p = self.run_cli("clear-group", "--group", "g")
        self.assertEqual(p.returncode, 0)
        data = json.loads(p.stdout)
        self.assertEqual(data["status"], "cleared")


if __name__ == "__main__":
    unittest.main()
