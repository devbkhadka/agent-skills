#!/bin/bash
# run.sh - Main orchestrator for timelog-generator skill
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# TIMELOG_TARGETS is a colon-delimited list of repository roots to scan.
# Defaults to current directory if not set in environment or CLI flags.
DEFAULT_TIMELOG_TARGETS="."
TIMELOG_TARGETS="${TIMELOG_TARGETS:-${TARGET_PATH:-$DEFAULT_TIMELOG_TARGETS}}"
DATA_DIR="${DATA_DIR:=$SKILL_DIR/data}"
SINCE_DATE="${SINCE_DATE:=}"
UNTIL_DATE="${UNTIL_DATE:=}"
WEEKEND_TARGET="${WEEKEND_TARGET:=friday}"
DRY_RUN="${DRY_RUN:=0}"
AUTHOR_PATTERNS=()
EXCLUDE_CO_AUTHORS=()
CLI_TARGETS=()

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --target) CLI_TARGETS+=("$2"); shift 2 ;;
    --data-dir|--output) DATA_DIR="$2"; shift 2 ;;
    --since) SINCE_DATE="$2"; shift 2 ;;
    --until) UNTIL_DATE="$2"; shift 2 ;;
    --weekend-target) WEEKEND_TARGET="$2"; shift 2 ;;
    --author) AUTHOR_PATTERNS+=("$2"); shift 2 ;;
    --exclude-co-author) EXCLUDE_CO_AUTHORS+=("$2"); shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ ${#CLI_TARGETS[@]} -gt 0 ]]; then
  TARGET_PATHS=("${CLI_TARGETS[@]}")
else
  IFS=':' read -r -a TARGET_PATHS <<< "$TIMELOG_TARGETS"
fi

if [[ ${#TARGET_PATHS[@]} -eq 0 ]]; then
  echo "ERROR: No scan targets configured. Set TIMELOG_TARGETS or pass --target." >&2
  exit 1
fi

STATES_DIR="$DATA_DIR/states"
mkdir -p "$DATA_DIR/daily-summaries" "$DATA_DIR/scan-results" "$STATES_DIR"
LOG_FILE="$STATES_DIR/run.log"

log_msg() {
  local msg="$1"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $msg" | tee -a "$LOG_FILE"
}

log_msg "====== Time Log Generation ======"
log_msg "Targets: ${TARGET_PATHS[*]}"
log_msg "Data Directory: $DATA_DIR"
log_msg "Weekend Shift Target: $WEEKEND_TARGET"

# Check prerequisites
for cmd in git jq python3; do
  if ! command -v "$cmd" &>/dev/null; then
    log_msg "ERROR: $cmd not found"
    exit 1
  fi
done

for target_path in "${TARGET_PATHS[@]}"; do
  if [ ! -d "$target_path" ]; then
    log_msg "ERROR: Target path not found: $target_path"
    exit 1
  fi
done

log_msg "Prerequisites OK"

# Step 1: Extract commits
log_msg "Running extraction..."
extract_args=(
  --output "$STATES_DIR/.extracted.json"
  --log "$LOG_FILE"
)
for target_path in "${TARGET_PATHS[@]}"; do
  extract_args+=(--target "$target_path")
done
if [[ -n "$SINCE_DATE" ]]; then
  extract_args+=(--since "$SINCE_DATE")
fi
if [[ -n "$UNTIL_DATE" ]]; then
  extract_args+=(--until "$UNTIL_DATE")
fi
for author_pattern in "${AUTHOR_PATTERNS[@]}"; do
  extract_args+=(--author "$author_pattern")
done
for exclude_co_author in "${EXCLUDE_CO_AUTHORS[@]}"; do
  extract_args+=(--exclude-co-author "$exclude_co_author")
done
python3 "$SKILL_DIR/timelog_extract.py" "${extract_args[@]}" || {
  log_msg "ERROR: Extraction failed"
  exit 1
}

# Check if any commits found
EXTRACT_SIZE=$(stat -f%z "$STATES_DIR/.extracted.json" 2>/dev/null || stat -c%s "$STATES_DIR/.extracted.json" 2>/dev/null || wc -c < "$STATES_DIR/.extracted.json" || echo 0)
if [ "$EXTRACT_SIZE" -lt 10 ]; then
  log_msg "No commits found"
  echo "[]" > "$STATES_DIR/.inferred.json"
  exit 0
fi

log_msg "Extraction complete"

# Step 2: Run inference
if [ "$DRY_RUN" -eq 0 ]; then
  log_msg "Running feature inference..."
  python3 "$SKILL_DIR/timelog_infer.py" \
    --input "$STATES_DIR/.extracted.json" \
    --output "$STATES_DIR/.inferred.json" \
    --log "$LOG_FILE" || {
    log_msg "WARNING: Inference failed, using extracted data as-is"
    cp "$STATES_DIR/.extracted.json" "$STATES_DIR/.inferred.json"
  }
else
  log_msg "Dry-run mode: skipping inference"
  cp "$STATES_DIR/.extracted.json" "$STATES_DIR/.inferred.json"
fi

log_msg "Inference complete"

# Step 3: Generate reports
log_msg "Generating reports..."
python3 "$SKILL_DIR/timelog_report.py" \
  --input "$STATES_DIR/.inferred.json" \
  --output "$DATA_DIR" \
  --weekend-target "$WEEKEND_TARGET" \
  --log "$LOG_FILE" || {
  log_msg "ERROR: Report generation failed"
  exit 1
}

log_msg "Report generation complete"

# Step 4: Display summary
log_msg "====== Summary ======"
if [ -f "$STATES_DIR/timelog_summary.md" ]; then
  cat "$STATES_DIR/timelog_summary.md"
fi

echo ""
log_msg "Generated files:"
find "$DATA_DIR/daily-summaries" -maxdepth 1 -name "*.md" -type f 2>/dev/null | while read -r f; do
  echo "  - daily-summaries/$(basename "$f")"
done

if [ -d "$DATA_DIR/scan-results" ]; then
  find "$DATA_DIR/scan-results" -maxdepth 1 -name "*.json" -type f 2>/dev/null | while read -r f; do
    echo "  - scan-results/$(basename "$f")"
  done
fi

log_msg "====== Complete ======"
