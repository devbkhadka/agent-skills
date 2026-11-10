"""Shared validation primitives for verified-feature-delivery.

Defined once here and imported across all stage validators to eliminate
duplicated logic and ensure uniform enforcement of validation rules.
"""

import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple


class ClosedKeySetResult(NamedTuple):
    is_valid: bool
    unexpected: Set[str]
    allowed: Set[str]
    message: str


def is_reason_meaningful(text: Any) -> bool:
    """Check if negative declaration / justification reason is meaningful prose.

    Positive requirement: several words (at least 3), predominantly letters (at least 8 letters and >= 50% non-whitespace chars).
    Not a negative blacklist.
    """
    if text is None:
        return False
    s = str(text).strip()
    if not s:
        return False
    words = re.findall(r"[a-zA-Z]+", s)
    if len(words) < 3:
        return False
    letters = sum(len(w) for w in words)
    non_space = sum(1 for c in s if not c.isspace())
    if letters < 8 or (letters / non_space) < 0.5:
        return False
    return True


def check_closed_key_set(obj: Any, allowed: Iterable[str], label: str = "object") -> ClosedKeySetResult:
    """Refuse any key outside a stated vocabulary, returning what was unexpected and what was allowed.

    Args:
        obj: The dictionary / object to validate.
        allowed: The collection of allowed key names.
        label: Human-readable label describing the object for error formatting.

    Returns:
        ClosedKeySetResult(is_valid, unexpected, allowed, message)
    """
    allowed_set = set(allowed)
    if not isinstance(obj, dict):
        return ClosedKeySetResult(
            is_valid=False,
            unexpected=set(),
            allowed=allowed_set,
            message=f"{label} must be a dictionary object",
        )

    unexpected = set(obj.keys()) - allowed_set
    if unexpected:
        msg = (
            f"{label} contains unexpected attribute(s): {sorted(list(unexpected))}; "
            f"allowed attributes are: {sorted(list(allowed_set))}"
        )
        return ClosedKeySetResult(
            is_valid=False,
            unexpected=unexpected,
            allowed=allowed_set,
            message=msg,
        )

    return ClosedKeySetResult(
        is_valid=True,
        unexpected=set(),
        allowed=allowed_set,
        message="",
    )


def is_vacuous_container(value: Any) -> bool:
    """Check if a value is None, empty, or a collection (list/dict/set) containing only vacuous elements.

    A collection whose elements carry no content is empty for gate purposes.
    Examples:
        None -> True
        "" / "   " -> True
        {} / {"template": ""} -> True
        [] / [{}] / [{"template": ""}] -> True
        {"template": "active"} -> False
        [{"template": "active"}] -> False
        0 / False -> False (concrete non-vacuous scalars)
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (bool, int, float)):
        return False
    if isinstance(value, dict):
        if not value:
            return True
        return all(is_vacuous_container(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        if not value:
            return True
        return all(is_vacuous_container(item) for item in value)
    return False


def collect_tokens(
    registers: Any,
    allowed_overlaps: Optional[Set[Tuple[str, str]]] = None,
) -> Tuple[Dict[str, Set[str]], List[Tuple[str, str, str]]]:
    """Collect tokens across multiple named registers into one namespace and detect collisions.

    Args:
        registers: Dict of {register_name: iterable_of_tokens} or list of (register_name, iterable_of_tokens).
        allowed_overlaps: Optional set of (reg1, reg2) pairs permitted to share token names (e.g. actors & observers).

    Returns:
        (token_to_registers_map, collisions_list)
        where each collision is (token, reg1_name, reg2_name).
    """
    allowed_pairs = set()
    if allowed_overlaps:
        for r1, r2 in allowed_overlaps:
            allowed_pairs.add((r1, r2))
            allowed_pairs.add((r2, r1))

    items = registers.items() if isinstance(registers, dict) else registers

    token_map: Dict[str, Set[str]] = {}
    registered_list: List[Tuple[str, Set[str]]] = []

    for reg_name, tok_iter in items:
        tok_set = set(tok_iter) if tok_iter else set()
        registered_list.append((reg_name, tok_set))
        for tok in tok_set:
            if not tok:
                continue
            if tok not in token_map:
                token_map[tok] = set()
            token_map[tok].add(reg_name)

    collisions: List[Tuple[str, str, str]] = []
    n = len(registered_list)
    for i in range(n):
        reg1_name, reg1_tokens = registered_list[i]
        for j in range(i + 1, n):
            reg2_name, reg2_tokens = registered_list[j]
            if (reg1_name, reg2_name) in allowed_pairs:
                continue
            overlap = reg1_tokens & reg2_tokens
            for tok in sorted(list(overlap)):
                collisions.append((tok, reg1_name, reg2_name))

    return token_map, collisions


def detect_repeatable_trigger(data: Any, file_path: Optional[Any] = None) -> bool:
    """Check if any trigger is actor-repeatable from data or companion files.

    Unifies checking across:
    1. Direct flags on data (has_actor_repeatable_trigger, has_repeatable_triggers).
    2. data['triggers'] list where trigger has is_actor_repeatable is True.
    3. data['slices'] list where slice leg or slice trigger has is_actor_repeatable is True.
    4. Companion files in the same directory, parent directory, or .verified-feature-delivery/<slug>/.
    """
    if not isinstance(data, dict):
        return False

    # 1. Direct in data
    if data.get("has_actor_repeatable_trigger") is True or data.get("has_repeatable_triggers") is True:
        return True

    # 2. Triggers list
    triggers = data.get("triggers")
    if isinstance(triggers, list):
        for t in triggers:
            if isinstance(t, dict) and t.get("is_actor_repeatable") is True:
                return True

    # 3. Slices list (legs.trigger or trigger)
    slices = data.get("slices")
    if isinstance(slices, list):
        for sl in slices:
            if isinstance(sl, dict):
                legs = sl.get("legs")
                trig = legs.get("trigger") if isinstance(legs, dict) else sl.get("trigger")
                if isinstance(trig, dict) and trig.get("is_actor_repeatable") is True:
                    return True

    # 4. Companion files
    search_dirs: List[Path] = []
    if file_path is not None:
        p = Path(file_path) if not isinstance(file_path, Path) else file_path
        if p.is_file():
            search_dirs.append(p.parent)
            if p.parent.parent.is_dir():
                search_dirs.append(p.parent.parent)

    feature_slug = data.get("feature_slug") or data.get("feature")
    if feature_slug:
        cwd = Path.cwd()
        search_dirs.append(cwd / ".verified-feature-delivery" / feature_slug)
        search_dirs.append(cwd / feature_slug)

    for sdir in search_dirs:
        if not sdir.is_dir():
            continue
        for fname in [
            "scenario-matrix.json",
            "flow-simulation-matrix.json",
            "domain-lexicon.json",
            "execution-dag.json",
            "slice-invariants.json",
            "slicing-decisions.json",
            "decision-rules.json",
        ]:
            cand = sdir / fname
            if cand.is_file():
                try:
                    with open(cand, "r", encoding="utf-8") as f:
                        cdata = json.load(f)
                    if isinstance(cdata, dict):
                        if cdata.get("has_actor_repeatable_trigger") is True or cdata.get("has_repeatable_triggers") is True:
                            return True
                        c_trigs = cdata.get("triggers")
                        if isinstance(c_trigs, list):
                            for t in c_trigs:
                                if isinstance(t, dict) and t.get("is_actor_repeatable") is True:
                                    return True
                        c_slices = cdata.get("slices")
                        if isinstance(c_slices, list):
                            for sl in c_slices:
                                if isinstance(sl, dict):
                                    legs = sl.get("legs")
                                    trig = legs.get("trigger") if isinstance(legs, dict) else sl.get("trigger")
                                    if isinstance(trig, dict) and trig.get("is_actor_repeatable") is True:
                                        return True
                except Exception:
                    pass

    return False

