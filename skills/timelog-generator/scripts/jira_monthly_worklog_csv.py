#!/usr/bin/env python3
"""Create a deterministic daily, per-ticket Jira worklog CSV for one calendar month."""

from __future__ import annotations

import argparse
import base64
import calendar
import csv
import json
import os
import subprocess
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def run_jira(*args: str) -> Any:
    """Run jira-cli and decode its --raw JSON output."""
    try:
        result = subprocess.run(
            ["jira", *args], check=True, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError("`jira` CLI is not installed or is not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or exc.stdout.strip()) from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("jira-cli did not return valid JSON for a --raw request.") from exc


def month_bounds(value: str) -> tuple[date, date]:
    try:
        year, month = map(int, value.split("-"))
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("month must be YYYY-MM")


def issue_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return payload.get("issues", [])
    if isinstance(payload, list):
        return payload
    raise RuntimeError("Unexpected JSON shape from `jira issue list --raw`.")


def list_candidates(jql: str) -> list[dict[str, Any]]:
    """Read every jira-cli issue-search page without relying on its default page size."""
    candidates: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = issue_rows(
            run_jira("issue", "list", "--jql", jql, "--raw", "--paginate", f"{offset}:100")
        )
        candidates.extend(page)
        if len(page) < 100:
            return candidates
        offset += len(page)


def worklog_text(value: Any) -> str:
    """Extract plain text from a Jira worklog comment.

    Jira Cloud may return a legacy string or an Atlassian Document Format value,
    depending on the endpoint and Jira configuration.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(worklog_text(item) for item in value)
    if not isinstance(value, dict):
        return ""

    text = value.get("text")
    if isinstance(text, str):
        return text

    content = value.get("content")
    if isinstance(content, list):
        separator = "\n" if value.get("type") == "doc" else ""
        return separator.join(filter(None, (worklog_text(item) for item in content)))

    # Some Jira CLI/API variants wrap the comment content in one of these keys.
    for key in ("value", "body"):
        if key in value:
            return worklog_text(value[key])
    return ""


def author_matches(author: dict[str, Any], expected: str) -> bool:
    expected = expected.casefold()
    return any(
        str(author.get(key) or "").casefold() == expected
        for key in ("accountId", "emailAddress", "name", "key", "displayName")
    )


def jira_credentials() -> tuple[str, str] | None:
    """Resolve Jira base URL and basic auth credentials from env or jira-cli config."""
    server = (os.environ.get("JIRA_SERVER") or "").rstrip("/")
    token = os.environ.get("JIRA_API_TOKEN") or ""
    user = os.environ.get("JIRA_USER") or os.environ.get("JIRA_EMAIL") or ""

    if not (server and user):
        cfg_path = Path.home() / ".config" / ".jira" / ".config.yml"
        if cfg_path.is_file():
            for line in cfg_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("server:") and not server:
                    server = line.split(":", 1)[1].strip().rstrip("/")
                elif line.startswith("login:") and not user:
                    user = line.split(":", 1)[1].strip()

    if server and token and user:
        auth_bytes = f"{user}:{token}".encode("utf-8")
        return server, base64.b64encode(auth_bytes).decode("ascii")
    return None


def fetch_issue_worklogs(
    key: str,
    start: date,
    end: date,
    creds: tuple[str, str] | None,
) -> list[dict[str, Any]]:
    """Retrieve worklogs for an issue.

    Uses Jira REST API's startedAfter/startedBefore date-range query parameters
    when credentials are available, avoiding loading hundreds of historical logs.
    Falls back to `jira issue view` if REST credentials are not found.
    """
    if creds:
        server, auth_header = creds
        # Buffer by 1 day before start and 2 days after end to safely cover all timezone offsets
        start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc) - timedelta(days=1)
        end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=2)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)

        worklogs: list[dict[str, Any]] = []
        start_at = 0
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Accept": "application/json",
        }
        while True:
            url = (
                f"{server}/rest/api/3/issue/{key}/worklog"
                f"?startedAfter={start_ms}&startedBefore={end_ms}&startAt={start_at}&maxResults=1000"
            )
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                raise RuntimeError(f"Failed to fetch worklogs for {key} from Jira REST API: {exc}") from exc
            page = data.get("worklogs", [])
            worklogs.extend(page)
            total = data.get("total", len(worklogs))
            if len(worklogs) >= total or not page:
                break
            start_at += len(page)
        return worklogs

    # Fallback to jira-cli if REST credentials are not provided
    issue = run_jira("issue", "view", str(key), "--raw")
    fields = issue.get("fields", {})
    worklog_field = fields.get("worklog", {})
    worklogs = worklog_field.get("worklogs", [])
    total = worklog_field.get("total", len(worklogs))
    if total > len(worklogs):
        raise RuntimeError(
            f"{key} has {total} worklogs but jira-cli returned only {len(worklogs)}. "
            "Set JIRA_API_TOKEN and JIRA_SERVER to enable direct date-filtered worklog querying."
        )
    return worklogs


def load_env(env_path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding explicitly exported values."""
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("'\""))


def env_paths(explicit_path: str | None) -> list[Path]:
    """Find conventional .env locations while keeping an explicit file first."""
    if explicit_path:
        return [Path(explicit_path).expanduser()]

    paths: list[Path] = []
    current = Path.cwd().resolve()
    paths.append(current / ".env")
    paths.extend(parent / ".env" for parent in current.parents)
    skill_dir = Path(__file__).resolve().parent.parent
    paths.extend((skill_dir / ".env", skill_dir.parent / ".env"))

    seen: set[Path] = set()
    return [path for path in paths if not (path in seen or seen.add(path))]


def parse_args() -> argparse.Namespace:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default=f"{today:%Y-%m}", help="Calendar month (YYYY-MM); defaults to current month.")
    parser.add_argument(
        "--author",
        help="Jira account ID, username, email, or display name. Defaults to JIRA_USER from the environment or .env.",
    )
    parser.add_argument(
        "--env-file",
        help="Credentials file to load before running. Defaults to auto-discovered .env files.",
    )
    parser.add_argument(
        "--output",
        help="CSV destination. Defaults to data/monthly-worklogs/YYYY-MM-jira-hour-log.csv.",
    )
    return parser.parse_args()


def configured_author() -> str | None:
    """Accept the common names used for the Jira identity in .env files."""
    for key in ("JIRA_USER", "JIRA_EMAIL", "JIRA_ACCOUNT_ID", "JIRA_USERNAME"):
        value = os.environ.get(key)
        if value:
            return value
    return None


def main() -> int:
    args = parse_args()
    for env_path in env_paths(args.env_file):
        load_env(env_path)
    author = args.author or configured_author()
    if not author:
        raise SystemExit(
            "No Jira user identity found. Add JIRA_USER (or JIRA_EMAIL, JIRA_ACCOUNT_ID, or JIRA_USERNAME) "
            "to .env, or pass --author."
        )
    skill_dir = Path(__file__).resolve().parent.parent
    start, end = month_bounds(args.month)
    output = Path(args.output) if args.output else (skill_dir / "data" / "monthly-worklogs" / f"{args.month}-jira-hour-log.csv")
    if not output.is_absolute():
        output = (skill_dir / output).resolve()
    if not output.name.startswith(f"{args.month}-"):
        raise SystemExit("Output filename must start with YYYY-MM to identify the report month.")

    # JQL searches across all projects and filters to issues with this user's logs this month.
    author_clause = f'worklogAuthor = "{author}"' if author and author != "currentUser()" else "worklogAuthor = currentUser()"
    jql = f'project IS NOT EMPTY AND {author_clause} AND worklogDate >= "{start:%Y-%m-%d}" AND worklogDate <= "{end:%Y-%m-%d}"'
    candidates = list_candidates(jql)
    creds = jira_credentials()

    # Aggregate by calendar day first, then ticket, so the CSV can be reviewed day by day.
    totals: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"seconds": 0, "worklog_count": 0, "summaries": []}
    )
    for candidate in candidates:
        key = candidate.get("key")
        if not key:
            continue
        worklogs = fetch_issue_worklogs(str(key), start, end, creds)
        for worklog in worklogs:
            started = str(worklog.get("started") or "")[:10]
            if not (start.isoformat() <= started <= end.isoformat()):
                continue
            if not author_matches(worklog.get("author") or {}, author):
                continue
            group = totals[(started, str(key))]
            summary = worklog_text(worklog.get("comment") or worklog.get("body"))
            if summary and summary not in group["summaries"]:
                group["summaries"].append(summary)
            group["seconds"] += int(worklog.get("timeSpentSeconds") or 0)
            group["worklog_count"] += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    daily_totals: dict[str, int] = defaultdict(int)
    for (worklog_date, _), total in totals.items():
        daily_totals[worklog_date] += total["seconds"]

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "worklog_date",
                "daily_total_hours",
                "issue_key",
                "summary",
                "hours",
            ],
        )
        writer.writeheader()
        for worklog_date, key in sorted(totals):
            total = totals[(worklog_date, key)]
            if not total["seconds"]:
                continue
            writer.writerow({
                "worklog_date": worklog_date,
                "daily_total_hours": f"{daily_totals[worklog_date] / 3600:.2f}",
                "issue_key": key,
                # A row may aggregate several worklogs for one ticket and day.
                "summary": "\n".join(total["summaries"]),
                "hours": f"{total['seconds'] / 3600:.2f}",
            })
    rows = sum(1 for data in totals.values() if data["seconds"])
    print(f"Generated {output} ({rows} daily ticket rows).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        raise SystemExit(f"Error: {exc}")
