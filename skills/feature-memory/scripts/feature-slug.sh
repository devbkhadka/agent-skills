#!/usr/bin/env bash
# Print the feature folder slug derived from the current git branch, or exit 1 with nothing
# on stdout if the branch carries no usable feature signal (generic branch, detached, empty).
# Folder convention: dev-plans/<slug>/ — slug is always derived from the branch name (which is
# itself normally ticket-prefixed, e.g. IN-1234-checkout-redesign), never looked up separately.
set -euo pipefail

branch="$(git branch --show-current 2>/dev/null || true)"
generic='^(HEAD|main|master|develop|dev|trunk)$'

if [ -z "$branch" ] || printf '%s' "$branch" | grep -qE "$generic"; then
  exit 1
fi

slug="$(printf '%s' "$branch" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's#^(feature|feat|fix|bugfix|chore|hotfix)/##' \
  | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"

[ -n "$slug" ] || exit 1
printf '%s\n' "$slug"
