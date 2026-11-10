#!/usr/bin/env python3
"""Unit tests for the Stage 5 evidence verifier and the changelog generator.

The whole stage is the independence of the two axes, so most of these tests are the two axes
held against each other over one capture: the medium axis must accept a snapshot supplied for a
rendered_ui milestone and the claim axis must refuse it for a layout claim, and adding the
geometry a layout engine would have measured must flip the second without touching the first. A
test that only ran one direction would pass against a checker that had one axis twice.

The rest are the shapes that defeated checks earlier in this build: a declared field the
validator branches on, a check enumerating what it rejects rather than what it allows, and a
container asserted non-empty without element content.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
CASES_DIR = TESTS_DIR / "cases" / "stage-5"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def load_executable(name, module_name):
    loader = SourceFileLoader(module_name, str(SKILL_DIR / "bin" / name))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


generate_changelog = load_executable("generate-changelog", "generate_changelog")

import lib.delivery as delivery


MARKUP = (
    '<div class="pane pane--results" id="search-pane">'
    '<h2 class="title">Results for the indexed corpus</h2>'
    '<ul class="result-list"><li class="result-row">First indexed chunk</li></ul>'
    '<input id="search-query-field" aria-label="Search query">'
    "</div>"
)

LAYOUT_CAPTURE = {
    "viewport": {"width": 360, "height": 640},
    "boxes": [{"selector": "ul.result-list", "width": 328, "height": 96, "overflow_x": 0}],
}


class DeliveryFixture(object):
    """A minimal one-milestone delivery on disk, built so a test can mutate exactly one thing."""

    def __init__(self, claim_kind="layout_viewport_floor_overflow", envelope_extra=None):
        self.root = Path(tempfile.mkdtemp(prefix="afp-s5-"))
        (self.root / "evidence").mkdir()
        (self.root / "evidence" / "capture.html").write_text(
            f"<!DOCTYPE html><html><body>{MARKUP}</body></html>", encoding="utf-8"
        )
        envelope = {
            "captured_artifact": MARKUP,
            "observation_target": {"entry_point": "evidence/capture.html"},
        }
        envelope.update(envelope_extra or {})
        self.envelope_path = self.root / "evidence" / "capture.json"
        self.envelope_path.write_text(json.dumps(envelope), encoding="utf-8")

        self.dag = {
            "schema_version": "1.0.0",
            "feature_slug": "search-console-web-ui",
            "slices": [{
                "slice_id": "slice_1_query_entry",
                "milestones": [{
                    "milestone_id": "M3_UI_QUERY_FLOW",
                    "verification_medium": "rendered_ui",
                    "claims": ["claim:layout:narrow_viewport_floor"],
                }],
            }],
        }
        self.report = {
            "schema_version": "1.0.0",
            "feature_slug": "search-console-web-ui",
            "delivered_at": "2026-08-22T11:45:00Z",
            "intent_sections": [{
                "header": "[Milestone]",
                "title": "Search Console Query Flow Verified",
                "summary": "The operator can enter a query and read a ranked list of matches, "
                           "and the states they can tell apart were verified against a capture.",
                "milestone_refs": ["M3_UI_QUERY_FLOW"],
            }],
            "milestone_evidence_records": [{
                "milestone_id": "M3_UI_QUERY_FLOW",
                "slice_id": "slice_1_query_entry",
                "artifact_path": "evidence/capture.json",
                "claims_evaluated": [{
                    "claim_id": "claim:layout:narrow_viewport_floor",
                    "claim_kind": claim_kind,
                    "evidence_key": "state:RESULTS_POPULATED",
                }],
            }],
        }

        self.lexicon = {
            "schema_version": "1.0.0",
            "feature_slug": "search-console-web-ui",
            "bounded_context": "search_console",
            "entities": [
                {
                    "name": "SearchChunk",
                    "description": "Indexed search chunk.",
                    "provenance": "FEATURE_INTRODUCED",
                    "repo_reference": None,
                    "glossary_visibility": "INCLUDE",
                    "plain_language_summary": "Indexed search chunk.",
                    "attributes": [
                        { "name": "id", "type": "string" },
                        { "name": "count", "type": "int64", "unit_ref": "chunk_count" }
                    ]
                },
                {
                    "name": "SearchSummary",
                    "description": "Search summary.",
                    "provenance": "FEATURE_INTRODUCED",
                    "repo_reference": None,
                    "glossary_visibility": "INCLUDE",
                    "plain_language_summary": "Search summary.",
                    "attributes": [
                        { "name": "total_count", "type": "int64", "unit_ref": "chunk_count" }
                    ]
                }
            ],
            "unit_precisions": [
                {
                    "id": "chunk_count",
                    "quantity_type": "NUMERIC",
                    "base_unit": "count",
                    "numeric_type": "INTEGER",
                    "representation": "INTEGER",
                    "rounding_mode": "ROUND_HALF_UP"
                }
            ],
            "projection_bindings": [
                {
                    "projection_id": "search_total_count",
                    "quantity_or_label": "Search Total Count",
                    "observer_id": "SearchObserver",
                    "source_type": "PURE_EXPRESSION",
                    "source_reference": "SearchSummary.total_count",
                    "derivation_expression": "sum(SearchChunk.count)"
                },
                {
                    "projection_id": "chunk_count_badge",
                    "quantity_or_label": "Chunk Count Badge",
                    "observer_id": "SearchObserver",
                    "source_type": "CONTRACT_FIELD",
                    "source_reference": "SearchChunk.count",
                    "derivation_expression": "SearchChunk.count"
                }
            ],
            "value_authorities": [
                {
                    "value_name": "search_total_count",
                    "authority_model": "LOCAL_AUTHORITATIVE",
                    "upstream_boundary": None,
                    "local_computation": "sum(SearchChunk.count)",
                    "tolerance_epsilon": 0.001,
                    "divergence_action": "REJECT",
                    "rationale": "Total count is the sum of chunk counts."
                }
            ]
        }
        (self.root / "domain-lexicon.json").write_text(json.dumps(self.lexicon), encoding="utf-8")

    def discharge(self, claim_id="claim:layout:narrow_viewport_floor", alternative=None):
        self.report["intent_sections"].append({
            "header": "[Accepted Constraint]",
            "title": "Narrow Viewport Floor Is Not Evidenceable In This Harness",
            "summary": "The declared harness renders without a layout engine and measures no "
                       "geometry, so the viewport floor was not verified in the automated run.",
            "alternative_verification": alternative if alternative is not None else
                "Opened once by hand in a real browser at a 360 pixel width and read for overflow.",
            "discharged_claim_ids": [claim_id],
        })
        return self

    def write(self):
        (self.root / "execution-dag.json").write_text(json.dumps(self.dag), encoding="utf-8")
        (self.root / "delivery-report.json").write_text(json.dumps(self.report), encoding="utf-8")
        return self.root / "delivery-report.json", self.root / "execution-dag.json"

    def run(self):
        """(medium problems, claim problems, discharge notes) for the fixture as it now stands."""
        report_path, dag_path = self.write()
        report, report_problems = delivery.read_report(report_path)
        if report is None:
            return report_problems, [], []
        planned, plan_problems = delivery.read_planned_milestones(dag_path)
        if plan_problems:
            return plan_problems, [], []
        verified, medium_problems = delivery.verify_medium(report, planned, self.root)
        from lib.observation import is_browser_available
        if not is_browser_available(self.root):
            with unittest.mock.patch("lib.observation.observe_rendered_ui", return_value=(MARKUP, None, None)):
                notes, claim_problems = delivery.verify_claim(report, planned, verified, base_dir=self.root)
        else:
            notes, claim_problems = delivery.verify_claim(report, planned, verified, base_dir=self.root)
        return medium_problems, claim_problems, notes


    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def codes(problems):
    """The bare tokens a run printed, ignoring the indented detail lines beneath them."""
    return {
        line.split(":", 1)[0]
        for line in problems
        if line and not line.startswith(" ")
    }


class TestAxesAreIndependent(unittest.TestCase):
    """One capture, two questions. The medium axis accepts it and the claim axis need not."""

    def test_snapshot_passes_medium_and_fails_layout_claim(self):
        fixture = DeliveryFixture()
        try:
            medium, claim, _notes = fixture.run()
            self.assertEqual(medium, [], "a rendered_ui snapshot is the right kind of artifact")
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(claim))
        finally:
            fixture.cleanup()

    def test_same_snapshot_with_measured_geometry_passes_both(self):
        fixture = DeliveryFixture(envelope_extra={"layout_capture": LAYOUT_CAPTURE})
        try:
            medium, claim, _notes = fixture.run()
            self.assertEqual(medium, [])
            self.assertEqual(claim, [], "a layout claim is admissible once geometry was measured")
        finally:
            fixture.cleanup()

    def test_geometry_measuring_zero_is_refused(self):
        """A runtime with no layout engine reports zero for every element."""
        flat = {"viewport": {"width": 360}, "boxes": [
            {"selector": "ul.result-list", "width": 0, "height": 0},
        ]}
        fixture = DeliveryFixture(envelope_extra={"layout_capture": flat})
        try:
            _medium, claim, _notes = fixture.run()
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(claim))
        finally:
            fixture.cleanup()

    def test_geometry_for_an_element_not_in_the_capture_is_refused(self):
        elsewhere = {"viewport": {"width": 360}, "boxes": [
            {"selector": "table.ledger-grid", "width": 328, "height": 96},
        ]}
        fixture = DeliveryFixture(envelope_extra={"layout_capture": elsewhere})
        try:
            _medium, claim, _notes = fixture.run()
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(claim))
        finally:
            fixture.cleanup()

    def test_medium_mismatch_is_not_reported_as_a_claim_failure(self):
        fixture = DeliveryFixture()
        try:
            fixture.envelope_path.write_text(
                json.dumps({"captured_artifact": {"exit_code": 0, "stdout": "PASS\n"}}),
                encoding="utf-8",
            )
            medium, _claim, _notes = fixture.run()
            self.assertIn("EVIDENCE_MEDIUM_MISMATCH", codes(medium))
        finally:
            fixture.cleanup()


class TestAcceptedConstraintDischarge(unittest.TestCase):
    """The escape hatch passes, and does not become a way to wave verifiable claims through."""

    def test_discharge_passes_and_records_what_was_not_verified(self):
        fixture = DeliveryFixture().discharge()
        try:
            medium, claim, notes = fixture.run()
            self.assertEqual(medium, [])
            self.assertEqual(claim, [], "a properly emitted [Accepted Constraint] is not a failure")
            self.assertEqual(len(notes), 1)
            self.assertIn("ACCEPTED_CONSTRAINT_DISCHARGED", notes[0])
        finally:
            fixture.cleanup()

    def test_discharge_without_an_alternative_check_does_not_discharge(self):
        fixture = DeliveryFixture().discharge(alternative="-")
        try:
            _medium, claim, _notes = fixture.run()
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(claim))
        finally:
            fixture.cleanup()

    def test_discharging_a_claim_the_capture_can_address_is_refused(self):
        """Otherwise every claim is discharged and the gate is off."""
        fixture = DeliveryFixture(envelope_extra={"layout_capture": LAYOUT_CAPTURE}).discharge()
        try:
            _medium, claim, _notes = fixture.run()
            self.assertIn("CONSTRAINT_DISCHARGES_ADMISSIBLE_CLAIM", codes(claim))
        finally:
            fixture.cleanup()

    def test_discharging_a_claim_nothing_evaluated_is_refused(self):
        fixture = DeliveryFixture().discharge(claim_id="claim:ghost:never_evaluated")
        try:
            _medium, claim, _notes = fixture.run()
            self.assertIn("DISCHARGED_CLAIM_NOT_EVALUATED", codes(claim))
        finally:
            fixture.cleanup()

    def test_a_committed_claim_neither_evaluated_nor_discharged_is_refused(self):
        fixture = DeliveryFixture()
        fixture.report["milestone_evidence_records"][0]["claims_evaluated"] = [{
            "claim_id": "claim:layout:narrow_viewport_floor",
            "claim_kind": "state_observable_and_distinct",
            "evidence_key": "state:RESULTS_POPULATED",
        }]
        fixture.dag["slices"][0]["milestones"][0]["claims"].append("claim:ui:focus_returns")
        try:
            _medium, claim, _notes = fixture.run()
            self.assertIn("CLAIM_WITHOUT_ACCEPTED_CONSTRAINT", codes(claim))
        finally:
            fixture.cleanup()


class TestClaimTableRows(unittest.TestCase):
    """One test per remaining row of the claim table, in both directions."""

    def test_focus_order_needs_a_recorded_sequence(self):
        absent = DeliveryFixture(claim_kind="traversal_and_focus_order")
        present = DeliveryFixture(claim_kind="traversal_and_focus_order", envelope_extra={
            "focus_sequence": [
                {"step": 1, "target": "input#search-query-field"},
                {"step": 2, "target": "ul.result-list"},
            ],
        })
        try:
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(absent.run()[1]))
            self.assertEqual(present.run()[1], [])
        finally:
            absent.cleanup()
            present.cleanup()

    def test_a_focus_sequence_of_one_step_records_no_traversal(self):
        fixture = DeliveryFixture(claim_kind="traversal_and_focus_order", envelope_extra={
            "focus_sequence": [{"step": 1, "target": "input#search-query-field"}],
        })
        try:
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(fixture.run()[1]))
        finally:
            fixture.cleanup()

    def test_temporal_egress_needs_a_time_travelled_run_with_no_external_event(self):
        driven = DeliveryFixture(claim_kind="timing_deadline_temporal_egress", envelope_extra={
            "time_travel": {
                "advanced_by_ms": 3000,
                "terminal_transition": {"from_state": "LOADING", "to_state": "TIMED_OUT"},
                "external_events": [{"kind": "boundary_response"}],
            },
        })
        clean = DeliveryFixture(claim_kind="timing_deadline_temporal_egress", envelope_extra={
            "time_travel": {
                "advanced_by_ms": 3000,
                "terminal_transition": {"from_state": "LOADING", "to_state": "TIMED_OUT"},
                "external_events": [],
            },
        })
        try:
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(driven.run()[1]))
            self.assertEqual(clean.run()[1], [])
        finally:
            driven.cleanup()
            clean.cleanup()

    def test_value_correctness_needs_the_value_to_be_in_the_capture(self):
        untraced = DeliveryFixture(claim_kind="value_correctness", envelope_extra={
            "value_bindings": [{
                "domain_term": "result_row_label",
                "projection_binding": "SearchResult.title to the result row",
                "asserted_value": "A chunk that was never rendered",
            }],
        })
        traced = DeliveryFixture(claim_kind="value_correctness", envelope_extra={
            "value_bindings": [{
                "domain_term": "result_row_label",
                "projection_binding": "SearchResult.title to the result row",
                "asserted_value": "First indexed chunk",
            }],
        })
        try:
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(untraced.run()[1]))
            self.assertEqual(traced.run()[1], [])
        finally:
            untraced.cleanup()
            traced.cleanup()

    def test_observable_state_needs_an_evidence_key(self):
        fixture = DeliveryFixture(claim_kind="state_observable_and_distinct")
        del fixture.report["milestone_evidence_records"][0]["claims_evaluated"][0]["evidence_key"]
        try:
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(fixture.run()[1]))
        finally:
            fixture.cleanup()


class TestVacuousContainers(unittest.TestCase):
    """`[{}]` defeated two earlier stages of this build. It satisfies nothing here."""

    def test_an_empty_claim_entry_does_not_satisfy_claims_evaluated(self):
        fixture = DeliveryFixture()
        fixture.report["milestone_evidence_records"][0]["claims_evaluated"] = [{}]
        try:
            _medium, claim, _notes = fixture.run()
            self.assertIn("MISSING_MILESTONE_EVIDENCE", codes(claim))
        finally:
            fixture.cleanup()

    def test_an_empty_layout_box_does_not_satisfy_a_layout_capture(self):
        fixture = DeliveryFixture(envelope_extra={
            "layout_capture": {"viewport": {"width": 360}, "boxes": [{}]},
        })
        try:
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(fixture.run()[1]))
        finally:
            fixture.cleanup()

    def test_an_empty_value_binding_does_not_satisfy_value_correctness(self):
        fixture = DeliveryFixture(claim_kind="value_correctness", envelope_extra={
            "value_bindings": [{}],
        })
        try:
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(fixture.run()[1]))
        finally:
            fixture.cleanup()

    def test_an_empty_discharged_claim_id_discharges_nothing(self):
        fixture = DeliveryFixture()
        fixture.discharge()
        fixture.report["intent_sections"][-1]["discharged_claim_ids"] = [""]
        try:
            _medium, claim, _notes = fixture.run()
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(claim))
        finally:
            fixture.cleanup()

    def test_an_empty_capture_is_no_evidence(self):
        fixture = DeliveryFixture()
        try:
            fixture.envelope_path.write_text(
                json.dumps({"captured_artifact": {}}), encoding="utf-8",
            )
            medium, _claim, _notes = fixture.run()
            self.assertIn("MISSING_MILESTONE_EVIDENCE", codes(medium))
        finally:
            fixture.cleanup()


class TestClosedVocabularies(unittest.TestCase):
    """Every vocabulary is a positive set. What is not named is refused."""

    def test_the_five_intent_headers_are_the_whole_vocabulary(self):
        report = {
            "intent_sections": [
                {"header": header, "title": "t", "summary": "a b c d e f g h"}
                for header in delivery.INTENT_HEADERS
            ],
        }
        self.assertEqual(
            sorted(delivery.SECTION_KEYS), sorted(delivery.INTENT_HEADERS),
            "every header carries a key set, so a new header cannot arrive unchecked",
        )
        self.assertEqual(len(report["intent_sections"]), 5)

    def test_an_invented_header_is_refused(self):
        report = {"intent_sections": [{
            "header": "[Note]", "title": "t", "summary": "one two three four five six",
        }]}
        self.assertIn("INVALID_INTENT_HEADER", codes(delivery.lint_intent(report)))

    def test_a_section_may_not_borrow_another_headers_keys(self):
        report = {"intent_sections": [{
            "header": "[Risk]",
            "title": "t",
            "summary": "one two three four five six seven",
            "discharged_claim_ids": ["claim:layout:narrow_viewport_floor"],
        }]}
        self.assertIn("INVALID_INTENT_HEADER", codes(delivery.lint_intent(report)))

    def test_a_claim_kind_outside_the_table_is_refused(self):
        fixture = DeliveryFixture(claim_kind="looks_fine_to_me")
        try:
            self.assertIn("UNKNOWN_CLAIM_KIND", codes(fixture.run()[1]))
        finally:
            fixture.cleanup()

    def test_a_medium_outside_the_six_is_refused(self):
        fixture = DeliveryFixture()
        fixture.dag["slices"][0]["milestones"][0]["verification_medium"] = "screenshot"
        try:
            self.assertIn("UNKNOWN_VERIFICATION_MEDIUM", codes(fixture.run()[0]))
        finally:
            fixture.cleanup()

    def test_an_unknown_envelope_key_is_refused(self):
        fixture = DeliveryFixture(envelope_extra={"claim_admissible": True})
        try:
            self.assertIn("MISSING_MILESTONE_EVIDENCE", codes(fixture.run()[0]))
        finally:
            fixture.cleanup()

    def test_an_unknown_evidence_record_key_is_refused(self):
        fixture = DeliveryFixture()
        fixture.report["milestone_evidence_records"][0]["evidence_sufficient"] = True
        try:
            self.assertIn("UNKNOWN_DELIVERY_ATTRIBUTE", codes(fixture.run()[0]))
        finally:
            fixture.cleanup()

    def test_evidence_for_a_milestone_the_plan_never_committed_is_refused(self):
        fixture = DeliveryFixture()
        fixture.report["milestone_evidence_records"].append({
            "milestone_id": "M9_INVENTED",
            "slice_id": "slice_1_query_entry",
            "artifact_path": "evidence/capture.json",
            "claims_evaluated": [{
                "claim_id": "claim:ghost", "claim_kind": "state_observable_and_distinct",
                "evidence_key": "state:GHOST",
            }],
        })
        try:
            self.assertIn("UNPLANNED_MILESTONE_EVIDENCE", codes(fixture.run()[0]))
        finally:
            fixture.cleanup()


class TestTransitionGate(unittest.TestCase):
    """§4: the unresolved counts are differenced from the sections, never read from the report."""

    def _decision(self, resolution=None):
        section = {
            "header": "[Decision Needed]",
            "title": "Which count the result summary reads",
            "summary": "The summary can read the corpus total or the rows on the page, and the "
                       "two disagree once the corpus is larger than one page.",
            "options": [
                {"option_id": "corpus_total", "impact": "The operator reads the whole corpus "
                                                        "match count, which is what they came for."},
                {"option_id": "page_rows", "impact": "The operator reads what is on screen, which "
                                                     "understates the corpus on a large search."},
            ],
        }
        if resolution is not None:
            section["resolution"] = resolution
        return {"intent_sections": [section]}

    def test_an_open_decision_blocks_completion(self):
        self.assertIn("UNRESOLVED_DECISION_NEEDED", codes(delivery.check_gates(self._decision())))

    def test_a_resolution_naming_one_of_the_options_clears_it(self):
        report = self._decision({
            "chosen_option_id": "corpus_total",
            "rationale": "The operator is searching the corpus rather than the page they are on.",
        })
        self.assertEqual(delivery.check_gates(report), [])
        self.assertEqual(delivery.lint_intent(report), [])

    def test_a_resolution_naming_an_option_that_was_never_offered_is_refused(self):
        report = self._decision({
            "chosen_option_id": "some_third_thing",
            "rationale": "The operator is searching the corpus rather than the page they are on.",
        })
        self.assertIn("INVALID_INTENT_HEADER", codes(delivery.lint_intent(report)))

    def test_a_blocker_with_nothing_recording_it_cleared_blocks_completion(self):
        report = {"intent_sections": [{
            "header": "[Blocker]",
            "title": "No credential for the search boundary in staging",
            "summary": "The console cannot reach the live boundary from staging because no "
                       "credential has been issued for that environment.",
        }]}
        self.assertIn("UNRESOLVED_BLOCKER", codes(delivery.check_gates(report)))

    def test_a_risk_does_not_block_completion(self):
        report = {"intent_sections": [{
            "header": "[Risk]",
            "title": "Search boundary latency above the client guard",
            "summary": "A search longer than three seconds trips the client timeout guard and "
                       "the console returns to idle rather than hanging.",
        }]}
        self.assertEqual(delivery.check_gates(report), [])


class TestAbstractionLevel(unittest.TestCase):
    """Structural, not lexical. The false-positive direction is the one that matters."""

    def _summary(self, text):
        return {"intent_sections": [{
            "header": "[Risk]", "title": "t", "summary": text,
        }]}

    def test_a_domain_summary_naming_a_boundary_binding_passes(self):
        report = self._summary(
            "Resolved the SearchResponse.total_results binding in the search summary card, so "
            "the count the operator reads is the corpus total rather than the page length."
        )
        self.assertEqual(delivery.lint_abstraction(report), [])

    def test_a_summary_naming_a_module_and_a_class_passes(self):
        report = self._summary(
            "The search adapter module now owns the failure envelope for the search endpoint, "
            "and the network api boundary class is the only place that import happens."
        )
        self.assertEqual(delivery.lint_abstraction(report), [])

    def test_a_fenced_code_block_is_refused(self):
        report = self._summary("Wired the submit handler as follows.\n```ts\nconst q = 1\n```")
        self.assertIn("LOW_LEVEL_CODE_NOISE", codes(delivery.lint_abstraction(report)))

    def test_a_unified_diff_hunk_is_refused(self):
        report = self._summary(
            "Changed the submit handler.\n@@ -41,6 +41,9 @@\n-old line\n+new line"
        )
        self.assertIn("LOW_LEVEL_CODE_NOISE", codes(delivery.lint_abstraction(report)))

    def test_a_diff_header_pair_is_refused(self):
        report = self._summary(
            "Changed the submit handler.\n--- a/src/App.tsx\n+++ b/src/App.tsx"
        )
        self.assertIn("LOW_LEVEL_CODE_NOISE", codes(delivery.lint_abstraction(report)))

    def test_a_line_of_source_is_refused_on_density(self):
        report = self._summary(
            "Updated App.tsx line 42: const [q, setQ] = useState<string>(''); "
            "function handleSearch() { axios.post('/v1/search', { params: { query: q } }); }"
        )
        self.assertIn("LOW_LEVEL_CODE_NOISE", codes(delivery.lint_abstraction(report)))

    def test_the_check_states_its_own_limit(self):
        self.assertIn("[Milestone] sign-off", delivery.ABSTRACTION_SCOPE_NOTE)

    def test_the_option_impact_and_the_alternative_check_are_linted_too(self):
        report = {"intent_sections": [{
            "header": "[Accepted Constraint]",
            "title": "t",
            "summary": "The harness measures no geometry, so the viewport floor was not verified.",
            "alternative_verification": "```sh\nnpx playwright test --viewport 360\n```",
            "discharged_claim_ids": ["claim:layout:narrow_viewport_floor"],
        }]}
        self.assertIn("LOW_LEVEL_CODE_NOISE", codes(delivery.lint_abstraction(report)))


class TestChangelogFromEvidence(unittest.TestCase):
    """The release record is the verified evidence, and nothing else."""

    def test_the_changelog_names_every_verified_artifact_and_its_medium(self):
        fixture = DeliveryFixture(envelope_extra={"layout_capture": LAYOUT_CAPTURE})
        try:
            report_path, dag_path = fixture.write()
            from lib.observation import is_browser_available
            if not is_browser_available(fixture.root):
                with unittest.mock.patch("lib.observation.observe_rendered_ui", return_value=(MARKUP, None, None)):
                    report, verified, notes, problems = generate_changelog.verified_delivery(
                        report_path, dag_path,
                    )
            else:
                report, verified, notes, problems = generate_changelog.verified_delivery(
                    report_path, dag_path,
                )
            self.assertEqual(problems, [])
            lines, compose_problems = generate_changelog.compose_changelog(report, verified)
            self.assertEqual(compose_problems, [])
            text = "\n".join(lines)
            self.assertIn("evidence/capture.json", text)
            self.assertIn("rendered_ui", text)
            self.assertIn("Search Console Query Flow Verified", text)
            self.assertEqual(notes, [])
        finally:
            fixture.cleanup()

    def test_the_changelog_is_refused_when_the_report_does_not_verify(self):
        fixture = DeliveryFixture()
        try:
            report_path, dag_path = fixture.write()
            from lib.observation import is_browser_available
            if not is_browser_available(fixture.root):
                with unittest.mock.patch("lib.observation.observe_rendered_ui", return_value=(MARKUP, None, None)):
                    _report, _verified, _notes, problems = generate_changelog.verified_delivery(
                        report_path, dag_path,
                    )
            else:
                _report, _verified, _notes, problems = generate_changelog.verified_delivery(
                    report_path, dag_path,
                )
            self.assertIn("EVIDENCE_CLAIM_INADMISSIBLE", codes(problems))
        finally:
            fixture.cleanup()

    def test_a_delivered_milestone_with_no_verified_evidence_is_refused(self):
        fixture = DeliveryFixture(envelope_extra={"layout_capture": LAYOUT_CAPTURE})
        try:
            report_path, dag_path = fixture.write()
            from lib.observation import is_browser_available
            if not is_browser_available(fixture.root):
                with unittest.mock.patch("lib.observation.observe_rendered_ui", return_value=(MARKUP, None, None)):
                    report, verified, _notes, problems = generate_changelog.verified_delivery(
                        report_path, dag_path,
                    )
            else:
                report, verified, _notes, problems = generate_changelog.verified_delivery(
                    report_path, dag_path,
                )
            self.assertEqual(problems, [])
            report["intent_sections"][0]["milestone_refs"] = ["M9_NEVER_VERIFIED"]
            _lines, compose_problems = generate_changelog.compose_changelog(report, verified)
            self.assertIn("MISSING_MILESTONE_EVIDENCE", codes(compose_problems))
        finally:
            fixture.cleanup()

    def test_the_generator_reads_no_version_control_log(self):
        source = (SKILL_DIR / "bin" / "generate-changelog").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "git log", "git rev-list"):
            self.assertNotIn(
                forbidden, source,
                "the changelog is computed from the evidence record, not from commit messages",
            )


class TestShippedFixtures(unittest.TestCase):
    """Each shipped rejection fixture is invalid for exactly one reason."""

    EXPECTED = {
        "invalid-intent-header": "INVALID_INTENT_HEADER",
        "unresolved-decision": "UNRESOLVED_DECISION_NEEDED",
        "unresolved-blocker": "UNRESOLVED_BLOCKER",
        "low-level-code-noise": "LOW_LEVEL_CODE_NOISE",
        "missing-milestone-evidence": "MISSING_MILESTONE_EVIDENCE",
        "evidence-medium-mismatch": "EVIDENCE_MEDIUM_MISMATCH",
        "medium-match-claim-mismatch": "EVIDENCE_CLAIM_INADMISSIBLE",
        "claim-without-accepted-constraint": "CLAIM_WITHOUT_ACCEPTED_CONSTRAINT",
    }

    def _full_run(self, name):
        directory = CASES_DIR / name
        report, problems = delivery.read_report(directory / "delivery-report.json")
        if report is None:
            return problems
        planned, plan_problems = delivery.read_planned_milestones(directory / "execution-dag.json")
        if plan_problems:
            return plan_problems
        for check in (delivery.lint_intent, delivery.lint_abstraction, delivery.check_gates):
            found = check(report)
            if found:
                return found
        verified, found = delivery.verify_medium(report, planned, directory)
        if found:
            return found
        from lib.observation import is_browser_available
        if not is_browser_available(directory):
            with unittest.mock.patch("lib.delivery.verify_rendered_ui_observation", return_value=[]):
                _notes, found = delivery.verify_claim(report, planned, verified, base_dir=directory)
        else:
            _notes, found = delivery.verify_claim(report, planned, verified, base_dir=directory)
        return found


    def test_each_rejection_fixture_raises_exactly_its_own_code(self):
        for name, code in sorted(self.EXPECTED.items()):
            with self.subTest(fixture=name):
                found = codes(self._full_run(name))
                self.assertEqual(
                    found, {code},
                    "%s must be invalid for one reason only, found %s" % (name, sorted(found)),
                )

    def test_the_two_positive_fixtures_verify_clean(self):
        for name in ("valid-report", "accepted-constraint"):
            with self.subTest(fixture=name):
                self.assertEqual(self._full_run(name), [])

    def test_non_arithmetic_lexicon_returns_no_problems(self):
        from lib.arithmetic import verify_arithmetic_consistency
        lexicon_no_arith = {
            "schema_version": "1.0.0",
            "feature_slug": "simple-crud",
            "bounded_context": "simple_crud",
            "entities": [],
            "value_objects": [],
            "state_enums": [],
            "unit_precisions": [],
            "actors_and_observers": {},
            "alias_pairs": [],
            "projection_bindings": [],
            "value_authorities": [],
            "fact_precedences": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "domain-lexicon.json").write_text(json.dumps(lexicon_no_arith), encoding="utf-8")
            report = {"feature_slug": "simple-crud"}
            problems = verify_arithmetic_consistency(report, {}, [], base_dir=tmp_path)
            self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
