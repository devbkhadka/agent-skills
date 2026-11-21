---
name: domain-baseline
description: Build or amend the project's committed domain baseline — ubiquitous language, entities with clear boundaries, unit precisions, external boundary contract fixtures — that the other skills reuse instead of re-deriving. Use at project start, or when a change introduces a new entity or boundary. Usable standalone, no planner-state.
---

# Domain Baseline

The domain knowledge verified-feature-delivery builds in Stages 0 and 1 (lexicon, entity
boundaries, precisions, probed contract fixtures) normally lives per feature and is rebuilt every
time. This skill promotes it once into a **project baseline** committed to the repository, so later
work reuses it: `feature-gaps` checks terms against it, `change-lane` classifies diffs against it,
and the full pipeline seeds new features from it and skips re-probing recorded boundaries.

It does **not** need `planner-state`. Engine paths are relative to this skill directory; the
baseline lives in `docs/domain/` by default (`--baseline-dir` to override).

```
docs/domain/
  baseline.json            # sources, element_digests, approvals (element id -> approved digest)
  domain-lexicon.json      # merged lexicon, same schema as Stage 0
  contract-fixtures/       # merged Stage 1 manifest + fixtures
  triage.json              # optional change-lane config
  docs/                    # rendered glossary + entity map — generated, read-only
```

Element ids are `<kind>:<id>`, e.g. `entity:Invoice`, `state_enum:InvoiceStatus`,
`boundary:billing`, `unit_precision:amount_cents`. Approval is per element: amending one entity
asks for re-approval of that entity only.

## When NOT to use

- A change that adds no term, entity, boundary or contract — use `change-lane`; a TWEAK never
  touches the baseline.
- Exploring a feature's states and failure paths — that is `feature-gaps` (baseline optional).

## Procedure

`B="python3 ../verified-feature-delivery/bin/baseline"`

1. **Start or resume.** `$B init` if `docs/domain/` is absent. Otherwise `$B show` — this is an
   amendment; read the existing lexicon and keep its words (one word per concept).
2. **Author a working lexicon** in a scratch dir (e.g. `.verified-feature-delivery/<slug>/`)
   following `../verified-feature-delivery/phases/0-domain-ingestion/guide.md` and
   `../verified-feature-delivery/schemas/domain-lexicon.schema.json`, then validate:
   `python3 ../verified-feature-delivery/bin/validate-lexicon <scratch>/domain-lexicon.json`.
3. **Probe only new boundaries.** Read `docs/domain/contract-fixtures/manifest.json`; a boundary
   class with verdict `PRESENT` whose properties are `PROBED` (fixtures `PROBED_LIVE` or
   `REPLAYED_FROM_RECORDING`) is already recorded — do not re-probe it. (`NOT_PRESENT` with reason "Baseline initialized unprobed." means
   *never probed*, not absent.) For the rest follow
   `../verified-feature-delivery/phases/1-contract-probing/guide.md` and `boundary-classes.md`,
   using `bin/contract-probe-runner` and validating with `bin/verify-fixture-variance`.
4. **Show the human what changes, then promote.**
   `$B diff --lexicon <scratch>/domain-lexicon.json` lists added / changed / removed element ids.
   `$B promote --feature-dir <scratch>`; changing an existing element requires naming it:
   `--amend entity:Invoice`. Promotion re-renders `docs/domain/docs/`.
5. **Approve.** The human reviews `docs/domain/docs/domain-entity-map.md` and the glossary, then
   `$B approve --element <id>...` for what changed (`--all` only on first creation).
6. **Guard in CI.** Add `$B check --strict-approvals` so the baseline cannot drift silently: it
   fails on stale rendered docs, unapproved elements and stale approvals.

Commit `docs/domain/` with the change that needed it.

## Reason codes (as `bin/baseline` emits them)

| Code | Meaning |
|---|---|
| `BASELINE_INITIALIZED` | Layout created |
| `BASELINE_PROMOTED` | Feature lexicon + fixtures merged; docs re-rendered |
| `BASELINE_CONFLICT` | Promotion would change existing element(s) not named in `--amend`; nothing written (exit 1) |
| `BASELINE_APPROVED` | Element digests recorded as approved |
| `UNKNOWN_ELEMENT` | `approve --element` named an id not in the baseline |
| `BASELINE_CHECK_CLEAN` | Every element approved at its current digest, docs fresh |
| `BASELINE_CHECK_PASSED` | Passes, but lists unapproved/stale elements (advisory without `--strict-approvals`) |
| `UNAPPROVED_ELEMENT` | Element never approved (refused with `--strict-approvals`) |
| `STALE_APPROVAL` | Element changed since approval — re-approve that element only (refused with `--strict-approvals`) |
| `STALE_RENDERED_DOCS` | `docs/` differs from what the lexicon renders — someone hand-edited it; re-promote |
| `MISSING_BASELINE` / `MISSING_BASELINE_LEXICON` | Run `init` first |
| `MISSING_FEATURE_LEXICON` / `INVALID_FEATURE_LEXICON` / `INVALID_FIXTURE_MANIFEST` | Fix the scratch dir and validate it (step 2–3) |
