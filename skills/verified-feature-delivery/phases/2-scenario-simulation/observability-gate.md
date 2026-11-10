# The Observability Gate & Normalisation Rules

**Owner:** `stage-2` (claim, advisory) and `stage-4` (refutation/confirmation, authoritative)  
**Purpose:** Defines structured observable projection claims, evidence keys, and per-medium normalisation rules.

---

## The Observability Gate Claim Schema

Every state in the scenario matrix carries a structured `observable_projection` per registered observer — never free-form prose. The claim is authored at Stage 2 and evaluated/refuted at Stage 4:

```json
{
  "state_id": "RESULTS_EMPTY",
  "evidence_key": "state:RESULTS_EMPTY",
  "observable_projections": [
    {
      "observer_id": "operator",
      "medium": "rendered_ui",
      "discriminator": {
        "region": "result_list",
        "template": "no_matches_for_query",
        "affordances": ["clear_filters"]
      }
    }
  ]
}
```

### Core Invariants

1. **Evidence Key Anchor**: Every state must carry a stable `evidence_key` string. Omission is rejected with `MISSING_EVIDENCE_KEY`.
2. **Structured Discriminator**: Every projection must carry a non-empty structured `discriminator` object containing medium-appropriate perceptual atoms. Omission or empty object is rejected with `MISSING_DISCRIMINATOR`.

---

## Stage 2 Claim Check (Advisory) vs. Stage 4 Refutation (Binding)

- **Stage 2 (Claim, Advisory)**:
  `bin/verify-projections` asserts pairwise inequality of the `discriminator` for every pair of states sharing an `observer_id`. Discriminator keys are free-form planner-authored descriptions, so this check catches a collision the planner spelled the same way and misses one they spelled differently.
  - **Behavior**: Emits `DISCRIMINATOR_COLLISION_ADVISORY`, names the collision, raises a mandatory `[Decision Needed]`, and **exits 0**.
  - **Rule**: Making this check block Stage 2 with a non-zero exit is a defect.
- **Stage 4 (Refutation, Authoritative)**:
  The milestone harness computes a **projection fingerprint** from captured runtime evidence artifacts across each slice's medium. Gate 4 fails when two `evidence_key`s fingerprint alike for the same observer (`FINGERPRINT_COLLISION`).

---

## Per-Medium Normalisation Table

Normalisation preserves perceptual distinctions that the observer can perceive, drops non-semantic execution artifacts, and masks runtime data values:

| Medium | Kept | Dropped | Masked |
| :--- | :--- | :--- | :--- |
| `rendered_ui` | element role, accessible name, visible text, `busy`/`disabled`/`expanded`, interactive affordances, document order | class names, style attributes, test ids, framework keys | numerals, values present in the recorded fixture |
| `api_response` | status code, envelope key set, key types | headers not named in the contract | scalar values |
| `process_exit` | exit code, stream templates, warning classes | timestamps, pids, absolute paths | interpolated values |
| `datastore_state` | changed column set, constraint outcomes | physical row ids, write timestamps | scalar values |
| `device_actuation` | command opcodes and their order | timing jitter | operands |
| `agent_transcript` | tool-call names and order, final message template | token counts, latencies | interpolated values |

Masking ensures that two executions of the same state differing only in transient data fingerprint alike, while two distinct states differing in UI wording or affordances fingerprint differently.

