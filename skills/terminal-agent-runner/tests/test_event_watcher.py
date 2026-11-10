import argparse
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fleet = load_module("terminal_agent_runner_fleet", ROOT / "scripts" / "fleet.py")


class EventWatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp_dir.name)
        self.patcher = mock.patch.object(fleet, "STATE_DIR", self.state_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp_dir.cleanup()

    def test_drain_events_empty_and_nonexistent(self):
        # Non-existent session events dir
        self.assertEqual(fleet.drain_events("sess-1"), [])

        # Empty events dir
        events_dir = fleet.state_path("events", "sess-1")
        events_dir.mkdir(parents=True, exist_ok=True)
        self.assertEqual(fleet.drain_events("sess-1"), [])

    def test_drain_events_success_and_unlink_sorted(self):
        events_dir = fleet.state_path("events", "sess-1")
        events_dir.mkdir(parents=True, exist_ok=True)

        evt2 = events_dir / "evt_2000_worker2_b.json"
        evt1 = events_dir / "evt_1000_worker1_a.json"
        evt3 = events_dir / "evt_3000_worker3_c.json"

        evt2.write_text(json.dumps({"agent": "worker2", "kind": "notify", "timestamp": 2000}), encoding="utf-8")
        evt1.write_text(json.dumps({"agent": "worker1", "kind": "parked", "at": 1000}), encoding="utf-8")
        evt3.write_text(json.dumps({"agent": "worker3", "kind": "complete", "at": 3000}), encoding="utf-8")

        drained = fleet.drain_events("sess-1")
        self.assertEqual(len(drained), 3)
        self.assertEqual(drained[0]["agent"], "worker1")
        self.assertEqual(drained[1]["agent"], "worker2")
        self.assertEqual(drained[2]["agent"], "worker3")

        # Files should be unlinked
        self.assertFalse(evt1.exists())
        self.assertFalse(evt2.exists())
        self.assertFalse(evt3.exists())
        # Subsequent drain should be empty
        self.assertEqual(fleet.drain_events("sess-1"), [])

    def test_drain_events_ignores_corrupted_and_inflight_files(self):
        events_dir = fleet.state_path("events", "sess-1")
        events_dir.mkdir(parents=True, exist_ok=True)

        corrupt = events_dir / "evt_1000_corrupt.json"
        corrupt.write_text("{broken json syntax...", encoding="utf-8")

        empty = events_dir / "evt_1500_empty.json"
        empty.write_text("", encoding="utf-8")

        non_dict = events_dir / "evt_1600_nondict.json"
        non_dict.write_text("[1, 2, 3]", encoding="utf-8")

        valid = events_dir / "evt_2000_valid.json"
        valid.write_text(json.dumps({"agent": "worker1", "kind": "notify", "at": 2000}), encoding="utf-8")

        # Non-event file (should not even match glob)
        other = events_dir / "other_file.txt"
        other.write_text("hello", encoding="utf-8")

        drained = fleet.drain_events("sess-1")
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0]["agent"], "worker1")
        self.assertFalse(valid.exists())
        self.assertTrue(other.exists())

    def test_wait_for_events_immediate_if_present(self):
        fleet.emit_event("sess-1", "worker1", "notify", {"info": "test"})
        t0 = time.monotonic()
        res = fleet.wait_for_events("sess-1", timeout=1.0)
        elapsed = time.monotonic() - t0
        self.assertTrue(res)
        self.assertLess(elapsed, 0.2)

    def test_wait_for_events_wakes_on_emission(self):
        events_dir = fleet.state_path("events", "sess-1")

        def background_emit():
            time.sleep(0.05)
            fleet.emit_event("sess-1", "worker1", "parked", {"reason": "test"})

        t = threading.Thread(target=background_emit)
        t.start()

        t0 = time.monotonic()
        res = fleet.wait_for_events("sess-1", timeout=2.0)
        elapsed = time.monotonic() - t0
        t.join()

        self.assertTrue(res)
        self.assertLess(elapsed, 0.5)

    def test_wait_for_events_timeout_when_no_events(self):
        t0 = time.monotonic()
        res = fleet.wait_for_events("sess-1", timeout=0.1)
        elapsed = time.monotonic() - t0
        self.assertFalse(res)
        self.assertGreaterEqual(elapsed, 0.08)

    def test_cmd_watch_wakes_immediately_on_event(self):
        call_count = 0
        def fake_fleet_rows(session=None, settle_samples=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{
                    "agent": "worker-1",
                    "cli": "agy",
                    "state": "working",
                    "health": "ok",
                    "action": None,
                    "rule": None,
                    "pending": None,
                }]
            else:
                return [{
                    "agent": "worker-1",
                    "cli": "agy",
                    "state": "parked",
                    "health": "ok",
                    "action": None,
                    "rule": None,
                    "pending": None,
                }]

        def background_emit():
            time.sleep(0.05)
            fleet.emit_event("sess-1", "worker-1", "parked", {"reason": "blocked"})

        t = threading.Thread(target=background_emit)
        t.start()

        args = argparse.Namespace(
            session="sess-1",
            until="actionable",
            timeout=2.0,
            interval=1.0,
            renew_interval=60.0,
            settle_samples=1,
        )

        buf = io.StringIO()
        with mock.patch.object(fleet, "fleet_rows", side_effect=fake_fleet_rows), \
             mock.patch("sys.stdout", buf):
            t0 = time.monotonic()
            fleet.cmd_watch(args)
            elapsed = time.monotonic() - t0

        t.join()
        self.assertLess(elapsed, 0.5)
        out = json.loads(buf.getvalue())
        self.assertEqual(out.get("event"), "actionable")
        self.assertEqual(len(out.get("fleet", [])), 1)
        self.assertEqual(out["fleet"][0]["state"], "parked")

    def test_cmd_watch_timeout_exits_with_code_1(self):
        def fake_fleet_rows(session=None, settle_samples=None):
            return [{
                "agent": "worker-1",
                "cli": "agy",
                "state": "working",
                "health": "ok",
                "action": None,
                "rule": None,
                "pending": None,
            }]

        args = argparse.Namespace(
            session="sess-1",
            until="actionable",
            timeout=0.05,
            interval=1.0,
            renew_interval=60.0,
            settle_samples=1,
        )

        buf = io.StringIO()
        with mock.patch.object(fleet, "fleet_rows", side_effect=fake_fleet_rows), \
             mock.patch("sys.stdout", buf):
            with self.assertRaises(SystemExit) as cm:
                fleet.cmd_watch(args)
            self.assertEqual(cm.exception.code, 1)

        out = json.loads(buf.getvalue())
        self.assertEqual(out.get("event"), "timeout")


if __name__ == "__main__":
    unittest.main()
