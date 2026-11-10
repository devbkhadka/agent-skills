import argparse
import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fleet = load_module("terminal_agent_runner_fleet_profiles", ROOT / "scripts" / "fleet.py")

PROFILES = {
    "default": "work",
    "profiles": {
        "work": {
            "codex": {"CODEX_HOME": "~/.codex-work"},
            "agy": {"AGY_ACCOUNT_HOME": "$HOME/.agy-accounts/work"},
        },
        "personal": {
            "codex": {"CODEX_HOME": "~/.codex-personal"},
            "claude": {"CLAUDE_CONFIG_DIR": "~/.claude-personal"},
        },
        "client-x": {
            "codex": {"CODEX_HOME": "~/.codex-client-x"},
        },
    },
}


class ProfileFileCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name).resolve()
        self.file = self.dir / "profiles.json"
        self.home = self.dir / "home"
        patches = [
            mock.patch.object(fleet, "PROFILES_FILE", self.file),
            mock.patch.dict(os.environ, {"HOME": str(self.home)}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def write(self, data):
        self.file.write_text(json.dumps(data))

    def log_in_agy(self, account_home):
        token = Path(account_home) / fleet.AGY_TOKEN_FILE
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text("{}")


class ResolveProfileTests(ProfileFileCase):
    def test_missing_file_launches_the_plain_cli(self):
        self.assertEqual(fleet.resolve_profile("codex", None), ("none", {}))

    def test_explicit_profile_wins_over_default_and_expands_home(self):
        self.write(PROFILES)
        self.assertEqual(fleet.resolve_profile("codex", "personal"),
                         ("personal", {"CODEX_HOME": f"{self.home}/.codex-personal"}))

    def test_default_applies_when_no_profile_is_named(self):
        self.write(PROFILES)
        self.log_in_agy(self.home / ".agy-accounts" / "work")
        self.assertEqual(fleet.resolve_profile("agy", None),
                         ("work", {"AGY_ACCOUNT_HOME": f"{self.home}/.agy-accounts/work"}))

    def test_agy_account_that_never_logged_in_is_refused(self):
        self.write(PROFILES)
        with self.assertRaises(SystemExit) as ctx:
            fleet.resolve_profile("agy", "work")
        self.assertIn("not logged in", str(ctx.exception))

    def test_more_than_two_profiles_are_supported(self):
        self.write(PROFILES)
        name, env = fleet.resolve_profile("codex", "client-x")
        self.assertEqual((name, env["CODEX_HOME"]), ("client-x", f"{self.home}/.codex-client-x"))

    def test_default_without_an_entry_for_the_cli_launches_the_plain_cli(self):
        self.write(PROFILES)
        self.assertEqual(fleet.resolve_profile("claude", None), ("none", {}))

    def test_none_forces_the_plain_cli_even_with_a_default(self):
        self.write(PROFILES)
        self.assertEqual(fleet.resolve_profile("codex", "none"), ("none", {}))

    def test_no_default_launches_the_plain_cli(self):
        self.write({"profiles": PROFILES["profiles"]})
        self.assertEqual(fleet.resolve_profile("codex", None), ("none", {}))

    def test_unknown_profile_is_refused_not_defaulted(self):
        self.write(PROFILES)
        with self.assertRaises(SystemExit) as ctx:
            fleet.resolve_profile("codex", "wrok")
        self.assertIn("unknown profile 'wrok'", str(ctx.exception))

    def test_explicit_profile_missing_the_cli_is_refused(self):
        self.write(PROFILES)
        with self.assertRaises(SystemExit) as ctx:
            fleet.resolve_profile("claude", "work")
        self.assertIn("does not define CLI 'claude'", str(ctx.exception))

    def test_invalid_files_are_refused(self):
        bad = {
            "undefined default": {"default": "ghost", "profiles": {}},
            "reserved name": {"profiles": {"none": {"codex": {"CODEX_HOME": "x"}}}},
            "unknown cli": {"profiles": {"work": {"gemini": {"X": "y"}}}},
            "herdr env": {"profiles": {"work": {"codex": {"HERDR_SOCKET_PATH": "/x"}}}},
            "bad env name": {"profiles": {"work": {"codex": {"A-B": "x"}}}},
            "multiline value": {"profiles": {"work": {"codex": {"CODEX_HOME": "a\nb"}}}},
            "empty env": {"profiles": {"work": {"codex": {}}}},
            "agy without account home": {"profiles": {"work": {"agy": {"X": "y"}}}},
            "agy ignored data dir": {"profiles": {"work": {"agy": {
                "AGY_ACCOUNT_HOME": "/a", "ANTIGRAVITY_APP_DATA_DIR": "/a/.gemini/antigravity-cli"}}}},
            "agy runner-owned env": {"profiles": {"work": {"agy": {
                "AGY_ACCOUNT_HOME": "/a", "ZDOTDIR": "/z"}}}},
        }
        for label, data in bad.items():
            with self.subTest(label):
                self.write(data)
                with self.assertRaises(SystemExit):
                    fleet.resolve_profile("codex", None)


class SpawnProfileTests(ProfileFileCase):
    def _spawn(self, cli, profile, real_agy_allowlist=False):
        herdr_calls, submitted = [], []

        def fake_herdr(argv, session=None, check=False):
            herdr_calls.append(argv)
            if argv[:2] == ["pane", "list"]:
                return {"result": {"panes": [
                    {"pane_id": "lead-pane", "tab_id": "lead-tab", "workspace_id": "ws-h"},
                    {"pane_id": "p1", "tab_id": "tab-1", "workspace_id": "ws-h"},
                ]}}
            if argv[:2] == ["tab", "create"]:
                return {"result": {"tab": {"tab_id": "tab-1"}, "root_pane": {"pane_id": "p1"}}}
            if argv[:2] == ["agent", "start"]:
                return {"result": {"agent": {"name": "w1"}}}
            if argv[:2] == ["agent", "prompt"]:
                submitted.append(argv[3])
                return {"result": {"type": "agent_prompted"}}
            if argv[:2] == ["agent", "read"]:
                return {"result": {"read": {"text": " ".join(submitted) or "> "}}}
            if argv[:2] in (["agent", "send"], ["agent", "wait"], ["pane", "send-keys"]):
                return {"result": "ok"}
            raise AssertionError(f"unexpected Herdr call: {argv}")

        found = [False]

        def fake_find_agent(name, session=None):
            if not found[0]:
                found[0] = True
                return None
            return {"pane_id": "p1", "name": name, "agent_status": "working",
                    "interactive_ready": True}

        args = argparse.Namespace(
            name="w1", cli=cli, cwd=str(self.dir), task="inspect the repo",
            group=None, resource=[], read_only=True, lock_timeout=0,
            approval_mode="plan", session=None, quiet=True, profile=profile,
        )
        state = self.dir / ".fleet-state"
        with mock.patch.dict(os.environ, {"HERDR_PANE_ID": "lead-pane"}), \
             mock.patch.object(fleet, "STATE_DIR", state), \
             mock.patch.object(fleet, "herdr", side_effect=fake_herdr), \
             mock.patch.object(fleet, "find_agent", side_effect=fake_find_agent), \
             (contextlib.nullcontext() if real_agy_allowlist else
              mock.patch.object(fleet, "install_agy_allowlist", return_value={})):
            result = fleet.cmd_spawn(args)
            assignment = fleet.get_assignment("w1", None)
        tab_call = next(c for c in herdr_calls if c[:2] == ["tab", "create"])
        envs = [tab_call[i + 1] for i, v in enumerate(tab_call) if v == "--env"]
        return result, assignment, envs

    def test_spawn_launches_the_worker_under_the_profile_environment(self):
        self.write(PROFILES)
        result, assignment, envs = self._spawn("codex", "personal")
        self.assertEqual(envs, [f"CODEX_HOME={self.home}/.codex-personal"])
        self.assertEqual(result["profile"], "personal")
        self.assertEqual(assignment["profile"], "personal")

    def test_spawn_without_profile_uses_the_default(self):
        self.write(PROFILES)
        result, _, envs = self._spawn("codex", None)
        self.assertEqual(envs, [f"CODEX_HOME={self.home}/.codex-work"])
        self.assertEqual(result["profile"], "work")

    def test_spawn_without_profiles_file_is_unchanged(self):
        result, assignment, envs = self._spawn("codex", None)
        self.assertEqual(envs, [])
        self.assertEqual(result["profile"], "none")
        self.assertEqual(assignment["profile"], "none")

    def test_agy_profile_routes_the_tab_through_the_account_wrapper(self):
        account = self.dir / "agy-personal"
        self.log_in_agy(account)
        self.write({"profiles": {"personal": {"agy": {"AGY_ACCOUNT_HOME": str(account)}}}})
        _, _, envs = self._spawn("agy", "personal")
        self.assertEqual(envs, [f"AGY_ACCOUNT_HOME={account}",
                                f"ZDOTDIR={fleet.AGY_PROFILE_DIR / 'zdotdir'}",
                                f"AGY_SHIM_DIR={fleet.AGY_PROFILE_DIR / 'bin'}"])

    def test_agy_allowlist_goes_to_the_profile_account(self):
        account = self.dir / "agy-personal"
        self.log_in_agy(account)
        settings = account / ".gemini" / "antigravity-cli" / "settings.json"
        default_settings = self.dir / "default-agy" / "settings.json"
        self.write({"profiles": {"personal": {"agy": {"AGY_ACCOUNT_HOME": str(account)}}}})
        with mock.patch.object(fleet.agy_permissions_module(), "DEFAULT_SETTINGS_PATH", default_settings):
            result, _, _ = self._spawn("agy", "personal", real_agy_allowlist=True)
        self.assertEqual(result["agy_allowlist"]["settings_path"], str(settings))
        allow = json.loads(settings.read_text())["permissions"]["allow"]
        self.assertIn("command(ls)", allow)
        self.assertFalse(default_settings.exists(), "default AGY account must not be touched")

    def test_spawn_with_unknown_profile_creates_nothing(self):
        self.write(PROFILES)
        with mock.patch.object(fleet, "herdr", side_effect=AssertionError("herdr called")):
            args = argparse.Namespace(
                name="w1", cli="codex", cwd=str(self.dir), task=None, group=None,
                resource=[], read_only=True, lock_timeout=0, approval_mode="plan",
                session=None, quiet=True, profile="ghost")
            with self.assertRaises(SystemExit):
                fleet.cmd_spawn(args)


class ResetProfileTests(ProfileFileCase):
    def _reset(self, old, profile):
        args = argparse.Namespace(
            agent="w1", cli="codex", cwd="/tmp", task="next task", handoff="facts",
            old_group=None, group=None, resource=[], read_only=True, lock_timeout=0,
            approval_mode="plan", session=None, workspace_id="ws-h", profile=profile,
        )
        with mock.patch.object(fleet, "get_assignment", return_value=old), \
             mock.patch.object(fleet, "acquire_assignment", return_value=[]), \
             mock.patch.object(fleet, "find_agent", return_value=None), \
             mock.patch.object(fleet, "cmd_spawn", side_effect=lambda a: {"profile": a.profile}):
            return fleet.cmd_reset(args)

    def test_reset_keeps_the_previous_account(self):
        self.write(PROFILES)
        self.assertEqual(self._reset({"profile": "personal"}, None), {"profile": "personal"})

    def test_reset_can_switch_account(self):
        self.write(PROFILES)
        self.assertEqual(self._reset({"profile": "personal"}, "client-x"), {"profile": "client-x"})

    def test_reset_refuses_before_closing_when_the_old_profile_was_removed(self):
        self.write(PROFILES)
        with self.assertRaises(SystemExit):
            self._reset({"profile": "retired"}, None)


class ProfileArgparseTests(unittest.TestCase):
    def test_spawn_and_reset_expose_profile(self):
        for cmd in ("spawn", "reset"):
            with self.subTest(cmd):
                out = subprocess.run([sys.executable, str(ROOT / "scripts" / "fleet.py"), cmd, "--help"],
                                     capture_output=True, text=True, check=True).stdout
                self.assertIn("--profile", out)


if __name__ == "__main__":
    unittest.main()
