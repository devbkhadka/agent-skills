# Phase Guide: Stage 2 — Scenario Simulation & Pre-Implementation Replay

**Stage:** `STAGE_2_SCENARIO_SIMULATION`  
**Owner:** `stage-2`  
**Required Exit Artifacts:**
- `flow-simulation-matrix.json` (under `.verified-feature-delivery/<feature-slug>/`)
- `docs/flow-simulation.md` (rendered via `bin/render-flow-simulation`)
**Gate Validators:** `bin/validate-scenario-matrix`, `bin/verify-projections`

---

## 1. Purpose & Process

1. Model system and observed states across all actors and observers.
2. Complete 100% state coverage with explicit egress transitions (no hanging states).
3. Cover Branch Class Group A (Resource & State) and Group B (Trigger & Observation).
4. Materialise supersession resolutions as distinct states before the projection step runs.
5. Attach a structured `observable_projection` with discriminator per state per observer.
6. Validate pairwise discriminator collisions (advisory at Stage 2; raises `[Decision Needed]`).

---

## 2. Tooling & Exit Gate Verification

### Driver Role Rule
The driving agent does not author simulation branches or test matrix transitions directly. It dispatches a simulation modeling worker and reviews the structured output.

### Render Readable Document
```bash
python3 bin/render-flow-simulation --artifact .verified-feature-delivery/<feature-slug>/flow-simulation-matrix.json --out .verified-feature-delivery/<feature-slug>/docs
```

`--out` takes the output directory or the document's own path (`docs/<doc name>`); an existing
derived document is overwritten in place. A file path naming any other document, or an existing
file given as the directory, is refused with `INVALID_OUTPUT_PATH`.

### Validate Gate
```bash
python3 bin/validate-scenario-matrix .verified-feature-delivery/<feature-slug>/flow-simulation-matrix.json
python3 bin/verify-projections .verified-feature-delivery/<feature-slug>/flow-simulation-matrix.json
```

### Exit Gate Criteria
- Zero missing egress transitions or dead-end states (`UNBOUND_STATE_EGRESS`).
- Full coverage of Branch Class Groups A and B (`INCOMPLETE_BRANCH_COVERAGE`).
- Readable Flow Simulation document present on disk (`docs/flow-simulation.md`).
- Human approval recorded via `bin/record-approval --milestone FLOW_SIMULATION` before advancing to Stage 3.
- Human approval recorded via `bin/record-approval --milestone ENTITY_BOUNDARIES`, which digests the
  `entity_boundaries`, `entity_relationships` and `simulated_dependencies` declared in
  `domain-lexicon.json` (the source of `docs/domain-entity-map.md`). A lexicon carrying none of them is
  refused with `EMPTY_APPROVAL_SCOPE`: a digest over nothing could never go stale.
- An approval whose guarded content changes after the pipeline has passed its gate is invalidated by
  the next `planner-state advance` (`STALE_APPROVAL_INVALIDATED`): the feature returns to that gate,
  the record goes `STALE`, and its derived documents are deleted, in that order. A `STALE` record found
  past its gate (an interrupted invalidation) routes the feature back to the gate the same way, so
  re-running `advance` then `record-approval` always recovers.

