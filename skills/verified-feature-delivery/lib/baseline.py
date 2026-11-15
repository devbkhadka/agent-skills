"""Project domain baseline engine — pure domain model, element indexing, diffing, and merging.

Part of verified-feature-delivery tiered delivery architecture.
Manages the committed repository baseline under `<repo>/docs/domain/` (override with `--baseline-dir`).
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

SCHEMA_VERSION = "1.0.0"
DEFAULT_BASELINE_DIR = "docs/domain"

# 10 canonical boundary classes defined by schemas/boundary-manifest.schema.json
CANONICAL_BOUNDARY_CLASSES = (
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
)


class BaselineError(Exception):
    """Base exception for baseline operations."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class BaselineConflictError(BaselineError):
    """Raised when changed elements in a feature lexicon are not authorized by --amend."""

    def __init__(self, message: str, conflicts: Optional[Set[str]] = None):
        super().__init__("BASELINE_CONFLICT", message)
        self.conflicts = conflicts or set()


def now_iso() -> str:
    """Return the current UTC timestamp formatted as ISO-8601."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def element_digest(element: Any) -> str:
    """Return the sha256 hex digest of the canonical JSON representation of element.
    
    Canonical JSON is whitespace-free with sorted keys and ASCII encoding.
    """
    blob = json.dumps(element, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _extract_actors_and_observers(lexicon: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extract actors and observers lists from domain lexicon."""
    actors_and_observers = lexicon.get("actors_and_observers")
    actors: List[Dict[str, Any]] = []
    observers: List[Dict[str, Any]] = []

    if isinstance(actors_and_observers, dict):
        actors = actors_and_observers.get("actors") or []
        observers = actors_and_observers.get("observers") or []
    else:
        if isinstance(lexicon.get("actors"), list):
            actors = lexicon.get("actors") or []
        if isinstance(lexicon.get("observers"), list):
            observers = lexicon.get("observers") or []

    return actors, observers


def element_index(lexicon: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index all domain elements declared in lexicon into a flat mapping of '<kind>:<id>' -> element.
    
    Derived strictly from schemas/domain-lexicon.schema.json:
    - entity:<name>
    - value_object:<name>
    - action:<name>
    - state_enum:<name>
    - monotonic_clock:<name>
    - unit_precision:<id>
    - actor:<id>
    - observer:<id>
    - alias_pair:<canonical_concept>
    - projection_binding:<projection_id>
    - value_authority:<value_name>
    - fact_precedence:<fact_name>
    - boundary:<boundary_id>
    - entity_relationship:<from_entity>-><to_entity>
    - simulated_dependency:<boundary_class>
    """
    index: Dict[str, Dict[str, Any]] = {}
    if not isinstance(lexicon, dict):
        return index

    # 1. Entities
    for item in lexicon.get("entities") or []:
        if isinstance(item, dict) and item.get("name"):
            index[f"entity:{item['name']}"] = item

    # 2. Value Objects
    for item in lexicon.get("value_objects") or []:
        if isinstance(item, dict) and item.get("name"):
            index[f"value_object:{item['name']}"] = item

    # 3. Actions
    for item in lexicon.get("actions") or []:
        if isinstance(item, dict) and item.get("name"):
            index[f"action:{item['name']}"] = item

    # 4. State Enums
    for item in lexicon.get("state_enums") or []:
        if isinstance(item, dict) and item.get("name"):
            index[f"state_enum:{item['name']}"] = item

    # 5. Monotonic Clocks
    for item in lexicon.get("monotonic_clocks") or []:
        if isinstance(item, dict) and item.get("name"):
            index[f"monotonic_clock:{item['name']}"] = item

    # 6. Unit Precisions
    for item in lexicon.get("unit_precisions") or []:
        if isinstance(item, dict) and item.get("id"):
            index[f"unit_precision:{item['id']}"] = item

    # 7 & 8. Actors and Observers
    actors, observers = _extract_actors_and_observers(lexicon)
    for item in actors:
        if isinstance(item, dict) and item.get("id"):
            index[f"actor:{item['id']}"] = item
    for item in observers:
        if isinstance(item, dict) and item.get("id"):
            index[f"observer:{item['id']}"] = item

    # 9. Alias Pairs
    for item in lexicon.get("alias_pairs") or []:
        if isinstance(item, dict) and item.get("canonical_concept"):
            index[f"alias_pair:{item['canonical_concept']}"] = item

    # 10. Projection Bindings
    for item in lexicon.get("projection_bindings") or []:
        if isinstance(item, dict) and item.get("projection_id"):
            index[f"projection_binding:{item['projection_id']}"] = item

    # 11. Value Authorities
    for item in lexicon.get("value_authorities") or []:
        if isinstance(item, dict) and item.get("value_name"):
            index[f"value_authority:{item['value_name']}"] = item

    # 12. Fact Precedences
    for item in lexicon.get("fact_precedences") or []:
        if isinstance(item, dict) and item.get("fact_name"):
            index[f"fact_precedence:{item['fact_name']}"] = item

    # 13. Entity Boundaries
    for item in lexicon.get("entity_boundaries") or []:
        if isinstance(item, dict) and item.get("boundary_id"):
            index[f"boundary:{item['boundary_id']}"] = item

    # 14. Entity Relationships
    for item in lexicon.get("entity_relationships") or []:
        if isinstance(item, dict) and item.get("from_entity") and item.get("to_entity"):
            rel_id = f"{item['from_entity']}->{item['to_entity']}"
            index[f"entity_relationship:{rel_id}"] = item

    # 15. Simulated Dependencies
    for item in lexicon.get("simulated_dependencies") or []:
        if isinstance(item, dict) and item.get("boundary_class"):
            index[f"simulated_dependency:{item['boundary_class']}"] = item

    return index


def _is_index(data: Any) -> bool:
    """True if data appears to already be an element index rather than a domain lexicon."""
    if not isinstance(data, dict):
        return False
    if "schema_version" in data and ("entities" in data or "bounded_context" in data):
        return False
    return any(":" in str(k) for k in data.keys())


def diff_lexicons(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Compute the difference between two domain lexicons (or two element indices).
    
    Returns a dict with:
      - 'added': Set of element IDs in new but not in old
      - 'changed': Set of element IDs present in both whose element_digest differs
      - 'removed': Set of element IDs in old but not in new
    """
    old_idx = old if _is_index(old) else element_index(old)
    new_idx = new if _is_index(new) else element_index(new)

    old_keys = set(old_idx.keys())
    new_keys = set(new_idx.keys())

    added = new_keys - old_keys
    removed = old_keys - new_keys
    common = old_keys & new_keys

    changed = set()
    for k in common:
        if element_digest(old_idx[k]) != element_digest(new_idx[k]):
            changed.add(k)

    return {
        "added": added,
        "changed": changed,
        "removed": removed,
    }


# Scenario-matrix sections an approval may guard, with the field that names each element.
# Lexicon sections are indexed by element_index, whose ids the baseline already uses.
MATRIX_ELEMENT_ID_FIELDS = {
    "flows": ("flow", "flow_id"),
    "flow_groups": ("flow_group", "group_id"),
    "states": ("state", "state_id"),
    "transitions": ("transition", "transition_id"),
}

# The element kinds element_index derives from each lexicon section.
LEXICON_SECTION_KINDS = {
    "entity_boundaries": "boundary",
    "entity_relationships": "entity_relationship",
    "simulated_dependencies": "simulated_dependency",
}


def scoped_element_digests(artifact: Any, scope_keys: Iterable[str]) -> Dict[str, str]:
    """Map '<kind>:<id>' -> element_digest for every element inside the named sections.

    The per-element counterpart of an approval's whole-scope digest: when the scope digest goes
    stale, comparing these maps names the elements that changed. An element without its id field
    is keyed by position ('<section>[<i>]') so it is still guarded.
    """
    digests: Dict[str, str] = {}
    if not isinstance(artifact, dict):
        return digests
    lexicon_index = None
    for section in scope_keys:
        if section in LEXICON_SECTION_KINDS:
            if lexicon_index is None:
                lexicon_index = element_index(artifact)
            prefix = LEXICON_SECTION_KINDS[section] + ":"
            for key, element in lexicon_index.items():
                if key.startswith(prefix):
                    digests[key] = element_digest(element)
            continue
        kind, id_field = MATRIX_ELEMENT_ID_FIELDS.get(section, (section, None))
        items = artifact.get(section)
        if isinstance(items, dict):
            items = [items]
        for position, item in enumerate(items or []):
            element_id = item.get(id_field) if isinstance(item, dict) and id_field else None
            key = "%s:%s" % (kind, element_id) if element_id else "%s[%d]" % (section, position)
            digests[key] = element_digest(item)
    return digests


def changed_element_ids(approved: Dict[str, str], current: Dict[str, str]) -> List[str]:
    """Element ids added, removed or edited between two scoped_element_digests maps."""
    keys = set(approved) | set(current)
    return sorted(k for k in keys if approved.get(k) != current.get(k))


def normalize_element_id(element_id: str) -> str:
    """Normalize aliases for element kinds (e.g. entity_boundary -> boundary)."""
    if element_id.startswith("entity_boundary:"):
        return "boundary:" + element_id[len("entity_boundary:"):]
    return element_id


def merge(
    baseline_lexicon: Dict[str, Any],
    feature_lexicon: Dict[str, Any],
    amend: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Merge a feature lexicon into a baseline lexicon.
    
    Refuses with BaselineConflictError if any changed element is not authorized in amend.
    Additive: keeps all existing baseline elements, appends new elements from feature,
    and updates elements present in amend.
    """
    diff = diff_lexicons(baseline_lexicon, feature_lexicon)
    changed_ids = diff["changed"]

    # Normalize amend set
    amend_set = set()
    for item in amend or []:
        norm = normalize_element_id(item)
        amend_set.add(norm)
        # Also store original
        amend_set.add(item)

    unauthorized_changes = set()
    for ch in changed_ids:
        norm_ch = normalize_element_id(ch)
        if norm_ch not in amend_set and ch not in amend_set:
            unauthorized_changes.add(ch)

    if unauthorized_changes:
        sorted_conflicts = sorted(unauthorized_changes)
        raise BaselineConflictError(
            f"BASELINE_CONFLICT: Changed elements not authorized in amend: {', '.join(sorted_conflicts)}",
            conflicts=unauthorized_changes,
        )

    merged = copy.deepcopy(baseline_lexicon) if baseline_lexicon else {}

    # Initialize top-level metadata
    merged["schema_version"] = merged.get("schema_version") or feature_lexicon.get("schema_version") or SCHEMA_VERSION
    merged["feature_slug"] = merged.get("feature_slug") or "baseline"
    if merged.get("bounded_context") in (None, "", "domain") and feature_lexicon.get("bounded_context"):
        merged["bounded_context"] = feature_lexicon["bounded_context"]
    else:
        merged["bounded_context"] = merged.get("bounded_context") or "domain"

    # Register specifications for list merges: (list_key, kind, id_extractor)
    registers = [
        ("entities", "entity", lambda el: el.get("name")),
        ("value_objects", "value_object", lambda el: el.get("name")),
        ("actions", "action", lambda el: el.get("name")),
        ("state_enums", "state_enum", lambda el: el.get("name")),
        ("monotonic_clocks", "monotonic_clock", lambda el: el.get("name")),
        ("unit_precisions", "unit_precision", lambda el: el.get("id")),
        ("alias_pairs", "alias_pair", lambda el: el.get("canonical_concept")),
        ("projection_bindings", "projection_binding", lambda el: el.get("projection_id")),
        ("value_authorities", "value_authority", lambda el: el.get("value_name")),
        ("fact_precedences", "fact_precedence", lambda el: el.get("fact_name")),
        ("entity_boundaries", "boundary", lambda el: el.get("boundary_id")),
        ("entity_relationships", "entity_relationship", lambda el: f"{el.get('from_entity')}->{el.get('to_entity')}"),
        ("simulated_dependencies", "simulated_dependency", lambda el: el.get("boundary_class")),
    ]

    for list_key, kind, id_fn in registers:
        base_list = merged.get(list_key)
        if base_list is None:
            base_list = []
            merged[list_key] = base_list

        feat_list = feature_lexicon.get(list_key) or []
        base_id_map = {id_fn(item): idx for idx, item in enumerate(base_list) if isinstance(item, dict) and id_fn(item)}

        for feat_item in feat_list:
            if not isinstance(feat_item, dict):
                continue
            item_id = id_fn(feat_item)
            if not item_id:
                continue
            key = f"{kind}:{item_id}"
            if item_id in base_id_map:
                if key in changed_ids:
                    # Update authorized amended element
                    base_list[base_id_map[item_id]] = copy.deepcopy(feat_item)
            else:
                # Add new element
                base_list.append(copy.deepcopy(feat_item))
                base_id_map[item_id] = len(base_list) - 1

    # Actors and observers
    merged_ao = merged.get("actors_and_observers")
    if not isinstance(merged_ao, dict):
        merged_ao = {"actors": [], "observers": []}
        merged["actors_and_observers"] = merged_ao
    if "actors" not in merged_ao or not isinstance(merged_ao["actors"], list):
        merged_ao["actors"] = []
    if "observers" not in merged_ao or not isinstance(merged_ao["observers"], list):
        merged_ao["observers"] = []

    feat_actors, feat_observers = _extract_actors_and_observers(feature_lexicon)

    # Merge actors
    base_actors = merged_ao["actors"]
    actor_id_map = {a.get("id"): idx for idx, a in enumerate(base_actors) if isinstance(a, dict) and a.get("id")}
    for fa in feat_actors:
        if isinstance(fa, dict) and fa.get("id"):
            aid = fa["id"]
            key = f"actor:{aid}"
            if aid in actor_id_map:
                if key in changed_ids:
                    base_actors[actor_id_map[aid]] = copy.deepcopy(fa)
            else:
                base_actors.append(copy.deepcopy(fa))
                actor_id_map[aid] = len(base_actors) - 1

    # Merge observers
    base_observers = merged_ao["observers"]
    obs_id_map = {o.get("id"): idx for idx, o in enumerate(base_observers) if isinstance(o, dict) and o.get("id")}
    for fo in feat_observers:
        if isinstance(fo, dict) and fo.get("id"):
            oid = fo["id"]
            key = f"observer:{oid}"
            if oid in obs_id_map:
                if key in changed_ids:
                    base_observers[obs_id_map[oid]] = copy.deepcopy(fo)
            else:
                base_observers.append(copy.deepcopy(fo))
                obs_id_map[oid] = len(base_observers) - 1

    # String arrays
    string_lists = ["dual_computed_values", "dual_boundary_facts", "terminal_states", "observable_quantities"]
    for s_key in string_lists:
        base_items = list(merged.get(s_key) or [])
        feat_items = list(feature_lexicon.get(s_key) or [])
        for item in feat_items:
            if item not in base_items:
                base_items.append(item)
        merged[s_key] = base_items

    return merged


# ---------------------------------------------------------------------------
# Lexicon validation helper
# ---------------------------------------------------------------------------

_VALIDATE_LEXICON_FN = None


def _get_validator():
    global _VALIDATE_LEXICON_FN
    if _VALIDATE_LEXICON_FN is None:
        bin_path = Path(__file__).resolve().parent.parent / "bin" / "validate-lexicon"
        loader = SourceFileLoader("validate_lexicon", str(bin_path))
        spec = importlib.util.spec_from_loader("validate_lexicon", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        _VALIDATE_LEXICON_FN = mod.validate_lexicon_dict
    return _VALIDATE_LEXICON_FN


def validate_baseline_lexicon(lexicon: Dict[str, Any], file_path: Optional[str] = None) -> Tuple[bool, Optional[str], str]:
    """Validate a baseline lexicon.
    
    If empty baseline (layout with 0 elements), passes clean.
    Otherwise delegates to bin/validate-lexicon's validate_lexicon_dict.
    """
    if not isinstance(lexicon, dict):
        return False, "LEXICON_INVALID_SCHEMA", "Root must be a JSON object"

    req_keys = [
        "schema_version",
        "feature_slug",
        "bounded_context",
        "entities",
        "value_objects",
        "state_enums",
        "unit_precisions",
        "actors_and_observers",
        "alias_pairs",
        "projection_bindings",
        "value_authorities",
        "fact_precedences",
    ]
    for k in req_keys:
        if k not in lexicon:
            return False, "LEXICON_INVALID_SCHEMA", f"Missing required top-level key: {k}"

    idx = element_index(lexicon)
    if not idx:
        return True, None, "LEXICON_VALIDATED_CLEAN"

    val_fn = _get_validator()
    return val_fn(lexicon, file_path=file_path)


# ---------------------------------------------------------------------------
# Storage and layout helpers
# ---------------------------------------------------------------------------

def load_baseline_index(baseline_dir: Union[str, Path]) -> Dict[str, Any]:
    """Load baseline.json from baseline_dir."""
    p = Path(baseline_dir) / "baseline.json"
    if not p.is_file():
        raise BaselineError("MISSING_BASELINE", f"baseline.json not found in {baseline_dir}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError("INVALID_BASELINE_JSON", f"Malformed baseline.json: {exc}")


def save_baseline_index(baseline_dir: Union[str, Path], data: Dict[str, Any]) -> None:
    """Save baseline.json into baseline_dir."""
    p = Path(baseline_dir) / "baseline.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_baseline_lexicon(baseline_dir: Union[str, Path]) -> Dict[str, Any]:
    """Load domain-lexicon.json from baseline_dir."""
    p = Path(baseline_dir) / "domain-lexicon.json"
    if not p.is_file():
        raise BaselineError("MISSING_BASELINE_LEXICON", f"domain-lexicon.json not found in {baseline_dir}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError("INVALID_LEXICON_JSON", f"Malformed domain-lexicon.json: {exc}")


def save_baseline_lexicon(baseline_dir: Union[str, Path], data: Dict[str, Any]) -> None:
    """Save domain-lexicon.json into baseline_dir."""
    p = Path(baseline_dir) / "domain-lexicon.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def render_baseline_docs(baseline_dir: Union[str, Path]) -> None:
    """Re-render ubiquitous-language-glossary.md and domain-entity-map.md into <baseline_dir>/docs/."""
    b_dir = Path(baseline_dir).resolve()
    lex_path = b_dir / "domain-lexicon.json"
    docs_dir = b_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    skill_bin = Path(__file__).resolve().parent.parent / "bin"
    glossary_bin = skill_bin / "render-glossary"
    entity_map_bin = skill_bin / "render-entity-map"

    res1 = subprocess.run(
        [sys.executable, str(glossary_bin), "--artifact", str(lex_path), "--out", str(docs_dir)],
        capture_output=True,
        text=True,
    )
    if res1.returncode != 0:
        raise BaselineError("RENDER_GLOSSARY_FAILED", f"render-glossary refused: {res1.stdout.strip()} {res1.stderr.strip()}")

    res2 = subprocess.run(
        [sys.executable, str(entity_map_bin), "--artifact", str(lex_path), "--out", str(docs_dir)],
        capture_output=True,
        text=True,
    )
    if res2.returncode != 0:
        raise BaselineError("RENDER_ENTITY_MAP_FAILED", f"render-entity-map refused: {res2.stdout.strip()} {res2.stderr.strip()}")


def check_rendered_docs_fresh(baseline_dir: Union[str, Path]) -> Tuple[bool, Optional[str]]:
    """Verify that rendered documentation in <baseline_dir>/docs/ matches a fresh rendering of domain-lexicon.json."""
    b_dir = Path(baseline_dir).resolve()
    lex_path = b_dir / "domain-lexicon.json"
    docs_dir = b_dir / "docs"
    glossary_path = docs_dir / "ubiquitous-language-glossary.md"
    entity_map_path = docs_dir / "domain-entity-map.md"

    if not glossary_path.is_file():
        return False, f"Missing rendered document: {glossary_path.name}"
    if not entity_map_path.is_file():
        return False, f"Missing rendered document: {entity_map_path.name}"

    skill_bin = Path(__file__).resolve().parent.parent / "bin"
    glossary_bin = skill_bin / "render-glossary"
    entity_map_bin = skill_bin / "render-entity-map"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_docs = Path(tmpdir)
        res1 = subprocess.run(
            [sys.executable, str(glossary_bin), "--artifact", str(lex_path), "--out", str(tmp_docs)],
            capture_output=True,
            text=True,
        )
        if res1.returncode != 0:
            return False, f"render-glossary failed: {res1.stdout.strip()}"

        res2 = subprocess.run(
            [sys.executable, str(entity_map_bin), "--artifact", str(lex_path), "--out", str(tmp_docs)],
            capture_output=True,
            text=True,
        )
        if res2.returncode != 0:
            return False, f"render-entity-map failed: {res2.stdout.strip()}"

        expected_glossary = (tmp_docs / "ubiquitous-language-glossary.md").read_text(encoding="utf-8")
        actual_glossary = glossary_path.read_text(encoding="utf-8")
        if expected_glossary != actual_glossary:
            return False, "ubiquitous-language-glossary.md is stale"

        expected_map = (tmp_docs / "domain-entity-map.md").read_text(encoding="utf-8")
        actual_map = entity_map_path.read_text(encoding="utf-8")
        if expected_map != actual_map:
            return False, "domain-entity-map.md is stale"

    return True, None


def init_baseline_layout(baseline_dir: Union[str, Path]) -> None:
    """Create a new empty-but-valid baseline layout in baseline_dir."""
    b_dir = Path(baseline_dir).resolve()
    b_dir.mkdir(parents=True, exist_ok=True)

    # 1. baseline.json
    baseline_index = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now_iso(),
        "sources": [],
        "element_digests": {},
        "approvals": {},
    }
    save_baseline_index(b_dir, baseline_index)

    # 2. domain-lexicon.json
    empty_lexicon = {
        "schema_version": SCHEMA_VERSION,
        "feature_slug": "baseline",
        "bounded_context": "domain",
        "entities": [],
        "value_objects": [],
        "actions": [],
        "state_enums": [],
        "monotonic_clocks": [],
        "unit_precisions": [],
        "actors_and_observers": {
            "actors": [],
            "observers": [],
        },
        "alias_pairs": [],
        "projection_bindings": [],
        "value_authorities": [],
        "fact_precedences": [],
        "entity_relationships": [],
        "entity_boundaries": [],
        "simulated_dependencies": [],
    }
    save_baseline_lexicon(b_dir, empty_lexicon)

    # 3. contract-fixtures/manifest.json
    fixtures_dir = b_dir / "contract-fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    for cls_name in CANONICAL_BOUNDARY_CLASSES:
        row: Dict[str, Any] = {
            "class_name": cls_name,
            "verdict": "NOT_PRESENT",
            "absence_reason": "Baseline initialized unprobed.",
        }
        if cls_name == "OTHER":
            row["description"] = "None"
        manifest_rows.append(row)

    manifest_data = {
        "schema_version": SCHEMA_VERSION,
        "feature_slug": "baseline",
        "updated_at": now_iso(),
        "boundary_classes": manifest_rows,
    }
    manifest_file = fixtures_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")

    # 4. docs/
    render_baseline_docs(b_dir)


def merge_contract_fixtures(baseline_dir: Union[str, Path], feature_fixtures_dir: Union[str, Path]) -> None:
    """Merge contract fixtures from feature_fixtures_dir into <baseline_dir>/contract-fixtures/."""
    f_dir = Path(feature_fixtures_dir).resolve()
    f_manifest = f_dir / "manifest.json"
    if not f_manifest.is_file():
        return

    b_fixtures_dir = Path(baseline_dir).resolve() / "contract-fixtures"
    b_fixtures_dir.mkdir(parents=True, exist_ok=True)
    b_manifest_path = b_fixtures_dir / "manifest.json"

    if b_manifest_path.is_file():
        try:
            b_manifest = json.loads(b_manifest_path.read_text(encoding="utf-8"))
        except Exception:
            b_manifest = {"schema_version": SCHEMA_VERSION, "boundary_classes": []}
    else:
        b_manifest = {"schema_version": SCHEMA_VERSION, "boundary_classes": []}

    try:
        f_data = json.loads(f_manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BaselineError("INVALID_FIXTURE_MANIFEST", f"Malformed feature fixture manifest: {exc}")

    # Build map of baseline boundary classes
    base_classes = {
        row.get("class_name"): row
        for row in b_manifest.get("boundary_classes", [])
        if isinstance(row, dict) and row.get("class_name")
    }

    # Ensure all canonical classes exist in base_classes
    for cname in CANONICAL_BOUNDARY_CLASSES:
        if cname not in base_classes:
            new_row: Dict[str, Any] = {
                "class_name": cname,
                "verdict": "NOT_PRESENT",
                "absence_reason": "Baseline initialized unprobed.",
            }
            if cname == "OTHER":
                new_row["description"] = "None"
            base_classes[cname] = new_row

    # Merge present classes from feature manifest
    for f_row in f_data.get("boundary_classes") or f_data.get("manifest_rows") or []:
        if not isinstance(f_row, dict):
            continue
        cname = f_row.get("class_name")
        if not cname:
            continue

        target_row = base_classes.get(cname)
        if not target_row:
            target_row = copy.deepcopy(f_row)
            base_classes[cname] = target_row
            continue

        if f_row.get("verdict") == "PRESENT":
            target_row["verdict"] = "PRESENT"
            if f_row.get("probe_path"):
                target_row["probe_path"] = f_row["probe_path"]
            target_row.pop("absence_reason", None)
            target_row.pop("reason_if_not_present", None)

            # Merge properties
            existing_props = {
                p.get("property_name"): p
                for p in target_row.get("properties", [])
                if isinstance(p, dict) and p.get("property_name")
            }
            for fp in f_row.get("properties") or []:
                if isinstance(fp, dict) and fp.get("property_name"):
                    pname = fp["property_name"]
                    if fp.get("verdict") == "PROBED" or pname not in existing_props:
                        existing_props[pname] = copy.deepcopy(fp)
            target_row["properties"] = list(existing_props.values())

    b_manifest["boundary_classes"] = list(base_classes.values())
    b_manifest["updated_at"] = now_iso()
    b_manifest_path.write_text(json.dumps(b_manifest, indent=2) + "\n", encoding="utf-8")

    # Copy fixture JSON files
    for item in f_dir.iterdir():
        if item.is_file() and item.name.endswith(".json") and item.name != "manifest.json":
            shutil.copy2(item, b_fixtures_dir / item.name)
