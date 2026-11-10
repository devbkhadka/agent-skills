# Phase Guide: Stage 0 — Domain Ingestion & Ubiquitous Language Definition

**Stage:** `STAGE_0_DOMAIN_INGESTION`  
**Owner:** `stage-0`  
**Required Exit Artifacts:**
- `domain-lexicon.json` (persisted under `.verified-feature-delivery/<feature-slug>/`)
- `docs/ubiquitous-language-glossary.md` (rendered via `bin/render-glossary`)
- `docs/domain-entity-map.md` (rendered via `bin/render-entity-map`)
**Gate Validator:** `bin/validate-lexicon`  
**JSON Schema:** `schemas/domain-lexicon.schema.json`

---

## 1. Objectives & Overview

Stage 0 establishes the formal bounded context, entity taxonomy, state enums, unit precisions, actor/observer registries, cross-boundary alias pairs, projection bindings, value authorities, and fact precedence rules before any behavioral modeling or vertical slicing occurs.

Per §1.1 of the architecture specification:
- **Every declared field names its consumer.** No field is declared without a tool that verifies it.
- **Gates name classes, not technologies.** Technologies like Stripe, SQL, or REST are instances of boundary classes.
- **A class that does not apply is declared, not skipped.**

---

## 2. Process Steps

1. **Ingest Context & Boundaries**: Analyze user requirements, existing codebases, and target integration boundaries (`SUBPROCESS_CLI`, `NETWORK_API`, `DATASTORE`, `UI_RUNTIME`, etc.).
2. **Build the Domain Dictionary**:
   - `entities`: Core entities with typed attributes and lifecycle state references.
   - `value_objects`: Immutable compound structures with explicit data types and precision locks.
   - `actions`: Operations transitioning state machines across defined states.
   - `state_enums`: Exhaustive enum sets with declared `terminal_tokens`.
   - `monotonic_clocks`: Monotonic ordering sources and comparison rules.
3. **Lock Precision Units (`unit_precisions`)**:
   - Explicitly lock measurement units (e.g. `integer_cents`, `unix_epoch_seconds`, `iso8601_datetime_ms`, `seat_count`).
   - Define `quantity_type`, `base_unit`, `numeric_type`, and `rounding_mode`. Floating-point arithmetic for financial or discrete metrics is strictly prohibited.
4. **Register Actors and Observers (`actors_and_observers`)**:
   - `actors`: Parties initiating triggers (users, upstream webhooks, schedulers, workers).
   - `observers`: Parties perceiving state (dashboards, audit logs, monitoring systems). Observers are distinct from triggers.
5. **Declare Cross-Boundary Alias Pairs (`alias_pairs`)**:
   - Map divergent terminology across boundary layers (e.g., UI `file_path` $\leftrightarrow$ REST API `filename`).
   - Aliases must be explicitly declared; collapsing them into ambiguous informal synonyms is forbidden.
6. **Bind Observer Projections (`projection_bindings`)**:
   - For every observable fact/badge/counter, declare its exact derivation source (`CONTRACT_FIELD` or `PURE_EXPRESSION`).
   - Eliminates the inverse naming defect where an observable quantity presents misleading data (e.g. rendering page size as total matches).
7. **Lock Value Authorities (`value_authorities`)**:
   - For dual-computed values across boundaries (prorations, taxes, token counts), assign:
     - `UPSTREAM_AUTHORITATIVE`: Ingest verbatim, never re-assert locally.
     - `LOCAL_AUTHORITATIVE`: Local calculation pushes to upstream.
     - `SHADOW_RECONCILED`: Both calculate and assert agreement within `tolerance_epsilon`.
8. **Define Fact Precedence Rules (`fact_precedences`)**:
   - For domain facts derivable from dual boundaries (e.g., local git ref vs remote GitHub PR status), specify `primary_boundary`, `secondary_boundary`, `precedence_rule`, and `reconciliation_check`.

---

## 3. Tooling & Exit Gate Verification

### Driver Role Rule
The driving agent must not traverse source trees, read massive codebase files, or run validators directly. It dispatches an ingestion worker to analyze the codebase, write `domain-lexicon.json`, and render the human-readable documents.

### Render Readable Documents
```bash
python3 bin/render-glossary --artifact .verified-feature-delivery/<feature-slug>/domain-lexicon.json --out .verified-feature-delivery/<feature-slug>/docs
python3 bin/render-entity-map --artifact .verified-feature-delivery/<feature-slug>/domain-lexicon.json --out .verified-feature-delivery/<feature-slug>/docs
```

### Validate Gate
```bash
python3 bin/validate-lexicon .verified-feature-delivery/<feature-slug>/domain-lexicon.json
```

### Exit Gate Criteria
- Zero undefined state/action tokens (`UNDEFINED_STATE_TOKEN`).
- Zero entity/value object token collisions (`LEXICON_TOKEN_COLLISION`).
- Explicit unit precisions for all numeric/temporal quantities (`MISSING_UNIT_PRECISION`).
- Non-empty actor and observer registers (`EMPTY_OBSERVER_REGISTER`).
- Declared alias pairs for all boundary token divergences (`UNDECLARED_ALIAS`).
- Explicit projection bindings for all observable quantities (`UNBOUND_PROJECTION_QUANTITY`).
- Authority models for all dual-computed values (`MISSING_VALUE_AUTHORITY`).
- Precedence rules for all multi-boundary derivable facts (`MISSING_FACT_PRECEDENCE`).
- Readable Ubiquitous Language Glossary present on disk (`docs/ubiquitous-language-glossary.md`).
- Readable Domain Entity Map present on disk (`docs/domain-entity-map.md`).

