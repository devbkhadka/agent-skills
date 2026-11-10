"""Worker handoff capsule and file-backed diagnostics protocol for verified-feature-delivery.

Enforces compact, deterministic structured handoffs between workers and the driver.
Returns only outcome, changed artifacts, verification summary, evidence paths,
file-backed diagnostics references, blockers, and next action.
Persists raw execution logs and detailed diagnostics to feature-scoped files so
capsules remain compact (under 4000 characters) while full execution traces remain
retrievable on demand.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

CAPSULE_SCHEMA_VERSION = "1.0.0"

CAPSULE_ALLOWED_KEYS = {
    "schema_version",
    "feature_slug",
    "task_id",
    "slice_id",
    "phase",
    "status",
    "outcome",
    "changed_artifacts",
    "verification",
    "evidence_paths",
    "diagnostics_ref",
    "sidecar_ref",
    "blockers",
    "next_action",
    "timestamp",
}

VERIFICATION_ENTRY_KEYS = {
    "command",
    "exit_code",
    "output",
    "expected_exit",
    "expected_empty",
    "passed",
}


def now_iso_utc() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_empty_or_whitespace(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str):
        return not val.strip()
    return False


def save_diagnostics(
    feature_dir: Path,
    task_id: str,
    raw_output: str,
) -> str:
    """Persist raw command stdout/stderr or expanded traces to a feature-scoped file.

    Returns the relative path to the diagnostics file from feature_dir.
    """
    feature_dir = Path(feature_dir).resolve()
    diag_dir = feature_dir / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    safe_task_id = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in task_id)
    diag_file = diag_dir / f"{safe_task_id}.log"
    with open(diag_file, "w", encoding="utf-8") as f:
        f.write(raw_output or "")
        f.write("\n")
    return f"diagnostics/{diag_file.name}"


def get_diagnostics(feature_dir: Path, diagnostics_ref: str) -> Optional[str]:
    """Retrieve raw file-backed diagnostics on demand."""
    if not diagnostics_ref:
        return None
    p = (Path(feature_dir) / diagnostics_ref).resolve()
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def create_capsule(
    feature_slug: str,
    status: str,
    outcome: str,
    changed_artifacts: List[str],
    verification: List[Dict[str, Any]],
    evidence_paths: List[str],
    blockers: Optional[List[str]] = None,
    next_action: str = "",
    task_id: Optional[str] = None,
    slice_id: Optional[str] = None,
    phase: Optional[str] = None,
    raw_output: Optional[str] = None,
    diagnostics_ref: Optional[str] = None,
    sidecar_ref: Optional[str] = None,
    feature_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create a structured, compact worker handoff capsule.

    Persists raw output to a file-backed log if raw_output is provided and feature_dir is known.
    """
    task_identifier = task_id or slice_id or phase or "task"

    if raw_output and feature_dir and not diagnostics_ref:
        diagnostics_ref = save_diagnostics(feature_dir, task_identifier, raw_output)

    # Sanitize and summarize verification entries
    sanitized_verification = []
    for v in verification:
        if not isinstance(v, dict):
            continue
        v_entry = {
            "command": str(v.get("command", "")),
            "exit_code": int(v.get("exit_code", 0)),
            "output": str(v.get("output", ""))[:500],  # Keep output summary compact
        }
        if "expected_exit" in v:
            v_entry["expected_exit"] = int(v["expected_exit"])
        if "expected_empty" in v:
            v_entry["expected_empty"] = bool(v["expected_empty"])
        exp_exit = v.get("expected_exit", 0)
        exp_empty = v.get("expected_empty", False)
        passed = (v_entry["exit_code"] == exp_exit) and (not exp_empty or not v_entry["output"].strip())
        v_entry["passed"] = passed
        sanitized_verification.append(v_entry)

    capsule: Dict[str, Any] = {
        "schema_version": CAPSULE_SCHEMA_VERSION,
        "feature_slug": str(feature_slug),
        "status": str(status).lower(),
        "outcome": str(outcome),
        "changed_artifacts": [str(p) for p in (changed_artifacts or [])],
        "verification": sanitized_verification,
        "evidence_paths": [str(p) for p in (evidence_paths or [])],
        "diagnostics_ref": diagnostics_ref,
        "blockers": [str(b) for b in (blockers or [])],
        "next_action": str(next_action),
        "timestamp": now_iso_utc(),
    }

    if task_id:
        capsule["task_id"] = str(task_id)
    if slice_id:
        capsule["slice_id"] = str(slice_id)
    if phase:
        capsule["phase"] = str(phase)
    if sidecar_ref:
        capsule["sidecar_ref"] = str(sidecar_ref)

    if feature_dir:
        handoffs_dir = Path(feature_dir) / "handoffs"
        handoffs_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in task_identifier)
        capsule_file = handoffs_dir / f"{safe_name}-capsule.json"
        with open(capsule_file, "w", encoding="utf-8") as f:
            json.dump(capsule, f, indent=2)
            f.write("\n")

    return capsule


def evaluate_capsule(
    feature_dir: Path,
    capsule_data: Dict[str, Any],
) -> Tuple[bool, str, Dict[str, Any]]:
    """Evaluate a worker handoff capsule against gate requirements.

    Returns:
        (is_valid: bool, decision_token: str, details: Dict[str, Any])

    Decision Tokens:
        - CAPSULE_VERIFIED: All verification passed and evidence files exist on disk.
        - MISSING_EVIDENCE: One or more declared evidence_paths missing or empty on disk.
        - VERIFICATION_FAILED: One or more verification commands failed exit code or empty checks.
        - CAPSULE_BLOCKED: Worker reported status 'blocked' or 'failed'.
        - CAPSULE_INVALID_SCHEMA: Malformed capsule JSON structure or unknown keys.
    """
    if not isinstance(capsule_data, dict):
        return False, "CAPSULE_INVALID_SCHEMA", {"error": "Capsule must be a dictionary"}

    unexpected_keys = set(capsule_data.keys()) - CAPSULE_ALLOWED_KEYS
    if unexpected_keys:
        return (
            False,
            "CAPSULE_INVALID_SCHEMA",
            {"error": f"Unexpected keys in capsule: {sorted(list(unexpected_keys))}"},
        )

    required_keys = ["schema_version", "feature_slug", "status", "outcome", "changed_artifacts", "verification", "evidence_paths", "blockers", "next_action"]
    for rk in required_keys:
        if rk not in capsule_data:
            return False, "CAPSULE_INVALID_SCHEMA", {"error": f"Missing required key '{rk}'"}

    status = str(capsule_data.get("status", "")).lower()
    if status in ("blocked", "failed"):
        blockers = capsule_data.get("blockers", [])
        return False, "CAPSULE_BLOCKED", {
            "status": status,
            "outcome": capsule_data.get("outcome"),
            "blockers": blockers,
            "next_action": capsule_data.get("next_action"),
            "diagnostics_ref": capsule_data.get("diagnostics_ref"),
        }

    if status != "completed":
        return False, "CAPSULE_INVALID_SCHEMA", {"error": f"Unknown status value: {status}"}

    # Verify each verification entry
    verification_entries = capsule_data.get("verification", [])
    if not isinstance(verification_entries, list):
        return False, "CAPSULE_INVALID_SCHEMA", {"error": "verification must be a list"}

    for idx, v in enumerate(verification_entries):
        if not isinstance(v, dict):
            return False, "CAPSULE_INVALID_SCHEMA", {"error": f"verification[{idx}] must be a dict"}
        cmd = v.get("command", "")
        code = v.get("exit_code")
        output = str(v.get("output", ""))
        expected_exit = v.get("expected_exit", 0)
        expected_empty = v.get("expected_empty", False)

        if code != expected_exit:
            return False, "VERIFICATION_FAILED", {
                "command": cmd,
                "exit_code": code,
                "expected_exit": expected_exit,
                "output": output,
            }
        if expected_empty and output.strip():
            return False, "VERIFICATION_FAILED", {
                "command": cmd,
                "exit_code": code,
                "expected_empty": True,
                "output": output,
            }

    # Verify evidence paths exist on disk
    feature_dir = Path(feature_dir).resolve()
    evidence_paths = capsule_data.get("evidence_paths", [])
    if not isinstance(evidence_paths, list):
        return False, "CAPSULE_INVALID_SCHEMA", {"error": "evidence_paths must be a list"}

    missing_evidence = []
    for ep in evidence_paths:
        ev_file = feature_dir / ep
        if not ev_file.is_file() or ev_file.stat().st_size == 0:
            # Check relative to cwd as fallback
            cwd_file = Path.cwd() / ep
            if not cwd_file.is_file() or cwd_file.stat().st_size == 0:
                missing_evidence.append(ep)

    if missing_evidence:
        return False, "MISSING_EVIDENCE", {
            "missing_evidence_paths": missing_evidence,
            "feature_dir": str(feature_dir),
        }

    return True, "CAPSULE_VERIFIED", {
        "feature_slug": capsule_data.get("feature_slug"),
        "task_id": capsule_data.get("task_id"),
        "slice_id": capsule_data.get("slice_id"),
        "changed_artifacts": capsule_data.get("changed_artifacts"),
        "evidence_paths": evidence_paths,
        "next_action": capsule_data.get("next_action"),
    }
