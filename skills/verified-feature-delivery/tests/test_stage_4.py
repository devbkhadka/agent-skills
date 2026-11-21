#!/usr/bin/env python3
"""Unit tests for the Stage 4 milestone harness and the binding projection check.

The normalisation table is the whole stage, so most of these tests are the two directions of
masking held against each other, per medium: two captures of one state differing only in data
must fingerprint alike, and two states differing only in wording must not. A test that only
checked the first direction would pass against a normaliser that hashes an empty string.
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
CASES_DIR = TESTS_DIR / "cases" / "stage-4"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def load_executable(name, module_name):
    loader = SourceFileLoader(module_name, str(SKILL_DIR / "bin" / name))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


verify_projections = load_executable("verify-projections", "verify_projections")
milestone_harness = load_executable("milestone-harness", "milestone_harness")

import lib.normalisation as normalisation
import lib.rollback as rollback


def fingerprint(medium, artifact, **kwargs):
    return normalisation.capture_projection(medium, artifact, **kwargs)[1]


class TestRenderedUiNormalisation(unittest.TestCase):
    POPULATED_TEN = (
        '<div class="MuiBox-root css-1a2b" data-testid="results-pane">'
        '<h2 class="title">Found 10 matches for "database"</h2>'
        '<button class="btn btn-primary" aria-label="Clear filters">Clear</button>'
        '<ul role="list"><li role="listitem" key="k-1"><span>Result 1: SQL indexing guide</span></li></ul></div>'
    )
    POPULATED_MANY = (
        '<section class="theme-dark results-container" data-testid="search-results-wrapper">'
        '<h2 class="header-bold">Found 4210 matches for "caching"</h2>'
        '<button class="custom-button" aria-label="Clear filters">Clear</button>'
        '<ol role="list"><li role="listitem" key="node-99"><span>Result 4210: Distributed caching patterns</span></li></ol></section>'
    )
    EMPTY = (
        '<div class="MuiBox-root" data-testid="results-pane">'
        '<h2 class="title">No results found for "database"</h2>'
        '<button class="btn" aria-label="Clear filters">Clear</button></div>'
    )

    def test_data_differences_fingerprint_alike(self):
        first = fingerprint("rendered_ui", self.POPULATED_TEN,
                            recorded_values=["database", "SQL indexing guide"])
        second = fingerprint("rendered_ui", self.POPULATED_MANY,
                             recorded_values=["caching", "Distributed caching patterns"])
        self.assertEqual(first, second)

    def test_wording_differences_fingerprint_apart(self):
        populated = fingerprint("rendered_ui", self.POPULATED_TEN, recorded_values=["database"])
        empty = fingerprint("rendered_ui", self.EMPTY, recorded_values=["database"])
        self.assertNotEqual(populated, empty)

    def test_kept_and_dropped(self):
        normalised, _ = normalisation.capture_projection(
            "rendered_ui", self.POPULATED_TEN, recorded_values=["database"]
        )
        self.assertIn("[button|name=Clear filters]", normalised)
        self.assertIn("[list]", normalised)
        self.assertIn("[listitem]", normalised)
        self.assertNotIn("MuiBox", normalised)
        self.assertNotIn("data-testid", normalised)
        self.assertNotIn("k-1", normalised)

    def test_aria_state_is_kept(self):
        busy = fingerprint("rendered_ui", '<ul role="list" aria-busy="true"><li role="listitem">a</li></ul>')
        settled = fingerprint("rendered_ui", '<ul role="list"><li role="listitem">a</li></ul>')
        self.assertNotEqual(busy, settled)

    def test_script_content_is_not_observable(self):
        with_script = fingerprint("rendered_ui", '<div><script>window.__k=1</script><h2>Ready</h2></div>')
        without = fingerprint("rendered_ui", '<div><h2>Ready</h2></div>')
        self.assertEqual(with_script, without)

    def test_a_structured_capture_is_refused(self):
        with self.assertRaises(normalisation.NormalisationRefused):
            normalisation.capture_projection("rendered_ui", {"normalised_projection": "[button]"})


class TestApiResponseNormalisation(unittest.TestCase):
    def response(self, total, item_id, score, cursor, request_id):
        return {
            "status_code": 200,
            "headers": {
                "content-type": "application/json",
                "date": "Sat, 22 Aug 2026 11:00:00 GMT",
                "x-request-id": request_id,
            },
            "body": {
                "status": "ok",
                "total_count": total,
                "cursor": cursor,
                "items": [{"id": item_id, "score": score, "active": True}],
            },
        }

    def test_scalar_differences_fingerprint_alike(self):
        first = fingerprint("api_response", self.response(10, "item_1", 0.98, "cur_xyz", "req_a"),
                            contract_headers=["content-type"])
        second = fingerprint("api_response", self.response(8500, "item_999", 0.42, "cur_abc", "req_b"),
                             contract_headers=["content-type"])
        self.assertEqual(first, second)

    def test_envelope_key_differences_fingerprint_apart(self):
        populated = fingerprint("api_response", self.response(10, "item_1", 0.98, "cur_xyz", "req_a"),
                                contract_headers=["content-type"])
        empty = fingerprint("api_response", {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": {"status": "empty", "message": "no records found", "total_count": 0, "items": []},
        }, contract_headers=["content-type"])
        self.assertNotEqual(populated, empty)

    def test_uncontracted_headers_are_dropped(self):
        normalised, _ = normalisation.capture_projection(
            "api_response", self.response(10, "item_1", 0.98, "cur_xyz", "req_a"),
            contract_headers=["content-type"],
        )
        self.assertIn("header:content-type=application/json", normalised)
        self.assertNotIn("x-request-id", normalised)

    def test_raw_http_text_is_accepted(self):
        text = (
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nDate: Sat, 22 Aug 2026 11:00:00 GMT\r\n"
            "\r\n{\"status\": \"ok\", \"total_count\": 10, \"items\": [], \"cursor\": \"cur_xyz\"}"
        )
        normalised, _ = normalisation.capture_projection(
            "api_response", text, contract_headers=["content-type"]
        )
        self.assertIn("status:200", normalised)
        self.assertIn("total_count(number)", normalised)

    def test_a_capture_without_a_status_is_refused(self):
        with self.assertRaises(normalisation.NormalisationRefused):
            normalisation.capture_projection("api_response", {"body": {"status": "ok"}})


class TestProcessExitNormalisation(unittest.TestCase):
    def run_of(self, timestamp, pid, path, frame, fps, seconds, stderr=""):
        return {
            "exit_code": 0,
            "stdout": (
                "[%s] PID %d: Processing %s\n"
                "frame=%d fps=%s q=28.0 size=1024kB time=00:00:02.00 bitrate=4194.3kbits/s speed=1.0x\n"
                "[info] Transcode completed successfully in %ss" % (timestamp, pid, path, frame, fps, seconds)
            ),
            "stderr": stderr,
        }

    def test_timestamps_pids_paths_and_counters_fingerprint_alike(self):
        first = fingerprint("process_exit", self.run_of(
            "2026-08-22T11:00:01Z", 14202, "/var/app/scratch/job_1/chunk_001.mp4", 120, "60.2", "2.14"))
        second = fingerprint("process_exit", self.run_of(
            "2026-08-22T11:05:30Z", 18999, "/tmp/scratch/job_99/chunk_042.mp4", 950, "59.8", "16.02"))
        self.assertEqual(first, second)

    def test_a_warning_on_stderr_fingerprints_apart(self):
        clean = fingerprint("process_exit", self.run_of(
            "2026-08-22T11:00:01Z", 14202, "/var/app/chunk_001.mp4", 120, "60.2", "2.14"))
        degraded = fingerprint("process_exit", self.run_of(
            "2026-08-22T11:00:01Z", 14202, "/var/app/chunk_001.mp4", 120, "60.2", "2.14",
            stderr="decode error: frame dropped"))
        self.assertNotEqual(clean, degraded)

    def test_a_nonzero_exit_fingerprints_apart(self):
        passing = fingerprint("process_exit", {"exit_code": 0, "stdout": "1 passed", "stderr": ""})
        failing = fingerprint("process_exit", {"exit_code": 1, "stdout": "1 passed", "stderr": ""})
        self.assertNotEqual(passing, failing)

    def test_census_warning_classes_are_kept(self):
        normalised, _ = normalisation.capture_projection(
            "process_exit",
            {"exit_code": 0, "stdout": "done", "stderr": "decode error: frame dropped"},
            warning_classes=["FRAME_DROPPED"],
        )
        self.assertIn("warnings:FRAME_DROPPED", normalised)

    def test_a_capture_without_an_exit_code_is_refused(self):
        with self.assertRaises(normalisation.NormalisationRefused):
            normalisation.capture_projection("process_exit", {"stdout": "done"})


class TestDatastoreStateNormalisation(unittest.TestCase):
    def downgrade(self, row_id, org_id, seats, stamp):
        return {
            "mutations": [
                {
                    "table": "subscriptions",
                    "row_identity": row_id,
                    "changed_columns": [
                        {"name": "tier", "value": "FREE"},
                        {"name": "status", "value": "ACTIVE"},
                        {"name": "seat_count", "value": seats},
                        {"name": "updated_at", "value": stamp},
                    ],
                },
                {
                    "table": "organizations",
                    "row_identity": org_id,
                    "changed_columns": [
                        {"name": "quota_status", "value": "QUOTA_EXCEEDED"},
                        {"name": "updated_at", "value": stamp},
                    ],
                },
            ],
            "constraints": [
                {"name": "fk_org_id", "outcome": "SATISFIED"},
                {"name": "chk_seat_limit", "outcome": "SATISFIED"},
            ],
        }

    def test_row_identity_and_cell_values_fingerprint_alike(self):
        first = fingerprint("datastore_state", self.downgrade("sub_uuid_111", "org_uuid_aaa", 3, 1724328000))
        second = fingerprint("datastore_state", self.downgrade("sub_uuid_999", "org_uuid_zzz", 5, 1724331600))
        self.assertEqual(first, second)

    def test_a_different_changed_column_set_fingerprints_apart(self):
        downgraded = fingerprint("datastore_state", self.downgrade("sub_uuid_111", "org_uuid_aaa", 3, 1724328000))
        cancelled = fingerprint("datastore_state", {
            "mutations": [
                {
                    "table": "subscriptions",
                    "row_identity": "sub_uuid_111",
                    "changed_columns": [
                        {"name": "status", "value": "CANCELED"},
                        {"name": "canceled_at", "value": 1724331600},
                        {"name": "updated_at", "value": 1724331600},
                    ],
                }
            ],
            "constraints": [{"name": "fk_org_id", "outcome": "SATISFIED"}],
        })
        self.assertNotEqual(downgraded, cancelled)

    def test_a_failed_constraint_fingerprints_apart(self):
        satisfied = fingerprint("datastore_state", self.downgrade("sub_uuid_111", "org_uuid_aaa", 3, 1724328000))
        violated_source = self.downgrade("sub_uuid_111", "org_uuid_aaa", 3, 1724328000)
        violated_source["constraints"][1] = {"name": "chk_seat_limit", "outcome": "VIOLATED"}
        self.assertNotEqual(satisfied, fingerprint("datastore_state", violated_source))


class TestDeviceActuationNormalisation(unittest.TestCase):
    def test_operands_and_jitter_fingerprint_alike(self):
        first = fingerprint("device_actuation", {"commands": [
            {"opcode": "OP_MOTOR_SET_ANGLE", "operands": [0, 45.0], "elapsed_us": 1002},
            {"opcode": "OP_SENSOR_EXPOSE", "operands": [100], "elapsed_us": 124310},
        ]})
        second = fingerprint("device_actuation", {"commands": [
            {"opcode": "OP_MOTOR_SET_ANGLE", "operands": [0, 90.0], "elapsed_us": 1005},
            {"opcode": "OP_SENSOR_EXPOSE", "operands": [250], "elapsed_us": 118002},
        ]})
        self.assertEqual(first, second)

    def test_a_different_opcode_sequence_fingerprints_apart(self):
        capture = fingerprint("device_actuation", {"commands": [
            {"opcode": "OP_MOTOR_SET_ANGLE", "operands": [0, 45.0]},
            {"opcode": "OP_SENSOR_EXPOSE", "operands": [100]},
        ]})
        calibrate = fingerprint("device_actuation", {"commands": [
            {"opcode": "OP_MOTOR_HOME", "operands": [0]},
            {"opcode": "OP_SENSOR_SELFTEST", "operands": []},
        ]})
        self.assertNotEqual(capture, calibrate)

    def test_a_text_trace_drops_its_timing_prefix(self):
        structured = fingerprint("device_actuation", {"commands": [
            {"opcode": "OP_MOTOR_SET_ANGLE", "operands": [0, 45.0]},
        ]})
        text = fingerprint("device_actuation", ["[T+0.001s] OP_MOTOR_SET_ANGLE(axis=0, deg=45.00)"])
        self.assertEqual(structured, text)


class TestAgentTranscriptNormalisation(unittest.TestCase):
    def transcript(self, topic, path, count, subject, tokens, latency):
        return {
            "tool_calls": [
                {"name": "query_graph", "arguments": {"topic": topic}, "prompt_tokens": tokens, "latency_ms": latency},
                {"name": "view_file", "arguments": {"path": path}, "prompt_tokens": tokens, "latency_ms": latency},
            ],
            "final_message": "Found %d %s modules in the repository." % (count, subject),
        }

    def test_arguments_counts_and_latencies_fingerprint_alike(self):
        first = fingerprint("agent_transcript",
                            self.transcript("auth", "/src/auth.ts", 2, "authentication", 45, 120),
                            recorded_values=["authentication"])
        second = fingerprint("agent_transcript",
                             self.transcript("billing", "/src/billing.ts", 5, "billing", 210, 340),
                             recorded_values=["billing"])
        self.assertEqual(first, second)

    def test_a_different_tool_sequence_fingerprints_apart(self):
        two_calls = fingerprint("agent_transcript",
                                self.transcript("auth", "/src/auth.ts", 2, "authentication", 45, 120),
                                recorded_values=["authentication"])
        one_call = fingerprint("agent_transcript", {
            "tool_calls": [{"name": "query_graph", "arguments": {"topic": "unknown"}}],
            "final_message": "No modules found matching the query.",
        })
        self.assertNotEqual(two_calls, one_call)

    def test_a_different_final_template_fingerprints_apart(self):
        found = fingerprint("agent_transcript", {
            "tool_calls": [{"name": "query_graph", "arguments": {"topic": "auth"}}],
            "final_message": "Found 2 modules in the repository.",
        })
        none = fingerprint("agent_transcript", {
            "tool_calls": [{"name": "query_graph", "arguments": {"topic": "auth"}}],
            "final_message": "No modules found matching the query.",
        })
        self.assertNotEqual(found, none)


class TestNormalisedFormPinned(unittest.TestCase):
    """Pin the normalised form per medium, so the documented projections stay the real ones."""

    def normalised(self, medium, artifact, **kwargs):
        return normalisation.capture_projection(medium, artifact, **kwargs)[0]

    def test_rendered_ui_form(self):
        self.assertEqual(
            self.normalised("rendered_ui", TestRenderedUiNormalisation.POPULATED_TEN,
                            recorded_values=["database", "SQL indexing guide"]),
            '[heading][text|Found # matches for "<value>"][button|name=Clear filters][text|Clear]'
            '[list][listitem][text|Result #: <value>]',
        )

    def test_api_response_form(self):
        artifact = {
            "status_code": 200,
            "headers": {"content-type": "application/json", "x-request-id": "req_88ab91c0"},
            "body": {
                "status": "ok",
                "total_count": 10,
                "cursor": "cur_xyz",
                "items": [{"id": "item_1", "score": 0.98, "active": True}],
            },
        }
        self.assertEqual(
            self.normalised("api_response", artifact, contract_headers=["content-type"]),
            "status:200|header:content-type=application/json|keys:cursor(string),"
            "items(array[object{active:boolean,id:string,score:number}]),status(string),"
            "total_count(number)",
        )

    def test_process_exit_form(self):
        artifact = {
            "exit_code": 0,
            "stdout": (
                "[2026-08-22T11:00:01Z] PID 14202: Processing /var/app/scratch/job_1/chunk_001.mp4\n"
                "frame=120 fps=60.2 q=28.0 size=1024kB time=00:00:02.00 bitrate=4194.3kbits/s speed=1.0x\n"
                "[info] Transcode completed successfully in 2.14s"
            ),
            "stderr": "",
        }
        self.assertEqual(
            self.normalised("process_exit", artifact),
            "exit:0|stdout:PID <val>: Processing <path>\\n"
            "frame=<val> fps=<val> q=<val> size=<val>kB time=<val> bitrate=<val>kbits/s speed=<val>x\\n"
            "[info] Transcode completed successfully in <val>s|stderr:<empty>|warnings:<none>",
        )

    def test_datastore_state_form(self):
        artifact = {
            "mutations": [
                {"table": "subscriptions", "row_identity": "sub_uuid_111", "changed_columns": [
                    {"name": "tier", "value": "FREE"},
                    {"name": "status", "value": "ACTIVE"},
                    {"name": "seat_count", "value": 3},
                    {"name": "updated_at", "value": 1724328000},
                ]},
                {"table": "organizations", "row_identity": "org_uuid_aaa", "changed_columns": [
                    {"name": "quota_status", "value": "QUOTA_EXCEEDED"},
                    {"name": "updated_at", "value": 1724328000},
                ]},
            ],
            "constraints": [
                {"name": "fk_org_id", "outcome": "SATISFIED"},
                {"name": "chk_seat_limit", "outcome": "SATISFIED"},
            ],
        }
        self.assertEqual(
            self.normalised("datastore_state", artifact),
            "tables:organizations(quota_status:string,updated_at:number),"
            "subscriptions(seat_count:number,status:string,tier:string,updated_at:number)"
            "|constraints:chk_seat_limit(SATISFIED),fk_org_id(SATISFIED)",
        )

    def test_device_actuation_form(self):
        artifact = {"commands": [
            {"opcode": "OP_MOTOR_SET_ANGLE", "operands": [0, 45.0], "elapsed_us": 1002},
            {"opcode": "OP_SENSOR_EXPOSE", "operands": [100], "elapsed_us": 124310},
            {"opcode": "OP_FRAME_TRANSFER", "operands": ["0x20001000"], "elapsed_us": 252004},
        ]}
        self.assertEqual(
            self.normalised("device_actuation", artifact),
            "opcodes:[OP_MOTOR_SET_ANGLE(<operand>,<operand>),OP_SENSOR_EXPOSE(<operand>),"
            "OP_FRAME_TRANSFER(<operand>)]",
        )

    def test_agent_transcript_form(self):
        artifact = {
            "tool_calls": [
                {"name": "query_graph", "arguments": {"topic": "auth"}, "prompt_tokens": 45, "latency_ms": 120},
                {"name": "view_file", "arguments": {"path": "/src/auth.ts"}, "prompt_tokens": 120, "latency_ms": 80},
            ],
            "final_message": "Found 2 authentication modules in the repository.",
        }
        self.assertEqual(
            self.normalised("agent_transcript", artifact, recorded_values=["authentication"]),
            "tool_calls:[query_graph(topic=<val>),view_file(path=<val>)]"
            "|final_template:Found <val> <val> modules in the repository.",
        )


class TestClosedSetsAndVacuity(unittest.TestCase):
    def test_the_six_media_are_the_closed_set(self):
        self.assertEqual(len(normalisation.CANONICAL_MEDIA), 6)
        for medium in normalisation.CANONICAL_MEDIA:
            normalisation.refuse_unrecognised_medium(medium)

    def test_a_misspelt_medium_is_refused(self):
        with self.assertRaises(normalisation.NormalisationRefused) as raised:
            normalisation.refuse_unrecognised_medium("rendered_u1")
        self.assertEqual(raised.exception.code, "UNKNOWN_VERIFICATION_MEDIUM")

    def test_an_empty_capture_is_refused(self):
        for artifact in ({}, "", [{}], None):
            with self.assertRaises(normalisation.NormalisationRefused) as raised:
                normalisation.capture_projection("rendered_ui", artifact)
            self.assertEqual(raised.exception.code, "EMPTY_CAPTURED_ARTIFACT")

    def test_a_capture_record_declaring_its_own_fingerprint_is_refused(self):
        computed, problems = normalisation.compute_captured_projections(
            [{
                "evidence_key": "state:IDLE",
                "observer_id": "operator",
                "captured_artifact": "<div><h2>Ready</h2></div>",
                "fingerprint_sha256": "0" * 64,
            }],
            {"operator": "rendered_ui"},
        )
        self.assertEqual(computed, [])
        self.assertTrue(any("UNKNOWN_PROJECTION_ATTRIBUTE" in problem for problem in problems))

    def test_a_capture_attributed_to_an_unregistered_observer_is_refused(self):
        computed, problems = normalisation.compute_captured_projections(
            [{
                "evidence_key": "state:IDLE",
                "observer_id": "auditor",
                "captured_artifact": "<div><h2>Ready</h2></div>",
            }],
            {"operator": "rendered_ui"},
        )
        self.assertEqual(computed, [])
        self.assertTrue(any("UNREGISTERED_OBSERVER" in problem for problem in problems))


class TestProjectionGate(unittest.TestCase):
    def check(self, directory):
        with open(directory / "projections.json", encoding="utf-8") as handle:
            projections = json.load(handle)
        with open(directory / "scenario-matrix.json", encoding="utf-8") as handle:
            matrix = json.load(handle)
        return verify_projections.check_stage_4_projections(projections, matrix, artifact_root=directory)

    def test_collision_is_refused(self):
        code, message = self.check(CASES_DIR / "fingerprint-collision")
        self.assertEqual(code, 1)
        self.assertIn("FINGERPRINT_COLLISION", message)

    def test_coverage_gap_is_refused(self):
        code, message = self.check(CASES_DIR / "projection-coverage-gap")
        self.assertEqual(code, 1)
        self.assertIn("PROJECTION_COVERAGE_GAP", message)

    def test_covered_and_distinct_projections_are_accepted(self):
        code, message = self.check(CASES_DIR / "positive" / "valid-projections")
        self.assertEqual(code, 0)
        self.assertIn("PROJECTIONS_VERIFIED_STAGE_4", message)

    def test_coverage_is_per_observer(self):
        with open(CASES_DIR / "positive" / "valid-projections" / "projections.json", encoding="utf-8") as handle:
            projections = json.load(handle)
        with open(CASES_DIR / "positive" / "valid-projections" / "scenario-matrix.json", encoding="utf-8") as handle:
            matrix = json.load(handle)
        projections["captured_projections"] = [
            record for record in projections["captured_projections"]
            if record["observer_id"] != "system_telemetry"
        ]
        code, message = verify_projections.check_stage_4_projections(projections, matrix)
        self.assertEqual(code, 1)
        self.assertIn("PROJECTION_COVERAGE_GAP", message)

    def test_the_stage_2_half_stays_advisory(self):
        with open(CASES_DIR / "fingerprint-collision" / "scenario-matrix.json", encoding="utf-8") as handle:
            matrix = json.load(handle)
        code, message = verify_projections.check_stage_2_projections(matrix)
        self.assertEqual(code, 0)
        self.assertIn("PROJECTIONS_VERIFIED_STAGE_2", message)


class TestMaskingDirectionsThroughTheCli(unittest.TestCase):
    def test_data_invariance_fixture_is_accepted(self):
        code, message = verify_projections.run_data_invariance_check(
            CASES_DIR / "positive" / "data-invariant"
        )
        self.assertEqual(code, 0, message)

    def test_wording_distinctness_fixture_is_accepted(self):
        code, message = verify_projections.run_wording_distinctness_check(
            CASES_DIR / "positive" / "wording-distinct"
        )
        self.assertEqual(code, 0, message)

    def test_data_invariance_would_catch_a_raw_hash(self):
        """The invariance direction is what a raw-artifact hash fails, so prove it fails it."""
        document, error = verify_projections.load_capture_set(
            CASES_DIR / "positive" / "data-invariant"
        )
        self.assertIsNone(error)
        raw = {
            normalisation.compute_fingerprint(capture["captured_artifact"])
            for capture in document["captures"]
        }
        self.assertEqual(len(raw), 2)


class TestMilestoneHarness(unittest.TestCase):
    def grade(self, directory):
        return milestone_harness.validate_milestone_execution(directory, write=False)

    def test_variance_shape_unexercised_is_refused(self):
        code, message = self.grade(CASES_DIR / "variance-shape-unexercised")
        self.assertEqual(code, 1)
        self.assertIn("VARIANCE_SHAPE_UNEXERCISED", message)

    def test_silent_boundary_degradation_is_refused(self):
        code, message = self.grade(CASES_DIR / "silent-boundary-degradation")
        self.assertEqual(code, 1)
        self.assertIn("SILENT_BOUNDARY_DEGRADATION", message)

    def test_provisional_fixture_outstanding_is_refused(self):
        code, message = self.grade(CASES_DIR / "provisional-outstanding")
        self.assertEqual(code, 1)
        self.assertIn("PROVISIONAL_FIXTURE_OUTSTANDING", message)

    def test_unclassified_boundary_is_refused(self):
        code, message = self.grade(CASES_DIR / "unclassified-boundary")
        self.assertEqual(code, 1)
        self.assertIn("UNCLASSIFIED_BOUNDARY", message)

    def test_a_verified_run_is_accepted(self):
        code, message = self.grade(CASES_DIR / "positive" / "valid-milestone")
        self.assertEqual(code, 0, message)
        self.assertIn("MILESTONES_VERIFIED_STAGE_4", message)

    def test_a_verified_run_writes_its_gate_artifact(self):
        with tempfile.TemporaryDirectory() as workspace:
            feature_dir = Path(workspace) / "search-console-web-ui"
            shutil.copytree(CASES_DIR / "positive" / "valid-milestone", feature_dir)
            code, message = milestone_harness.validate_milestone_execution(feature_dir, write=True)
            self.assertEqual(code, 0, message)
            self.assertTrue((feature_dir / "milestones" / "evidence-summary.json").is_file())
            with open(feature_dir / "projections.json", encoding="utf-8") as handle:
                written = json.load(handle)
            self.assertTrue(written["captured_projections"])

    def test_a_milestone_with_a_misspelt_medium_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            feature_dir = Path(workspace) / "search-console-web-ui"
            shutil.copytree(CASES_DIR / "positive" / "valid-milestone", feature_dir)
            executions_path = feature_dir / "milestones" / "executions.json"
            executions = json.loads(executions_path.read_text(encoding="utf-8"))
            executions["milestones"][0]["verification_medium"] = "rendered_ui "
            executions_path.write_text(json.dumps(executions), encoding="utf-8")
            code, message = milestone_harness.validate_milestone_execution(feature_dir, write=False)
            self.assertEqual(code, 1)
            self.assertIn("UNKNOWN_VERIFICATION_MEDIUM", message)

    def test_a_milestone_declaring_its_own_outcome_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            feature_dir = Path(workspace) / "search-console-web-ui"
            shutil.copytree(CASES_DIR / "positive" / "valid-milestone", feature_dir)
            executions_path = feature_dir / "milestones" / "executions.json"
            executions = json.loads(executions_path.read_text(encoding="utf-8"))
            executions["milestones"][0]["status"] = "PASSED"
            executions_path.write_text(json.dumps(executions), encoding="utf-8")
            code, message = milestone_harness.validate_milestone_execution(feature_dir, write=False)
            self.assertEqual(code, 1)
            self.assertIn("UNKNOWN_EXECUTION_ATTRIBUTE", message)

    def test_all_six_media_are_fingerprinted(self):
        code, message = milestone_harness.verify_all_media(CASES_DIR / "positive" / "all-media")
        self.assertEqual(code, 0, message)
        for medium in normalisation.CANONICAL_MEDIA:
            self.assertIn(medium, message)

    def test_a_missing_medium_sample_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            fixture = Path(workspace) / "all-media"
            shutil.copytree(CASES_DIR / "positive" / "all-media", fixture)
            samples_path = fixture / "media-samples.json"
            document = json.loads(samples_path.read_text(encoding="utf-8"))
            document["samples"] = document["samples"][:-1]
            samples_path.write_text(json.dumps(document), encoding="utf-8")
            code, message = milestone_harness.verify_all_media(fixture)
            self.assertEqual(code, 1)
            self.assertIn("agent_transcript", message)


class TestRegressionRouting(unittest.TestCase):
    def test_the_four_edges_route_and_land(self):
        with tempfile.TemporaryDirectory() as workspace:
            fixture = Path(workspace) / "rollback-routing"
            shutil.copytree(CASES_DIR / "positive" / "rollback-routing", fixture)
            code, message = milestone_harness.test_rollback_routing(fixture)
            self.assertEqual(code, 0, message)
            for cause in ("FINGERPRINT_COLLISION", "CONTRACT_DRIFT", "UNCLASSIFIED_BOUNDARY",
                          "DOMAIN_TYPE_PRECISION_MISMATCH"):
                self.assertIn(cause, message)

    def test_the_targets_come_from_the_transition_table(self):
        edges = rollback.rollback_edges()
        self.assertEqual(rollback.route_cause("FINGERPRINT_COLLISION"), "STAGE_2_SCENARIO_SIMULATION")
        self.assertEqual(rollback.route_cause("CONTRACT_DRIFT"), "STAGE_1_CONTRACT_PROBING")
        self.assertEqual(rollback.route_cause("UNCLASSIFIED_BOUNDARY"), "STAGE_1_CONTRACT_PROBING")
        self.assertEqual(rollback.route_cause("DOMAIN_TYPE_PRECISION_MISMATCH"), "STAGE_0_DOMAIN_INGESTION")
        for target, causes in edges.items():
            for cause in causes:
                self.assertEqual(rollback.route_cause(cause), target)

    def test_a_cause_outside_the_table_is_refused(self):
        self.assertIsNone(rollback.route_cause("IMPLEMENTATION_BUG"))

    def test_the_fourth_edge_records_the_boundary_and_raises_an_amendment(self):
        with tempfile.TemporaryDirectory() as workspace:
            feature_dir = Path(workspace) / "search-console-web-ui"
            shutil.copytree(CASES_DIR / "unclassified-boundary", feature_dir)
            bundle = milestone_harness.RunBundle(feature_dir)
            records, problems = bundle.milestone_records()
            self.assertEqual(problems, [])
            findings, _routing_problems = milestone_harness.classify_regressions(bundle, records)
            self.assertEqual(findings[0].cause, "UNCLASSIFIED_BOUNDARY")
            code, message = milestone_harness.route_and_execute(bundle, findings[0], write=True)
            self.assertEqual(code, 1)
            self.assertIn("UNCLASSIFIED_BOUNDARY", message)

            manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            other = [row for row in manifest["boundary_classes"] if row["class_name"] == "OTHER"][0]
            self.assertEqual(other["verdict"], "PRESENT")
            self.assertTrue(manifest["register_amendments"])
            state = json.loads((feature_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["current_state"], "STAGE_1_CONTRACT_PROBING")

    def test_a_registered_property_is_not_an_unclassified_boundary(self):
        manifest = {"boundary_classes": [
            {"class_name": "UI_RUNTIME", "verdict": "PRESENT",
             "properties": [{"property_name": "overlay_portal_mounting", "verdict": "PROBED"}]}
        ]}
        finding, _problems = rollback.classify_failure(
            "M9",
            {
                "boundary_identifier": "react_client_runtime",
                "boundary_property": "overlay_portal_mounting",
                "broken_invariant": "one_dispatch_per_submit",
                "description": "The overlay mounted outside the portal root and the assertion on it failed.",
            },
            manifest, None, {},
        )
        self.assertEqual(finding.cause, "BROKEN_INVARIANT")
        self.assertEqual(finding.target_state(), "STAGE_2_SCENARIO_SIMULATION")


class TestDeclaredOutcomeSuccess(unittest.TestCase):
    """A milestone that drives an approved error state passes only on the code the matrix declares.

    Each test writes a hermetic feature directory: a matrix with a process_exit observer and an
    api_response observer, a DAG whose milestones claim those projections, and one execution
    record per projection. Only the captured code under test varies.
    """

    MATRIX = {
        "schema_version": "1.0.0",
        "feature_slug": "declared-outcome",
        "observers": [
            {"observer_id": "agent", "medium": "process_exit"},
            {"observer_id": "client", "medium": "api_response"},
        ],
        "states": [
            {"state_id": "SESSION_STARTED", "evidence_key": "state:SESSION_STARTED",
             "observable_projections": [
                 {"observer_id": "agent", "medium": "process_exit",
                  "discriminator": {"exit_code": "0", "outcome": "start_ok"}},
                 {"observer_id": "client", "medium": "api_response",
                  "discriminator": {"status_code": "201"}}]},
            {"state_id": "SESSION_UNRESOLVED_EXIT_1", "evidence_key": "state:SESSION_UNRESOLVED_EXIT_1",
             "observable_projections": [
                 {"observer_id": "agent", "medium": "process_exit",
                  "discriminator": {"exit_code": "1", "outcome": "exit_1_error"}},
                 {"observer_id": "client", "medium": "api_response",
                  "discriminator": {"status_code": "409", "error": "conflict"}}]},
        ],
    }
    DAG = {"slices": [{"slice_id": "s01", "milestones": [
        {"milestone_id": "M_EXIT", "verification_medium": "process_exit",
         "claims": ["state:SESSION_STARTED@agent", "state:SESSION_UNRESOLVED_EXIT_1@agent"]},
        {"milestone_id": "M_API", "verification_medium": "api_response",
         "claims": ["state:SESSION_STARTED@client", "state:SESSION_UNRESOLVED_EXIT_1@client"]},
    ]}]}

    def exit_record(self, evidence_key, exit_code, milestone_id="M_EXIT"):
        return {
            "milestone_id": milestone_id, "slice_id": "s01", "verification_medium": "process_exit",
            "executed_command": "agent-desk update", "evidence_key": evidence_key,
            "observer_id": "agent",
            "captured_evidence": {"exit_code": exit_code, "stdout": "out", "stderr": ""},
        }

    def api_record(self, evidence_key, status_code):
        return {
            "milestone_id": "M_API", "slice_id": "s01", "verification_medium": "api_response",
            "executed_command": "vitest run api", "evidence_key": evidence_key, "observer_id": "client",
            "captured_evidence": {"status_code": status_code, "body": {"id": "x"}},
        }

    def records(self, unresolved_exit=1, unresolved_status=409, started_exit=0):
        return [
            self.exit_record("state:SESSION_STARTED", started_exit),
            self.exit_record("state:SESSION_UNRESOLVED_EXIT_1", unresolved_exit),
            self.api_record("state:SESSION_STARTED", 201),
            self.api_record("state:SESSION_UNRESOLVED_EXIT_1", unresolved_status),
        ]

    def grade(self, records, matrix=None, dag=None):
        """Grade `records`; a str matrix or dag is written verbatim, so it can be malformed."""
        def text(document, default):
            if isinstance(document, str):
                return document
            return json.dumps(default if document is None else document)

        with tempfile.TemporaryDirectory() as workspace:
            feature_dir = Path(workspace) / "declared-outcome"
            (feature_dir / "milestones").mkdir(parents=True)
            (feature_dir / "flow-simulation-matrix.json").write_text(
                text(matrix, self.MATRIX), encoding="utf-8")
            (feature_dir / "execution-dag.json").write_text(text(dag, self.DAG), encoding="utf-8")
            (feature_dir / "milestones" / "executions.json").write_text(json.dumps({
                "schema_version": "1.0.0", "feature_slug": "declared-outcome",
                "generated_at": "2026-11-08T10:00:00Z", "milestones": records,
            }), encoding="utf-8")
            return milestone_harness.validate_milestone_execution(feature_dir, write=False)

    def test_the_declared_exit_observed_passes(self):
        code, message = self.grade(self.records())
        self.assertEqual(code, 0, message)
        self.assertNotIn("UNTRACED_FAILURE", message)
        self.assertIn("MILESTONES_VERIFIED_STAGE_4", message)

    def test_a_zero_exit_where_exit_1_is_declared_fails(self):
        code, message = self.grade(self.records(unresolved_exit=0))
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE", message)
        self.assertIn("M_EXIT", message)

    def test_a_different_nonzero_exit_where_exit_1_is_declared_fails(self):
        code, message = self.grade(self.records(unresolved_exit=2))
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE", message)

    def test_a_nonzero_exit_on_a_state_declaring_exit_0_is_still_untraced(self):
        code, message = self.grade(self.records(started_exit=1))
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE", message)

    def test_a_nonzero_exit_on_a_state_declaring_no_code_is_still_untraced(self):
        matrix = json.loads(json.dumps(self.MATRIX))
        matrix["states"][1]["observable_projections"][0]["discriminator"]["exit_code"] = "pending"
        code, message = self.grade(self.records(), matrix=matrix)
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE", message)

    def test_the_declared_status_observed_passes_and_a_2xx_instead_fails(self):
        code, message = self.grade(self.records(unresolved_status=409))
        self.assertEqual(code, 0, message)
        code, message = self.grade(self.records(unresolved_status=200))
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE", message)
        self.assertIn("M_API", message)

    def test_a_mismatch_with_a_failure_trace_routes_as_a_failure(self):
        records = self.records(unresolved_exit=0)
        records[1]["failure_trace"] = {
            "boundary_identifier": "agent_desk_cli", "description": "exited 0 instead of 1",
        }
        code, message = self.grade(records)
        self.assertEqual(code, 1)
        # The trace reached the failure classifier, exactly as any failed milestone's does.
        self.assertIn("UNROUTABLE_FAILURE: the failure of M_EXIT", message)

    def test_no_extra_record_field_turns_a_mismatch_into_a_pass(self):
        for key, value in (
            ("expected_exit_code", 0), ("declared_exit_code", 0), ("exit_code", 0),
            ("status", "PASSED"), ("verdict", "PASSED"), ("success", True),
            ("expected_outcome", {"medium": "process_exit", "code": 0}),
        ):
            records = self.records(unresolved_exit=0)
            records[1][key] = value
            code, message = self.grade(records)
            self.assertEqual(code, 1, "%s let a mismatch pass" % key)
            self.assertIn("UNKNOWN_EXECUTION_ATTRIBUTE", message)

    def test_an_unclaimed_milestone_cannot_borrow_an_error_state(self):
        """Relabelling a failed capture with an error state's key is not enough on its own."""
        dag = json.loads(json.dumps(self.DAG))
        dag["slices"][0]["milestones"][0]["claims"] = ["state:SESSION_STARTED@agent"]
        dag["slices"][0]["milestones"].append({
            "milestone_id": "M_OTHER", "verification_medium": "process_exit",
            "claims": ["state:SESSION_UNRESOLVED_EXIT_1@agent"]})
        code, message = self.grade(self.records(), dag=dag)
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE", message)

    def matrix_with(self, state_index, projection_index, discriminator=None, medium=None):
        matrix = json.loads(json.dumps(self.MATRIX))
        projection = matrix["states"][state_index]["observable_projections"][projection_index]
        if discriminator is not None:
            projection["discriminator"] = discriminator
        if medium is not None:
            projection["medium"] = medium
        return matrix

    # --- only the declared medium's field is graded -------------------------------------

    def test_a_declared_status_ignores_a_passing_exit_code(self):
        records = self.records()
        records[3]["captured_evidence"] = {"exit_code": 0, "status_code": 500, "body": {"id": "x"}}
        code, message = self.grade(records)
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE: milestone M_API", message)

    def test_a_declared_status_ignores_a_failing_exit_code(self):
        records = self.records()
        records[3]["captured_evidence"] = {"exit_code": 1, "status_code": 409, "body": {"id": "x"}}
        code, message = self.grade(records)
        self.assertEqual(code, 0, message)

    def test_a_declared_exit_ignores_the_status(self):
        signal = milestone_harness.success_signal
        expected = ("process_exit", 1)
        self.assertIs(signal({"captured_evidence": {"exit_code": 1, "status_code": 500}}, expected), True)
        self.assertIs(signal({"captured_evidence": {"exit_code": 0, "status_code": 200}}, expected), False)
        self.assertIsNone(signal({"captured_evidence": {"status_code": 200}}, expected))
        self.assertIs(signal({"captured_evidence": {"status_code": 409}}, ("api_response", 409)), True)
        self.assertIsNone(signal({"captured_evidence": {"exit_code": 0}}, ("api_response", 409)))
        self.assertIs(signal({"captured_evidence": "HTTP/1.1 409 Conflict\n\n{}"}, ("api_response", 409)), True)
        self.assertIs(signal({"captured_evidence": "HTTP/1.1 200 OK\n\n{}"}, ("api_response", 409)), False)

    def test_a_capture_missing_the_declared_field_never_verifies(self):
        records = self.records()
        records[3]["captured_evidence"] = {"exit_code": 0, "body": {"id": "x"}}
        code, message = self.grade(records)
        self.assertEqual(code, 1)
        self.assertIn("UNNORMALISABLE_ARTIFACT", message)
        records = self.records()
        records[1]["captured_evidence"] = {"status_code": 200, "stdout": "out"}
        code, message = self.grade(records)
        self.assertEqual(code, 1)
        self.assertIn("UNNORMALISABLE_ARTIFACT", message)

    def test_a_raw_http_capture_is_graded_against_the_declared_status(self):
        records = self.records()
        records[3]["captured_evidence"] = "HTTP/1.1 200 OK\ncontent-type: application/json\n\n{\"id\": \"x\"}"
        code, message = self.grade(records)
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE: milestone M_API", message)
        records[3]["captured_evidence"] = "HTTP/1.1 409 Conflict\ncontent-type: application/json\n\n{\"id\": \"x\"}"
        code, message = self.grade(records)
        self.assertEqual(code, 0, message)

    def test_a_malformed_exit_code_never_falls_through_to_the_status(self):
        signal = milestone_harness.success_signal
        for malformed in (True, False, "0", 0.0, [0]):
            evidence = {"exit_code": malformed, "status_code": 200}
            self.assertIs(signal({"captured_evidence": evidence}), False, repr(malformed))
            self.assertIs(signal({"captured_evidence": evidence}, ("process_exit", 1)), False, repr(malformed))
            self.assertIs(signal({"captured_evidence": evidence}, ("process_exit", 0)), False, repr(malformed))
        self.assertIs(signal({"captured_evidence": {"status_code": True}}), False)
        self.assertIs(signal({"captured_evidence": {"status_code": True}}, ("api_response", 409)), False)

    def test_a_declared_201_fails_on_a_200(self):
        """The approved matrix is authoritative: a success state declaring 201 does not take any 2xx."""
        records = self.records()
        records[2]["captured_evidence"]["status_code"] = 200
        code, message = self.grade(records)
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE: milestone M_API", message)

    # --- an ambiguous or unreadable declaration falls back to the default rule ----------

    def test_an_unparseable_sibling_makes_the_target_ambiguous(self):
        matrix = json.loads(json.dumps(self.MATRIX))
        matrix["states"][1]["observable_projections"].append(
            {"observer_id": "agent", "medium": "process_exit", "discriminator": {"exit_code": "pending"}})
        declared = normalisation.matrix_declared_outcomes(matrix)
        self.assertNotIn(("state:SESSION_UNRESOLVED_EXIT_1", "agent"), declared)
        code, message = self.grade(self.records(), matrix=matrix)
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE: milestone M_EXIT", message)

    def test_disagreeing_siblings_make_the_target_ambiguous(self):
        matrix = json.loads(json.dumps(self.MATRIX))
        matrix["states"][1]["observable_projections"].append(
            {"observer_id": "agent", "medium": "process_exit", "discriminator": {"exit_code": "2"}})
        self.assertNotIn(("state:SESSION_UNRESOLVED_EXIT_1", "agent"),
                         normalisation.matrix_declared_outcomes(matrix))

    def test_range_and_list_codes_fall_back_to_the_default(self):
        for atom in ("4xx", "1-4", "1,2", "", "none", "-1", "256"):
            matrix = self.matrix_with(1, 0, {"exit_code": atom})
            self.assertNotIn(("state:SESSION_UNRESOLVED_EXIT_1", "agent"),
                             normalisation.matrix_declared_outcomes(matrix), atom)
            code, message = self.grade(self.records(), matrix=matrix)
            self.assertEqual(code, 1, atom)
            self.assertIn("UNTRACED_FAILURE: milestone M_EXIT", message)
        for atom in ("4xx", "400-499"):
            matrix = self.matrix_with(1, 1, {"status_code": atom})
            code, message = self.grade(self.records(), matrix=matrix)
            self.assertEqual(code, 1, atom)
            self.assertIn("UNTRACED_FAILURE: milestone M_API", message)

    def test_a_projection_medium_contradicting_the_register_declares_nothing(self):
        matrix = self.matrix_with(1, 0, medium="api_response")
        self.assertNotIn(("state:SESSION_UNRESOLVED_EXIT_1", "agent"),
                         normalisation.matrix_declared_outcomes(matrix))
        code, message = self.grade(self.records(), matrix=matrix)
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE: milestone M_EXIT", message)

    def test_an_unreadable_observer_register_is_refused_before_grading(self):
        matrix = json.loads(json.dumps(self.MATRIX))
        matrix["observers"][0]["medium"] = "process exit"
        code, message = self.grade(self.records(), matrix=matrix)
        self.assertEqual(code, 1)
        self.assertIn("MISSING_GATE_ARTIFACT", message)
        self.assertIn("UNKNOWN_VERIFICATION_MEDIUM", message)

    def test_a_blank_evidence_key_takes_the_default_rule(self):
        records = self.records()
        records[1]["evidence_key"] = "  "
        code, message = self.grade(records)
        self.assertEqual(code, 1)
        self.assertIn("UNTRACED_FAILURE: milestone M_EXIT", message)

    def test_a_malformed_dag_honours_no_declared_code(self):
        for dag in ("{not json", {"slices": "s01"}, {"slices": [{"milestones": [
                {"milestone_id": "M_EXIT", "claims": "state:SESSION_UNRESOLVED_EXIT_1@agent"}]}]}):
            code, message = self.grade(self.records(), dag=dag)
            self.assertEqual(code, 1, dag)
            self.assertIn("UNTRACED_FAILURE: milestone M_EXIT", message)

    def test_a_malformed_matrix_honours_no_declared_code(self):
        for matrix in ("{not json", dict(self.MATRIX, states="none")):
            code, message = self.grade(self.records(), matrix=matrix)
            self.assertEqual(code, 1)
            self.assertIn("UNTRACED_FAILURE: milestone M_EXIT", message)

    def test_exit_codes_fingerprint_apart(self):
        codes = {
            fingerprint("process_exit", {"exit_code": n, "stdout": "same", "stderr": ""})
            for n in (1, 2, 3, 4)
        }
        self.assertEqual(len(codes), 4)

    def test_the_matrix_string_codes_are_read_as_declared_outcomes(self):
        declared = normalisation.matrix_declared_outcomes(self.MATRIX)
        self.assertEqual(declared[("state:SESSION_UNRESOLVED_EXIT_1", "agent")], ("process_exit", 1))
        self.assertEqual(declared[("state:SESSION_UNRESOLVED_EXIT_1", "client")], ("api_response", 409))


class TestRetiredRecords(unittest.TestCase):
    """Records whose projection a Stage 2 amendment removed are reported, never graded or deleted."""

    MATRIX = TestDeclaredOutcomeSuccess.MATRIX
    DAG = TestDeclaredOutcomeSuccess.DAG
    exit_record = TestDeclaredOutcomeSuccess.exit_record
    api_record = TestDeclaredOutcomeSuccess.api_record
    records = TestDeclaredOutcomeSuccess.records
    grade = TestDeclaredOutcomeSuccess.grade

    def removed_record(self):
        # Fingerprints exactly like state:SESSION_STARTED@agent, so grading it would collide.
        return self.exit_record("state:WORKSTREAM_REOPENED", 0)

    def test_a_record_for_a_removed_projection_is_retired_not_collided(self):
        code, message = self.grade(self.records() + [self.removed_record(), self.removed_record()])
        self.assertEqual(code, 0, message)
        self.assertNotIn("FINGERPRINT_COLLISION", message)
        self.assertIn("RETIRED: 2 execution record(s) across 1 projection(s)", message)
        self.assertIn("state:WORKSTREAM_REOPENED@agent (2 record(s))", message)

    def test_the_same_record_still_collides_while_the_matrix_declares_it(self):
        matrix = json.loads(json.dumps(self.MATRIX))
        matrix["states"].append({
            "state_id": "WORKSTREAM_REOPENED", "evidence_key": "state:WORKSTREAM_REOPENED",
            "observable_projections": [{"observer_id": "agent", "medium": "process_exit",
                                        "discriminator": {"exit_code": "0"}}]})
        code, message = self.grade(self.records() + [self.removed_record()], matrix=matrix)
        self.assertEqual(code, 1)
        self.assertIn("FINGERPRINT_COLLISION", message)
        self.assertNotIn("RETIRED", message)

    def test_retired_records_stay_in_the_ledger_and_are_summarised(self):
        with tempfile.TemporaryDirectory() as workspace:
            feature_dir = Path(workspace) / "declared-outcome"
            (feature_dir / "milestones").mkdir(parents=True)
            (feature_dir / "flow-simulation-matrix.json").write_text(json.dumps(self.MATRIX), encoding="utf-8")
            (feature_dir / "execution-dag.json").write_text(json.dumps(self.DAG), encoding="utf-8")
            executions = {
                "schema_version": "1.0.0", "feature_slug": "declared-outcome",
                "generated_at": "2026-11-08T10:00:00Z",
                "milestones": self.records() + [self.removed_record()],
            }
            executions_path = feature_dir / "milestones" / "executions.json"
            executions_path.write_text(json.dumps(executions), encoding="utf-8")
            code, message = milestone_harness.validate_milestone_execution(feature_dir, write=True)
            self.assertEqual(code, 0, message)
            self.assertEqual(json.loads(executions_path.read_text(encoding="utf-8")), executions)
            summary = json.loads((feature_dir / "milestones" / "evidence-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["retired_records"], [
                {"evidence_key": "state:WORKSTREAM_REOPENED", "observer_id": "agent", "record_count": 1}])
            self.assertEqual(len(summary["milestones_verified"]), 4)

    def test_a_retired_failure_is_not_routed_as_a_regression(self):
        failing = self.exit_record("state:WORKSTREAM_REOPENED", 3)
        code, message = self.grade(self.records() + [failing])
        self.assertEqual(code, 0, message)
        self.assertNotIn("UNTRACED_FAILURE", message)


class TestCodelessMediumDischarge(unittest.TestCase):
    """A provisional fixture pinned to a medium with no exit code or status can be discharged.

    Success for those media is computed from the capture itself (see milestone_harness
    medium_success); the record still has no field that can declare it.
    """

    DATASTORE_OK = {
        "mutations": [{"table": "seats", "changed_columns": [{"name": "org_id", "value": "o-1"}]}],
        "constraints": [{"name": "fk_org_id", "outcome": "SATISFIED"},
                        {"name": "chk_seat_limit", "outcome": "SATISFIED"}],
    }

    def grade(self, medium, captured):
        matrix = {
            "schema_version": "1.0.0", "feature_slug": "codeless",
            "observers": [{"observer_id": "obs", "medium": medium}],
            "states": [{"state_id": "SEAT_ADDED", "evidence_key": "state:SEAT_ADDED",
                        "observable_projections": [{"observer_id": "obs", "medium": medium,
                                                    "discriminator": {"label": "seat added"}}]}],
        }
        dag = {"slices": [{"slice_id": "s01", "milestones": [
            {"milestone_id": "M_REPROBE", "verification_medium": medium,
             "claims": ["state:SEAT_ADDED@obs"]}]}]}
        record = {
            "milestone_id": "M_REPROBE", "slice_id": "s01", "verification_medium": medium,
            "executed_command": "vitest run reprobe", "evidence_key": "state:SEAT_ADDED",
            "observer_id": "obs", "boundary_identifier": "pg_main", "captured_evidence": captured,
        }
        fixture = {"boundary_class": "DATASTORE", "boundary_identifier": "pg_main",
                   "fixture_status": "PROVISIONAL_FROM_SCHEMA", "pinned_reprobe_milestone": "M_REPROBE"}
        with tempfile.TemporaryDirectory() as workspace:
            feature_dir = Path(workspace) / "codeless"
            (feature_dir / "milestones").mkdir(parents=True)
            (feature_dir / "contract-fixtures").mkdir()
            (feature_dir / "contract-fixtures" / "manifest.json").write_text("{}", encoding="utf-8")
            (feature_dir / "contract-fixtures" / "pg_main.json").write_text(json.dumps(fixture), encoding="utf-8")
            (feature_dir / "flow-simulation-matrix.json").write_text(json.dumps(matrix), encoding="utf-8")
            (feature_dir / "execution-dag.json").write_text(json.dumps(dag), encoding="utf-8")
            (feature_dir / "milestones" / "executions.json").write_text(json.dumps({
                "schema_version": "1.0.0", "feature_slug": "codeless",
                "generated_at": "2026-11-08T10:00:00Z", "milestones": [record],
            }), encoding="utf-8")
            return milestone_harness.validate_milestone_execution(feature_dir, write=False)

    def test_satisfied_constraints_discharge_a_datastore_reprobe(self):
        code, message = self.grade("datastore_state", self.DATASTORE_OK)
        self.assertEqual(code, 0, message)

    def test_a_violated_constraint_does_not_discharge_and_is_not_a_regression(self):
        violated = json.loads(json.dumps(self.DATASTORE_OK))
        violated["constraints"][1]["outcome"] = "VIOLATED"
        code, message = self.grade("datastore_state", violated)
        self.assertEqual(code, 1)
        self.assertIn("PROVISIONAL_FIXTURE_OUTSTANDING", message)
        self.assertNotIn("UNTRACED_FAILURE", message)

    def test_a_rendered_page_discharges_and_an_empty_one_does_not(self):
        code, message = self.grade("rendered_ui", '<main><p role="status">Seat added</p></main>')
        self.assertEqual(code, 0, message)
        code, message = self.grade("rendered_ui", '<div class="x"></div>')
        self.assertIn("PROVISIONAL_FIXTURE_OUTSTANDING", message)

    def test_a_transcript_needs_a_final_message_to_discharge(self):
        code, message = self.grade("agent_transcript", {
            "tool_calls": [{"name": "seats.add", "arguments": {"org": "o-1"}}],
            "final_message": "Added 1 seat."})
        self.assertEqual(code, 0, message)
        code, message = self.grade("agent_transcript", {
            "tool_calls": [{"name": "seats.add", "arguments": {"org": "o-1"}}], "final_message": ""})
        self.assertIn("PROVISIONAL_FIXTURE_OUTSTANDING", message)


class TestVarianceCensusExercise(unittest.TestCase):
    FIXTURE = {
        "success_samples": [
            {"sample_id": "a", "payload": {"results": [{"metadata": {"author": "dev"}}]}},
            {"sample_id": "b", "payload": {"results": [{"metadata": {"tokens": 240, "checksum": "abc"}}]}},
        ],
        "variance_census": {"unbounded_regions": ["payload.results[].metadata"]},
    }

    def test_recorded_shapes_are_computed_from_the_samples(self):
        shapes = milestone_harness.recorded_region_shapes(self.FIXTURE)
        self.assertEqual(len(shapes["payload.results[].metadata"]), 2)
        self.assertIn("object{author:string}", shapes["payload.results[].metadata"])

    def test_an_assertion_over_one_shape_leaves_the_other_unexercised(self):
        exercised = milestone_harness.exercised_region_shapes(
            [{"asserted_payloads": [{"results": [{"metadata": {"author": "dev"}}]}]}],
            ["payload.results[].metadata"],
        )
        recorded = milestone_harness.recorded_region_shapes(self.FIXTURE)["payload.results[].metadata"]
        self.assertEqual(len(recorded - exercised["payload.results[].metadata"]), 1)


if __name__ == "__main__":
    unittest.main()
