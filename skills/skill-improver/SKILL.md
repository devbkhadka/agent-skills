---
name: skill-improver
description: "Record bugs, friction, edge cases, or improvement suggestions encountered while using any skill or workflow tool. Fire-and-forget, non-blocking issue recorder."
---

# Skill Improver

Ultra-lite, fire-and-forget feedback recorder for skills. When encountering defects, unhandled edge cases, or friction while using a skill, record actionable feedback non-blockingly without interrupting the current task.

```bash
# Locate skill scripts directory
SKILL_IMPROVER_DIR="$(dirname "$(realpath "$0")")" # or path from loaded SKILL.md
S="$SKILL_IMPROVER_DIR/scripts"

# Record an issue or improvement (fire-and-forget, non-blocking)
python3 "$S/suggest.py" \
  --skill <skill-name> \
  --priority <critical|high|medium|low> \
  --summary "Concise 1-line summary" \
  --detail "Optional detailed explanation of what failed" \
  --category <critical|bug|improvement> \
  --agent <gemini|claude|codex|omp> \
  --session-id "<session-or-task-id>" \
  --fix "Optional suggested fix or diff"
```

---

## Required Arguments

| Argument | Allowed Values | Description |
| :--- | :--- | :--- |
| `--skill` | String | Target skill name (e.g. `timelog-generator`, `verified-feature-delivery`). |
| `--priority` | `critical` \| `high` \| `medium` \| `low` | Perceived urgency level. |
| `--summary` | String | Concise 1-line description of the friction or enhancement. |

*All other arguments (`--detail`, `--category`, `--agent`, `--session-id`, `--fix`) are optional.*

---

## When to Record Feedback

Whenever you execute a skill or CLI tool and observe:
1. **Critical Blocker (`--priority critical`)**: Tool crashes, corrupts state, hangs, or causes infinite loops. *(Note: for unrelated/external tools, only critical issues are audited).*
2. **Defect / Bug (`--priority high` or `medium`)**: Regex mismatch, missing CLI flag, path resolution error, or unhandled exit code.
3. **Enhancement (`--priority low` / `--category improvement`)**: Ambiguous prompt instructions, token-heavy workflows, or ergonomics optimizations.

---

## Non-Negotiable Rules

1. **Fire-and-Forget**: The CLI runs in `<10ms`, handles file locking automatically, and returns immediately. Never pause or block the user's primary task.
2. **Zero In-Chat Debate**: Do NOT write lengthy apologies or skill critiques in user chats. Record the report silently in the background and continue task execution.
3. **Actionable Detail**: Always supply concrete context (file paths, exact commands run, or specific line numbers) in `--detail` when available.
