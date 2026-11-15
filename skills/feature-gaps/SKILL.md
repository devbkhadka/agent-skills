---
name: feature-gaps
description: Find missing states, unconsidered branches, dead ends and unobservable outcomes in a feature, then emit Given/When/Then acceptance rules for it. Usable standalone — no planner-state, no Stage 0/1, baseline optional.
---

# Feature Gaps

Model a feature as a flow-simulation matrix, list **every** gap in it at once, and turn the clean
matrix into Given/When/Then acceptance rules you commit to the repository as high-level test rules.

This is Stage 2 of [verified-feature-delivery](../verified-feature-delivery/SKILL.md) on its own.
It does **not** need `planner-state`, approvals, a domain lexicon (Stage 0) or contract fixtures
(Stage 1). Paths below are relative to this skill directory.

## When to use

- Before building a feature: "what states and failure paths have we not thought about?"
- Reviewing a spec or an existing flow for dead ends and unobservable outcomes.
- Producing acceptance rules a reviewer can read and a test suite can implement.

## Procedure

1. **Author the matrix** (`flow-simulation-matrix.json`) against
   `../verified-feature-delivery/schemas/scenario-matrix.schema.json`. Read
   `../verified-feature-delivery/phases/2-scenario-simulation/branch-taxonomies.md` (Group A
   resource/state branches, Group B trigger/observation branches) and
   `../verified-feature-delivery/phases/2-scenario-simulation/observability-gate.md` (every state
   needs an evidence key and a per-observer discriminator).

2. **Find gaps** — every one, not the first:
   ```bash
   python3 ../verified-feature-delivery/bin/find-gaps flow-simulation-matrix.json \
     --out gaps/ [--baseline-dir docs/domain] [--json] [--strict]
   ```
   Writes `gaps/gap-report.md` (grouped by gap type) and `gaps/gap-report.json`. Gap codes are the
   Stage 2 gate's own reason codes (`HANGING_STATE`, `BRANCH_CLASS_UNADDRESSED`,
   `MISSING_DISCRIMINATOR`, `STATE_NOT_COVERED_BY_FLOW`, …), so a gap fixed here never resurfaces
   later under another name. Advisory only: `DISCRIMINATOR_COLLISION_ADVISORY` and, when
   `--baseline-dir` holds a project `domain-lexicon.json`, `UNVERIFIED_TERM` for states, triggers,
   actors and observers the baseline does not know (see the `domain-baseline` skill).
   Exit 0 when it ran; `--strict` exits 1 while any blocking gap remains (use in CI).

3. **Iterate** on the matrix until `GAPS_CLEAN`. Each blocking gap is a product question
   (what happens when…?) — answer it in the matrix, not in code.

4. **Emit acceptance rules**:
   ```bash
   python3 ../verified-feature-delivery/bin/emit-acceptance-rules flow-simulation-matrix.json \
     --out tests/acceptance/ [--format gherkin|markdown]
   ```
   One `Feature` per flow group, one `Scenario` per flow: `Given` the preconditions and start
   state, `When` each trigger by its actor, `Then` each state reached, ending with what every
   observer sees (the state's discriminator). Output is deterministic, so re-running produces no
   diff; commit `acceptance-rules.feature` and implement it with your test runner of choice.

## Going further

- Need clear entity boundaries and module rules for the code? → `architecture-rules`.
- Want the terms checked against a committed project vocabulary? → `domain-baseline`.
- Delivering the whole feature with verified evidence? → `verified-feature-delivery`.
