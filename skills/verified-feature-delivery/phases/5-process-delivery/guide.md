# Phase Guide: Stage 5 — Process-Level Delivery & Intent Sign-Off

**Stage:** `STAGE_5_DELIVERY`
**Owner:** `stage-5`
**Required Exit Artifact:** `delivery-report.json` and the generated `changelog.md`, under `.verified-feature-delivery/<feature-slug>/`
**Gate Validators:** `bin/verify-evidence`, `bin/generate-changelog`

---

## Purpose & Process

1. Format every human-in-the-loop update using the five intent headers. They are a closed set:
   `[Decision Needed]`, `[Risk]`, `[Accepted Constraint]`, `[Milestone]`, `[Blocker]`.
   Templates and the per-header obligations: `intent-templates.md`.
2. Attach evidence keyed to the slice's verification medium. A log line stating that a test
   passed is not evidence that a state was observable.
3. Check that evidence on a second axis — **claim**, not only medium. The two axes are
   independent: `evidence-claim-matrix.md`.
4. Emit any claim with no admissible artifact in the declared harness as an
   `[Accepted Constraint]` naming what was not verified and how it was checked instead. It may
   not pass as verified, and it may not be reported as a broken gate either.
5. Generate the changelog from the verified milestone evidence record — never from the
   version-control log.

### Driver Role & Mandatory Tier 3 Gatekeeper
The driving agent does not run browser observations or write changelogs directly. It dispatches a delivery worker and validates the resulting report via `bin/planner-state advance`. Stage 5 delivery exit strictly enforces a mandatory **Tier 3 Gatekeeper** verification run ([`verification-policy.md`](../4-milestone-execution/verification-policy.md)), executing full regressions, adversarial checks, and Playwright UI observations without bypassing.


---

## Running the gate

Every `rendered_ui` slice is verified by live browser observation, so Node.js and Playwright
must be resolvable here (`PLAYWRIGHT_PATH`, `NODE_PATH`, or `node_modules/playwright` above the
repository). Absent, the gate refuses with `OBSERVATION_BROWSER_UNAVAILABLE`; it does not pass.

```bash
# The exit gate, in the order the process states it
python3 bin/verify-evidence check-all .verified-feature-delivery/<slug>/delivery-report.json \
                                      .verified-feature-delivery/<slug>/execution-dag.json

# One axis at a time, while iterating
python3 bin/verify-evidence lint-intent       .verified-feature-delivery/<slug>/delivery-report.json
python3 bin/verify-evidence lint-abstraction  .verified-feature-delivery/<slug>/delivery-report.json
python3 bin/verify-evidence check-gates       .verified-feature-delivery/<slug>/delivery-report.json
python3 bin/verify-evidence verify-medium     .verified-feature-delivery/<slug>/delivery-report.json \
                                              .verified-feature-delivery/<slug>/execution-dag.json
python3 bin/verify-evidence verify-claim      .verified-feature-delivery/<slug>/delivery-report.json \
                                              .verified-feature-delivery/<slug>/execution-dag.json

# The release record, refused unless the report verifies first
python3 bin/generate-changelog .verified-feature-delivery/<slug>/delivery-report.json \
                               .verified-feature-delivery/<slug>/execution-dag.json
```

---

## The transition to `COMPLETED`

§4 refuses `STAGE_5_DELIVERY -> COMPLETED` while any `[Decision Needed]` or `[Blocker]` is open,
or where evidence does not match the declared medium or is inadmissible for its claims. The open
counts are computed by differencing the intent sections against their own resolutions; the report
does not state them.

---

## What this stage does not do

`lint-abstraction` is structural only. It refuses fenced code, unified diff hunks and a
code-syntax density above 15 per cent, and it does not attempt to judge whether prose sits at the
flow level. That judgement belongs to the human reviewer at `[Milestone]` sign-off, and the tool
says so in its own output rather than letting a reader mistake the check for more than it is.
