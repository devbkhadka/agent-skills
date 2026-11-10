# Phase Guide: Stage 4 — Milestone Execution & Verified Flow Loop

**Stage:** `STAGE_4_MILESTONE_EXECUTION`  
**Owner:** `stage-4`  
**Required Exit Artifacts:**
- `milestones/executions.json` (under `.verified-feature-delivery/<feature-slug>/`)
- `milestones/evidence-summary.json`
- `evidence/<capture-file>` (captured medium artifacts per milestone)
**Gate Validators:** `bin/milestone-harness`, `bin/verify-projections`

---

## 1. Purpose & Process

1. Implement `pure_modules` first, assertable in isolation.
2. Mount external adapters verified against full failure envelope and variance censuses.
3. Execute slice verification scripts tailored by 3-tier verification plan ([`verification-policy.md`](verification-policy.md)) and capture medium-appropriate evidence envelopes.
4. Compute projection fingerprints per evidence key; refute or confirm Stage 2 claims.
5. Discharge all `PROVISIONAL_FROM_SCHEMA` fixtures through live re-probes.
6. Evaluate milestone claims by verification kind:
   - `deterministic`: settled by validator or test script exit code with no agent in the loop.
   - `judged`: evaluated by an independent subagent spawned with a scoped judge packet as its entire context.
7. Trigger automated regression rollback routing upon contract drift, invariant breach, or unclassified boundary.

---

## 2. Capture Envelopes vs Test Assertions

A common failure mode is slice test suites asserting purely on internal variables without emitting capture envelopes. The harness evaluates projection coverage from captured envelopes rather than assertion pass counts.

### Capture Envelope Requirement
For each projection target/milestone claim, the slice suite must write a capture envelope (`.verified-feature-delivery/<slug>/evidence/<milestone_id>_capture.json` or `.html`) and record the run in `milestones/executions.json`.

```json
{
  "captured_artifact": "<rendered DOM, response JSON, or process exit output>",
  "recorded_fixture_values": ["search query", "sample title"],
  "layout_capture": null,
  "focus_sequence": null,
  "time_travel": null,
  "value_bindings": null,
  "observation_target": "http://localhost:3000"
}
```

### Executions Document (`milestones/executions.json`)
```json
{
  "schema_version": "1.0.0",
  "feature_slug": "<feature-slug>",
  "generated_at": "2026-08-25T12:00:00Z",
  "milestones": [
    {
      "milestone_id": "M1_QUERY_RENDER",
      "slice_id": "slice_query_to_render",
      "verification_medium": "rendered_ui",
      "executed_command": "npm run test:slice:1",
      "captured_evidence": "evidence/m1_query_render.html",
      "evidence_key": "state:RESULTS_POPULATED",
      "observer_id": "operator",
      "boundary_identifier": "ui_runtime",
      "asserted_payloads": ["results_list"],
      "recorded_fixture_values": ["database"],
      "failure_trace": null
    }
  ]
}
```

---

## 3. Judged vs Deterministic Claims & Evaluator Subagent Separation

Claims evaluated across milestone execution carry a declared `verification_kind` (`deterministic | judged`):

* **Deterministic Claims**: Purely objective assertions (e.g. exit codes, schema validation, type parity, regex invariants) that a validator or script settles. Exit code is the verdict; no evaluating agent is invoked.
* **Judged Claims**: Qualitative or semantic criteria (e.g. abstraction appropriateness, UI layout readability, unambiguous outcome fulfillment) requiring evaluation against captured evidence and an explicit `acceptance_rule`.

### Evaluator Separation & Judge Packets

To prevent the authoring agent from inheriting its own blind spots:
1. The judging agent is an independent subagent spawned with the judge packet (`.verified-feature-delivery/<slug>/judge/<claim-id>.packet.json`) as its entire context.
2. `bin/build-judge-packet` builds the packet strictly from stage artifacts and captured evidence (`claim_id`, `claim_text`, `acceptance_rule`, `verification_medium`, `evidence_refs`, `artifact_excerpts`).
3. Any worker rationale, author transcript, change diff, prior verdicts, or self-assessment is strictly refused with `JUDGE_PACKET_CONTAMINATED`.
4. The judge emits a structured verdict (`CONFIRMED | REFUTED | COLLABORATE`). The anti-nitpick rule requires `blocking: true` to cite examined evidence; `COLLABORATE` (non-blocking with a minimal fix) is the collaborative default. Verdicts are verified by `bin/verify-evidence verify-verdict`.

---

## 4. Tooling & Exit Gate Verification

### Driver Role & Context Hygiene
* **The driving agent must never run tests, compile slices, or capture evidence directly.**
* Slices are implemented by worker agents; evidence is captured by workers; judged claims are evaluated by isolated judge subagents.
* The driver inspects only structured milestone summaries (`milestones/evidence-summary.json`) and handles regression rollbacks via `bin/planner-state rollback`.

### Validate Gate
```bash
python3 bin/milestone-harness .verified-feature-delivery/<feature-slug>
python3 bin/verify-projections .verified-feature-delivery/<feature-slug>/projections.json \
                               .verified-feature-delivery/<feature-slug>/flow-simulation-matrix.json
```

