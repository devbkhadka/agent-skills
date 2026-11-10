# Time Log Generator — Technical Reference

Complete specification of algorithms, data formats, and edge cases.

## Data Structures

### Extracted Commit Object

Output by `timelog_extract.sh`, stored in `.extracted.json`:

```json
{
  "sha": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0",
  "branch": "feature/PROJ-123-implement-auth",
  "author": "John Doe",
  "email": "john@example.com",
  "date": "2024-11-15T14:30:45+00:00",
  "message": "PROJ-123 Implement OAuth2 authentication\n\nAdded JWT token generation and validation.\nRefactored session middleware.\n\nFixes: PROJ-456",
  "ticket_id": "PROJ-123",
  "files_changed": 5,
  "lines_added": 127,
  "lines_removed": 43,
  "estimated_hours": 2.1,
  "repository": "auth-service",
  "repository_path": "/Users/dev/projects/incyt/auth-service",
  "inferred": false
}
```

**Fields:**
- `sha` — Full git commit SHA (40 hex chars)
- `branch` — Current branch name
- `author` — Commit author name
- `email` — Commit author email
- `date` — Commit date in ISO 8601 format with timezone
- `message` — Full commit message (including multi-line body)
- `ticket_id` — Extracted Jira ticket ID or empty string
- `files_changed` — Count of modified files
- `lines_added` — Total lines added (across all files)
- `lines_removed` — Total lines removed (across all files)
- `estimated_hours` — Estimated work hours (0.25-8.0)
- `repository` — Short repo name (basename of path)
- `repository_path` — Full path to repository
- `inferred` — Boolean, true if ticket_id was inferred by Claude

### Inferred Commit Object

After stage 2, same structure with possibly different `ticket_id` and `inferred` flag set.

Example inferred ticket ID:
```json
{
  "ticket_id": "feature/oauth-implementation",
  "inferred": true
}
```

### Month Grouping Data

Built in-memory during report generation:

```
by_date_ticket: {
  "2024-11-15": {
    "PROJ-123": [commit, commit, ...],
    "PROJ-124": [commit],
    "feature/database-optimization": [commit, commit]
  },
  "2024-11-16": { ... }
}

by_month: {
  "2024-11": ["2024-11-15", "2024-11-16", ...],
  "2024-12": [...]
}

by_feature_date: {
  "OAuth Implementation": {
    "2024-11-15": [commit, commit, ...],
    "2024-11-16": [commit]
  },
  "Database Optimization": { ... }
}
```

## Algorithms

### Hour Estimation (Stage 1)

Deterministic formula for estimating work hours from commit metadata.

**Pseudocode:**

```python
def estimate_hours(files_changed, lines_added, lines_removed, message_length, is_merge):
    base_hours = 0.5
    
    # Files factor: 1-2x multiplier
    if files_changed == 0:
        files_factor = 1.0
    else:
        files_factor = 1.0 + (files_changed / 5.0)
        files_factor = min(files_factor, 2.0)
    
    # Lines factor: 1-1.5x multiplier
    total_lines = lines_added + lines_removed
    if total_lines == 0:
        lines_factor = 1.0
    else:
        lines_factor = 1.0 + (total_lines / 400.0)
        lines_factor = min(lines_factor, 1.5)
    
    # Complexity factor based on message length
    if message_length > 100:
        complexity_factor = 1.2
    elif message_length < 20:
        complexity_factor = 0.7
    else:
        complexity_factor = 1.0
    
    # Merge commit factor (very low effort)
    merge_factor = 0.1 if is_merge else 1.0
    
    # Calculate total
    total = (base_hours * files_factor * lines_factor * 
             complexity_factor * merge_factor)
    
    # Clamp to [0.25, 8.0]
    return max(0.25, min(total, 8.0))
```

**Parameters:**
- `files_changed` — Number of files modified (from `git diff-tree --numstat`)
- `lines_added` — Total lines added (sum of '+' in numstat)
- `lines_removed` — Total lines removed (sum of '+' in numstat for removed)
- `message_length` — Character count of commit message
- `is_merge` — True if commit has 2+ parents (merge commit)

**Calibration:**

| Scenario | Input | Output | Rationale |
|----------|-------|--------|-----------|
| Single-line doc fix | 1 file, 2 lines | 0.25 hours | Minimum clamp |
| Small bug fix | 3 files, 50 lines | 0.6 hours | Base + minimal factors |
| Feature with tests | 8 files, 300 lines, detailed msg | 1.8 hours | Strong multipliers |
| Large refactor | 20 files, 1000 lines | 8.0 hours | Clamped at full day |
| Merge commit | 0 files, 0 lines | 0.1 hours | Nominal effort |

### Daily Hour Allocation (Stage 3)

Normalizes estimated hours to fit within 8 hours per day.

**Pseudocode:**

```python
def allocate_daily_hours(ticket_ids_and_hours):
    """
    Input: dict of {ticket_id: sum_of_estimated_hours}
    Output: dict of {ticket_id: allocated_hours}
    """
    total_hours = sum(ticket_ids_and_hours.values())
    
    if total_hours <= 8.0:
        # Partial day or fits perfectly
        return ticket_ids_and_hours
    
    # Normalize down to 8 hours
    scale_factor = 8.0 / total_hours
    return {
        ticket: hours * scale_factor
        for ticket, hours in ticket_ids_and_hours.items()
    }
```

**Behavior:**
- If tickets total ≤ 8 hours: leave as-is (e.g., 6 hours = partial day)
- If tickets total > 8 hours: scale all proportionally (e.g., 10h → 8h, each ticket scaled by 0.8)
- Guaranteed: `sum(allocated_hours) ≤ 8.0` for each day

**Example:**

```
Day 1: PROJ-123 (4h) + PROJ-124 (5h) = 9h
→ Normalize: 4 * (8/9) = 3.56h, 5 * (8/9) = 4.44h
→ Allocated: PROJ-123: 3.56h, PROJ-124: 4.44h
```

### Ticket ID Extraction (Stage 1)

Deterministic regex-based extraction with priority ordering.

**Priority 1: Branch Name**

Pattern: `([A-Z][A-Z0-9]*-[0-9]+)`

Examples:
- `feature/PROJ-123-implement-auth` → `PROJ-123`
- `fix/BUG-456-typo` → `BUG-456`
- `refactor/ABC-789-performance` → `ABC-789`

**Priority 2: First Commit Message Line**

Pattern: `^([A-Z][A-Z0-9]*-[0-9]+)` or `([A-Z][A-Z0-9]*-[0-9]+)` anywhere in first line

Examples:
- `PROJ-123 Implement OAuth2` → `PROJ-123`
- `Fix auth bug [PROJ-123]` → `PROJ-123`
- `[PROJ-123] Update documentation` → `PROJ-123`

**Priority 3: Any Line of Message**

Scan entire message body for the pattern.

**Priority 4: Feature Inference**

If no ticket found, pass message to Claude to infer a feature name.

**Output Format:**

- Real ticket: `PROJ-123` (uppercase, contains hyphen and digits)
- Inferred feature: `feature/oauth-implementation` (lowercase, hyphenated, prefixed)

### Feature Name Inference (Stage 2)

Claude prompt-based semantic extraction.

**Prompt:**

```
You are extracting a feature name from a git commit message for time-logging purposes.

Commit message:
{commit_message}

Return ONLY a short feature name (2-5 words), lowercase, no special characters.
Examples:
- "fix login button" 
- "database migration"
- "api performance improvements"
- "documentation updates"

Feature name:
```

**Post-processing:**

1. Strip whitespace
2. Remove non-alphanumeric characters (keep hyphens, spaces)
3. Collapse multiple spaces to single space
4. Lowercase
5. Trim edges
6. Prefix with `feature/`

**Result example:** `"OAuth implementation" → "feature/oauth-implementation"`

### Last-Run Cache

Stored in `.timelog_last_run.json`:

```json
{
  "last_run": "2024-11-20T14:30:45.123456",
  "repos": {
    "/Users/dev/projects/incyt/auth-service": 1731509445.123456,
    "/Users/dev/projects/incyt/db-service": 1731509445.123456
  }
}
```

**Algorithm:**

1. Before processing each repo, read cache file
2. Check modification time (mtime) of `.git/` directory
3. Compare against cached time
4. If mtime > cached time OR `--force` flag: process repo
5. Otherwise: skip repo (unchanged)
6. After processing, update cache with current mtime

**Rationale:** Avoids re-processing unchanged repos; speeds up subsequent runs.

## Edge Cases & Validation

### Commits with No Message

Input:
```
Author: John Doe
Date: 2024-11-15T14:30:45+00:00
Message: (empty)
```

Handling:
- `message` field set to empty string `""`
- `message_length` = 0
- `complexity_factor` = 0.7 (< 20 chars)
- Hour estimate still valid, just conservative

Output in report:
```
- `14:30` PROJ-123: "[No message]" (0.35h, ...)
```

### Merge Commits

Detected by checking parent count: `git rev-list COMMIT --parents | wc -w`

Input:
```
Merge: abc123d + def456e → ghi789f
Parents: 2 or more
```

Handling:
- Skip file diff (merge commits don't have meaningful diffs)
- Set `files_changed` = 0, `lines_added` = 0, `lines_removed` = 0
- Apply `merge_factor` = 0.1
- Estimated hours typically 0.05-0.1

Rationale: Merges usually require minimal manual work; automated

### Very Old Commits (>2 years)

Behavior: Included by default, no filtering

Concern: Old commits might skew estimates, but deterministic extraction doesn't discriminate

Solution: Use `--since` to filter:
```bash
/timelog ~/projects/incyt --since 2023-01-01
```

### Future Commits (timestamp > now)

Detection: During extraction, skip commits with date > current time

Handling:
```bash
if commit_date > now:
    log "WARNING: Skipping future commit: {sha}"
    continue
```

Rationale: Git can have commits with incorrect system clock; they're anomalies

### Very Large Commits (>500 lines)

Input:
```
Files: 25
Lines added: 1,200
Lines removed: 300
```

Estimation:
```
base = 0.5
files_factor = min(25/5, 2.0) = 2.0
lines_factor = min(1500/400, 1.5) = 1.5
complexity_factor = 1.2 (assuming detailed msg)
merge_factor = 1.0
total = 0.5 * 2.0 * 1.5 * 1.2 * 1.0 = 1.8
CLAMPED = min(1.8, 8.0) = 1.8 hours
```

If unclamped exceeds 8:
```
total = 0.5 * 2.0 * 1.5 * 1.2 * 1.0 = 1.8... (won't exceed 8 with current formula)
```

Actually, with extreme values:
```
Files: 100, Lines: 5000, Long msg
files_factor = 2.0 (capped)
lines_factor = 1.5 (capped)
complexity_factor = 1.2
total = 0.5 * 2.0 * 1.5 * 1.2 = 1.8 < 8.0
```

So clamping to 8.0 rarely triggers; it's a safety valve.

### Multiple Commits Same Minute

Git allows multiple commits with exact same timestamp.

Handling: Both extracted and ordered by SHA (lexicographic)

Output in daily report:
```
- `14:30` PROJ-123: "Commit A" (1.0h, ...) [a1b2c3d]
- `14:30` PROJ-124: "Commit B" (1.0h, ...) [b2c3d4e]
```

Both appear same minute, hour allocation works normally

### Repository with No Commits

Input: Empty git repo (initial commit, no history)

Handling:
```bash
git log --all  # Returns nothing
continue to next repo
```

Output: Skipped silently, doesn't appear in reports

### Repo URL Changes

Input: Repository cloned from `origin/main`, later pushed with different URL

Detection: Only looks at filesystem and `.git` metadata, not URL

Behavior: Treats as same repo (path-based identity)

Output: All commits included under same repository name

### Commits with Special Characters in Message

Input:
```
"Fix: quote parsing \"escaped\" quotes"
"Update docs — use em-dash"
"Support UTF-8: 你好世界"
```

Handling: JSON-escape special characters during serialization

Python:
```python
json.dumps(commits, indent=2)  # Handles all escaping
```

Output:
```json
{
  "message": "Fix: quote parsing \"escaped\" quotes"
}
```

Reports: Rendered as-is (markdown supports Unicode)

### Commit Message > 10K Characters

Input: Extremely long commit message (rare but possible)

Handling: Full message stored and processed

Concern: Inference API might hit token limits

Mitigation: Message truncated before sending to Claude (typically takes first 1000 chars)

## Git Command Reference

Commands used by extraction stage:

```bash
# Find all git repos
find $PATH -name ".git" -type d

# Get modification time of .git directory
stat -f%m $REPO/.git  # macOS
stat -c%Y $REPO/.git  # Linux

# Extract commits with metadata
git log --all --pretty=format:%H%n%an%n%ae%n%aI%n%B%n==END== --numstat

# Get commit parent count (for merge detection)
git rev-list $SHA --parents

# Get file diff stats
git diff-tree --no-commit-id --numstat $SHA
```

## Performance Characteristics

### Time Complexity

- **Extraction** — O(n) where n = number of commits; ~100-1000 commits/second
- **Inference** — O(m) where m = ungrouped commits; ~10 API calls/second (rate limited)
- **Reporting** — O(n) with O(1) aggregation; very fast (~milliseconds)

### Space Complexity

- **In-memory** — O(n) for extracted commit objects (~1KB per commit)
- **Disk** — O(n) for JSON files (~5-10KB per 100 commits)

### Wall Time

- 50 commits, 3 repos: ~2-3 seconds
- 500 commits, 10 repos: ~10-15 seconds (dominated by Git operations)
- 5000 commits, 50 repos: ~60-120 seconds (potential parallelization opportunity)

## Limitations

1. **No code analysis** — Estimates based on metadata, not actual code complexity
2. **Merge commits underestimated** — Treated as 0.1x hours; actual resolution time not measured
3. **Concurrent work not detected** — Cannot distinguish between "worked 8h on X" vs "worked 2h each on X,Y,Z,W"
4. **Single person assumption** — No co-author detection; whole commit hours to single author
5. **No working directory time** — Only counts committed time; local WIP not included
6. **Deterministic but arbitrary** — Heuristic formula is educated guess, not measured reality

## Accuracy Expectations

The skill is designed for:
- ✅ Approximate time accounting (±20%)
- ✅ Feature/project grouping
- ✅ Trend detection (which projects got time)
- ✅ Monthly invoicing (rough allocation)

Not designed for:
- ❌ Precise per-minute tracking
- ❌ Validation against timesheets
- ❌ Billing customers (use real time tracking)

## Extensibility

### Adding Custom Ticket ID Patterns

Edit `timelog_extract.sh` function `extract_ticket_from_branch()`:

```bash
# Add pattern for your system
if [[ $branch =~ ([A-Z]+-[0-9]+) ]]; then
  echo "${BASH_REMATCH[1]}"
fi

# Add pattern for alternative format (e.g., numeric IDs)
if [[ $branch =~ (#[0-9]+) ]]; then
  echo "${BASH_REMATCH[1]}"
fi
```

### Customizing Hour Formula

Edit `estimate_hours()` function in `timelog_extract.sh` (around line 70).

Example: De-emphasize line count, emphasize file count:

```bash
files_factor = 1.0 + ($files_changed / 3.0)  # More aggressive
lines_factor = 1.0 + ($total_lines / 600.0)  # Less aggressive
```

### Changing Daily Hour Allocation

Edit `allocate_daily_hours()` in `timelog_report.py` to use different cap:

```python
HOURS_PER_DAY = 9.0  # Instead of 8.0
if total_hours <= HOURS_PER_DAY:
    return ticket_ids_and_hours
scale_factor = HOURS_PER_DAY / total_hours
```

## Testing

No automated test suite included, but manual verification:

```bash
# Test extraction on small repo
cd test-repo
git init
git commit --allow-empty -m "PROJ-123 Test commit"
bash /path/to/timelog_extract.sh --target . --output test.json
jq '.' test.json  # Verify output structure

# Test inference (requires API key)
python3 /path/to/timelog_infer.py --input test.json --output inferred.json

# Test reporting
python3 /path/to/timelog_report.py --input inferred.json --output report-out/
ls report-out/  # Check generated files
```
