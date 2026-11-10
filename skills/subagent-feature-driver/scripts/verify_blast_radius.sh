#!/usr/bin/env bash
# verify_blast_radius.sh
# Validates touched files against an expected target directory or path prefix,
# prints concise git diff statistics, and detects untracked out-of-scope files.
#
# Usage:
#   ./verify_blast_radius.sh [ALLOWED_PREFIX...]
# Example:
#   ./verify_blast_radius.sh src/domain/ src/application/ tests/

set -euo pipefail

ALLOWED_PATHS=("$@")

echo "=== Git Working Tree Status ==="
CHANGED_FILES=$(git status --porcelain | awk '{print $2}')

if [ -z "$CHANGED_FILES" ]; then
    echo "Verification: Working tree is clean. No uncommitted changes."
    exit 0
fi

echo "Touched files:"
echo "$CHANGED_FILES" | sed 's/^/  - /'

echo ""
echo "=== Git Diff Summary ==="
git diff --stat

if [ ${#ALLOWED_PATHS[@]} -eq 0 ]; then
    echo ""
    echo "Notice: No boundary prefixes specified. Skipping blast-radius path check."
    exit 0
fi

echo ""
echo "=== Checking Blast Radius Boundaries ==="
VIOLATIONS=0

while IFS= read -r file; do
    [ -z "$file" ] && continue
    MATCHED=0
    for prefix in "${ALLOWED_PATHS[@]}"; do
        if [[ "$file" == "$prefix"* ]]; then
            MATCHED=1
            break
        fi
    done
    if [ "$MATCHED" -eq 0 ]; then
        echo "[VIOLATION] File modified outside allowed boundary: $file"
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done <<< "$CHANGED_FILES"

if [ "$VIOLATIONS" -gt 0 ]; then
    echo "ERROR: Blast radius check failed with $VIOLATIONS out-of-boundary file(s)." >&2
    exit 1
else
    echo "Verification: All modified files fall within specified boundaries."
    exit 0
fi
