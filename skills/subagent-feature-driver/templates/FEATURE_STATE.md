# Feature Execution State: [Feature Name]

## 1. High-Level Objective & Invariants
- **Goal**: [Clear one-sentence description of the target capability]
- **Core Invariant**: [Key business rule or domain constraint that must remain true]
- **Boundary**: [Target modules / packages under development]

---

## 2. Milestone DAG (Dependency Graph)

| Milestone ID | Description | Dependencies | Status | Assignee |
| :--- | :--- | :--- | :--- | :--- |
| **M1** | [Core domain model / entities] | None | [Pending / Active / Done] | [Worker / Lead] |
| **M2** | [Application service & business rules] | M1 | [Pending / Active / Done] | [Worker / Lead] |
| **M3** | [External adapter / API handler] | M2 | [Pending / Active / Done] | [Worker / Lead] |
| **M4** | [Integration smoke tests & E2E verification] | M3 | [Pending / Active / Done] | [Lead] |

---

## 3. Active Subagent Slots (Max 2 Concurrently)

- **Worker Slot (Implementer)**:
  - Active Task: [e.g. M1 - Domain model implementation]
  - Session Mode: [Clustered / Fresh]
  - Started: [Timestamp]
- **Sidecar Slot (Investigator / Flex)**:
  - Active Task: [e.g. Researching adapter signatures in neighbor package / Idle]
  - Session Mode: [Fresh]
  - Started: [Timestamp]

---

## 4. Decision Journal (Audit Trail)

- [Decision] YYYY-MM-DDTHH:MM:SSZ (M1: [Short Title])
  - Problem: [Choice that arose]
  - Decision: [Option chosen]
  - Flow Impact: [System/User behavior consequence]
  - Precedent: [Existing repo pattern followed]

---

## 5. Tier-2 Verification Scorecard (Lead Gating)

- [ ] **Contract Integrity**: No internal abstractions or database entities leaked to external boundaries.
- [ ] **Blast Radius Audit**: Zero unexpected file modifications or foreign test regressions.
- [ ] **Integration Pass**: End-to-end smoke test passes without mocked domain core.
- [ ] **Artifact Deliverable**: Clean status summary and diff manifest recorded.
