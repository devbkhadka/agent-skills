---
name: smart-context-lite
description: Find and learn trustworthy project context through the REST API. Query existing concepts first; use ask for questions or learn for an explicit feature hierarchy with revision provenance.
metadata:
  type: skill
  domain: technical
  status: active
  scope: organization
  priority: high
  tech_stack: [typescript, fastify, sqlite]
  module: context-engine:api
  created_date: 2026-10-04
  last_updated: 2026-10-11
---

# Skill: Smart Context Lite

**Purpose:** Get project context before reading or changing code: what remembered **concepts** say about a topic, which **anchors** (files, code symbols, db tables, log groups, tools) back them, whether they have **drifted**, and what the **catalog** and **project map** list. Replaces the deprecated `docs/skills/smart-context-search/SKILL.md`. The search-ui-v2 console (`npm run dev` on `:5173`) proxies `/v1/*` to this service, so `http://localhost:5173/v1/...` works too while the console is running. The context-engine does not index the vault; hand-written ADRs and notes are not searchable here.

## Configuration & Resolution

Check for local uncommitted configuration in `context-engine.md`, `.context-engine.md`, or `.git/context-engine.md` (or repo `AGENTS.md` / `GEMINI.md`). If not explicitly defined, resolve dynamically with worktree awareness:

```bash
# Target server URL (default: http://127.0.0.1:4317)
CE="${CE_URL:-http://127.0.0.1:4317}"

# Bearer token, only when the server sets CE_API_TOKEN (see Authentication below). Every curl below passes
# "${AUTH[@]}"; with no token the array is empty and the header is omitted.
AUTH=(); [ -n "$CE_API_TOKEN" ] && AUTH=(-H "Authorization: Bearer $CE_API_TOKEN")

# Resolve main repository root even when running inside a linked git worktree:
MAIN_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
if [ -n "$MAIN_GIT_DIR" ]; then
  REPO_NAME="$(basename "$(cd "$MAIN_GIT_DIR/.." && pwd)")"
else
  REPO_NAME="$(basename "$PWD")"
fi

# Project and org slugs (override via context-engine.md or CE_PROJECT, default to repository name).
# PROJECT must be the engine's project id for the TARGET codebase, which is often not this repo's name,
# e.g. CE_PROJECT=incyt when working on the incyt code from another checkout.
PROJECT="${CE_PROJECT:-$REPO_NAME}"
ORG="${CE_ORG:-$PROJECT}"

# Verify service liveness before executing queries
curl -s "$CE/healthz" || echo "Smart Context Lite is not running at $CE"

# Verify PROJECT is a configured project: 404 {"detail":"project \"…\" not found"} means it is not
curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}\n' "$CE/v1/projects/$PROJECT/map"
# Project ids that hold concepts, with counts
curl -s "${AUTH[@]}" "$CE/v1/concepts?limit=200" | jq -r '.items[].scope.project' | sort | uniq -c
```

The default `PROJECT` is the basename of the current repository's main worktree. The context-engine repo itself (`smart-context-lite`) is not a configured project and holds no concepts. `GET /v1/concepts?project=<unknown>` answers `404` (`{"detail":"project \"…\" not found"}`), like `/v1/projects/:id/*`. The catalog list for an unknown project still answers `200` with `{"total":0,"items":[]}`, so an empty catalog can mean a wrong `PROJECT` rather than missing metadata. Set `CE_PROJECT` to the target project id (for example `incyt`) before running the examples below.

### Authentication

`CE_API_TOKEN` is optional. When the server sets it, every route except `/healthz` and `/docs/json` requires `Authorization: Bearer $CE_API_TOKEN`. A missing or wrong token answers `401 {"detail":"missing or invalid bearer token"}` with a `WWW-Authenticate: Bearer` header. The server refuses to start on a non-loopback `CE_HOST` (anything other than `127.0.0.0/8`, `::1` or `localhost`) without a token, so a token is always set on a reachable bind. The default bind is `127.0.0.1:4317` with no token.

Request scope org/project/module values are slugs (`[A-Za-z0-9_.-]`). Stored concepts and relation responses may contain free-text module names such as `client-api/billing`; do not copy these into request scope without checking the accepted shape. Write paths without a trailing slash.

---

## 1. Fast Retrieval vs Concept Learning

Smart Context Lite has two core functions:
1. **Free Retrieval**: Querying existing concepts, catalog entries, project maps, and tools (returns in milliseconds, zero model cost).
2. **Concept Learning & Synthesis**: `POST /v1/ask` investigates a question and can record concepts; `POST /v1/learn` prioritizes recording a reusable feature hierarchy. Both use the same persistent knowledge store and validation pipeline.

> [!IMPORTANT]
> **Free endpoints (`/v1/search`, `/v1/projects/:id/map`, `/v1/catalog`) NEVER learn or record concepts.**
> Finding raw code symbols, files, or project maps in search does NOT mean a concept exists. Manual source reading does not add concepts to the knowledge store.

| Need | Call | Cost |
| :--- | :--- | :--- |
| Find anything by words (concepts, catalog entries, tools, code symbols) | `POST /v1/search` | free |
| List or open concepts, filter by verdict | `GET /v1/concepts`, `GET /v1/concepts/:id` | free |
| Related concepts & parallel flows (semantic knowledge graph) | `GET /v1/concepts/:id/relations` | free |
| Structural parents, children, ancestors and bounded subtree | `GET /v1/concepts/:id/hierarchy?depth=2&maxNodes=50` | free |
| Record an evidence-backed relationship | `POST /v1/concepts/:id/relations` | free; writes engine knowledge |
| Retire a relationship | `DELETE /v1/concepts/:id/relations/:relationId` | free; writes engine knowledge |
| Explicitly curate module scope | `PATCH /v1/concepts/:id/scope` | free; writes engine knowledge |
| Rename a concept or replace its aliases | `PATCH /v1/concepts/:id` | free; writes engine knowledge |
| Retire an obsolete or wrong concept (and its relations) | `DELETE /v1/concepts/:id?note=…` | free; writes engine knowledge |
| Explicitly learn/update a feature hierarchy from local HEAD | `POST /v1/learn` | **paid** |
| Tables, repo projects, log groups (metadata only) | `GET /v1/catalog` | free |
| The compact project map, ranked for a question | `GET /v1/projects/:id/map?q=…` | free |
| Read-only query tools | `GET /v1/tools`, `GET /v1/tools/:id` | free |
| Re-validate one tool against the database (LIMIT 1 dry run) | `POST /v1/tools/:id/validate` | free; writes tool status |
| Re-check one concept's anchors now | `POST /v1/concepts/:id/verify` | free |
| Endpoint docs | `GET /v1/help` (index of every route; `?api=<name or path>&version=detailed` for one); request/response schemas: `GET /docs/json` | free |
| Learn & record concepts for a new domain flow, or answer questions not covered by existing concepts | `POST /v1/ask` | **paid** (learns concepts) |
| Re-anchor one drifted concept | `POST /v1/concepts/:id/repair` | **paid** |
| Re-anchor a project's drifted concepts (up to 5 per call) | `POST /v1/projects/:id/repair` | **paid** |

### Execution Protocol:
User instructions limiting paid calls or live operations take precedence over this protocol. Inspecting source or running mocked tests does not require a paid learning call. For an explicit feature/module learning request, use the learn workflow below; ask remains the question-oriented learning entry point.
1. **Check for existing concepts first**: Query `GET /v1/concepts?project=$PROJECT&q=...` or `POST /v1/search` with `category: concept`.
2. **If relevant concepts cover the task**: Retrieve their hierarchy/relations and follow anchors for free. Call ask or learn when coverage is insufficient or the user explicitly requests a live answer, learning update, or API test; an existing concept is not a reason to skip that request.
3. **If NO concept exists for the topic/flow**: Use `POST /v1/ask` for a question, or `POST /v1/learn` for recording an explicit feature/module hierarchy. These are paid calls charged to the daily budget (`CE_DAILY_BUDGET_USD`, default $10). Manual source inspection does not persist concepts; use the learning API when paid learning is authorized.
4. **Daily Budget**: Ask and learn share accounting. Avoid unconstrained automated loops and honor user limits on paid calls and live operations.

**How the budget is enforced.** The budget is per UTC calendar day: `CE_DAILY_BUDGET_USD` (default `10`; `0` means unlimited) caps logged agent spend since UTC midnight. Every agent run is checked before it starts: ask (learn runs as an ask), the standard-tier escalation inside an ask, and concept repair. A run's cost is known only after it ends, so each in-flight run holds `agent.runEstimateUsd` (default $0.10) against the day until its spend is logged. A run is refused when logged spend + what other in-flight runs hold + its own estimate exceeds the limit. Runs can therefore be refused while some budget is still left, and a run that costs more than its estimate can still finish past the limit. A refused ask, learn or concept repair answers `429` (see Errors), and its `spentUsd` includes the in-flight holds. A refused escalation keeps the lite answer, when there is one, and reports `run.escalation.skipped: "budget"`. Project repair does not answer 429: it still verifies each concept in the batch and reports outcome `budget` for each one it could not repair.

---

## 2. Search (`POST /v1/search`)

```bash
curl -s "${AUTH[@]}" -X POST "$CE/v1/search" -H 'content-type: application/json' -d "{
  \"query\": {
    \"text_match\": \"invoice generation\",
    \"filters\": { \"operator\": \"AND\", \"conditions\": [
      { \"field\": \"project\",  \"operator\": \"EQUALS\",     \"value\": \"$PROJECT\" },
      { \"field\": \"category\", \"operator\": \"IN\",         \"value\": [\"concept\", \"db-table\"] },
      { \"field\": \"status\",   \"operator\": \"NOT_EQUALS\", \"value\": \"stale\" }
    ] }
  },
  \"options\": { \"limit\": 10 }
}"
```

```json
{ "total_results": 4,
  "results": [
    { "file_path": "concept:<concept-id>", "is_summary": true, "is_promoted": false, "score": 1,
      "snippet": "Draft invoices are generated …\n\nFlow: scheduler -> queue -> generateDraftInvoice",
      "metadata": { "type": "concept", "module": "billing", "verdict": "fresh", "confidence": 0.65,
                    "anchors": { "db-table": ["incyt_billing.invoice"], "code-symbol": ["libs/billing-service/…#generateDraftInvoice"] } },
      "links": { "detailed": null, "parent_moc": null, "successor": null } },
    { "file_path": "db-table:incyt_billing.invoice", "score": 0.71, "snippet": "incyt_billing.invoice — columns: id, org_id, …" } ] }
```

- `text_match`: full-text, any term. `score` is 0..1 relative to the best match in the same index. `total_results` counts every match, not just the page.
- `options`: `limit` (10, max 1000), `offset` (0), `include_detailed` (false; true adds a concept's anchor list or a table's full columns to the snippet), `promote_detailed` (false), `promotion_threshold` (0.8).
- `file_path` is `kind:ref`: `concept:<id>`, `repo-project:<name>`, `db-table:<schema.table>`, `log-group:<name>`, `tool:<id>`, `code-symbol:<path>#<Symbol>`.

**Filter fields** (aliases in brackets):

| Field | Values / notes |
| :--- | :--- |
| `project` | e.g. `incyt` |
| `module` | concept scope module or catalog entry module, e.g. `billing` |
| `category` [`type`, `kind`] | `concept`, `repo-project`, `db-table`, `log-group`, `tool`, `code-symbol` |
| `status` | concept verdict `fresh`, `partially-stale`, `stale`, `broken`; `active` for catalog entries and code symbols; tool status `candidate`, `validated`, `active`, `deprecated` |
| `name` [`title`] | concept name and aliases, entry title, symbol name |
| `anchors` [`anchor`] | refs a concept is anchored to; a code-symbol ref also matches its file path |
| `file_path` [`filename`, `ref`] | `kind:ref` or the bare ref |
| `tags` [`scope`] | Nx project tags (repo-project only), e.g. `type:lib` |
| `frameworks` [`framework`] | Architectural frameworks (repo-project only), e.g. `react`, `trpc`, `express`, `prisma`, `nx` |
| `confidence` | concept confidence 0..1, use `BETWEEN` |
| `last_updated` [`date_range`] | `YYYY-MM-DD`, use `BETWEEN` |
| `document_level` | `summary` or `detailed` |

**Operators:** groups `AND`, `OR` (with `conditions`); leaves `EQUALS`, `NOT_EQUALS`, `CONTAINS`, `NOT_CONTAINS`, `IN` (scalar or list = any-of), `EXISTS` (boolean), `BETWEEN` (`{ "start", "end" }`). Maximum nesting depth 2.

Useful filters: "who anchors this file" → `{ "field": "anchors", "operator": "EQUALS", "value": "libs/billing-service/src/queue.ts" }`; "find a symbol" → `category = code-symbol` plus `text_match`.

---

## 3. Concepts, catalog, project map

```bash
curl -s "${AUTH[@]}" "$CE/v1/concepts?project=$PROJECT&q=invoice&limit=10"
curl -s "${AUTH[@]}" "$CE/v1/concepts?project=$PROJECT&status=partially-stale"   # status: a verdict or "retired"
curl -s "${AUTH[@]}" "$CE/v1/concepts?project=$PROJECT&module=billing&limit=50&offset=50"  # module word prefix; next page
curl -s "${AUTH[@]}" "$CE/v1/concepts/<concept-id>"
```

```json
{ "id": "<concept-id>", "name": "Draft invoice generation", "aliases": ["invoice draft"],
  "scope": { "org": "incyt", "project": "incyt", "module": "billing" },
  "summary": "…", "flowNote": "scheduler -> queue -> generateDraftInvoice",
  "confidence": 0.65, "lastVerdict": "fresh", "lastVerifiedAt": "…", "status": "active",
  "anchors": [
    { "kind": "code-symbol", "ref": "libs/billing-service/…#generateDraftInvoice", "status": "fresh", "derived": false, "checkedAt": "…" },
    { "kind": "log-group", "ref": "incyt/v2/backend-services", "status": "unchecked", "derived": true } ] }
```

`GET /v1/concepts` returns `{"items": [<concept>, …]}`. Query parameters: `project` (omit to list every project; an unknown project answers `404`), `module` (keeps concepts whose module starts with these words, the same rule as structural containment: `billing` matches `billing` and `billing-service`, not `client-api/billing`), `q` (matches name, aliases and summary), `status`, `limit` (1–200, default 50) and `offset` (default 0; page until a page is shorter than `limit`). Any other parameter answers `400`. `GET /v1/concepts/:id` returns one concept (shown above, abridged; it also carries `repairFailures`, `uses`, `createdAt`, `updatedAt` and per-anchor `fingerprint`/`note`) plus a compact `navigation` hierarchy: the concept and its direct children. `navigation.truncated` is `true` only when deeper nodes exist; read them through `/hierarchy`.

```bash
curl -s "${AUTH[@]}" "$CE/v1/catalog?project=$PROJECT&kind=repo-project&q=express&limit=5" # kind: repo-project | db-table | log-group; also module, offset
curl -s "${AUTH[@]}" "$CE/v1/projects/$PROJECT/map?q=invoice%20generation"                # {projectId, question, chars, truncated, projects, tables, logGroups, map}
curl -s "${AUTH[@]}" "$CE/v1/concepts/<concept-id>/relations"                         # list outgoing/incoming semantic relations with action guidance
```

```json
{
  "relations": [
    { "id": "<rel-id>", "kind": "alternate-to", "direction": "outgoing",
      "summary": "Manual credit ingestion managed by support after bank invoice payment verification",
      "confidence": 0.85, "health": "healthy", "actionGuidance": "check_compatibility",
      "counterpart": { "id": "<other-concept-id>", "name": "Manual Invoice Credit Grant", "summary": "Manual grant after verified payment",
                       "scope": { "org": "incyt", "project": "incyt", "module": "billing" }, "lastVerdict": "fresh", "status": "active" } }
  ]
}
```

- **Concept Relations (Knowledge Graph)**: Find substantive cross-cutting workflows and parallel flows (`GET /v1/concepts/:id/relations`). It returns `{"relations": [...]}` with active **semantic** edges only (`dependent-on`, `alternate-to`, `coupled-with`, `reconciles-with`). Structural `includes` (parent/child) edges are served only by `/hierarchy`. `direction` is relative to the path concept: `outgoing` when it is the stored source, `incoming` when it is the stored target. `counterpart` is the concept at the other end (the target when `outgoing`, the source when `incoming`). Each relation also shows derived health (`healthy`, `degraded`, `broken`) and `actionGuidance`, one of `must_follow`, `check_compatibility`, `inspect_subflow`, `inspect_parent`, `check_invariants`, `optional`:
  - `dependent-on`: Prerequisite/co-requisite flow (`must_follow`, in either direction).
  - `alternate-to`: Sibling/parallel mechanism (`check_compatibility`).
  - `coupled-with`: Shared database table/balance/invariant (`check_invariants`).
  - `reconciles-with`: Background/eventual consistency reconciliation flow (`optional`).
  - `includes` (structural, parent→child; seen in ask briefs, not in `/relations`): `inspect_subflow` from the parent, `inspect_parent` from the child.
  - Legacy custom kinds written before the closed set (for example `decouples-from`) were retired by store migration 3 and are no longer listed.
- **Record relations**: `POST /v1/concepts/:id/relations` with exactly `{"targetConceptId": "...", "kind": "alternate-to", "summary": "...", "confidence": 0.8}`. `confidence` is optional, 0..1: a new relation defaults to 0.7, and a rewrite of an existing one keeps its confidence. The body is strict: any other key answers `400` with `(root): Unrecognized key: "…"` in `detail`; a misspelt required key is reported once, for example `targetConceptId: required; got unrecognized key "target_concept_id" (did you mean "targetConceptId"?)`. The path concept is the source. Kinds are a closed set (`dependent-on`, `alternate-to`, `includes`, `coupled-with`, `reconciles-with`). The synonyms `depends-on`, `alternative-to`, `alternative-flow` and `coupled-invariant` are also accepted, plus `child-of`, which is stored as a reversed `includes` (`child-of` is never stored). Kind input is trimmed, lowercased and `_` becomes `-`, so `DEPENDS_ON` works. Symmetric kinds (`alternate-to`, `coupled-with`, `reconciles-with`) are stored once with endpoints in id order, so a repeated write of the same fact, in either direction or through a synonym, updates one edge and reactivates it if it was retired. Both concepts must exist (404 otherwise, naming the request's own side: `Source concept` is the path concept, `Target concept` is `targetConceptId`, even for `child-of`) and be in the same project (400). Self-links and unknown kinds answer 400. Structural `includes` has the extra rules listed under hierarchy below. The response is the stored relation in canonical form, `{id, sourceConceptId, targetConceptId, kind, summary, confidence, status, createdAt, updatedAt}`, so `sourceConceptId`/`targetConceptId` may be swapped relative to the request.
- **Retire a relation**: `DELETE /v1/concepts/:id/relations/:relationId`, where `:id` is the concept at either end. It marks the relation `retired` (the row is kept) and returns `200` with the same relation view, `status: "retired"`. It answers `404` when the relation does not exist or is not attached to `:id`. The relation then disappears from `/relations` and `/hierarchy`. Recording the same fact again reactivates it.

```bash
curl -s "${AUTH[@]}" -X POST "$CE/v1/concepts/<concept-id>/relations" -H 'content-type: application/json' \
  -d '{ "targetConceptId": "<other-concept-id>", "kind": "dependent-on", "summary": "Settlement needs the purchase validation result", "confidence": 0.8 }'
# {"id":"<rel-id>","sourceConceptId":"<concept-id>","targetConceptId":"<other-concept-id>","kind":"dependent-on","summary":"…","confidence":0.8,"status":"active","createdAt":"…","updatedAt":"…"}
curl -s "${AUTH[@]}" -X DELETE "$CE/v1/concepts/<concept-id>/relations/<rel-id>"   # the same view with "status":"retired"
```

- **Retire a concept**: `DELETE /v1/concepts/:id`, with an optional `?note=` that is kept in the concept history (any other query parameter answers `400`). In one transaction it marks the concept `retired`, retires every active relation it is part of (semantic and structural, both directions) and records a `retired` history entry. It returns `200` with `{"concept": <concept, "status":"retired">, "retiredRelationIds": ["<rel-id>", …]}`. Retiring again answers `200` with the same concept and `retiredRelationIds: []`; an unknown id answers `404`. A retired concept disappears from search, ask recall, `/hierarchy` and `/relations`, and from `GET /v1/concepts` unless `status=retired`. Its children stay active and only lose the edge; reattach them with `POST …/relations` (`includes`) if they belong under another parent. Rows are kept; there is no un-retire endpoint.

```bash
curl -s "${AUTH[@]}" -X DELETE "$CE/v1/concepts/<concept-id>?note=duplicate%20of%20<other-concept-id>"
# {"concept":{"id":"<concept-id>","status":"retired",…},"retiredRelationIds":["<rel-id>"]}
```

- **Relational Briefing in `POST /v1/ask`**: Recalled concepts automatically include `# Related concepts & parallel flows` with action guidance, warning the agent of parallel mechanisms and co-dependencies upfront. Learned concepts can also propose relations. A proposed relation endpoint resolves by concept id (`c_` + 16 hex, given alone or embedded in a name), then exact name or alias, then a unique name prefix among the run's learned and recalled concepts; anything else lands in `rejectedRelations` with the reason. A learned concept named exactly like an existing project concept (name or alias) updates it, whatever module the agent proposed. Stored names and aliases never carry a concept id: an id the agent copies into a learned concept's name or alias is stripped, and when it names an active concept of the project, that concept is the one updated.
- **Curated architectural frameworks**: Repo projects scanned from `apps/*`, `libs/*`, `packages/*`, and root workspace (`.`) capture foundational frameworks (`react`, `next`, `vue`, `svelte`, `express`, `fastify`, `nestjs`, `trpc`, `koa`, `prisma`, `typeorm`, `drizzle`, `mongoose`, `nx`, `turborepo`, `graphql`, `apollo`, `aws-sdk`, `mqtt`, `socket.io`) in `detail.frameworks`.
- **Project map ranking & format**: Renders compact project signposts formatted as `- <name> [<type>] (<frameworks>) <root>` (e.g. `- client-api [application] (express, trpc) apps/client-api`). Ranking scores question stems against both project tags and detected framework indicators.

---

## 4. Read results as signposts

A concept is a **signpost, not an answer**. Open its anchors before you rely on it.

**Concept verdict** (`lastVerdict`, `metadata.verdict`, `concepts[].verdict`), computed from anchor statuses with `unchecked` anchors ignored:

| Verdict | Meaning | What to do |
| :--- | :--- | :--- |
| `fresh` | every checked anchor matches its fingerprint | use as a starting point; still open the anchors |
| `partially-stale` | some anchors changed or are gone | trust only the `fresh` anchors; re-read the rest |
| `stale` | every checked anchor changed | treat the summary as history |
| `broken` | every checked anchor is gone | ignore the summary; search again |

**Anchor status:** `fresh`, `stale` (content changed), `broken` (gone), `unchecked` (not verified; log groups stay unchecked). `derived: true` marks anchors the server added itself. Anchor `kind`: `file`, `code-symbol` (`path#Symbol`), `db-table`, `log-group`, `tool`. Paths are relative to the project's repo root.

**Confidence** (0..0.95, new concepts 0.6) rises on a fresh verification and falls on new drift. Concepts below 0.2 are not recalled by ask. `status: retired` concepts are hidden from search.

**Ask evidence:** `evidence[]` is `{kind, ref, note}` proposed by the agent. Resolve refs before citing them; invalid paths can remain in answer/evidence text even when `droppedAnchors` excludes them from concept persistence. A fresh concept verdict checks fingerprints, not whether every generated claim is correct. Prefer executable conditions, constants and relevant tests over conflicting comments or planning docs.

---

## 5. Ask (`POST /v1/ask`), paid

```bash
curl -s "${AUTH[@]}" -X POST "$CE/v1/ask" -H 'content-type: application/json' -d "{
  \"question\": \"How are draft invoices generated?\",
  \"scope\": { \"org\": \"$ORG\", \"project\": \"$PROJECT\", \"module\": \"billing\" }
}"
```

```json
{ "answer": "…", "confidence": 0.8,
  "evidence": [ { "kind": "code-symbol", "ref": "libs/billing-service/…#generateDraftInvoice", "note": "…" } ],
  "concepts": [ { "id": "<concept-id>", "name": "Draft invoice generation", "verdict": "fresh", "origin": "learned" } ],
  "droppedAnchors": [], "relations": [], "rejectedRelations": [], "tools": { "used": [], "proposals": [] },
  "run": { "warm": false, "tier": "lite", "costUsd": 0.0269, "durationMs": 230000, "toolCalls": 20, "toolBudget": 20, "escalation": null } }
```

- **Scope is `{org, project, module?}` only.** No `category` or other filter. `org` must own `project` (otherwise `400`).
- `module` narrows which concepts are recalled (same module or no module) and becomes the default module of concepts the ask learns. It does **not** restrict which files the agent reads.
- `question` is 3 to 2000 characters. The call is synchronous. Do not set a short curl timeout: a single agent run may take up to 5 minutes, and escalation adds a second run.
- `origin`: `recalled` (already known), `learned` (new), `updated`. `run.warm: true` means recall met the relevance threshold for a smaller ask investigation budget. A nonempty `run.recalled` does not guarantee warm execution. Inspect `run.model`, `tier`, `calls`, `toolCalls`, `toolBudget`, and `costUsd`; warm execution does not guarantee a complete or cheaper answer. `run.tier` is `lite` or `standard`; `run.escalation.reason` is `low-confidence`, `no-evidence` or `invalid-output`.
- `droppedAnchors` lists anchors the server refused (did not resolve). Don't cite them.
- `relations` lists persisted links as relation views `{id, sourceConceptId, targetConceptId, kind, summary, confidence, status, createdAt, updatedAt}` in canonical form; `rejectedRelations` lists rejected proposals as `{source, target, reason}`. The agent may give each proposed relation its own `confidence` (0..1); without one, the relation takes the answer's `confidence`. Concept summaries include bounded `navigation`. Ask may create unattached concepts; it does not automatically place every learned concept under an existing feature root.
- **Concept Learning & Persistence**: Ask and learn record persistent concepts discoverable through `GET /v1/concepts`. `origin: "learned"` in ask means a new concept; learn returns created/updated/reused IDs.
- Manual source reading does not record concepts. Use ask or learn when the task calls for persistent learning and paid calls are authorized. Free retrieval remains the first step for existing knowledge.

---

## 5a. Explicit feature learning (`POST /v1/learn`), paid

Use learn to record a feature/module as a navigable hierarchy, optionally from changes after a locally available base revision. The request body is:

```json
{
  "objective": "Learn the checkout feature from changes after the production base",
  "name": "Checkout",
  "scope": { "org": "<org>", "project": "<project>" },
  "baseRevision": "origin/production",
  "startingFiles": ["src/checkout.ts"],
  "coverage": ["Payment settlement", "Cancellation"]
}
```

Required: `objective` (3–2000 characters) and scope. Optional: `name` (≤200 characters), `rootConceptId`, `parentConceptId`, `baseRevision`, `revision`, `startingFiles` (≤50 paths, each tracked at HEAD), and `coverage` (≤30 priorities, each ≤500 characters). Unknown keys are ignored.

- `rootConceptId` names an existing root to update: its name becomes the root the agent must produce, and its module scope must equal `scope.module` when one is given. Without it, the root is `name` or, failing that, the first proposed concept with no incoming `includes`.
- `parentConceptId` attaches the learned root under an existing concept with an `includes` edge (parent → root), subject to the structural rules below. If no root is produced, the attachment is reported in `partialFailures`.
- Both IDs must refer to active concepts in the requested organization/project (`400` otherwise).

The local checkout must have clean tracked files. A supplied `revision` must resolve to checkout HEAD. No fetch, branch switching or target-repository edits occur. Missing local references are rejected. Without `baseRevision`, HEAD is the base: use the objective/starting files to learn an existing module. Branch changes come from the merge-base diff. Unchanged tracked dependencies can provide evidence, and untracked files are excluded from persisted evidence. Code and relevant tests establish implemented behavior; planning documents establish intent only.

Review `rootConceptId`, `descendantConceptIds`, `created`, `updated`, `reused`, `hierarchy`, `relations`, `evidence`, `droppedAnchors`, `rejectedRelations`, `coverageGaps`, `partialFailures`, `revision`, and `run`. `answer` is the agent's narrative, including the coverage gaps it describes. A partial result may have no persisted root. `revision` is `{repositoryIdentity, head, baseRevision, baseRef, mergeBase, changedFiles, trackedFiles}`: repository identity, HEAD, resolved base commit, original base reference, merge-base, changed files and tracked files (the server's repository path is not returned). Cost and trace metadata use the ask run format. `coverageGaps` has one entry per `coverage` term that no hierarchy node name contains (case and separators folded), plus `"Hierarchy navigation reached the response limit"` when the root hierarchy (depth 10, 200 nodes) was truncated. Coverage matching assists review; it does not prove that every requested behavior was learned.

Without a supplied name/root ID, the first proposed concept with no incoming structural relation becomes the root; a renamed root ("connectors module" for `name` "Connectors") is still matched. Without `rootConceptId`, the root updates an existing concept only when that concept's name or alias is exactly the root name in the run's module; otherwise a new root is created. If the agent's root named another existing concept (by name, alias or embedded id), that concept is left untouched and `partialFailures` says so. A new root without tracked anchors inherits up to 5 file anchors from `startingFiles` and its children. Learned and updated concepts unreachable from the root are attached under it with `includes` (semantic edges are kept); an attach the structural rules reject is reported in `partialFailures`. Children always take the root's module (with `rootConceptId` and no `scope.module`, the run adopts the root's module), never a module the agent proposes.

Learn runs on its own budget: `agent.learn.toolBudget` (default 60 tool calls, env `CE_LEARN_TOOL_BUDGET`) and `agent.learn.maxTimeSec` (default 600, env `CE_LEARN_MAX_TIME_SEC`), separate from ask's `agent.toolBudget` and `agent.maxTimeSec`.

Reruns match names/aliases and upsert validated edges. An exact name or alias match updates the existing concept whatever module the agent proposes; loosely reworded names can still produce near-duplicates. Retrieve existing IDs/scopes first and pass `rootConceptId` when updating a known root. Updates are additive and preserve unrelated or curated relationships. Renamed concepts should retain an alias. Calls are synchronous and paid; a retry can incur a new agent run even when concepts are reused. Do not retry paid failures in an unattended loop.

```bash
curl -s "${AUTH[@]}" "$CE/v1/concepts/<root-id>/hierarchy?depth=2&maxNodes=50"
```

Hierarchy returns `conceptId`, `parents`, `children`, `ancestors`, `nodes` (`id`, `name`, `depth`), `edges` (`parentId`, `childId`), and `truncated`. Depth is 0–10 (default 2); node limit is 1–200 (default 50) per collection, with at most `2 * maxNodes` structural edges. A truncated view is incomplete. Concept detail and ask summaries include compact `navigation`.

Containment uses canonical `includes` parent→child links. `child-of` input is normalized to the inverse canonical `includes` edge and is never stored. Concepts can have multiple parents so shared knowledge remains reusable. Every relation across organizations/projects is rejected, as are self-links. A structural link also needs both endpoints active, the child inside the parent's module scope (see below), and no cycle; any violation answers `400` and writes nothing. Cross-cutting relations (`alternate-to`, `dependent-on`, `coupled-with`, `reconciles-with`) are separate and never traversed as descendants. Retired/deleted endpoints disappear from navigation without deleting shared descendants.

Ask and learn share synthesis, anchor validation/fingerprinting, concept matching, confidence, relation validation and persistence. Use free retrieval for existing knowledge, ask for questions, and learn for explicitly recording reusable coverage.

Learning preserves established concept scope; a focused ask cannot narrow a feature-wide root. To deliberately curate scope, use free `PATCH /v1/concepts/<id>/scope` with `{"module": null, "expectedModule": "billing", "note": "Restore feature-wide scope"}` (or set a module string). Organization/project cannot change; active parent/child containment is validated and history recorded.

Read the current scope first and supply `expectedModule` (use `null` for project-wide scope) to guard against concurrent edits. `module` is required and accepts a string or `null`; the endpoint rejects extra fields. A stale expectation or containment-breaking edit is rejected without changing scope or links. Existing content, anchors and verification remain intact.

To fix a concept's names, use free `PATCH /v1/concepts/<id>` with `{"name": "…", "aliases": ["…"], "note": "why"}`; at least one of `name` (trimmed, 1–200 characters) and `aliases` is required. `aliases` replaces the whole list: read the concept first, send every alias to keep, and expect entries to be trimmed and deduped and one equal to the name to be dropped. A rename does not keep the old name; add it to `aliases` if recall should still find it. The edit answers `400` and writes nothing when a name or alias contains a concept id (`c_` + 16 hex) or equals, ignoring case and separators, a name or alias of another active concept in the same project. Only active concepts can be edited; history is recorded, and scope, anchors and verification stay untouched.

A module-scoped parent requires children in its normalized module namespace (`billing` can contain `billing-service`; case and separators are folded). Omit the root module for features spanning unrelated modules. An explicit root must match the requested module scope; when `scope.module` is omitted, the run adopts the root's module. Checkout checks detect observed changes but do not lock the repository or create an immutable snapshot. The staged concept/history batch is atomic; relations are separately validated and written. Rejected relations leave successful concepts and other relations intact and are reported explicitly. History persistence failure rolls back the concept batch. No automatic pruning or replacement occurs.

### Review persisted learning

After learning or an ask that records concepts, compare the narrative with the actual stored IDs. Retrieve the root hierarchy and each relevant concept's relations; confirm expected descendants are reachable, reciprocal structural views share edge IDs, and cross-cutting links stay outside containment. Read `coverageGaps`, `partialFailures`, rejected anchors/relations, and run stop reasons; an HTTP 200 or high confidence does not establish full coverage.

Check for same-name/overlapping concepts under different modules and for learned IDs outside the root subtree. Report these instead of treating them as successful reuse. Within an authorized knowledge-curation task, attach only evidence-backed missing children through `POST /v1/concepts/<root-id>/relations` with `{"targetConceptId":"<child-id>","kind":"includes","summary":"Evidence-backed containment explanation"}`. Do not invent links, silently delete duplicates, or change scope to bypass a legitimate containment constraint. Re-read the saved graph and distinguish curated corrections from model output.

Keep the service stable during synchronous paid calls. Source edits can reload a `tsx watch` server and interrupt an in-flight request. After an empty reply or transport failure, check service health and saved concepts before considering a retry; the request may have performed model work or writes without returning a response, and recorded cost may be incomplete.

---

## 6. After you change code in the target repo

```bash
curl -s "${AUTH[@]}" -X POST "$CE/v1/projects/$PROJECT/sync"              # free: records HEAD, re-verifies concepts the new commits could drift
# {"head":"…","previousHead":"…","mode":"incremental","changedPaths":7,"verified":3,"skipped":12,
#  "verdicts":{"fresh":2,"partially-stale":1,"stale":0,"broken":0},
#  "concepts":[{"id":"<concept-id>","name":"…","verdict":"partially-stale"}],"codeIndex":{"mode":"incremental",…}}

curl -s "${AUTH[@]}" -X POST "$CE/v1/concepts/<concept-id>/verify"     # free: {"verdict":"partially-stale","concept":{…}}
curl -s "${AUTH[@]}" -X POST "$CE/v1/concepts/<concept-id>/repair"     # paid: one lite-agent run
# {"outcome":"repaired","verdictBefore":"partially-stale","verdict":"fresh","confidence":0.6,"changeNote":"…",…}
curl -s "${AUTH[@]}" -X POST "$CE/v1/projects/$PROJECT/repair"            # paid: up to 5 drifted concepts; {"queued":…,"outcomes":[…],"remaining":…}
```

Sync only sees committed changes. Repair a concept only when you will use it again soon. Repair `outcome` is `unchanged` (was fresh, no agent run), `repaired`, `failed`, `retired` (after repeated failed repairs), or `budget` (project repair skipped it because the budget is spent). A retired concept answers `400` on repair, and a second repair of a concept that is already being repaired answers `409`. After a schema change, `POST /v1/projects/$PROJECT/catalog/sync` refreshes the catalog and code index, and reports `toolsNeedingRevalidation`.

---

## 7. Data questions: tools

```bash
curl -s "${AUTH[@]}" "$CE/v1/tools?project=$PROJECT&q=voided%20invoices"     # also status=candidate|validated|active|deprecated
curl -s "${AUTH[@]}" "$CE/v1/tools/<tool-id>"                               # template, typed params (number|string|date|boolean), outputColumns
curl -s "${AUTH[@]}" -X POST "$CE/v1/tools/<tool-id>/run" -H 'content-type: application/json' \
  -d '{ "params": { "p1": "2026-09-01" } }'                   # omitted params take their defaults
# {"columns":[…],"rows":[…],"rowCount":1,"truncated":false,"durationMs":12,"tool":{"id":"<tool-id>","status":"active","version":1}}
```

Only `active` and `validated` tools run. Rows are capped (100) and secret columns are redacted.

```bash
curl -s "${AUTH[@]}" -X POST "$CE/v1/tools/<tool-id>/validate"   # free (no agent run); no body
# {"ok":true,"error":null,"tool":{…tool view, status and outputColumns as recorded…}}
```

Validate re-checks a tool now: the SQL policy, then a `LIMIT 1` dry run with the default params against the project database. It records the output columns and the resulting status (a pass can promote the tool and re-activates a deprecated one), and answers `200` whether or not the tool passed (`ok:false` with `error` on failure). Unknown tool: `404`. Database unavailable: `503`. After a catalog sync reports `toolsNeedingRevalidation`, validate those tools before relying on them.

When no tool fits, use one ad-hoc **read-only SELECT**:

```bash
curl -s "${AUTH[@]}" -X POST "$CE/v1/tools/run-adhoc" -H 'content-type: application/json' -d "{
  \"project\": \"$PROJECT\", \"kind\": \"sql\",
  \"query\": \"SELECT count(*) FROM incyt_billing.invoice WHERE voided_at >= '2026-09-01'\",
  \"purpose\": \"invoices voided this month\" }"
# {"columns":[…],"rows":[…],"rowCount":1,"truncated":false,"shape":"…","shapeHash":"…","tool":null}
```

`kind` is `sql` only. Anything other than a `SELECT` is refused with `400 query rejected: …`. Every run is logged as a trace (query shape and literals, never rows). After 3 runs of the same shape the server promotes it to a tool, so check `GET /v1/tools` first.

---

## 8. Errors

Every error is `{"detail": "…"}` except as noted. Don't retry a 4xx unchanged.

| Status | When | Action |
| :--- | :--- | :--- |
| `400` | Bad body or params (including unknown keys on strict bodies such as relations and scope), unknown project, org/project mismatch, filter refusal (`Filter recursion depth exceeded (max 2)`), rejected SQL, tool not runnable | Fix the request using `detail` |
| `401` | The server has `CE_API_TOKEN` set and the `Authorization: Bearer` header is missing or wrong: `{"detail":"missing or invalid bearer token"}` plus `WWW-Authenticate: Bearer` | Set `CE_API_TOKEN` so `AUTH` carries the header |
| `404` | Unknown concept, relation, tool, project or route | Check the id; list first |
| `409` | Concept repair while the same concept is already being repaired: `{"detail":"concept \"<id>\" is already being repaired"}` | Wait for the running repair and read its result; don't start another |
| `422` | `/v1/search` only: request-shape error; `detail` is a list of `{type, loc, msg, input}` | Fix the field at `loc` |
| `429` | Ask, learn or concept repair refused by the daily budget: `{"detail": "…", "code": "daily_budget_exceeded", "spentUsd": 1.95, "limitUsd": 10, "resetAt": "<next UTC midnight, ISO>"}` plus a `Retry-After` header (whole seconds until `resetAt`). `spentUsd` is logged spend plus in-flight holds, so it can be below `limitUsd` | Stop paid calls until `resetAt`. Don't retry in a loop. Fall back to free calls |
| `502` | `/v1/ask`, `/v1/learn`: the agent failed or gave no answer (`detail` includes what it spent) | Narrow the question or use free calls; don't loop |
| `503` | Database unavailable (tool runs, run-adhoc, validate) | Report it; don't loop |
| `405` | `/v1/search` with a method other than POST | Use POST |

---

## 9. Safety

- Tools and run-adhoc are read-only. Ask, learn, relations, scope curation, sync, verify and repair can update engine records; they never authorize edits to the target repository.
- Never put credentials, DB URLs, passwords or API keys in a request, a `purpose`, or a `question`. Refer to connection settings by env var name only (e.g. `CE_INCYT_DB_URL`). Don't read `.env`/`.envrc` files or `context-engine/config.local.json` to find them.
- Count paid calls and honor the configured daily budget. Use ask for questions and learn for explicit feature hierarchies. Run batches only within the user's authorization and limits; do not ask again for calls already authorized. Stop on budget exhaustion or unexpected failures rather than starting an unbounded retry loop. Knowledge learning does not authorize unrelated business-database queries.
