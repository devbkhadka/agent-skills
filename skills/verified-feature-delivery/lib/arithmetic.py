"""Arithmetic consistency verification for Stage 5 delivery evidence.

Owner: stage-5.
Verifies that captured artifacts (such as rendered_ui DOM trees or structured payloads)
maintain arithmetic consistency between declared aggregate values and the sum of their
contained parts, evaluated using Stage 0 declarations:
  - value_authorities: local_computation strings (e.g. sum(TicketWorklog.seconds)), tolerance_epsilon
  - unit_precisions: quantity_type, representation, and rounding_mode (e.g. ROUND_HALF_UP)
  - projection_bindings: bindings mapping observer elements to quantities and derivation expressions

Structural Invariant:
Reads real numeric values BEFORE normalisation masking replaces numerals with '#'
and verifies aggregate invariants structurally per container scope.
"""

from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_FLOOR, ROUND_CEILING, ROUND_DOWN, ROUND_UP
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

_NUM_PATTERN = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_AGG_FUNC_PATTERN = re.compile(r"^(sum|count|avg)\((.+)\)$", re.IGNORECASE)

ROUNDING_MODES = {
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "HALF_UP": ROUND_HALF_UP,
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_FLOOR": ROUND_FLOOR,
    "FLOOR": ROUND_FLOOR,
    "ROUND_CEILING": ROUND_CEILING,
    "CEILING": ROUND_CEILING,
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_UP": ROUND_UP,
    "TRUNCATE": ROUND_DOWN,
}


class DOMNode(object):
    """DOM node representation for structural HTML traversal."""

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["DOMNode"] = None):
        self.tag = tag.lower()
        self.attrs = attrs
        self.classes = set(attrs.get("class", "").split())
        self.id = attrs.get("id", "").strip()
        self.parent = parent
        self.children: List["DOMNode"] = []
        self.direct_text: List[str] = []

    @property
    def all_text(self) -> str:
        parts = list(self.direct_text)
        for child in self.children:
            parts.append(child.all_text)
        return " ".join(parts)

    def find_all(self, predicate) -> List["DOMNode"]:
        matches = []
        if predicate(self):
            matches.append(self)
        for child in self.children:
            matches.extend(child.find_all(predicate))
        return matches

    def find_descendants(self, predicate) -> List["DOMNode"]:
        matches = []
        for child in self.children:
            if predicate(child):
                matches.append(child)
            matches.extend(child.find_descendants(predicate))
        return matches

    def describe(self) -> str:
        desc = self.tag
        if self.id:
            desc += "#" + self.id
        if self.classes:
            desc += "." + ".".join(sorted(self.classes))
        return desc


class _DOMBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = DOMNode("root", {})
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        attr_dict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        node = DOMNode(tag, attr_dict, parent=self.current)
        self.current.children.append(node)
        # Avoid nesting for void elements
        if tag.lower() not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.current = node

    def handle_endtag(self, tag: str):
        curr = self.current
        while curr is not self.root:
            if curr.tag == tag.lower():
                self.current = curr.parent or self.root
                break
            curr = curr.parent or self.root

    def handle_data(self, data: str):
        if data.strip():
            self.current.direct_text.append(data.strip())


def parse_dom(html: str) -> DOMNode:
    builder = _DOMBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def extract_numeric_value(text: str) -> Optional[Decimal]:
    """Extract the first numeric value in a text snippet."""
    cleaned = text.replace(",", "")
    match = _NUM_PATTERN.search(cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except Exception:
        return None


def parse_precision_decimals(representation: Optional[str]) -> int:
    """Derive decimal places from representation string (e.g. 2_DECIMAL_PLACES -> 2)."""
    if not representation:
        return 2
    rep = str(representation).upper()
    match = re.search(r"(\d+)_DECIMAL_PLACES", rep)
    if match:
        return int(match.group(1))
    if "INTEGER" in rep:
        return 0
    if "." in rep:
        return len(rep.split(".")[-1])
    return 2


class AggregateRule(NamedTuple):
    name: str
    agg_binding: Dict[str, Any]
    part_binding: Dict[str, Any]
    precision: int
    rounding_mode: str
    tolerance_epsilon: Decimal


def _normalize_name(name: str) -> Set[str]:
    s = name.lower().strip()
    if not s:
        return set()
    return {
        s,
        s.replace("_", "-"),
        s.replace("-", "_"),
        s.replace(".", "-"),
        s.replace(".", "_"),
    }


def _matches_binding(node: DOMNode, binding: Dict[str, Any]) -> bool:
    proj_id = binding.get("projection_id") or ""
    candidates = _normalize_name(proj_id)
    source_ref = binding.get("source_reference") or ""
    if "." in source_ref:
        candidates |= _normalize_name(source_ref.split(".")[-1])
    candidates |= _normalize_name(source_ref)

    if node.id and _normalize_name(node.id) & candidates:
        return True
    for cls in node.classes:
        if _normalize_name(cls) & candidates:
            return True
    for attr in ("data-projection", "data-binding", "data-field", "data-testid", "data-name", "name"):
        val = node.attrs.get(attr) or ""
        if val and _normalize_name(val) & candidates:
            return True
    return False


def _filter_top_level_nodes(nodes: List[DOMNode]) -> List[DOMNode]:
    """Filter to only outermost matching nodes to avoid double counting nested parts."""
    node_set = set(nodes)
    out = []
    for node in nodes:
        ancestor = node.parent
        is_nested = False
        while ancestor is not None:
            if ancestor in node_set:
                is_nested = True
                break
            ancestor = ancestor.parent
        if not is_nested:
            out.append(node)
    return out


def load_domain_lexicon(
    base_dir: Optional[Path] = None, feature_slug: Optional[str] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[Path], Optional[str]]:
    """Locate and load domain-lexicon.json from candidate locations.

    Returns (lexicon, directory the lexicon file was found in, error). The directory is the base
    other declarations resolve paths against — a contract-fixture `contract_ref` beside the
    lexicon, for instance — so a caller resolving such a path does not re-derive this same search.
    """
    candidates: List[Path] = []
    if base_dir is not None:
        candidates.extend([
            base_dir / "domain-lexicon.json",
            base_dir.parent / "domain-lexicon.json",
        ])
    if feature_slug:
        cwd = Path.cwd()
        candidates.extend([
            cwd / ".verified-feature-delivery" / feature_slug / "domain-lexicon.json",
            cwd / feature_slug / "domain-lexicon.json",
        ])
        if base_dir is not None:
            candidates.append(base_dir / feature_slug / "domain-lexicon.json")

    for p in candidates:
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8")), p.parent, None
            except Exception as e:
                return None, None, f"Failed to parse {p}: {e}"
    return None, None, "domain-lexicon.json not found in search paths"


def _find_unit_precision_for_binding(
    lexicon: Dict[str, Any],
    binding: Dict[str, Any],
    va: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    unit_precisions = {
        up.get("id"): up
        for up in lexicon.get("unit_precisions") or []
        if isinstance(up, dict) and up.get("id")
    }

    # 1. Check if source_reference points to Entity.attribute
    source_ref = binding.get("source_reference") or ""
    if "." in source_ref:
        entity_name, attr_name = source_ref.split(".", 1)
        for entity in lexicon.get("entities") or []:
            if isinstance(entity, dict) and entity.get("name") == entity_name:
                for attr in entity.get("attributes") or []:
                    if isinstance(attr, dict) and attr.get("name") == attr_name:
                        unit_ref = attr.get("unit_ref")
                        if unit_ref in unit_precisions:
                            return unit_precisions[unit_ref]

    # 2. Check candidate keys directly in unit_precisions
    candidates = [
        binding.get("projection_id"),
        source_ref,
        source_ref.split(".")[-1] if "." in source_ref else None,
        va.get("value_name") if va else None,
    ]
    for cand in candidates:
        if cand and cand in unit_precisions:
            return unit_precisions[cand]

    # 3. Check any unit_precision matching quantity_type
    for up in unit_precisions.values():
        if up.get("quantity_type") in {"TEMPORAL", "NUMERIC", "CURRENCY"}:
            return up

    return None


def extract_aggregate_rules(lexicon: Dict[str, Any]) -> Tuple[List[AggregateRule], List[str]]:
    """Derive aggregate-to-parts rules from Stage 0 declarations."""
    problems: List[str] = []
    rules: List[AggregateRule] = []

    value_authorities = lexicon.get("value_authorities") or []
    projection_bindings = lexicon.get("projection_bindings") or []

    # Find authorities declaring local aggregate computation
    for va in value_authorities:
        if not isinstance(va, dict):
            continue
        vname = va.get("value_name") or ""
        comp = va.get("local_computation")
        if not comp or not isinstance(comp, str):
            continue

        agg_match = _AGG_FUNC_PATTERN.match(comp.strip())
        if not agg_match:
            continue

        func_name = agg_match.group(1).lower()
        if func_name != "sum":
            continue

        inner_target = agg_match.group(2).strip()

        # Find aggregate projection binding
        agg_pb = None
        for pb in projection_bindings:
            if not isinstance(pb, dict):
                continue
            if pb.get("source_reference") == vname or pb.get("derivation_expression") == comp or pb.get("projection_id") == vname:
                agg_pb = pb
                break
        if not agg_pb:
            for pb in projection_bindings:
                if not isinstance(pb, dict):
                    continue
                deriv = pb.get("derivation_expression") or ""
                if _AGG_FUNC_PATTERN.match(deriv.strip()):
                    agg_pb = pb
                    break

        # Find parts projection binding
        part_pb = None
        for pb in projection_bindings:
            if not isinstance(pb, dict):
                continue
            if pb.get("source_reference") == inner_target or pb.get("derivation_expression") == inner_target:
                part_pb = pb
                break

        if not agg_pb or not part_pb:
            problems.append(
                f"UNDECLARED_ARITHMETIC_RELATION: value authority {vname!r} declares {comp!r}, "
                f"but lexicon does not provide projection bindings for both aggregate and parts"
            )
            continue

        up = _find_unit_precision_for_binding(lexicon, agg_pb, va)
        decimals = parse_precision_decimals(up.get("representation") if up else None)
        mode_str = (up.get("rounding_mode") if up else "ROUND_HALF_UP") or "ROUND_HALF_UP"
        epsilon = Decimal(str(va.get("tolerance_epsilon") or "0.001"))

        rules.append(AggregateRule(
            name=vname,
            agg_binding=agg_pb,
            part_binding=part_pb,
            precision=decimals,
            rounding_mode=mode_str,
            tolerance_epsilon=epsilon,
        ))

    # Also check projection_bindings with sum(...) derivation expressions not in value_authorities
    for pb in projection_bindings:
        if not isinstance(pb, dict):
            continue
        deriv = pb.get("derivation_expression") or ""
        agg_match = _AGG_FUNC_PATTERN.match(deriv.strip())
        if not agg_match or agg_match.group(1).lower() != "sum":
            continue
        if any(r.agg_binding == pb for r in rules):
            continue

        inner_target = agg_match.group(2).strip()
        part_pb = None
        for other in projection_bindings:
            if not isinstance(other, dict) or other == pb:
                continue
            if other.get("source_reference") == inner_target or other.get("derivation_expression") == inner_target:
                part_pb = other
                break

        if not part_pb:
            problems.append(
                f"UNDECLARED_ARITHMETIC_RELATION: projection binding {pb.get('projection_id')!r} "
                f"declares {deriv!r} but no matching part binding was declared"
            )
            continue

        up = _find_unit_precision_for_binding(lexicon, pb)
        decimals = parse_precision_decimals(up.get("representation") if up else None)
        mode_str = (up.get("rounding_mode") if up else "ROUND_HALF_UP") or "ROUND_HALF_UP"
        epsilon = Decimal("0.001")
        rules.append(AggregateRule(
            name=pb.get("projection_id") or "unnamed_aggregate",
            agg_binding=pb,
            part_binding=part_pb,
            precision=decimals,
            rounding_mode=mode_str,
            tolerance_epsilon=epsilon,
        ))

    return rules, problems


def check_artifact_arithmetic(
    artifact: Any,
    rules: List[AggregateRule],
) -> List[str]:
    """Verify arithmetic invariants inside captured artifact before masking."""
    problems: List[str] = []
    if not isinstance(artifact, str):
        return problems

    root = parse_dom(artifact)

    for rule in rules:
        all_agg = root.find_all(lambda n: _matches_binding(n, rule.agg_binding))
        all_parts = root.find_all(lambda n: _matches_binding(n, rule.part_binding))

        agg_nodes = _filter_top_level_nodes(all_agg)
        part_nodes = _filter_top_level_nodes(all_parts)

        if not agg_nodes and not part_nodes:
            continue

        if not agg_nodes:
            problems.append(
                f"EVIDENCE_CLAIM_INADMISSIBLE: captured artifact carries part elements for {rule.name!r} "
                f"but no declared aggregate projection node ({rule.agg_binding.get('projection_id')!r}) was found"
            )
            continue

        for agg_node in agg_nodes:
            agg_val = extract_numeric_value(agg_node.all_text)
            if agg_val is None:
                problems.append(
                    f"ARITHMETIC_INCONSISTENCY: aggregate node <{agg_node.describe()}> carries no parseable numeric value"
                )
                continue

            # Find nearest enclosing container that contains parts
            container = agg_node.parent
            scoped_parts: List[DOMNode] = []
            while container is not None and container is not root:
                descendant_parts = container.find_descendants(lambda n: _matches_binding(n, rule.part_binding) and n is not agg_node)
                descendant_parts = _filter_top_level_nodes(descendant_parts)
                if descendant_parts:
                    scoped_parts = descendant_parts
                    break
                container = container.parent

            if not scoped_parts:
                scoped_parts = [n for n in part_nodes if n is not agg_node]
                container = root

            part_vals = []
            for pn in scoped_parts:
                v = extract_numeric_value(pn.all_text)
                if v is not None:
                    part_vals.append(v)

            total_parts = sum(part_vals, Decimal(0))
            rounding = ROUNDING_MODES.get(rule.rounding_mode, ROUND_HALF_UP)
            quantizer = Decimal("1." + "0" * rule.precision) if rule.precision > 0 else Decimal("1")
            rounded_parts = total_parts.quantize(quantizer, rounding=rounding)
            rounded_agg = agg_val.quantize(quantizer, rounding=rounding)

            diff = abs(rounded_agg - rounded_parts)
            if diff > rule.tolerance_epsilon:
                container_desc = container.describe() if container is not None else "document"
                problems.append(
                    f"ARITHMETIC_INCONSISTENCY: container <{container_desc}> reports aggregate {rounded_agg} "
                    f"({agg_node.describe()}), but sum of its {len(part_vals)} contained part(s) is {rounded_parts} "
                    f"(discrepancy {diff} > tolerance {rule.tolerance_epsilon} under {rule.rounding_mode})"
                )

    return problems


def verify_arithmetic_consistency(
    report: Dict[str, Any],
    planned: Dict[str, Any],
    verified: Sequence[Any],
    base_dir: Optional[Path] = None,
) -> List[str]:
    """Verify arithmetic aggregate consistency for all milestone evidence captures."""
    lexicon, _lexicon_dir, err = load_domain_lexicon(base_dir, report.get("feature_slug"))
    if lexicon is None:
        return [f"MISSING_GATE_ARTIFACT: domain-lexicon.json could not be loaded ({err})"]

    rules, rule_problems = extract_aggregate_rules(lexicon)
    if rule_problems:
        return rule_problems
    if not rules:
        # No aggregate calculations or local computation rules declared for this feature
        return []

    problems: List[str] = []
    for evidence in verified:
        envelope = getattr(evidence, "envelope", None)
        if envelope is None and isinstance(evidence, dict):
            envelope = evidence.get("envelope")
        if envelope is None:
            continue
        artifact = envelope.get("captured_artifact")
        problems.extend(check_artifact_arithmetic(artifact, rules))

    return problems


