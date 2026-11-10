#!/usr/bin/env python3
"""
timelog_infer.py - Feature inference for ungrouped commits

Uses Anthropic Claude if available and configured, otherwise falls back to
an intelligent rule-based extractor for commit messages without Jira ticket IDs.
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import Any, Optional
import os
import re

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


def log_msg(message: str, log_file: Optional[Path] = None) -> None:
    """Write timestamped message to log file and stderr."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {message}"
    print(log_line, file=sys.stderr)
    if log_file:
        with open(log_file, "a") as f:
            f.write(log_line + "\n")


def sanitize_feature_name(name: str) -> str:
    """Clean up feature name for display/grouping."""
    name = re.sub(r"[^a-zA-Z0-9\-\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def heuristic_feature_name(commit_message: str) -> str:
    """
    Extract a descriptive short summary from a commit message when no LLM is available.
    """
    if not commit_message:
        return "General Updates"
    
    first_line = commit_message.strip().split("\n")[0].strip()
    
    # Remove git ticket tags e.g. [IN-123], IN-123:, etc.
    first_line = re.sub(r"^\[?[A-Z]+-\d+\]?[:\s-]*", "", first_line)
    
    # Remove conventional commit prefixes: feat(ui-kit): ..., fix: ..., chore: ...
    match_conv = re.match(r"^(?:feat|fix|refactor|chore|test|docs|style|perf|build|ci|wip)(?:\([^)]+\))?[:\s-]+(.*)$", first_line, re.IGNORECASE)
    if match_conv:
        first_line = match_conv.group(1).strip()
    
    # Remove PR merge boilerplate
    match_merge = re.match(r"^Merge pull request #\d+ from \S+/(.*)$", first_line, re.IGNORECASE)
    if match_merge:
        first_line = match_merge.group(1).replace("-", " ").replace("_", " ")
    
    match_branch_merge = re.match(r"^Merge branch '([^']+)'", first_line, re.IGNORECASE)
    if match_branch_merge:
        first_line = match_branch_merge.group(1).replace("-", " ").replace("_", " ")
    
    # Clean up and capitalize
    first_line = first_line.strip()
    if not first_line:
        return "General Updates"
    
    # Capitalize first letter
    return first_line[0].upper() + first_line[1:]


def infer_feature_name(commit_message: str, client: Optional[Any]) -> str:
    """
    Use Claude or heuristic fallback to infer a feature name.
    """
    if client:
        prompt = f"""You are extracting a short summary/feature title from a git commit message for a daily time log.

Commit message:
{commit_message}

Return ONLY a short, professional title (2-6 words) describing the work done in plain English. Capitalize as a title.
Examples:
- "Device Simulation for Rain"
- "Widget Layout Engine Refactor"
- "Matrix Layout Breakpoint Restrictions"
- "Tank Fill Source Summary Consolidation"

Title:"""
        try:
            message = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=40,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            feature_name = message.content[0].text.strip().strip('"').strip("'")
            return sanitize_feature_name(feature_name)
        except Exception as e:
            print(f"WARNING: Claude API error: {e}", file=sys.stderr)
    
    return heuristic_feature_name(commit_message)


def process_commits(
    input_file: Path,
    output_file: Path,
    log_file: Optional[Path] = None,
    batch_size: int = 5
) -> None:
    """Process extracted commits and infer feature names for ungrouped ones."""
    log_msg(f"Reading extracted commits from {input_file}", log_file)
    
    try:
        with open(input_file) as f:
            commits = json.load(f)
    except Exception as e:
        log_msg(f"ERROR: Failed to read {input_file}: {e}", log_file)
        sys.exit(1)
    
    if not commits:
        log_msg("No commits to process", log_file)
        with open(output_file, "w") as f:
            json.dump([], f)
        return
    
    log_msg(f"Loaded {len(commits)} commits", log_file)
    
    client = None
    if HAS_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            client = anthropic.Anthropic()
            log_msg("Initialized Anthropic client for feature inference", log_file)
        except Exception as e:
            log_msg(f"Anthropic initialization skipped ({e}), using heuristic inference", log_file)
    else:
        log_msg("Using heuristic rule-based feature inference", log_file)
    
    ungrouped_commits = [
        c for c in commits
        if not c.get("ticket_id") or c["ticket_id"].strip() == ""
    ]
    
    log_msg(f"Found {len(ungrouped_commits)} commits needing feature inference", log_file)
    
    inferred_count = 0
    for i, commit in enumerate(ungrouped_commits):
        feature_name = infer_feature_name(commit.get("message", ""), client)
        commit["ticket_id"] = feature_name
        commit["inferred"] = True
        inferred_count += 1
        
        if (i + 1) % batch_size == 0:
            log_msg(f"Inferred features for {i + 1}/{len(ungrouped_commits)} commits", log_file)
    
    log_msg(f"Inferred features for {inferred_count} commits", log_file)
    
    try:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(commits, f, indent=2)
        log_msg(f"Wrote processed commits to {output_file}", log_file)
    except Exception as e:
        log_msg(f"ERROR: Failed to write {output_file}: {e}", log_file)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Infer feature names for ungrouped commits"
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to .extracted.json file"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write .inferred.json file"
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="Optional path to log file"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Number of inferences before logging progress"
    )
    
    args = parser.parse_args()
    process_commits(args.input, args.output, args.log, args.batch_size)


if __name__ == "__main__":
    main()
