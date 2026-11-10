"""BDD Scenario & Mock-Boundary Contract Engine for verified-feature-delivery.

Enforces:
1. Scenario Derivation & Phasing: Links Given/When/Then to Stage 2 simulation and Stage 1 fixtures.
2. Strict Mock-Boundary Isolation: Real production code executes for domain entities, business logic,
   validation, and state machines. Only registered external boundaries in boundary-manifest.json may
   be mocked, and they must replay verified responses from contract-fixtures/.
3. Interface Discovery & Static Rejection: Undeclared domain actions, state tokens, or projection
   fields reject with bare tokens UNDEFINED_STATE_TOKEN, UNDEFINED_DOMAIN_ACTION, or
   UNBOUND_PROJECTION_QUANTITY.
4. Upstream Refactoring & Rollback Routing: Maps runtime scenario failures deterministically to
   Stage 0, 1, or 2, triggering content-addressed cache invalidation cascades across dependent slices.
5. Evidence & Verification Output: Formats machine-readable execution logs and Markdown reports.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .primitives import (
    check_closed_key_set,
    is_reason_meaningful,
    is_vacuous_container,
)
from .rollback import (
    RollbackFinding,
    classify_collision,
    classify_failure,
    route_cause,
)

# Canonical boundary classes permitted to be mocked if declared PRESENT in boundary-manifest
CANONICAL_BOUNDARY_CLASSES = {
    "SUBPROCESS_CLI",
    "NETWORK_API",
    "FRAMEWORK_MIDDLEWARE",
    "DATASTORE",
    "MESSAGE_TRANSPORT",
    "DEVICE_IO",
    "MODEL_ENDPOINT",
    "UI_RUNTIME",
    "VERIFICATION_HARNESS",
    "OTHER",
}

# Known internal component indicators that must NEVER be mocked
PROHIBITED_INTERNAL_PATTERNS = {
    "entity", "aggregate", "domain", "model", "statemachine", "state_machine",
    "validator", "validation", "service", "pure_module", "usecase", "use_case",
    "business_logic", "repository_impl"
}

ALLOWED_TOP_KEYS = {
    "schema_version",
    "feature_slug",
    "scenario_id",
    "slice_id",
    "description",
    "derived_from",
    "mock_boundary_contract",
    "given",
    "when",
    "then",
    "execution_evidence",
}

ALLOWED_DERIVED_FROM_KEYS = {
    "state_id",
    "transition_id",
    "branch_coverage_class",
}

ALLOWED_MOCK_CONTRACT_KEYS = {
    "isolation_mode",
    "mock_boundaries",
    "prohibited_mocks",
}

ALLOWED_MOCK_BOUNDARY_KEYS = {
    "boundary_identifier",
    "boundary_class",
    "fixture_ref",
    "is_external",
}

ALLOWED_FIXTURE_REF_KEYS = {
    "fixture_path",
    "sample_id",
    "fixture_status",
}

ALLOWED_GIVEN_KEYS = {
    "state_token",
    "entity_states",
    "datastore_state",
    "fixture_presets",
}

ALLOWED_WHEN_KEYS = {
    "entrypoint",
}

ALLOWED_ENTRYPOINT_KEYS = {
    "entrypoint_type",
    "invoked_target",
    "actor_id",
    "action_token",
    "payload",
    "headers",
    "parameters",
    "command_args",
}

ALLOWED_THEN_KEYS = {
    "state_token",
    "state_assertions",
    "observer_projections",
    "event_emissions",
}

ALLOWED_STATE_ASSERTION_KEYS = {
    "entity_name",
    "attribute",
    "operator",
    "expected_value",
    "unit_ref",
}

ALLOWED_OBSERVER_PROJECTION_KEYS = {
    "observer_id",
    "medium",
    "expected_projection",
    "discriminator",
    "projection_quantities",
    "modality",
}

ALLOWED_EVENT_EMISSION_KEYS = {
    "event_name",
    "topic_or_stream",
    "payload",
}

ALLOWED_EXECUTION_EVIDENCE_KEYS = {
    "execution_status",
    "raw_log_path",
    "markdown_report_path",
    "captured_fingerprint",
}


def _extract_declared_states(scenario_matrix: Optional[Dict[str, Any]], lexicon: Optional[Dict[str, Any]]) -> Set[str]:
    """Collect declared state tokens across flow-simulation-matrix and domain-lexicon."""
    declared = set()
    if scenario_matrix and isinstance(scenario_matrix, dict):
        for s in scenario_matrix.get("states", []) or []:
            if isinstance(s, dict) and s.get("state_id"):
                declared.add(str(s["state_id"]))
            if isinstance(s, dict) and s.get("evidence_key"):
                # strip 'state:' prefix if present
                ek = str(s["evidence_key"])
                if ek.startswith("state:"):
                    declared.add(ek[6:])
                declared.add(ek)
    if lexicon and isinstance(lexicon, dict):
        for se in lexicon.get("state_enums", []) or []:
            if isinstance(se, dict):
                for val in se.get("values", []) or []:
                    declared.add(str(val))
        for ts in lexicon.get("terminal_states", []) or []:
            declared.add(str(ts))
    return declared


def _extract_declared_actions_and_actors(scenario_matrix: Optional[Dict[str, Any]], lexicon: Optional[Dict[str, Any]]) -> Tuple[Set[str], Set[str]]:
    """Collect declared trigger/action tokens and actor IDs."""
    actors = set()
    actions = set()
    if scenario_matrix and isinstance(scenario_matrix, dict):
        for a in scenario_matrix.get("actors", []) or []:
            if isinstance(a, dict) and a.get("actor_id"):
                actors.add(str(a["actor_id"]))
        for t in scenario_matrix.get("triggers", []) or []:
            if isinstance(t, dict):
                if t.get("trigger_id"):
                    actions.add(str(t["trigger_id"]))
                if t.get("actor_id"):
                    actors.add(str(t["actor_id"]))
    if lexicon and isinstance(lexicon, dict):
        ao = lexicon.get("actors_and_observers", {})
        if isinstance(ao, dict):
            for a in ao.get("actors", []) or []:
                if isinstance(a, dict) and a.get("id"):
                    actors.add(str(a["id"]))
                elif isinstance(a, dict) and a.get("actor_id"):
                    actors.add(str(a["actor_id"]))
        for act in lexicon.get("actions", []) or []:
            if isinstance(act, dict) and act.get("name"):
                actions.add(str(act["name"]))
    return actors, actions


def _extract_declared_projection_quantities(lexicon: Optional[Dict[str, Any]]) -> Set[str]:
    """Collect declared observer projection quantities and bindings from domain-lexicon."""
    quantities = set()
    if not lexicon or not isinstance(lexicon, dict):
        return quantities
    for pb in lexicon.get("projection_bindings", []) or []:
        if isinstance(pb, dict) and pb.get("fact_name"):
            quantities.add(str(pb["fact_name"]))
        elif isinstance(pb, dict) and pb.get("quantity_name"):
            quantities.add(str(pb["quantity_name"]))
        elif isinstance(pb, dict) and pb.get("observer_fact"):
            quantities.add(str(pb["observer_fact"]))
    for oq in lexicon.get("observable_quantities", []) or []:
        quantities.add(str(oq))
    # Also collect attribute names from entities and value objects
    for ent in lexicon.get("entities", []) or []:
        if isinstance(ent, dict):
            for attr in ent.get("attributes", []) or []:
                if isinstance(attr, dict) and attr.get("name"):
                    quantities.add(str(attr["name"]))
    return quantities


def _extract_present_boundary_identifiers(manifest: Optional[Dict[str, Any]]) -> Set[str]:
    """Collect all boundary properties, probe paths, and identifiers declared as PRESENT in boundary-manifest."""
    present = set()
    if not manifest or not isinstance(manifest, dict):
        return present
    rows = manifest.get("boundary_classes") or manifest.get("manifest_rows") or []
    for row in rows:
        if isinstance(row, dict) and row.get("verdict") == "PRESENT":
            for prop in row.get("properties", []) or []:
                if isinstance(prop, dict):
                    if prop.get("boundary_identifier"):
                        present.add(str(prop["boundary_identifier"]).strip())
                    if prop.get("property_name"):
                        present.add(str(prop["property_name"]).strip())
            if row.get("probe_path"):
                present.add(str(row["probe_path"]).strip())
            if row.get("boundary_identifier"):
                present.add(str(row["boundary_identifier"]).strip())
    return present



def validate_bdd_scenario(
    scenario: Dict[str, Any],
    lexicon: Optional[Dict[str, Any]] = None,
    manifest: Optional[Dict[str, Any]] = None,
    scenario_matrix: Optional[Dict[str, Any]] = None,
    fixtures_by_boundary: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Validate a single BDD scenario and its mock-boundary contract against upstream stages.

    Returns:
        (is_valid, list_of_error_tokens)
    """
    errors: List[str] = []

    if not isinstance(scenario, dict):
        return False, ["INVALID_SCENARIO_DOCUMENT: scenario must be a dictionary"]

    # 1. Closed key set validation
    closed_top = check_closed_key_set(scenario, ALLOWED_TOP_KEYS, label="bdd-scenario")
    if not closed_top.is_valid:
        errors.append("UNKNOWN_SCENARIO_ATTRIBUTE: %s" % closed_top.message)

    for req in ("schema_version", "feature_slug", "scenario_id", "slice_id", "description",
                "derived_from", "mock_boundary_contract", "given", "when", "then"):
        if req not in scenario or scenario[req] is None:
            errors.append("MISSING_REQUIRED_ATTRIBUTE: %s is required" % req)

    if errors:
        return False, errors

    # Derived from
    derived = scenario.get("derived_from")
    if isinstance(derived, dict):
        closed_der = check_closed_key_set(derived, ALLOWED_DERIVED_FROM_KEYS, label="derived_from")
        if not closed_der.is_valid:
            errors.append("UNKNOWN_DERIVED_FROM_ATTRIBUTE: %s" % closed_der.message)

    # 2. Strict Mock-Boundary Isolation
    mock_contract = scenario.get("mock_boundary_contract")
    if not isinstance(mock_contract, dict):
        errors.append("INVALID_MOCK_BOUNDARY_CONTRACT: mock_boundary_contract must be an object")
    else:
        closed_mc = check_closed_key_set(mock_contract, ALLOWED_MOCK_CONTRACT_KEYS, label="mock_boundary_contract")
        if not closed_mc.is_valid:
            errors.append("UNKNOWN_MOCK_CONTRACT_ATTRIBUTE: %s" % closed_mc.message)

        mode = mock_contract.get("isolation_mode")
        if mode != "STRICT_EXTERNAL_MOCK_ONLY":
            errors.append("INVALID_ISOLATION_MODE: isolation_mode must be STRICT_EXTERNAL_MOCK_ONLY")

        # Check entity names in lexicon to detect internal unit mocking
        lexicon_entities = set()
        if lexicon and isinstance(lexicon, dict):
            for e in lexicon.get("entities", []) or []:
                if isinstance(e, dict) and e.get("name"):
                    lexicon_entities.add(str(e["name"]).lower())
            for vo in lexicon.get("value_objects", []) or []:
                if isinstance(vo, dict) and vo.get("name"):
                    lexicon_entities.add(str(vo["name"]).lower())

        prohibited = [str(p).lower() for p in (mock_contract.get("prohibited_mocks") or [])]
        mock_boundaries = mock_contract.get("mock_boundaries", [])
        if not isinstance(mock_boundaries, list):
            errors.append("INVALID_MOCK_BOUNDARIES: mock_boundaries must be an array")
            mock_boundaries = []

        manifest_present_boundaries = _extract_present_boundary_identifiers(manifest)

        for mb in mock_boundaries:
            if not isinstance(mb, dict):
                errors.append("INVALID_MOCK_BOUNDARY_ITEM: mock boundary must be an object")
                continue
            closed_mb = check_closed_key_set(mb, ALLOWED_MOCK_BOUNDARY_KEYS, label="mock_boundary")
            if not closed_mb.is_valid:
                errors.append("UNKNOWN_MOCK_BOUNDARY_ATTRIBUTE: %s" % closed_mb.message)

            b_id = str(mb.get("boundary_identifier") or "").strip()
            b_class = str(mb.get("boundary_class") or "").strip()
            is_ext = mb.get("is_external")

            # Check for forbidden internal mocking
            b_id_lower = b_id.lower()
            if is_ext is not True:
                errors.append("FORBIDDEN_INTERNAL_MOCK: boundary %r is not marked external (is_external must be true)" % b_id)

            if any(p in b_id_lower for p in PROHIBITED_INTERNAL_PATTERNS) or b_id_lower in lexicon_entities or b_id_lower in prohibited:
                errors.append("FORBIDDEN_INTERNAL_MOCK: internal domain unit %r is prohibited from being mocked" % b_id)

            if b_class not in CANONICAL_BOUNDARY_CLASSES:
                errors.append("UNKNOWN_BOUNDARY_CLASS: %r is not a canonical boundary class" % b_class)

            # Check boundary manifest declaration if manifest provided
            if manifest is not None:
                if manifest_present_boundaries:
                    matched = (
                        b_id in manifest_present_boundaries
                        or any(b_id == pb for pb in manifest_present_boundaries)
                    )
                    if not matched:
                        errors.append("UNVERIFIED_MOCK_BOUNDARY: boundary %r is not declared PRESENT in boundary-manifest.json" % b_id)
                else:
                    errors.append("UNVERIFIED_MOCK_BOUNDARY: boundary-manifest.json declares no PRESENT boundaries, cannot mock %r" % b_id)


            # Check fixture replay
            fref = mb.get("fixture_ref")
            if not isinstance(fref, dict):
                errors.append("UNVERIFIED_FIXTURE_REPLAY: mock boundary %r missing fixture_ref" % b_id)
            else:
                closed_fref = check_closed_key_set(fref, ALLOWED_FIXTURE_REF_KEYS, label="fixture_ref")
                if not closed_fref.is_valid:
                    errors.append("UNKNOWN_FIXTURE_REF_ATTRIBUTE: %s" % closed_fref.message)
                sample_id = fref.get("sample_id")
                fpath = fref.get("fixture_path")
                fstatus = fref.get("fixture_status")
                if not sample_id or not fpath:
                    errors.append("UNVERIFIED_FIXTURE_REPLAY: mock boundary %r fixture_ref must declare sample_id and fixture_path" % b_id)
                if fstatus not in ("PROBED_LIVE", "REPLAYED_FROM_RECORDING", "PROVISIONAL_FROM_SCHEMA"):
                    errors.append("UNKNOWN_FIXTURE_STATUS: %r in fixture_ref for %r" % (fstatus, b_id))

                # If fixtures map is provided, verify concrete sample exists
                if fixtures_by_boundary and b_id in fixtures_by_boundary:
                    fix_data = fixtures_by_boundary[b_id]
                    samples = {
                        s.get("sample_id")
                        for s in (fix_data.get("success_samples") or []) + (fix_data.get("failure_envelope_census") or [])
                        if isinstance(s, dict) and s.get("sample_id")
                    }
                    if samples and sample_id not in samples:
                        errors.append("UNVERIFIED_FIXTURE_REPLAY: sample_id %r not found in recorded contract fixture for %r" % (sample_id, b_id))

    # 3. Given section validation
    given = scenario.get("given")
    if not isinstance(given, dict):
        errors.append("INVALID_GIVEN_CLAUSE: given must be an object")
    else:
        closed_g = check_closed_key_set(given, ALLOWED_GIVEN_KEYS, label="given")
        if not closed_g.is_valid:
            errors.append("UNKNOWN_GIVEN_ATTRIBUTE: %s" % closed_g.message)

        given_state_token = str(given.get("state_token") or "").strip()
        if not given_state_token:
            errors.append("MISSING_STATE_TOKEN: given.state_token is required")

    # 4. When section validation (Production Entrypoint)
    when = scenario.get("when")
    if not isinstance(when, dict):
        errors.append("INVALID_WHEN_CLAUSE: when must be an object")
    else:
        closed_w = check_closed_key_set(when, ALLOWED_WHEN_KEYS, label="when")
        if not closed_w.is_valid:
            errors.append("UNKNOWN_WHEN_ATTRIBUTE: %s" % closed_w.message)

        ep = when.get("entrypoint")
        if not isinstance(ep, dict):
            errors.append("MISSING_PRODUCTION_ENTRYPOINT: when.entrypoint is required")
        else:
            closed_ep = check_closed_key_set(ep, ALLOWED_ENTRYPOINT_KEYS, label="entrypoint")
            if not closed_ep.is_valid:
                errors.append("UNKNOWN_ENTRYPOINT_ATTRIBUTE: %s" % closed_ep.message)

            ep_type = ep.get("entrypoint_type")
            target = ep.get("invoked_target")
            actor_id = ep.get("actor_id")
            action_tok = ep.get("action_token")

            if ep_type not in ("CLI_COMMAND", "HTTP_HANDLER", "MESSAGE_CONSUMER", "FUNCTION_CALL"):
                errors.append("INVALID_ENTRYPOINT_TYPE: %r must be CLI_COMMAND, HTTP_HANDLER, MESSAGE_CONSUMER, or FUNCTION_CALL" % ep_type)
            if not target:
                errors.append("MISSING_PRODUCTION_ENTRYPOINT: entrypoint.invoked_target is empty")
            if not actor_id:
                errors.append("MISSING_ACTOR_ID: entrypoint.actor_id is required")
            if not action_tok:
                errors.append("MISSING_ACTION_TOKEN: entrypoint.action_token is required")

    # 5. Then section validation
    then = scenario.get("then")
    if not isinstance(then, dict):
        errors.append("INVALID_THEN_CLAUSE: then must be an object")
    else:
        closed_t = check_closed_key_set(then, ALLOWED_THEN_KEYS, label="then")
        if not closed_t.is_valid:
            errors.append("UNKNOWN_THEN_ATTRIBUTE: %s" % closed_t.message)

        then_state_token = str(then.get("state_token") or "").strip()
        if not then_state_token:
            errors.append("MISSING_STATE_TOKEN: then.state_token is required")

        assertions = then.get("state_assertions", [])
        if not isinstance(assertions, list):
            errors.append("INVALID_STATE_ASSERTIONS: then.state_assertions must be an array")
        else:
            for sa in assertions:
                if not isinstance(sa, dict):
                    errors.append("INVALID_STATE_ASSERTION_ITEM: state assertion must be an object")
                    continue
                closed_sa = check_closed_key_set(sa, ALLOWED_STATE_ASSERTION_KEYS, label="state_assertion")
                if not closed_sa.is_valid:
                    errors.append("UNKNOWN_STATE_ASSERTION_ATTRIBUTE: %s" % closed_sa.message)

        projections = then.get("observer_projections", [])
        if not isinstance(projections, list):
            errors.append("INVALID_OBSERVER_PROJECTIONS: then.observer_projections must be an array")
        else:
            for op in projections:
                if not isinstance(op, dict):
                    errors.append("INVALID_OBSERVER_PROJECTION_ITEM: observer projection must be an object")
                    continue
                closed_op = check_closed_key_set(op, ALLOWED_OBSERVER_PROJECTION_KEYS, label="observer_projection")
                if not closed_op.is_valid:
                    errors.append("UNKNOWN_OBSERVER_PROJECTION_ATTRIBUTE: %s" % closed_op.message)

    # 6. Upstream Interface Discovery Validations
    declared_states = _extract_declared_states(scenario_matrix, lexicon)
    if declared_states:
        if given and isinstance(given, dict) and given.get("state_token"):
            st = str(given["state_token"])
            if st not in declared_states:
                errors.append("UNDEFINED_STATE_TOKEN: initial state_token %r is not declared in upstream simulation or lexicon" % st)
        if then and isinstance(then, dict) and then.get("state_token"):
            st = str(then["state_token"])
            if st not in declared_states:
                errors.append("UNDEFINED_STATE_TOKEN: resulting state_token %r is not declared in upstream simulation or lexicon" % st)

    declared_actors, declared_actions = _extract_declared_actions_and_actors(scenario_matrix, lexicon)
    if when and isinstance(when, dict) and isinstance(when.get("entrypoint"), dict):
        ep = when["entrypoint"]
        act_id = ep.get("actor_id")
        act_tok = ep.get("action_token")
        if declared_actors and act_id and act_id not in declared_actors:
            errors.append("UNDEFINED_ACTOR: actor %r is not declared in lexicon or scenario matrix" % act_id)
        if declared_actions and act_tok and act_tok not in declared_actions:
            errors.append("UNDEFINED_DOMAIN_ACTION: action_token %r is not declared in lexicon or scenario matrix" % act_tok)

    declared_quantities = _extract_declared_projection_quantities(lexicon)
    if declared_quantities and then and isinstance(then, dict):
        for op in then.get("observer_projections", []) or []:
            if isinstance(op, dict):
                for q in op.get("projection_quantities", []) or []:
                    if str(q) not in declared_quantities:
                        errors.append("UNBOUND_PROJECTION_QUANTITY: projection quantity %r is not bound in domain-lexicon" % q)

    return len(errors) == 0, errors


def render_bdd_scenario_markdown(scenario: Dict[str, Any]) -> str:
    """Render a clean, human-readable Gherkin-style markdown report of the scenario."""
    s_id = scenario.get("scenario_id", "untitled_scenario")
    desc = scenario.get("description", "")
    slice_id = scenario.get("slice_id", "")
    derived = scenario.get("derived_from", {})
    mock_contract = scenario.get("mock_boundary_contract", {})
    given = scenario.get("given", {})
    when = scenario.get("when", {})
    then = scenario.get("then", {})

    lines = [
        f"# BDD Scenario: {s_id}",
        "",
        f"**Slice Target:** `{slice_id}`  ",
        f"**Derived From State:** `{derived.get('state_id')}` | **Transition:** `{derived.get('transition_id')}` | **Branch Class:** `{derived.get('branch_coverage_class')}`  ",
        f"**Isolation Mode:** `{mock_contract.get('isolation_mode')}`  ",
        "",
        "## Narrative",
        f"> {desc}",
        "",
        "## Mock-Boundary Contract",
        "| Boundary Identifier | Boundary Class | Fixture Sample | Fixture Status | Isolation |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    mock_bounds = mock_contract.get("mock_boundaries", [])
    if not mock_bounds:
        lines.append("| *(None - Pure Real Execution)* | N/A | N/A | N/A | STRICT |")
    else:
        for mb in mock_bounds:
            fref = mb.get("fixture_ref", {})
            lines.append(
                f"| `{mb.get('boundary_identifier')}` | `{mb.get('boundary_class')}` | "
                f"`{fref.get('sample_id')}` (`{fref.get('fixture_path')}`) | `{fref.get('fixture_status')}` | EXTERNAL_ONLY |"
            )

    prohibited = mock_contract.get("prohibited_mocks", [])
    if prohibited:
        lines.extend([
            "",
            f"**Prohibited Mocks (Real Production Execution):** {', '.join(f'`{p}`' for p in prohibited)}"
        ])

    lines.extend([
        "",
        "## Specification",
        f"**Given** initial domain state is `{given.get('state_token')}`"
    ])

    for ent in given.get("entity_states", []) or []:
        lines.append(f"  - Entity `{ent.get('entity_name')}` (`{ent.get('entity_id')}`) with attributes `{json.dumps(ent.get('attributes', {}))}`")

    for fix in given.get("fixture_presets", []) or []:
        lines.append(f"  - Boundary preset `{fix.get('boundary_identifier')}` replaying `{fix.get('sample_id')}`")

    ep = when.get("entrypoint", {})
    lines.extend([
        "",
        f"**When** actor `{ep.get('actor_id')}` invokes production `{ep.get('entrypoint_type')}` entrypoint `{ep.get('invoked_target')}` (`{ep.get('action_token')}`)",
    ])
    if ep.get("payload"):
        lines.append(f"  - Payload: `{json.dumps(ep.get('payload'))}`")
    if ep.get("command_args"):
        lines.append(f"  - Arguments: `{json.dumps(ep.get('command_args'))}`")

    lines.extend([
        "",
        f"**Then** domain state transitions to `{then.get('state_token')}`",
    ])

    for sa in then.get("state_assertions", []) or []:
        lines.append(f"  - Assert `{sa.get('entity_name')}.{sa.get('attribute')}` {sa.get('operator')} `{sa.get('expected_value')}`")

    for op in then.get("observer_projections", []) or []:
        lines.append(f"  - Observer `{op.get('observer_id')}` perceives projection on `{op.get('medium')}` with discriminator `{op.get('discriminator')}`")

    for ev in then.get("event_emissions", []) or []:
        lines.append(f"  - Event `{ev.get('event_name')}` emitted to topic `{ev.get('topic_or_stream')}`")

    return "\n".join(lines) + "\n"


def compute_upstream_cache_digest(
    lexicon_path: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    scenario_matrix_path: Optional[Path] = None,
) -> str:
    """Compute content-addressed cache key from upstream stage artifacts."""
    hasher = hashlib.sha256()
    for p in (lexicon_path, manifest_path, scenario_matrix_path):
        if p and p.is_file():
            hasher.update(p.read_bytes())
        else:
            hasher.update(b"__ABSENT__")
    return hasher.hexdigest()
