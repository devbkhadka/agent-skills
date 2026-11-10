---
name: feature-memory
description: Lightweight, living routing-hub cheatsheet ("shared brain") for a feature branch — one small MEMORY.md per feature under dev-plans/<slug>/memory/ (the memory/ subfolder is self-gitignored), giving quick-start commands, a concept-to-code domain map, and a linked-out status/roadmap. Not a decision journal or milestone tracker (see subagent-feature-driver / FEATURE_STATE.md for that) and not a wave/dispatch loop (see agent-herder for that) — both compose this skill in as a dependency for feature orientation. Use at the start of a multi-session feature to orient fast, and whenever the domain map or status would otherwise have to be re-derived from scratch.
---

# Feature Memory

A per-feature `MEMORY.md` that indexes, rather than explains: quick-start commands and env
pointers, a concept → source-of-truth table, and a status line that links out to task detail
instead of restating it. High-signal, low-noise — if an entry needs more than one line, it's a
pointer to a file, not prose here.

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory path.
FEATURE_MEMORY_SKILL_DIR=/absolute/path/from-the-skill-source-locator/feature-memory
S="$FEATURE_MEMORY_SKILL_DIR/scripts"
```

## 1. Resolve the feature folder

Convention: the folder is always `<FEATURE_MEMORY_ROOT>/<slug>`, where `<slug>` is the current
branch name, slugified — no separate search/matching step, so there's nothing to disambiguate.
Branches are normally already ticket-prefixed (`IN-1234-checkout-redesign`), so the slug carries
that for free.

```bash
ROOT="${FEATURE_MEMORY_ROOT:-dev-plans}"
slug="$("$S/feature-slug.sh")" || slug=""   # empty on main/master/develop/detached HEAD
```

- **`slug` non-empty** — `DIR="$ROOT/$slug"` (never name this variable `PATH` — it shadows the
  shell's executable search path). If `$DIR/memory/MEMORY.md` exists, read it and use as-is.
  Otherwise state the inferred name in one sentence and scaffold it (tactical, reversible — not
  worth a question):
  ```bash
  mkdir -p "$DIR/memory"
  printf '*\n' > "$DIR/memory/.gitignore"    # self-contained; ignores memory/ only
  cp "$FEATURE_MEMORY_SKILL_DIR/templates/MEMORY.md" "$DIR/memory/MEMORY.md"
  ```
  Then fill in the template's placeholders from what you already know about the feature.
- **`slug` empty** — the branch (`main`, `master`, detached `HEAD`, ...) carries no feature
  signal. Ask the user for a short feature name/ticket id, slugify it the same way, and proceed
  as above.

## 2. Read before you dig

Once resolved, read `memory/MEMORY.md` **before** grepping the codebase for context you might
already have written down — that's the entire point of the file. Trust its domain map's file
pointers over re-discovering them, but verify a pointer still resolves (file exists, symbol still
there) before acting on it — code moves faster than docs.

## 3. Keep it lean when writing

- One row per concept in the domain map — a *pointer*, not a description of how the code works.
- Status section links to a task file (e.g. `../checkout-tasks.md`) or an external ticket/board
  for granular detail; it does not enumerate sub-tasks itself.
- Never record secrets in `MEMORY.md` — env/sandbox entries are pointers to where credentials
  live (a vault path, a `.env.example`, a doc), never the values.
- Update at checkpoints (session start/end, or when a concept's file moves), not per edit. See
  [references/update-discipline-and-hooks.md](references/update-discipline-and-hooks.md) for the
  full rationale, plus whether to wire this to a `SessionStart`/`PreCompact` hook (short answer:
  auto-load on session start is fine, auto-write on tool-use is not).

## 4. Keep it out of git

Only `memory/` is local-only (env pointers, personal notes) — the scaffold step above drops a
`*` `.gitignore` inside it, so it's excluded via git's normal nested-ignore support without
touching the repo's own root `.gitignore` or any shared config. Sibling files in the feature
folder (e.g. `checkout-tasks.md`) are NOT covered by that rule and may be committed normally if
the team wants them shared.
