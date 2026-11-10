# Improvements Implemented

Version 1.1.0 — Enhanced extraction and reporting

## 1. User Filtering

**Feature:** Only extract commits from specific developer  
**Location:** `timelog_extract.py`, line 18

```python
TARGET_AUTHOR_EMAIL = "developer@example.com"  # Only extract commits by this developer
```

**Impact:**
- Only commits matching `TARGET_AUTHOR_EMAIL` (e.g. `developer@example.com`) are extracted
- All other developers' commits are ignored
- Filter applied automatically via `git log --author=...`
- Easy to customize: change constant to filter for different developer

**Before:** Mixed commits from all developers  
**After:** Only dev.k's work included in time logs

---

## 2. Daily Hours Allocation

**Feature:** Each day totals exactly 8 hours (unless partial day)  
**Location:** `timelog_report.py`, method `allocate_daily_hours()`

**Algorithm:**
- Collect all tickets/features worked on each day
- Sum their estimated hours
- If total ≤ 8: use as-is (partial days valid, not forced to 8)
- If total > 8: scale down proportionally to exactly 8.0

**Example:**
```
Day has work on: PROJ-123 (6h) + PROJ-124 (6h) = 12 hours
→ Scale factor = 8/12 = 0.667
→ Allocated: PROJ-123 (4.0h) + PROJ-124 (4.0h)
→ Total: exactly 8.0 hours
```

**Impact:**
- Fair time allocation across concurrent work
- Never exceeds 8 hours per day
- Preserves proportions when normalization needed
- Partial days remain under 8h (e.g., 2-hour day stays 2 hours)

---

## 3. Unified Daily Report

**Feature:** Single monthly daily time log (aggregated by feature)  
**Output:** `data/daily-summaries/YYYY-MM-daily-time-log.md`

**Previous:** Separate `daily/REPO_NAME.md` for each repository (10+ files)  
**Now:** Single file per month in `data/daily-summaries/` with all features aggregated

**Format:**
```markdown
# Daily Time Log — August 2026

## 2026-08-15 (Friday)

**Total: 8.0 hours**

**PROJ-123 — Implement feature X**
- 4.0 hours • 3 commits

**PROJ-124 — Bug fix in dashboard**  
- 2.5 hours • 1 commit

**Database optimization**
- 1.5 hours • 2 commits
```

**Impact:**
- Clean, compact daily overview
- Features sorted by hours (highest first)
- Plain English descriptions
- Maximum ~5 features per day = easy to read
- Complements detailed monthly report

---

## 4. JSON Scan Results

**Feature:** Machine-readable commit data for programmatic processing  
**Output:** `data/scan-results/YYYY-MM-scan-result.json`

**Format:**
```json
{
  "month": "2026-08",
  "generated": "2026-08-14T20:35:00.000000",
  "total_commits": 898,
  "date_range": "2026-08-05 to 2026-08-14",
  "commits": [
    {
      "sha": "...",
      "branch": "...",
      "date": "2026-08-14T17:42:24+05:45",
      "message": "...",
      "ticket_id": "PROJ-1234",
      "files_changed": 5,
      "lines_added": 127,
      "estimated_hours": 2.1,
      "repository": "auth-service"
    },
    ...
  ]
}
```

**Use Cases:**
- Parse and filter data with jq or Python
- Export to database or BI tools
- Generate custom reports
- Track metrics over time
- Integrate with other tools

**Impact:**
- Easy integration with external systems
- No need to parse markdown
- Full commit metadata available
- Timestamped for reproducibility

---

## 5. Improved Directory and File Organization

**Feature:** Structured data organization into dedicated subdirectories  
**Naming & Folder Convention:**

| Purpose | Location | Example |
|---------|----------|---------|
| Monthly summary (by ticket) | `data/daily-summaries/YYYY-MM.md` | `data/daily-summaries/2026-08.md` |
| Daily time log (by feature) | `data/daily-summaries/YYYY-MM-daily-time-log.md` | `data/daily-summaries/2026-08-daily-time-log.md` |
| Scan results (JSON) | `data/scan-results/YYYY-MM-scan-result.json` | `data/scan-results/2026-08-scan-result.json` |
| State storage | `data/states/` | `data/states/` |

**Before:**  
- `examples/trial-run/2026-08.md`
- `examples/trial-run/daily/*.md` (10+ per-repo files)

**After:**
- Removed `examples/trial-run`
- `data/daily-summaries/` — Monthly summaries and daily time logs
- `data/scan-results/` — Machine-readable scan JSON results
- `data/states/` — Run state and cache storage

---

## Summary of Changes

| Aspect | Before | After |
|--------|--------|-------|
| User filter | None (all commits) | `TARGET_AUTHOR_EMAIL` constant |
| Daily total | Varies per day | Exactly 8 hours (or partial) |
| Directory layout | `examples/trial-run/daily/*.md` | `data/` (`daily-summaries/`, `scan-results/`, `states/`) |
| Daily reports | 10+ files per month | 1 daily time log per month |
| Daily format | Markdown (commit detail) | Markdown (feature aggregate) |
| Scan data | None | JSON format in `data/scan-results/` |
| File naming | Mixed | Consistent convention |
| Daily descriptions | Commit messages | Plain English summaries |

---

## Configuration

### Change Target Developer

Edit `timelog_extract.py`:

```python
# Line 18
TARGET_AUTHOR_EMAIL = "developer@example.com"
# Change to your email, e.g.:
# TARGET_AUTHOR_EMAIL = "john@example.com"
```

### Change Daily Hours Cap

Edit `timelog_report.py`, method `allocate_daily_hours()`:

```python
# Current: 8.0 hours
# Change to: 9.0 (or any other value)
if total <= 9.0:
    return tickets_hours
scale_factor = 9.0 / total
```

---

## Testing the Changes

```bash
cd /Users/devkhadka/projects/agent-skills/timelog-generator

# Run with improvements
bash run.sh --target ~/projects/incyt --output ./test-output --since 2026-08-07

# View results
cat test-output/data/daily-summaries/2026-08.md                        # Monthly summary
cat test-output/data/daily-summaries/2026-08-daily-time-log.md         # Daily aggregate
cat test-output/data/scan-results/2026-08-scan-result.json | jq '.'   # JSON data

# Verify daily totals
jq '.commits[] | select(.date | startswith("2026-08-14")) | .estimated_hours' test-output/data/scan-results/2026-08-scan-result.json | jq -s add
```

---

## Migration from v1.0.0

Replaced legacy `examples/trial-run/` with structured `data/` directories:

1. **Removed:** `examples/trial-run/` directory and per-repo daily markdown files.
2. **Replaced with:** `data/` folder containing:
   - `data/daily-summaries/` — For monthly summary (`YYYY-MM.md`) and aggregated daily time logs (`YYYY-MM-daily-time-log.md`)
   - `data/scan-results/` — For programmatic JSON scan results (`YYYY-MM-scan-result.json`)
   - `data/states/` — For persistent run states and caches
3. **Run new extraction:** `bash run.sh --target ~/projects/incyt`

No data loss — new format adds capabilities without removing information.

---

## Next Improvements (Future)

- [ ] Weekly rollups (aggregate multiple months)
- [ ] Team reports (combine multiple developers)
- [ ] Burndown charts (track hours by milestone)
- [ ] Custom filters (exclude certain repos/branches)
- [ ] CSV export from JSON data
- [ ] Email summaries (send daily logs automatically)
