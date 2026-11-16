"""Ground-truth reconciliation for Stage 5 delivery evidence.

Owner: stage-5.

`lib/arithmetic.py` proves a captured artifact is internally consistent: an aggregate node equals
the sum of the part nodes beside it. That is necessary and not sufficient — an author or an agent
can fabricate both the aggregate and its parts in the same breath, and internal consistency alone
cannot tell a fabricated pair from a true one. This module closes that specific gap: it resolves
each numeric value a value_authority governs back to the source Stage 0 declared for it, and
compares the rendered figure against the evidence the pipeline already recorded for that source —

  - UPSTREAM_AUTHORITATIVE: the authority is the recorded contract fixture for the
    value_authority's named `upstream_boundary` (an `entity_boundaries` entry's `boundary_id`,
    resolved through its interface's `contract_ref` to a `contract-fixture.schema.json` file).
  - LOCAL_AUTHORITATIVE: the authority is the declared `local_computation`, and it is only
    answerable to ground truth when the values it computes over are themselves declared under a
    traceable authority. A locally consistent sum over untraceable parts is not evidence of
    anything; it is refused rather than accepted as if it were.

A word-valued (string-enum) authority is reconciled by exact string match instead: the rendered
text must be one of the declared value set (the attribute's state enum tokens or its
`allowed_values`), and must equal a recorded authoritative value when one is recorded.

Every branch that cannot finish the reconciliation — a rendered value that resolves to no
declared entity attribute, a local computation whose inputs carry no authority of their own, a
named boundary no `entity_boundaries` entry declares, a contract fixture file that is absent or
unreadable, or a contract fixture that carries no sample covering the capture's scope — is
reported as a named refusal. None of those conditions returns a clean pass; see SURROGATE-
VERIFICATION.md §4 for why a check that can be satisfied by its own absence of data is not a gate.

What this module does not do (see tests/cases/surrogate-verification/README.md for the ledger):
it does not fetch a live boundary itself, so a contract fixture recorded once and never reprobed
still passes even if the live boundary has since drifted; and for LOCAL_AUTHORITATIVE values it
verifies the *inputs* are declared under some traceable authority, not that each individual part
value itself reconciles against its own upstream sample — that would require per-part scope
resolution this round does not build.
"""

from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .arithmetic import (
    ROUNDING_MODES,
    _filter_top_level_nodes,
    _find_unit_precision_for_binding,
    _matches_binding,
    extract_numeric_value,
    load_domain_lexicon,
    parse_dom,
    parse_precision_decimals,
)

RECONCILED_AUTHORITY_MODELS = ("UPSTREAM_AUTHORITATIVE", "LOCAL_AUTHORITATIVE")
_LOCAL_COMP_PATTERN = re.compile(r"^(sum|count|avg)\((.+)\)$", re.IGNORECASE)


def _entity_and_attribute(source_reference: Any) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(source_reference, str) or "." not in source_reference:
        return None, None
    entity, _dot, attribute = source_reference.partition(".")
    entity, attribute = entity.strip(), attribute.strip()
    if not entity or not attribute:
        return None, None
    return entity, attribute


def _find_entity(lexicon: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for entity in lexicon.get("entities") or []:
        if isinstance(entity, dict) and entity.get("name") == name:
            return entity
    return None


def _find_attribute(entity: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for attribute in entity.get("attributes") or []:
        if isinstance(attribute, dict) and attribute.get("name") == name:
            return attribute
    return None


def _find_allowed_word_values(
    lexicon: Dict[str, Any], attribute: Optional[Dict[str, Any]]
) -> Optional[List[str]]:
    """Allowed word tokens declared in the domain lexicon for this attribute.

    The declared value set comes from the state enum the attribute names — through `enum_ref`,
    or through a `type` that is itself a declared state enum's name (`"type": "SessionState"`)
    — or from the attribute's own `allowed_values`. A rendered word is reconciled against that
    set by exact string match; nothing here parses it as a number.
    """
    if not attribute:
        return None
    state_enums = [se for se in (lexicon.get("state_enums") or []) if isinstance(se, dict)]
    for enum_name in (attribute.get("enum_ref"), attribute.get("type")):
        if not enum_name:
            continue
        for se in state_enums:
            if se.get("name") == enum_name:
                return [str(t) for t in (se.get("tokens") or [])]
    if isinstance(attribute.get("allowed_values"), list):
        return [str(v) for v in attribute["allowed_values"]]
    return None


def _find_recorded_authoritative_value(
    envelope: Dict[str, Any], va: Dict[str, Any], pb: Dict[str, Any]
) -> Optional[str]:
    """An authoritative value recorded alongside the capture envelope."""
    vname, pid = va.get("value_name"), pb.get("projection_id")
    for vb in envelope.get("value_bindings") or []:
        if isinstance(vb, dict) and (vb.get("projection_binding") in (pid, vname) or vb.get("domain_term") in (vname, pid)):
            if "asserted_value" in vb and vb["asserted_value"] is not None:
                return str(vb["asserted_value"]).strip()
    return None


def _find_projection_binding_for_authority(
    va: Dict[str, Any], projection_bindings: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """The projection_binding a value_authority governs.

    Matched by exact equality against `value_name`, on either the binding's own `projection_id`
    or its `source_reference` — the two conventions this build's fixtures already use. Equality
    rather than fuzzy overlap is deliberate: a loose match would risk pulling in a value_authority
    this round never meant to engage for a capture that happens to render something similarly
    named.
    """
    vname = va.get("value_name")
    if not vname:
        return None
    for pb in projection_bindings:
        if pb.get("projection_id") == vname or pb.get("source_reference") == vname:
            return pb
    return None


def _find_rendered_node(root: Any, binding: Dict[str, Any]) -> Optional[Any]:
    matches = _filter_top_level_nodes(root.find_all(lambda n: _matches_binding(n, binding)))
    return matches[0] if matches else None


def _entity_boundaries_by_id(lexicon: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for boundary in lexicon.get("entity_boundaries") or []:
        if isinstance(boundary, dict) and boundary.get("boundary_id"):
            out[boundary["boundary_id"]] = boundary
    return out


def _load_contract_fixture(
    boundary: Dict[str, Any], lexicon_dir: Optional[Path]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The contract-fixture.schema.json document a boundary's interface names, or why not."""
    ref = None
    for interface in boundary.get("interfaces") or []:
        if isinstance(interface, dict) and interface.get("contract_ref"):
            ref = interface["contract_ref"]
            break
    boundary_id = boundary.get("boundary_id")
    if not ref:
        return None, (
            "boundary %r declares no interface with a contract_ref, so there is no recorded "
            "contract fixture to reconcile against" % boundary_id
        )
    candidate = (lexicon_dir / ref) if lexicon_dir is not None else Path(ref)
    if not candidate.is_file():
        return None, "contract fixture %s for boundary %r does not exist on disk" % (
            candidate, boundary_id,
        )
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception as e:
        return None, "contract fixture %s is not valid JSON: %s" % (candidate, e)
    if not isinstance(data, dict):
        return None, "contract fixture %s carries no fixture object" % candidate
    return data, None


def _capture_scope(
    lexicon: Dict[str, Any],
    projection_bindings: List[Dict[str, Any]],
    root: Any,
    governed_value_names: set,
    entity_name: str,
    exclude_attr: str,
) -> Dict[str, str]:
    """The identifying facts rendered alongside the value under audit, keyed by attribute name.

    Only sibling attributes of the same entity that are NOT themselves under a value_authority are
    scope candidates — an attribute the pipeline already treats as a measured fact (rather than an
    identifying key like a month or an account id) is not a stable way to select which upstream
    sample this capture claims to be.
    """
    entity = _find_entity(lexicon, entity_name)
    if entity is None:
        return {}
    scope: Dict[str, str] = {}
    for attribute in entity.get("attributes") or []:
        if not isinstance(attribute, dict):
            continue
        attr_name = attribute.get("name")
        if not attr_name or attr_name == exclude_attr:
            continue
        reference = "%s.%s" % (entity_name, attr_name)
        if reference in governed_value_names:
            continue
        pb = None
        for candidate in projection_bindings:
            if candidate.get("source_reference") == reference:
                pb = candidate
                break
        if pb is None:
            continue
        node = _find_rendered_node(root, pb)
        if node is None:
            continue
        text = node.all_text.strip()
        if text:
            scope[attr_name] = text
    return scope


def _sample_covering_scope(fixture: Dict[str, Any], scope: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """The recorded sample whose partition_cell/payload matches every scope fact rendered.

    An empty scope resolves only when the fixture is unambiguous on its own — exactly one
    recorded sample. With more than one sample and nothing rendered to pick between them, the
    match is undefined and this returns None rather than guessing the first one.
    """
    samples = [s for s in (fixture.get("success_samples") or []) if isinstance(s, dict)]
    if not samples:
        return None
    if not scope:
        return samples[0] if len(samples) == 1 else None
    for sample in samples:
        payload = sample.get("payload") if isinstance(sample.get("payload"), dict) else {}
        cell = sample.get("partition_cell") if isinstance(sample.get("partition_cell"), dict) else {}
        covers = True
        for key, rendered_text in scope.items():
            candidate = cell.get(key, payload.get(key))
            if candidate is None or str(candidate) not in rendered_text:
                covers = False
                break
        if covers:
            return sample
    return None


def _check_local_authoritative(va: Dict[str, Any], node: Any, lexicon: Dict[str, Any]) -> List[str]:
    """A LOCAL_AUTHORITATIVE value answers to ground truth only if its inputs do.

    This does not itself fetch the parts' own upstream sample — only that each part is declared
    under a model this reconciliation (or a future round of it) can trace at all. See the module
    docstring's honesty note.
    """
    comp = (va.get("local_computation") or "").strip()
    match = _LOCAL_COMP_PATTERN.match(comp)
    if not match:
        return [
            "GROUND_TRUTH_UNTRACEABLE: value authority %r is LOCAL_AUTHORITATIVE but declares no "
            "computable local_computation, so <%s> has no ground truth to answer to"
            % (va.get("value_name"), node.describe())
        ]
    func_name, inner_target = match.group(1).lower(), match.group(2).strip()
    if func_name != "sum":
        return [
            "GROUND_TRUTH_UNTRACEABLE: value authority %r computes %r, which this reconciliation "
            "does not know how to trace upstream" % (va.get("value_name"), comp)
        ]
    part_va = None
    for other in lexicon.get("value_authorities") or []:
        if isinstance(other, dict) and other.get("value_name") == inner_target:
            part_va = other
            break
    if part_va is None:
        return [
            "GROUND_TRUTH_UNTRACEABLE: value authority %r declares local_computation %r, but %r "
            "carries no value_authority of its own, so the computed total in <%s> cannot be traced "
            "to any recorded ground truth" % (va.get("value_name"), comp, inner_target, node.describe())
        ]
    if part_va.get("authority_model") not in RECONCILED_AUTHORITY_MODELS:
        return [
            "GROUND_TRUTH_UNTRACEABLE: %r is declared %r, which is not a traceable authority "
            "model, so the computed total in <%s> cannot be trusted"
            % (inner_target, part_va.get("authority_model"), node.describe())
        ]
    return []


def verify_ground_truth_reconciliation(
    report: Dict[str, Any],
    planned: Dict[str, Any],
    verified: Sequence[Any],
    base_dir: Optional[Path] = None,
) -> List[str]:
    """Reconcile every UPSTREAM_/LOCAL_AUTHORITATIVE value rendered in a capture against ground truth."""
    lexicon, lexicon_dir, err = load_domain_lexicon(base_dir, report.get("feature_slug"))
    if lexicon is None:
        return [
            "MISSING_GATE_ARTIFACT: domain-lexicon.json could not be loaded for ground-truth "
            "reconciliation (%s)" % err
        ]

    value_authorities = [va for va in (lexicon.get("value_authorities") or []) if isinstance(va, dict)]
    projection_bindings = [pb for pb in (lexicon.get("projection_bindings") or []) if isinstance(pb, dict)]
    governed_value_names = {va.get("value_name") for va in value_authorities if va.get("value_name")}
    boundaries = _entity_boundaries_by_id(lexicon)

    problems: List[str] = []
    for evidence in verified:
        envelope = getattr(evidence, "envelope", None)
        if envelope is None and isinstance(evidence, dict):
            envelope = evidence.get("envelope")
        if envelope is None:
            continue
        artifact = envelope.get("captured_artifact")
        if not isinstance(artifact, str):
            continue
        root = parse_dom(artifact)

        for va in value_authorities:
            model = va.get("authority_model")
            if model not in RECONCILED_AUTHORITY_MODELS:
                continue
            pb = _find_projection_binding_for_authority(va, projection_bindings)
            if pb is None:
                continue
            node = _find_rendered_node(root, pb)
            if node is None:
                continue

            entity_name, attr_name = _entity_and_attribute(pb.get("source_reference"))
            entity = _find_entity(lexicon, entity_name) if entity_name else None
            attribute = _find_attribute(entity, attr_name) if entity else None
            if entity is None or attribute is None:
                problems.append(
                    "GROUND_TRUTH_UNTRACEABLE: value authority %r binds to source_reference %r, "
                    "which names no declared entity attribute in domain-lexicon.json"
                    % (va.get("value_name"), pb.get("source_reference"))
                )
                continue

            allowed_values = _find_allowed_word_values(lexicon, attribute)
            recorded_auth = _find_recorded_authoritative_value(envelope, va, pb)
            atype = str(attribute.get("type") or "").lower()
            is_word = allowed_values is not None or atype in ("enum", "string", "str")

            if is_word:
                rendered_text = node.all_text.strip()
                if not rendered_text:
                    problems.append(
                        "GROUND_TRUTH_UNTRACEABLE: %s <%s> carries no parseable word value to "
                        "reconcile against %r's declared authority"
                        % (pb.get("projection_id"), node.describe(), va.get("value_name"))
                    )
                    continue

                if allowed_values is not None and rendered_text not in allowed_values:
                    problems.append(
                        "GROUND_TRUTH_CONTRADICTED: %s <%s> reports %r for %r, which is outside "
                        "the declared allowed values %s"
                        % (
                            pb.get("projection_id"),
                            node.describe(),
                            rendered_text,
                            va.get("value_name"),
                            sorted(allowed_values),
                        )
                    )
                    continue

                if model == "LOCAL_AUTHORITATIVE":
                    if allowed_values is None and recorded_auth is None:
                        problems.append(
                            "GROUND_TRUTH_UNTRACEABLE: value authority %r carries word value %r, "
                            "but no ground truth is declared or recorded to reconcile against"
                            % (va.get("value_name"), rendered_text)
                        )
                        continue
                    if recorded_auth is not None and rendered_text != recorded_auth:
                        problems.append(
                            "GROUND_TRUTH_CONTRADICTED: %s <%s> reports %r for %r, but the recorded "
                            "authoritative value is %r"
                            % (
                                pb.get("projection_id"),
                                node.describe(),
                                rendered_text,
                                va.get("value_name"),
                                recorded_auth,
                            )
                        )
                        continue
                    continue

            rendered_val = extract_numeric_value(node.all_text)
            if rendered_val is None:
                problems.append(
                    "GROUND_TRUTH_UNTRACEABLE: %s <%s> carries no parseable numeric value to "
                    "reconcile against %r's declared authority"
                    % (pb.get("projection_id"), node.describe(), va.get("value_name"))
                )
                continue

            if model == "LOCAL_AUTHORITATIVE":
                problems.extend(_check_local_authoritative(va, node, lexicon))
                continue

            # UPSTREAM_AUTHORITATIVE from here down.
            boundary_id = va.get("upstream_boundary")
            if not boundary_id:
                problems.append(
                    "GROUND_TRUTH_UNTRACEABLE: value authority %r is UPSTREAM_AUTHORITATIVE but "
                    "names no upstream_boundary, so <%s> cannot be reconciled against anything"
                    % (va.get("value_name"), node.describe())
                )
                continue
            boundary = boundaries.get(boundary_id)
            if boundary is None:
                problems.append(
                    "MISSING_GATE_ARTIFACT: value authority %r names upstream_boundary %r, which "
                    "no entity_boundaries entry in domain-lexicon.json declares"
                    % (va.get("value_name"), boundary_id)
                )
                continue
            fixture, fixture_error = _load_contract_fixture(boundary, lexicon_dir)
            if fixture is None:
                problems.append("MISSING_GATE_ARTIFACT: %s" % fixture_error)
                continue

            scope = _capture_scope(
                lexicon, projection_bindings, root, governed_value_names, entity_name, attr_name
            )
            sample = _sample_covering_scope(fixture, scope)
            if sample is None:
                problems.append(
                    "GROUND_TRUTH_SCOPE_UNCOVERED: the contract fixture for boundary %r records no "
                    "sample covering this capture's scope (%s), so %r cannot be reconciled"
                    % (boundary_id, scope or "no identifying attribute rendered alongside it", va.get("value_name"))
                )
                continue

            payload = sample.get("payload") if isinstance(sample.get("payload"), dict) else {}
            cell = sample.get("partition_cell") if isinstance(sample.get("partition_cell"), dict) else {}
            raw_authoritative = payload.get(attr_name, cell.get(attr_name))
            if raw_authoritative is None:
                problems.append(
                    "GROUND_TRUTH_UNTRACEABLE: the contract fixture sample %r for boundary %r "
                    "records no value for %r" % (sample.get("sample_id"), boundary_id, attr_name)
                )
                continue

            if is_word:
                authoritative_str = str(raw_authoritative).strip()
                if rendered_text != authoritative_str:
                    problems.append(
                        "GROUND_TRUTH_CONTRADICTED: %s <%s> reports %r for %r, but the recorded "
                        "contract fixture (sample %r, boundary %r) records %r"
                        % (
                            pb.get("projection_id"),
                            node.describe(),
                            rendered_text,
                            va.get("value_name"),
                            sample.get("sample_id"),
                            boundary_id,
                            authoritative_str,
                        )
                    )
                continue

            authoritative_val = (
                extract_numeric_value(str(raw_authoritative)) if raw_authoritative is not None else None
            )
            if authoritative_val is None:
                problems.append(
                    "GROUND_TRUTH_UNTRACEABLE: the contract fixture sample %r for boundary %r "
                    "records no value for %r" % (sample.get("sample_id"), boundary_id, attr_name)
                )
                continue

            up = _find_unit_precision_for_binding(lexicon, pb, va)
            decimals = parse_precision_decimals(up.get("representation") if up else None)
            mode_str = (up.get("rounding_mode") if up else "ROUND_HALF_UP") or "ROUND_HALF_UP"
            rounding = ROUNDING_MODES.get(mode_str, ROUND_HALF_UP)
            quantizer = Decimal("1." + "0" * decimals) if decimals > 0 else Decimal("1")
            epsilon = Decimal(str(va.get("tolerance_epsilon") or "0.001"))

            rounded_rendered = rendered_val.quantize(quantizer, rounding=rounding)
            rounded_authoritative = authoritative_val.quantize(quantizer, rounding=rounding)
            diff = abs(rounded_rendered - rounded_authoritative)
            if diff > epsilon:
                problems.append(
                    "GROUND_TRUTH_CONTRADICTED: %s <%s> reports %s for %r, but the recorded "
                    "contract fixture (sample %r, boundary %r) records %s (discrepancy %s > "
                    "tolerance %s under %s)"
                    % (
                        pb.get("projection_id"), node.describe(), rounded_rendered, va.get("value_name"),
                        sample.get("sample_id"), boundary_id, rounded_authoritative, diff, epsilon, mode_str,
                    )
                )

    return problems
