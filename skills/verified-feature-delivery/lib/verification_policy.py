"""Risk- and impact-based verification policy engine for verified-feature-delivery.

Defines deterministic rules for risk- and impact-based verification, establishing
a 3-tier execution model (slice-local, affected-feature, and full-suite) with reasoned
skip audits and conservative escalation.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from lib.primitives import is_reason_meaningful, check_closed_key_set

PLAN_SCHEMA_VERSION = "1.0.0"

TIER_1 = "TIER_1"
TIER_2 = "TIER_2"
TIER_3 = "TIER_3"

VALID_TIERS = {TIER_1, TIER_2, TIER_3}

SKIP_CODE_UI_ONLY = "SKIP_UI_ONLY"
SKIP_CODE_SLICE_LOCAL_SUFFICIENT = "SKIP_SLICE_LOCAL_SUFFICIENT"
SKIP_CODE_FEATURE_SCOPED = "SKIP_FEATURE_SCOPED"
SKIP_CODE_NON_OBSERVABLE = "SKIP_NON_OBSERVABLE"
SKIP_CODE_NOT_APPLICABLE = "SKIP_NOT_APPLICABLE"

VALID_SKIP_CODES = {
    SKIP_CODE_UI_ONLY,
    SKIP_CODE_SLICE_LOCAL_SUFFICIENT,
    SKIP_CODE_FEATURE_SCOPED,
    SKIP_CODE_NON_OBSERVABLE,
    SKIP_CODE_NOT_APPLICABLE,
}

VALID_CATEGORIES = {
    "unit_test",
    "bdd_scenario",
    "linter",
    "render_check",
    "browser_snapshot",
    "stage_validator",
    "projection_verifier",
    "regression_suite",
    "adversarial_check",
    "ui_observation",
    "gatekeeper",
}

PLAN_ALLOWED_KEYS = {
    "schema_version",
    "feature_slug",
    "slice_id",
    "phase",
    "escalation_tier",
    "diff_files",
    "selected_checks",
    "skipped_checks",
    "warnings",
    "mandatory_gatekeeper",
    "generated_at",
}

# File classification pattern definitions
UI_EXTENSIONS = {".css", ".scss", ".sass", ".less", ".svg"}
UI_PATH_PATTERNS = [
    re.compile(r"^.*\.(css|scss|sass|less|svg)$", re.IGNORECASE),
    re.compile(r"(^|/)(styles|stylesheets|theme|themes)/", re.IGNORECASE),
    re.compile(r"(^|/)components/.*\.module\.(css|scss|less)$", re.IGNORECASE),
    re.compile(r"(^|/)ui/mockups/.*$", re.IGNORECASE),
    re.compile(r"^.*layout\.html$", re.IGNORECASE),
]

FEATURE_SCHEMA_PATTERNS = [
    re.compile(r"(^|/)domain-lexicon\.json$"),
    re.compile(r"(^|/)contract-fixtures/.*$"),
    re.compile(r"(^|/)flow-simulation-matrix\.json$"),
    re.compile(r"(^|/)execution-dag\.json$"),
    re.compile(r"(^|/)slice-invariants\.json$"),
    re.compile(r"(^|/)schemas/.*\.schema\.json$"),
    re.compile(r"(^|/)adapters/.*$"),
    re.compile(r"(^|/)state_machine/.*$"),
]

CORE_SHARED_PATTERNS = [
    re.compile(r"(^|/)lib/.*$"),
    re.compile(r"(^|/)migrations/.*$"),
    re.compile(r"^.*\.sql$"),
    re.compile(r"(^|/)core/.*$"),
    re.compile(r"(^|/)shared/.*$"),
    re.compile(r"(^|/)database/migrations/.*$"),
]

SLICE_LOCAL_PATTERNS = [
    re.compile(r"(^|/)slices/.*$"),
    re.compile(r"(^|/)features/.*$"),
    re.compile(r"(^|/)src/.*$"),
    re.compile(r"(^|/)tests/test_.*\.py$"),
    re.compile(r"(^|/)tests/.*\.test\.(ts|js|tsx|jsx)$"),
    re.compile(r"(^|/)tests/.*\.spec\.(ts|js|tsx|jsx)$"),
]


def now_iso_utc() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_file_diff(file_path: str) -> str:
    """Classify a file diff path into an architectural scope category.

    Categories:
    - 'UI_ONLY': Pure styling, layout templates, or static mockups.
    - 'CORE_SHARED': Shared framework primitives, migrations, core libraries.
    - 'FEATURE_SCHEMA': Feature-wide domain schemas, lexicons, boundary fixtures, adapters.
    - 'SLICE_LOCAL': Slice business logic, entrypoints, slice-specific unit tests.
    - 'UNMAPPED': Ambiguous or unclassified file path requiring conservative escalation.
    """
    p = str(file_path).strip().replace("\\", "/")
    if not p:
        return "UNMAPPED"

    # 1. Check Core Shared libraries first (highest risk)
    for pat in CORE_SHARED_PATTERNS:
        if pat.search(p):
            return "CORE_SHARED"

    # 2. Check Feature Schema / Domain Lexicon / Fixtures / Adapters
    for pat in FEATURE_SCHEMA_PATTERNS:
        if pat.search(p):
            return "FEATURE_SCHEMA"

    # 3. Check UI / Layout / CSS only
    for pat in UI_PATH_PATTERNS:
        if pat.search(p):
            return "UI_ONLY"

    # 4. Check Slice Local business logic & unit tests
    for pat in SLICE_LOCAL_PATTERNS:
        if pat.search(p):
            return "SLICE_LOCAL"

    return "UNMAPPED"


def determine_escalation_tier(
    diff_files: Optional[List[str]] = None,
    stage: Optional[str] = None,
    is_stage_5_exit: bool = False,
    slice_def: Optional[Dict[str, Any]] = None,
    lane: Optional[str] = None,
) -> Tuple[str, List[str], List[str]]:
    """Deterministically determine execution tier, rationale tokens, and warnings.

    Args:
        diff_files: List of modified file paths.
        stage: Current lifecycle state name.
        is_stage_5_exit: Explicit flag indicating Stage 5 delivery exit gating.
        slice_def: Optional slice definition from execution DAG.
        lane: The feature's change lane (state.json `lane`); None means NEW_CAPABILITY.

    Returns:
        Tuple of (escalation_tier, rationale_tokens, warnings).
    """
    warnings: List[str] = []
    rationale_tokens: List[str] = []

    # Rule 1: the Stage 5 delivery exit runs at the lane's tier. NEW_CAPABILITY (the default, and
    # every feature initialised before lanes existed) keeps the mandatory Tier 3 gatekeeper run.
    if is_stage_5_exit or stage in {"STAGE_5_DELIVERY", "COMPLETED"}:
        from lib.triage import LANE_TIER, NEW_CAPABILITY
        lane_tier = LANE_TIER.get(lane or NEW_CAPABILITY)
        if lane_tier is None:
            warnings.append("Unknown lane %r. Conservatively escalating to TIER_3." % lane)
            lane_tier = TIER_3
        if lane_tier == TIER_3:
            rationale_tokens.append("STAGE_5_DELIVERY_EXIT_MANDATORY_TIER_3")
        else:
            rationale_tokens.append("STAGE_5_DELIVERY_EXIT_LANE_%s" % lane)
        return lane_tier, rationale_tokens, warnings

    if not diff_files:
        # Default based on slice definition if provided, else conservative Tier 1/2
        if slice_def:
            legs = slice_def.get("legs", {})
            has_adapter = bool(legs.get("adapter_persistence", {}).get("optional_leg_present"))
            if has_adapter:
                rationale_tokens.append("SLICE_WITH_ADAPTER_BOUNDARY")
                return TIER_2, rationale_tokens, warnings
            rationale_tokens.append("SLICE_LOCAL_DEFAULT")
            return TIER_1, rationale_tokens, warnings
        rationale_tokens.append("DEFAULT_TIER_1_LOCAL")
        return TIER_1, rationale_tokens, warnings

    categories: Set[str] = set()
    unmapped_files: List[str] = []

    for f in diff_files:
        cat = classify_file_diff(f)
        categories.add(cat)
        if cat == "UNMAPPED":
            unmapped_files.append(f)

    # Rule 2: Conservative escalation on unmapped paths or ambiguous diffs
    if "UNMAPPED" in categories:
        for uf in unmapped_files:
            warnings.append(
                f"Unmapped path or ambiguous dependency detected: '{uf}'. "
                f"Conservatively escalating verification execution to TIER_3."
            )
        rationale_tokens.append("UNMAPPED_DIFF_CONSERVATIVE_ESCALATION")
        return TIER_3, rationale_tokens, warnings

    # Rule 3: Core shared library / datastore migrations trigger Tier 3
    if "CORE_SHARED" in categories:
        rationale_tokens.append("CORE_SHARED_LIBRARY_MUTATION")
        return TIER_3, rationale_tokens, warnings

    # Rule 4: Domain schema, lexicon, or contract fixtures trigger Tier 2
    if "FEATURE_SCHEMA" in categories:
        rationale_tokens.append("FEATURE_SCHEMA_MUTATION")
        return TIER_2, rationale_tokens, warnings

    # Rule 5: Pure UI / CSS / Layout changes trigger Tier 1 UI-only
    if categories == {"UI_ONLY"}:
        rationale_tokens.append("UI_LAYOUT_CSS_ONLY")
        return TIER_1, rationale_tokens, warnings

    # Rule 6: Slice business logic & entrypoint changes trigger Tier 1
    if categories == {"SLICE_LOCAL"} or categories == {"SLICE_LOCAL", "UI_ONLY"}:
        rationale_tokens.append("SLICE_BUSINESS_LOGIC_LOCAL")
        return TIER_1, rationale_tokens, warnings

    # Default fallback: Conservative escalation to Tier 3
    warnings.append("Ambiguous multi-category diff combination. Conservatively escalating to TIER_3.")
    rationale_tokens.append("AMBIGUOUS_DIFF_CONSERVATIVE_ESCALATION")
    return TIER_3, rationale_tokens, warnings


def generate_verification_plan(
    feature_dir: Path,
    slice_id: Optional[str] = None,
    diff_files: Optional[List[str]] = None,
    stage: Optional[str] = None,
    is_stage_5_exit: bool = False,
    slice_def: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a structured, deterministic verification plan.

    Args:
        feature_dir: Path to feature directory.
        slice_id: Optional target slice identifier.
        diff_files: Optional list of modified files.
        stage: Current pipeline stage.
        is_stage_5_exit: Whether this is a Stage 5 delivery exit run.
        slice_def: Optional slice definition from execution DAG.

    Returns:
        Structured dictionary matching schemas/verification-plan.schema.json.
    """
    feature_dir = Path(feature_dir).resolve()
    state_file = feature_dir / "state.json"
    feature_slug = feature_dir.name
    lane = None

    if state_file.is_file():
        try:
            with open(state_file, "r", encoding="utf-8") as sf:
                sdata = json.load(sf)
                feature_slug = sdata.get("feature_slug") or feature_slug
                if stage is None:
                    stage = sdata.get("current_state")
                lane = sdata.get("lane")
        except Exception:
            pass

    files_to_eval = [str(x) for x in (diff_files or [])]
    tier, tokens, warnings = determine_escalation_tier(
        diff_files=files_to_eval,
        stage=stage,
        is_stage_5_exit=is_stage_5_exit,
        slice_def=slice_def,
        lane=lane,
    )

    is_mandatory_gatekeeper = (tier == TIER_3 and (is_stage_5_exit or stage == "STAGE_5_DELIVERY"))

    selected_checks: List[Dict[str, Any]] = []
    skipped_checks: List[Dict[str, Any]] = []

    slice_tag = slice_id or "feature"

    if tier == TIER_1:
        if "UI_LAYOUT_CSS_ONLY" in tokens:
            # UI / Layout / CSS only: render checks & browser snapshots only
            selected_checks.append({
                "command": f"python3 bin/render-ui-mockup --feature {feature_slug}",
                "rationale": "Verify UI template rendering and styling integrity for layout changes.",
                "rationale_token": "RENDER_SNAPSHOT",
                "category": "render_check",
            })
            selected_checks.append({
                "command": f"python3 bin/observe-rendered-ui --feature {feature_slug} --target mockups/index.html",
                "rationale": "Observe rendered DOM structure and verify visual component constraints.",
                "rationale_token": "BROWSER_SNAPSHOT",
                "category": "browser_snapshot",
            })
            # Explicit skips with SKIP_UI_ONLY
            skipped_checks.append({
                "suite_name": "backend_integration_suite",
                "reason_code": SKIP_CODE_UI_ONLY,
                "reason_description": "Pure UI styling and layout modifications do not touch backend business logic or data contracts.",
            })
            skipped_checks.append({
                "suite_name": "adversarial_fuzz_checks",
                "reason_code": SKIP_CODE_UI_ONLY,
                "reason_description": "CSS and layout changes are isolated from API failure envelopes and adversarial contract boundaries.",
            })
            skipped_checks.append({
                "suite_name": "datastore_migration_verifier",
                "reason_code": SKIP_CODE_UI_ONLY,
                "reason_description": "Styling changes do not modify database schemas, transaction boundaries, or persistent state.",
            })
        else:
            # Slice business logic / entrypoint
            selected_checks.append({
                "command": f"pytest tests/unit/{slice_tag} -q",
                "rationale": "Inner-loop execution of slice-specific unit tests verifying isolated pure logic modules.",
                "rationale_token": "SLICE_UNIT_TEST",
                "category": "unit_test",
            })
            selected_checks.append({
                "command": f"pytest tests/bdd/test_{slice_tag}.py -q",
                "rationale": "Targeted execution of slice BDD scenarios validating local flow expectations.",
                "rationale_token": "SLICE_BDD_SCENARIO",
                "category": "bdd_scenario",
            })
            selected_checks.append({
                "command": f"flake8 src/{slice_tag} tests/{slice_tag}",
                "rationale": "Static linting and style conformance verification across modified slice files.",
                "rationale_token": "STATIC_LINTER",
                "category": "linter",
            })
            # Explicit skips with SKIP_SLICE_LOCAL_SUFFICIENT
            skipped_checks.append({
                "suite_name": "monolithic_regression_suite",
                "reason_code": SKIP_CODE_SLICE_LOCAL_SUFFICIENT,
                "reason_description": "Slice modifications are fully encapsulated within local pure modules and covered by slice-specific test suites.",
            })
            skipped_checks.append({
                "suite_name": "adversarial_redteam_suite",
                "reason_code": SKIP_CODE_SLICE_LOCAL_SUFFICIENT,
                "reason_description": "Global adversarial security probes are deferred to Stage 5 delivery exit gating.",
            })

    elif tier == TIER_2:
        # Affected-Feature: executes all feature BDD scenarios, stage validators, and projection verifiers
        selected_checks.append({
            "command": f"pytest tests/unit/ -q",
            "rationale": "Execution of all unit test suites across the feature boundary.",
            "rationale_token": "FEATURE_UNIT_TESTS",
            "category": "unit_test",
        })
        selected_checks.append({
            "command": f"pytest tests/bdd/ -q",
            "rationale": "Execution of all feature BDD scenarios to verify state-machine transitions and adapter integrations.",
            "rationale_token": "FEATURE_BDD_ALL",
            "category": "bdd_scenario",
        })
        selected_checks.append({
            "command": f"python3 bin/validate-lexicon {feature_dir}/domain-lexicon.json",
            "rationale": "Deterministic validation of domain entity bindings, taxonomy precision, and boundary aliases.",
            "rationale_token": "VALIDATE_LEXICON",
            "category": "stage_validator",
        })
        selected_checks.append({
            "command": f"python3 bin/validate-scenario-matrix {feature_dir}/flow-simulation-matrix.json",
            "rationale": "Deterministic validation of simulation matrix branch coverages and transition egresses.",
            "rationale_token": "VALIDATE_SCENARIO_MATRIX",
            "category": "stage_validator",
        })
        selected_checks.append({
            "command": f"python3 bin/verify-projections {feature_dir}/projections.json {feature_dir}/flow-simulation-matrix.json",
            "rationale": "Verification of observable projection fingerprints against simulated prediction claims.",
            "rationale_token": "VERIFY_PROJECTIONS",
            "category": "projection_verifier",
        })
        # Explicit skips with SKIP_FEATURE_SCOPED
        skipped_checks.append({
            "suite_name": "multi_feature_cross_system_gatekeeper",
            "reason_code": SKIP_CODE_FEATURE_SCOPED,
            "reason_description": "Modifications are strictly contained within feature boundaries; global multi-feature gatekeeper suites are reserved for delivery exit.",
        })
        skipped_checks.append({
            "suite_name": "adversarial_redteam_suite",
            "reason_code": SKIP_CODE_FEATURE_SCOPED,
            "reason_description": "Comprehensive adversarial red-team fuzzing is deferred to Stage 5 delivery exit gating.",
        })

    else:
        # Tier 3: Full-Suite & Gatekeeper
        selected_checks.append({
            "command": "pytest tests/ -q",
            "rationale": "Full regression test suite execution across all unit, integration, and contract tests.",
            "rationale_token": "FULL_REGRESSION_SUITE",
            "category": "regression_suite",
        })
        selected_checks.append({
            "command": "python3 tests/test_surrogate_verification.py",
            "rationale": "Adversarial red-team measurement suite refuting fabrication and surrogate bypass defects.",
            "rationale_token": "ADVERSARIAL_MEASUREMENT_SUITE",
            "category": "adversarial_check",
        })
        selected_checks.append({
            "command": f"python3 bin/milestone-harness {feature_dir}",
            "rationale": "Verification of vertical milestone flow, variance coverage, and provisional fixture discharge.",
            "rationale_token": "MILESTONE_HARNESS_GATEKEEPER",
            "category": "gatekeeper",
        })
        selected_checks.append({
            "command": f"python3 bin/verify-evidence {feature_dir}",
            "rationale": "Stage 5 gatekeeper verification of captured evidence across all declared media and projection claims.",
            "rationale_token": "DELIVERY_EVIDENCE_GATEKEEPER",
            "category": "gatekeeper",
        })
        if is_mandatory_gatekeeper or stage == "STAGE_5_DELIVERY":
            selected_checks.append({
                "command": f"python3 bin/observe-rendered-ui --feature {feature_slug} --headless",
                "rationale": "Mandatory Playwright headless browser observation of rendered UI states at Stage 5 exit.",
                "rationale_token": "PLAYWRIGHT_UI_OBSERVATION",
                "category": "ui_observation",
            })
        # In Tier 3, no core verification suites are skipped

    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "feature_slug": feature_slug,
        "slice_id": slice_id,
        "phase": stage,
        "escalation_tier": tier,
        "diff_files": files_to_eval,
        "selected_checks": selected_checks,
        "skipped_checks": skipped_checks,
        "warnings": warnings,
        "mandatory_gatekeeper": is_mandatory_gatekeeper,
        "generated_at": now_iso_utc(),
    }

    return plan


def validate_verification_plan(plan: Dict[str, Any]) -> Tuple[bool, str, List[str]]:
    """Validate a verification plan dictionary against policy requirements.

    Returns:
        Tuple of (is_valid, token, errors)
    """
    errors: List[str] = []

    if not isinstance(plan, dict):
        return False, "INVALID_PLAN_STRUCTURE", ["Verification plan must be a JSON dictionary object."]

    closed_res = check_closed_key_set(plan, PLAN_ALLOWED_KEYS, "Verification plan")
    if not closed_res.is_valid:
        return False, "UNKNOWN_PLAN_ATTRIBUTE", [closed_res.message]

    required_keys = [
        "schema_version",
        "feature_slug",
        "escalation_tier",
        "selected_checks",
        "skipped_checks",
        "diff_files",
        "warnings",
        "mandatory_gatekeeper",
        "generated_at",
    ]
    for rk in required_keys:
        if rk not in plan:
            errors.append(f"Missing required attribute: '{rk}'")

    if errors:
        return False, "MISSING_REQUIRED_FIELD", errors

    tier = plan.get("escalation_tier")
    if tier not in VALID_TIERS:
        errors.append(f"Invalid escalation_tier '{tier}'; allowed values are: {sorted(list(VALID_TIERS))}")

    selected = plan.get("selected_checks", [])
    if not isinstance(selected, list):
        errors.append("selected_checks must be an array.")
    else:
        for idx, chk in enumerate(selected):
            if not isinstance(chk, dict):
                errors.append(f"selected_checks[{idx}] must be an object.")
                continue
            if not chk.get("command"):
                errors.append(f"selected_checks[{idx}] missing 'command'.")
            rat = chk.get("rationale")
            if not is_reason_meaningful(rat):
                errors.append(f"selected_checks[{idx}] rationale '{rat}' is not substantive meaningful prose.")
            cat = chk.get("category")
            if cat not in VALID_CATEGORIES:
                errors.append(f"selected_checks[{idx}] invalid category '{cat}'.")

    skipped = plan.get("skipped_checks", [])
    if not isinstance(skipped, list):
        errors.append("skipped_checks must be an array.")
    else:
        for idx, sk in enumerate(skipped):
            if not isinstance(sk, dict):
                errors.append(f"skipped_checks[{idx}] must be an object.")
                continue
            if not sk.get("suite_name"):
                errors.append(f"skipped_checks[{idx}] missing 'suite_name'.")
            code = sk.get("reason_code")
            if code not in VALID_SKIP_CODES:
                errors.append(f"skipped_checks[{idx}] invalid reason_code '{code}'; allowed: {sorted(list(VALID_SKIP_CODES))}")
            desc = sk.get("reason_description")
            if not is_reason_meaningful(desc):
                errors.append(
                    f"skipped_checks[{idx}] reason_description '{desc}' is vacuous or not substantive meaningful prose."
                )

    # Invariant: If mandatory_gatekeeper is True, escalation_tier must be TIER_3 and no gatekeeper checks may be skipped
    if plan.get("mandatory_gatekeeper") is True:
        if tier != TIER_3:
            errors.append("mandatory_gatekeeper is True but escalation_tier is not TIER_3.")
        for sk in skipped:
            if "gatekeeper" in sk.get("suite_name", "").lower():
                errors.append(f"Mandatory gatekeeper run cannot skip gatekeeper suite '{sk.get('suite_name')}'.")

    if errors:
        return False, "VERIFICATION_PLAN_INVALID", errors

    return True, "PLAN_VALID", []
