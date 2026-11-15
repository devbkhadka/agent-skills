---
name: change-lane
description: Triage a code change against the project's domain baseline and run only the verification its risk needs — minutes for tweaks, escalating automatically to feature-gaps or the full verified-feature-delivery pipeline when the diff introduces new domain terms, entities, boundaries or contracts.
---

# Change Lane

Once a project has a verified baseline (`docs/domain/`, built with `domain-baseline`), most changes
do not need the six-stage pipeline. `triage-change` reads the diff and decides — deterministically,
from the diff and the baseline, never from the author's say-so — how much verification it needs.

| Lane | Tier | Raised by |
|---|---|---|
| `TWEAK` | TIER_1 | nothing below fired: styles, copy, docs, tests, or logic using only known tokens, boundaries and paths |
| `EXTENSION` | TIER_2 | `NEW_DOMAIN_TOKEN` (UPPER_SNAKE string literal or `Enum.MEMBER` absent from the lexicon), `LEXICON_EXTENSION` (new/changed state enum, action, actor, observer, projection), `SHARED_SCOPE_PATH` (shared core, migrations, feature schemas), `UNMAPPED_PATH`, `TRIAGE_CONFIG_CHANGE` |
| `NEW_CAPABILITY` | TIER_3 | `LEXICON_CAPABILITY_CHANGE` (entity, value object, boundary, unit precision, value authority, fact precedence, clock, simulated dependency added/changed; anything removed), `CONTRACT_CHANGE`, `BOUNDARY_LEAK` (I/O client imported outside adapter paths), `NEW_BOUNDARY` (adapter opens a boundary class the baseline never probed), `NO_BASELINE` |

The lane is the highest any reason asks for. It escalates, never de-escalates.

```bash
python3 ../verified-feature-delivery/bin/triage-change [--repo .] [--base HEAD] \
  [--baseline-dir docs/domain] [--declare change.json] [--json]
```

Input is `git diff <base>` plus untracked files (or `--diff-file`). `--declare` takes
`{"lane": "TWEAK"}`; declaring below the computed lane exits 1 with `LANE_UNDERDECLARED` — use it in
CI so a PR labelled "tweak" cannot skip the verification its diff needs. Exit 2 means unreadable
input (`INVALID_TRIAGE_INPUT`). Configure paths and patterns in `docs/domain/triage.json`
(schema: `../verified-feature-delivery/schemas/triage-config.schema.json`; every key optional).

## Procedure per lane

The driver may run every command below itself: they are deterministic checks with exit-code
verdicts, not judged evidence.

**TWEAK** — genuinely light. No planner-state, no judge packets, no Playwright.
1. Make the change.
2. Run the repo's tests, the architecture fitness check
   (`python3 tools/architecture/check_architecture.py`, see `architecture-rules`) and
   `python3 ../verified-feature-delivery/bin/baseline check`.
3. `triage-change` → still `TWEAK`? Done.

**EXTENSION**
1. Update the flow-simulation matrix for the affected flows and run `feature-gaps` until
   `GAPS_CLEAN`; re-emit acceptance rules.
2. If slices or pure modules change, update the execution DAG and re-emit fitness checks
   (`architecture-rules`).
3. Promote new terms into the baseline (`domain-baseline`: diff → promote → approve only the
   changed elements).
4. Verify at TIER_2: TWEAK checks plus the acceptance rules for the affected flows.

**NEW_CAPABILITY**
1. Amend the baseline first (`domain-baseline`): new entities, boundaries, probed contracts.
2. Deliver through `verified-feature-delivery`, initialised with the lane and baseline
   (`planner-state init --lane NEW_CAPABILITY --baseline-dir docs/domain`) so recorded boundaries
   are reused rather than re-probed.

Re-run `triage-change` after each step: work done in a lower lane that turns up a new signal
escalates the change, it does not get waved through.

## Accepted constraints

- Token detection looks at string literals and `Enum.MEMBER` access, not bare constants
  (`MAX_RETRIES = 3`). A new domain state introduced only as a bare constant is caught by the
  lexicon or feature-gaps step, not here.
- Boundary detection is pattern-based; extend `boundary_import_patterns` for your clients.
