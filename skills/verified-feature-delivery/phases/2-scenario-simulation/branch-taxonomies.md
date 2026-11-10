# Branch Class Taxonomies: Group A and Group B

**Owner:** `stage-2`  
**Purpose:** Defines the mandatory branch classes for scenario simulation.

---

## Branch Class Group A — Resource & State (7 Mandatory Classes)

Mandatory where the class is present in the feature's boundary domain:

1. **Orphaned / Deleted Target States (`ORPHANED_DELETED_TARGET_STATES`)**:
   In-flight work whose target entity was deleted, merged, or revoked mid-flight. The scenario matrix must model the resolution when operations execute against target entities that have ceased to exist.

2. **Resource Leak & Termination Invariants (`RESOURCE_LEAK_AND_TERMINATION_INVARIANTS`)**:
   Cleanup of scratch storage, open handles, multipart sessions, device claims, and long-lived subscriptions on abort, crash, or eviction. Where a boundary writes to ephemeral storage, this includes a declared expansion-ratio bound and a headroom pre-flight check.

3. **Event Ordering & Monotonic Sequence Clocks (`EVENT_ORDERING_AND_MONOTONIC_SEQUENCE_CLOCKS`)**:
   Out-of-order or delayed delivery resolved by monotonic sequence comparison rather than arrival order. Ensures deterministic state settlement regardless of transport network jitter.

4. **Ingress Handoff Durability (`INGRESS_HANDOFF_DURABILITY`)**:
   Where a trigger is acknowledged before the work is completed, the acknowledgement must be causally downstream of durable persistence. Acknowledging ahead of the commit is a validation failure, not a latency optimisation.

5. **Temporal Egress from Finite-Lifespan States (`TEMPORAL_EGRESS_FROM_FINITE_LIFESPAN_STATES`)**:
   Any state with a deadline — grace period, pending authorization, soft-delete retention, unconfirmed reservation, lease — must name the mechanism that ends it without an external event (scheduler, delayed job, TTL eviction), and Stage 4 must prove the terminal transition occurs when **zero** upstream events arrive.

6. **Compound Publication Ordering (`COMPOUND_PUBLICATION_ORDERING`)**:
   Where the flow publishes several artifacts that reference each other (a manifest and its parts, an index and its rows), declare the publication order or the atomic promotion step. A reference published before its referent is observable as corruption.

7. **Operational Edge Cases (`OPERATIONAL_EDGE_CASES`)**:
   Mid-flight network timeouts, partial payload writes, duplicate transport delivery, and idempotency key handling across all boundary operations.

---

## Branch Class Group B — Trigger & Observation (5 Mandatory Classes)

Mandatory wherever any trigger in the feature is actor-repeatable (`is_actor_repeatable: true`):

1. **Repeat Trigger with Out-of-Order Settlement (`REPEAT_TRIGGER_OUT_OF_ORDER_SETTLEMENT`)**:
   The actor re-triggers before the prior attempt settles, and the older attempt settles last. The flow must explicitly state which attempt is authoritative (e.g. Generation Sequencing where settlements from superseded generations are discarded).

2. **Duplicate Trigger (`DUPLICATE_TRIGGER`)**:
   Double submit, double tap, rapid re-invocation, retried request, or duplicated tool call. The scenario matrix must define deduplication or sequential generation handling.

3. **Observer Teardown Mid-Flight (`OBSERVER_TEARDOWN_MID_FLIGHT`)**:
   The observer disappears before the result arrives — view unmounted, app backgrounded, device asleep, session ended, calling agent cancelled. The flow must ensure clean handle release and avoid invalid state mutation on torn-down observers.

4. **Stale Output Supersession (`STALE_OUTPUT_SUPERSESSION`)**:
   When new work supersedes output already presented, the matrix must state explicitly whether prior output is **retained, dimmed, cleared, or marked superseded** — and any resolution other than *cleared* **splits the affected state** before the projection step runs. A supersession recorded as an attribute of an existing state is invisible to the projection check, because it changes what the observer sees without changing the state set.

5. **Semantic Contradiction (`SEMANTIC_CONTRADICTION`)**:
   A structurally valid request that cannot be satisfied — every boundary returns success and the result is still wrong. The matrix must decide whether the contradiction is pre-detected or explained after the fact.

---

## Invariant and Inapplicability Rules

- **Negative-Space Declaration**: Any branch class that does not apply must be declared as `NOT_APPLICABLE` with an explicit, non-empty `inapplicable_reason`. Skipping or omitting a class fails with `BRANCH_CLASS_UNADDRESSED`. Declaring inapplicability without a reason fails with `BRANCH_INAPPLICABLE_UNREASONED`.
- **Repeatable Trigger Constraint**: If any trigger has `is_actor_repeatable: true`, Group B cannot be omitted or marked `NOT_APPLICABLE`. Attempting to do so fails with `GROUP_B_REQUIRED_ON_REPEATABLE_TRIGGER`.
- **Supersession State Splitting**: Resolutions other than `CLEARED` require materialized state splits in `states[]`. Attribute-form annotations fail with `SUPERSESSION_NOT_MATERIALISED`.

