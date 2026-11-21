#!/usr/bin/env bash
# Contract test for skills/agent-desk/SKILL.md.
#
# Static checks (always): frontmatter, every command of the agent-desk CLI surface, the closed exit-code
# set 0-4 with each code's next step, per-agent long-command guidance, Session id resolution, carried-over
# open Asks, Workstream-wide inbox, bulk withdraw, Artifact Review (submit/reviews/comment surface and
# exit codes), and the exact domain vocabulary (no synonyms).
#
# Drift check (when a CLI is available): set AGENT_DESK_CLI to the CLI entry (e.g.
# ~/projects/tools/agent-desk/dist/delivery/cli/main.js), or have `agent-desk` on PATH. The CLI's usage
# text must list every command and flag the skill documents.
#
# Exits non-zero and names each failed check.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_MD="${SKILL_MD:-$(cd "$SCRIPT_DIR/.." && pwd)/SKILL.md}"

failures=()
fail() { failures+=("$1"); }

if [ ! -f "$SKILL_MD" ]; then
  echo "FAIL: $SKILL_MD not found" >&2
  exit 1
fi
SKILL="$(cat "$SKILL_MD")"
COMMAND_BLOCK="$(awk '/^## Commands/ {inside=1; next} inside && /^```/ {if (fence) exit; fence=1; next} fence {print}' <<<"$SKILL")"

has() { grep -qF -- "$1" <<<"$SKILL"; }
has_re() { grep -qE -- "$1" <<<"$SKILL"; }
has_cmd() { grep -qF -- "$1" <<<"$COMMAND_BLOCK"; }

# --- Frontmatter -------------------------------------------------------------------------------
[ "$(head -n 1 "$SKILL_MD")" = "---" ] || fail "frontmatter: first line is not ---"
FRONTMATTER="$(awk 'NR==1 && $0=="---" {inside=1; next} inside && $0=="---" {exit} inside {print}' "$SKILL_MD")"
grep -qx 'name: agent-desk' <<<"$FRONTMATTER" || fail "frontmatter: missing 'name: agent-desk'"
DESCRIPTION="$(grep -E '^description: ' <<<"$FRONTMATTER" | sed 's/^description: //')"
[ -n "$DESCRIPTION" ] || fail "frontmatter: missing description"
grep -qE '\bUse (at|when|to|for)\b' <<<"$DESCRIPTION" || fail "frontmatter: description has no trigger ('Use at/when/to/for ...')"
for agent in 'Claude Code' 'Codex' 'AGY'; do
  grep -qF "$agent" <<<"$DESCRIPTION" || fail "frontmatter: description does not name $agent"
done

# --- CLI surface ---------------------------------------------------------------------------------
COMMANDS=(start update ask detail wait inbox withdraw end submit reviews comment)
for cmd in "${COMMANDS[@]}"; do
  has_re "agent-desk ${cmd}( |\$|\`)" || fail "surface: 'agent-desk ${cmd}' not documented"
done
has_cmd 'agent-desk start --agent claude|codex|agy --title' || fail "surface: start --agent claude|codex|agy --title"
has_cmd '[--workstream NAME]' || fail "surface: start --workstream"
has_cmd 'agent-desk update "one-line status" [--body TEXT]' || fail "surface: update --body"
has_cmd 'agent-desk ask "question" [--risk] [--choices a,b,c] [--no-wait]' || fail "surface: ask flags"
has_cmd 'agent-desk detail <ask-id> "more detail"' || fail "surface: detail <ask-id>"
has_cmd 'agent-desk wait <ask-id>' || fail "surface: wait <ask-id>"
has_cmd 'agent-desk withdraw <ask-id...>' || fail "surface: withdraw <ask-id...>"
has_cmd 'agent-desk withdraw --all' || fail "surface: withdraw --all"
has_cmd 'agent-desk end [--summary TEXT]' || fail "surface: end --summary"
has '--session ID' || fail "surface: --session ID"

# --- Exit codes: closed set 0-4, each with its next step ----------------------------------------
EXIT_ROWS="$(grep -E '^\| `[0-9]+` \|' <<<"$SKILL")"
codes="$(sed -E 's/^\| `([0-9]+)`.*/\1/' <<<"$EXIT_ROWS" | tr '\n' ' ')"
[ "$codes" = "0 1 2 3 4 " ] || fail "exit codes: table rows are '${codes}', expected exactly 0 1 2 3 4"
row() { grep -E "^\| \`$1\` \|" <<<"$EXIT_ROWS"; }
row 0 | grep -qi 'answered' || fail "exit 0: does not say answered"
row 0 | grep -qi 'stdout is the answer' || fail "exit 0: does not say stdout is the answer"
row 1 | grep -qi 'error' || fail "exit 1: does not say error"
row 1 | grep -qF 'agent-desk start' || fail "exit 1: unknown session does not point to agent-desk start"
row 1 | grep -qi 'newer' || fail "exit 1: does not cover 'a newer wait took over'"
row 2 | grep -qi 'timed out' || fail "exit 2: does not say timed out"
row 2 | grep -qF 'agent-desk wait <ask-id>' || fail "exit 2: next step is not 'agent-desk wait <ask-id>'"
row 3 | grep -qi 'clarify' || fail "exit 3: does not say clarify"
row 3 | grep -qF 'agent-desk detail <ask-id>' || fail "exit 3: next step is not 'agent-desk detail <ask-id>'"
row 4 | grep -qi 'withdrawn' || fail "exit 4: does not say withdrawn"
row 4 | grep -qi 'closed' || fail "exit 4: does not say closed"

# --- Per-agent long-command guidance (contract-fixtures/subprocess-agent-shell-timeout.json) ------
has 'timeout: 600000' || fail "claude: Bash 'timeout: 600000' not stated"
grep -A3 -F '**Codex:**' <<<"$SKILL" | grep -qi 'poll' || fail "codex: polling guidance missing"
grep -A3 -F '**AGY:**' <<<"$SKILL" | grep -qi 'background' || fail "agy: backgrounding guidance missing"
grep -A3 -F '**AGY:**' <<<"$SKILL" | grep -qi 'notified' || fail "agy: completion notice guidance missing"
has_re '9 minutes|540 s' || fail "wait deadline (9 minutes / 540 s) not stated"
grep -A3 -F '**Long waits.**' <<<"$SKILL" | grep -qF -- '--no-wait' || fail "long waits: --no-wait not described"
grep -A4 -F '**Long waits.**' <<<"$SKILL" | grep -qF 'agent-desk inbox' || fail "long waits: inbox not described"

# --- Artifact Review: submit / reviews / comment (contracts.md section 5) ------------------------
REVIEW_COMMANDS=(submit reviews comment)
for cmd in "${REVIEW_COMMANDS[@]}"; do
  has_re "agent-desk ${cmd}( |\$|\`)" || fail "review surface: 'agent-desk ${cmd}' not documented"
done
has_cmd 'agent-desk submit <path> [--title T] [--kind K] [--no-wait]' || fail "review surface: submit flags"
has_cmd 'agent-desk reviews [art_<n>] [--wait]' || fail "review surface: reviews [art_<n>] [--wait]"
has_cmd 'agent-desk comment <thr_id> "text" [--resolve] [--wait]' || fail "review surface: comment flags"
has_cmd 'agent-desk withdraw art_<n...>' || fail "review surface: withdraw art_<n...>"
has '## 7. Submit an Artifact for review' || fail "review: 'Submit an Artifact for review' section missing"
grep -A3 -F '## 4. Long-running' <<<"$SKILL" | tr '\n' ' ' | grep -qF 'submit` (without `--no-wait`), `reviews --wait` and `comment --wait`' \
  || fail "review: submit, reviews --wait and comment --wait not under the 9-minute rule"
sed -n '/^## 1\. Start a Session/,/^## 2\./p' <<<"$SKILL" | grep -qF 'unreceived Reviews' || fail "review: start does not list Artifacts with unreceived Reviews"

# Submit exit codes use plain digits, so the Ask table above remains the only backticked 0-4 table.
REVIEW_SECTION="$(sed -n '/^## 7\. Submit an Artifact for review/,/^## Setup/p' <<<"$SKILL")"
REVIEW_EXIT_ROWS="$(grep -E '^\| [0-9] \|' <<<"$REVIEW_SECTION")"
review_codes="$(sed -E 's/^\| ([0-9]) \|.*/\1/' <<<"$REVIEW_EXIT_ROWS" | tr '\n' ' ')"
[ "$review_codes" = "0 3 2 4 1 " ] || fail "submit exit codes: table rows are '${review_codes}', expected exactly 0 3 2 4 1"
review_row() { grep -E "^\| $1 \|" <<<"$REVIEW_EXIT_ROWS"; }
review_row 0 | grep -qi 'approved' || fail "submit exit 0: does not say approved"
review_row 3 | grep -qi 'changes requested' || fail "submit exit 3: does not say changes requested"
review_row 3 | grep -qi 'Comment block' || fail "submit exit 3: does not point to the Comment block"
review_row 2 | grep -qi 'timed out' || fail "submit exit 2: does not say timed out"
review_row 2 | grep -qF 'agent-desk reviews art_<n> --wait' || fail "submit exit 2: next step is not 'agent-desk reviews art_<n> --wait'"
review_row 4 | grep -qi 'withdrawn' || fail "submit exit 4: does not say withdrawn"
review_row 4 | grep -qi 'closed' || fail "submit exit 4: does not say closed"
review_row 1 | grep -qi 'error' || fail "submit exit 1: does not say error"
grep -qF 'Never re-submit' <<<"$REVIEW_SECTION" || fail "submit exit 2: does not forbid re-submitting to keep waiting"

# Acting on the A4 block: edit + submit, resolve via comment, ask back via comment --wait.
has 'agent-desk comment thr_7 "Switched to branch deploy in §3" --resolve' || fail "review: resolve example missing"
has 'agent-desk comment thr_7 "Does staging have a slot?" --wait' || fail "review: ask-back --wait example missing"
has 'Resolving every Thread does not approve' || fail "review: resolving must not approve is not stated"
has 'never a **Reply** or a **Detail**' || fail "review: review traffic must never be a Reply or Detail"

# --- Session id resolution ----------------------------------------------------------------------
has '`--session ID` > `$AGENT_DESK_SESSION` >' || fail "session: precedence --session > \$AGENT_DESK_SESSION > native id not stated"
for var in CLAUDE_CODE_SESSION_ID CODEX_THREAD_ID ANTIGRAVITY_CONVERSATION_ID; do
  has "$var" || fail "session: agent-native variable $var not named"
done

# --- Carried-over Asks, Workstream inbox, bulk withdraw (flow amendment A1) ----------------------
has 'open Asks left by' || fail "start: does not say it lists open Asks left by earlier Sessions"
has 'earlier Sessions' || fail "start: does not mention earlier Sessions"
grep -i 'inbox' <<<"$SKILL" | grep -q 'Workstream' || fail "inbox: does not say it covers the Workstream"
grep -A1 -F 'agent-desk withdraw --all` withdraws' <<<"$SKILL" | tr -s ' \n' ' ' | grep -qi 'only when the user asks' || fail "withdraw --all: not limited to when the user asks"

# --- Progress updates, one-click choices, proactive withdraw -----------------------------------
has '[k/n]' || fail "update: '[k/n]' step progress prefix not documented"
grep -A6 -F '## 2. Post an Update' <<<"$SKILL" | grep -qi 'step progress' || fail "update: step progress guidance missing"
grep -A6 -F '## 3. Raise an Ask' <<<"$SKILL" | grep -qi 'one click' || fail "ask: one-click guidance with --choices missing"
grep -A6 -F '## 5. Withdraw Asks you no longer need' <<<"$SKILL" | grep -qF 'agent-desk withdraw <ask-id>' || fail "withdraw: proactive withdraw <ask-id> not documented"
grep -A6 -F '## 5. Withdraw Asks you no longer need' <<<"$SKILL" | grep -qi 'no longer needed\|obsolete\|clutter' || fail "withdraw: proactive cleanup when no longer needed missing"

# --- Exact vocabulary ----------------------------------------------------------------------------
for term in Workstream Session Update Ask Reply Detail Artifact Revision Thread Comment Review; do
  grep -qE "\\b${term}\\b" <<<"$SKILL" || fail "vocabulary: '$term' never used"
done
for kind in question risk answer clarify; do
  grep -qF "\`${kind}\`" <<<"$SKILL" || fail "vocabulary: kind '\`${kind}\`' not named"
done
# Synonyms for the lexicon terms are defects (case-sensitive, whole word).
for synonym in Workspace Channel Ticket Conversation Request Response Clarification Followup Follow-up Notification Message Prompt Job Task; do
  if grep -qE "\\b${synonym}s?\\b" <<<"$SKILL"; then
    fail "vocabulary: synonym '$synonym' used; use the lexicon term"
  fi
done
# Artifact Review synonyms (case-insensitive, whole word). "note" is checked only where it names a
# review message, because "progress note" is a legitimate Update description.
grep -qiE '\bfeedback\b' <<<"$SKILL" && fail "vocabulary: synonym 'feedback' used; use Comment or Review"
grep -qiE '\bannotations?\b' <<<"$SKILL" && fail "vocabulary: synonym 'annotation' used; use Comment"
grep -qiE '\bdocs?\b' <<<"$SKILL" && fail "vocabulary: synonym 'doc' used; use Artifact"
grep -qiE '\b(review|comment|thread|artifact|reviewer|user|agent|inline|draft)s? notes?\b' <<<"$SKILL" \
  && fail "vocabulary: 'note' used for a review message; use Comment"
grep -qiE '\bnotes? (on|for|about|to) (the |a |an |this |that |your |their )?(artifact|thread|revision|review|comment|plan|file)\b' <<<"$SKILL" \
  && fail "vocabulary: 'note' used for a review message; use Comment"

# --- Drift against the real CLI (optional) ------------------------------------------------------
CLI=()
if [ -n "${AGENT_DESK_CLI:-}" ]; then
  CLI=(node "$AGENT_DESK_CLI")
elif command -v agent-desk >/dev/null 2>&1; then
  CLI=(agent-desk)
fi
if [ "${#CLI[@]}" -gt 0 ]; then
  # With no arguments the CLI prints its usage and exits 1 (it never prompts).
  USAGE="$("${CLI[@]}" 2>&1 </dev/null)"
  for cmd in "${COMMANDS[@]}"; do
    grep -qE "^\s*agent-desk ${cmd}( |\$)" <<<"$USAGE" || fail "drift: CLI usage has no 'agent-desk ${cmd}'"
  done
  for flag in --agent --title --workstream --body --risk --choices --no-wait --session --all --summary; do
    grep -qF -- "$flag" <<<"$USAGE" || fail "drift: CLI usage has no '$flag'"
  done
  grep -qE 'Exit codes: 0 .*1 .*2 .*3 .*4 ' <<<"$USAGE" || fail "drift: CLI usage exit codes are not 0-4"
  echo "Drift check against: ${CLI[*]}"
else
  echo "Drift check skipped: set AGENT_DESK_CLI or put agent-desk on PATH"
fi

if [ "${#failures[@]}" -gt 0 ]; then
  printf 'FAIL: %s\n' "${failures[@]}" >&2
  exit 1
fi
echo "PASS: agent-desk SKILL.md contract"
exit 0
