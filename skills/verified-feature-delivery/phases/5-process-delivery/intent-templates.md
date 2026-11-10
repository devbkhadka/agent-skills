# Intent Header Templates & Communication Protocols

**Owner:** `stage-5`
**Consumer:** `bin/verify-evidence lint-intent`, `bin/verify-evidence check-gates`,
`bin/verify-evidence lint-abstraction`, `bin/generate-changelog`

Every field named below is read by one of those tools. A field no tool reads passes the gate
identically whether or not it is true, so this file carries none.

---

## The five intent headers are a closed set

`[Decision Needed]`, `[Risk]`, `[Accepted Constraint]`, `[Milestone]`, `[Blocker]`.

Anything else — `[Update]`, `[Status]`, `[Note]`, `[Summary]` — is refused with
`INVALID_INTENT_HEADER`. This is not style enforcement. An invented heading is how an unmade
product choice gets filed as a `[Note]` and walks past the gate that refuses to complete a
feature while a `[Decision Needed]` is open, and how a live impediment gets filed as an
`[Update]` and never reaches the person who could clear it.

Each header also carries its own closed key set, so a `[Risk]` cannot borrow
`discharged_claim_ids` to wave a claim through and a `[Milestone]` cannot carry a `resolution`.

---

## `[Decision Needed]` — a flow-level trade-off with options and impact

```json
{
  "header": "[Decision Needed]",
  "title": "Which count the result summary reads",
  "summary": "The result summary can read the total the boundary reports for the whole corpus, or the number of rows on the page the operator is looking at. The two disagree whenever the corpus is larger than one page.",
  "options": [
    {"option_id": "corpus_total", "impact": "The summary reports the whole corpus match count, which matches what an operator expects from a search tool."},
    {"option_id": "page_rows",    "impact": "The summary reports what is on screen, which never disagrees with the visible rows but understates the corpus."}
  ],
  "resolution": {
    "chosen_option_id": "corpus_total",
    "rationale": "The operator is searching the corpus, not the page, so the corpus total is the number they came for."
  }
}
```

* At least two options. A single option is not a choice, and a decision with one option is a
  decision already made and stated as a question.
* Every option states its **impact**. An option without its consequence is not something a
  reader can choose between.
* `resolution.chosen_option_id` must name one of the options the section itself offered. A
  resolution naming something that was never on the table is refused.
* **A section with no `resolution` is open**, and an open one blocks
  `STAGE_5_DELIVERY -> COMPLETED` with `UNRESOLVED_DECISION_NEEDED`. The count of open decisions
  is computed by differencing the sections against their own resolutions — the report never
  states it, because a report that states its own unresolved count states the verdict the gate
  exists to establish.

---

## `[Risk]` — an invariant at risk, or an external constraint

```json
{
  "header": "[Risk]",
  "title": "Search boundary latency above the client guard",
  "summary": "A search that takes longer than three seconds trips the client timeout guard and the console returns to idle rather than hanging.",
  "mitigation": "The timeout guard is the invariant under watch. If the boundary starts to exceed it in normal use, the deadline belongs in the scenario matrix as a branch rather than as a guard."
}
```

A `[Risk]` does not block completion. It is the record of what is known and accepted, and it is
carried into the changelog under operational risks so the people running the thing can read it.

---

## `[Accepted Constraint]` — the escape hatch, and a first-class outcome

```json
{
  "header": "[Accepted Constraint]",
  "title": "Narrow viewport floor is not evidenceable in this harness",
  "summary": "The declared verification harness renders the console without a layout engine, so it measures no element geometry at all and reports the same tree at every viewport width. Whether the result list stays inside a 360 pixel floor without overflowing was therefore not verified in the automated run.",
  "alternative_verification": "The result list was opened once by hand in a real browser at a 360 pixel width and read for overflow, and a browser based visual check is queued for the next slice so this stops being manual.",
  "discharged_claim_ids": ["claim:layout:narrow_viewport_floor"]
}
```

This is the most important template on the page, and the most commonly got wrong in both
directions.

* A claim with **no admissible artifact in the declared harness** is emitted here. It **may not
  pass as verified**, and it **may not be reported as a broken gate** either. The second error is
  what makes a team switch the checker off, and a checker that is off catches nothing.
* The `summary` names **what was not verified**. The `alternative_verification` names **how it
  was checked instead**. A section missing either discharges nothing, and the claim it would have
  covered is refused with `EVIDENCE_CLAIM_INADMISSIBLE`.
* `discharged_claim_ids` must name claims some milestone actually evaluated. A discharge for a
  claim nothing evaluated is refused with `DISCHARGED_CLAIM_NOT_EVALUATED`.
* Discharging a claim the capture **can** address is refused with
  `CONSTRAINT_DISCHARGES_ADMISSIBLE_CLAIM`. Without that rule the escape hatch is a way to
  discharge everything, and the gate is off by another route.

---

## `[Milestone]` — a completed flow slice, with its evidence

```json
{
  "header": "[Milestone]",
  "title": "Search console query flow verified",
  "summary": "The operator can enter a query, watch the console move from idle to loading, and read a ranked list of matches.",
  "milestone_refs": ["M1_PURE_FILTER", "M2_API_ADAPTER", "M3_UI_QUERY_FLOW"]
}
```

Every id in `milestone_refs` must have a verified evidence record behind it. `bin/generate-changelog`
refuses to write a release note for a milestone with nothing captured for it.

`[Milestone]` is also the **human sign-off boundary**. The abstraction check below is structural
only; whether a summary sits at the flow level rather than the mechanics level is a judgement a
person makes here, and no regex is asked to make it for them.

---

## `[Blocker]` — a critical impediment halting execution

```json
{
  "header": "[Blocker]",
  "title": "No credential for the search boundary in the staging environment",
  "summary": "The console cannot reach the live search boundary from staging because no credential has been issued for that environment, so the flow cannot be exercised anywhere an operator can see it."
}
```

A `[Blocker]` with no `resolution` blocks `STAGE_5_DELIVERY -> COMPLETED` with
`UNRESOLVED_BLOCKER`. A cleared one carries `{"rationale": "..."}` saying how it was cleared.

Whether a blocker has actually been cleared is an extrinsic fact about the world that no upstream
artifact can prove, so it is recorded as a declaration — but the declaration has to be a
resolution with a rationale, never a flag.

---

## Writing a summary that passes the abstraction check

`bin/verify-evidence lint-abstraction` refuses three shapes in every human-facing passage
(`summary`, `alternative_verification`, `mitigation`, each option's `impact`, and a resolution's
`rationale`):

1. a fenced code block;
2. a unified diff hunk or diff header pair;
3. a code-syntax character density above 15 per cent of the word count.

It does **not** blacklist words. A domain summary legitimately names boundary bindings and entity
fields, and "resolved the `SearchResponse.total_results` binding in the search summary card"
is exactly the sentence this stage wants. A keyword blacklist on `function`, `class`, `import`,
`const` or a file extension rejects it, which is why there is not one.

| Reads as flow | Reads as mechanics |
| :--- | :--- |
| The operator can enter a query and read a ranked list of matches. | Updated the submit handler to call the search endpoint with the query param. |
| The count in the summary now reads the corpus total rather than the page length. | Changed the state hook so the setter fires before the request resolves. |
| A search past three seconds returns the console to idle rather than hanging. | Added a timeout wrapper around the fetch call with an abort controller. |

The rule of thumb: a summary a person who does not read this codebase can act on.

---

## Delivery report shape

```json
{
  "schema_version": "1.0.0",
  "feature_slug": "<slug>",
  "delivered_at": "<ISO 8601>",
  "intent_sections": [ ... the templates above ... ],
  "milestone_evidence_records": [
    {
      "milestone_id": "M3_UI_QUERY_FLOW",
      "slice_id": "slice_1_query_entry",
      "artifact_path": "evidence/m3_ui_query_capture.json",
      "claims_evaluated": [
        {"claim_id": "claim:ui:observable_projections_distinct",
         "claim_kind": "state_observable_and_distinct",
         "evidence_key": "state:RESULTS_POPULATED"},
        {"claim_id": "claim:layout:narrow_viewport_floor",
         "claim_kind": "layout_viewport_floor_overflow"}
      ]
    }
  ]
}
```

There is **no** `declared_medium` on an evidence record and **no** `status` on a claim. The
medium is the one the execution DAG committed the milestone to, and whether a claim is verified
is computed from what the capture carries. Both were removed deliberately: a report that restates
its own medium can move its own goalposts, and a claim that declares its own status declares the
verdict the second axis exists to reach.

See `evidence-claim-matrix.md` for the capture envelope each claim kind needs.
