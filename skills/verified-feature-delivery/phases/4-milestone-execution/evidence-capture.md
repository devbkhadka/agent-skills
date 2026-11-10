# Evidence Capture & Projection Fingerprinting

**Stage:** `STAGE_4_MILESTONE_EXECUTION`
**Owner:** `stage-4`
**Artifacts:** `milestones/executions.json` in, `projections.json` and `milestones/evidence-summary.json` out (under `.verified-feature-delivery/<feature-slug>/`)
**Validators:** `bin/milestone-harness`, `bin/verify-projections`
**Implementation:** `lib/normalisation.py`

---

## 1. Why the fingerprint is computed and never declared

Stage 2 attached a structured `observable_projection` claim to every state, per observer. That
claim is a **prediction**. Stage 4 holds the **binding** check, because Stage 4 compares the
captured artifact rather than the planner description.

Two failure modes bracket the whole design:

* **Hashing the raw artifact is worthless.** No two captures are byte-identical — timestamps,
  process ids, request ids, style hashes and result counts all move — so a raw hash makes every
  state distinct from every other and passes every collision test while catching nothing.
* **Masking everything is worse than useless.** Strip the visible text and `Found 10 matches`
  and `No results found` both reduce to `[heading][button]`, so two genuinely distinct states
  collide and the gate reports a defect that is not there.

Masking is what makes the check **correct** rather than merely strict. Both directions are gate
lines, and both are required:

| Direction | Case | Requirement |
| :--- | :--- | :--- |
| Data invariance | `s4-fingerprint-data-invariant` | two captures of one state differing only in **data** fingerprint **alike** |
| Wording distinctness | `s4-fingerprint-wording-distinct` | two captures differing only in **wording** fingerprint **differently** |

There is therefore no `fingerprint_sha256` field and no `normalised_projection` field on a
capture record. Both are computed here from the captured artifact; a record that declares either
is **refused**, not believed, because a declared fingerprint asserts exactly the property the
gate exists to establish.

---

## 2. The six media, and what each keeps, drops and masks

The table is a **closed set**. A medium outside it is refused with
`UNKNOWN_VERIFICATION_MEDIUM` rather than skipped, because a milestone whose medium is misspelt
would otherwise skip capture entirely and take its state's evidence with it.

| Medium | Kept | Dropped | Masked |
| :--- | :--- | :--- | :--- |
| `rendered_ui` | element role, accessible name, visible text, aria states (`busy`, `disabled`, `expanded`, `checked`, `selected`, …), interactive affordances, document order | class names, inline style, test attributes, framework keys — everything not on the keep-list | numerals → `#`, values the run's fixture recorded → `<value>` |
| `api_response` | status code, envelope key set, recursive key types, headers the contract names | every header the contract does not name | scalar payload values → their type tag |
| `process_exit` | exit code, stream line templates in order, census warning classes | timestamps, process ids, absolute paths, addresses | interpolated numbers, counters and byte metrics → `<val>`, paths → `<path>` |
| `datastore_state` | changed column set per table with each column's type, constraint outcomes | physical row identity | cell values → their type tag |
| `device_actuation` | command opcodes and their order, operand arity | timing and bus jitter | operands → `<operand>` |
| `agent_transcript` | tool-call names and order, argument names, final message template | token counts, latencies, step timestamps | argument values and interpolated values → `<val>` |

Two rules run through every row, and they are what stop this table from being defeated by a
rename:

* **Keeping is a keep-list.** An attribute, header or field that is not named as kept is dropped
  by never being read. Nothing is recognised by matching a project's own vocabulary — no
  `data-testid`, no log phrase, no column name — so renaming the thing does not defeat the drop.
* **Dropping noise is done by format.** A timestamp is recognised as an ISO-8601 shape, a path as
  a path shape, a process id or a byte count as a number. A stream that calls its process id
  something else is masked all the same.

---

## 3. Worked keep / drop / mask, per medium

Each normalised form below is pinned by a test in `tests/test_stage_4.py`
(`TestNormalisedFormPinned`), so the documentation cannot drift from the implementation.

### `rendered_ui`

*Capture A — `RESULTS_POPULATED`, ten matches, recorded fixture values `database`, `SQL indexing guide`:*

```html
<div class="MuiBox-root css-1a2b" data-testid="results-pane"><h2 class="title">Found 10 matches for "database"</h2><button class="btn btn-primary" aria-label="Clear filters">Clear</button><ul role="list"><li role="listitem" key="k-1"><span>Result 1: SQL indexing guide</span></li></ul></div>
```

*Capture B — the same state, 4210 matches, a different build's styling, recorded values `caching`, `Distributed caching patterns`:*

```html
<section class="theme-dark results-container" data-testid="search-results-wrapper"><h2 class="header-bold">Found 4210 matches for "caching"</h2><button class="custom-button" aria-label="Clear filters">Clear</button><ol role="list"><li role="listitem" key="node-99"><span>Result 4210: Distributed caching patterns</span></li></ol></section>
```

*Both normalise to:*

```
[heading][text|Found # matches for "<value>"][button|name=Clear filters][text|Clear][list][listitem][text|Result #: <value>]
```

→ **identical fingerprints.** The count, the query, the result title, the classes, the test ids,
the framework keys and even `div` against `section` and `ul` against `ol` are all gone; the
template wording and the affordance are not.

*Capture C — `RESULTS_EMPTY`, same build:*

```html
<div class="MuiBox-root" data-testid="results-pane"><h2 class="title">No results found for "database"</h2><button class="btn" aria-label="Clear filters">Clear</button></div>
```

```
[heading][text|No results found for "<value>"][button|name=Clear filters][text|Clear]
```

→ **distinct from A.** The wording the operator reads survived masking.

### `api_response`

*Capture — 200 with a summary payload, contract headers `content-type`:*

```json
{"status_code": 200,
 "headers": {"content-type": "application/json", "x-request-id": "req_88ab91c0"},
 "body": {"status": "ok", "total_count": 10, "cursor": "cur_xyz",
          "items": [{"id": "item_1", "score": 0.98, "active": true}]}}
```

```
status:200|header:content-type=application/json|keys:cursor(string),items(array[object{active:boolean,id:string,score:number}]),status(string),total_count(number)
```

A second capture with `total_count: 8500`, different ids, scores and request id normalises to the
same string. A capture whose envelope carries `message` instead of `cursor` does not — the key
set is what an API observer distinguishes.

### `process_exit`

*Capture — a transcode that exited zero:*

```
exit 0
stdout: [2026-08-22T11:00:01Z] PID 14202: Processing /var/app/scratch/job_1/chunk_001.mp4
        frame=120 fps=60.2 q=28.0 size=1024kB time=00:00:02.00 bitrate=4194.3kbits/s speed=1.0x
        [info] Transcode completed successfully in 2.14s
```

```
exit:0|stdout:PID <val>: Processing <path>\nframe=<val> fps=<val> q=<val> size=<val>kB time=<val> bitrate=<val>kbits/s speed=<val>x\n[info] Transcode completed successfully in <val>s|stderr:<empty>|warnings:<none>
```

Another job with a different timestamp, pid, scratch path, frame count and duration normalises
identically. A run whose stderr carries `decode error: frame dropped` does not — and the same
capture also fails the milestone under `SILENT_BOUNDARY_DEGRADATION`, because that warning class
is in the boundary's failure envelope census.

The exit code stays in the projection, so states exiting 1, 2, 3 and 4 fingerprint apart. It also
decides success: a state whose matrix projection declares `"exit_code": "1"` (and that the
milestone's DAG claims name) succeeds only on an observed `exit 1`; any other code, `0`
included, is a failed milestone and routes like one. The same holds for an `api_response`
projection declaring `status_code`. The record has no key that can state or override the code.

### `datastore_state`

*Capture — a downgrade applied to one tenant:*

```json
{"mutations": [
   {"table": "subscriptions", "row_identity": "sub_uuid_111",
    "changed_columns": [{"name": "tier", "value": "FREE"}, {"name": "status", "value": "ACTIVE"},
                        {"name": "seat_count", "value": 3}, {"name": "updated_at", "value": 1724328000}]},
   {"table": "organizations", "row_identity": "org_uuid_aaa",
    "changed_columns": [{"name": "quota_status", "value": "QUOTA_EXCEEDED"},
                        {"name": "updated_at", "value": 1724328000}]}],
 "constraints": [{"name": "fk_org_id", "outcome": "SATISFIED"},
                 {"name": "chk_seat_limit", "outcome": "SATISFIED"}]}
```

```
tables:organizations(quota_status:string,updated_at:number),subscriptions(seat_count:number,status:string,tier:string,updated_at:number)|constraints:chk_seat_limit(SATISFIED),fk_org_id(SATISFIED)
```

The same downgrade on another tenant, with other row ids, seat counts and write stamps,
normalises identically. A cancellation, which changes `canceled_at` and `status` instead, does
not. Note that each column's type is **computed from the captured value**; `row_identity` is
dropped by never being read, which is why no name list of identity columns appears anywhere.

### `device_actuation`

*Capture — a pan and capture at 45 degrees:*

```json
{"commands": [{"opcode": "OP_MOTOR_SET_ANGLE", "operands": [0, 45.0], "elapsed_us": 1002},
              {"opcode": "OP_SENSOR_EXPOSE", "operands": [100], "elapsed_us": 124310},
              {"opcode": "OP_FRAME_TRANSFER", "operands": ["0x20001000"], "elapsed_us": 252004}]}
```

```
opcodes:[OP_MOTOR_SET_ANGLE(<operand>,<operand>),OP_SENSOR_EXPOSE(<operand>),OP_FRAME_TRANSFER(<operand>)]
```

The same sequence at 90 degrees, with a longer exposure, a different destination register and
microseconds of bus jitter, normalises identically. A calibration sequence
(`OP_MOTOR_HOME`, `OP_SENSOR_SELFTEST`) does not. A text trace of the form
`[T+0.001s] OP_MOTOR_SET_ANGLE(axis=0, deg=45.00)` normalises to the same string as the
structured form.

### `agent_transcript`

*Capture — a search and summarise turn, recorded fixture value `authentication`:*

```json
{"tool_calls": [{"name": "query_graph", "arguments": {"topic": "auth"}, "prompt_tokens": 45, "latency_ms": 120},
                {"name": "view_file", "arguments": {"path": "/src/auth.ts"}, "prompt_tokens": 120, "latency_ms": 80}],
 "final_message": "Found 2 authentication modules in the repository."}
```

```
tool_calls:[query_graph(topic=<val>),view_file(path=<val>)]|final_template:Found <val> <val> modules in the repository.
```

The same turn over billing, with different token counts and latencies, normalises identically. A
turn that calls one tool and answers `No modules found matching the query.` does not.

---

## 4. `captureProjection`

```python
from lib.normalisation import capture_projection

normalised, fingerprint = capture_projection(
    medium,                  # one of the six; anything else raises UNKNOWN_VERIFICATION_MEDIUM
    captured_artifact,       # the raw capture; empty or vacuous raises EMPTY_CAPTURED_ARTIFACT
    recorded_values=(),      # values the run's fixture supplied, masked to <value> / <val>
    contract_headers=(),     # header names the contract fixtures recorded; the rest are dropped
    warning_classes=(),      # classes matched against the failure envelope census, kept for process_exit
)
```

`recorded_values`, `contract_headers` and `warning_classes` are all sourced from artifacts the
pipeline already produced — the run's fixture data, the Stage 1 contract fixtures' recorded
headers, and the Stage 1 failure envelope census — not from anything a planner writes beside the
capture. Keeping more headers than the contract names would let a volatile header make two
identical states look distinct, which is why the header keep-list comes from the fixtures.

### `projections.json` — the capture record

```json
{
  "schema_version": "1.0.0",
  "feature_slug": "search-console-web-ui",
  "generated_at": "2026-08-22T12:10:00Z",
  "captured_projections": [
    {
      "evidence_key": "state:RESULTS_POPULATED",
      "observer_id": "operator",
      "milestone_id": "M2_RESULTS_RENDER",
      "captured_artifact": "<div class=\"results-pane\">…</div>",
      "recorded_fixture_values": ["database", "SQL indexing guide"]
    }
  ]
}
```

The full key set of a capture record is closed: `evidence_key`, `observer_id`,
`captured_artifact`, `captured_artifact_path`, `recorded_fixture_values`, `slice_id`,
`milestone_id`. Any other key is refused with `UNKNOWN_PROJECTION_ATTRIBUTE`.

Note what is **not** in it: no `medium`. The medium comes from the observer register in the
scenario matrix, so the normaliser a capture is run through is chosen by the pipeline's own
artifact rather than by a field beside the capture. A capture attributed to an observer the
matrix does not register is refused with `UNREGISTERED_OBSERVER`.

Large artifacts may be referenced instead of inlined, with `captured_artifact_path` relative to
the artifact directory; a path that is not there is a `PROJECTION_COVERAGE_GAP`, because a
capture whose artifact is missing is not a capture.
