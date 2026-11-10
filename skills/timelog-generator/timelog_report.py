#!/usr/bin/env python3
"""
timelog_report.py - Generate delivery-focused daily time logs and scan results

Requirements:
- Group by Date (descending)
- Inside each day, group tickets by Repository Name
- If > 5 tickets in a day, group similar tickets into comma-separated IDs
- Bullet points focus on delivery/user story (roughly 3 points +/- 1-2)
- Quantized hours in multiples of 0.5h, exactly 8h per working day
- Support weekend shifting (Friday or Monday)
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set
from collections import defaultdict
import re


def log_msg(message: str, log_file: Optional[Path] = None) -> None:
    """Write timestamped message to log file and stderr."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {message}"
    print(log_line, file=sys.stderr)
    if log_file:
        with open(log_file, "a") as f:
            f.write(log_line + "\n")


def format_hours(h: float) -> str:
    """Format hours cleanly: e.g., 2 -> '2h', 2.5 -> '2.5h', 0.5 -> '0.5h'."""
    if h.is_integer():
        return f"{int(h)}h"
    return f"{h:.1f}h"


def is_delivery_focused(line: str) -> bool:
    """Filter out internal noise, agent attempts, churn, or micro-steps."""
    lower = line.lower()
    noise_patterns = [
        "lockfile mod",
        "index on parity",
        "attempt 1",
        "attempt 2",
        "attempt 3",
        "attempt 4",
        "attempt 5",
        "attempt 6",
        "attempt 7",
        "attempt 8",
        "attempt 9",
        "attempt 10",
        "attempt 11",
        "attempt 12",
        "merge branch",
        "merge pull request",
        "merged changes for",
        "wip:",
        "baseline captured",
        "what this run worked out",
        "what the port turned out",
        "record the fifth blocked session",
        "send system_error",
        "q4 struck by",
        "rebuild brief.json",
        " step ",
        "record the verification",
        "record the run",
        "record the port",
        "plan the ",
        "docs:",
        "chore(parity):",
        "record published milestone",
        "record pushed milestone",
        "record ported milestone",
        "record testsverified milestone",
        "record typecheckverified milestone",
        "record milestone",
        "port record",
        "learnings and verification",
        "dead commented import",
    ]
    return not any(p in lower for p in noise_patterns)


def outcome_summary(line: str) -> str:
    """Turn a commit-like summary into a short delivered-outcome statement."""
    transformations = [
        (r"^add(?:ed)?\s+", "Delivered "),
        (r"^fix(?:ed)?\s+", "Resolved "),
        (r"^update(?:d)?\s+", "Improved "),
        (r"^improve(?:d)?\s+", "Improved "),
        (r"^migrat(?:e|ed|ing)\s+", "Migrated "),
        (r"^enable(?:d)?\s+", "Enabled "),
        (r"^allow(?:ed)?\s+", "Enabled "),
        (r"^harden(?:ed)?\s+", "Hardened "),
        (r"^remov(?:e|ed|ing)\s+", "Removed "),
        (r"^resolv(?:e|ed|ing)\s+", "Resolved "),
    ]
    for pattern, replacement in transformations:
        if re.match(pattern, line, flags=re.IGNORECASE):
            return re.sub(pattern, replacement, line, count=1, flags=re.IGNORECASE).rstrip(".") + "."
    return f"Delivered {line[:1].lower() + line[1:].rstrip('.')} .".replace(" .", ".")


def clean_commit_for_jira(msg: str) -> str:
    """Convert raw commit message into clean, delivery-focused Jira bullet point."""
    if not msg:
        return ""
    
    first_line = msg.strip().split("\n")[0].strip()
    
    # Remove leading ticket tags: "PROJ-1234: ", "[PROJ-1234] ", "PROJ-1234 "
    first_line = re.sub(r"^\[?[A-Z][A-Z0-9]*-\d+\]?[:\s-]*", "", first_line, flags=re.IGNORECASE)
    
    # Remove branch refs: "On parity/...: "
    first_line = re.sub(r"^On\s+[\w\/-]+:\s*", "", first_line, flags=re.IGNORECASE)
    
    # Remove parity / port prefixes: "parity: ", "parity "
    first_line = re.sub(r"^parity[:\s-]+", "", first_line, flags=re.IGNORECASE)
    
    # Remove web step prefixes: "web #2804 step 3: "
    first_line = re.sub(r"^web\s*#\d+(?:\s+step\s+\d+)?:?\s*", "", first_line, flags=re.IGNORECASE)
    
    # Remove conventional commit prefixes: "feat(ui): ", "fix:", "refactor:", "build:", etc.
    first_line = re.sub(
        r"^(?:feat|fix|refactor|chore|test|docs|style|perf|build|ci|wip)(?:\([^)]+\))?[:\s-]+",
        "",
        first_line,
        flags=re.IGNORECASE
    )
    
    # Clean PR merges
    pr_match = re.match(r"^Merge pull request #\d+ from \S+/(.*)$", first_line, re.IGNORECASE)
    if pr_match:
        branch_name = pr_match.group(1).replace("-", " ").replace("_", " ")
        first_line = f"Integrated updates for {branch_name}"
    
    first_line = first_line.strip().rstrip(".")
    if not first_line:
        return ""
    
    # Capitalize
    first_line = first_line[0].upper() + first_line[1:]
    return first_line


def extract_delivery_bullets(commits: List[Dict[str, Any]], target_points: int = 3) -> List[str]:
    """Extract roughly 3 (+/- 1-2) high-value deliverable summaries from commits."""
    candidates = []
    seen = set()
    
    for c in commits:
        cleaned = clean_commit_for_jira(c.get("message", ""))
        if not cleaned:
            continue
        
        # Deduplicate
        norm = re.sub(r"[^a-zA-Z0-9]", "", cleaned.lower())[:35]
        if norm in seen:
            continue
        seen.add(norm)
        
        is_priority = is_delivery_focused(cleaned)
        candidates.append((cleaned, is_priority))
    
    # Prioritize high-signal delivery statements over noise
    priority_items = [item[0] for item in candidates if item[1]]
    fallback_items = [item[0] for item in candidates if not item[1]]
    
    selected = priority_items[:target_points]
    if len(selected) < target_points and fallback_items:
        needed = target_points - len(selected)
        selected.extend(fallback_items[:needed])
    
    if not selected:
        selected = ["Delivered planned feature implementation and validated end-to-end tests."]
    
    return [outcome_summary(s) for s in selected[:target_points]]


def allocate_8h_quantized(item_weights: Dict[str, float]) -> Dict[str, float]:
    """Allocate strictly 8.0 hours in increments of 0.5h across items."""
    items = list(item_weights.keys())
    n = len(items)
    if n == 0:
        return {}
    if n == 1:
        return {items[0]: 8.0}
    
    sorted_items = sorted(items, key=lambda it: item_weights[it], reverse=True)
    active_items = sorted_items[:16]  # Max 16 items can receive >= 0.5h in 8h
    total_weight = sum(item_weights[it] for it in active_items) or float(len(active_items))
    
    alloc = {it: 0.5 for it in active_items}
    remaining_hours = 8.0 - (len(active_items) * 0.5)
    slots_to_distribute = int(round(remaining_hours / 0.5))
    
    if slots_to_distribute > 0:
        raw_shares = []
        for it in active_items:
            share = (item_weights[it] / total_weight) * slots_to_distribute
            base_slots = int(share)
            remainder = share - base_slots
            raw_shares.append((it, base_slots, remainder))
        
        assigned_slots = sum(s[1] for s in raw_shares)
        unassigned_slots = slots_to_distribute - assigned_slots
        
        raw_shares.sort(key=lambda s: s[2], reverse=True)
        for i, (it, base_slots, _) in enumerate(raw_shares):
            extra = 1 if i < unassigned_slots else 0
            alloc[it] += (base_slots + extra) * 0.5
    
    diff = round(8.0 - sum(alloc.values()), 1)
    if diff != 0.0 and active_items:
        top_it = active_items[0]
        alloc[top_it] = max(0.5, alloc[top_it] + diff)
    
    return alloc


def group_similar_tickets_in_day(repo_tickets: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    If a day has many tickets (>5 total), cluster similar tickets together.
    Returns a list of ticket group objects with header and combined commits.
    """
    total_tickets = sum(len(tickets) for tickets in repo_tickets.values())
    groups = []
    
    for repo, tickets_dict in repo_tickets.items():
        if total_tickets <= 5:
            # Keep tickets individually
            for ticket, commits in tickets_dict.items():
                groups.append({
                    "repo": repo,
                    "header": ticket,
                    "commits": commits,
                    "weight": sum(float(c.get("estimated_hours", 0.5)) for c in commits)
                })
        else:
            # Group tickets by topic / similarity if multiple related tickets exist
            # Check for cluster keywords or merge tickets with shared messages
            clusters: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"tickets": set(), "commits": [], "weight": 0.0})
            
            for ticket, commits in tickets_dict.items():
                # Extract key topic
                first_msg = commits[0].get("message", "").lower() if commits else ""
                if "tank" in first_msg or "matrix" in first_msg or "layout" in first_msg:
                    topic = "Tank Fill & Matrix Layouts"
                elif "power-switch" in first_msg or "alarm" in first_msg:
                    topic = "Power Switch Controls"
                elif "parity-watch" in first_msg or "missing-feature" in first_msg:
                    topic = "Parity Watch & Automation"
                elif "slack" in first_msg:
                    topic = "Slack Notifications"
                else:
                    topic = ticket if bool(re.match(r"^[A-Z][A-Z0-9]*-\d+$", ticket)) else "Feature Enhancements"
                
                clusters[topic]["tickets"].add(ticket)
                clusters[topic]["commits"].extend(commits)
                clusters[topic]["weight"] += sum(float(c.get("estimated_hours", 0.5)) for c in commits)
            
            for topic, data in clusters.items():
                sorted_tickets = sorted(data["tickets"])
                ticket_ids = [t for t in sorted_tickets if re.match(r"^[A-Z][A-Z0-9]*-\d+$", t)]
                other_names = [t for t in sorted_tickets if not re.match(r"^[A-Z][A-Z0-9]*-\d+$", t)]
                
                if ticket_ids and topic not in ticket_ids:
                    header = f"{', '.join(ticket_ids)} ({topic})"
                elif ticket_ids:
                    header = ", ".join(ticket_ids)
                elif other_names:
                    header = ", ".join(other_names[:2])
                else:
                    header = topic
                
                groups.append({
                    "repo": repo,
                    "header": header,
                    "commits": data["commits"],
                    "weight": data["weight"]
                })
    
    return groups


def parse_existing_daily_blocks(file_path: Path) -> Dict[str, str]:
    """Parse an existing YYYY-MM-daily-time-log.md into a map of { 'YYYY-MM-DD': block_text }."""
    if not file_path.exists():
        return {}
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return {}
    
    pattern = r"(^##\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}))"
    matches = list(re.finditer(pattern, content, flags=re.MULTILINE))
    if not matches:
        return {}
    
    blocks = {}
    for i, match in enumerate(matches):
        date_str_raw = match.group(2)
        start_idx = match.start()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        block_text = content[start_idx:end_idx].strip()
        try:
            dt = datetime.strptime(date_str_raw, "%B %d, %Y")
            iso_date = dt.strftime("%Y-%m-%d")
            blocks[iso_date] = block_text
        except ValueError:
            pass
    return blocks


class TimeLogProcessor:
    """Process commits and generate organized outputs in data/ subdirectories."""
    
    def __init__(self, commits: List[Dict[str, Any]], data_dir: Path, weekend_target: str = "monday"):
        self.commits = commits
        self.data_dir = Path(data_dir)
        self.weekend_target = weekend_target.lower()
        
        self.daily_summaries_dir = self.data_dir / "daily-summaries"
        self.scan_results_dir = self.data_dir / "scan-results"
        self.states_dir = self.data_dir / "states"

        self.daily_summaries_dir.mkdir(parents=True, exist_ok=True)
        self.scan_results_dir.mkdir(parents=True, exist_ok=True)
        self.states_dir.mkdir(parents=True, exist_ok=True)
        
        # Grouping: date -> repo -> ticket -> commits
        self.by_date_repo_ticket: Dict[str, Dict[str, Dict[str, List[Dict]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )
        self.by_month: Dict[str, List[str]] = defaultdict(list)
    
    def _shift_weekend_date(self, date_str: str) -> str:
        """Shift Saturday/Sunday to Friday or Monday, or keep separate if requested."""
        if self.weekend_target in ("none", "separate", "keep"):
            return date_str
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        weekday = dt.weekday()
        if weekday == 5:  # Saturday
            dt = dt + timedelta(days=2) if self.weekend_target == "monday" else dt - timedelta(days=1)
        elif weekday == 6:  # Sunday
            dt = dt + timedelta(days=1) if self.weekend_target == "monday" else dt - timedelta(days=2)
        return dt.strftime("%Y-%m-%d")
    
    def parse_commits(self) -> None:
        """Parse, deduplicate, and organize commits into date/repo/ticket groupings."""
        for commit in self.commits:
            try:
                full_date = commit["date"]
                raw_date_str = full_date.split("T")[0]
            except (ValueError, IndexError, KeyError):
                continue
            
            date_str = self._shift_weekend_date(raw_date_str)
            repo = commit.get("repository", "general")
            raw_ticket = commit.get("ticket_id", "").strip()
            ticket = raw_ticket if raw_ticket else clean_commit_for_jira(commit.get("message", "")) or "General Updates"
            
            self.by_date_repo_ticket[date_str][repo][ticket].append(commit)
            
            month = date_str[:7]
            if date_str not in self.by_month[month]:
                self.by_month[month].append(date_str)
        
        for month in self.by_month:
            self.by_month[month].sort()
    
    def generate_daily_time_log(self) -> int:
        """Generate delivery-focused daily time logs grouped by repo and date, merging/appending into existing logs in ascending order."""
        if not self.by_month:
            return 0
        
        files_created = 0
        
        for month in sorted(self.by_month.keys()):
            daily_file = self.daily_summaries_dir / f"{month}-daily-time-log.md"
            dates = sorted(self.by_month[month])
            
            existing_blocks = parse_existing_daily_blocks(daily_file)
            new_blocks = {}
            
            for date_str in dates:
                day_obj = datetime.strptime(date_str, "%Y-%m-%d")
                formatted_date = day_obj.strftime("%B %d, %Y")
                
                day_section = [f"## {formatted_date}"]
                repo_tickets = self.by_date_repo_ticket[date_str]
                
                # Group similar tickets if > 5 tickets in day
                ticket_groups = group_similar_tickets_in_day(repo_tickets)
                
                # Combine same ticket/feature across repositories and worktrees.
                combined_groups: Dict[str, Dict[str, Any]] = {}
                for group in ticket_groups:
                    existing = combined_groups.setdefault(group["header"], {
                        "header": group["header"], "commits": [], "weight": 0.0
                    })
                    existing["commits"].extend(group["commits"])
                    existing["weight"] += group["weight"]

                ticket_groups = list(combined_groups.values())

                # Allocate exactly 8.0h in 0.5h increments.
                weights = {g["header"]: g["weight"] for g in ticket_groups}
                allocated = allocate_8h_quantized(weights)
                ticket_groups.sort(key=lambda g: allocated.get(g["header"], 0.0), reverse=True)

                entries = []
                for group in ticket_groups:
                    hours_str = format_hours(allocated.get(group["header"], 0.5))
                    bullets = extract_delivery_bullets(group["commits"])
                    bullets_str = "\n".join(f"  - {b}" for b in bullets)
                    entries.append(f"### {group['header']}: {hours_str}\n{bullets_str}")

                if entries:
                    day_section.append("\n\n".join(entries))
                    new_blocks[date_str] = "\n\n".join(day_section)
            
            # Merge with existing blocks so previous estimations are preserved
            merged_blocks = dict(existing_blocks)
            merged_blocks.update(new_blocks)

            # Sort in ascending chronological order so estimations are in order
            sorted_dates = sorted(merged_blocks.keys())
            output_content = "\n\n".join(merged_blocks[d] for d in sorted_dates) + "\n"
            
            with open(daily_file, "w", encoding="utf-8") as f:
                f.write(output_content)
            
            files_created += 1
            log_msg(f"Generated daily time log: {daily_file} ({len(new_blocks)} new/updated days, {len(sorted_dates)} total days)")
        
        return files_created
    
    def generate_scan_results(self) -> int:
        """Generate scan results for commits, preserving existing scan commits."""
        files_created = 0
        
        for month in sorted(self.by_month.keys()):
            scan_file = self.scan_results_dir / f"{month}-scan-result.json"
            dates = sorted(self.by_month[month])
            
            month_commits = []
            for date_str in dates:
                for repo in self.by_date_repo_ticket[date_str].values():
                    for commits in repo.values():
                        month_commits.extend(commits)
            
            existing_commits = []
            if scan_file.exists():
                try:
                    with open(scan_file, "r", encoding="utf-8") as f:
                        old_data = json.load(f)
                        existing_commits = old_data.get("commits", [])
                except Exception:
                    pass
            
            commits_by_sha = {c["sha"]: c for c in existing_commits if "sha" in c}
            for c in month_commits:
                if "sha" in c:
                    commits_by_sha[c["sha"]] = c
            
            merged_commits = sorted(commits_by_sha.values(), key=lambda c: c.get("date", ""))
            
            all_dates = sorted(set(
                c["date"].split("T")[0] for c in merged_commits if "date" in c
            ))
            
            scan_data = {
                "month": month,
                "generated": datetime.now().isoformat(),
                "total_commits": len(merged_commits),
                "date_range": f"{all_dates[0]} to {all_dates[-1]}" if all_dates else "",
                "weekend_target": self.weekend_target,
                "commits": merged_commits
            }
            
            with open(scan_file, "w", encoding="utf-8") as f:
                json.dump(scan_data, f, indent=2)
            
            files_created += 1
            log_msg(f"Generated scan results: {scan_file} ({len(merged_commits)} total unique commits)")
        
        return files_created
    
    def generate_summary_report(self, extraction_time_s: float = 0.0) -> None:
        """Generate timelog_summary.md inside data/states/."""
        summary_file = self.states_dir / "timelog_summary.md"
        
        total_commits = len(self.commits)
        unique_tickets = len(set(c.get("ticket_id", "").strip() for c in self.commits if c.get("ticket_id", "").strip()))
        
        all_dates = list(self.by_date_repo_ticket.keys())
        date_range = f"{min(all_dates)} to {max(all_dates)}" if all_dates else "N/A"
        working_days = len(all_dates)
        total_logged_hours = working_days * 8.0
        
        ticket_stats = defaultdict(int)
        for commit in self.commits:
            ticket = commit.get("ticket_id", "").strip() or clean_commit_for_jira(commit.get("message", "")) or "General Work"
            ticket_stats[ticket] += 1
        
        top_tickets = sorted(ticket_stats.items(), key=lambda x: x[1], reverse=True)[:10]
        month_count = len(self.by_month)
        
        lines = [
            "# Time Log Generation Summary\n",
            f"**Generated**: {datetime.now().isoformat()}\n",
            "## Statistics",
            f"- **Total unique commits processed**: {total_commits}",
            f"- **Unique ticket IDs / features**: {unique_tickets}",
            f"- **Working days logged**: {working_days} days ({total_logged_hours:.0f} hours total @ 8h/day)",
            f"- **Weekend shift target**: {self.weekend_target.capitalize()}",
            f"- **Date range**: {date_range}",
            f"- **Processing time**: {extraction_time_s:.2f}s",
            "\n## Files Generated",
            f"- **Daily time logs**: `data/daily-summaries/YYYY-MM-daily-time-log.md` ({month_count} files)",
            f"- **Scan results**: `data/scan-results/YYYY-MM-scan-result.json` ({month_count} files)",
            f"- **State files**: `data/states/` (.extracted.json, .inferred.json, .timelog_last_run.json, timelog_summary.md)",
        ]
        
        if top_tickets:
            lines.append("\n## Top Tickets / Features")
            for ticket, count in top_tickets:
                lines.append(f"- **{ticket}**: {count} commits")
        
        lines.append("")
        
        with open(summary_file, "w") as f:
            f.write("\n".join(lines))
        
        log_msg(f"Generated summary report: {summary_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate delivery-focused daily time logs and scan results"
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to .inferred.json file"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Directory to write data/ files"
    )
    parser.add_argument(
        "--weekend-target",
        choices=["friday", "monday", "none", "separate", "keep"],
        default="monday",
        help="Where to shift Saturday/Sunday logs ('friday', 'monday', 'separate', or 'none')"
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="Optional path to log file"
    )
    
    args = parser.parse_args()
    log_msg(f"Reading processed commits from {args.input}", args.log)
    
    try:
        with open(args.input) as f:
            commits = json.load(f)
    except Exception as e:
        log_msg(f"ERROR: Failed to read {args.input}: {e}", args.log)
        sys.exit(1)
    
    if not commits:
        log_msg("No commits to process", args.log)
        sys.exit(0)
    
    processor = TimeLogProcessor(commits, args.output, weekend_target=args.weekend_target)
    processor.parse_commits()
    
    start_time = datetime.now()
    daily_count = processor.generate_daily_time_log()
    scan_count = processor.generate_scan_results()
    elapsed = (datetime.now() - start_time).total_seconds()
    processor.generate_summary_report(elapsed)
    
    log_msg(f"Report generation complete: {daily_count} daily logs + {scan_count} scan result files", args.log)


if __name__ == "__main__":
    main()
