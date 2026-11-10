"""Slice sidecar context generator and loader for verified-feature-delivery.

Supports slice-scoped worker context loading: generates a focused sidecar state
containing only the specified slice's definition, relevant invariants, domain entity
subsets, referenced contract fixtures, and target evidence/diagnostics paths.
Workers load only their assigned slice context rather than monolithic stage documents.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def now_iso_utc() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json_safe(path: Path) -> Optional[Any]:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


from lib.verification_policy import generate_verification_plan


def build_slice_sidecar(
    feature_dir: Path,
    slice_id: str,
    out_path: Optional[Path] = None,
    diff_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate a self-contained, slice-scoped sidecar context for worker dispatch.

    Args:
        feature_dir: Directory holding feature state and stage artifacts.
        slice_id: Target vertical slice identifier from execution-dag.json.
        out_path: Optional explicit output path for sidecar JSON.
        diff_files: Optional list of modified file paths for verification plan tailoring.

    Returns:
        The sidecar dictionary object.
    """
    feature_dir = Path(feature_dir).resolve()
    state_file = feature_dir / "state.json"
    state_data = _load_json_safe(state_file) or {}
    feature_slug = state_data.get("feature_slug") or feature_dir.name

    dag_file = feature_dir / "execution-dag.json"
    dag_data = _load_json_safe(dag_file) or {}
    slices = dag_data.get("slices") if isinstance(dag_data, dict) else []

    target_slice: Optional[Dict[str, Any]] = None
    if isinstance(slices, list):
        for sl in slices:
            if isinstance(sl, dict) and sl.get("slice_id") == slice_id:
                target_slice = sl
                break

    if target_slice is None:
        raise ValueError(f"Slice '{slice_id}' not found in {dag_file}")

    # Extract relevant invariants
    inv_file = feature_dir / "slice-invariants.json"
    inv_data = _load_json_safe(inv_file) or {}
    relevant_invariants: List[Dict[str, Any]] = []
    if isinstance(inv_data, dict):
        for inv in inv_data.get("invariants", []):
            if isinstance(inv, dict):
                # Match by explicit slice_id, slice_ref, or covered invariants
                sref = inv.get("slice_id") or inv.get("slice_ref")
                if sref == slice_id or slice_id in inv.get("applicable_slices", []):
                    relevant_invariants.append(inv)
                elif inv.get("invariant_id") in target_slice.get("invariants_covered", []):
                    relevant_invariants.append(inv)

    # If execution-dag declared invariants inline, merge them
    for inv_inline in target_slice.get("owned_invariants", []):
        if isinstance(inv_inline, dict) and inv_inline not in relevant_invariants:
            relevant_invariants.append(inv_inline)

    # Extract relevant domain entities from lexicon
    lex_file = feature_dir / "domain-lexicon.json"
    lex_data = _load_json_safe(lex_file) or {}
    relevant_entities: List[Dict[str, Any]] = []
    slice_domain_scope: Set[str] = set()
    for ent_ref in target_slice.get("domain_entities", []):
        slice_domain_scope.add(str(ent_ref))
    if isinstance(lex_data, dict):
        for ent in lex_data.get("entities", []):
            if isinstance(ent, dict):
                ename = ent.get("name")
                if ename and (ename in slice_domain_scope or not slice_domain_scope):
                    relevant_entities.append(ent)

    # Extract referenced contract fixtures
    fixtures_dir = feature_dir / "contract-fixtures"
    fixture_refs: List[str] = []
    for dep in target_slice.get("external_dependencies", []):
        if isinstance(dep, dict) and dep.get("fixture_ref"):
            fixture_refs.append(dep["fixture_ref"])
        elif isinstance(dep, str):
            fixture_refs.append(dep)

    evidence_target = f"evidence/{slice_id}_capture.json"
    diagnostics_target = f"diagnostics/{slice_id}.log"

    verification_plan = generate_verification_plan(
        feature_dir=feature_dir,
        slice_id=slice_id,
        diff_files=diff_files,
        slice_def=target_slice,
    )

    sidecar = {
        "schema_version": "1.0.0",
        "feature_slug": feature_slug,
        "slice_id": slice_id,
        "slice_definition": target_slice,
        "relevant_invariants": relevant_invariants,
        "relevant_entities": relevant_entities,
        "contract_fixture_refs": fixture_refs,
        "verification_plan": verification_plan,
        "evidence_target_path": evidence_target,
        "diagnostics_path": diagnostics_target,
        "generated_at": now_iso_utc(),
    }

    target_out = out_path or (feature_dir / "sidecars" / f"{slice_id}.json")
    target_out.parent.mkdir(parents=True, exist_ok=True)
    with open(target_out, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)
        f.write("\n")

    return sidecar


def load_slice_sidecar(sidecar_path: Path) -> Dict[str, Any]:
    """Load a slice sidecar JSON file."""
    p = Path(sidecar_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Sidecar file not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)
