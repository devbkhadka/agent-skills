---
name: architecture-rules
description: Design layer boundaries and vertical slices for a feature, then emit CI-enforced architecture fitness checks (pure-module purity, inward-only dependencies) into the repository. Usable standalone — no planner-state, Stages 0–2 not required.
---

# Architecture Rules

Decide where a feature's boundaries are, slice it into vertical tracer bullets, and turn the
"this module stays pure" decisions into checks your CI keeps running long after the planning
session is gone.

This is Stage 3 of [verified-feature-delivery](../verified-feature-delivery/SKILL.md) on its own.
It does **not** need `planner-state`, approvals, or Stages 0–2. If the repository has a project
baseline (`docs/domain/domain-lexicon.json`, see `domain-baseline`), name slices, modules and
entities with its terms — one word per concept. Paths below are relative to this skill directory.

## Procedure

1. **Lock the six architectural dimensions** in `slice-invariants.json` and validate:
   ```bash
   python3 ../verified-feature-delivery/bin/decision-rules-cli slice-invariants.json
   ```
   Read `../verified-feature-delivery/phases/3-vertical-slicing/guide.md` and
   `actor-modality-matrix.md` in the same directory first.

2. **Author the slice DAG** (`execution-dag.json`, schema
   `../verified-feature-delivery/schemas/execution-dag.schema.json`). Declare every domain module
   that must stay framework- and I/O-free in `pure_modules` (or a slice's
   `legs.core_pure_logic.pure_module_files`). Validate from the repository root so purity is
   computed against real source:
   ```bash
   python3 ../verified-feature-delivery/bin/validate-execution-dag execution-dag.json
   ```

3. **Render the slice plan** for reviewers:
   ```bash
   python3 ../verified-feature-delivery/bin/render-slice-plan --artifact execution-dag.json --out docs/
   ```

4. **Emit fitness checks** and commit them:
   ```bash
   python3 ../verified-feature-delivery/bin/emit-fitness-checks execution-dag.json \
     --out tools/architecture/ [--stack python|typescript|auto] [--repo .]
   ```
   - `check_architecture.py` — zero-dependency gate; run `python3 tools/architecture/check_architecture.py`
     from the repo root in CI. Exits 1 with `IMPURE_PURE_MODULE` (pure module reaches a framework,
     I/O, device or model package), `OUTWARD_DEPENDENCY` (pure module imports a local file outside
     the pure roots), or `PURE_MODULE_NOT_FOUND` (a renamed module would otherwise silently drop
     out of the check). Its import walking is copied from the Stage 3 gate, so CI and the gate agree.
   - `.importlinter` (python) or `.dependency-cruiser.cjs` (typescript) — editor-time feedback in
     the toolchain you already use. Re-emit after changing the DAG; never hand-edit either file.

## Accepted constraints

- Local imports are followed only when written as relative paths (`./x`, `../x`, as in JS/TS).
  Python imports of local packages (`from app.adapters import store`) are not followed, so
  `OUTWARD_DEPENDENCY` does not see them; the emitted `.importlinter` contract is the check there.
- The forbidden-package lists are the Stage 3 gate's; extend them there, then re-emit.

## Going further

- Need the feature's states and failure paths first? → `feature-gaps`.
- Classifying a later change to this code? → `change-lane`.
