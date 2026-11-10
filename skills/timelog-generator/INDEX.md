# Time Log Generator Skill — Documentation Index

Complete reference for the `/timelog` skill for generating time-log data from git commits.

## Quick Navigation

### For Users (Getting Started)

1. **[README.md](README.md)** — Start here
   - What the skill does
   - Quick-start examples
   - Troubleshooting common issues
   - Architecture overview

2. **[INTEGRATION.md](INTEGRATION.md)** — How to use the skill
   - Step-by-step execution flow
   - Reading the output files
   - Advanced usage (date ranges, dry-run, force rescan)
   - Automation & scheduling

### For Developers & Customizers

3. **[TECHNICAL.md](TECHNICAL.md)** — Implementation details
   - Data structures and formats
   - Algorithm specifications
   - Edge cases and validation
   - Performance characteristics
   - Extensibility points

### Official Documentation

4. **[SKILL.md](SKILL.md)** — Skill manifest & step-by-step instructions
   - Official spec for Claude AI integration
   - Deterministic extraction rules
   - LLM usage guidelines
   - Output file formats with examples

---

## Files in This Directory

```
timelog-generator/
├── SKILL.md                    Skill manifest (official spec)
├── README.md                   User guide
├── INTEGRATION.md              End-to-end usage guide
├── TECHNICAL.md                Technical reference
├── INDEX.md                    This file
├── run.sh                      Main pipeline orchestrator
├── timelog_extract.py          Stage 1: Deterministic extraction
├── timelog_infer.py            Stage 2: LLM feature inference
├── timelog_report.py           Stage 3: Report generation
├── .timelog_version            Version tracking
├── data/                       Data directory
│   ├── daily-summaries/        Monthly summaries and daily time logs
│   ├── scan-results/           Raw JSON scan results
│   └── states/                 Run state and cache storage
└── (output directory)          Generated reports and data
    ├── data/
    │   ├── daily-summaries/
    │   │   ├── YYYY-MM.md
    │   │   └── YYYY-MM-daily-time-log.md
    │   ├── scan-results/
    │   │   └── YYYY-MM-scan-result.json
    │   └── states/
    ├── timelog_summary.md      Overall report
    ├── run.log                 Execution log
    ├── .extracted.json         Raw extracted data
    ├── .inferred.json          Processed data
    └── .timelog_last_run.json  Cache for incremental runs
```

---

## What This Skill Does

Automatically generates time-log entries from git commit history:

- **Extracts** commits from git repositories
- **Parses** Jira ticket IDs from branch names and commit messages
- **Infers** feature names using Claude when ticket IDs missing
- **Estimates** work hours based on commit size and complexity
- **Allocates** 8 hours per day across work items
- **Reports** organized in `data/daily-summaries/` and `data/scan-results/`

**Output:** Markdown files and JSON scan results suitable for invoicing, timesheet reconciliation, or project retrospectives.

---

## One-Sentence Invocation

```
/timelog [path] [--since DATE] [--until DATE] [--force] [--dry-run]
```

Default path: `~/projects/incyt`

---

## The Three Stages

### Stage 1: Extraction (Deterministic, Bash)

**Script:** `timelog_extract.sh` (or `timelog_extract.py`)

- Scans for git repositories
- Extracts all commits with full metadata
- Parses ticket IDs from branch names and messages
- Estimates hours using deterministic heuristic
- **Output:** `.extracted.json` (raw commit data)

### Stage 2: Inference (LLM, Python)

**Script:** `timelog_infer.py`

- Takes commits from stage 1
- Uses Claude to infer feature names for ungrouped commits
- Preserves real ticket IDs (doesn't modify)
- **Output:** `.inferred.json` (all commits now have ticket_id)

### Stage 3: Reporting (Deterministic, Python)

**Script:** `timelog_report.py`

- Organizes commits by date and ticket
- Allocates 8 hours per day (normalized)
- Generates markdown reports and scan results
- **Outputs:**
  - `data/daily-summaries/YYYY-MM.md` (monthly summaries)
  - `data/daily-summaries/YYYY-MM-daily-time-log.md` (daily feature aggregates)
  - `data/scan-results/YYYY-MM-scan-result.json` (programmatic commit data)
  - `timelog_summary.md` (statistics)

---

## Output Examples

### Monthly Summary (`data/daily-summaries/2024-11.md`)

```markdown
# Time Log — November 2024

## 2024-11-15 (Friday)

**Total: 8.0 hours**

### PROJ-123 Implement OAuth2 auth
- **Hours**: 4.0
- **Summary**: Added JWT token generation, refactored session middleware
- **Commits**: 3 commits

### PROJ-124 Dashboard bug fixes
- **Hours**: 2.5
- **Summary**: Fixed data table sorting, improved rendering
- **Commits**: 1 commit

### Database optimization (no ticket)
- **Hours**: 1.5
- **Summary**: Optimized query performance
- **Commits**: 2 commits
```

### Daily Time Log (`data/daily-summaries/2024-11-daily-time-log.md`)

```markdown
# Daily Time Log — November 2024

## 2024-11-15 (Friday)

**Total: 8.0 hours**

**PROJ-123 Implement OAuth2 auth** — Added JWT token generation
- 4.0 hours • 3 commits

**PROJ-124 Dashboard bug fixes** — Fixed sort bug
- 2.5 hours • 1 commit
```

---

## Hour Estimation

Each commit estimated independently based on:

- **Files changed** — 1-2x multiplier (more files = more work)
- **Lines added/removed** — 1-1.5x multiplier (larger changes = more work)
- **Message length** — Complexity indicator (detailed msgs suggest complex work)
- **Merge commits** — 0.1x multiplier (typically minimal manual effort)

**Result:** Always 0.25-8.0 hours per commit, normalized to 8 hours per calendar day.

---

## Key Features

✅ **Deterministic** — All extraction and allocation uses exact logic (reproducible)

✅ **Smart grouping** — Extracts Jira IDs from branch names and commit messages

✅ **Feature inference** — Claude infers meaningful group names when tickets missing

✅ **Daily normalization** — Fair 8-hour/day allocation across concurrent work

✅ **Incremental** — Caches last-run time; subsequent runs process only changed repos

✅ **Date range filtering** — `--since` and `--until` for narrowing scope

✅ **Dry-run testing** — `--dry-run` previews output without writing files

✅ **Comprehensive reports** — Monthly summaries + daily per-repo breakdowns

---

## Getting Started

### 1. Verify Prerequisites

```bash
git --version           # Required
jq --version            # Required
python3 --version       # Required
echo $ANTHROPIC_API_KEY # For feature inference (optional but recommended)
```

### 2. Run the Skill

```bash
/timelog
```

Scans `~/projects/incyt` and generates reports in `timelog-out/`

### 3. Review Output

```bash
# View monthly summary
cat timelog-out/data/daily-summaries/2024-11.md

# View daily feature breakdown
cat timelog-out/data/daily-summaries/2024-11-daily-time-log.md

# View scan results
cat timelog-out/data/scan-results/2024-11-scan-result.json | jq '.'

# View overall statistics
cat timelog-out/timelog_summary.md
```

### 4. Next Steps

- **For invoicing:** Use monthly files grouped by ticket ID
- **For retrospectives:** Review daily time logs to see what got done
- **For weekly review:** Use `--since` to narrow to past week
- **For automation:** Add to cron job or CI/CD pipeline

---

## Troubleshooting Quick Reference

| Problem | Solution |
|---------|----------|
| No commits found | `--force` flag, verify repos exist with `git log` |
| Wrong ticket IDs | Check branch name and commit message format |
| Hours seem wrong | Review `timelog-out/data/daily-summaries/` and `data/scan-results/` |
| API errors | Set `export ANTHROPIC_API_KEY=sk-...` |
| Permission denied | Check `ls -la` on repo folders |

See [INTEGRATION.md](INTEGRATION.md) "Troubleshooting" for detailed diagnostics.

---

## Configuration Files

### Last-Run Cache (`.timelog_last_run.json`)

Tracks which repos have been processed:

```json
{
  "last_run": "2024-11-20T14:30:45.123456",
  "repos": {
    "/path/to/repo1": 1731509445.123456,
    "/path/to/repo2": 1731509445.123456
  }
}
```

Remove to force full rescan:
```bash
rm timelog-out/.timelog_last_run.json
```

### Run Log (`run.log`)

Timestamped log of execution:

```
[2024-11-20 14:30:45] Starting timelog generation
[2024-11-20 14:30:45] Checking prerequisites...
[2024-11-20 14:30:46] Running extraction...
[2024-11-20 14:30:48] Extracted 47 commits from 3 repos
[2024-11-20 14:30:49] Running feature inference...
```

---

## Performance

- **50 commits, 3 repos** → ~2-3 seconds
- **500 commits, 10 repos** → ~10-15 seconds
- **5000 commits, 50 repos** → ~60-120 seconds

Dominated by git operations; inference is fast (~0.1s per inferred commit).

---

## Documentation Map

```
README.md              ←— User guide, quick start
    ↓
INTEGRATION.md        ←— Detailed usage, step-by-step
    ↓
TECHNICAL.md          ←— Algorithms, edge cases, customization
    ↓
SKILL.md              ←— Official spec for Claude (read by system)
```

**For most users:** Start with README.md, then INTEGRATION.md.

**For customizers:** Jump to TECHNICAL.md for algorithm details and extension points.

**For system maintainers:** Reference SKILL.md for the official spec and step-by-step instructions.

---

## Version

Current version: 1.0.0

Check version:
```bash
cat ~/.claude/skills/timelog-generator/.timelog_version
```

---

## License & Maintenance

This skill is part of the Oh My Pi harness and follows the repository's conventions.

For issues or improvements, see the main project repository.

---

## Quick Reference

| Task | Command |
|------|---------|
| Generate reports | `/timelog` |
| Scan specific path | `/timelog ~/path --since 2024-01-01` |
| Force rescan | `/timelog ~/path --force` |
| Preview without writing | `/timelog ~/path --dry-run` |
| Check version | `cat ~/.claude/skills/timelog-generator/.timelog_version` |
| View log | `cat timelog-out/run.log` |
| Inspect data | `jq '.' timelog-out/.extracted.json` |

---

Last updated: 2024-11-20
