"""The four-way Stage 4 regression router.

The spec gives Stage 4 four regression edges, not three:

    a broken invariant, or two states' projections fingerprinting alike -> STAGE_2
    an external interface schema that drifted                           -> STAGE_1
    a failure tracing to a boundary property no register row names      -> STAGE_1, as
                                                                          UNCLASSIFIED_BOUNDARY,
                                                                          recorded under OTHER
                                                                          with a register
                                                                          amendment raised
    a domain type or precision mismatch                                 -> STAGE_0

The fourth is the expensive one. The first three all assume the boundary was known; a boundary
that is present and unnamed is not drift, not a type mismatch and not an unmodelled branch, so
without its own cause code it lands as an ordinary implementation bug and the register never
learns.

Two things are deliberately not done here. The edges are not restated: the source of truth for
which cause may route where is the transition table in `bin/planner-state`, loaded and read at
runtime, and the state write itself is that tool's own rollback command. And no cause is read
from a field: each is computed by differencing a recorded failure against the artifact that
would have had to name it — the register for a boundary property, the lexicon for a unit
precision, the contract fixture's recorded envelope for drift.
"""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .normalisation import type_signature
from .primitives import check_closed_key_set, collect_tokens, is_reason_meaningful

STAGE_4_STATE = "STAGE_4_MILESTONE_EXECUTION"
UNCLASSIFIED_BOUNDARY_CAUSE = "UNCLASSIFIED_BOUNDARY"
UNCLASSIFIED_REGISTER_CLASS = "OTHER"

# What a recorded failure may say about itself. It records observations — which boundary, which
# property, what was observed — and never a cause: the cause is computed from them.
FAILURE_TRACE_KEYS = (
    "boundary_identifier",
    "boundary_property",
    "observed_response",
    "domain_term",
    "observed_unit",
    "observed_value",
    "broken_invariant",
    "description",
)

_ENVELOPE_KINDS = {
    type(None): "null",
    bool: "boolean",
    int: "number",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}


def load_planner_state(skill_root: Optional[Path] = None):
    """Load `bin/planner-state` as a module so its transition table is the single source.

    The file has no `.py` suffix because it is an executable, so it is loaded by path rather
    than imported by name. Its module body only defines the table and the commands; nothing runs
    until a command is called.
    """
    root = Path(skill_root) if skill_root else Path(__file__).resolve().parent.parent
    path = root / "bin" / "planner-state"
    spec = importlib.util.spec_from_loader(
        "planner_state", importlib.machinery.SourceFileLoader("planner_state", str(path))
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rollback_edges(from_state: str = STAGE_4_STATE, skill_root: Optional[Path] = None) -> Dict[str, List[str]]:
    """The rollback targets and their permitted causes, read from the transition table."""
    table = load_planner_state(skill_root).TRANSITION_TABLE
    if from_state not in table:
        return {}
    return dict(table[from_state].get("allowed_rollback_targets", {}))


def route_cause(cause: str, from_state: str = STAGE_4_STATE, skill_root: Optional[Path] = None) -> Optional[str]:
    """The stage a cause routes to, or None if the transition table does not permit it.

    The permitted causes are a closed set and the table defines it, so a cause outside it is
    refused here rather than silently defaulting to some stage.
    """
    edges = rollback_edges(from_state, skill_root)
    permitted = sorted({c for causes in edges.values() for c in causes})
    if not check_closed_key_set({str(cause): None}, permitted, label="rollback cause").is_valid:
        return None
    for target in sorted(edges):
        if cause in edges[target]:
            return target
    return None


# ---------------------------------------------------------------------------
# What the register, the lexicon and the fixtures already name
# ---------------------------------------------------------------------------

def register_property_index(manifest: Any) -> Dict[str, Set[str]]:
    """Map every boundary property the register names to the classes whose rows name it.

    A property absent from this map is one nothing probed because no row named it — which is
    the whole definition of an unclassified boundary. Verdict is not consulted: a row that
    names a property and reasons its absence has still named it, and that is a different
    failure from a property the register never mentions.
    """
    registers: List[Tuple[str, Set[str]]] = []
    rows = manifest.get("boundary_classes") if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        class_name = str(row.get("class_name") or "").strip()
        if not class_name:
            continue
        names: Set[str] = set()
        properties = row.get("properties")
        if isinstance(properties, list):
            for prop in properties:
                if isinstance(prop, dict):
                    name = str(prop.get("property_name") or "").strip()
                    if name:
                        names.add(name)
        registers.append((class_name, names))

    # Every class may legitimately name the same property, so overlaps are not collisions here;
    # what is wanted is the token -> naming-registers map the primitive builds.
    token_map, _overlaps = collect_tokens(
        registers, allowed_overlaps={(a, b) for a, _ in registers for b, _ in registers if a != b}
    )
    return token_map


def declared_unit_precision(lexicon: Any, domain_term: str) -> Optional[Dict[str, Any]]:
    """The unit precision the lexicon declares for a domain term, by attribute or by unit id."""
    if not isinstance(lexicon, dict) or not domain_term:
        return None
    precisions = {
        str(row.get("id")): row
        for row in lexicon.get("unit_precisions") or []
        if isinstance(row, dict) and row.get("id")
    }
    if domain_term in precisions:
        return precisions[domain_term]

    for group in ("entities", "value_objects"):
        for holder in lexicon.get(group) or []:
            if not isinstance(holder, dict):
                continue
            for attribute in holder.get("attributes") or []:
                if not isinstance(attribute, dict):
                    continue
                if str(attribute.get("name") or "") != domain_term:
                    continue
                unit_ref = str(attribute.get("unit_ref") or "")
                if unit_ref in precisions:
                    return precisions[unit_ref]
    return None


def precision_mismatch(precision: Dict[str, Any], trace: Dict[str, Any]) -> Optional[str]:
    """Compare an observed quantity against the precision the lexicon declares for its term."""
    declared_unit = str(precision.get("base_unit") or "").strip().lower()
    observed_unit = str(trace.get("observed_unit") or "").strip().lower()
    if declared_unit and observed_unit and declared_unit != observed_unit:
        return "declared base unit %r, observed %r" % (
            precision.get("base_unit"), trace.get("observed_unit"),
        )

    declared_type = str(precision.get("numeric_type") or "").strip().lower()
    if "observed_value" not in trace or not declared_type:
        return None
    observed = trace["observed_value"]
    if declared_type.startswith("int"):
        if isinstance(observed, bool):
            return "declared %s, observed a boolean" % precision.get("numeric_type")
        if isinstance(observed, float) and observed != int(observed):
            return "declared %s, observed the fractional value %r" % (
                precision.get("numeric_type"), observed,
            )
        if isinstance(observed, str):
            return "declared %s, observed the string %r" % (precision.get("numeric_type"), observed)
    elif declared_type == "string" and isinstance(observed, (int, float)) and not isinstance(observed, bool):
        return "declared a string representation, observed the number %r" % observed
    return None


def envelope_signature(payload: Any) -> str:
    """The bounded shape of a response envelope: its key set and each key's kind.

    Only the top level is compared, so variance inside an UNBOUNDED region — which the contract
    permits and the census records — does not read as the interface having drifted.
    """
    if not isinstance(payload, dict):
        return type_signature(payload)
    parts = []
    for key in sorted(payload):
        parts.append("%s:%s" % (key, _ENVELOPE_KINDS.get(type(payload[key]), "unknown")))
    return "{%s}" % ",".join(parts)


def recorded_envelopes(contract_fixture: Any) -> Set[str]:
    """Envelope signatures the contract fixture recorded, across samples and failure envelopes."""
    signatures: Set[str] = set()
    if not isinstance(contract_fixture, dict):
        return signatures
    for group in ("success_samples", "failure_envelope_census"):
        for sample in contract_fixture.get(group) or []:
            if isinstance(sample, dict):
                payload = sample.get("payload", sample.get("body"))
                if payload is not None:
                    signatures.add(envelope_signature(payload))
    return signatures


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class RollbackFinding(object):
    """One classified regression: its cause, the stage it routes to, and why."""

    def __init__(self, cause: str, detail: str, milestone_id: str = "", trace: Optional[Dict[str, Any]] = None):
        self.cause = cause
        self.detail = detail
        self.milestone_id = milestone_id
        self.trace = trace or {}

    def target_state(self, skill_root: Optional[Path] = None) -> Optional[str]:
        return route_cause(self.cause, STAGE_4_STATE, skill_root)


def classify_collision(observer_id: str, key_a: str, key_b: str, fingerprint: str) -> RollbackFinding:
    """A projection collision is a Stage 2 regression: the graph modelled two states as one."""
    return RollbackFinding(
        "FINGERPRINT_COLLISION",
        "evidence keys %r and %r fingerprint alike (%s) for observer %r"
        % (key_a, key_b, fingerprint[:16], observer_id),
    )


def classify_failure(
    milestone_id: str,
    trace: Any,
    manifest: Any,
    lexicon: Any,
    contract_fixtures_by_boundary: Dict[str, Any],
) -> Tuple[Optional[RollbackFinding], List[str]]:
    """Classify one recorded failure by differencing it against the artifacts that name things.

    Precedence, and the reason for it:

      1. a boundary property no register row names is UNCLASSIFIED_BOUNDARY — checked first,
         because a property that was never named cannot have drifted from anything;
      2. a domain term whose observed quantity contradicts the lexicon's declared precision is
         a Stage 0 failure — checked before drift, because a precision mismatch also shows up
         as a shape difference and would otherwise be misfiled as drift;
      3. an observed envelope matching none the contract fixture recorded is CONTRACT_DRIFT;
      4. a named broken slice invariant is a Stage 2 regression.
    """
    problems: List[str] = []
    if not isinstance(trace, dict):
        return None, ["a recorded failure carries no trace of what it touched"]

    closed = check_closed_key_set(trace, FAILURE_TRACE_KEYS, label="failure trace of %s" % milestone_id)
    if not closed.is_valid:
        return None, ["UNKNOWN_FAILURE_TRACE_ATTRIBUTE: %s" % closed.message]

    if not is_reason_meaningful(trace.get("description")):
        problems.append(
            "UNREASONED_FAILURE_TRACE: the failure of %s records no description of what was observed"
            % milestone_id
        )

    boundary_property = str(trace.get("boundary_property") or "").strip()
    if boundary_property:
        named_by = register_property_index(manifest).get(boundary_property)
        if not named_by:
            return RollbackFinding(
                UNCLASSIFIED_BOUNDARY_CAUSE,
                "the failure of %s traces to boundary property %r at %r, which no register row names, "
                "so nothing probed it" % (
                    milestone_id, boundary_property,
                    str(trace.get("boundary_identifier") or "an unnamed boundary"),
                ),
                milestone_id,
                trace,
            ), problems

    domain_term = str(trace.get("domain_term") or "").strip()
    if domain_term:
        precision = declared_unit_precision(lexicon, domain_term)
        if precision is not None:
            mismatch = precision_mismatch(precision, trace)
            if mismatch:
                return RollbackFinding(
                    "DOMAIN_TYPE_PRECISION_MISMATCH",
                    "the failure of %s traces to domain term %r: %s"
                    % (milestone_id, domain_term, mismatch),
                    milestone_id,
                    trace,
                ), problems

    boundary_identifier = str(trace.get("boundary_identifier") or "").strip()
    if boundary_identifier and "observed_response" in trace:
        fixture = contract_fixtures_by_boundary.get(boundary_identifier)
        recorded = recorded_envelopes(fixture)
        observed = envelope_signature(trace["observed_response"])
        if recorded and observed not in recorded:
            return RollbackFinding(
                "CONTRACT_DRIFT",
                "the failure of %s observed envelope %s at %r, which matches none the contract "
                "fixture recorded (%s)" % (
                    milestone_id, observed, boundary_identifier, ", ".join(sorted(recorded)),
                ),
                milestone_id,
                trace,
            ), problems

    broken_invariant = str(trace.get("broken_invariant") or "").strip()
    if broken_invariant:
        return RollbackFinding(
            "BROKEN_INVARIANT",
            "the failure of %s broke slice invariant %r" % (milestone_id, broken_invariant),
            milestone_id,
            trace,
        ), problems

    problems.append(
        "UNROUTABLE_FAILURE: the failure of %s names no boundary property, domain term, observed "
        "envelope or broken invariant, so no regression edge can be computed for it" % milestone_id
    )
    return None, problems


# ---------------------------------------------------------------------------
# Executing the rollback
# ---------------------------------------------------------------------------

def record_unclassified_boundary(
    manifest_path: Path,
    manifest: Dict[str, Any],
    finding: RollbackFinding,
    timestamp: str,
) -> str:
    """Record the unnamed boundary under OTHER and raise a register amendment.

    This is the half of the fourth edge that makes the pipeline learn: without the row and the
    amendment, the next feature repeats the same blind spot.
    """
    rows = manifest.setdefault("boundary_classes", [])
    trace = finding.trace
    boundary = str(trace.get("boundary_identifier") or "an unnamed boundary")
    boundary_property = str(trace.get("boundary_property") or "")
    amendment = {
        "amendment_id": "AMEND_%s" % boundary_property.upper().replace(" ", "_"),
        "raised_at": timestamp,
        "boundary_identifier": boundary,
        "boundary_property": boundary_property,
        "raised_because": finding.detail,
        "proposed_register_change": (
            "add %r as a probed property of the boundary class that owns %r, so a later feature "
            "probes it instead of meeting it at run time" % (boundary_property, boundary)
        ),
    }

    other_row = None
    for row in rows:
        if isinstance(row, dict) and str(row.get("class_name") or "") == UNCLASSIFIED_REGISTER_CLASS:
            other_row = row
            break
    if other_row is None:
        other_row = {"class_name": UNCLASSIFIED_REGISTER_CLASS}
        rows.append(other_row)

    other_row["verdict"] = "PRESENT"
    other_row.pop("absence_reason", None)
    properties = other_row.setdefault("properties", [])
    if not any(
        isinstance(p, dict) and str(p.get("property_name") or "") == boundary_property
        for p in properties
    ):
        properties.append({
            "property_name": boundary_property,
            "verdict": "NOT_PROBED",
            "boundary_identifier": boundary,
            "recorded_because": finding.detail,
        })
    manifest.setdefault("register_amendments", []).append(amendment)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return amendment["amendment_id"]


def execute_rollback(
    feature_dir: Path,
    feature_slug: str,
    cause: str,
    skill_root: Optional[Path] = None,
) -> Tuple[int, Optional[str]]:
    """Perform the state transition through `bin/planner-state`'s own rollback command.

    The target, the permitted causes and the invalidation of downstream artifacts all live in
    that tool. Reimplementing any of them here would give the pipeline two state machines.
    """
    target = route_cause(cause, STAGE_4_STATE, skill_root)
    if target is None:
        return 1, None
    planner_state = load_planner_state(skill_root)
    args = SimpleNamespace(
        feature=feature_slug,
        dir=str(feature_dir),
        root=None,
        from_state=STAGE_4_STATE,
        to_state=target,
        cause=cause,
        dry_run=False,
    )
    return planner_state.cmd_rollback(args), target
