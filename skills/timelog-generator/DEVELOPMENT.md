# Time Log Generator — Development Guide

Local management guide for the `/timelog` skill in the `agent-skills` repository.

## Directory Structure

```
timelog-generator/
├── SKILL.md                  Official skill manifest (read by Claude)
├── README.md                 User guide & examples
├── INTEGRATION.md            End-to-end usage & troubleshooting
├── TECHNICAL.md              Algorithms & implementation details
├── INDEX.md                  Documentation roadmap
├── MANIFEST.txt              Quick reference
├── DEVELOPMENT.md            This file
├── data/                     Data directory
│   ├── daily-summaries/      Monthly summaries and daily logs
│   ├── scan-results/         Raw JSON scan results
│   └── states/               Run states and caches
├── run.sh                    Main orchestrator script
├── timelog_extract.py        Stage 1: Extract commits & estimate hours
├── timelog_infer.py          Stage 2: LLM feature inference
└── timelog_report.py         Stage 3: Generate markdown reports & scan data
```

## Quick Start

### Test the skill locally

```bash
cd timelog-generator

# Run on test data (past week)
bash run.sh --target ~/projects/incyt --output ./test-output --since 2026-08-07

# View results
cat test-output/timelog_summary.md
cat test-output/data/daily-summaries/2026-08.md | head -50
```

### Install dependencies

```bash
# Required system tools
brew install git jq python3

# Python package (for inference stage)
python3 -m pip install anthropic
```

### Set API key for feature inference

```bash
export ANTHROPIC_API_KEY=sk-...
bash run.sh --target ~/projects/incyt
```

## Development Workflow

### 1. Modify Hour Estimation Formula

Edit `timelog_extract.py` function `estimate_hours()`:

```python
def estimate_hours(files_changed, lines_added, lines_removed, message_length, is_merge):
    # Adjust these multipliers for your needs:
    base = 0.5              # Minimum hours per commit
    files_factor = ...      # Weight for file count
    lines_factor = ...      # Weight for code volume
    complexity = ...        # Weight based on message
```

**Test changes:**
```bash
python3 timelog_extract.py --target ~/test-repo --output test.json
python3 << 'EOF'
import json
data = json.load(open('test.json'))
for c in data[:5]:
    print(f"{c['estimated_hours']}h - {c['message'].split(chr(10))[0][:60]}")
EOF
```

### 2. Customize Ticket ID Patterns

Edit `timelog_extract.py` functions:
- `extract_ticket_from_branch()` — regex for branch names
- `extract_ticket_from_message()` — regex for commit messages

**Example: Support numeric ticket IDs**
```python
def extract_ticket_from_branch(branch: str) -> Optional[str]:
    # Add pattern for #123 style IDs
    match = re.search(r"#([0-9]+)", branch)
    if match:
        return f"#{match.group(1)}"
    # Fall back to PROJ-123 style
    match = re.search(r"([A-Z][A-Z0-9]*-[0-9]+)", branch)
    return match.group(1) if match else None
```

### 3. Modify Report Format

Edit `timelog_report.py`:
- `generate_monthly_reports()` — monthly markdown structure (`data/daily-summaries/YYYY-MM.md`)
- `generate_daily_time_log()` — feature aggregated daily time log (`data/daily-summaries/YYYY-MM-daily-time-log.md`)
- `generate_scan_results()` — machine-readable JSON data (`data/scan-results/YYYY-MM-scan-result.json`)
- `generate_summary_report()` — statistics report (`timelog_summary.md`)

**Example: Add project grouping to monthly report**
```python
# In generate_monthly_reports():
# Group by project first, then date within project
for project in sorted(projects):
    lines.append(f"\n## Project: {project}\n")
    # ... then iterate dates
```

### 4. Add CSV Export (Enhancement)

Create `timelog_export.py`:

```python
#!/usr/bin/env python3
import json
import csv
from pathlib import Path

def export_to_csv(inferred_json: Path, output_csv: Path):
    commits = json.load(open(inferred_json))
    
    with open(output_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'date', 'ticket_id', 'repository', 'hours', 'files_changed', 'summary'
        ])
        writer.writeheader()
        
        for c in commits:
            date = c['date'].split('T')[0]
            summary = c['message'].split('\n')[0][:80]
            writer.writerow({
                'date': date,
                'ticket_id': c['ticket_id'],
                'repository': c['repository'],
                'hours': f"{c['estimated_hours']:.2f}",
                'files_changed': c['files_changed'],
                'summary': summary
            })
```

Then call from `run.sh`:
```bash
python3 timelog_export.py \
  --input "$OUTPUT_DIR/.inferred.json" \
  --output "$OUTPUT_DIR/export.csv"
```

## Testing

### Unit Test: Hour Estimation

Create `test_estimation.py`:

```python
from timelog_extract import estimate_hours

# Test cases
test_cases = [
    # (files, added, removed, msg_len, is_merge) → expected_hours
    (1, 5, 0, 10, False, 0.25),        # Minimum
    (3, 50, 0, 50, False, 0.6),        # Small fix
    (8, 200, 50, 150, False, 1.5),     # Feature
    (20, 500, 100, 200, False, 8.0),   # Large (clamped)
    (0, 0, 0, 20, True, 0.1),          # Merge commit
]

for files, added, removed, msg_len, is_merge, expected in test_cases:
    result = estimate_hours(files, added, removed, msg_len, is_merge)
    status = "✓" if abs(result - expected) < 0.01 else "✗"
    print(f"{status} {result:.2f}h (expected {expected:.2f}h)")
```

Run:
```bash
python3 test_estimation.py
```

### Integration Test: Full Pipeline

```bash
# Create test repo
mkdir test-repo
cd test-repo
git init
git config user.name "Test User"
git config user.email "test@example.com"

# Add some test commits
git commit --allow-empty -m "IN-123 First feature"
git commit --allow-empty -m "IN-123 Second feature"
git commit --allow-empty -m "IN-456 Bug fix"

# Run skill
cd ../timelog-generator
bash run.sh --target ../test-repo --output ../test-output

# Verify output
cat ../test-output/timelog_summary.md
```

## Performance Tuning

### Profile extraction speed

```bash
time python3 timelog_extract.py --target ~/large/project --output test.json
```

**Current performance:**
- 50 commits / 3 repos: ~2-3 seconds
- 500 commits / 10 repos: ~10-15 seconds
- 5000 commits / 50 repos: ~60-120 seconds

**Bottleneck:** Git subprocess calls for each commit (one per commit for parent count, one for numstat)

**Optimization options:**
1. Batch git calls (fetch multiple commits' stats in one call)
2. Parallelize repo processing (use multiprocessing)
3. Cache git results

### Profile reporting speed

```bash
time python3 timelog_report.py --input test.json --output test-report
```

**Current:** ~100-500ms (very fast)

## Deployment

### Install to system skills directory

```bash
# From this repo
cp -r . ~/.claude/skills/timelog-generator

# Then use with /timelog in Claude sessions
```

### Update after changes

```bash
# Edit files in agent-skills repo
# Then update installed version
cp timelog-*.py ~/.claude/skills/timelog-generator/
cp run.sh ~/.claude/skills/timelog-generator/
cp SKILL.md ~/.claude/skills/timelog-generator/
```

### Version control

Track in git:
```bash
git add timelog-generator/
git commit -m "feat(timelog): improve hour estimation formula"
```

## Debugging

### Enable verbose logging

Add to any script:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
```

### Inspect intermediate files

```bash
# View raw extracted data
jq '.' output/.extracted.json | head -50

# View after inference
jq '.[] | {sha, ticket_id, estimated_hours}' output/.inferred.json | head -20

# Check specific commit
jq '.[] | select(.sha == "abc123...")' output/.extracted.json
```

### Check git operations manually

```bash
cd ~/projects/incyt/some-repo

# List commits in date range
git log --since 2026-08-07 --until 2026-08-14 --oneline | wc -l

# Get file stats for a commit
git diff-tree --no-commit-id --numstat abc123...

# Check parent count (merge detection)
git rev-list abc123... --parents | head -1
```

## Common Issues & Fixes

### "No git repositories found"

```bash
# Verify paths exist
ls -la ~/projects/incyt/

# Try manual scan
find ~/projects/incyt -name ".git" -type d
```

### "No commits found"

```bash
# Check date range
git log --since 2026-08-07 --until 2026-08-14 --oneline | head

# Try without date filters
python3 timelog_extract.py --target ~/projects/incyt --output test.json
```

### API key errors (inference stage)

```bash
# Set key
export ANTHROPIC_API_KEY=sk-...

# Verify it's set
echo $ANTHROPIC_API_KEY

# Test API connection
python3 -c "import anthropic; print(anthropic.Anthropic())"
```

### Hour estimates seem wrong

```bash
# Check formula in timelog_extract.py
# Review test cases in test_estimation.py
# Compare against actual code commits:
git show --stat abc123...
```

## Future Enhancements

- [ ] Parallel repo processing (speed up extraction 3-5x)
- [ ] CSV/Excel export formats
- [ ] Configurable hour formula (via config file)
- [ ] Caching improvements (incremental extraction)
- [ ] Burndown chart generation
- [ ] Team/developer analytics
- [ ] Jira integration (fetch real ticket descriptions)
- [ ] Slack notifications (daily summary)

## Documentation

See:
- `README.md` — User guide
- `SKILL.md` — Official spec
- `TECHNICAL.md` — Algorithms
- `INTEGRATION.md` — Usage guide
- `INDEX.md` — Documentation index

## Support

For issues:
1. Check `run.log` for detailed errors
2. Review `TECHNICAL.md` edge cases section
3. Run `test_estimation.py` to validate core logic
4. Inspect `.extracted.json` and `.inferred.json` intermediate files
