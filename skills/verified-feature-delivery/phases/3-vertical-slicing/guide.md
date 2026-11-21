# Phase Guide: Stage 3 — Vertical Slice Breakdown & Decision Engine

**Stage:** `STAGE_3_VERTICAL_SLICING`  
**Owner:** `stage-3`  
**Required Exit Artifacts:**
- `execution-dag.json` (under `.verified-feature-delivery/<feature-slug>/`)
- `slice-invariants.json`
- `docs/vertical-slice-plan.md` (rendered via `bin/render-slice-plan`)
- `docs/ui-mockup-<slice_id>.html` (for each slice declaring `ui_layout`, rendered via `bin/render-ui-mockup`)
**Gate Validators:** `bin/decision-rules-cli`, `bin/validate-execution-dag`

---

## 1. Purpose & Process

1. Lock decisions across all 6 Universal Dimensions via `slicing-decisions.json`.
2. Construct DAG of thin vertical tracer bullets (Trigger -> Core Logic -> Verification).
3. Declare `bootstrap_floor` (minimum files for runnable Slice 1).
4. Compute `shared_substrate` exemptions dynamically from DAG multi-slice reads.
5. Compute purity of declared `pure_modules` against framework/I/O imports.
6. Attach Actor Modality Matrix and concurrency budgets.
7. Validate DAG acyclicity and verification script availability per slice.
8. Declare `milestones[]` census per slice: each slice specifies `milestone_id`, `verification_medium`, and the `claims[]` (or projection targets) that its verification suite will capture and evaluate.
9. For any slice whose `verification_medium` is a UI medium, declare `ui_layout` and clear the
   per-slice `UI_LAYOUT` approval before Stage 4 — see
   [ui-layout-milestone.md](ui-layout-milestone.md).

---

## 2. Milestone Census & Slice Schema

Each slice in `execution-dag.json` defines its milestone commitments for Stage 4 execution and Stage 5 delivery:

```json
{
  "slice_id": "slice_query_to_render",
  "name": "Query to Render Tracer Bullet",
  "dependencies": [],
  "slice_level": "FLOW",
  "user_visible_outcome": "Search results list rendered for query input",
  "flow_refs": ["flow_search_query"],
  "milestones": [
    {
      "milestone_id": "M1_QUERY_RENDER",
      "verification_medium": "rendered_ui",
      "claims": ["claim_state_observable_results", "claim_value_correctness"]
    }
  ]
}
```

---

## 3. Tooling & Exit Gate Verification

### Driver Role Rule
The driving agent must not generate slice DAGs or calculate purity directly. It dispatches a slicing architect worker.

### Render Readable Documents
```bash
python3 bin/render-slice-plan --artifact .verified-feature-delivery/<feature-slug>/execution-dag.json --out .verified-feature-delivery/<feature-slug>/docs
# If UI slices exist:
python3 bin/render-ui-mockup --artifact .verified-feature-delivery/<feature-slug>/execution-dag.json --slice <slice_id> --out .verified-feature-delivery/<feature-slug>/docs
```

`--out` takes the output directory or the document's own path (`docs/<doc name>`); an existing
derived document is overwritten in place. A file path naming any other document, or an existing
file given as the directory, is refused with `INVALID_OUTPUT_PATH`.

### Validate Gate
```bash
python3 bin/validate-execution-dag .verified-feature-delivery/<feature-slug>/execution-dag.json
```

### Exit Gate Criteria
- Valid DAG with zero cycles (`CYCLIC_DEPENDENCY_DETECTED`).
- Verification scripts defined for every slice (`MISSING_VERIFICATION_SCRIPT`).
- Milestones census declared per slice with valid media and claim identifiers (`MISSING_GATE_ARTIFACT`).
- Readable Vertical Slice Plan present on disk (`docs/vertical-slice-plan.md`).
- Approved `UI_LAYOUT` milestone per UI slice before entering Stage 4.

