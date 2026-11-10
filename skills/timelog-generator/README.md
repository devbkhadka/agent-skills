# Time Log Generator Skill

Automatically generate time-log data from git commit history. Extracts ticket IDs, estimates hours, infers features, and produces organized markdown reports by month and repository.

## Quick Start

```bash
# Scan ~/projects/incyt (default) and generate time logs
/timelog

# Scan a specific path
/timelog ~/my/other/projects

# Scan commits from a date range
/timelog ~/projects/incyt --since 2024-01-01 --until 2024-02-01

# Force full rescan (ignore cache)
/timelog ~/projects/incyt --force

# Test without generating files
/timelog ~/projects/incyt --dry-run
```

## What Gets Generated

### 1. Monthly Summary Files (`data/daily-summaries/YYYY-MM.md`)

Organized by date and ticket ID:

```markdown
# Time Log — November 2024

## 2024-11-15 (Friday)

**Total: 8.0 hours**

### PROJ-123 Implement feature X
- **Hours**: 4.0
- **Summary**: Added new API endpoint and unit tests
- **Commits**: 3 commits

### PROJ-124 Fix dashboard sorting
- **Hours**: 2.5
- **Summary**: Fixed data table sort order
- **Commits**: 1 commit

### Database optimization (no ticket)
- **Hours**: 1.5
- **Summary**: Optimized query performance
- **Commits**: 2 commits
```

### 2. Daily Time Log Files (`data/daily-summaries/YYYY-MM-daily-time-log.md`)

Feature-aggregated daily breakdown:

```markdown
# Daily Time Log — November 2024

## 2024-11-15 (Friday)

**Total: 8.0 hours**

**PROJ-123 Implement feature X** — Added new API endpoint and unit tests
- 4.0 hours • 3 commits

**PROJ-124 Fix dashboard sorting** — Fixed data table sort order
- 2.5 hours • 1 commit

**Database optimization** — Optimized query performance
- 1.5 hours • 2 commits
```

### 3. Scan Results (`data/scan-results/YYYY-MM-scan-result.json`)

Machine-readable JSON commit data for programmatic processing.

### 4. Summary Report (`timelog_summary.md`)

Overall statistics and file listing.

## How It Works

### Stage 1: Extraction (Deterministic)

The extraction stage (`timelog_extract.sh`) scans for git repositories and performs:

1. **Repository Discovery** — Find all `.git` folders under the target path
2. **Change Detection** — Compare repo modification time against last-run cache
3. **Commit Extraction** — Use `git log` to get all commits with:
   - Author, date, message
   - Changed files and line counts
   - Branch name
4. **Ticket ID Parsing** — Extract Jira IDs from:
   - Branch names: `feature/PROJ-123-description`
   - First line of commit: `PROJ-123 Fixed the thing`
   - Any line in commit message
5. **Hour Estimation** — Apply deterministic heuristic based on:
   - Files changed (1-2x multiplier)
   - Lines added/removed (1-1.5x multiplier)
   - Commit message length (complexity factor)
   - Merge commit detection (0.1x multiplier)

**Estimation Formula:**

```
base_hours = 0.5
files_factor = min(changed_files / 5, 2.0)
lines_factor = min((added + removed) / 400, 1.5)
merge_factor = 0.1 if is_merge else 1.0
complexity_factor = 1.2 if msg_len > 100 else (0.7 if msg_len < 20 else 1.0)

estimated_hours = base_hours * files_factor * lines_factor * complexity_factor * merge_factor
clamped = clamp(estimated_hours, 0.25, 8.0)
```

**Output:** `timelog-out/.extracted.json` — Array of commit objects with estimated hours.

### Stage 2: Feature Inference (LLM)

The inference stage (`timelog_infer.py`) processes commits without ticket IDs:

1. **Filter Ungrouped** — Find commits where `ticket_id` is empty
2. **LLM Processing** — For each ungrouped commit:
   - Send commit message to Claude
   - Request 2-5 word feature name (e.g., "authentication refactor")
   - Store as `feature/lowercase-name`
3. **Preserve Tickets** — Commits with ticket IDs pass through unchanged

**Why LLM here?** Inferring meaningful feature groups from raw commits requires semantic understanding. The deterministic stage can't do this reliably.

**Output:** `timelog-out/.inferred.json` — Updated commit array with all ticket_ids populated.

### Stage 3: Report Generation (Deterministic)

The reporting stage (`timelog_report.py`) produces final markdown:

1. **Parse Commits** — Organize by (date, ticket), (repo, date), and (month)
2. **Daily Hour Allocation** — For each calendar day:
   - Collect all tickets worked that day
   - Sum their estimated hours
   - If total > 8: normalize down proportionally
   - If total ≤ 8: leave as-is (partial days are valid)
3. **Generate Monthly Files** — Group by date, list tickets per day
4. **Generate Daily Files** — Per-repo, commit-level details
5. **Generate Summary** — Statistics and file listing

**Why deterministic?** All time allocation and grouping must be reproducible. No randomness, no LLM inconsistency.

**Outputs:**
- `timelog-out/data/daily-summaries/YYYY-MM.md` — Monthly summary file
- `timelog-out/data/daily-summaries/YYYY-MM-daily-time-log.md` — Daily time log aggregated by feature
- `timelog-out/data/scan-results/YYYY-MM-scan-result.json` — Machine-readable scan data
- `timelog-out/data/states/` — State and cache directory
- `timelog-out/timelog_summary.md` — Overall report

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Skill Invocation                                        │
│ /timelog [path] [--since DATE] [--until DATE] [--force] │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
        ┌────────────────────────┐
        │ Stage 1: Extraction    │
        │ (timelog_extract.sh)   │
        │ DETERMINISTIC          │
        └────────┬───────────────┘
                 │
    Finds repos, extracts commits, parses tickets,
    estimates hours using heuristics
                 │
                 ▼
        .extracted.json
        [
          {
            "sha": "abc123...",
            "branch": "feature/PROJ-123-impl",
            "date": "2024-11-15T14:30:45+00:00",
            "message": "Implement feature X",
            "ticket_id": "PROJ-123",
            "files_changed": 3,
            "lines_added": 45,
            "lines_removed": 0,
            "estimated_hours": 1.2,
            "repository": "my-project",
            ...
          },
          ...
        ]
                 │
                 ▼
        ┌────────────────────────┐
        │ Stage 2: Inference     │
        │ (timelog_infer.py)     │
        │ LLM (Claude)           │
        └────────┬───────────────┘
                 │
    For commits with empty ticket_id:
    Send message to Claude → get feature name
                 │
                 ▼
        .inferred.json (same structure, all ticket_ids filled)
                 │
                 ▼
        ┌────────────────────────┐
        │ Stage 3: Reporting     │
        │ (timelog_report.py)    │
        │ DETERMINISTIC          │
        └────────┬───────────────┘
                 │
    Organize by date/ticket, allocate 8h/day,
    generate markdown reports & scan results
                 │
                 ▼
    timelog-out/
    ├── data/
    │   ├── daily-summaries/
    │   │   ├── YYYY-MM.md (monthly summary)
    │   │   └── YYYY-MM-daily-time-log.md (daily time log)
    │   ├── scan-results/
    │   │   └── YYYY-MM-scan-result.json (raw commit data)
    │   └── states/ (run state and cache)
    ├── timelog_summary.md
    ├── .extracted.json
    ├── .inferred.json
    ├── .timelog_last_run.json (cache)
    └── run.log
```

## Configuration & Caching

### Last-Run Cache

Stored in `timelog-out/.timelog_last_run.json`:

```json
{
  "last_run": "2024-11-20T14:30:45.123456",
  "repos": {
    "/Users/dev/projects/incyt/my-project": 1731509445.123456,
    "/Users/dev/projects/incyt/other-service": 1731509445.123456
  }
}
```

**How it works:**
1. Track the modification time (mtime) of each `.git/` folder
2. On next run, check if mtime has changed
3. If unchanged and `--force` not passed, skip that repo
4. Update cache after successful processing

**To force full rescan:**
```bash
/timelog ~/projects/incyt --force
```

### Environment Variables

```bash
ANTHROPIC_API_KEY=sk-...     # For feature inference (required if commits lack tickets)
TARGET_PATH=~/my/projects    # Override default path
OUTPUT_DIR=./timelog-out     # Override output location
SINCE_DATE=2024-01-01        # Filter commits from date
UNTIL_DATE=2024-02-01        # Filter commits until date
```

## Examples

### Example 1: Scan incyt project

```bash
$ /timelog
Scanning ~/projects/incyt for git repositories...
Extracted: 47 commits from 3 repos
Inferred features for 12 commits
Generating reports...

# Time Log Generation Summary

**Generated**: 2024-11-20T14:30:45

## Statistics
- **Total commits processed**: 47
- **Repositories scanned**: 3
- **Date range**: 2024-11-15 to 2024-11-20
- **Total hours logged**: 240.0

## Files Generated
- **Monthly summaries**: 1 file
- **Daily time logs**: 1 file
- **Scan results**: 1 file

Generated files:
  - 2024-11.md
  - 2024-11-daily-time-log.md
  - scan: 2024-11-scan-result.json
```

### Example 2: Narrow time range

```bash
/timelog ~/projects/incyt --since 2024-11-01 --until 2024-11-15
```

Only processes commits between these dates.

### Example 3: Dry-run preview

```bash
/timelog ~/projects/incyt --dry-run
```

Shows what would be generated without writing files. Useful for testing parameters.

## Hour Estimation Examples

| Commits | Files | Lines | Message | Est. Hours | Notes |
|---------|-------|-------|---------|------------|-------|
| Simple fix | 1 | 5 added | "Fix typo" | 0.25 | Minimum clamp |
| Feature impl | 5 | 200 added | Detailed msg | 1.8 | Normal case |
| Large refactor | 12 | 500+ lines | Multi-line | 8.0 | Clamped max |
| Merge commit | 0 | 0 | "Merge branch" | 0.1 | Low effort |

## Troubleshooting

### No commits found

```bash
# Check if repos exist
ls -la ~/projects/incyt/*/git

# Try wider time range
/timelog ~/projects/incyt --since 2024-01-01

# Force full rescan
/timelog ~/projects/incyt --force

# Check log
cat timelog-out/run.log
```

### Wrong ticket IDs

Verify your branch naming or commit message format:
- Branch: `feature/PROJ-123-description`
- Commit: `PROJ-123 Fixed the thing` or `Fixed thing [PROJ-123]`

Check extracted data to see what was parsed:
```bash
jq '.[] | {branch, ticket_id, message}' timelog-out/.extracted.json
```

### Hours look wrong

Review generated daily time log or scan JSON data:
```bash
cat timelog-out/data/daily-summaries/2024-11-daily-time-log.md
```

Check the heuristic formula in "Stage 1: Extraction" above.

### Inference errors (missing ANTHROPIC_API_KEY)

Feature inference requires Claude. Set the API key:
```bash
export ANTHROPIC_API_KEY=sk-...
/timelog ~/projects/incyt
```

If not set, the skill will error on ungrouped commits. Use `--force` to reprocess with a valid key.

## Validation & Output

All reports are deterministic. Running the same command twice on unchanged repos produces identical output.

The 8-hour-per-day allocation is normalized to never exceed 8 hours per date (except in partial days).

## Implementation Notes

- **Git Parsing** — Uses `git log --all --pretty=format` and `git diff-tree` for line counts
- **Ticket Extraction** — Regex pattern: `[A-Z][A-Z0-9]*-[0-9]+` (matches PROJ-123, ABC-456, etc.)
- **Hour Clamping** — Individual commits clamped to [0.25, 8.0]; daily totals normalized to ≤ 8
- **Performance** — Scales to 100+ commits per repo; extraction ~1-2s per repo, inference ~0.1s per ungrouped commit
- **Parallelism** — Currently sequential (one repo at a time); could parallelize extraction in future
