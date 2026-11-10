---
name: timelog-generator
description: "Generate concise daily Git-based time logs or deterministic monthly Jira worklog CSVs, grouped by ticket or feature."
trigger: /timelog
---

# /timelog — Time Log Generator

Extracts Git history across project repositories and worktrees, deduplicates commits by SHA, groups work by ticket or feature, and normalizes each logged weekday to exactly 8h in 0.5h increments.

## Usage

```bash
/timelog                                                      # scan ~/projects/incyt for recent commits
/timelog <path>                                               # scan specific project folder
/timelog <path> --since <date>                                # scan commits since YYYY-MM-DD
/timelog <path> --since <date> --until <date>                # inclusive author-date range
/timelog <path> --author <name-or-email>                     # repeat for multiple Git identities
/timelog <path> --weekend-target <friday|monday>             # shift Saturday/Sunday logs
/timelog <path> --dry-run                                     # show what would be processed without writing
/timelog --jira-monthly                                       # export the current month's Jira worklogs as CSV
/timelog --jira-monthly --month 2026-07                       # export a specific month
```

## Default Incyt Scan Scope

For Git-based estimates, the default scan scope is explicitly configured through `TIMELOG_TARGETS` so the agent does not fall back to its current working directory and miss a project:

```bash
export TIMELOG_TARGETS="$HOME/projects/repo-a:$HOME/projects/repo-b"
```

`TIMELOG_TARGETS` is colon-delimited. The bundled `run.sh` uses the two paths above when the variable is unset; use repeated `--target <path>` flags to override the list for one run. Always state the exact folders and repositories found in the generated-report summary.

---

## Weekend Handling Rule (MANDATORY)

> [!IMPORTANT]
> **Agent Directive on Weekend Commits**:
> Whenever commits on weekends (Saturday or Sunday) are detected during a run, the agent **MUST ALWAYS ask the user** how they want weekend work allocated:
> - **Option 1**: Shift weekend logs to **Previous Friday**
> - **Option 2**: Shift weekend logs to **Following Monday**
>
> Pass `--weekend-target friday` or `--weekend-target monday` according to the user's choice. Weekend dates are not rendered as standalone headers; their tickets and work items roll into the chosen weekday.

---

## Directory Structure & Storage Constraints

All generated files and state data **MUST** be placed directly under the `data/` directory. The contents of `data/` are git-ignored with `.gitkeep` files preserved.

```
data/
├── daily-summaries/
│   ├── 2026-08-daily-time-log.md
│   └── 2026-07-daily-time-log.md
├── scan-results/
│   ├── 2026-08-scan-result.json
│   └── 2026-07-scan-result.json
├── monthly-worklogs/
│   └── 2026-08-jira-hour-log.csv
└── states/
    ├── .extracted.json
    ├── .inferred.json
    ├── .timelog_last_run.json
    ├── run.log
    └── timelog_summary.md
```

### Folder Responsibilities

1. **`data/daily-summaries/`**:
   - Contains monthly daily time logs named `YYYY-MM-daily-time-log.md`.
   - Formatted in plain English grouped by date (descending) with clean bullet points and quantized hours.

2. **`data/scan-results/`**:
   - Contains machine-readable scan data named `YYYY-MM-scan-result.json`.
   - Stores raw commit metadata, shas, authors, timestamps, and weekend mapping.

3. **`data/states/`**:
    - Used by the skill to store execution state, run logs, cached checkpoints, and summaries.

4. **`data/monthly-worklogs/`**:
   - Contains deterministic Jira exports named `YYYY-MM-jira-hour-log.csv`.
   - Each row is one worklog date and Jira issue, with that ticket's hours and the overall `daily_total_hours` for verification. Rows are ordered by date, then ticket.

---

## Monthly Jira Worklog CSV Branch

Use this branch when the user asks for a monthly Jira time, hour, or worklog report. It is separate from Git commit inference: Jira's recorded worklogs are the source of truth, and the default period is the current calendar month.

1. The exporter automatically loads Jira credentials from `.env` in the working directory or its parents, then from the skill directory, without overriding already-exported environment values. Use `JIRA_USER`, `JIRA_EMAIL`, `JIRA_ACCOUNT_ID`, or `JIRA_USERNAME` for the identity to match. Alternatively, pass an account ID, username, email, or display name with `--author`. Use `--env-file <path>` to select one exact file.
2. Run the deterministic exporter from this skill directory:

   ```bash
   python3 scripts/jira_monthly_worklog_csv.py
   # or: python3 scripts/jira_monthly_worklog_csv.py --month 2026-07 --author 'user@example.com'
   ```

   It first uses this narrow JQL to find candidate tickets, then reads each candidate's raw worklogs and sums only logs by the requested user whose `started` date falls in the target month:

   ```jql
   worklogAuthor = currentUser() AND worklogDate >= "YYYY-MM-01" AND worklogDate <= "YYYY-MM-DD"
   ```

3. Verify `data/monthly-worklogs/YYYY-MM-jira-hour-log.csv` exists, has the exact year and month in its filename, and includes only the requested author's worklogs. Check the `worklog_date` → `issue_key` grouping and report its path and the total of the `hours` column.

The installed Jira CLI exposes raw issue search (`jira issue list --jql ... --raw`) and raw issue views, but its worklog command only adds logs; it has no worklog-list command. The JQL-first flow therefore avoids fetching unrelated issues, and the exporter paginates issue-search results. It refuses to create a possibly incomplete report if Jira returns a truncated worklog list; use Jira's paginated `GET /rest/api/2/issue/{issueIdOrKey}/worklog` endpoint in that case.

---

## Daily Time Log Output Format (`data/daily-summaries/YYYY-MM-daily-time-log.md`)

The LLM and generator **MUST strictly adhere** to the following format.

### Format Rules

1. **Date Header**: a level-2 Markdown heading, `## Month DD, YYYY` (e.g. `## March 01, 2026`, `## August 14, 2026`), ordered **ascending** (chronological order, earlier day first). When generating reports for partial date ranges, existing date blocks are preserved and new estimations are merged and appended in order.
2. **Item Header**: a level-3 Markdown heading, `### <Ticket id> or <short summary>: <hours>h` (e.g. `### PROJ-1234: 5.5h`, `### PROJ-1020: 2h`, `### Device Simulation for Rain: 0.5h`). This makes both dates and work items available in the IDE document outline.
3. **Hour Quantization**:
   - Hours MUST always be multiples of at least half an hour (`0.5h`, `1h`, `1.5h`, `2h`, `2.5h`, `3h`, etc.). Non-half-hour fractions (like `2.6h`) are strictly forbidden.
   - Formatted as an integer when whole (`2h`, `4h`) or with one decimal when half (`2.5h`, `0.5h`).
   - Total hours per working day MUST equal exactly **8h**.
4. **Bullet Points**:
   - Indented by 2 spaces (`  - `).
   - Use one concise, outcome-focused sentence per item. State what became available, reliable, resolved, or delivered—not plans, records, baselines, or implementation steps.
5. **No Markdown Clutter**:
   - Use only the required level-2 date and level-3 item headings—no document title, hourly subtotal tables, or commit count annotations.
   - Blank lines separate ticket items and dates.

### Reference Template

```markdown
## March 08, 2026
### <Ticket id> or <short summary if ticket id not available>: 2h
  - work summary in plain english, preferably in bullet points +/- 3 points

### <Ticket id> or <short summary if ticket id not available>: 4h
  - work summary in plain english focus on user story, preferably in bullet points +/- 3 points

## March 07, 2026
### <Ticket id> or <short summary if ticket id not available>: 3h
  - work summary in plain english, preferably in bullet points +/- 3 points

### <Ticket id> or <short summary if ticket id not available>: 3h
  - work summary in plain english focus on user story, preferably in bullet points +/- 3 points
```

### Example Concrete Output

```markdown
## August 14, 2026
### PROJ-1234: 4h
  - Added shortlist evaluation for missing feature finder based on repo patterns
  - Streamlined work queue transition rules for automated parity reviews

### Consolidate TankFillSourceSummary icon-only into compact mode: 1.5h
  - Consolidated TankFillSourceSummary icon-only interfaces into shared UI kit components
  - Fixed SVG alignment and responsive text rendering

### PROJ-1001: 1h
  - Documented verification baseline context and learnings for web port #2596
  - Validated test suite passes against staging worktree

### PROJ-1002: 0.5h
  - Restricted MatrixLayout upTo rules and responsive breakpoints across device screens

### PROJ-1003: 0.5h
  - Adapted matrix layout density for compact screen widths down to 264px

### PROJ-1004: 0.5h
  - Refactored icon-only interface props across status widgets
```

---

## Execution Instructions

When running the skill:

1. **Scope and authors**:
   - Scan the requested workspace root so nested repositories and worktrees are included. Do not scan only the current repository unless that is the requested scope.
   - Inspect date-range commit authors before generating the report. If the same person uses multiple Git identities, pass each via a repeated `--author` argument. Prefer a shared author name when it reliably covers the identities.
   - Date boundaries are inclusive and use the commit author date—the same date used by the report.
2. **Weekend Check & Prompt**:
   - Detect weekend commits using author dates. If any exist, ask the user whether to shift them to Previous Friday or Following Monday before generating output.
3. **Execute Orchestrator**:
   ```bash
   TIMELOG_TARGETS="$HOME/projects/repo-a:$HOME/projects/repo-b" \
     bash run.sh --since <YYYY-MM-DD> --until '<YYYY-MM-DD 23:59:59>' \
     --author '<name-or-email>' --weekend-target <friday|monday>
   ```
   Repeat `--author` for each separate identity. The extractor deduplicates commits appearing in the main checkout and any worktree.
4. **Verify Generated Files**:
   - Check `data/daily-summaries/YYYY-MM-daily-time-log.md`
   - Check `data/scan-results/YYYY-MM-scan-result.json`
   - Check `data/states/timelog_summary.md`
   - Verify every rendered weekday totals exactly 8h and no weekend date appears as a standalone header.
   - Review the daily log for concise outcome wording; revise the generated Markdown when a raw commit summary still describes steps rather than an outcome.
5. **Display Summary**: State the exact workspace roots scanned, identities used, total hours, and provide links to the generated files in `data/`.
