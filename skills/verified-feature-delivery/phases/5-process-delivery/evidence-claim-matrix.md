# The Two Evidence Axes

**Owner:** `stage-5`
**Consumer:** `bin/verify-evidence verify-medium`, `bin/verify-evidence verify-claim`

The two axes are independent, and that is the point. Matching the artifact kind to the declared
verification medium does not establish that the artifact can address the criterion. A
`rendered_ui` snapshot serialises structure and text, so it satisfies the **medium** axis for a
layout or viewport-floor claim and cannot possibly evidence it — a harness with no layout engine
produces the same snapshot at every viewport. A checker that runs only the medium axis passes
that case, which is why the claim table exists.

---

## The capture envelope

One JSON file per milestone, at `artifact_path` relative to the delivery report. Its key set is
closed.

```json
{
  "captured_artifact":       "…exactly what Stage 4 captured for the medium…",
  "recorded_fixture_values": ["…values the run supplied, masked out of the projection…"],

  "layout_capture":  { "viewport": {"width": 360, "height": 640},
                       "boxes": [{"selector": "ul.result-list", "width": 328, "height": 96, "overflow_x": 0}] },
  "focus_sequence":  [{"step": 1, "target": "input#search-query-field"},
                      {"step": 2, "target": "ul.result-list"}],
  "time_travel":     { "advanced_by_ms": 3000,
                       "terminal_transition": {"from_state": "LOADING", "to_state": "TIMED_OUT"},
                       "external_events": [] },
  "value_bindings":  [{"domain_term": "total_results",
                       "projection_binding": "SearchResponse.total_results to the operator result count",
                       "asserted_value": 4210}],

  "observation_target": { "entry_point": "dist/index.html",
                          "wait_for_selector": "ul.result-list",
                          "settle_ms": 300 }
}
```

`captured_artifact` is the medium axis. The four records below it are the claim axis: a harness
produces each one only when it is actually capable of that measurement, so their presence and
their internal consistency is what the second axis reads.

`observation_target` is neither axis. It is what the gate needs in order to stop taking
`captured_artifact` on trust, and it is **required of every `rendered_ui` milestone** —
`OBSERVATION_TARGET_UNDECLARED` otherwise. Declare where the thing under verification can be
reached and nothing about what it renders: `entry_point` (a file relative to the artifact
directory), or `url`, or `server_command` with `port` for something that must be started first,
plus optional `wait_for_selector`, `settle_ms` and `timeout_ms`. Its key set is closed too.

Stage 5 then drives that target in headless Chromium itself, normalises what the browser
actually rendered through the same table, and compares it against `captured_artifact`. A
disagreement is `OBSERVATION_DISAGREEMENT`; a target that renders nothing is
`OBSERVATION_RENDER_EMPTY`, which is what an unexecutable client looks like from outside; a
declared selector that never appears is `OBSERVATION_SELECTOR_TIMEOUT`; an unreachable target is
`OBSERVATION_TARGET_UNREACHABLE`; and no browser at all is `OBSERVATION_BROWSER_UNAVAILABLE`,
refused rather than skipped, because a delivery nobody could observe is not verified. Note what
this proves and what it does not: that a real browser executed the declared target and rendered
what was claimed — not that the target is the product. An `entry_point` pointing at a static
file someone wrote by hand still passes.

---

## Axis 1 — medium

The milestone's declared medium comes from the **execution DAG**, not from the delivery report.
The capture is run through the same normalisation table Stage 4 captured it with, and an artifact
that cannot be normalised as that medium at all is `EVIDENCE_MEDIUM_MISMATCH`.

| Verification medium | Admissible milestone evidence | What the check reads |
| :--- | :--- | :--- |
| `process_exit` | Recorded command, exit code, captured streams | an exit code and its streams |
| `api_response` | Recorded request/response pair against the probed fixture | a status code, contracted headers, the body's key set and types |
| `datastore_state` | Before/after state assertion or query output | changed columns per table and constraint outcomes |
| `rendered_ui` | Snapshot of the asserted projection | the markup an observer was shown |
| `device_actuation` | Captured actuator command trace or telemetry readback | an ordered opcode sequence |
| `agent_transcript` | Recorded transcript including tool calls and final output | tool-call order and the final message template |

A log line stating that a test passed is not evidence that a state was observable. A test
runner's exit code offered for a `rendered_ui` milestone fails here.

---

## Axis 2 — claim

Per claim kind, what the envelope must carry. Each row is a positive requirement — what has to be
present — not a list of harnesses to reject.

| Claim kind | Admissible evidence | The envelope must carry |
| :--- | :--- | :--- |
| `state_observable_and_distinct` | Normalised projection fingerprint per `evidence_key` | the claim names an `evidence_key`, and the capture normalises to something an observer could distinguish |
| `layout_viewport_floor_overflow` | Capture from a runtime with a layout engine at the declared dimension | `layout_capture` with a positive viewport width and at least one box measuring non-zero, every box naming an element the capture actually rendered |
| `traversal_and_focus_order` | Recorded focus sequence across the trigger's completion | `focus_sequence` of two or more ordered, distinct targets, each present in the capture |
| `timing_deadline_temporal_egress` | Time-travelled run showing the terminal transition with no external event | `time_travel` with a clock advanced, a terminal transition between two distinct states, and `external_events` carrying nothing |
| `value_correctness` | Asserted value traced to its Stage 0 projection binding in the recorded response | `value_bindings` naming a domain term, its projection binding, and an asserted value that appears in the capture |

Why the geometry rules are shaped the way they are: a runtime without a layout engine reports
zero for every element, so a box measuring zero is exactly what a harness that cannot do this
produces. And geometry for an element the snapshot does not contain is a measurement of something
else — tying the box selector back to the captured markup is what stops a hand-written geometry
block standing in for a real render.

---

## When a claim has no admissible artifact

Emit it as an `[Accepted Constraint]` naming what was not verified and how it was checked
instead. It may not pass as verified, and it may not be reported as a broken gate either. Both
errors are wrong, and the second is what makes a team switch the checker off. See
`intent-templates.md`.

A claim the plan committed to that the delivery neither evidences nor discharges is
`CLAIM_WITHOUT_ACCEPTED_CONSTRAINT`: it is silently absent, which reads exactly like delivered.
