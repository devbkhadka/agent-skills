"""Unit tests for AGY permissions allowlist installer module.

Ensures safe installation, idempotency, backup numbering, error handling,
permission modes, and clean takeback of managed entries.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import agy_permissions
from agy_permissions import (
    AgyPermissionsError,
    DEFAULT_SETTINGS_PATH,
    MARKER_KEY,
    main as agy_main,
    read_allowlist,
    remove_allowlist,
    sync_allowlist,
)


class TestAgyPermissions(unittest.TestCase):
    """Test suite for agy_permissions module."""

    def test_constants_and_types(self):
        """DEFAULT_SETTINGS_PATH is a Path ending in settings.json and MARKER_KEY is defined."""
        self.assertIsInstance(DEFAULT_SETTINGS_PATH, Path)
        self.assertEqual(DEFAULT_SETTINGS_PATH.name, "settings.json")
        self.assertEqual(MARKER_KEY, "_terminalAgentRunnerManaged")
        self.assertTrue(issubclass(AgyPermissionsError, Exception))

    def test_creation_from_missing_file(self):
        """Syncing into a non-existent settings file creates it with permissions and marker."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            self.assertFalse(settings_path.exists())

            result = sync_allowlist(["git status", "ls"], settings_path=settings_path)

            self.assertTrue(settings_path.exists())
            self.assertEqual(result["settings_path"], str(settings_path))
            self.assertEqual(result["added"], ["command(git status)", "command(ls)"])
            self.assertEqual(result["already_present"], [])
            self.assertEqual(result["total"], 2)

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data["permissions"]["allow"], ["command(git status)", "command(ls)"])
            self.assertEqual(data[MARKER_KEY], ["command(git status)", "command(ls)"])

            # No backup should be created since the file did not exist before
            backups = list(Path(tmp_dir).glob("*.bak-*"))
            self.assertEqual(backups, [])

    def test_owner_only_mode_on_newly_created_file_and_directory(self):
        """Newly created files and directories must have owner-only permissions (0o600 and 0o700)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            nested_dir = Path(tmp_dir) / "sub" / "config"
            settings_path = nested_dir / "settings.json"

            sync_allowlist(["git status"], settings_path=settings_path)

            self.assertTrue(settings_path.exists())
            file_mode = stat.S_IMODE(settings_path.stat().st_mode)
            dir_mode = stat.S_IMODE(nested_dir.stat().st_mode)

            self.assertEqual(file_mode, 0o600)
            self.assertEqual(dir_mode, 0o700)

    def test_preserve_existing_file_mode(self):
        """Existing file mode must be preserved when modified."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            os.chmod(settings_path, 0o644)

            sync_allowlist(["git status"], settings_path=settings_path)

            current_mode = stat.S_IMODE(settings_path.stat().st_mode)
            self.assertEqual(current_mode, 0o644)

    def test_creation_from_file_with_no_permissions_key(self):
        """Syncing into a file with other settings preserves them and creates permissions."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            initial_data = {
                "theme": "dark",
                "editor.tabSize": 2,
                "nested": {"key": [1, 2, 3]},
            }
            settings_path.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

            result = sync_allowlist(["git status"], settings_path=settings_path)

            self.assertEqual(result["added"], ["command(git status)"])
            self.assertEqual(result["total"], 1)

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data["theme"], "dark")
            self.assertEqual(data["editor.tabSize"], 2)
            self.assertEqual(data["nested"], {"key": [1, 2, 3]})
            self.assertEqual(data["permissions"]["allow"], ["command(git status)"])
            self.assertEqual(data[MARKER_KEY], ["command(git status)"])

            # A backup of the original file was created before modification
            bak1 = Path(tmp_dir) / "settings.json.bak-1"
            self.assertTrue(bak1.exists())
            self.assertEqual(json.loads(bak1.read_text(encoding="utf-8")), initial_data)

    def test_merge_preserves_unrelated_key_and_human_added_allow_entry(self):
        """Human authorizations and other keys must survive alongside merged entries."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            initial_data = {
                "customFlag": True,
                "permissions": {
                    "allow": ["command(cargo build)", "command(git status)"],
                },
            }
            settings_path.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

            result = sync_allowlist(["git status", "pytest"], settings_path=settings_path)

            self.assertEqual(result["added"], ["command(pytest)"])
            self.assertEqual(result["already_present"], ["command(git status)"])
            self.assertEqual(result["total"], 3)

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertTrue(data["customFlag"])
            self.assertEqual(
                data["permissions"]["allow"],
                ["command(cargo build)", "command(git status)", "command(pytest)"],
            )
            # MARKER_KEY should only track what we added, not what was already present
            self.assertEqual(data[MARKER_KEY], ["command(pytest)"])

    def test_idempotency_across_two_syncs(self):
        """Running sync twice leaves exactly one copy of each entry and reports second run as already present."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"

            res1 = sync_allowlist(["git status", "ls"], settings_path=settings_path)
            self.assertEqual(res1["added"], ["command(git status)", "command(ls)"])
            self.assertEqual(res1["already_present"], [])
            self.assertEqual(res1["total"], 2)

            res2 = sync_allowlist(["git status", "ls"], settings_path=settings_path)
            self.assertEqual(res2["added"], [])
            self.assertEqual(res2["already_present"], ["command(git status)", "command(ls)"])
            self.assertEqual(res2["total"], 2)

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data["permissions"]["allow"], ["command(git status)", "command(ls)"])
            self.assertEqual(data[MARKER_KEY], ["command(git status)", "command(ls)"])

            # Second identical run is a no-op and should not create unnecessary backup
            backups = list(Path(tmp_dir).glob("*.bak-*"))
            self.assertEqual(len(backups), 0)

    def test_second_sync_produces_second_numbered_backup_rather_than_overwriting_first(self):
        """Consecutive modifications produce incremented backup file names without overwriting."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            settings_path.write_text(json.dumps({"initial": 1}), encoding="utf-8")

            sync_allowlist(["git status"], settings_path=settings_path)
            bak1 = Path(tmp_dir) / "settings.json.bak-1"
            self.assertTrue(bak1.exists())
            self.assertEqual(json.loads(bak1.read_text(encoding="utf-8")), {"initial": 1})

            sync_allowlist(["ls"], settings_path=settings_path)
            bak2 = Path(tmp_dir) / "settings.json.bak-2"
            self.assertTrue(bak2.exists())
            self.assertEqual(
                json.loads(bak2.read_text(encoding="utf-8"))["permissions"]["allow"],
                ["command(git status)"],
            )

            # Final file contains both
            current = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                current["permissions"]["allow"],
                ["command(git status)", "command(ls)"],
            )

    def test_removal_takes_back_only_marked_entries_and_leaves_human_entry(self):
        """remove_allowlist removes only managed entries, deletes MARKER_KEY, and leaves human entries."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            initial_data = {
                "userSetting": "custom",
                "permissions": {
                    "allow": ["command(human command)"],
                },
            }
            settings_path.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

            sync_allowlist(["git status", "ls"], settings_path=settings_path)
            data_after_sync = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertIn(MARKER_KEY, data_after_sync)
            self.assertEqual(len(data_after_sync["permissions"]["allow"]), 3)

            remove_res = remove_allowlist(settings_path=settings_path)
            self.assertEqual(
                sorted(remove_res["removed"]),
                ["command(git status)", "command(ls)"],
            )

            final_data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertNotIn(MARKER_KEY, final_data)
            self.assertEqual(final_data["permissions"]["allow"], ["command(human command)"])
            self.assertEqual(final_data["userSetting"], "custom")

    def test_removal_with_no_marker_present(self):
        """remove_allowlist on a file with no marker returns empty removed list and does not error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            settings_path.write_text(
                json.dumps({"permissions": {"allow": ["command(git status)"]}}),
                encoding="utf-8",
            )

            res = remove_allowlist(settings_path=settings_path)
            self.assertEqual(res, {"settings_path": str(settings_path), "removed": []})

            # File should be completely untouched
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data["permissions"]["allow"], ["command(git status)"])

    def test_removal_on_non_existent_file(self):
        """remove_allowlist on a non-existent file returns empty removed list."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "non_existent.json"
            res = remove_allowlist(settings_path=settings_path)
            self.assertEqual(res, {"settings_path": str(settings_path), "removed": []})

    def test_read_allowlist(self):
        """read_allowlist returns permissions.allow or empty list when absent."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"

            # Non-existent file
            self.assertEqual(read_allowlist(settings_path=settings_path), [])

            # File with empty object
            settings_path.write_text("{}", encoding="utf-8")
            self.assertEqual(read_allowlist(settings_path=settings_path), [])

            # File with permissions object but no allow
            settings_path.write_text(json.dumps({"permissions": {}}), encoding="utf-8")
            self.assertEqual(read_allowlist(settings_path=settings_path), [])

            # File with allow array
            settings_path.write_text(
                json.dumps({"permissions": {"allow": ["command(git)", "command(ls)"]}}),
                encoding="utf-8",
            )
            self.assertEqual(
                read_allowlist(settings_path=settings_path),
                ["command(git)", "command(ls)"],
            )

    def test_error_invalid_json(self):
        """AgyPermissionsError is raised when file contains invalid JSON."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            settings_path.write_text("{ invalid json", encoding="utf-8")

            with self.assertRaises(AgyPermissionsError):
                sync_allowlist(["git status"], settings_path=settings_path)

            with self.assertRaises(AgyPermissionsError):
                remove_allowlist(settings_path=settings_path)

            with self.assertRaises(AgyPermissionsError):
                read_allowlist(settings_path=settings_path)

    def test_error_top_level_not_an_object(self):
        """AgyPermissionsError is raised when top-level JSON is not an object."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            for invalid_top in (["a", "b"], "string", 123, True, None):
                settings_path.write_text(json.dumps(invalid_top), encoding="utf-8")
                with self.assertRaises(AgyPermissionsError):
                    sync_allowlist(["git status"], settings_path=settings_path)
                with self.assertRaises(AgyPermissionsError):
                    remove_allowlist(settings_path=settings_path)
                with self.assertRaises(AgyPermissionsError):
                    read_allowlist(settings_path=settings_path)

    def test_error_permissions_exists_and_is_not_an_object(self):
        """AgyPermissionsError is raised when permissions key is not an object."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            for invalid_perms in ("not_an_object", [1, 2], 123, None):
                settings_path.write_text(
                    json.dumps({"permissions": invalid_perms}), encoding="utf-8"
                )
                with self.assertRaises(AgyPermissionsError):
                    sync_allowlist(["git status"], settings_path=settings_path)
                with self.assertRaises(AgyPermissionsError):
                    remove_allowlist(settings_path=settings_path)
                with self.assertRaises(AgyPermissionsError):
                    read_allowlist(settings_path=settings_path)

    def test_error_permissions_allow_exists_and_is_not_an_array(self):
        """AgyPermissionsError is raised when permissions.allow is not an array."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            for invalid_allow in ("not_an_array", {"cmd": "git"}, 123, None):
                settings_path.write_text(
                    json.dumps({"permissions": {"allow": invalid_allow}}), encoding="utf-8"
                )
                with self.assertRaises(AgyPermissionsError):
                    sync_allowlist(["git status"], settings_path=settings_path)
                with self.assertRaises(AgyPermissionsError):
                    remove_allowlist(settings_path=settings_path)
                with self.assertRaises(AgyPermissionsError):
                    read_allowlist(settings_path=settings_path)

    def test_error_marker_key_exists_and_is_not_an_array_of_strings(self):
        """AgyPermissionsError is raised when MARKER_KEY is not an array of strings."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            for invalid_marker in ("not_an_array", [1, 2], ["valid", None], [{"cmd": "git"}], 123):
                settings_path.write_text(
                    json.dumps({MARKER_KEY: invalid_marker}), encoding="utf-8"
                )
                with self.assertRaises(AgyPermissionsError):
                    sync_allowlist(["git status"], settings_path=settings_path)
                with self.assertRaises(AgyPermissionsError):
                    remove_allowlist(settings_path=settings_path)
                with self.assertRaises(AgyPermissionsError):
                    read_allowlist(settings_path=settings_path)

    def test_error_prefix_is_not_non_empty_string(self):
        """AgyPermissionsError is raised when any prefix is not a non-empty string."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            for invalid_prefix in ("", None, 123, [], {}, True):
                with self.assertRaises(AgyPermissionsError):
                    sync_allowlist([invalid_prefix], settings_path=settings_path)  # type: ignore

            # If prefixes itself is not iterable of strings (e.g. single string or None)
            with self.assertRaises(AgyPermissionsError):
                sync_allowlist("git status", settings_path=settings_path)  # type: ignore
            with self.assertRaises(AgyPermissionsError):
                sync_allowlist(None, settings_path=settings_path)  # type: ignore

    def test_prefix_containing_shell_syntax_is_refused(self):
        """AgyPermissionsError is raised for prefixes containing forbidden shell characters."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            forbidden_chars = [";", "|", "&", ">", "<", "`", "$", "(", ")", "\n", "\r"]
            for ch in forbidden_chars:
                prefix = f"git status {ch} dangerous"
                with self.subTest(char=repr(ch)):
                    with self.assertRaises(AgyPermissionsError):
                        sync_allowlist([prefix], settings_path=settings_path)

            # Combined attack patterns
            for dangerous in (
                "git status; rm -rf /",
                "git status | bash",
                "echo hello > /tmp/pwned",
                "git status &",
                "echo $(whoami)",
                "echo `id`",
                "git status\nrm -rf /",
            ):
                with self.subTest(dangerous=dangerous):
                    with self.assertRaises(AgyPermissionsError):
                        sync_allowlist([dangerous], settings_path=settings_path)

    def test_cli_interface(self):
        """Command-line interface supports sync, show, and remove with JSON output."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"

            # sync via CLI
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = agy_main([
                    "sync",
                    "--prefix",
                    "git status",
                    "--prefix",
                    "ls",
                    "--settings",
                    str(settings_path),
                ])
            self.assertEqual(exit_code, 0)
            sync_out = json.loads(stdout_buf.getvalue())
            self.assertEqual(sync_out["added"], ["command(git status)", "command(ls)"])
            self.assertEqual(sync_out["total"], 2)

            # show via CLI
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = agy_main(["show", "--settings", str(settings_path)])
            self.assertEqual(exit_code, 0)
            show_out = json.loads(stdout_buf.getvalue())
            self.assertEqual(show_out, ["command(git status)", "command(ls)"])

            # remove via CLI
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = agy_main(["remove", "--settings", str(settings_path)])
            self.assertEqual(exit_code, 0)
            remove_out = json.loads(stdout_buf.getvalue())
            self.assertEqual(
                sorted(remove_out["removed"]),
                ["command(git status)", "command(ls)"],
            )

            # show after remove
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = agy_main(["show", "--settings", str(settings_path)])
            self.assertEqual(exit_code, 0)
            show_after = json.loads(stdout_buf.getvalue())
            self.assertEqual(show_after, [])

    def test_cli_error_handling(self):
        """CLI prints error message to stderr and returns exit code 1 on AgyPermissionsError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            settings_path.write_text("{ corrupt json", encoding="utf-8")

            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                exit_code = agy_main(["show", "--settings", str(settings_path)])
            self.assertEqual(exit_code, 1)
            self.assertIn("Error:", stderr_buf.getvalue())

    def test_expand_cwd_placeholder(self):
        """<cwd> in prefix is expanded to the provided cwd path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            workspace_dir = Path(tmp_dir) / "workspace"
            workspace_dir.mkdir()
            script_file = workspace_dir / "tool.py"
            script_file.write_text("# tool", encoding="utf-8")

            result = sync_allowlist(
                ["python3 <cwd>/tool.py"],
                settings_path=settings_path,
                cwd=workspace_dir,
            )
            expected_cmd = f"command(python3 {workspace_dir.resolve()}/tool.py)"
            self.assertEqual(result["added"], [expected_cmd])

    def test_expand_directory_suffix(self):
        """Prefix ending in / expands to all files in that directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            target_dir = Path(tmp_dir) / "bin"
            target_dir.mkdir()
            (target_dir / "b_cmd").write_text("bin", encoding="utf-8")
            (target_dir / "a_cmd").write_text("bin", encoding="utf-8")
            (target_dir / "subdir").mkdir()  # Should not be included as it is a directory

            result = sync_allowlist(
                [f"{target_dir}/"],
                settings_path=settings_path,
            )
            expected = [
                f"command({target_dir}/a_cmd)",
                f"command({target_dir}/b_cmd)",
            ]
            self.assertEqual(result["added"], expected)

    def test_expand_glob_pattern(self):
        """Prefix with glob wildcards expands to matching files."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            target_dir = Path(tmp_dir) / "scripts"
            target_dir.mkdir()
            (target_dir / "test1.py").write_text("py", encoding="utf-8")
            (target_dir / "test2.py").write_text("py", encoding="utf-8")
            (target_dir / "readme.txt").write_text("txt", encoding="utf-8")

            result = sync_allowlist(
                [f"python3 {target_dir}/*.py"],
                settings_path=settings_path,
            )
            expected = [
                f"command(python3 {target_dir}/test1.py)",
                f"command(python3 {target_dir}/test2.py)",
            ]
            self.assertEqual(result["added"], expected)

    def test_cli_with_cwd(self):
        """CLI sync passes --cwd to prefix expansion."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.json"
            workspace_dir = Path(tmp_dir) / "ws"
            workspace_dir.mkdir()
            (workspace_dir / "run.sh").write_text("sh", encoding="utf-8")

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = agy_main([
                    "sync",
                    "--prefix",
                    "bash <cwd>/run.sh",
                    "--cwd",
                    str(workspace_dir),
                    "--settings",
                    str(settings_path),
                ])
            self.assertEqual(exit_code, 0)
            sync_out = json.loads(stdout_buf.getvalue())
            self.assertEqual(
                sync_out["added"],
                [f"command(bash {workspace_dir.resolve()}/run.sh)"],
            )


if __name__ == "__main__":
    unittest.main()

