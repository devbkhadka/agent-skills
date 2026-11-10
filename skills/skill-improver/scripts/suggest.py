#!/usr/bin/env python3
"""
suggest.py — Fire-and-forget suggestion recorder for skill-improver.
Handles skill name resolution, required fields validation (skill, priority, summary),
audit-only tracking for unmanaged critical issues, and file locking.
"""

import argparse
import datetime
import fcntl
import json
import os
import sys
import uuid
from pathlib import Path


def infer_agent(explicit_agent: str | None) -> str:
    if explicit_agent:
        return explicit_agent.strip()
    if os.environ.get("HERDR_AGENT_NAME"):
        return os.environ.get("HERDR_AGENT_NAME").strip()
    if os.environ.get("GEMINI_CLI") or os.environ.get("AGY") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID"):
        return "gemini"
    if os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    if os.environ.get("CODEX_HOME"):
        return "codex"
    if os.environ.get("OMP_HOME"):
        return "omp"
    return "unknown"


def infer_session_id(explicit_session: str | None) -> str:
    if explicit_session:
        return explicit_session.strip()
    for key in (
        "ANTIGRAVITY_CONVERSATION_ID",
        "HERDR_SESSION_NAME",
        "CONVERSATION_ID",
        "SESSION_ID",
        "CLAUDE_SESSION_ID",
        "TASK_ID",
    ):
        val = os.environ.get(key)
        if val:
            return val.strip()
    return "unknown"


def find_repo_root() -> Path:
    custom_dir = os.environ.get("AGENT_SKILLS_HOME")
    if custom_dir:
        return Path(os.path.expanduser(custom_dir)).resolve()

    # Traverse up from this script: skills/skill-improver/scripts/suggest.py -> repo root
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / "package.json").is_file() and (parent / "skills").is_dir():
            return parent
        if (parent / ".git").exists() and (parent / "skills").is_dir():
            return parent

    # Fallback to default user path
    return Path(os.path.expanduser("~/projects/agent-skills")).resolve()


def resolve_suggestions_dir() -> Path:
    custom_dir = os.environ.get("AGENT_SKILLS_SUGGESTIONS_DIR")
    if custom_dir:
        return Path(os.path.expanduser(custom_dir)).resolve()
    return find_repo_root() / "suggestions"


def resolve_managed_skill(raw_name: str, repo_root: Path) -> tuple[str, bool]:
    """
    Resolves skill or prompt name against known skills in <repo_root>/skills
    and known prompts in <repo_root>/system-prompts.
    Returns (canonical_name, is_managed).
    """
    if not raw_name:
        return ("_unknown", False)

    stripped = raw_name.strip()
    if stripped.lower().startswith("prompt:"):
        stripped = stripped[7:]

    normalized = stripped.strip().lower().replace("_", "-")

    # Read available skills and system-prompts from repo
    managed_targets: set[str] = set()
    for subdir in ("skills", "system-prompts"):
        d = repo_root / subdir
        if d.is_dir():
            managed_targets |= {
                entry.name for entry in d.iterdir()
                if entry.is_dir() and not entry.name.startswith(".")
            }

    # 1. Exact match
    if normalized in managed_targets:
        return (normalized, True)

    # 2. Fuzzy / alias matching (prefix or token match)
    for managed in managed_targets:
        if managed.startswith(normalized) or normalized.startswith(managed):
            return (managed, True)
        tokens = [t for t in normalized.split("-") if t]
        if len(tokens) >= 2 and all(t in managed for t in tokens):
            return (managed, True)

    # 3. Unrelated / unmanaged target
    sanitized = "".join(c for c in normalized if c.isalnum() or c in ("-", "_")).strip("-")
    if not sanitized:
        sanitized = "_unassigned"
    return (sanitized, False)


def normalize_category(category_arg: str | None, priority_arg: str) -> str:
    cat = (category_arg or "").strip().lower()
    prio = priority_arg.strip().lower()

    if cat in ("critical", "blocker") or prio == "critical":
        return "critical"
    if cat in ("bug", "issue", "defect", "error"):
        return "bug"
    if cat in ("improvement", "enhancement", "opt", "feature", "prompt"):
        return "improvement"

    # Default based on priority if category unspecified
    if prio == "critical":
        return "critical"
    if prio in ("high", "medium"):
        return "bug"
    return "improvement"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fire-and-forget suggestion recorder for agent skills."
    )
    parser.add_argument(
        "--skill",
        required=True,
        help="Name of the target skill (e.g. timelog-generator, verified-feature-delivery)",
    )
    parser.add_argument(
        "--priority",
        required=True,
        choices=["critical", "high", "medium", "low"],
        help="Perceived priority level (Required: critical, high, medium, low)",
    )
    parser.add_argument(
        "--summary",
        required=True,
        help="Short summary / title of the issue or improvement (Required)",
    )
    parser.add_argument(
        "--detail",
        default="",
        help="Optional detailed explanation of the friction, bug, or improvement proposal",
    )
    parser.add_argument(
        "--category",
        choices=["critical", "bug", "improvement", "issue"],
        default=None,
        help="Optional category tier: critical (blockers), bug (defects), or improvement (enhancements)",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Reporting agent identifier (gemini, claude, codex, omp, etc.)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session ID / Conversation ID of the current agent run",
    )
    parser.add_argument(
        "--fix",
        default="",
        help="Optional suggested code, regex, or prompt fix",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Optional project directory or task context",
    )

    args = parser.parse_args()

    repo_root = find_repo_root()
    canonical_skill, is_managed = resolve_managed_skill(args.skill, repo_root)

    priority = args.priority.strip().lower()
    category = normalize_category(args.category, priority)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    ts_str = now_utc.strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    suggestion_id = f"sug_{ts_str}_{short_uuid}"

    # Policy for unmanaged / unrelated skills:
    # Only record critical issues, and record summary only as an audit trail.
    if not is_managed:
        if priority != "critical" and category != "critical":
            # Non-critical issue on unmanaged skill -> ignore cleanly without failing
            print(
                json.dumps(
                    {
                        "status": "ignored",
                        "reason": "unmanaged_non_critical",
                        "skill": canonical_skill,
                        "raw_skill_input": args.skill.strip(),
                    }
                )
            )
            return 0

        # Unmanaged critical issue -> audit-only record with summary
        payload = {
            "id": suggestion_id,
            "timestamp": now_utc.isoformat(),
            "skill": canonical_skill,
            "raw_skill_input": args.skill.strip(),
            "managed": False,
            "priority": "critical",
            "category": "critical",
            "summary": args.summary.strip(),
            "agent": infer_agent(args.agent),
            "session_id": infer_session_id(args.session_id),
            "status": "pending",
        }
    else:
        # Managed skill -> full record
        payload = {
            "id": suggestion_id,
            "timestamp": now_utc.isoformat(),
            "skill": canonical_skill,
            "raw_skill_input": args.skill.strip(),
            "managed": True,
            "agent": infer_agent(args.agent),
            "session_id": infer_session_id(args.session_id),
            "priority": priority,
            "category": category,
            "summary": args.summary.strip(),
            "detail": args.detail.strip(),
            "suggested_fix": args.fix.strip(),
            "project_context": (args.context or os.getcwd()).strip(),
            "status": "pending",
        }

    try:
        suggestions_base = resolve_suggestions_dir()
        if is_managed:
            skill_dir = suggestions_base / canonical_skill
        else:
            skill_dir = suggestions_base / "_unmanaged" / canonical_skill

        skill_dir.mkdir(parents=True, exist_ok=True)

        target_file = skill_dir / f"{category}.jsonl"

        # Safe atomic append with POSIX advisory locking
        with open(target_file, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        print(
            json.dumps(
                {
                    "status": "recorded",
                    "id": suggestion_id,
                    "skill": canonical_skill,
                    "managed": is_managed,
                    "audit_only": not is_managed,
                    "category": category,
                    "priority": priority,
                    "file": str(target_file),
                }
            )
        )
        return 0

    except Exception as err:
        sys.stderr.write(f"Error recording suggestion: {err}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
