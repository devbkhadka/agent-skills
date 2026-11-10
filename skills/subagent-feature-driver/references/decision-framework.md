# Autonomous Decision Framework & Decision Journal

Enables continuous unattended forward momentum while strictly isolating data integrity, security, and cost risks.

---

## 1. Clarification Threshold

| Decision Category | Conditions | Protocol |
| :--- | :--- | :--- |
| **Irreversible Fork** | Data loss, dropping tables, non-nullable migrations without default, paid cloud provisioning, unprecedented architecture. | **HALT & PROMPT**: Present options, tradeoffs, and wait for human confirmation. |
| **Precedented Architecture** | Adding domain services, interfaces, or adapters with precedent in repo. | **AUTONOMOUS**: Follow dominant repo pattern, log in Decision Journal. |
| **Tactical / Implementation** | Naming, schema primitive types, existing library utilities, test fixtures, error types. | **AUTONOMOUS**: Select simplest existing repo pattern, log in Decision Journal. |

---

## 2. Invariants & Domain Boundaries

1. **Ubiquitous Language**: Reuse existing repo domain terms. Introducing synonyms is a defect.
2. **Pure Domain Isolation**: Business logic imports no external I/O, DB, HTTP, or UI packages. Must run unit tests in milliseconds in-memory.
3. **Boundary Validation**: Validate untrusted input at controllers/handlers; pass strongly-typed domain primitives into the core.
4. **No Premature Abstraction**: Never introduce dynamic registries or policy engines for single-use implementations. Function -> module -> aggregate only.

---

## 3. Decision Journal Specification

Record all tactical and architectural decisions in the active feature state using this schema:

```markdown
- [Decision] <ISO-8601-Timestamp> (<Milestone ID>): <Short Title>
  - Context: <Decision fork encountered>
  - Selected: <Chosen pattern or option>
  - Flow Impact: <System/user flow effect, not raw code mechanics>
  - Precedent: <File or pattern in repo that justified choice>
```

### Examples

```markdown
- [Decision] 2026-09-24T10:15:00Z (M2): Tenant Association
  - Context: Associating teams with organization without session invalidation.
  - Selected: Nullable `organization_id` column with DB default + background backfill.
  - Flow Impact: Existing sessions remain active during rollout; new teams inherit org context.
  - Precedent: Follows `db/migrations/20240312_add_user_roles.sql`.

- [Decision] 2026-09-24T11:42:00Z (M4): Rate Limiter Sliding Window
  - Context: Sliding-window timestamp tracking needed for throttling.
  - Selected: In-memory standard library deque bounded to window size.
  - Flow Impact: Throttles bursts locally without requiring external Redis infrastructure.
  - Precedent: Aligns with zero-dependency domain core in `src/domain/rate_limit.py`.
```
