"""Stage 5 delivery reading, and the two independent evidence axes.

Owner: stage-5. Imported by `bin/verify-evidence` and `bin/generate-changelog` so the axes are
defined once; a primitive that exists twice is fixed once, which is how the same blacklist
survived remediation in two earlier stages of this build.

The two axes are independent and that is the point. `verify_medium` asks whether the artifact the
milestone attached is the *kind* of thing the plan said would be captured: it normalises the
capture under the medium the execution DAG declares, using the same normalisation table Stage 4
captured it with, and refuses when the artifact cannot be normalised that way at all.
`verify_claim` asks a different question of the same file — whether the capture carries the
*structure* the criterion needs. A rendered_ui snapshot passes the first and fails the second for
a layout claim, because a harness with no layout engine serialises structure and text and
produces the same snapshot at every viewport.

Nothing here reads a verdict. There is no key for admissibility, for sufficiency, for an
unresolved count or for a verified status: admissibility is computed from what the capture
envelope actually carries, the unresolved counts are computed by differencing the intent sections
against their own resolutions, and the medium is the one the plan declared upstream rather than
the one the report declares about itself. Every vocabulary the report draws on — the five intent
headers, the five claim kinds, the six media, and the key set of every object — is closed, so a
new attribute cannot be introduced and then quietly believed.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .arithmetic import verify_arithmetic_consistency
from .ground_truth import verify_ground_truth_reconciliation
from .normalisation import (
    CANONICAL_MEDIA,
    NormalisationRefused,
    capture_projection,
)
from .observation import verify_rendered_ui_observation
from .primitives import check_closed_key_set, is_reason_meaningful, is_vacuous_container

# ---------------------------------------------------------------------------
# The closed vocabularies of Stage 5
# ---------------------------------------------------------------------------

# §2 Stage 5 step 1. Five headers, closed. An invented header is how an unaddressed issue gets
# filed as a [Note] and walks past the [Blocker] gate, so anything outside this tuple is refused.
INTENT_HEADERS = (
    "[Decision Needed]",
    "[Risk]",
    "[Accepted Constraint]",
    "[Milestone]",
    "[Blocker]",
)

# The two headers §4 makes blocking for STAGE_5_DELIVERY -> COMPLETED.
BLOCKING_HEADERS = ("[Decision Needed]", "[Blocker]")

# §2 Stage 5 step 3. Five claim kinds, closed, one per row of the claim table.
CLAIM_KINDS = (
    "state_observable_and_distinct",
    "layout_viewport_floor_overflow",
    "traversal_and_focus_order",
    "timing_deadline_temporal_egress",
    "value_correctness",
)

REPORT_KEYS = (
    "$schema",
    "schema_version",
    "feature_slug",
    "delivered_at",
    "intent_sections",
    "milestone_evidence_records",
)

_COMMON_SECTION_KEYS = ("header", "title", "summary")

# Per header, the keys that header may carry beyond the three common ones. A section is checked
# against its own header's set, so a [Risk] cannot borrow discharged_claim_ids to wave a claim
# through and a [Milestone] cannot carry a resolution.
SECTION_KEYS = {
    "[Decision Needed]": _COMMON_SECTION_KEYS + ("options", "resolution"),
    "[Risk]": _COMMON_SECTION_KEYS + ("mitigation",),
    "[Accepted Constraint]": _COMMON_SECTION_KEYS
    + ("alternative_verification", "discharged_claim_ids"),
    "[Milestone]": _COMMON_SECTION_KEYS + ("milestone_refs",),
    "[Blocker]": _COMMON_SECTION_KEYS + ("resolution",),
}

OPTION_KEYS = ("option_id", "impact")
RESOLUTION_KEYS = ("chosen_option_id", "rationale")

# An evidence record says which milestone it discharges, where the capture is, and which claims
# the capture is offered for. It carries no medium of its own: the medium is what the execution
# DAG declared for that milestone, so a report cannot move its own goalposts by restating it.
EVIDENCE_RECORD_KEYS = ("milestone_id", "slice_id", "artifact_path", "claims_evaluated")

# A claim entry names the claim and its kind. There is no admissibility key and no status key:
# both are computed from the capture envelope below.
CLAIM_ENTRY_KEYS = ("claim_id", "claim_kind", "evidence_key")

# The capture envelope on disk. `captured_artifact` is exactly what Stage 4 captured for the
# medium; the remaining keys are the claim-axis records a harness produces only when it is
# actually capable of the measurement in question.
ENVELOPE_KEYS = (
    "captured_artifact",
    "recorded_fixture_values",
    "layout_capture",
    "focus_sequence",
    "time_travel",
    "value_bindings",
    "observation_target",
)

LAYOUT_CAPTURE_KEYS = ("viewport", "boxes")
VIEWPORT_KEYS = ("width", "height")
BOX_KEYS = ("selector", "width", "height", "overflow_x")
FOCUS_STEP_KEYS = ("step", "target")
TIME_TRAVEL_KEYS = ("advanced_by_ms", "terminal_transition", "external_events")
TRANSITION_KEYS = ("from_state", "to_state")
VALUE_BINDING_KEYS = ("domain_term", "projection_binding", "asserted_value")

# The DAG's per-milestone plan: what Stage 3 committed to verifying, and how.
DAG_MILESTONE_KEYS = ("milestone_id", "verification_medium", "claims")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    if not path.is_file():
        return None, "no such file: %s" % path
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except ValueError as error:
        return None, "%s is not valid JSON: %s" % (path, error)


class PlannedMilestone(NamedTuple):
    """One milestone as the execution DAG committed to it, upstream of the delivery report."""

    milestone_id: str
    slice_id: str
    verification_medium: str
    claims: Tuple[str, ...]


def read_report(path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Load a delivery report, refusing any attribute outside its closed key set."""
    document, error = load_json(path)
    if error is not None:
        return None, ["MISSING_GATE_ARTIFACT: %s" % error]
    if not isinstance(document, dict) or is_vacuous_container(document):
        return None, ["MISSING_GATE_ARTIFACT: %s carries no delivery report" % path]

    closed = check_closed_key_set(document, REPORT_KEYS, label="delivery report")
    if not closed.is_valid:
        return None, ["UNKNOWN_DELIVERY_ATTRIBUTE: %s" % closed.message]

    sections = document.get("intent_sections")
    if is_vacuous_container(sections) or not isinstance(sections, list):
        return None, [
            "MISSING_GATE_ARTIFACT: the delivery report carries no intent section with any content"
        ]
    return document, []


def read_planned_milestones(path: Path) -> Tuple[Dict[str, PlannedMilestone], List[str]]:
    """The milestone census the execution DAG committed to, keyed by milestone id.

    The census is the plan's, not the report's. Differencing the report against it is what makes
    a milestone that was quietly dropped from delivery visible at all.
    """
    document, error = load_json(path)
    if error is not None:
        return {}, ["MISSING_GATE_ARTIFACT: %s" % error]
    if not isinstance(document, dict):
        return {}, ["MISSING_GATE_ARTIFACT: %s is not an execution DAG" % path]

    slices = document.get("slices")
    if is_vacuous_container(slices) or not isinstance(slices, list):
        return {}, ["MISSING_GATE_ARTIFACT: the execution DAG declares no slice with any content"]

    planned: Dict[str, PlannedMilestone] = {}
    problems: List[str] = []
    for index, slice_entry in enumerate(slices):
        if not isinstance(slice_entry, dict):
            problems.append("MISSING_GATE_ARTIFACT: slices[%d] records nothing" % index)
            continue
        slice_id = str(slice_entry.get("slice_id") or "").strip()
        milestones = slice_entry.get("milestones")
        if is_vacuous_container(milestones) or not isinstance(milestones, list):
            problems.append(
                "MISSING_GATE_ARTIFACT: slice %r declares no milestone carrying any content, so "
                "there is nothing for Stage 5 to require evidence of" % (slice_id or index)
            )
            continue
        for position, milestone in enumerate(milestones):
            label = "slice %r milestones[%d]" % (slice_id, position)
            if not isinstance(milestone, dict) or is_vacuous_container(milestone):
                problems.append("MISSING_GATE_ARTIFACT: %s records nothing" % label)
                continue
            closed = check_closed_key_set(milestone, DAG_MILESTONE_KEYS, label=label)
            if not closed.is_valid:
                problems.append("UNKNOWN_DELIVERY_ATTRIBUTE: %s" % closed.message)
                continue
            milestone_id = str(milestone.get("milestone_id") or "").strip()
            if not milestone_id:
                problems.append("MISSING_GATE_ARTIFACT: %s names no milestone" % label)
                continue
            medium = str(milestone.get("verification_medium") or "").strip()
            if medium not in CANONICAL_MEDIA:
                problems.append(
                    "UNKNOWN_VERIFICATION_MEDIUM: milestone %s declares medium %r; the table "
                    "carries a closed set of six: %s"
                    % (milestone_id, medium, ", ".join(CANONICAL_MEDIA))
                )
                continue
            raw_claims = milestone.get("claims")
            if is_vacuous_container(raw_claims) or not isinstance(raw_claims, list):
                problems.append(
                    "MISSING_GATE_ARTIFACT: milestone %s commits to no claim carrying any "
                    "content, so its evidence would answer nothing" % milestone_id
                )
                continue
            claims = tuple(str(claim).strip() for claim in raw_claims if str(claim).strip())
            if len(claims) != len(raw_claims):
                problems.append(
                    "MISSING_GATE_ARTIFACT: milestone %s lists a claim with no identifier"
                    % milestone_id
                )
                continue
            if milestone_id in planned:
                problems.append(
                    "DUPLICATE_MILESTONE_ID: %s is declared by two slices" % milestone_id
                )
                continue
            planned[milestone_id] = PlannedMilestone(milestone_id, slice_id, medium, claims)

    if not planned and not problems:
        problems.append("MISSING_GATE_ARTIFACT: the execution DAG declares no milestone")
    return planned, problems


def read_envelope(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Load one capture envelope, refusing any attribute outside its closed key set."""
    document, error = load_json(path)
    if error is not None:
        return None, error
    if not isinstance(document, dict) or is_vacuous_container(document):
        return None, "%s carries no capture envelope" % path
    closed = check_closed_key_set(document, ENVELOPE_KEYS, label="capture envelope %s" % path.name)
    if not closed.is_valid:
        return None, closed.message
    if is_vacuous_container(document.get("captured_artifact")):
        return None, "%s captured nothing for the milestone to be evidenced by" % path.name
    return document, None


def sections_with_header(report: Dict[str, Any], header: str) -> List[Dict[str, Any]]:
    return [
        section
        for section in report.get("intent_sections", [])
        if isinstance(section, dict) and section.get("header") == header
    ]


def evidence_records(report: Dict[str, Any]) -> List[Any]:
    records = report.get("milestone_evidence_records")
    return records if isinstance(records, list) else []


# ---------------------------------------------------------------------------
# Axis 0: the intent header linter
# ---------------------------------------------------------------------------

def _list_of_identifiers(value: Any) -> Optional[List[str]]:
    """A non-vacuous list whose every element is a non-empty identifier string, or None."""
    if not isinstance(value, list) or is_vacuous_container(value):
        return None
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        out.append(item.strip())
    return out


def lint_intent(report: Dict[str, Any]) -> List[str]:
    """Every intent section draws its header from the closed five and carries that header's body.

    The header check is a positive vocabulary, not a list of forbidden headings: the set of
    headings someone could invent is unbounded, so only the five can be allowed and everything
    else refused.
    """
    problems: List[str] = []
    for index, section in enumerate(report.get("intent_sections", [])):
        label = "intent_sections[%d]" % index
        if not isinstance(section, dict) or is_vacuous_container(section):
            problems.append("INVALID_INTENT_HEADER: %s records nothing" % label)
            continue

        header = section.get("header")
        if header not in INTENT_HEADERS:
            problems.append(
                "INVALID_INTENT_HEADER: %s uses header %r; Stage 5 carries a closed vocabulary of "
                "five: %s" % (label, header, ", ".join(INTENT_HEADERS))
            )
            continue

        closed = check_closed_key_set(section, SECTION_KEYS[header], label="%s %s" % (label, header))
        if not closed.is_valid:
            problems.append("INVALID_INTENT_HEADER: %s" % closed.message)
            continue

        if is_vacuous_container(section.get("title")):
            problems.append("INVALID_INTENT_HEADER: %s %s carries no title" % (label, header))
        if not is_reason_meaningful(section.get("summary")):
            problems.append(
                "INVALID_INTENT_HEADER: %s %s carries no flow-level summary a reader could act on"
                % (label, header)
            )

        problems.extend(_lint_section_body(label, header, section))
    return problems


def _lint_section_body(label: str, header: str, section: Dict[str, Any]) -> List[str]:
    """The obligations each header carries beyond a title and a summary."""
    problems: List[str] = []

    if header == "[Milestone]":
        if _list_of_identifiers(section.get("milestone_refs")) is None:
            problems.append(
                "INVALID_INTENT_HEADER: %s [Milestone] names no completed milestone; a milestone "
                "section is the flow slice its evidence discharges" % label
            )

    if header == "[Accepted Constraint]":
        if _list_of_identifiers(section.get("discharged_claim_ids")) is None:
            problems.append(
                "INVALID_INTENT_HEADER: %s [Accepted Constraint] names no claim it discharges, so "
                "nothing records what was not verified" % label
            )
        if not is_reason_meaningful(section.get("alternative_verification")):
            problems.append(
                "INVALID_INTENT_HEADER: %s [Accepted Constraint] does not say how the claim was "
                "checked instead; naming the limitation alone is not the escape hatch" % label
            )

    if header == "[Decision Needed]":
        options = section.get("options")
        if not isinstance(options, list) or is_vacuous_container(options) or len(options) < 2:
            problems.append(
                "INVALID_INTENT_HEADER: %s [Decision Needed] offers fewer than two options; a "
                "flow-level trade-off is a choice between stated alternatives" % label
            )
        else:
            for position, option in enumerate(options):
                option_label = "%s options[%d]" % (label, position)
                if not isinstance(option, dict) or is_vacuous_container(option):
                    problems.append("INVALID_INTENT_HEADER: %s records nothing" % option_label)
                    continue
                closed = check_closed_key_set(option, OPTION_KEYS, label=option_label)
                if not closed.is_valid:
                    problems.append("INVALID_INTENT_HEADER: %s" % closed.message)
                    continue
                if is_vacuous_container(option.get("option_id")):
                    problems.append("INVALID_INTENT_HEADER: %s names no option" % option_label)
                if not is_reason_meaningful(option.get("impact")):
                    problems.append(
                        "INVALID_INTENT_HEADER: %s states no impact; an option without its "
                        "consequence is not a choice anyone can make" % option_label
                    )

    if "resolution" in section:
        problems.extend(_lint_resolution(label, header, section))
    return problems


def _lint_resolution(label: str, header: str, section: Dict[str, Any]) -> List[str]:
    """A resolution is well formed, and for a decision it names one of that decision's options."""
    problems: List[str] = []
    resolution = section.get("resolution")
    if not isinstance(resolution, dict) or is_vacuous_container(resolution):
        problems.append("INVALID_INTENT_HEADER: %s %s records an empty resolution" % (label, header))
        return problems
    closed = check_closed_key_set(resolution, RESOLUTION_KEYS, label="%s resolution" % label)
    if not closed.is_valid:
        problems.append("INVALID_INTENT_HEADER: %s" % closed.message)
        return problems
    if not is_reason_meaningful(resolution.get("rationale")):
        problems.append(
            "INVALID_INTENT_HEADER: %s %s records a resolution with no rationale" % (label, header)
        )
    if header == "[Decision Needed]":
        chosen = str(resolution.get("chosen_option_id") or "").strip()
        option_ids = {
            str(option.get("option_id") or "").strip()
            for option in section.get("options", [])
            if isinstance(option, dict)
        }
        if chosen not in option_ids or not chosen:
            problems.append(
                "INVALID_INTENT_HEADER: %s [Decision Needed] resolves to %r, which is not one of "
                "the options it offered (%s)" % (label, chosen, ", ".join(sorted(option_ids)))
            )
    return problems


# ---------------------------------------------------------------------------
# The §4 transition gate: unresolved decisions and blockers
# ---------------------------------------------------------------------------

def _is_resolved(section: Dict[str, Any]) -> bool:
    """Whether a blocking section carries a resolution the linter would accept.

    Whether a blocker has actually been cleared is an extrinsic fact about the world that no
    upstream artifact can prove, so it is recorded as a declaration — but the declaration has to
    be a resolution with a rationale, and for a decision it has to name one of the options the
    section itself offered. A bare truthy flag would be the one-word bypass this build banned.
    """
    if not isinstance(section.get("resolution"), dict):
        return False
    return not _lint_resolution("gate", str(section.get("header")), section)


def check_gates(report: Dict[str, Any]) -> List[str]:
    """§4: an unresolved [Decision Needed] or [Blocker] blocks STAGE_5_DELIVERY -> COMPLETED.

    The counts are differenced from the sections themselves rather than read from the report,
    because a report that states its own unresolved count states the verdict the gate exists for.
    """
    problems: List[str] = []
    codes = {
        "[Decision Needed]": "UNRESOLVED_DECISION_NEEDED",
        "[Blocker]": "UNRESOLVED_BLOCKER",
    }
    for header in BLOCKING_HEADERS:
        outstanding = [
            section for section in sections_with_header(report, header) if not _is_resolved(section)
        ]
        if not outstanding:
            continue
        problems.append(
            "%s: %d %s section(s) are still open, and §4 refuses STAGE_5_DELIVERY -> COMPLETED "
            "while any remains" % (codes[header], len(outstanding), header)
        )
        for section in outstanding:
            problems.append("  %s" % (section.get("title") or "<untitled>"))
    return problems


# ---------------------------------------------------------------------------
# Axis 1: the medium
# ---------------------------------------------------------------------------

class MilestoneEvidence(NamedTuple):
    """One milestone's evidence, read and normalised against the medium the plan declared."""

    planned: PlannedMilestone
    record: Dict[str, Any]
    envelope: Dict[str, Any]
    normalised: str


def _record_index(report: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """The report's evidence records keyed by milestone, each checked against its closed key set."""
    by_milestone: Dict[str, Dict[str, Any]] = {}
    problems: List[str] = []
    for index, record in enumerate(evidence_records(report)):
        label = "milestone_evidence_records[%d]" % index
        if not isinstance(record, dict) or is_vacuous_container(record):
            problems.append("MISSING_MILESTONE_EVIDENCE: %s records nothing" % label)
            continue
        closed = check_closed_key_set(record, EVIDENCE_RECORD_KEYS, label=label)
        if not closed.is_valid:
            problems.append("UNKNOWN_DELIVERY_ATTRIBUTE: %s" % closed.message)
            continue
        milestone_id = str(record.get("milestone_id") or "").strip()
        if not milestone_id:
            problems.append("MISSING_MILESTONE_EVIDENCE: %s names no milestone" % label)
            continue
        if milestone_id in by_milestone:
            problems.append(
                "MISSING_MILESTONE_EVIDENCE: milestone %s carries two evidence records, so which "
                "artifact the gate reads is undefined" % milestone_id
            )
            continue
        by_milestone[milestone_id] = record
    return by_milestone, problems


def verify_medium(
    report: Dict[str, Any],
    planned: Dict[str, PlannedMilestone],
    base_dir: Path,
) -> Tuple[List[MilestoneEvidence], List[str]]:
    """Step 2: one evidence artifact per milestone, of the kind the declared medium admits.

    The medium is the execution DAG's. The artifact is normalised through the same table Stage 4
    captured it with, so the check is whether this file can be read as that medium at all — a
    process result offered where the plan declared a rendering has no exit code to normalise, and
    a rendering offered where the plan declared a process result is not a dict with one.
    """
    by_milestone, problems = _record_index(report)
    verified: List[MilestoneEvidence] = []

    for milestone_id in sorted(planned):
        plan = planned[milestone_id]
        record = by_milestone.get(milestone_id)
        if record is None:
            problems.append(
                "MISSING_MILESTONE_EVIDENCE: the plan commits milestone %s of slice %s to a %s "
                "verification and the delivery report attaches no evidence for it"
                % (milestone_id, plan.slice_id, plan.verification_medium)
            )
            continue

        artifact_path = str(record.get("artifact_path") or "").strip()
        if not artifact_path:
            problems.append(
                "MISSING_MILESTONE_EVIDENCE: milestone %s names no evidence artifact" % milestone_id
            )
            continue
        resolved = base_dir / artifact_path
        envelope, error = read_envelope(resolved)
        if envelope is None:
            problems.append(
                "MISSING_MILESTONE_EVIDENCE: milestone %s attaches %s, which is not a readable "
                "capture envelope (%s)" % (milestone_id, artifact_path, error)
            )
            continue

        try:
            normalised, _fingerprint = capture_projection(
                plan.verification_medium,
                envelope["captured_artifact"],
                recorded_values=envelope.get("recorded_fixture_values") or (),
            )
        except NormalisationRefused as refused:
            if refused.code == "UNNORMALISABLE_ARTIFACT":
                problems.append(
                    "EVIDENCE_MEDIUM_MISMATCH: the plan verifies milestone %s through %s, and the "
                    "capture in %s cannot be read as one — %s"
                    % (milestone_id, plan.verification_medium, artifact_path, refused.detail)
                )
            else:
                problems.append("MISSING_MILESTONE_EVIDENCE: milestone %s — %s" % (
                    milestone_id, refused.detail,
                ))
            continue

        if is_vacuous_container(normalised):
            problems.append(
                "MISSING_MILESTONE_EVIDENCE: milestone %s normalised to nothing observable"
                % milestone_id
            )
            continue
        verified.append(MilestoneEvidence(plan, record, envelope, normalised))

    for milestone_id in sorted(set(by_milestone) - set(planned)):
        problems.append(
            "UNPLANNED_MILESTONE_EVIDENCE: the report attaches evidence for %s, which the "
            "execution DAG never committed to" % milestone_id
        )
    return verified, problems


# ---------------------------------------------------------------------------
# Axis 2: the claim
# ---------------------------------------------------------------------------

_ID_SELECTOR = re.compile(r"#([A-Za-z0-9_\-:]+)")
_CLASS_SELECTOR = re.compile(r"\.([A-Za-z0-9_\-]+)")
_TAG_SELECTOR = re.compile(r"^([A-Za-z][A-Za-z0-9]*)")


def markup_carries(selector: str, markup: Any) -> bool:
    """Whether a selector names something the captured markup actually rendered.

    Ties a geometry or focus record to the same render it claims to measure. A measurement of an
    element that is not in the snapshot is a measurement of something else.
    """
    if not isinstance(markup, str):
        return False
    target = selector.strip()
    if not target:
        return False
    identifier = _ID_SELECTOR.search(target)
    if identifier and ('id="%s"' % identifier.group(1)) not in markup \
            and ("id='%s'" % identifier.group(1)) not in markup:
        return False
    for css_class in _CLASS_SELECTOR.findall(target):
        if css_class not in markup:
            return False
    tag = _TAG_SELECTOR.match(target)
    if tag and ("<%s" % tag.group(1)) not in markup:
        return False
    return bool(identifier or _CLASS_SELECTOR.findall(target) or tag)


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _admits_layout(evidence: MilestoneEvidence) -> Optional[str]:
    """Capture from a runtime with a layout engine at the declared dimension.

    Returns None when admissible, or the reason it is not. A snapshot serialises structure and
    text and carries no geometry at all, which is why a harness with no layout engine produces
    the same file at every viewport and cannot evidence a viewport floor.
    """
    layout = evidence.envelope.get("layout_capture")
    if layout is None:
        return (
            "the capture carries no layout measurement, so the harness that produced it recorded "
            "no geometry — a snapshot of structure and text reads identically at every viewport"
        )
    if not isinstance(layout, dict) or is_vacuous_container(layout):
        return "the layout capture records nothing"
    closed = check_closed_key_set(layout, LAYOUT_CAPTURE_KEYS, label="layout capture")
    if not closed.is_valid:
        return closed.message

    viewport = layout.get("viewport")
    if not isinstance(viewport, dict):
        return "the layout capture declares no viewport dimension it was measured at"
    closed = check_closed_key_set(viewport, VIEWPORT_KEYS, label="layout viewport")
    if not closed.is_valid:
        return closed.message
    if not _positive_number(viewport.get("width")):
        return "the layout capture declares no positive viewport width"

    boxes = layout.get("boxes")
    if not isinstance(boxes, list) or is_vacuous_container(boxes):
        return "the layout capture measured no element box"
    for position, box in enumerate(boxes):
        if not isinstance(box, dict) or is_vacuous_container(box):
            return "layout box %d measured nothing" % position
        closed = check_closed_key_set(box, BOX_KEYS, label="layout box %d" % position)
        if not closed.is_valid:
            return closed.message
        selector = str(box.get("selector") or "").strip()
        if not selector:
            return "layout box %d names no element" % position
        if not (_positive_number(box.get("width")) and _positive_number(box.get("height"))):
            return (
                "layout box %r measures zero, which is what a runtime without a layout engine "
                "reports for every element" % selector
            )
        if evidence.planned.verification_medium == "rendered_ui" and not markup_carries(
            selector, evidence.envelope.get("captured_artifact")
        ):
            return (
                "layout box %r measures an element the captured rendering does not contain, so "
                "the geometry is not this render's" % selector
            )
    return None


def _admits_focus_order(evidence: MilestoneEvidence) -> Optional[str]:
    """Recorded focus sequence across the trigger's completion."""
    sequence = evidence.envelope.get("focus_sequence")
    if sequence is None:
        return (
            "the capture records no focus sequence, and where focus travelled cannot be read off "
            "a static snapshot of the tree"
        )
    if not isinstance(sequence, list) or is_vacuous_container(sequence):
        return "the focus sequence records nothing"
    if len(sequence) < 2:
        return "a focus sequence of one step records no traversal"

    previous_step: Optional[int] = None
    seen_targets = set()
    for position, entry in enumerate(sequence):
        if not isinstance(entry, dict) or is_vacuous_container(entry):
            return "focus step %d records nothing" % position
        closed = check_closed_key_set(entry, FOCUS_STEP_KEYS, label="focus step %d" % position)
        if not closed.is_valid:
            return closed.message
        step = entry.get("step")
        if not isinstance(step, int) or isinstance(step, bool):
            return "focus step %d carries no ordinal, so the sequence has no order" % position
        if previous_step is not None and step <= previous_step:
            return "focus step %d does not follow the one before it" % position
        previous_step = step
        target = str(entry.get("target") or "").strip()
        if not target:
            return "focus step %d names no target" % position
        if target in seen_targets:
            return "focus returns to %r without a step between, so no traversal is recorded" % target
        seen_targets.add(target)
        if evidence.planned.verification_medium == "rendered_ui" and not markup_carries(
            target, evidence.envelope.get("captured_artifact")
        ):
            return "focus step %d names %r, which the captured rendering does not contain" % (
                position, target,
            )
    return None


def _admits_temporal(evidence: MilestoneEvidence) -> Optional[str]:
    """Time-travelled run showing the terminal transition with no external event."""
    travel = evidence.envelope.get("time_travel")
    if travel is None:
        return (
            "the capture records no time-travelled run, so a deadline that fires on its own is "
            "indistinguishable from one an external event drove"
        )
    if not isinstance(travel, dict) or is_vacuous_container(travel):
        return "the time-travel record records nothing"
    closed = check_closed_key_set(travel, TIME_TRAVEL_KEYS, label="time-travel record")
    if not closed.is_valid:
        return closed.message
    if not _positive_number(travel.get("advanced_by_ms")):
        return "the run advanced no clock, so nothing was time-travelled"

    transition = travel.get("terminal_transition")
    if not isinstance(transition, dict) or is_vacuous_container(transition):
        return "the run records no terminal transition the advanced clock produced"
    closed = check_closed_key_set(transition, TRANSITION_KEYS, label="terminal transition")
    if not closed.is_valid:
        return closed.message
    origin = str(transition.get("from_state") or "").strip()
    destination = str(transition.get("to_state") or "").strip()
    if not origin or not destination:
        return "the terminal transition names no pair of states"
    if origin == destination:
        return "the terminal transition leaves the state unchanged, so no egress was observed"
    if "external_events" not in travel:
        return "the run does not record whether an external event drove the transition"
    if not is_vacuous_container(travel.get("external_events")):
        return "an external event drove the transition, so the clock is not what produced it"
    return None


def _admits_value_correctness(evidence: MilestoneEvidence) -> Optional[str]:
    """Asserted value traced to its Stage 0 projection binding in the recorded response."""
    bindings = evidence.envelope.get("value_bindings")
    if bindings is None:
        return "the capture traces no asserted value back to a projection binding"
    if not isinstance(bindings, list) or is_vacuous_container(bindings):
        return "the value bindings record nothing"

    serialised = evidence.envelope["captured_artifact"]
    if not isinstance(serialised, str):
        serialised = json.dumps(serialised, sort_keys=True)
    for position, binding in enumerate(bindings):
        if not isinstance(binding, dict) or is_vacuous_container(binding):
            return "value binding %d records nothing" % position
        closed = check_closed_key_set(binding, VALUE_BINDING_KEYS, label="value binding %d" % position)
        if not closed.is_valid:
            return closed.message
        if is_vacuous_container(binding.get("domain_term")):
            return "value binding %d names no domain term" % position
        if is_vacuous_container(binding.get("projection_binding")):
            return "value binding %d names no Stage 0 projection binding" % position
        if "asserted_value" not in binding:
            return "value binding %d asserts no value" % position
        asserted = binding["asserted_value"]
        rendered = asserted if isinstance(asserted, str) else json.dumps(asserted, sort_keys=True)
        if rendered not in serialised:
            return (
                "value binding %r asserts %s, which does not appear in the recorded capture"
                % (binding.get("domain_term"), rendered)
            )
    return None


def _admits_observable_state(evidence: MilestoneEvidence, claim: Dict[str, Any]) -> Optional[str]:
    """Normalised projection fingerprint per evidence_key."""
    if is_vacuous_container(claim.get("evidence_key")):
        return (
            "the claim names no evidence_key, so there is no state for a fingerprint to be "
            "computed per"
        )
    if is_vacuous_container(evidence.normalised):
        return "the capture normalised to nothing an observer could distinguish"
    return None


def claim_inadmissibility(evidence: MilestoneEvidence, claim: Dict[str, Any]) -> Optional[str]:
    """Why this capture cannot address this claim, or None when it can.

    One branch per row of the claim table, each stating what the capture must carry rather than
    what it must not. The kinds are a closed set, so a claim kind the table does not have is
    refused by the caller before it reaches here.
    """
    kind = claim.get("claim_kind")
    if kind == "state_observable_and_distinct":
        return _admits_observable_state(evidence, claim)
    if kind == "layout_viewport_floor_overflow":
        return _admits_layout(evidence)
    if kind == "traversal_and_focus_order":
        return _admits_focus_order(evidence)
    if kind == "timing_deadline_temporal_egress":
        return _admits_temporal(evidence)
    return _admits_value_correctness(evidence)


def discharged_claims(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Every claim an [Accepted Constraint] discharges, mapped to the section discharging it.

    Only a well-formed section counts: one that names what was not verified and how it was
    checked instead. A section missing either is caught by the intent linter, and a claim it
    would have discharged stays undischarged here.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for section in sections_with_header(report, "[Accepted Constraint]"):
        claims = _list_of_identifiers(section.get("discharged_claim_ids"))
        if claims is None or not is_reason_meaningful(section.get("alternative_verification")):
            continue
        for claim_id in claims:
            out.setdefault(claim_id, section)
    return out


def verify_claim(
    report: Dict[str, Any],
    planned: Dict[str, PlannedMilestone],
    verified: Sequence[MilestoneEvidence],
    base_dir: Optional[Path] = None,
) -> Tuple[List[str], List[str]]:
    """Steps 3 and 4: the second axis, and the escape hatch that is a first-class outcome.

    Returns (discharge_notes, problems). A claim the capture cannot address is not automatically
    a broken gate: emitted as an [Accepted Constraint] naming what was not verified and how it was
    checked instead, it is discharged and the delivery proceeds. Reporting that case as a failure
    is the error that makes a team switch the checker off; passing it as verified is the error the
    stage exists to stop.
    """
    problems: List[str] = []
    notes: List[str] = []
    discharged = discharged_claims(report)
    evaluated_ids = set()

    # Verify arithmetic consistency before normalisation masking replaces numerals
    arithmetic_problems = verify_arithmetic_consistency(report, planned, verified, base_dir=base_dir)
    problems.extend(arithmetic_problems)

    # A capture can be internally consistent and still fabricated; reconcile the values Stage 0
    # declared an authority for against the ground truth the pipeline already recorded.
    ground_truth_problems = verify_ground_truth_reconciliation(report, planned, verified, base_dir=base_dir)
    problems.extend(ground_truth_problems)

    # Live browser observation: verify that rendered_ui captures were produced by live
    # observation of an executable target rather than agent self-testimony.
    observation_problems = verify_rendered_ui_observation(report, planned, verified, base_dir=base_dir)
    problems.extend(observation_problems)

    for evidence in verified:
        claims = evidence.record.get("claims_evaluated")
        if not isinstance(claims, list) or is_vacuous_container(claims):
            problems.append(
                "MISSING_MILESTONE_EVIDENCE: milestone %s offers its capture for no claim, so the "
                "artifact answers nothing" % evidence.planned.milestone_id
            )
            continue
        for position, claim in enumerate(claims):
            label = "milestone %s claims_evaluated[%d]" % (evidence.planned.milestone_id, position)
            if not isinstance(claim, dict) or is_vacuous_container(claim):
                problems.append("MISSING_MILESTONE_EVIDENCE: %s records nothing" % label)
                continue
            closed = check_closed_key_set(claim, CLAIM_ENTRY_KEYS, label=label)
            if not closed.is_valid:
                problems.append("UNKNOWN_DELIVERY_ATTRIBUTE: %s" % closed.message)
                continue
            claim_id = str(claim.get("claim_id") or "").strip()
            if not claim_id:
                problems.append("MISSING_MILESTONE_EVIDENCE: %s names no claim" % label)
                continue
            if claim.get("claim_kind") not in CLAIM_KINDS:
                problems.append(
                    "UNKNOWN_CLAIM_KIND: %s declares kind %r; the claim table carries a closed set "
                    "of five: %s" % (label, claim.get("claim_kind"), ", ".join(CLAIM_KINDS))
                )
                continue
            if claim_id not in evidence.planned.claims:
                problems.append(
                    "UNPLANNED_CLAIM_EVALUATED: %s evaluates %s, which the plan did not commit "
                    "milestone %s to" % (label, claim_id, evidence.planned.milestone_id)
                )
                continue
            evaluated_ids.add(claim_id)

            reason = claim_inadmissibility(evidence, claim)
            section = discharged.get(claim_id)
            if reason is None:
                if section is not None:
                    problems.append(
                        "CONSTRAINT_DISCHARGES_ADMISSIBLE_CLAIM: %s is discharged by the "
                        "[Accepted Constraint] %r although its capture can address it; the escape "
                        "hatch is for what the harness cannot evidence, not for what it did not"
                        % (claim_id, section.get("title"))
                    )
                continue
            if section is not None:
                notes.append(
                    "ACCEPTED_CONSTRAINT_DISCHARGED: %s is not verifiable in the declared harness "
                    "(%s) and is recorded under %r, checked instead by: %s"
                    % (claim_id, reason, section.get("title"), section.get("alternative_verification"))
                )
                continue
            problems.append(
                "EVIDENCE_CLAIM_INADMISSIBLE: %s offers the milestone's %s capture for %s, and it "
                "matches that medium, but %s. Emit it as an [Accepted Constraint] naming what was "
                "not verified and how it was checked instead."
                % (label, evidence.planned.verification_medium, claim_id, reason)
            )

    problems.extend(_undischarged_commitments(planned, evaluated_ids, discharged))
    for claim_id in sorted(set(discharged) - evaluated_ids):
        problems.append(
            "DISCHARGED_CLAIM_NOT_EVALUATED: an [Accepted Constraint] discharges %s, which no "
            "milestone evaluated" % claim_id
        )
    return notes, problems



def _undischarged_commitments(
    planned: Dict[str, PlannedMilestone],
    evaluated_ids: set,
    discharged: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Step 4: a claim the plan committed to that the delivery neither evidenced nor discharged."""
    problems: List[str] = []
    for milestone_id in sorted(planned):
        for claim_id in planned[milestone_id].claims:
            if claim_id in evaluated_ids or claim_id in discharged:
                continue
            problems.append(
                "CLAIM_WITHOUT_ACCEPTED_CONSTRAINT: the plan commits milestone %s to %s and the "
                "delivery report neither offers evidence for it nor emits it as an "
                "[Accepted Constraint] naming what was not verified"
                % (milestone_id, claim_id)
            )
    return problems


# ---------------------------------------------------------------------------
# The abstraction-level check
# ---------------------------------------------------------------------------

# Structural, not lexical. A domain summary legitimately names boundary bindings and entity
# fields, so a blacklist on `function`, `class`, `import`, `const` or a file extension rejects
# exactly the sentences this stage wants. What is refused instead is a shape that cannot be
# anything but code: a fenced block, a unified diff hunk, and a density of code syntax no prose
# reaches. Semantic abstraction judgement is not attempted here; it stays with the human at
# [Milestone] sign-off, and the output says so.
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_DIFF_HUNK = re.compile(r"^\s*@@\s*-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s*@@")
_DIFF_HEADER = re.compile(r"^\s*(?:diff --git |index [0-9a-f]{7,}\.\.)")
_DIFF_MINUS = re.compile(r"^\s*---\s+\S")
_DIFF_PLUS = re.compile(r"^\s*\+\+\+\s+\S")

_SYNTAX_CHARS = "{};=<>"
_SYNTAX_DIGRAPHS = ("()", "[]", "::")
CODE_DENSITY_CEILING = 0.15
CODE_DENSITY_FLOOR = 3

ABSTRACTION_SCOPE_NOTE = (
    "This check is structural only: it refuses fenced code, diff hunks and code-syntax density. "
    "Whether prose sits at the flow level is a judgement it does not make, and that judgement "
    "stays with the human reviewer at [Milestone] sign-off."
)


def code_syntax_density(text: str) -> Tuple[int, int, float]:
    """(code syntax characters, natural-language words, their ratio) for one passage."""
    syntax = sum(text.count(char) for char in _SYNTAX_CHARS)
    syntax += sum(text.count(digraph) for digraph in _SYNTAX_DIGRAPHS)
    words = len(re.findall(r"[A-Za-z]+", text))
    return syntax, words, (syntax / words) if words else float(syntax)


def _prose_passages(section: Dict[str, Any], label: str) -> List[Tuple[str, str]]:
    """Every human-facing passage of one intent section, with where it came from."""
    passages: List[Tuple[str, str]] = []
    for key in ("summary", "alternative_verification", "mitigation"):
        if isinstance(section.get(key), str):
            passages.append(("%s %s" % (label, key), section[key]))
    resolution = section.get("resolution")
    if isinstance(resolution, dict) and isinstance(resolution.get("rationale"), str):
        passages.append(("%s resolution rationale" % label, resolution["rationale"]))
    options = section.get("options")
    if isinstance(options, list):
        for position, option in enumerate(options):
            if isinstance(option, dict) and isinstance(option.get("impact"), str):
                passages.append(("%s options[%d] impact" % (label, position), option["impact"]))
    return passages


def lint_abstraction(report: Dict[str, Any]) -> List[str]:
    """Refuse implementation mechanics in the passages a human reads at delivery."""
    problems: List[str] = []
    for index, section in enumerate(report.get("intent_sections", [])):
        if not isinstance(section, dict):
            continue
        label = "intent_sections[%d]" % index
        for where, text in _prose_passages(section, label):
            problems.extend(_lint_passage(where, text))
    return problems


def _lint_passage(where: str, text: str) -> List[str]:
    lines = text.splitlines()
    for position, line in enumerate(lines):
        if _FENCE.match(line):
            return [
                "LOW_LEVEL_CODE_NOISE: %s embeds a fenced code block; code belongs in the "
                "referenced evidence artifact, not in the delivery summary" % where
            ]
        if _DIFF_HUNK.match(line) or _DIFF_HEADER.match(line):
            return [
                "LOW_LEVEL_CODE_NOISE: %s embeds a unified diff hunk; a line-level patch is not a "
                "flow-level statement of what changed" % where
            ]
        if _DIFF_MINUS.match(line) and position + 1 < len(lines) and _DIFF_PLUS.match(lines[position + 1]):
            return [
                "LOW_LEVEL_CODE_NOISE: %s embeds a unified diff header pair; a line-level patch is "
                "not a flow-level statement of what changed" % where
            ]

    syntax, words, ratio = code_syntax_density(text)
    if syntax >= CODE_DENSITY_FLOOR and ratio > CODE_DENSITY_CEILING:
        return [
            "LOW_LEVEL_CODE_NOISE: %s carries %d code-syntax character(s) across %d word(s), a "
            "density of %.2f against a ceiling of %.2f; this reads as implementation mechanics "
            "rather than a business or system flow" % (
                where, syntax, words, ratio, CODE_DENSITY_CEILING,
            )
        ]
    return []
