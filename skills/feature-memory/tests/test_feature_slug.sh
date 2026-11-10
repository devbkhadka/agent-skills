#!/usr/bin/env bash
# Smoke test for feature-slug.sh against a throwaway git repo.
# Run: bash tests/test_feature_slug.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SLUG="$SCRIPT_DIR/scripts/feature-slug.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
git init -q -b scratch-init
git commit -q --allow-empty -m init

fail=0
assert_eq() {
  local actual="$1" expected="$2" label="$3"
  if [ "$actual" != "$expected" ]; then
    echo "FAIL: $label — expected '$expected', got '$actual'"
    fail=1
  else
    echo "ok: $label"
  fi
}
assert_fails() {
  local label="$1"
  if "$SLUG" >/tmp/feature-slug-out 2>/dev/null; then
    echo "FAIL: $label — expected non-zero exit, got: $(cat /tmp/feature-slug-out)"
    fail=1
  else
    echo "ok: $label"
  fi
}

git checkout -q -B IN-1234-checkout-redesign
assert_eq "$("$SLUG")" "in-1234-checkout-redesign" "ticket-prefixed branch slugifies as-is"

git checkout -q -B feature/quota-watch-improvements
assert_eq "$("$SLUG")" "quota-watch-improvements" "feature/ prefix is stripped"

git checkout -q -B main
assert_fails "generic branch 'main' has no signal"

git checkout -q -B "Fix/Weird_Chars!!v2"
assert_eq "$("$SLUG")" "weird-chars-v2" "non-alnum runs collapse to single dashes"

exit $fail
