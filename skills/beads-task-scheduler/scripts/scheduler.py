#!/usr/bin/env python3
"""Beads Task Scheduler - DAG Task Management & Wave Planning Helper.

Thin, standard-library helper over br and bv/bvr for:
- Listing ready beads with validated agent_context
- Ranking candidate tasks across single-repo or multi-repo workspaces
- Performing atomic claims
- Rolling back claims on failure or worker crash
- Validating completion evidence before workflow-gated closing
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

COMMAND_TIMEOUT = 30.0

DEFAULT_LEDGER_FILENAME = "close-ledger.jsonl"

HERDR_AGENT_PREFIX = "bead-"
MAX_HERDR_AGENT_NAME_LENGTH = 32
HERDR_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def validate_bead_id(bead_id: str) -> None:
    """Validate a durable Beads ID without imposing a runner-name limit."""
    if not isinstance(bead_id, str) or not bead_id.strip():
        raise RuntimeError("bead ID must be a non-empty string")


def pane_id_for_issue(issue_id: str, agent_context: Any) -> str:
    """Return the Herdr worker name for a bead, validating its durable mapping.

    ``herder.pane_id`` is the complete Herdr agent name, not a suffix.  A short
    durable ID keeps the historical ``bead-<id>`` default; a longer one must opt
    into an explicit mapping before it can be claimed or dispatched.
    """
    validate_bead_id(issue_id)
    context = json.loads(agent_context) if isinstance(agent_context, str) and agent_context.strip() else (agent_context or {})
    if not isinstance(context, dict):
        raise RuntimeError("invalid agent_context JSON object for pane mapping")
    herder = context.get("herder", {})
    if herder is None:
        herder = {}
    if not isinstance(herder, dict):
        raise RuntimeError("agent_context.herder must be an object for pane mapping")
    if "pane_id" in herder:
        pane_id = herder["pane_id"]
    elif len(issue_id) + len(HERDR_AGENT_PREFIX) > MAX_HERDR_AGENT_NAME_LENGTH:
        # Suggestion sug_20260902_074208_0b01d9:
        # Derive a legal short pane name deterministically: hash prefix and keep trailing suffix
        h = hashlib.sha256(issue_id.encode("utf-8")).hexdigest()[:6]
        tail = issue_id[-20:]
        derived = f"{HERDR_AGENT_PREFIX}{h}-{tail}".lower()
        derived = re.sub(r"[^a-z0-9\-_]", "-", derived)
        pane_id = derived[:MAX_HERDR_AGENT_NAME_LENGTH]
    else:
        pane_id = f"{HERDR_AGENT_PREFIX}{issue_id}"
    if (not isinstance(pane_id, str) or not pane_id.startswith(HERDR_AGENT_PREFIX)
            or not HERDR_AGENT_NAME_RE.fullmatch(pane_id)):
        raise RuntimeError(
            "herder.pane_id must be a lowercase Herdr worker name beginning 'bead-' (letters, digits, '-' or '_', 1-32 characters)"
        )
    if len(pane_id) > MAX_HERDR_AGENT_NAME_LENGTH:
        raise RuntimeError("herder.pane_id exceeds Herdr's 32-character worker-name limit")
    return pane_id


def validate_unique_pane_id(issue_id: str, pane_id: str, repo: Path | str) -> None:
    """Refuse a duplicate durable mapping before a claim can create a worker."""
    repo_path = Path(repo).expanduser().resolve()
    listed = run_command_json(["br", "list", "--json"], cwd=repo_path)
    issues = listed if isinstance(listed, list) else listed.get("issues", [])
    if not isinstance(issues, list):
        raise RuntimeError("br list returned invalid issues while checking herder.pane_id")
    duplicates = []
    for item in issues:
        if not isinstance(item, dict) or not item.get("id") or item["id"] == issue_id:
            continue
        try:
            other_pane = pane_id_for_issue(item["id"], item.get("agent_context"))
        except Exception:
            continue
        if other_pane == pane_id:
            duplicates.append(item["id"])
    if duplicates:
        raise RuntimeError(f"herder.pane_id {pane_id!r} is already mapped by {', '.join(sorted(duplicates))}")


def validate_issue_pane_mapping(issue: dict[str, Any], repo: Path | str, *, check_unique: bool = False) -> str:
    """Validate an issue's mapping and optionally its repository-wide uniqueness."""
    issue_id = issue.get("id")
    if not isinstance(issue_id, str):
        raise RuntimeError("issue has no valid id for pane mapping")
    pane_id = pane_id_for_issue(issue_id, issue.get("agent_context"))
    if check_unique:
        validate_unique_pane_id(issue_id, pane_id, repo)
    return pane_id


def create_bead(
    title: str,
    repo: Path | str,
    description: str | None = None,
    agent_context: dict[str, Any] | str | None = None,
    priority: int | None = None,
    issue_type: str | None = None,
    design: str | None = None,
    acceptance_criteria: str | None = None,
    notes: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Create a bead in the repository and enforce the 27-character ID limit."""
    repo_path = Path(repo).expanduser().resolve()
    cmd = ["br", "create", "--title", title, "--json"]
    if description:
        cmd.extend(["--description", description])
    if priority is not None:
        cmd.extend(["--priority", str(priority)])
    if issue_type:
        cmd.extend(["--type", issue_type])
    if actor:
        cmd.extend(["--actor", actor])

    res = run_command_json(cmd, cwd=repo_path)
    created_issue = res[0] if isinstance(res, list) and res else res
    if not isinstance(created_issue, dict) or not created_issue.get("id"):
        raise RuntimeError(f"br create returned unexpected response: {res}")

    created_id = created_issue["id"]
    validate_bead_id(created_id)

    # Set design, acceptance_criteria, notes via br update; fallback to folding into description if unsupported
    update_fields: dict[str, str] = {}
    if design:
        update_fields["design"] = design
    if acceptance_criteria:
        update_fields["acceptance_criteria"] = acceptance_criteria
    if notes:
        update_fields["notes"] = notes

    if update_fields:
        update_cmd = ["br", "update", created_id, "--json"]
        if actor:
            update_cmd.extend(["--actor", actor])
        if "design" in update_fields:
            update_cmd.extend(["--design", update_fields["design"]])
        if "acceptance_criteria" in update_fields:
            update_cmd.extend(["--acceptance-criteria", update_fields["acceptance_criteria"]])
        if "notes" in update_fields:
            update_cmd.extend(["--notes", update_fields["notes"]])
        try:
            run_command_json(update_cmd, cwd=repo_path)
            for k, v in update_fields.items():
                created_issue[k] = v
        except Exception:
            folded_parts = []
            if description:
                folded_parts.append(description)
            if acceptance_criteria:
                folded_parts.append(f"## Acceptance Criteria\n{acceptance_criteria}")
            if design:
                folded_parts.append(f"## Design\n{design}")
            if notes:
                folded_parts.append(f"## Notes\n{notes}")
            folded_desc = "\n\n".join(folded_parts)
            desc_cmd = ["br", "update", created_id, "--description", folded_desc, "--json"]
            if actor:
                desc_cmd.extend(["--actor", actor])
            try:
                run_command_json(desc_cmd, cwd=repo_path)
                created_issue["description"] = folded_desc
            except Exception:
                pass

    if agent_context is not None:
        validated_ctx = parse_agent_context(agent_context, repo_path)
        ctx_str = json.dumps(agent_context) if isinstance(agent_context, dict) else str(agent_context)
        update_cmd = ["br", "update", created_id, "--agent-context", ctx_str, "--json"]
        if actor:
            update_cmd.extend(["--actor", actor])
        run_command_json(update_cmd, cwd=repo_path)
        created_issue["agent_context"] = ctx_str
        created_issue["parsed_context"] = validated_ctx

    return created_issue


def close_ledger_path(repo: Path | str) -> Path:
    """Resolve the default close ledger path for a repository."""
    repo_path = Path(repo).expanduser().resolve()
    return repo_path / ".beads" / DEFAULT_LEDGER_FILENAME


def verification_log_path(repo: Path | str, issue_id: str) -> Path:
    """Resolve the verification log path for an issue in a repository."""
    repo_path = Path(repo).expanduser().resolve()
    return repo_path / ".beads" / "logs" / f"{issue_id}-verification.log"


def clamp_traceback_output(text: str, issue_id: str | None = None) -> str:
    """Clamp output/traceback to top 5 and bottom 10 lines if > 15 lines.

    When clamped, inserts:
    "... [N lines truncated. Full trace: .beads/logs/<issue_id>-verification.log] ..."
    """
    lines = text.splitlines()
    if len(lines) <= 15:
        return text
    target_id = issue_id or "<issue_id>"
    top = lines[:5]
    bottom = lines[-10:]
    n_truncated = len(lines) - 15
    notice = f"... [{n_truncated} lines truncated. Full trace: .beads/logs/{target_id}-verification.log] ..."
    return "\n".join(top + [notice] + bottom)


def log_verification_output(
    repo: Path | str,
    issue_id: str,
    verification_entries: list[dict[str, Any]],
) -> Path:
    """Log full verification command output to .beads/logs/<issue_id>-verification.log."""
    log_file = verification_log_path(repo, issue_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    lines = [f"=== Verification Log for {issue_id} at {timestamp} ==="]
    for idx, v in enumerate(verification_entries, 1):
        if not isinstance(v, dict):
            continue
        cmd = v.get("command", "<unknown>")
        exit_code = v.get("exit_code", "<unknown>")
        lines.append(f"--- [{idx}] Command: {cmd} (exit_code: {exit_code}) ---")
        output = v.get("output") or v.get("stdout") or v.get("stderr") or v.get("traceback") or v.get("error") or ""
        if output:
            lines.append(str(output).rstrip())
        else:
            lines.append("(no output recorded)")
        lines.append("")

    content = "\n".join(lines) + "\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(content)
        f.flush()

    return log_file


def execute_verification(
    commands: list[str] | list[dict[str, Any]],
    repo: Path | str,
    issue_id: str,
    cwd: Path | str | None = None,
    timeout: float = COMMAND_TIMEOUT,
) -> list[dict[str, Any]]:
    """Execute verification commands, logging full output and returning evidence records.

    Raises RuntimeError with clamped failure traceback if any command fails expectations.
    """
    repo_path = Path(repo).expanduser().resolve()
    exec_cwd = Path(cwd).expanduser().resolve() if cwd else repo_path

    results: list[dict[str, Any]] = []
    for item in commands:
        if isinstance(item, str):
            cmd_str = item
            expected_exit = 0
        elif isinstance(item, dict):
            cmd_str = item.get("command", "")
            expected_exit = item.get("expected_exit", 0)
        else:
            continue

        try:
            proc = subprocess.run(
                cmd_str,
                shell=True,
                cwd=str(exec_cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            raw_output = (proc.stdout or "") + (proc.stderr or "")
            entry = {
                "command": cmd_str,
                "exit_code": proc.returncode,
                "expected_exit": expected_exit,
                "output": raw_output,
            }
            results.append(entry)
        except subprocess.TimeoutExpired as err:
            entry = {
                "command": cmd_str,
                "exit_code": 124,
                "expected_exit": expected_exit,
                "output": f"Command timed out after {timeout}s: {err}",
            }
            results.append(entry)
        except Exception as err:
            entry = {
                "command": cmd_str,
                "exit_code": 1,
                "expected_exit": expected_exit,
                "output": f"Execution error: {err}",
            }
            results.append(entry)

    # Log complete verification output to .beads/logs/<issue_id>-verification.log
    log_verification_output(repo_path, issue_id, results)

    # Check for failures
    failed = [r for r in results if r["exit_code"] != r.get("expected_exit", 0)]
    if failed:
        err_lines = []
        for f in failed:
            out = f.get("output", "")
            clamped = clamp_traceback_output(out, issue_id=issue_id)
            err_lines.append(
                f"Command '{f['command']}' failed with exit code {f['exit_code']} "
                f"(expected {f.get('expected_exit', 0)}):\n{clamped}"
            )
        raise RuntimeError("\n\n".join(err_lines))

    return results


def append_close_ledger(
    repo: Path | str,
    record: dict[str, Any],
    ledger_path: Path | str | None = None,
) -> Path:
    """Append a closure record to the repository close ledger in JSON Lines format.

    Uses append mode with a single write call so POSIX atomic appends prevent line
    interleaving across concurrent worker processes.
    """
    target = Path(ledger_path).expanduser().resolve() if ledger_path else close_ledger_path(repo)
    target.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(record, sort_keys=True) + "\n"
    with open(target, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()

    return target


HANDOFF_FIELDS = {
    "status",
    "decisions",
    "changed_files",
    "verification",
    "blockers",
    "next_inputs",
}
HANDOFF_MARKER = "FLEET_HANDOFF:"
TERMINAL_BOUNDARY = re.compile(
    r"^[ \t]*[─━═]{3,}(?:[ \t]+Worked for "
    r"(?:(?:\d+h[ \t]+)?(?:\d+m[ \t]+)?\d+s)"
    r"[ \t]+[─━═]{3,})?[ \t]*$",
    re.MULTILINE,
)


def run_command_json(argv: list[str], cwd: str | Path | None = None) -> Any:
    """Run a CLI command and return parsed JSON stdout."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as err:
        raise RuntimeError(f"{' '.join(argv)} timed out after {COMMAND_TIMEOUT:g}s") from err
    except FileNotFoundError as err:
        raise RuntimeError(f"executable not found on PATH: {argv[0]}") from err

    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"{' '.join(argv)} failed ({proc.returncode}): {err_msg}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"{' '.join(argv)} returned invalid JSON: {err}") from err


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def workspace_base(path: Path) -> Path:
    """Resolve base directory for a workspace config file."""
    if path.parent.name == ".bv":
        return path.parent.parent
    return path.parent


def parse_workspace_repos(workspace_file: str | Path) -> list[Path]:
    """Parse explicit 'repos: - path:' entries from .bv/workspace.yaml."""
    path = Path(workspace_file).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"workspace config not found: {path}")

    repos: list[Path] = []
    in_repos = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            in_repos = stripped == "repos:"
            continue
        if in_repos:
            match = re.match(r"\s*-\s+path:\s*(.+?)\s*$", line)
            if match:
                raw_val = match.group(1).split("#")[0].strip()
                val = Path(_yaml_scalar(raw_val)).expanduser()
                repo_path = (workspace_base(path) / val).resolve() if not val.is_absolute() else val.resolve()
                repos.append(repo_path)

    if not repos:
        raise RuntimeError(
            f"{path} has no explicit 'repos: - path:' entries; add them for deterministic dispatch"
        )

    missing = [str(p) for p in repos if not (p / ".beads").is_dir()]
    if missing:
        raise RuntimeError("workspace repos missing .beads/: " + ", ".join(missing))

    return repos


def find_repos(
    project: str | Path | None,
    workspace: str | Path | None,
    repo_filter: str | None = None,
) -> list[Path]:
    """Determine target repositories from --project or --workspace."""
    if bool(project) == bool(workspace):
        if not project and not workspace:
            # Default to current directory if it has .beads
            cwd = Path.cwd().resolve()
            if (cwd / ".beads").is_dir():
                return [cwd]
        raise RuntimeError("choose exactly one of --project or --workspace")

    if project:
        proj_path = Path(project).expanduser().resolve()
        if not (proj_path / ".beads").is_dir():
            raise RuntimeError(f"project has no .beads workspace: {proj_path}")
        repos = [proj_path]
    else:
        repos = parse_workspace_repos(workspace)  # type: ignore[arg-type]

    if repo_filter:
        repos = [p for p in repos if p.name == repo_filter or str(p) == repo_filter]
        if not repos:
            raise RuntimeError(f"--repo matched no configured repository: {repo_filter}")

    return repos


def viewer_executable() -> str | None:
    """Find bvr or bv executable."""
    return shutil.which("bvr") or shutil.which("bv")


def query_viewer_ranking(workspace: str | Path) -> tuple[list[str], str | None]:
    """Query bvr or bv for parallel track plan."""
    viewer = viewer_executable()
    if not viewer:
        return [], None
    try:
        workspace_path = Path(workspace).expanduser().resolve()
        data = run_command_json(
            [viewer, "--workspace", str(workspace_path), "--robot-plan", "--format", "json"],
            cwd=workspace_base(workspace_path),
        )
        plan = data.get("plan", data) if isinstance(data, dict) else {}
        ids: list[str] = []
        if isinstance(plan, dict):
            if "tracks" in plan and isinstance(plan["tracks"], list):
                for track in plan["tracks"]:
                    for item in track.get("items", []):
                        if isinstance(item, dict) and item.get("id"):
                            ids.append(item["id"])
                        elif isinstance(item, str) and item:
                            ids.append(item)
            elif "phases" in plan and isinstance(plan["phases"], list):
                for phase in plan["phases"]:
                    for issue in phase.get("issues", []):
                        if isinstance(issue, dict) and issue.get("id"):
                            ids.append(issue["id"])
                        elif isinstance(issue, str) and issue:
                            ids.append(issue)
        return ids, Path(viewer).name
    except Exception:
        # Fall back gracefully to per-repo br scheduler
        return [], Path(viewer).name


def parse_agent_context(raw: Any, repo_root: Path) -> dict[str, Any]:
    """Parse and validate agent_context metadata from an issue."""
    if isinstance(raw, str) and raw.strip():
        try:
            context = json.loads(raw)
        except json.JSONDecodeError as err:
            raise RuntimeError(f"invalid agent_context JSON: {err}") from err
    elif isinstance(raw, dict):
        context = raw
    else:
        context = {}

    # Extract executor sub-dict if namespaced under "herder" or "runner", else root
    cfg = context.get("herder", context.get("runner", context)) if isinstance(context, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}

    resources = cfg.get("resources", [])
    if isinstance(resources, str):
        resources = [resources]
    if not isinstance(resources, list) or not all(isinstance(x, str) and x for x in resources):
        raise RuntimeError("resources must be a list of non-empty strings")

    read_only = cfg.get("read_only", False)
    if not isinstance(read_only, bool):
        raise RuntimeError("read_only must be a boolean")

    if not resources and not read_only:
        raise RuntimeError("mutating task must specify non-empty resources (or set read_only: true)")

    cwd_raw = cfg.get("cwd", ".")
    if not isinstance(cwd_raw, (str, os.PathLike)):
        raise RuntimeError("cwd must be a path string")

    task_cwd = Path(cwd_raw).expanduser()
    task_cwd = (repo_root / task_cwd).resolve() if not task_cwd.is_absolute() else task_cwd.resolve()
    try:
        task_cwd.relative_to(repo_root.resolve())
    except ValueError as err:
        raise RuntimeError(f"cwd escapes repository root: {task_cwd}") from err

    top_expected_exit = cfg.get("expected_exit") if "expected_exit" in cfg else (context.get("expected_exit") if isinstance(context, dict) else None)
    if top_expected_exit is not None and (type(top_expected_exit) is not int or isinstance(top_expected_exit, bool)):
        raise RuntimeError("expected_exit must be an integer")

    top_expected_failures = cfg.get("expected_failures") if "expected_failures" in cfg else (context.get("expected_failures") if isinstance(context, dict) else None)
    if top_expected_failures is not None and (type(top_expected_failures) is not int or isinstance(top_expected_failures, bool) or top_expected_failures < 0):
        raise RuntimeError("expected_failures must be a non-negative integer")

    raw_verif = cfg.get("verification")
    if raw_verif is None:
        raise RuntimeError("verification must be a list of non-empty strings or verification objects")

    verif_list: list[str] = []
    verif_specs: list[dict[str, Any]] = []

    if isinstance(raw_verif, str):
        if not raw_verif.strip():
            raise RuntimeError("verification must be a list of non-empty strings")
        cmd_str = raw_verif.strip()
        verif_list.append(cmd_str)
        spec: dict[str, Any] = {"command": cmd_str}
        if top_expected_exit is not None:
            spec["expected_exit"] = top_expected_exit
        if top_expected_failures is not None:
            spec["expected_failures"] = top_expected_failures
        verif_specs.append(spec)

    elif isinstance(raw_verif, dict):
        dict_expected_exit = raw_verif.get("expected_exit", top_expected_exit)
        if dict_expected_exit is not None and (type(dict_expected_exit) is not int or isinstance(dict_expected_exit, bool)):
            raise RuntimeError("expected_exit must be an integer")

        dict_expected_failures = raw_verif.get("expected_failures", top_expected_failures)
        if dict_expected_failures is not None and (type(dict_expected_failures) is not int or isinstance(dict_expected_failures, bool) or dict_expected_failures < 0):
            raise RuntimeError("expected_failures must be a non-negative integer")

        if "commands" in raw_verif or "command" in raw_verif:
            cmds = raw_verif.get("commands", raw_verif.get("command"))
            if isinstance(cmds, str):
                cmds = [cmds]
            if not isinstance(cmds, list) or not cmds:
                raise RuntimeError("verification commands must be a non-empty list")

            for c in cmds:
                if isinstance(c, str):
                    if not c.strip():
                        raise RuntimeError("verification commands must be non-empty strings")
                    cmd_str = c.strip()
                    verif_list.append(cmd_str)
                    spec = {"command": cmd_str}
                    if dict_expected_exit is not None:
                        spec["expected_exit"] = dict_expected_exit
                    if dict_expected_failures is not None:
                        spec["expected_failures"] = dict_expected_failures
                    verif_specs.append(spec)
                elif isinstance(c, dict):
                    cmd_str = c.get("command")
                    if not isinstance(cmd_str, str) or not cmd_str.strip():
                        raise RuntimeError("verification entry must have a non-empty string 'command'")
                    c_ee = c.get("expected_exit", dict_expected_exit)
                    if c_ee is not None and (type(c_ee) is not int or isinstance(c_ee, bool)):
                        raise RuntimeError("expected_exit must be an integer")
                    c_ef = c.get("expected_failures", dict_expected_failures)
                    if c_ef is not None and (type(c_ef) is not int or isinstance(c_ef, bool) or c_ef < 0):
                        raise RuntimeError("expected_failures must be a non-negative integer")
                    verif_list.append(cmd_str.strip())
                    spec = {"command": cmd_str.strip()}
                    if c_ee is not None:
                        spec["expected_exit"] = c_ee
                    if c_ef is not None:
                        spec["expected_failures"] = c_ef
                    verif_specs.append(spec)
                else:
                    raise RuntimeError("verification commands must be strings or objects")
        else:
            for cmd_key, cmd_val in raw_verif.items():
                if not isinstance(cmd_key, str) or not cmd_key.strip():
                    raise RuntimeError("verification dict keys must be non-empty command strings")
                cmd_str = cmd_key.strip()
                verif_list.append(cmd_str)
                spec = {"command": cmd_str}
                if isinstance(cmd_val, int) and not isinstance(cmd_val, bool):
                    spec["expected_exit"] = cmd_val
                    if top_expected_failures is not None:
                        spec["expected_failures"] = top_expected_failures
                elif isinstance(cmd_val, dict):
                    c_ee = cmd_val.get("expected_exit", dict_expected_exit)
                    if c_ee is not None and (type(c_ee) is not int or isinstance(c_ee, bool)):
                        raise RuntimeError("expected_exit must be an integer")
                    c_ef = cmd_val.get("expected_failures", dict_expected_failures)
                    if c_ef is not None and (type(c_ef) is not int or isinstance(c_ef, bool) or c_ef < 0):
                        raise RuntimeError("expected_failures must be a non-negative integer")
                    if c_ee is not None:
                        spec["expected_exit"] = c_ee
                    if c_ef is not None:
                        spec["expected_failures"] = c_ef
                elif cmd_val is None:
                    if dict_expected_exit is not None:
                        spec["expected_exit"] = dict_expected_exit
                    if dict_expected_failures is not None:
                        spec["expected_failures"] = dict_expected_failures
                else:
                    raise RuntimeError("verification dict values must be integer exit codes, spec objects, or null")
                verif_specs.append(spec)

        if not verif_list:
            raise RuntimeError("verification must specify at least one command")

        if dict_expected_exit is not None:
            top_expected_exit = dict_expected_exit
        if dict_expected_failures is not None:
            top_expected_failures = dict_expected_failures

    elif isinstance(raw_verif, list):
        if not raw_verif:
            raise RuntimeError("verification must be a list of non-empty strings")
        for item in raw_verif:
            if isinstance(item, str):
                if not item.strip():
                    raise RuntimeError("verification must be a list of non-empty strings")
                cmd_str = item.strip()
                verif_list.append(cmd_str)
                spec = {"command": cmd_str}
                if top_expected_exit is not None:
                    spec["expected_exit"] = top_expected_exit
                if top_expected_failures is not None:
                    spec["expected_failures"] = top_expected_failures
                verif_specs.append(spec)
            elif isinstance(item, dict):
                cmd_str = item.get("command")
                if not isinstance(cmd_str, str) or not cmd_str.strip():
                    raise RuntimeError("verification entry must have a non-empty string 'command'")
                i_ee = item.get("expected_exit", top_expected_exit)
                if i_ee is not None and (type(i_ee) is not int or isinstance(i_ee, bool)):
                    raise RuntimeError("expected_exit must be an integer")
                i_ef = item.get("expected_failures", top_expected_failures)
                if i_ef is not None and (type(i_ef) is not int or isinstance(i_ef, bool) or i_ef < 0):
                    raise RuntimeError("expected_failures must be a non-negative integer")
                verif_list.append(cmd_str.strip())
                spec = {"command": cmd_str.strip()}
                if i_ee is not None:
                    spec["expected_exit"] = i_ee
                if i_ef is not None:
                    spec["expected_failures"] = i_ef
                verif_specs.append(spec)
            else:
                raise RuntimeError("verification must be a list of non-empty strings or objects")
    else:
        raise RuntimeError("verification must be a list of non-empty strings or verification objects")

    result = copy.deepcopy(cfg)
    result.update(
        {
            "resources": resources,
            "read_only": read_only,
            "cwd": str(task_cwd),
            "verification": verif_list,
            "verification_specs": verif_specs,
        }
    )
    if top_expected_exit is not None:
        result["expected_exit"] = top_expected_exit
    if top_expected_failures is not None:
        result["expected_failures"] = top_expected_failures
    return result


def extract_bead_task_text(issue: dict[str, Any]) -> str:
    """Format full task description and acceptance criteria from issue."""
    parts = [f"Complete Beads task {issue.get('id', '')}: {issue.get('title', '')}"]
    for field, label in (
        ("description", "Objective"),
        ("design", "Design"),
        ("acceptance_criteria", "Acceptance criteria"),
        ("notes", "Notes"),
    ):
        if issue.get(field):
            parts.append(f"{label}:\n{issue[field]}")
    return "\n\n".join(parts)


def compute_chain_depth(
    issue: dict[str, Any],
    repo: Path,
    memo: dict[str, int] | None = None,
    visited: set[str] | None = None,
) -> tuple[int, int]:
    """Compute (chain_depth, blocking_dependent_count) for an issue by traversing blocking downstream dependents."""
    if memo is None:
        memo = {}
    issue_id = issue.get("id", "")
    if issue_id and issue_id in memo:
        blocking = [
            d.get("id")
            for d in issue.get("dependents", [])
            if isinstance(d, dict)
            and d.get("dependency_type") == "blocks"
            and d.get("status") != "closed"
            and d.get("id")
        ]
        return memo[issue_id], len(blocking)

    if visited is None:
        visited = set()
    if issue_id:
        if issue_id in visited:
            return 0, 0
        visited.add(issue_id)

    blocking_deps = [
        d.get("id")
        for d in issue.get("dependents", [])
        if isinstance(d, dict)
        and d.get("dependency_type") == "blocks"
        and d.get("status") != "closed"
        and d.get("id")
    ]
    if not blocking_deps:
        if issue_id:
            memo[issue_id] = 0
        return 0, 0

    max_child_depth = 0
    for child_id in blocking_deps:
        if child_id not in visited:
            try:
                shown = run_command_json(["br", "show", child_id, "--json"], cwd=repo)
                child_issue = shown[0] if isinstance(shown, list) and shown else shown
                if isinstance(child_issue, dict):
                    child_depth, _ = compute_chain_depth(
                        child_issue, repo, memo, visited.copy()
                    )
                    if child_depth > max_child_depth:
                        max_child_depth = child_depth
            except Exception:
                pass

    depth = 1 + max_child_depth
    if issue_id:
        memo[issue_id] = depth
    return depth, len(blocking_deps)


def list_ready_candidates(
    project: str | Path | None,
    workspace: str | Path | None,
    repo_filter: str | None = None,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """List ready candidate beads with ranked order and validated metadata.

    Returns a 3-tuple of (candidates, unresolvable_errors, meta). Candidates with
    context resolution errors or ID length violations are separated into
    unresolvable_errors so only valid, dispatchable beads are in candidates.
    """
    repos = find_repos(project, workspace, repo_filter)
    by_id: dict[str, dict[str, Any]] = {}
    scheduler_order: list[str] = []
    duplicates: set[str] = set()

    for repo in repos:
        data = run_command_json(["br", "scheduler", "--limit", str(limit), "--json"], cwd=repo)
        if not isinstance(data, dict) or not isinstance(data.get("recommendations", []), list):
            raise RuntimeError(f"br scheduler returned invalid schema in {repo}")
        for rec in data.get("recommendations", []):
            if not isinstance(rec, dict) or not isinstance(rec.get("issue", {}), dict):
                raise RuntimeError(f"br scheduler returned malformed recommendation in {repo}")
            issue = rec.get("issue", {})
            issue_id = issue.get("id")
            if not issue_id:
                continue
            if issue_id in by_id:
                duplicates.add(issue_id)
                continue
            by_id[issue_id] = {"repo": repo, "scheduler": rec}
            scheduler_order.append(issue_id)

    if duplicates:
        raise RuntimeError(
            "duplicate Beads IDs across repositories; configure unique id.prefix values: "
            + ", ".join(sorted(duplicates))
        )

    viewer_order, viewer = (
        query_viewer_ranking(workspace) if workspace and not repo_filter else ([], None)
    )
    ordered = [i for i in viewer_order if i in by_id]
    ordered.extend(i for i in scheduler_order if i not in ordered)

    candidates: list[dict[str, Any]] = []
    unresolvable_errors: list[dict[str, Any]] = []
    chain_depth_memo: dict[str, int] = {}
    for issue_id in ordered:
        if len(candidates) >= limit:
            break
        repo = by_id[issue_id]["repo"]
        shown = run_command_json(["br", "show", issue_id, "--json"], cwd=repo)
        issue = shown[0] if isinstance(shown, list) and shown else shown
        if not isinstance(issue, dict) or issue.get("id") != issue_id:
            raise RuntimeError(f"br show returned invalid issue for {issue_id} in {repo}")

        chain_depth, blocking_dep_count = compute_chain_depth(issue, repo, memo=chain_depth_memo)

        try:
            validate_bead_id(issue_id)
            pane_id = validate_issue_pane_mapping(issue, repo)
            validated_context = parse_agent_context(issue.get("agent_context"), repo)
            candidates.append(
                {
                    "id": issue_id,
                    "repo": str(repo),
                    "title": issue.get("title", ""),
                    "pane_id": pane_id,
                    "priority": issue.get("priority", 0),
                    "status": issue.get("status", "open"),
                    "context": validated_context,
                    "task_text": extract_bead_task_text(issue),
                    "scheduler_score": by_id[issue_id]["scheduler"].get("score"),
                    "chain_depth": chain_depth,
                    "dependent_count": blocking_dep_count,
                }
            )
        except Exception as err:
            unresolvable_errors.append(
                {
                    "id": issue_id,
                    "repo": str(repo),
                    "title": issue.get("title", ""),
                    "priority": issue.get("priority", 0),
                    "status": issue.get("status", "open"),
                    "error": str(err),
                    "context": {"error": str(err)},
                    "task_text": extract_bead_task_text(issue),
                    "scheduler_score": by_id[issue_id]["scheduler"].get("score"),
                    "chain_depth": chain_depth,
                    "dependent_count": blocking_dep_count,
                }
            )

    meta = {
        "repos": [str(r) for r in repos],
        "planner": viewer or "br",
        "viewer_ranked": bool(viewer_order),
        "count": len(candidates),
        "unresolvable_count": len(unresolvable_errors),
        "unresolvable_errors": unresolvable_errors,
    }
    return candidates, unresolvable_errors, meta


def claim_bead(issue_id: str, actor: str, repo: Path | str) -> dict[str, Any]:
    """Atomically claim a bead with br update --claim --actor."""
    validate_bead_id(issue_id)
    repo_path = Path(repo).expanduser().resolve()
    # Long IDs cannot become Herdr names directly.  Resolve and validate their
    # durable mapping before the mutating claim, including duplicate detection.
    if len(issue_id) + len(HERDR_AGENT_PREFIX) > MAX_HERDR_AGENT_NAME_LENGTH:
        shown = run_command_json(["br", "show", issue_id, "--json"], cwd=repo_path)
        issue = shown[0] if isinstance(shown, list) and shown else shown
        pane_id = validate_issue_pane_mapping(issue, repo_path, check_unique=True)
        if actor != pane_id:
            raise RuntimeError(f"long bead {issue_id!r} must be claimed by its mapped pane {pane_id!r}, not {actor!r}")
    try:
        return run_command_json(
            ["br", "update", issue_id, "--claim", "--actor", actor, "--json"],
            cwd=repo_path,
        )
    except Exception:
        canonical_id = resolve_canonical_bead_id(issue_id, repo_path, actor)
        validate_bead_id(canonical_id)
        return run_command_json(
            ["br", "update", canonical_id, "--claim", "--actor", actor, "--json"],
            cwd=repo_path,
        )


def resolve_canonical_bead_id(issue_id: str, repo: Path | str, actor: str | None = None) -> str:
    """Resolve an issue ID or sanitized agent name back to its canonical bead ID."""
    if not isinstance(issue_id, str) or not issue_id.strip():
        return issue_id

    repo_path = Path(repo).expanduser().resolve()
    raw_id = issue_id.strip()

    # 1. Direct match check
    try:
        shown = run_command_json(["br", "show", raw_id, "--json"], cwd=repo_path)
        issue = shown[0] if isinstance(shown, list) and shown else shown
        if isinstance(issue, dict) and issue.get("id") == raw_id:
            return raw_id
    except Exception:
        pass

    target_ids = []
    if raw_id.startswith("bead-"):
        target_ids.append(raw_id[len("bead-"):])
    target_ids.append(raw_id)

    for tid in target_ids:
        # 2. Check candidate variants where hyphens might have replaced dots
        if "-" in tid:
            parts = tid.rsplit("-", 1)
            if len(parts) == 2:
                candidate = f"{parts[0]}.{parts[1]}"
                try:
                    shown = run_command_json(["br", "show", candidate, "--json"], cwd=repo_path)
                    issue = shown[0] if isinstance(shown, list) and shown else shown
                    if isinstance(issue, dict) and issue.get("id"):
                        return str(issue["id"])
                except Exception:
                    pass

        # 3. Query active issues in repository to match by assignee / sanitized ID
        try:
            listed = run_command_json(["br", "list", "--status", "in_progress,claimed,open", "--json"], cwd=repo_path)
            issues = listed if isinstance(listed, list) else listed.get("issues", [])
            if isinstance(issues, list):
                if actor:
                    actor_clean = actor.removeprefix("bead-")
                    matching = [
                        it for it in issues
                        if isinstance(it, dict) and (
                            it.get("assignee") in (actor, actor_clean)
                            or it.get("id") == tid
                            or it.get("id", "").replace(".", "-") == tid
                            or it.get("id", "").replace(".", "-") == actor_clean
                        )
                    ]
                    if len(matching) == 1 and matching[0].get("id"):
                        return str(matching[0]["id"])

                sanitized_matches = [
                    it for it in issues
                    if isinstance(it, dict) and it.get("id", "").replace(".", "-") == tid
                ]
                if len(sanitized_matches) == 1 and sanitized_matches[0].get("id"):
                    return str(sanitized_matches[0]["id"])
        except Exception:
            pass

    return raw_id


def resolve_bead_id_for_pane(pane_id: str, repo: Path | str) -> str:
    """Resolve one legal Herdr worker name through durable Beads metadata.

    This is deliberately a Beads lookup rather than name truncation: runtime
    fleet state is a recovery cache, while ``herder.pane_id`` remains authoritative.
    """
    if not isinstance(pane_id, str) or not HERDR_AGENT_NAME_RE.fullmatch(pane_id):
        raise RuntimeError("pane ID must be a legal lowercase Herdr worker name")
    repo_path = Path(repo).expanduser().resolve()
    listed = run_command_json(["br", "list", "--json"], cwd=repo_path)
    issues = listed if isinstance(listed, list) else listed.get("issues", [])
    matches = []
    for issue in issues if isinstance(issues, list) else []:
        if not isinstance(issue, dict) or not issue.get("id"):
            continue
        try:
            mapped_pane = pane_id_for_issue(issue["id"], issue.get("agent_context"))
        except Exception:
            continue
        if mapped_pane == pane_id:
            matches.append(issue["id"])
    if len(matches) != 1:
        detail = "none" if not matches else ", ".join(sorted(matches))
        raise RuntimeError(f"pane ID {pane_id!r} maps to {detail}; expected exactly one Beads issue")
    return matches[0]


def rollback_claim(
    issue_id: str,
    actor: str,
    repo: Path | str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Roll back a bead claim back to status open and clear assignee, optionally attaching a diagnostic reason."""
    repo_path = Path(repo).expanduser().resolve()
    target_id = issue_id
    validate_bead_id(target_id)
    if len(target_id) + len(HERDR_AGENT_PREFIX) > MAX_HERDR_AGENT_NAME_LENGTH:
        shown = run_command_json(["br", "show", target_id, "--json"], cwd=repo_path)
        issue = shown[0] if isinstance(shown, list) and shown else shown
        pane_id = validate_issue_pane_mapping(issue, repo_path)
        if actor != pane_id:
            raise RuntimeError(f"long bead {target_id!r} must be rolled back by mapped pane {pane_id!r}, not {actor!r}")

    if reason:
        try:
            run_command_json(
                ["br", "comments", "add", target_id, "--message", f"Rollback ({actor}): {reason}", "--actor", actor, "--json"],
                cwd=repo_path,
            )
        except Exception:
            resolved_id = resolve_canonical_bead_id(target_id, repo_path, actor)
            if resolved_id != target_id:
                target_id = resolved_id
                try:
                    run_command_json(
                        ["br", "comments", "add", target_id, "--message", f"Rollback ({actor}): {reason}", "--actor", actor, "--json"],
                        cwd=repo_path,
                    )
                except Exception as err:
                    print(f"warning: failed to record rollback comment for {target_id}: {err}", file=sys.stderr)

    try:
        res = run_command_json(
            ["br", "update", target_id, "--status", "open", "--assignee", "", "--actor", actor, "--json"],
            cwd=repo_path,
        )
    except Exception:
        resolved_id = resolve_canonical_bead_id(target_id, repo_path, actor)
        target_id = resolved_id
        res = run_command_json(
            ["br", "update", target_id, "--status", "open", "--assignee", "", "--actor", actor, "--json"],
            cwd=repo_path,
        )

    if isinstance(res, dict) and reason:
        res["reason"] = reason
    return res


def _dewrap(text: str, joiner: str = "") -> str:
    return re.sub(r"\n[ \t]*", joiner, text)


def _handoff_candidates(payload: str) -> tuple[str, ...]:
    """Keep terminal boundaries intact while de-wrapping agent-rendered JSON.

    De-wrapping is lossy and cannot be made exact: a hard wrap falling on a space inside a
    string value is indistinguishable from one falling mid-token, so joining with "" turns
    `"git status --short"` into `"git status--short"` while joining with " " would break a
    mid-token wrap. The empty join stays authoritative -- guessing would fabricate evidence
    the worker never printed -- and `_dewrap_is_ambiguous` reports when the join could have
    dropped a character.
    """
    boundary = next(TERMINAL_BOUNDARY.finditer(payload), None)
    if boundary:
        prefix = _dewrap(payload[: boundary.start()])
        return payload.lstrip(), (prefix + payload[boundary.start() :]).lstrip()
    return payload.lstrip(), _dewrap(payload).lstrip()


def _dewrap_is_ambiguous(payload: str) -> bool:
    """True when a wrap fell inside a string value, so the tight join may have eaten a space.

    Deliberately imprecise in one direction: a wrap that landed mid-token rejoins exactly
    and still reports True, because nothing in the captured text distinguishes it from a
    wrap that ate a space -- the pane strips the trailing space either way. Wraps between
    JSON tokens never report True, so the warning stays off ordinary pretty-printed output.

    In the herder flow this rarely fires at all: `close_bead_with_evidence` is normally
    handed an already-parsed dict by fleet.py, which warned on the pane text itself. It
    matters for the string and `@file` paths, where this is the first parse.
    """
    boundary = next(TERMINAL_BOUNDARY.finditer(payload), None)
    subject = payload[: boundary.start()] if boundary else payload
    if "\n" not in subject:
        return False
    decoder = json.JSONDecoder()
    try:
        tight, _ = decoder.raw_decode(_dewrap(subject).lstrip())
        spaced, _ = decoder.raw_decode(_dewrap(subject, " ").lstrip())
    except json.JSONDecodeError:
        return False
    return tight != spaced


def trailing_prose(suffix: str) -> str:
    """Worker text after the handoff JSON, excluding terminal chrome.

    The handoff is meant to be the worker's last word, so prose after it can
    contradict the evidence ("I also changed another file"). Refusing it blocked
    correct handoffs every time a CLI signed off or redrew its prompt, so it is
    surfaced to the caller instead of refused -- and never silently dropped.
    """
    lines = []
    for line in suffix.splitlines():
        if TERMINAL_BOUNDARY.fullmatch(line):
            break
        if line.strip():
            lines.append(line.strip())
    return " ".join(lines)


def split_command_tokens(cmd: str) -> list[str]:
    """Split a shell command string into tokens, handling quotes gracefully."""
    if not isinstance(cmd, str) or not cmd.strip():
        return []
    text = cmd.strip()
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def normalize_token(tok: str) -> str:
    """Normalize an individual command token: expand user, env vars, and relative paths."""
    if not tok:
        return ""
    if tok.startswith("-") and "=" in tok:
        flag, val = tok.split("=", 1)
        val_exp = os.path.expandvars(os.path.expanduser(val))
        if val_exp.startswith(("./", ".\\")):
            val_exp = os.path.normpath(val_exp)
        return f"{flag}={val_exp}"

    exp = os.path.expandvars(os.path.expanduser(tok))
    if exp.startswith(("./", ".\\")):
        exp = os.path.normpath(exp)
    return exp


def _resolve_token_path(tok: str) -> str:
    """Resolve token if it corresponds to an existing file/directory or an executable on PATH."""
    norm = normalize_token(tok)
    try:
        p = Path(norm).expanduser()
        if p.exists():
            return str(p.resolve())
    except Exception:
        pass
    which = shutil.which(norm)
    if which:
        try:
            return str(Path(which).resolve())
        except Exception:
            return which
    return norm


def normalize_command(cmd: str) -> str:
    """Normalize whitespace, tildes, and environment variables across a command."""
    tokens = split_command_tokens(cmd)
    if not tokens:
        return ""
    return " ".join(normalize_token(t) for t in tokens)


def normalize_command_resolved(cmd: str) -> str:
    """Normalize and resolve path/executable tokens in a command."""
    tokens = split_command_tokens(cmd)
    if not tokens:
        return ""
    return " ".join(_resolve_token_path(t) for t in tokens)


def is_command_match(c1: str, c2: str) -> bool:
    """Compare two command strings for equivalence under normalization and token resolution."""
    if not isinstance(c1, str) or not isinstance(c2, str):
        return False
    if c1.strip() == c2.strip():
        return True
    if normalize_command(c1) == normalize_command(c2):
        return True
    if normalize_command_resolved(c1) == normalize_command_resolved(c2):
        return True

    t1 = split_command_tokens(c1)
    t2 = split_command_tokens(c2)
    if len(t1) != len(t2):
        return False

    for a, b in zip(t1, t2):
        if a == b:
            continue
        na = normalize_token(a)
        nb = normalize_token(b)
        if na == nb:
            continue
        ra = _resolve_token_path(a)
        rb = _resolve_token_path(b)
        if ra == rb:
            continue
        return False

    return True


def parse_and_validate_handoff(
    raw_handoff: str | dict[str, Any],
    required_verification: list[Any] | dict[str, Any] | None = None,
    issue_id: str | None = None,
    repo: Path | str | None = None,
) -> tuple[dict[str, Any], str]:
    """Parse and validate structured FLEET_HANDOFF payload."""
    dewrap_ambiguous = False
    if isinstance(raw_handoff, dict):
        handoff = raw_handoff
        canonical = "FLEET_HANDOFF: " + json.dumps(handoff, sort_keys=True)
    else:
        text = raw_handoff.strip()
        if text.startswith("@") and Path(text[1:]).is_file():
            text = Path(text[1:]).read_text(encoding="utf-8").strip()

        # Check for marker
        markers = list(re.finditer(rf"(?m)^[ \t]*(?:•[ \t]+)?{re.escape(HANDOFF_MARKER)}", text))
        payload = text[markers[-1].end() :] if markers else text

        decoder = json.JSONDecoder()
        decoded = None
        parsed = False
        last_error = None
        trailing = ""
        for candidate in _handoff_candidates(payload):
            try:
                decoded, end = decoder.raw_decode(candidate)
                trailing = trailing_prose(candidate[end:])
                parsed = True
                break
            except json.JSONDecodeError as err:
                last_error = str(err)

        if not parsed:
            raise RuntimeError(f"invalid FLEET_HANDOFF JSON: {last_error}")
        handoff = decoded
        canonical = "FLEET_HANDOFF: " + json.dumps(handoff, sort_keys=True)
        dewrap_ambiguous = _dewrap_is_ambiguous(payload)
        if trailing:
            print(
                f"warning: worker text after the handoff, not covered by its evidence: {trailing}",
                file=sys.stderr,
            )

    if not isinstance(handoff, dict) or not HANDOFF_FIELDS.issubset(handoff):
        missing = sorted(HANDOFF_FIELDS - set(handoff) if isinstance(handoff, dict) else HANDOFF_FIELDS)
        raise RuntimeError(f"handoff missing required fields: {missing}")

    for field in ("decisions", "changed_files", "verification", "blockers", "next_inputs"):
        if not isinstance(handoff[field], list):
            raise RuntimeError(f"handoff.{field} must be an array")

    if handoff["status"] != "completed":
        raise RuntimeError(f"handoff status is {handoff['status']!r}, expected 'completed'")

    if handoff["blockers"]:
        raise RuntimeError(f"completed handoff lists active blockers: {handoff['blockers']}")

    evidence = handoff["verification"]
    if not evidence:
        raise RuntimeError("completion refused: handoff verification evidence list is empty")

    if not all(
        isinstance(v, dict)
        and isinstance(v.get("command"), str)
        and v.get("command", "").strip()
        and type(v.get("exit_code")) is int
        and not isinstance(v.get("exit_code"), bool)
        and type(v.get("expected_exit", 0)) is int
        and not isinstance(v.get("expected_exit", 0), bool)
        and (
            v.get("expected_failures") is None
            or (type(v.get("expected_failures")) is int and not isinstance(v.get("expected_failures"), bool) and v.get("expected_failures") >= 0)
        )
        and (
            v.get("failures") is None
            or (type(v.get("failures")) is int and not isinstance(v.get("failures"), bool) and v.get("failures") >= 0)
        )
        for v in evidence
    ):
        raise RuntimeError(
            "each verification entry must be an object with non-empty string 'command', integer "
            "'exit_code', and integer 'expected_exit' when present"
        )

    # Build mapping of required commands and their expectations
    req_specs: list[dict[str, Any]] = []
    global_expected_exit: int | None = None
    global_expected_failures: int | None = None

    if isinstance(required_verification, dict):
        global_expected_exit = required_verification.get("expected_exit")
        global_expected_failures = required_verification.get("expected_failures")
        raw_reqs = (
            required_verification.get("verification_specs")
            or required_verification.get("verification")
            or required_verification.get("commands")
            or []
        )
        if isinstance(raw_reqs, str):
            raw_reqs = [raw_reqs]
    elif isinstance(required_verification, list):
        raw_reqs = required_verification
    else:
        raw_reqs = []

    for item in raw_reqs:
        if isinstance(item, str) and item.strip():
            cmd = item.strip()
            spec: dict[str, Any] = {"command": cmd}
            if global_expected_exit is not None:
                spec["expected_exit"] = global_expected_exit
            if global_expected_failures is not None:
                spec["expected_failures"] = global_expected_failures
            req_specs.append(spec)
        elif isinstance(item, dict) and "command" in item and isinstance(item["command"], str):
            cmd = item["command"].strip()
            spec = {"command": cmd}
            ee = item.get("expected_exit", global_expected_exit)
            if ee is not None:
                spec["expected_exit"] = ee
            ef = item.get("expected_failures", global_expected_failures)
            if ef is not None:
                spec["expected_failures"] = ef
            req_specs.append(spec)

    failed = []
    for v in evidence:
        v_cmd = v["command"].strip()
        matching_spec = next((s for s in req_specs if is_command_match(v_cmd, s["command"])), None)

        if matching_spec and "expected_exit" in matching_spec:
            target_expected_exit = matching_spec["expected_exit"]
        elif "expected_exit" in v:
            target_expected_exit = v["expected_exit"]
        elif global_expected_exit is not None:
            target_expected_exit = global_expected_exit
        else:
            target_expected_exit = 0

        if v["exit_code"] != target_expected_exit:
            failed.append(v)
            continue

        if matching_spec and "expected_failures" in matching_spec:
            target_ef = matching_spec["expected_failures"]
        elif "expected_failures" in v:
            target_ef = v["expected_failures"]
        elif global_expected_failures is not None:
            target_ef = global_expected_failures
        else:
            target_ef = None

        if target_ef is not None:
            actual_ef = v.get("failures", v.get("expected_failures"))
            if actual_ef is None and target_ef == 0:
                actual_ef = 0
            if actual_ef != target_ef:
                failed.append(v)
                continue
        elif v.get("failures", 0) != 0:
            failed.append(v)
            continue

    if failed:
        if repo and issue_id:
            try:
                log_verification_output(repo, issue_id, evidence)
            except Exception:
                pass

        failed_clamped = []
        for f in failed:
            f_copy = copy.deepcopy(f)
            if "output" in f_copy and isinstance(f_copy["output"], str):
                f_copy["output"] = clamp_traceback_output(f_copy["output"], issue_id=issue_id)
            failed_clamped.append(f_copy)

        err_msg = f"verification exits do not match expectations: {failed_clamped}"
        clamped_err = clamp_traceback_output(err_msg, issue_id=issue_id)
        raise RuntimeError(clamped_err)

    if req_specs:
        evidence_cmds = [v["command"].strip() for v in evidence]
        missing_cmds = [
            s["command"]
            for s in req_specs
            if not any(is_command_match(ev_cmd, s["command"]) for ev_cmd in evidence_cmds)
        ]
        if missing_cmds:
            raise RuntimeError(
                f"required verification commands missing from evidence: {missing_cmds} "
                f"(reported commands: {evidence_cmds})"
            )

    if dewrap_ambiguous:
        # The pane hard-wrapped inside a string value, so a character was lost rejoining it.
        # Say so: the evidence is the worker's, but this rendering of it is not exact.
        print(
            "warning: handoff was rebuilt from hard-wrapped pane output and a wrap fell "
            "inside a string value; command text may be missing a space. Compare it "
            "against the required verification before trusting it.",
            file=sys.stderr,
        )

    return handoff, canonical


def _issue_state(issue_id: str, repo_path: Path) -> str | None:
    """The bead's status according to br, or None if it cannot be read."""
    try:
        shown = run_command_json(["br", "show", issue_id, "--json"], cwd=repo_path)
    except RuntimeError:
        return None
    issue = shown[0] if isinstance(shown, list) and shown else shown
    return issue.get("status") if isinstance(issue, dict) else None


def _close_or_confirm(close_cmd: list[str], issue_id: str, repo_path: Path) -> dict[str, Any]:
    """Run `br close`, and on failure decide from the bead itself, not from the message.

    Observed on br 0.2.11 in a real fleet run: `br close` returned NOTHING_TO_DO -- "already
    closed or not found" -- for a bead that was claimed and open, once out of five closes in
    the same run and never reproducibly. Whatever the cause, the caller must not take the
    error at face value: an herder that treats a landed close as a refusal rolls back
    finished work and reopens it. So read the bead back. Closed means closed. Still open
    means the close really did not happen, and it is worth exactly one retry before raising,
    because the evidence comment is already attached and a second attempt adds no evidence.
    """
    try:
        return run_command_json(close_cmd, cwd=repo_path)
    except RuntimeError as first:
        if _issue_state(issue_id, repo_path) == "closed":
            return {"status": "closed", "id": issue_id, "confirmed_after_error": str(first)}

        # Suggestion sug_20260902_074122_a34888:
        # If br close failed because of milestone / parent-child dependencies blocking the child task,
        # retry with --force since completion verification was already verified by the scheduler.
        err_msg = str(first).lower()
        if any(marker in err_msg for marker in ("blocked by", "dependency", "parent-child", "milestone")):
            force_cmd = list(close_cmd)
            if "--force" not in force_cmd:
                force_cmd.append("--force")
            try:
                return run_command_json(force_cmd, cwd=repo_path)
            except RuntimeError as force_err:
                if _issue_state(issue_id, repo_path) == "closed":
                    return {"status": "closed", "id": issue_id, "confirmed_after_error": str(force_err)}

        try:
            result = run_command_json(close_cmd, cwd=repo_path)
        except RuntimeError as second:
            if _issue_state(issue_id, repo_path) == "closed":
                return {"status": "closed", "id": issue_id, "confirmed_after_error": str(second)}
            raise RuntimeError(f"br close failed twice: {first}; retry: {second}") from second
        return result


def close_bead_with_evidence(
    issue_id: str,
    actor: str,
    handoff_raw: str | dict[str, Any],
    repo: Path | str,
    session: str | None = None,
    reason: str | None = None,
    bypass_policy: bool = False,
    bypass_reason: str | None = None,
    ledger_path: Path | str | None = None,
) -> dict[str, Any]:
    """Validate handoff evidence, add comment, and close bead."""
    validate_bead_id(issue_id)
    repo_path = Path(repo).expanduser().resolve()
    canonical_id = issue_id

    # Load issue to verify required verification commands
    try:
        shown = run_command_json(["br", "show", canonical_id, "--json"], cwd=repo_path)
    except Exception:
        canonical_id = resolve_canonical_bead_id(issue_id, repo_path, actor)
        validate_bead_id(canonical_id)
        shown = run_command_json(["br", "show", canonical_id, "--json"], cwd=repo_path)

    issue = shown[0] if isinstance(shown, list) and shown else shown
    if not isinstance(issue, dict) or (issue.get("id") != canonical_id and issue.get("id") != issue_id):
        raise RuntimeError(f"could not load issue {canonical_id} from {repo_path}")
    if isinstance(issue, dict) and issue.get("id"):
        canonical_id = issue["id"]

    # Keep the completion gate bound to the same durable mapping that admitted
    # the claim.  The handoff cannot substitute a different issue ID.
    if len(canonical_id) + len(HERDR_AGENT_PREFIX) > MAX_HERDR_AGENT_NAME_LENGTH:
        pane_id = validate_issue_pane_mapping(issue, repo_path)
        if actor != pane_id:
            raise RuntimeError(f"long bead {canonical_id!r} must be closed by mapped pane {pane_id!r}, not {actor!r}")

    req_verification: Any = []
    try:
        ctx = parse_agent_context(issue.get("agent_context"), repo_path)
        req_verification = ctx
    except Exception:
        pass

    handoff, canonical_text = parse_and_validate_handoff(
        handoff_raw, req_verification, issue_id=canonical_id, repo=repo_path
    )

    # Ensure full verification output is logged to .beads/logs/<issue_id>-verification.log
    try:
        log_verification_output(repo_path, canonical_id, handoff.get("verification", []))
    except Exception as err:
        print(f"warning: failed to write verification log for {canonical_id}: {err}", file=sys.stderr)

    # 1. Attach handoff comment
    comment_res = run_command_json(
        ["br", "comments", "add", canonical_id, "--message", canonical_text, "--actor", actor, "--json"],
        cwd=repo_path,
    )

    # 2. Close issue
    close_cmd = [
        "br",
        "close",
        canonical_id,
        "--reason",
        reason or f"Completed by {actor}",
        "--actor",
        actor,
        "--json",
    ]
    if session:
        close_cmd.extend(["--session", session])
    if bypass_policy:
        if not bypass_reason:
            raise RuntimeError("--bypass-policy requires --bypass-reason")
        close_cmd.extend(["--bypass-policy", "--bypass-reason", bypass_reason])

    close_res = _close_or_confirm(close_cmd, canonical_id, repo_path)

    # 3. Record verified closure in append-only machine-readable ledger
    timestamp = datetime.now(timezone.utc).isoformat()
    verification_evidence = [
        {
            "command": v["command"],
            "exit_code": v["exit_code"],
            **({"expected_exit": v["expected_exit"]} if "expected_exit" in v else {}),
            **({"expected_failures": v["expected_failures"]} if "expected_failures" in v else {}),
            **({"failures": v["failures"]} if "failures" in v else {}),
        }
        for v in handoff.get("verification", [])
        if isinstance(v, dict) and "command" in v and "exit_code" in v
    ]
    ledger_record = {
        "id": canonical_id,
        "bead_id": canonical_id,
        "actor": actor,
        "timestamp": timestamp,
        "repo": str(repo_path),
        "verification": verification_evidence,
        "handoff": handoff,
    }
    try:
        append_close_ledger(repo_path, ledger_record, ledger_path=ledger_path)
    except Exception as err:
        print(f"warning: failed to write closure ledger for {canonical_id}: {err}", file=sys.stderr)

    return {
        "status": "closed",
        "id": canonical_id,
        "repo": str(repo_path),
        "comment": comment_res,
        "close": close_res,
        "handoff": handoff,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Beads Task Scheduler - DAG Task Management & Wave Planning CLI"
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # ready
    p_ready = subparsers.add_parser("ready", aliases=["candidates", "list-ready"], help="List ranked ready beads")
    p_ready.add_argument("--project", "-p", help="Path to project directory containing .beads/")
    p_ready.add_argument("--workspace", "-w", help="Path to .bv/workspace.yaml")
    p_ready.add_argument("--repo", "-r", help="Filter single repository when using workspace")
    p_ready.add_argument("--limit", "-l", type=int, default=20, help="Maximum candidates to return (default: 20)")
    p_ready.add_argument("--json", action="store_true", help="Output as JSON")

    # create
    p_create = subparsers.add_parser("create", help="Create a bead with validated execution metadata")
    p_create.add_argument("--title", "-t", required=True, help="Bead title")
    p_create.add_argument("--description", "-d", help="Bead objective / description")
    p_create.add_argument("--agent-context", "-c", help="Agent context JSON string or @file")
    p_create.add_argument("--priority", type=int, help="Bead priority (0-4)")
    p_create.add_argument("--type", dest="issue_type", help="Issue type (task, bug, feature)")
    p_create.add_argument("--design", help="Design specification")
    p_create.add_argument("--acceptance-criteria", help="Acceptance criteria")
    p_create.add_argument("--notes", help="Additional notes")
    p_create.add_argument("--actor", "-a", help="Actor name")
    p_create.add_argument("--project", "-p", help="Target repository directory")
    p_create.add_argument("--json", action="store_true", help="Output as JSON")

    # claim
    p_claim = subparsers.add_parser("claim", help="Atomically claim a ready bead")
    p_claim.add_argument("id", nargs="?", help="Bead ID to claim")
    p_claim.add_argument("--id", dest="opt_id", help="Bead ID to claim")
    p_claim.add_argument("--actor", "-a", required=True, help="Actor / worker name claiming the task")
    p_claim.add_argument("--project", "-p", help="Target repository directory")
    p_claim.add_argument("--json", action="store_true", help="Output as JSON")

    # resolve-pane is the seam used by auto_herd after a short worker completes.
    p_pane = subparsers.add_parser("resolve-pane", help="Resolve a Herdr pane name to its durable Beads ID")
    p_pane.add_argument("--pane-id", required=True, help="Legal Herdr worker name")
    p_pane.add_argument("--project", "-p", required=True, help="Target repository directory")
    p_pane.add_argument("--json", action="store_true", help="Output as JSON")

    # rollback
    p_roll = subparsers.add_parser("rollback", aliases=["requeue", "unclaim"], help="Roll back claim to open status")
    p_roll.add_argument("id", nargs="?", help="Bead ID to roll back")
    p_roll.add_argument("--id", dest="opt_id", help="Bead ID to roll back")
    p_roll.add_argument("--actor", "-a", required=True, help="Actor name")
    p_roll.add_argument("--project", "-p", help="Target repository directory")
    p_roll.add_argument("--reason", "--note", dest="reason", help="Diagnostic reason or note for rollback")
    p_roll.add_argument("--json", action="store_true", help="Output as JSON")

    # close
    p_close = subparsers.add_parser("close", help="Validate completion evidence and close bead")
    p_close.add_argument("id", nargs="?", help="Bead ID to close")
    p_close.add_argument("--id", dest="opt_id", help="Bead ID to close")
    p_close.add_argument("--actor", "-a", required=True, help="Actor name")
    p_close.add_argument("--handoff", required=True, help="Structured FLEET_HANDOFF string, JSON, or @file")
    p_close.add_argument("--session", "-s", help="Session ID for tracking")
    p_close.add_argument("--reason", help="Close reason")
    p_close.add_argument("--bypass-policy", action="store_true", help="Bypass closure policy gates")
    p_close.add_argument("--bypass-reason", help="Reason for bypassing policy gates")
    p_close.add_argument("--ledger", help="Custom path for close ledger file")
    p_close.add_argument("--project", "-p", help="Target repository directory")
    p_close.add_argument("--json", action="store_true", help="Output as JSON")

    # show
    p_show = subparsers.add_parser("show", help="Show issue details and parsed context")
    p_show.add_argument("id", nargs="?", help="Bead ID to inspect")
    p_show.add_argument("--id", dest="opt_id", help="Bead ID to inspect")
    p_show.add_argument("--project", "-p", help="Target repository directory")
    p_show.add_argument("--json", action="store_true", help="Output as JSON")

    # coordination
    p_coord = subparsers.add_parser("coordination", help="Inspect coordination status")
    p_coord.add_argument("--project", "-p", help="Target repository directory")
    p_coord.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    try:
        if args.subcommand in ("ready", "candidates", "list-ready"):
            candidates, unresolvable_errors, meta = list_ready_candidates(
                args.project, args.workspace, args.repo, args.limit
            )
            out = {
                "planning": meta,
                "ready": candidates,
                "unresolvable_errors": unresolvable_errors,
            }
            if args.json:
                print(json.dumps(out, indent=2))
            else:
                print(f"Ready candidates ({meta['count']} tasks across {len(meta['repos'])} repo(s), planner={meta['planner']}):\n")
                for c in candidates:
                    print(f"  [{c['id']}] P{c['priority']} - {c['title']} (chain_depth={c.get('chain_depth', 0)}, dependents={c.get('dependent_count', 0)})")
                    print(f"      repo: {c['repo']}")
                    print(f"      resources: {c['context'].get('resources', [])} (read_only={c['context'].get('read_only')})")
                    print(f"      verification: {c['context'].get('verification', [])}")
                    print()
                if unresolvable_errors:
                    print(f"Unresolvable context errors ({len(unresolvable_errors)} tasks):\n")
                    for e in unresolvable_errors:
                        print(f"  [{e['id']}] P{e['priority']} - {e['title']} (chain_depth={e.get('chain_depth', 0)}, dependents={e.get('dependent_count', 0)})")
                        print(f"      repo: {e['repo']}")
                        print(f"      error: {e.get('error')}")
                        print()

        elif args.subcommand == "create":
            repo = Path(args.project).expanduser().resolve() if args.project else Path.cwd()
            raw_ctx = args.agent_context
            if raw_ctx and raw_ctx.startswith("@") and Path(raw_ctx[1:]).is_file():
                raw_ctx = Path(raw_ctx[1:]).read_text(encoding="utf-8")
            res = create_bead(
                title=args.title,
                repo=repo,
                description=args.description,
                agent_context=raw_ctx,
                priority=args.priority,
                issue_type=args.issue_type,
                design=args.design,
                acceptance_criteria=args.acceptance_criteria,
                notes=args.notes,
                actor=args.actor,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Created bead {res.get('id')}: {res.get('title')}")

        elif args.subcommand == "claim":
            target_id = args.opt_id or args.id
            if not target_id:
                parser.error("bead ID is required")
            repo = args.project or Path.cwd()
            res = claim_bead(target_id, args.actor, repo)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Claimed bead {target_id} for {args.actor}")

        elif args.subcommand == "resolve-pane":
            bead_id = resolve_bead_id_for_pane(args.pane_id, args.project)
            out = {"pane_id": args.pane_id, "bead_id": bead_id}
            print(json.dumps(out) if args.json else bead_id)

        elif args.subcommand in ("rollback", "requeue", "unclaim"):
            target_id = args.opt_id or args.id
            if not target_id:
                parser.error("bead ID is required")
            repo = args.project or Path.cwd()
            reason = getattr(args, "reason", None)
            res = rollback_claim(target_id, args.actor, repo, reason=reason)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                msg = f"Rolled back bead {target_id} to open status"
                if reason:
                    msg += f" (reason: {reason})"
                print(msg)

        elif args.subcommand == "close":
            target_id = args.opt_id or args.id
            if not target_id:
                parser.error("bead ID is required")
            repo = args.project or Path.cwd()
            res = close_bead_with_evidence(
                issue_id=target_id,
                actor=args.actor,
                handoff_raw=args.handoff,
                repo=repo,
                session=args.session,
                reason=args.reason,
                bypass_policy=args.bypass_policy,
                bypass_reason=args.bypass_reason,
                ledger_path=args.ledger,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Successfully validated evidence and closed bead {target_id}")

        elif args.subcommand == "show":
            target_id = args.opt_id or args.id
            if not target_id:
                parser.error("bead ID is required")
            repo = Path(args.project).expanduser().resolve() if args.project else Path.cwd()
            shown = run_command_json(["br", "show", target_id, "--json"], cwd=repo)
            issue = shown[0] if isinstance(shown, list) and shown else shown
            try:
                ctx = parse_agent_context(issue.get("agent_context"), repo)
            except Exception as err:
                ctx = {"error": str(err)}
            out = {"issue": issue, "parsed_context": ctx}
            if args.json:
                print(json.dumps(out, indent=2))
            else:
                print(f"Bead {target_id}: {issue.get('title')}")
                print(f"Status: {issue.get('status')} | Priority: P{issue.get('priority')}")
                print(f"Parsed Context: {json.dumps(ctx, indent=2)}")

        elif args.subcommand == "coordination":
            repo = Path(args.project).expanduser().resolve() if args.project else Path.cwd()
            res = run_command_json(["br", "coordination", "status", "--json"], cwd=repo)
            print(json.dumps(res, indent=2))

    except Exception as err:
        if "--json" in sys.argv or "-j" in sys.argv:
            print(json.dumps({"error": str(err)}, indent=2))
        else:
            sys.stderr.write(f"Error: {err}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
