# Verification Patterns & Anti-Degradation Rules

**Stage:** `STAGE_4_MILESTONE_EXECUTION`
**Owner:** `stage-4`
**Artifacts:** `milestones/executions.json` in, `milestones/evidence-summary.json` and `projections.json` out
**Validators:** `bin/milestone-harness`, `bin/verify-projections`
**Implementation:** `lib/normalisation.py`, `lib/rollback.py`

---

## 1. The process, and the order it is graded in

Stage 4 executes vertical slices sequentially against real boundaries and contract-backed
fixtures. Progress increments **only** on a verified vertical flow, which is what stops a
progress metric from being a claim about the plan.

1. Implement the declared `pure_modules` first, isolated from I/O, framework, device and model calls.
2. Mount external adapters against the Stage 1 contract fixtures — every entry in the failure
   envelope census, not only the success sample.
3. Exercise consumers of `UNBOUNDED` regions against the **full** variance census.
4. Run the slice suite, capture medium-appropriate evidence, and compute the projection
   fingerprint for every `evidence_key` the slice reached.
5. Assert no silent boundary degradation.
6. Discharge every `PROVISIONAL_FROM_SCHEMA` fixture through its pinned live re-probe.
7. Increment progress only on success.
8. Route any regression to the stage it came from.

`bin/milestone-harness <feature-dir>` grades one run in that order, with one exception: a
milestone that **failed** is routed (step 8) before the remaining obligations are graded, because
a milestone that failed has not verified its vertical flow and the regression engine owns the run.

---

## 2. What the harness reads, and what it refuses

The harness resolves every artifact path from the **state file's own artifact map** when it has
one. That map is authoritative: a harness that hunted for a filename of its own choosing would
grade a different file from the one the state machine gates on.

### `milestones/executions.json` — what the suites recorded

```json
{
  "schema_version": "1.0.0",
  "feature_slug": "search-console-web-ui",
  "generated_at": "2026-08-22T12:05:00Z",
  "milestones": [
    {
      "milestone_id": "M2_DEFENSIVE_METADATA_CONSUMPTION",
      "slice_id": "slice_1_query_entry",
      "verification_medium": "api_response",
      "executed_command": "vitest run tests/searchApi.metadata.test.ts",
      "boundary_identifier": "POST /v1/search",
      "evidence_key": "state:RESULTS_POPULATED",
      "observer_id": "operator",
      "captured_evidence": { "status_code": 200, "headers": {}, "payload": {} },
      "asserted_payloads": [],
      "recorded_fixture_values": [],
      "failure_trace": null
    }
  ]
}
```

That key set is **closed**. Any other key is refused with `UNKNOWN_EXECUTION_ATTRIBUTE`, and the
reason is the point of the whole design: the keys that are missing are the ones a planner would
most want. There is no `status`, no `passed`, no `exit_verdict`, no `fingerprint`, no
`coverage_complete`, no `degradation_detected` and no `provisional_fixtures_outstanding`. Each of
those is **computed**:

| Property | Computed from |
| :--- | :--- |
| whether the milestone succeeded | the captured exit code or status, held against the approved plan: when the matrix projection for the record's `(evidence_key, observer_id)` declares an `exit_code` (`process_exit`) or `status_code` (`api_response`) **and** the DAG milestone claims `evidence_key@observer_id`, success is the observed code equalling the declared one (an approved `exit 1` or `409` passes; `0` or `2` fails); only the declared medium's field is graded (a malformed one fails; an absent one is refused by the normaliser as `UNNORMALISABLE_ARTIFACT`); a target whose projections lack a parseable code (`pending`, `4xx`, `1-4`) or disagree declares nothing; otherwise a zero exit, or a 2xx. A capture with neither field (`rendered_ui`, `datastore_state`, `device_actuation`, `agent_transcript`) uses the per-medium rule below, for fixture discharge only |
| the projection fingerprint | the captured artifact, normalised for its observer's medium |
| projection coverage | the matrix's `(evidence_key, observer)` targets differenced against the captured ones |
| silent degradation | a success signal held against the warning classes the census recorded |
| variance shapes recorded | the type signatures of the fixture's own success samples at each `UNBOUNDED` region |
| variance shapes exercised | the same signatures computed from what the suites asserted against |
| provisional fixtures outstanding | each provisional fixture's pinned re-probe milestone, differenced against the milestones that ran and succeeded (code, or per-medium success) |
| retired records | each record's `(evidence_key, observer_id)` differenced against the current matrix's projection targets |
| the rollback cause | the recorded failure differenced against the artifact that would have had to name it |

**Per-medium success (codeless media).** A capture carrying no `exit_code`, `status_code` or
`status` is graded by its observer's medium (from the matrix register; the record's own
`verification_medium` only when it names no registered observer). The capture must normalise under
that medium, and then:

| Medium | Success when |
| :--- | :--- |
| `datastore_state` | at least one changed table or constraint outcome is recorded, and every constraint outcome is `SATISFIED` |
| `rendered_ui` | the rendering holds at least one observable element or text run |
| `device_actuation` | at least one command opcode was issued |
| `agent_transcript` | the transcript ends in a non-empty `final_message` |

The rule yields success or nothing, never failure: a `VIOLATED` constraint or an empty page withholds
success (the pinned fixture stays `PROVISIONAL_FIXTURE_OUTSTANDING`) but is not routed as a regression,
because an approved error state can look exactly like that. It feeds only the provisional-fixture
discharge; the degradation check and regression routing stay on exit code and status, since an
approved error banner or failure transcript would otherwise read as success that hid its own loss.
No `exit_code` needs to be added to such a capture.

**Retired records.** A record whose `(evidence_key, observer_id)` the current matrix no longer
declares — an approved Stage 2 amendment removed that projection — is set aside before grading and
reported as `RETIRED: <n> execution record(s) across <m> projection(s) …` with each `key@observer` and
its record count. It is not deleted from `milestones/executions.json`, and it cannot verify a
milestone, discharge a fixture, collide or raise a regression. The evidence summary lists it under
the optional `retired_records` key. A record naming no projection (a boundary re-probe) is never
retired.

**Limit:** claims bind a declared code to a *milestone*, not to the command a record ran. In a milestone that claims several states, a failing run could be relabelled with a sibling state's `evidence_key` and pass. Coverage still needs one distinct record per claimed `(evidence_key, observer)` target, so the relabel shows up as a gap unless a record is also fabricated for the state it vacated.

A record's `verification_medium` is checked against the closed set of six media, so a misspelt
medium is refused with `UNKNOWN_VERIFICATION_MEDIUM` rather than silently skipping capture. A
record whose `captured_evidence` is empty, or vacuous — `{}`, `""`, `[{}]` — is refused too: a
milestone that captured nothing cannot be verified, however many milestones the list contains.

---

## 3. The five obligations

### `VARIANCE_SHAPE_UNEXERCISED` — the full variance census, exercised

Every distinct **recorded** shape of an `UNBOUNDED` region must be exercised by at least one
assertion. Both sides are computed as structural type signatures:

* recorded: for each region path in `variance_census.unbounded_regions`, the signature of the
  value at that path in each of the fixture's `success_samples`;
* exercised: the same signature computed at the same path over each milestone's
  `asserted_payloads`.

The difference is what fails. Nothing reads a list of shape *names*, so a census cannot claim a
shape it never recorded, nor hide one it did.

*Caught:* a UI that renders summary metadata of four keys in every test and crashes on the
promoted-chunk metadata of twelve that the census recorded but no assertion ever touched.

### `PROJECTION_COVERAGE_GAP` — every declared projection captured

Coverage is the difference between the `(evidence_key, observer_id)` pairs the matrix declares
observable and the pairs a capture was attributed to. It is keyed **per observer** because the
collision check is per observer: an observer whose projections were never captured has had
nothing refuted, however many other captures the run produced.

*Caught:* a slice reporting 100% state coverage whose suite never drives the error state, so
"100% coverage" is a claim about the graph rather than about the evidence.

### `FINGERPRINT_COLLISION` — the binding refutation

Two distinct `evidence_key`s that fingerprint alike for one observer fail Gate 4. The Stage 2
discriminator check is advisory and exits zero; this is the check that binds, because it compares
the captured artifact rather than the planner's description of it. See `evidence-capture.md` for
the normalisation table this rests on.

*Caught:* an un-run search console and a zero-result search console that the plan models as two
states and the DOM renders identically.

### `SILENT_BOUNDARY_DEGRADATION` — success that reports its own loss

A milestone whose capture reports success — a zero exit, or a 2xx — while carrying a warning
class the Stage 1 failure envelope census recorded for that boundary fails the milestone. A
boundary that succeeds while reporting that it dropped part of its work has not succeeded.

Both halves of the detection come out of the census: the class it recorded, and the text it
recorded for that envelope. No warning phrase is written into the validator, so a boundary that
words its warning differently is still caught as long as its own census recorded that wording —
and a boundary whose census is empty was already refused at Gate 1. A milestone that names the
boundary it exercised is checked against that boundary's census; one that does not is checked
against every census, because an unattributed capture is not a reason to check nothing.

*Caught:* a transcode that drops a corrupt audio channel, logs `decode error: frame dropped`, and
exits 0, so the runner declares success and ships silent media.

### `PROVISIONAL_FIXTURE_OUTSTANDING` — the promise Gate 1 accepted

`PROVISIONAL_FROM_SCHEMA` passed Gate 1 **only** on the promise of a pinned Stage 4 re-probe. A
fixture is discharged when the milestone it pinned ran and reported success against the live
boundary — a computed exit code or status, or for a codeless medium the per-medium rule in §2. A fixture whose pinned milestone did not run, or that pins no milestone at all, is
outstanding, and Gate 4 refuses to close the feature.

*Caught:* an adapter verified only against an OpenAPI document because the container network was
down at planning time, shipped without ever meeting the real endpoint.

---

## 4. The four regression edges

The causes are **four**, not three. The routing table is not restated here or in the validator:
`bin/planner-state` holds it, `lib/rollback.py` reads it, and the state transition is that tool's
own rollback command, so the pipeline has one state machine rather than two.

| Recorded failure | Computed cause | Routes to |
| :--- | :--- | :--- |
| two evidence keys fingerprint alike for one observer | `FINGERPRINT_COLLISION` | `STAGE_2_SCENARIO_SIMULATION` |
| a named slice invariant broke | `BROKEN_INVARIANT` | `STAGE_2_SCENARIO_SIMULATION` |
| the observed envelope matches none the contract fixture recorded | `CONTRACT_DRIFT` | `STAGE_1_CONTRACT_PROBING` |
| the failure traces to a boundary property **no register row names** | `UNCLASSIFIED_BOUNDARY` | `STAGE_1_CONTRACT_PROBING` |
| the observed quantity contradicts the lexicon's declared unit precision | `DOMAIN_TYPE_PRECISION_MISMATCH` | `STAGE_0_DOMAIN_INGESTION` |

A failed milestone records what it **touched**, never what it means:

```json
"failure_trace": {
  "boundary_identifier": "react_client_runtime",
  "boundary_property": "development_mode_effect_double_invocation",
  "observed_response": null,
  "domain_term": null,
  "observed_unit": null,
  "observed_value": null,
  "broken_invariant": null,
  "description": "The runtime invoked the query effect twice on mount, so one submit dispatched two searches and the second settled over the first."
}
```

That key set is closed as well, and there is no `cause` key in it. Classification runs in this
precedence, and the order is load-bearing:

1. **the register first** — a property that no row names cannot have drifted from anything, so it
   is `UNCLASSIFIED_BOUNDARY` rather than drift;
2. **the lexicon next** — a precision mismatch also shows up as a shape difference, so checking
   the lexicon before the fixture keeps it from being misfiled as drift;
3. **the contract fixture** — the observed envelope against the ones the fixture recorded,
   compared at the bounded top level so that variance inside an `UNBOUNDED` region, which the
   contract permits and the census records, does not read as drift;
4. **the invariant** — a named broken slice invariant.

`description` must be meaningful prose; a failure recorded as `-` is not a traced failure.

### Rollback is scoped to what the failure touched

When the rollback carries the classified failure (the harness passes it; by hand,
`planner-state rollback … --trace failure.json` with `{"milestone_id", "trace"}`), the affected
elements come from the trace's own keys and the affected slices are those that own the failed
milestone or reference a named element, plus every slice depending on them. Upstream artifacts stay
registered, the scope is written to `state.json` as `rollback_scope`, and only the affected
milestones' records leave `milestones/executions.json` (moved to `invalidated-executions.json`, with
the evidence summary renamed, never deleted). The harness re-grades the preserved records unchanged,
and every gate still re-checks on re-advance. When nothing in the trace maps to a slice — no trace, no
DAG, or a trace naming nothing any slice references — the rollback falls back to full invalidation
and prints `ROLLBACK_SCOPE_UNDERIVABLE`.

### The fourth edge is the expensive one

The first three causes all assume the boundary was known. A boundary that is present and
**unnamed** is not drift, not a type mismatch and not an unmodelled branch, so without its own
cause code it lands as an ordinary implementation bug, someone fixes it locally, and the register
never learns. `UNCLASSIFIED_BOUNDARY` therefore does three things rather than one:

1. rolls back to `STAGE_1_CONTRACT_PROBING`;
2. records the boundary under the register's `OTHER` class, flipping that row to `PRESENT` and
   adding the property with verdict `NOT_PROBED`;
3. raises a **register amendment** naming the property, the boundary and the proposed register
   change, so the next feature probes it instead of meeting it at run time.

*Caught:* a UI runtime probed for portal mounting but never for effect invocation semantics, which
then double-invoked the query effect on mount and dispatched two searches per submit.

---

## 5. Claim Verification Kinds & Subagent Judgement Separation

Every claim evaluated across milestone execution declares a `verification_kind`:

* **`deterministic`**: Evaluated directly via test script exit code or gate validator. No subagent or qualitative judgement is involved.
* **`judged`**: Qualitative criteria (e.g. semantic outcome clarity, layout integrity, abstraction quality) evaluated by an independent subagent against a declared `acceptance_rule`.

### Evaluator Subagent Isolation & Judge Packets

1. **Context Isolation**: The judging agent must not be the authoring agent and receives only the judge packet built by `bin/build-judge-packet`.
2. **Contamination Refusal**: Any worker rationale, authoring transcripts, diffs, prior verdicts, or self-assessments are strictly rejected with `JUDGE_PACKET_CONTAMINATED`.
3. **Structured Verdicts**: Verdicts are verified via `bin/verify-evidence verify-verdict --verdict <path>` adhering to the closed vocabulary `CONFIRMED | REFUTED | COLLABORATE`.
4. **Anti-Nitpick Invariant**: A `blocking: true` verdict must cite examined evidence (`evidence_examined`); empty citations fail with `BLOCKING_VERDICT_WITHOUT_EVIDENCE`. `COLLABORATE` defaults to `blocking: false` with a `minimal_fix`.
