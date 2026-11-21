#!/usr/bin/env python3
"""
timelog_extract.py - Deterministic git extraction for time-log data

Extracts commits authored by TARGET_AUTHOR_EMAIL, deduplicates across
git worktrees/clones by commit SHA and canonical repository, and extracts
accurate ticket IDs from commit messages and containing branches.
"""

import subprocess
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
import re
import logging
import os

# Configuration
def _load_env() -> None:
    current = Path.cwd().resolve()
    candidate_paths = [
        current / ".env",
        *(parent / ".env" for parent in current.parents),
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for env_path in candidate_paths:
        if env_path.is_file():
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k:
                    os.environ.setdefault(k, v.strip().strip("'\""))
            break

_load_env()
DEFAULT_AUTHOR_PATTERN = os.environ.get("TARGET_AUTHOR_EMAIL", "")
DEFAULT_EXCLUDED_CO_AUTHORS = [
    p.strip()
    for p in os.environ.get("TIMELOG_EXCLUDE_CO_AUTHORS", "").split(",")
    if p.strip()
]

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def log_to_file(log_file: Optional[Path], message: str):
    logger.info(message)
    if log_file:
        with open(log_file, "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def get_canonical_repo_name(repo_path: Path) -> str:
    """Get canonical repository name from git remote or folder name."""
    try:
        remote = subprocess.check_output(
            ["git", "-C", str(repo_path), "config", "--get", "remote.origin.url"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        if remote:
            # Extract name from url: git@github.com:org/repo.git or https://.../repo
            clean = remote.rstrip("/").removesuffix(".git")
            repo_name = clean.split("/")[-1].split(":")[-1]
            if repo_name:
                return repo_name
    except Exception:
        pass
    
    # Fallback: strip worktree suffixes
    name = repo_path.name
    name = re.sub(r"-(?:worktree|slot-\d+).*$", "", name, flags=re.IGNORECASE)
    return name


def extract_ticket_from_text(text: str) -> Optional[str]:
    """Extract Jira ticket ID pattern (e.g. IN-1234, PROJ-567)."""
    if not text:
        return None
    match = re.search(r"\b([A-Z][A-Z0-9]*-[0-9]+)\b", text)
    return match.group(1) if match else None


def is_excluded_co_authored(message: str, excluded_co_authors: List[str]) -> bool:
    """Return True if the commit message contains a Co-authored-by trailer matching any excluded pattern."""
    if not message or not excluded_co_authors:
        return False
    lowered_patterns = [p.lower() for p in excluded_co_authors if p.strip()]
    if not lowered_patterns:
        return False
    for raw_line in message.splitlines():
        match = re.match(r"^\s*\**\s*co-authored-by\s*:\s*(.+)$", raw_line, flags=re.IGNORECASE)
        if match:
            trailer_value = match.group(1).lower()
            if any(pat in trailer_value for pat in lowered_patterns):
                return True
    return False


def estimate_hours(files_changed: int, lines_added: int, lines_removed: int, message_length: int, is_merge: bool) -> float:
    """Deterministic heuristic for commit weight estimation."""
    base = 0.5
    files_factor = min(1.0 + (files_changed / 5.0), 2.0) if files_changed > 0 else 1.0
    total_lines = lines_added + lines_removed
    lines_factor = min(1.0 + (total_lines / 400.0), 1.5) if total_lines > 0 else 1.0
    
    if message_length > 100:
        complexity = 1.2
    elif message_length < 20:
        complexity = 0.7
    else:
        complexity = 1.0
    
    merge_factor = 0.1 if is_merge else 1.0
    total = base * files_factor * lines_factor * complexity * merge_factor
    return max(0.25, min(total, 8.0))


def get_commits_for_repo(
    repo_path: Path,
    canonical_repo: str,
    seen_shas: Set[str],
    author_patterns: List[str],
    since_date: Optional[str] = None,
    until_date: Optional[str] = None,
    excluded_co_authors: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Extract unique commits for repository."""
    commits = []
    if excluded_co_authors is None:
        excluded_co_authors = DEFAULT_EXCLUDED_CO_AUTHORS
    
    cmd = [
        "git", "-C", str(repo_path),
        "log", "--all",
        "--pretty=format:%H%x00%an%x00%ae%x00%aI%x00%B%x00"
    ]
    for author_pattern in author_patterns:
        cmd.append(f"--author={author_pattern}")
    
    if since_date:
        since_arg = f"{since_date} 00:00:00" if re.match(r"^\d{4}-\d{2}-\d{2}$", since_date.strip()) else since_date
        cmd.extend(["--since", since_arg])
    if until_date:
        until_arg = f"{until_date} 23:59:59" if re.match(r"^\d{4}-\d{2}-\d{2}$", until_date.strip()) else until_date
        cmd.extend(["--until", until_arg])
    
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        logger.warning(f"Failed to get commits from {repo_path}")
        return []
    
    if not output.strip():
        return []
    
    records = output.split("\x00")
    since_day = since_date[:10] if since_date else None
    until_day = until_date[:10] if until_date else None
    i = 0
    while i < len(records) - 1:
        sha = records[i].strip()
        author = records[i + 1].strip()
        email = records[i + 2].strip()
        date = records[i + 3].strip()
        message = records[i + 4].strip()
        i += 5
        
        if not sha or not date or sha in seen_shas:
            continue

        # Git's --since/--until filtering is based on the committer timestamp,
        # while this report groups work by author date. Enforce the requested
        # calendar range against the date that will actually be reported.
        author_day = date[:10]
        if (since_day and author_day < since_day) or (until_day and author_day > until_day):
            continue
        
        # Mark SHA as seen across all worktrees
        seen_shas.add(sha)

        if is_excluded_co_authored(message, excluded_co_authors):
            continue
        
        # Determine if merge commit
        is_merge = False
        files_changed = 0
        lines_added = 0
        lines_removed = 0
        
        try:
            parents = subprocess.check_output(
                ["git", "-C", str(repo_path), "rev-list", sha, "--parents", "-n", "1"],
                stderr=subprocess.DEVNULL,
                text=True
            ).split()
            is_merge = len(parents) > 2
            
            if not is_merge:
                diff_output = subprocess.check_output(
                    ["git", "-C", str(repo_path), "diff-tree", "--no-commit-id", "--numstat", sha],
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                for line in diff_output.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        try:
                            added = int(parts[0]) if parts[0] != "-" else 0
                            removed = int(parts[1]) if parts[1] != "-" else 0
                            lines_added += added
                            lines_removed += removed
                            files_changed += 1
                        except ValueError:
                            pass
        except subprocess.CalledProcessError:
            pass
        
        # Accurate ticket extraction:
        # 1. First check commit message
        ticket = extract_ticket_from_text(message)
        
        # 2. If not in message, query containing branches for this specific commit
        if not ticket:
            try:
                branches_output = subprocess.check_output(
                    ["git", "-C", str(repo_path), "branch", "-a", "--contains", sha],
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                for branch_line in branches_output.splitlines():
                    branch_clean = branch_line.replace("*", "").strip()
                    candidate = extract_ticket_from_text(branch_clean)
                    if candidate:
                        ticket = candidate
                        break
            except Exception:
                pass
        
        # Estimate hours
        hours = estimate_hours(files_changed, lines_added, lines_removed, len(message), is_merge)
        
        commit = {
            "sha": sha,
            "author": author,
            "email": email,
            "date": date,
            "message": message,
            "ticket_id": ticket or "",
            "files_changed": files_changed,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "estimated_hours": hours,
            "repository": canonical_repo,
            "repository_path": str(repo_path)
        }
        
        commits.append(commit)
    
    return commits


def main():
    parser = argparse.ArgumentParser(description="Extract commits for time logging")
    parser.add_argument(
        "--target",
        action="append",
        dest="targets",
        help="Repository root to scan; may be supplied more than once"
    )
    parser.add_argument("--output", default="data/states/.extracted.json", help="Output file")
    parser.add_argument("--log", help="Log file")
    parser.add_argument("--since", help="Since date (YYYY-MM-DD)")
    parser.add_argument("--until", help="Until date (YYYY-MM-DD)")
    parser.add_argument(
        "--author",
        action="append",
        dest="author_patterns",
        help="Git author name or email pattern; may be supplied more than once"
    )
    parser.add_argument(
        "--exclude-co-author",
        action="append",
        dest="exclude_co_authors",
        help="Co-author name or email pattern to exclude; may be supplied more than once"
    )
    
    args = parser.parse_args()
    
    output = Path(args.output)
    log_file = Path(args.log) if args.log else None
    raw_targets = args.targets or [p for p in os.environ.get("TIMELOG_TARGETS", ".").split(":") if p] or ["."]
    targets = [Path(target).expanduser() for target in raw_targets]
    
    output.parent.mkdir(parents=True, exist_ok=True)
    
    for target in targets:
        if not target.is_dir():
            logger.error(f"Target path not found: {target}")
            sys.exit(1)

    log_to_file(log_file, "Starting extraction from: " + ", ".join(str(target) for target in targets))
    author_patterns = args.author_patterns or ([DEFAULT_AUTHOR_PATTERN] if DEFAULT_AUTHOR_PATTERN else [])
    if author_patterns:
        log_to_file(log_file, f"Filtering commits by author: {', '.join(author_patterns)}")
    else:
        log_to_file(log_file, "No author filter specified; extracting commits for all authors.")
    excluded_co_authors = list(DEFAULT_EXCLUDED_CO_AUTHORS)
    if args.exclude_co_authors:
        for pat in args.exclude_co_authors:
            if pat not in excluded_co_authors:
                excluded_co_authors.append(pat)
    if excluded_co_authors:
        log_to_file(log_file, f"Excluding commits co-authored by: {', '.join(excluded_co_authors)}")
    
    def find_git_repos(search_target: Path) -> List[Path]:
        found = []
        skip_dirs = {".git", "node_modules", "build", "dist", ".next", ".turbo", "venv", ".venv", "__pycache__"}
        for root, dirs, files in os.walk(search_target):
            if ".git" in dirs or ".git" in files:
                found.append(Path(root))
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        return sorted(found)

    repos_by_path: Dict[str, Path] = {}
    for target in targets:
        for repo in find_git_repos(target):
            repos_by_path[str(repo.resolve())] = repo
    repos = [repos_by_path[path] for path in sorted(repos_by_path)]

    if not repos:
        logger.error("No git repositories found in the configured targets")
        sys.exit(1)

    log_to_file(log_file, f"Found {len(repos)} repository directories/worktrees")
    
    all_commits = []
    seen_shas: Set[str] = set()
    
    for repo_path in sorted(repos):
        canonical_repo = get_canonical_repo_name(repo_path)
        log_to_file(log_file, f"Scanning {repo_path.name} (canonical: {canonical_repo})")
        
        commits = get_commits_for_repo(
            repo_path,
            canonical_repo,
            seen_shas,
            author_patterns,
            args.since,
            args.until,
            excluded_co_authors,
        )
        all_commits.extend(commits)
        
        log_to_file(log_file, f"  Extracted {len(commits)} unique commits")
    
    with open(output, "w") as f:
        json.dump(all_commits, f, indent=2)
    
    log_to_file(log_file, "Extraction complete")
    print(f"EXTRACTED: {len(all_commits)} unique commits across canonical repositories")


if __name__ == "__main__":
    main()
