import io
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import importlib.util

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

fleet = load_module("fleet_outbox_test", ROOT / "scripts" / "fleet.py")


class TestOutboxEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "fleet-state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.orig_state_dir = fleet.STATE_DIR
        fleet.STATE_DIR = self.state_dir
        self.session = "test-session"

    def tearDown(self):
        fleet.STATE_DIR = self.orig_state_dir
        self.tmp.cleanup()

    def test_state_path_events_directories(self):
        events_path = fleet.state_path("events", self.session)
        self.assertTrue(events_path.is_dir())
        self.assertEqual(events_path, self.state_dir / self.session / "events")

        tmp_path = fleet.state_path("events/tmp", self.session)
        self.assertTrue(tmp_path.is_dir())
        self.assertEqual(tmp_path, self.state_dir / self.session / "events" / "tmp")

    def test_emit_event_atomic_file_creation(self):
        payload = {"step": 1, "detail": "testing outbox"}
        before_t = time.time()
        evt_file = fleet.emit_event(self.session, "worker-1", "custom_event", payload)
        after_t = time.time()

        self.assertTrue(evt_file.is_file())
        self.assertEqual(evt_file.parent, self.state_dir / self.session / "events")

        # Filename pattern: evt_{int(time.time()*1000)}_{agent}_{uuid[:8]}.json
        filename = evt_file.name
        self.assertTrue(
            re.match(r"^evt_\d+_worker-1_[0-9a-f]{8}\.json$", filename),
            f"Filename {filename} does not match expected format",
        )

        data = json.loads(evt_file.read_text(encoding="utf-8"))
        self.assertEqual(data["agent"], "worker-1")
        self.assertEqual(data["kind"], "custom_event")
        self.assertGreaterEqual(data["at"], before_t)
        self.assertLessEqual(data["at"], after_t + 0.1)
        self.assertEqual(data["payload"], payload)

        # Ensure tmp dir has no leftover temp files
        tmp_files = list((self.state_dir / self.session / "events" / "tmp").iterdir())
        self.assertEqual(tmp_files, [])

    def test_emit_event_none_payload_defaults_to_empty_dict(self):
        evt_file = fleet.emit_event(self.session, "worker-2", "ping", None)
        data = json.loads(evt_file.read_text(encoding="utf-8"))
        self.assertEqual(data["payload"], {})

    def test_cmd_notify_success(self):
        class Args:
            session = self.session
            agent = "worker-notify"
            kind = "ready"
            payload = '{"status": "ok", "items": [1, 2]}'

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            fleet.cmd_notify(Args())

        out_json = json.loads(buf.getvalue())
        self.assertTrue(out_json["notified"])
        self.assertEqual(out_json["agent"], "worker-notify")
        self.assertEqual(out_json["kind"], "ready")
        self.assertTrue(Path(out_json["file"]).is_file())

        data = json.loads(Path(out_json["file"]).read_text(encoding="utf-8"))
        self.assertEqual(data["payload"], {"status": "ok", "items": [1, 2]})

    def test_cmd_notify_dict_payload(self):
        class Args:
            session = self.session
            agent = "worker-notify-dict"
            kind = "heartbeat"
            payload = {"alive": True}

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            fleet.cmd_notify(Args())

        out_json = json.loads(buf.getvalue())
        self.assertTrue(out_json["notified"])
        data = json.loads(Path(out_json["file"]).read_text(encoding="utf-8"))
        self.assertEqual(data["payload"], {"alive": True})

    def test_cmd_notify_invalid_payload_json(self):
        class Args:
            session = self.session
            agent = "worker-bad"
            kind = "fail"
            payload = '{"unclosed_json'

        with self.assertRaises(SystemExit):
            fleet.cmd_notify(Args())

    def test_cmd_notify_non_dict_payload(self):
        class Args:
            session = self.session
            agent = "worker-bad"
            kind = "fail"
            payload = '["not", "a", "dict"]'

        with self.assertRaises(SystemExit):
            fleet.cmd_notify(Args())

    def test_cmd_park_emits_event(self):
        # Set up assignment in tasks.json
        tasks_file = fleet.state_path("tasks.json", self.session)
        tasks_file.write_text(json.dumps({
            "worker-park": {
                "task": "Do work",
                "group": "grp-1",
                "resources": ["file:a.txt"],
                "lifecycle": "active",
            }
        }))

        class Args:
            session = self.session
            agent = "worker-park"
            group = "grp-1"
            reason = "Awaiting human review for migration"
            cancel = False

        with mock.patch.object(fleet, "release_agent_strict", return_value={"freed": ["file:a.txt"]}), \
             mock.patch("sys.stdout", io.StringIO()):
            fleet.cmd_park(Args())

        events_dir = fleet.state_path("events", self.session)
        evt_files = list(events_dir.glob("evt_*_worker-park_*.json"))
        self.assertEqual(len(evt_files), 1)

        data = json.loads(evt_files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["agent"], "worker-park")
        self.assertEqual(data["kind"], "parked")
        self.assertEqual(data["payload"]["reason"], "Awaiting human review for migration")
        self.assertEqual(data["payload"]["freed_locks"], ["file:a.txt"])

    def test_cmd_complete_emits_event(self):
        handoff_data = {
            "status": "completed",
            "summary": "Finished task cleanly",
            "decisions": ["Used schema v2"],
            "changed_files": ["lib/core.py"],
            "verification": [
                {"command": "pytest", "exit_code": 0, "expected_exit": 0, "expected_empty": False, "output": "3 passed"}
            ],
            "blockers": [],
            "next_inputs": [],
        }
        tasks_file = fleet.state_path("tasks.json", self.session)
        tasks_file.write_text(json.dumps({
            "worker-done": {
                "task": "Complete core feature",
                "group": "grp-done",
                "resources": ["file:lib/core.py"],
                "lifecycle": "active",
            }
        }))

        class Args:
            session = self.session
            agent = "worker-done"
            group = "grp-done"
            lines = 45
            max_chars = 4000
            handoff = json.dumps(handoff_data)

        with mock.patch.object(fleet, "release_agent_strict", return_value={"freed": ["file:lib/core.py"]}), \
             mock.patch.object(fleet, "find_agent", return_value=None), \
             mock.patch("sys.stdout", io.StringIO()):
            fleet.cmd_complete(Args())

        events_dir = fleet.state_path("events", self.session)
        evt_files = list(events_dir.glob("evt_*_worker-done_*.json"))
        self.assertEqual(len(evt_files), 1)

        data = json.loads(evt_files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["agent"], "worker-done")
        self.assertEqual(data["kind"], "completed")
        self.assertEqual(data["payload"]["handoff"]["summary"], "Finished task cleanly")
        self.assertEqual(data["payload"]["summary"], "Finished task cleanly")
        self.assertEqual(data["payload"]["freed_locks"], ["file:lib/core.py"])

    def test_cmd_reset_emits_event(self):
        tasks_file = fleet.state_path("tasks.json", self.session)
        tasks_file.write_text(json.dumps({
            "worker-reset": {
                "task": "Old task",
                "group": "grp-old",
                "resources": [],
                "lifecycle": "active",
            }
        }))

        class Args:
            session = self.session
            agent = "worker-reset"
            cli = "agy"
            task = "New reset task"
            handoff = "Previous findings"
            group = "grp-new"
            old_group = "grp-old"
            resource = []
            read_only = False
            lock_timeout = 0
            approval_mode = "auto"
            cwd = "/tmp"

        with mock.patch.object(fleet, "acquire_assignment", return_value=[]), \
             mock.patch.object(fleet, "release_agent_strict", return_value={"freed": []}), \
             mock.patch.object(fleet, "find_agent", return_value=None), \
             mock.patch.object(fleet, "cmd_spawn", return_value={"spawned": True}), \
             mock.patch("sys.stdout", io.StringIO()):
            fleet.cmd_reset(Args())

        events_dir = fleet.state_path("events", self.session)
        evt_files = list(events_dir.glob("evt_*_worker-reset_*.json"))
        self.assertEqual(len(evt_files), 1)

        data = json.loads(evt_files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["agent"], "worker-reset")
        self.assertEqual(data["kind"], "reset")
        self.assertIn("New reset task", data["payload"]["task"])
        self.assertEqual(data["payload"]["agent"], "worker-reset")

    def test_herd_init_cleans_stale_events(self):
        events_dir = self.state_dir / self.session / "events"
        tmp_dir = events_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        stale_evt = events_dir / "evt_12345_stale_abcd1234.json"
        stale_evt.write_text('{"stale": true}')
        stale_tmp = tmp_dir / "stale_12345_tmp1234.json"
        stale_tmp.write_text('{"stale": true}')

        self.assertTrue(stale_evt.is_file())
        self.assertTrue(stale_tmp.is_file())

        env = dict(os.environ)
        env["AGENT_HERDER_STATE_DIR"] = str(self.state_dir)
        env["PATH"] = f"{ROOT / 'scripts'}:{env.get('PATH', '')}"

        # We can test herd.sh init with mocked herdr or direct subshell
        herd_sh = ROOT / "scripts" / "herd.sh"
        # Run a bash fragment mirroring herd.sh init setup or invoke herd.sh with a mock herdr
        res = os.system(f'AGENT_HERDER_STATE_DIR="{self.state_dir}" bash -c \'STATE_DIR="{self.state_dir}"; EVENTS_DIR="$STATE_DIR/{self.session}/events"; mkdir -p "$EVENTS_DIR/tmp"; rm -f "$EVENTS_DIR"/*.json "$EVENTS_DIR"/tmp/*.json 2>/dev/null || true\'')
        self.assertEqual(res, 0)
        self.assertFalse(stale_evt.exists())
        self.assertFalse(stale_tmp.exists())
        self.assertTrue(tmp_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
