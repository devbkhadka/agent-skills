# Time Log Generator — Integration Guide

This document explains how the skill integrates with the Claude AI system and how to use it end-to-end.

## Skill Registration

The skill is registered in `~/.claude/skills/timelog-generator/` with a standard SKILL.md manifest:

```yaml
---
name: timelog-generator
description: "Analyze git commits..."
trigger: /timelog
---
```

When you type `/timelog` in any conversation, Claude will:
1. Read this SKILL.md file
2. Follow the step-by-step instructions
3. Execute bash, Python, and tool commands to generate time-log data

## How to Invoke

### Basic Usage

```
/timelog
```

Scans `~/projects/incyt` (default) and generates reports.

### With Arguments

```
/timelog ~/my/projects --since 2024-01-01 --until 2024-02-01 --force --dry-run
```

Arguments are parsed by Claude from the natural language and passed to the skill scripts.

## Step-by-Step Execution

When you invoke `/timelog`, Claude will execute these steps in order:

### Step 1: Initialize

```bash
mkdir -p timelog-out
# Validate git, jq, python3 are installed
# Check target path exists
```

**Expected output:** `Scanning ~/projects/incyt for git repositories...`

### Step 2: Run Extraction

```bash
bash skill://timelog-generator/timelog_extract.sh \
  --target ~/projects/incyt \
  --output timelog-out/.extracted.json \
  --log timelog-out/run.log
```

**What happens:**
- Finds all `.git` directories
- Extracts commits using `git log` and `git diff-tree`
- Parses ticket IDs from branch names and messages
- Estimates hours using the heuristic formula
- Outputs JSON to `.extracted.json`

**Expected output:** `EXTRACTED: 47 commits from 3 repos`

### Step 3: Run Inference

```bash
python3 skill://timelog-generator/timelog_infer.py \
  --input timelog-out/.extracted.json \
  --output timelog-out/.inferred.json \
  --log timelog-out/run.log
```

**What happens:**
- Loads extracted commits
- Finds commits with empty `ticket_id`
- Uses Claude API to infer feature names
- Writes updated JSON to `.inferred.json`

**Expected output:** `Inferred features for 12 commits` (or `All commits have ticket IDs` if none needed)

### Step 4: Generate Reports

```bash
python3 skill://timelog-generator/timelog_report.py \
  --input timelog-out/.inferred.json \
  --output timelog-out \
  --log timelog-out/run.log
```

**What happens:**
- Parses all commits
- Groups by (date, ticket) and (feature, date) and (month)
- Allocates 8 hours per day among tickets (normalized if needed)
- Writes monthly markdown summaries in `data/daily-summaries/`
- Writes daily feature-aggregated time logs in `data/daily-summaries/`
- Writes JSON scan results in `data/scan-results/`
- Writes summary report

**Expected output:** 
```
Generated timelog-out/data/daily-summaries/2024-11.md (15 entries, 120.0 hours)
Generated timelog-out/data/daily-summaries/2024-11-daily-time-log.md
Generated timelog-out/data/scan-results/2024-11-scan-result.json (47 commits)
Generated timelog-out/timelog_summary.md
```

### Step 5: Display Summary

Claude reads and displays the generated summary report to you.

```
# Time Log Generation Summary

**Generated**: 2024-11-20 14:30:45

## Statistics
- **Total commits processed**: 47
- **Repositories scanned**: 3
- **Date range**: 2024-11-15 to 2024-11-20
- **Total hours logged**: 240.0
```

## Output Files

After running, you'll have:

```
timelog-out/
├── data/
│   ├── daily-summaries/
│   │   ├── 2024-11.md                 # Monthly summary (by date & ticket)
│   │   └── 2024-11-daily-time-log.md  # Daily feature-aggregated time log
│   ├── scan-results/
│   │   └── 2024-11-scan-result.json   # Machine-readable commit scan data
│   └── states/                        # State and cache storage
├── timelog_summary.md                 # Overall statistics
├── run.log                            # Execution log
├── .extracted.json                    # Raw extracted data (for debugging)
├── .inferred.json                     # Processed data (for debugging)
└── .timelog_last_run.json             # Cache for next run
```

## Reading the Output

### Monthly File (e.g., `data/daily-summaries/2024-11.md`)

```markdown
# Time Log — November 2024

## 2024-11-15 (Friday)

**Total: 8.0 hours**

### PROJ-123 Implement feature X
- **Hours**: 4.0
- **Summary**: Added new API endpoint and unit tests. Refactored auth middleware.
- **Commits**: 3 commits

### PROJ-124 Bug fix in dashboard
- **Hours**: 2.5
- **Summary**: Fixed sorting issue in data table, improved performance.
- **Commits**: 1 commit

### Database optimization (no ticket)
- **Hours**: 1.5
- **Summary**: Updated dependencies and documentation.
- **Commits**: 2 commits

---

## 2024-11-16 (Saturday)

**Total: 8.0 hours**
...
```

**How to read:**
- Each day lists all tickets worked that day
- Hours shown are normalized to fit within 8 hours/day
- Summaries are auto-generated from commit messages
- No ticket ID? The feature name was inferred by Claude

### Daily Time Log (e.g., `data/daily-summaries/2024-11-daily-time-log.md`)

```markdown
# Daily Time Log — November 2024

## 2024-11-15 (Friday)

**Total: 8.0 hours**

**PROJ-123 Implement feature X** — Added new API endpoint and unit tests
- 4.0 hours • 3 commits

**PROJ-124 Bug fix in dashboard** — Fixed sorting issue in data table
- 2.5 hours • 1 commit

**Database optimization** — Updated dependencies and documentation
- 1.5 hours • 2 commits
```

**How to read:**
- Summarizes total daily effort aggregated by feature
- Clean plain English descriptions with commit counts
- Features sorted by highest effort descending

## Advanced Usage

### Narrow the Time Range

```
/timelog ~/projects/incyt --since 2024-11-01 --until 2024-11-15
```

Only processes commits between these dates (ISO format: YYYY-MM-DD).

### Force Rescan

```
/timelog ~/projects/incyt --force
```

Ignores the last-run cache and processes all commits again. Useful if:
- You modified past commits (rebase, force push)
- You want fresh estimates
- The cache file is corrupted

### Dry-Run (Preview)

```
/timelog ~/projects/incyt --dry-run
```

Runs all stages but doesn't write `.md` files. Use to test parameters or inspect the extracted data.

## Troubleshooting

### "No git repositories found"

```bash
# Verify repos exist
find ~/projects/incyt -name ".git" -type d

# Check permissions
ls -la ~/projects/incyt/*/
```

### "No commits found"

Could mean:
- Repos are unchanged since last run (check `timelog-out/.timelog_last_run.json`)
- No commits in the time range
- No branches with commits (empty repos)

Try:
```
/timelog ~/projects/incyt --force --since 2024-01-01
```

### "ERROR: Extraction failed"

Check the log file:
```bash
cat timelog-out/run.log | tail -20
```

Common issues:
- `git` not found — install git
- `jq` not found — install jq
- Permission denied on repo folder — check `ls -la ~/projects/incyt/*/`

### "ERROR: Inference failed"

Claude feature inference requires the Anthropic API key:
```bash
export ANTHROPIC_API_KEY=sk-...
/timelog ~/projects/incyt
```

If you don't have an API key or don't want to use the inference stage, the skill will fall back to extracted data (all commits must have ticket IDs in their branch/message).

### Hours seem wrong

Each commit's estimated hours are shown in daily reports. To debug:

1. Check daily time logs for feature breakdown:
   ```bash
   cat timelog-out/data/daily-summaries/2024-11-daily-time-log.md
   ```

2. Review the heuristic formula in README.md under "Hour Estimation Heuristic"

3. Check raw extracted data:
   ```bash
   jq '.[] | {message, files_changed, estimated_hours}' timelog-out/.extracted.json | head -20
   ```

If a single commit exceeds 8 hours, it's clamped (that's correct behavior for very large commits).

## Automation & Scheduling

You can run the skill via command line for automation:

```bash
# Via the omp CLI (if available)
omp skill invoke timelog-generator --target ~/projects/incyt

# Or via bash directly
bash ~/.claude/skills/timelog-generator/run.sh --target ~/projects/incyt --output ~/timelog-reports
```

For scheduled runs (e.g., weekly):

```bash
# Add to crontab (runs every Monday at 6 AM)
0 6 * * 1 bash ~/.claude/skills/timelog-generator/run.sh --target ~/projects/incyt
```

## API Key Management

The skill uses the Anthropic API only for feature inference (stage 2). If you don't have ungrouped commits, it skips this stage entirely.

**How to provide the API key:**

Option 1: Environment variable (recommended)
```bash
export ANTHROPIC_API_KEY=sk-...
/timelog ~/projects/incyt
```

Option 2: In `.env` file (if your environment supports it)
```
ANTHROPIC_API_KEY=sk-...
```

Option 3: System-wide (macOS)
```bash
# Add to ~/.zprofile or ~/.bash_profile
export ANTHROPIC_API_KEY=sk-...
```

## Performance Notes

- **Extraction** — ~1-2 seconds per repository (uses git commands)
- **Inference** — ~0.1-0.2 seconds per ungrouped commit (API call to Claude)
- **Reporting** — ~0.5 seconds for all report generation

Total runtime: typically 5-15 seconds for 3-5 repos with 50-100 commits.

### Optimizations

If you have many ungrouped commits, inference will be slower. To speed up:
1. Use consistent ticket ID format in branch names (`PROJ-123-...`)
2. Include ticket ID in commit message first line
3. Use `--force` only when needed

## Caching Behavior

The skill tracks which repos have changed since the last run:

1. **First run** — Processes all repos and commits
2. **Subsequent runs** — Only processes repos that have changed (faster)
3. **With `--force`** — Processes all repos regardless of modification time

The cache is stored in `timelog-out/.timelog_last_run.json`. To clear it:

```bash
rm timelog-out/.timelog_last_run.json
# Next run will be a full scan
```

## Examples

### Example 1: Generate monthly reports

```
/timelog
```

Scans ~/projects/incyt, generates one file per month with all your work organized by date.

### Example 2: Weekly review

```
/timelog ~/projects/incyt --since $(date -u -d 'last Monday' +%Y-%m-%d) --until $(date -u +%Y-%m-%d)
```

Generate reports for just the past week.

### Example 3: New developer onboarding

```
/timelog ~/projects/incyt --since 2024-01-01 --until 2024-10-31 --dry-run
```

See what work was done over 10 months without writing files.

### Example 4: Monthly invoice

```
/timelog ~/projects/incyt --since 2024-11-01 --until 2024-11-30
```

Create a monthly report suitable for billing or retrospectives.

## Support & Maintenance

### Updating the Skill

To get the latest version, reinstall from the skill repository:

```bash
# Check current version
cat ~/.claude/skills/timelog-generator/.timelog_version

# Reinstall (replace entire directory)
# [Paste updated SKILL.md, scripts, etc.]
```

### Reporting Issues

If something goes wrong:

1. **Check the log** — `cat timelog-out/run.log`
2. **Try `--force`** — Forces full rescan
3. **Check prerequisites** — `git --version`, `jq --version`, `python3 --version`
4. **Verify permissions** — Can you `cd` and `git log` in your repos?

### Customization

To modify the hour estimation formula, edit `timelog_extract.sh` function `estimate_hours()` (around line 80).

To change output format, edit `timelog_report.py` functions `generate_monthly_reports()` and `generate_daily_repo_reports()`.
