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

