# Phase Guide: Stage 1 — Contract Probing & External Interface Verification

**Stage:** `STAGE_1_CONTRACT_PROBING`  
**Owner:** `stage-1`  
**Required Exit Artifact:** `contract-fixtures/manifest.json` (under `.verified-feature-delivery/<feature-slug>/`)  
**Gate Validators:** `bin/contract-probe-runner`, `bin/verify-fixture-variance`

---

## Purpose & Process

1. Emit `contract-fixtures/manifest.json` covering all 10 Boundary Register classes.
2. For each present class, record per-property verdicts (`PROBED` or `NOT_PRESENT`).
3. For each probed property, record:
   - Success sample(s)
   - Failure envelope census
   - Partitioned payload variance census
4. Stamp every fixture with `fixture_status` (`PROBED_LIVE`, `REPLAYED_FROM_RECORDING`, `PROVISIONAL_FROM_SCHEMA`).
5. For provisional fixtures, pin a named Stage 4 live re-probe milestone.
6. **Reuse from the project baseline.** A feature initialised with `planner-state init --baseline-dir docs/domain` starts with `contract-fixtures/manifest.json` already holding the rows Stage 1 may reuse — boundary classes the baseline records as `PRESENT`, every property `PROBED` (or `NOT_PRESENT`), each fixture `PROBED_LIVE` or `REPLAYED_FROM_RECORDING` — with their fixtures copied (`state.json` `baseline.reused_boundary_classes`). Do not re-probe those. Every other class still needs its row: a baseline `NOT_PRESENT` row reading "Baseline initialized unprobed." means never probed, not absent, so it is never seeded and must be probed here.

---

## Tooling & Driver Role

### Driver Role Rule
The driving agent must not run network probes, subshell execution probes, or database queries directly. It dispatches a probe worker agent to capture boundary samples and write fixture manifests.

