"""AGY permissions allowlist installer and manager.

Safely installs and removes command-prefix authorizations in AGY settings.json
with atomic writes, numbered backups, and marker tracking.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any, Iterable, Sequence


_home = os.environ.get("HOME")
if _home:
    DEFAULT_SETTINGS_PATH = Path(_home) / ".gemini" / "antigravity-cli" / "settings.json"
else:
    DEFAULT_SETTINGS_PATH = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"

MARKER_KEY = "_terminalAgentRunnerManaged"

FORBIDDEN_PREFIX_CHARS = set(";\n\r|&><`$()")


class AgyPermissionsError(Exception):
    """Raised when AGY settings cannot be parsed, validated, or modified safely."""


def _expand_single_prefix(p: str, cwd_path: Path) -> list[str]:
    """Expand a single prefix string which may contain <cwd>, directory suffix /, or glob patterns."""
    if not isinstance(p, str) or len(p) == 0:
        raise AgyPermissionsError(f"Command prefix must be a non-empty string, got {p!r}")

    # 1. Expand <cwd> placeholder if present
    expanded_p = p.replace("<cwd>", str(cwd_path))

    # 2. Check for forbidden shell characters in the expanded prefix string
    # Note: * and ? and [ and ] are allowed glob characters not in FORBIDDEN_PREFIX_CHARS
    for ch in expanded_p:
        if ch in FORBIDDEN_PREFIX_CHARS:
            raise AgyPermissionsError(
                f"Command prefix {p!r} contains forbidden shell character {ch!r}"
            )

    # 3. Check if there is a command prefix before a directory or glob (e.g. "python3 <cwd>/scripts/*.py")
    head = ""
    target = expanded_p
    if " " in expanded_p:
        parts = expanded_p.rsplit(" ", 1)
        candidate_target = parts[1]
        if candidate_target.endswith("/") or any(c in candidate_target for c in ("*", "?", "[", "]")):
            head, target = parts[0], candidate_target

    # 4. Expand directory ending with /
    if target.endswith("/"):
        d = Path(target)
        if d.is_dir():
            files = sorted(f for f in d.iterdir() if f.is_file())
            tokens = []
            for f in files:
                rel_f = f"{target.rstrip('/')}/{f.name}" if not target.startswith("/") else str(f)
                token = f"{head} {rel_f}" if head else rel_f
                tokens.append(token)
            return tokens
        return []

    # 5. Expand glob pattern
    if any(c in target for c in ("*", "?", "[", "]")):
        matches = sorted(glob.glob(target, recursive=True))
        files = [m for m in matches if os.path.isfile(m)]
        tokens = []
        for m in files:
            token = f"{head} {m}" if head else m
            tokens.append(token)
        return tokens

    # 6. Plain command prefix
    return [expanded_p]


def _validate_and_expand_prefixes(
    prefixes: Iterable[str], cwd: str | Path | None = None
) -> list[str]:
    """Validate that prefixes is an iterable of valid command prefixes and expand dirs/globs."""
    if isinstance(prefixes, (str, bytes)) or not hasattr(prefixes, "__iter__"):
        raise AgyPermissionsError(
            f"prefixes must be an iterable of strings, got {type(prefixes).__name__}"
        )

    cwd_path = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()

    validated: list[str] = []
    for p in prefixes:
        expanded_tokens = _expand_single_prefix(p, cwd_path)
        for token in expanded_tokens:
            if not isinstance(token, str) or len(token) == 0:
                raise AgyPermissionsError(f"Command prefix must be a non-empty string, got {token!r}")
            for ch in token:
                if ch in FORBIDDEN_PREFIX_CHARS:
                    raise AgyPermissionsError(
                        f"Command prefix {token!r} contains forbidden shell character {ch!r}"
                    )
            validated.append(token)
    return validated


def _validate_prefixes(
    prefixes: Iterable[str], cwd: str | Path | None = None
) -> list[str]:
    """Validate that prefixes is an iterable of valid command prefix strings."""
    return _validate_and_expand_prefixes(prefixes, cwd=cwd)


def _load_and_validate_settings(settings_path: Path) -> tuple[dict[str, Any], int | None, bool]:
    """Load settings file and validate structure.

    Returns:
        (data, existing_mode, file_existed)
    """
    if not settings_path.exists():
        return {}, None, False

    try:
        st = settings_path.stat()
        existing_mode = stat.S_IMODE(st.st_mode)
    except OSError as e:
        raise AgyPermissionsError(f"Cannot access settings file {settings_path}: {e}") from e

    try:
        raw = settings_path.read_text(encoding="utf-8")
    except OSError as e:
        raise AgyPermissionsError(f"Cannot read settings file {settings_path}: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AgyPermissionsError(f"Invalid JSON in settings file {settings_path}: {e}") from e

    if not isinstance(data, dict):
        raise AgyPermissionsError(
            f"Top-level JSON in {settings_path} must be an object, got {type(data).__name__}"
        )

    if "permissions" in data:
        perms = data["permissions"]
        if not isinstance(perms, dict):
            raise AgyPermissionsError(
                f"'permissions' in {settings_path} must be an object, got {type(perms).__name__}"
            )
        if "allow" in perms:
            allow = perms["allow"]
            if not isinstance(allow, list):
                raise AgyPermissionsError(
                    f"'permissions.allow' in {settings_path} must be an array, got {type(allow).__name__}"
                )

    if MARKER_KEY in data:
        marker = data[MARKER_KEY]
        if not isinstance(marker, list) or not all(isinstance(item, str) for item in marker):
            raise AgyPermissionsError(
                f"'{MARKER_KEY}' in {settings_path} must be an array of strings"
            )

    return data, existing_mode, True


def _create_backup(target_path: Path) -> Path | None:
    """Create a numbered backup <target_path.name>.bak-<n> where n is lowest available integer."""
    if not target_path.exists():
        return None

    n = 1
    while True:
        backup_path = target_path.parent / f"{target_path.name}.bak-{n}"
        if not backup_path.exists():
            break
        n += 1

    try:
        shutil.copy2(target_path, backup_path)
    except OSError as e:
        raise AgyPermissionsError(f"Failed to create backup {backup_path}: {e}") from e

    return backup_path


def _atomic_write_json(
    target_path: Path,
    data: dict[str, Any],
    existing_mode: int | None,
    file_existed: bool,
) -> None:
    """Atomically write data to target_path, creating backup if file existed and preserving mode."""
    target_path = Path(target_path)
    parent = target_path.parent

    if not parent.exists():
        try:
            parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(parent, 0o700)
        except OSError as e:
            raise AgyPermissionsError(f"Failed to create directory {parent}: {e}") from e

    if file_existed and target_path.exists():
        _create_backup(target_path)

    target_mode = existing_mode if existing_mode is not None else 0o600

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{target_path.name}.tmp-",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            os.chmod(temp_path, target_mode)
            json.dump(data, temp_file, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, target_path)
        os.chmod(target_path, target_mode)
    except Exception as e:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        if isinstance(e, AgyPermissionsError):
            raise
        raise AgyPermissionsError(
            f"Failed to write settings atomically to {target_path}: {e}"
        ) from e


def sync_allowlist(
    prefixes: Iterable[str],
    settings_path: str | Path | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Sync command prefixes into permissions.allow and track managed entries in MARKER_KEY."""
    target = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    validated = _validate_prefixes(prefixes, cwd=cwd)
    data, existing_mode, file_existed = _load_and_validate_settings(target)

    perms = data.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise AgyPermissionsError(f"'permissions' in {target} must be an object")

    if "allow" not in perms:
        perms["allow"] = []
    allow_list: list[Any] = perms["allow"]

    managed_entries = set(data.get(MARKER_KEY, []))
    added: list[str] = []
    already_present: list[str] = []

    for p in validated:
        entry = f"command({p})"
        if entry in allow_list:
            if entry not in already_present:
                already_present.append(entry)
        else:
            allow_list.append(entry)
            if entry not in added:
                added.append(entry)
            managed_entries.add(entry)

    if added or MARKER_KEY in data:
        data[MARKER_KEY] = sorted(managed_entries)

    # Only write if something was added or if file did not exist before
    if added or not file_existed:
        _atomic_write_json(target, data, existing_mode, file_existed)

    return {
        "settings_path": str(target),
        "added": added,
        "already_present": already_present,
        "total": len(perms["allow"]),
    }


def remove_allowlist(settings_path: str | Path | None = None) -> dict[str, Any]:
    """Remove only the entries managed by terminal-agent-runner and delete MARKER_KEY."""
    target = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    data, existing_mode, file_existed = _load_and_validate_settings(target)

    if not file_existed or MARKER_KEY not in data:
        return {
            "settings_path": str(target),
            "removed": [],
        }

    managed_entries = set(data.get(MARKER_KEY, []))
    perms = data.get("permissions", {})
    allow_list = perms.get("allow", []) if isinstance(perms, dict) else []

    removed: list[str] = []
    if isinstance(allow_list, list):
        new_allow = []
        for item in allow_list:
            if item in managed_entries:
                removed.append(item)
            else:
                new_allow.append(item)
        if isinstance(perms, dict) and "allow" in perms:
            perms["allow"] = new_allow

    del data[MARKER_KEY]

    _atomic_write_json(target, data, existing_mode, file_existed)

    return {
        "settings_path": str(target),
        "removed": removed,
    }


def read_allowlist(settings_path: str | Path | None = None) -> list[str]:
    """Return the current permissions.allow array, or an empty list if absent."""
    target = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    data, _, file_existed = _load_and_validate_settings(target)

    if not file_existed:
        return []

    perms = data.get("permissions")
    if not isinstance(perms, dict):
        return []

    allow = perms.get("allow")
    if not isinstance(allow, list):
        return []

    return list(allow)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for managing AGY permissions allowlist."""
    parser = argparse.ArgumentParser(
        description="Manage AGY permissions allowlist safely.",
        prog="agy_permissions.py" if argv is None else None,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # sync subcommand
    sync_parser = subparsers.add_parser("sync", help="Add command prefixes to permissions.allow")
    sync_parser.add_argument(
        "--prefix",
        "-p",
        action="append",
        required=True,
        help="Command prefix to allow (repeatable, e.g. --prefix 'git status' --prefix 'ls')",
    )
    sync_parser.add_argument(
        "--cwd",
        default=None,
        help="Workspace directory for <cwd> expansion (defaults to current working directory)",
    )
    sync_parser.add_argument(
        "--settings",
        default=None,
        help="Path to settings.json (defaults to ~/.gemini/antigravity-cli/settings.json)",
    )

    # remove subcommand
    remove_parser = subparsers.add_parser("remove", help="Remove managed command prefixes")
    remove_parser.add_argument(
        "--settings",
        default=None,
        help="Path to settings.json (defaults to ~/.gemini/antigravity-cli/settings.json)",
    )

    # show subcommand
    show_parser = subparsers.add_parser("show", help="Show current permissions.allow array")
    show_parser.add_argument(
        "--settings",
        default=None,
        help="Path to settings.json (defaults to ~/.gemini/antigravity-cli/settings.json)",
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "sync":
            result = sync_allowlist(args.prefix, settings_path=args.settings, cwd=args.cwd)
        elif args.command == "remove":
            result = remove_allowlist(settings_path=args.settings)
        elif args.command == "show":
            result = read_allowlist(settings_path=args.settings)
        else:
            parser.print_help()
            return 1

        print(json.dumps(result, indent=2))
        return 0
    except AgyPermissionsError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
