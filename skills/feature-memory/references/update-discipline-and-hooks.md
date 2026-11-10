# Update discipline & why this skill does not use write-hooks

## When to update `MEMORY.md`

Update it at **checkpoints**, not on every edit:
- Session start, if you learn the Quick start / domain map section is stale or missing an entry
  you had to rediscover the hard way (that rediscovery cost is exactly what this file exists to
  avoid next time).
- Session end / before context compaction, if the Status section no longer reflects reality.
- When a concept moves to a new file (rename/refactor) and the domain map would now misdirect
  the next reader.

Do **not** update it: per file edit, per commit, per tool call, or as a running log. It is a
routing hub, not a changelog — if it grows a chronological decision journal, that content belongs
in the feature's task file or ticket, not here. Keep entries pointers ("see X"), not explanations
of how X works.

## Should this use agent hooks?

Two different automation ideas, evaluated separately:

1. **Auto-load at session start** (`SessionStart` hook that cats the resolved `memory/MEMORY.md`
   into context). **Recommended, optional.** This is low-risk: it only reads, never writes, and
   directly serves the "orient quickly" goal without the agent needing to remember this skill
   exists. If your runtime supports a `SessionStart` hook, wire it to run
   `scripts/resolve-feature-folder.sh` and, on `STATUS=FOUND`, print that folder's
   `memory/MEMORY.md`. On any other status, print nothing — let the skill's own instructions
   handle folder inference, since a hook has no way to ask the user a clarifying question.

2. **Auto-update on `PostToolUse`/`PreCompact`** (a hook that rewrites `MEMORY.md` after edits or
   before compaction). **Not recommended.** Three concrete reasons:
   - A hook sees raw tool calls, not intent. It cannot tell "renamed a helper" from "moved a
     concept's source of truth" — the exact distinction this file exists to get right. A wrong
     auto-update is worse than a stale one, because a stale entry just costs a re-discovery while
     a wrong one actively misdirects the next reader (human or agent).
   - It reintroduces the per-edit noise this skill is designed to avoid — the whole point is a
     document that changes at the pace of decisions, not the pace of keystrokes.
   - It's a write happening outside the agent's own reasoning loop, so a bad auto-edit can't be
     caught by "does this match what I just did" — there's no one home to check.

   Prefer a lightweight nudge instead: a `PreCompact` or session-end hook that reminds ("check
   whether dev-plans/<feature>/memory/MEMORY.md's Status section is still accurate") rather than
   one that edits directly. That keeps a human/agent judgment call in the loop for the one section
   (Status) that actually changes often.
