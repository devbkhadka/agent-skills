#!/usr/bin/env python3
"""Unit tests for BDD Scenario & Mock-Boundary Contract.

Verifies:
1. Formal JSON schema compliance and reference scenario validation.
2. Strict mock boundary isolation (rejection of internal mocks and unverified boundaries).
3. Replay enforcement (rejection of ad-hoc mocks without fixture replay).
4. Upstream interface discovery validations (undefined state tokens, actions, unbound projection quantities).
5. Upstream rollback routing & cache cascade computation.
"""

import json
from pathlib import Path
import subprocess
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
EXAMPLES_DIR = SKILL_DIR / "examples" / "bdd-scenarios"
SCHEMAS_DIR = SKILL_DIR / "schemas"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import lib.bdd_scenario as bdd_scenario
import lib.rollback as rollback


class TestBDDScenarioContract(unittest.TestCase):
    def setUp(self):
        # Sample upstream artifacts for integration testing
        self.lexicon = {
            "schema_version": "1.0.0",
            "feature_slug": "order-fulfillment-pipeline",
            "bounded_context": "OrderFulfillment",
            "entities": [
                {
                    "name": "Order",
                    "attributes": [
                        {"name": "status", "type": "string"},
                        {"name": "total_cents", "type": "number", "unit_ref": "integer_cents"},
                        {"name": "order_total", "type": "number"},
                        {"name": "order_status", "type": "string"},
                        {"name": "purged_count", "type": "number"},
                        {"name": "released_lock_count", "type": "number"},
                        {"name": "retry_count", "type": "number"},
                    ]
                }
            ],
            "value_objects": [],
            "actions": [
                {"name": "SUBMIT_ORDER_CHECKOUT"},
                {"name": "ADD_ORDER_ITEM"},
                {"name": "DISPATCH_PAYMENT_REQUEST"},
                {"name": "PURGE_CANCELLED_ORDERS"},
            ],
            "state_enums": [
                {
                    "enum_name": "OrderStatus",
                    "values": [
                        "ORDER_DRAFTED",
                        "ORDER_CONFIRMED",
                        "ORDER_SUBMITTED",
                        "ORDER_CANCELLED",
                        "VALIDATION_REJECTED",
                        "PAYMENT_RETRYABLE_SUSPENDED",
                        "TERMINATED_PURGED",
                    ]
                }
            ],
            "unit_precisions": [
                {"id": "integer_cents", "base_unit": "cents", "numeric_type": "integer"}
            ],
            "actors_and_observers": {
                "actors": [
                    {"id": "customer"},
                    {"id": "system_worker"},
                    {"id": "system_scheduler"},
                ],
                "observers": [
                    {"id": "customer", "medium": "api_response"},
                    {"id": "operator", "medium": "rendered_ui"},
                    {"id": "operator", "medium": "process_exit"},
                ]
            },
            "alias_pairs": [],
            "projection_bindings": [
                {"fact_name": "order_total"},
                {"fact_name": "order_status"},
                {"fact_name": "validation_error_code"},
                {"fact_name": "purged_count"},
                {"fact_name": "released_lock_count"},
                {"fact_name": "retry_count"},
            ],
            "value_authorities": [],
            "fact_precedences": []
        }

        self.manifest = {
            "schema_version": "1.0.0",
            "feature_slug": "order-fulfillment-pipeline",
            "boundary_classes": [
                {
                    "class_name": "NETWORK_API",
                    "verdict": "PRESENT",
                    "properties": [
                        {"property_name": "POST /v1/payment/authorizations", "verdict": "PROBED"}
                    ]
                },
                {
                    "class_name": "DATASTORE",
                    "verdict": "PRESENT",
                    "properties": [
                        {"property_name": "PostgresOrderRepository", "verdict": "PROBED"}
                    ]
                },
                {"class_name": "SUBPROCESS_CLI", "verdict": "NOT_PRESENT", "absence_reason": "No subprocess boundaries used in core pipeline."},
                {"class_name": "FRAMEWORK_MIDDLEWARE", "verdict": "NOT_PRESENT", "absence_reason": "No external middleware adapters required."},
                {"class_name": "MESSAGE_TRANSPORT", "verdict": "NOT_PRESENT", "absence_reason": "Event stream uses local bus in test mode."},
                {"class_name": "DEVICE_IO", "verdict": "NOT_PRESENT", "absence_reason": "No IoT or hardware device actuation."},
                {"class_name": "MODEL_ENDPOINT", "verdict": "NOT_PRESENT", "absence_reason": "No LLM endpoints invoked."},
                {"class_name": "UI_RUNTIME", "verdict": "NOT_PRESENT", "absence_reason": "UI rendered via server template."},
                {"class_name": "VERIFICATION_HARNESS", "verdict": "NOT_PRESENT", "absence_reason": "Standard unittest runner used."},
                {"class_name": "OTHER", "verdict": "NOT_PRESENT", "absence_reason": "No other unclassified boundaries."}
            ]
        }

        self.scenario_matrix = {
            "schema_version": "1.0.0",
            "feature_slug": "order-fulfillment-pipeline",
            "actors": [
                {"actor_id": "customer", "description": "End customer placing orders"},
                {"actor_id": "system_worker", "description": "Async worker processing payments"},
                {"actor_id": "system_scheduler", "description": "Cron scheduler running purges"},
            ],
            "observers": [
                {"observer_id": "customer", "medium": "api_response"},
                {"observer_id": "operator", "medium": "rendered_ui"},
                {"observer_id": "operator", "medium": "process_exit"},
            ],
            "triggers": [
                {"trigger_id": "SUBMIT_ORDER_CHECKOUT", "actor_id": "customer", "is_actor_repeatable": False, "description": "Checkout order"},
                {"trigger_id": "ADD_ORDER_ITEM", "actor_id": "customer", "is_actor_repeatable": True, "description": "Add items"},
                {"trigger_id": "DISPATCH_PAYMENT_REQUEST", "actor_id": "system_worker", "is_actor_repeatable": False, "description": "Dispatch to gateway"},
                {"trigger_id": "PURGE_CANCELLED_ORDERS", "actor_id": "system_scheduler", "is_actor_repeatable": False, "description": "Purge orders"},
            ],
            "branch_coverage": {
                "group_a_resource_and_state": [],
                "group_b_trigger_and_observation": []
            },
            "states": [
                {"state_id": "ORDER_DRAFTED", "evidence_key": "state:ORDER_DRAFTED", "observable_projections": []},
                {"state_id": "ORDER_CONFIRMED", "evidence_key": "state:ORDER_CONFIRMED", "observable_projections": []},
                {"state_id": "ORDER_SUBMITTED", "evidence_key": "state:ORDER_SUBMITTED", "observable_projections": []},
                {"state_id": "ORDER_CANCELLED", "evidence_key": "state:ORDER_CANCELLED", "observable_projections": []},
                {"state_id": "VALIDATION_REJECTED", "evidence_key": "state:VALIDATION_REJECTED", "observable_projections": []},
                {"state_id": "PAYMENT_RETRYABLE_SUSPENDED", "evidence_key": "state:PAYMENT_RETRYABLE_SUSPENDED", "observable_projections": []},
                {"state_id": "TERMINATED_PURGED", "evidence_key": "state:TERMINATED_PURGED", "observable_projections": []},
            ],
            "transitions": []
        }

        self.fixtures = {
            "POST /v1/payment/authorizations": {
                "boundary_identifier": "POST /v1/payment/authorizations",
                "success_samples": [
                    {"sample_id": "sample_auth_200_success", "status_code": 200, "payload": {"auth_id": "auth_live_987654"}}
                ],
                "failure_envelope_census": [
                    {"sample_id": "sample_auth_504_gateway_timeout", "status_code": 504, "payload": {"error": "gateway_timeout"}}
                ]
            }
        }

    def test_reference_scenarios_valid(self):
        """All 4 reference scenarios validate clean against schema and upstream contracts."""
        reference_files = [
            "success-flow.json",
            "validation-failure.json",
            "external-boundary-failure.json",
            "lifecycle-delete-cleanup.json",
        ]
        for ref_name in reference_files:
            ref_path = EXAMPLES_DIR / ref_name
            self.assertTrue(ref_path.exists(), f"Reference scenario {ref_name} must exist")
            scenario_data = json.loads(ref_path.read_text(encoding="utf-8"))
            is_valid, errors = bdd_scenario.validate_bdd_scenario(
                scenario=scenario_data,
                lexicon=self.lexicon,
                manifest=self.manifest,
                scenario_matrix=self.scenario_matrix,
                fixtures_by_boundary=self.fixtures,
            )
            self.assertTrue(is_valid, f"Scenario {ref_name} failed validation: {errors}")
            self.assertEqual(len(errors), 0)

    def test_rejection_forbidden_internal_mock(self):
        """Validator rejects scenarios that mock internal domain entities or state machines."""
        scenario_data = json.loads((EXAMPLES_DIR / "success-flow.json").read_text(encoding="utf-8"))
        
        # Inject an internal domain mock
        scenario_data["mock_boundary_contract"]["mock_boundaries"].append({
            "boundary_identifier": "OrderAggregate",
            "boundary_class": "OTHER",
            "fixture_ref": {
                "fixture_path": "contract-fixtures/mock.json",
                "sample_id": "sample_1",
                "fixture_status": "PROBED_LIVE"
            },
            "is_external": True
        })

        is_valid, errors = bdd_scenario.validate_bdd_scenario(
            scenario=scenario_data,
            lexicon=self.lexicon,
            manifest=self.manifest,
            scenario_matrix=self.scenario_matrix,
            fixtures_by_boundary=self.fixtures,
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("FORBIDDEN_INTERNAL_MOCK" in e for e in errors))

    def test_rejection_unverified_mock_boundary(self):
        """Validator rejects scenarios mocking external boundaries not declared PRESENT in manifest."""
        scenario_data = json.loads((EXAMPLES_DIR / "success-flow.json").read_text(encoding="utf-8"))
        
        # Add an external boundary that is not in the manifest
        scenario_data["mock_boundary_contract"]["mock_boundaries"].append({
            "boundary_identifier": "POST /v1/unregistered/third-party",
            "boundary_class": "NETWORK_API",
            "fixture_ref": {
                "fixture_path": "contract-fixtures/unregistered/manifest.json",
                "sample_id": "sample_unreg",
                "fixture_status": "PROBED_LIVE"
            },
            "is_external": True
        })

        is_valid, errors = bdd_scenario.validate_bdd_scenario(
            scenario=scenario_data,
            lexicon=self.lexicon,
            manifest=self.manifest,
            scenario_matrix=self.scenario_matrix,
            fixtures_by_boundary=self.fixtures,
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("UNVERIFIED_MOCK_BOUNDARY" in e for e in errors))

    def test_rejection_unverified_fixture_replay(self):
        """Validator rejects scenarios referencing fixture sample_ids absent from recorded census."""
        scenario_data = json.loads((EXAMPLES_DIR / "success-flow.json").read_text(encoding="utf-8"))
        
        # Change fixture sample_id to an unrecorded sample
        scenario_data["mock_boundary_contract"]["mock_boundaries"][0]["fixture_ref"]["sample_id"] = "sample_unrecorded_adhoc_mock"

        is_valid, errors = bdd_scenario.validate_bdd_scenario(
            scenario=scenario_data,
            lexicon=self.lexicon,
            manifest=self.manifest,
            scenario_matrix=self.scenario_matrix,
            fixtures_by_boundary=self.fixtures,
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("UNVERIFIED_FIXTURE_REPLAY" in e for e in errors))

    def test_rejection_undefined_state_token(self):
        """Validator rejects undeclared state tokens with UNDEFINED_STATE_TOKEN."""
        scenario_data = json.loads((EXAMPLES_DIR / "success-flow.json").read_text(encoding="utf-8"))
        scenario_data["then"]["state_token"] = "UNDECLARED_MYSTERY_STATE"

        is_valid, errors = bdd_scenario.validate_bdd_scenario(
            scenario=scenario_data,
            lexicon=self.lexicon,
            manifest=self.manifest,
            scenario_matrix=self.scenario_matrix,
            fixtures_by_boundary=self.fixtures,
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("UNDEFINED_STATE_TOKEN" in e for e in errors))

    def test_rejection_undefined_domain_action(self):
        """Validator rejects undeclared action tokens with UNDEFINED_DOMAIN_ACTION."""
        scenario_data = json.loads((EXAMPLES_DIR / "success-flow.json").read_text(encoding="utf-8"))
        scenario_data["when"]["entrypoint"]["action_token"] = "UNDECLARED_RANDOM_ACTION"

        is_valid, errors = bdd_scenario.validate_bdd_scenario(
            scenario=scenario_data,
            lexicon=self.lexicon,
            manifest=self.manifest,
            scenario_matrix=self.scenario_matrix,
            fixtures_by_boundary=self.fixtures,
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("UNDEFINED_DOMAIN_ACTION" in e for e in errors))

    def test_rejection_unbound_projection_quantity(self):
        """Validator rejects observer projection quantities not bound in lexicon."""
        scenario_data = json.loads((EXAMPLES_DIR / "success-flow.json").read_text(encoding="utf-8"))
        scenario_data["then"]["observer_projections"][0]["projection_quantities"] = ["unbound_mystery_metric"]

        is_valid, errors = bdd_scenario.validate_bdd_scenario(
            scenario=scenario_data,
            lexicon=self.lexicon,
            manifest=self.manifest,
            scenario_matrix=self.scenario_matrix,
            fixtures_by_boundary=self.fixtures,
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("UNBOUND_PROJECTION_QUANTITY" in e for e in errors))

    def test_render_bdd_scenario_markdown(self):
        """Scenario renders to clean, Gherkin-structured markdown documentation."""
        scenario_data = json.loads((EXAMPLES_DIR / "success-flow.json").read_text(encoding="utf-8"))
        md = bdd_scenario.render_bdd_scenario_markdown(scenario_data)
        self.assertIn("# BDD Scenario: scenario_order_placement_success", md)
        self.assertIn("## Mock-Boundary Contract", md)
        self.assertIn("## Specification", md)
        self.assertIn("**Given** initial domain state is `ORDER_DRAFTED`", md)
        self.assertIn("**When** actor `customer` invokes production `HTTP_HANDLER`", md)
        self.assertIn("**Then** domain state transitions to `ORDER_CONFIRMED`", md)

    def test_upstream_cache_digest_cascade(self):
        """Modifying upstream files changes content-addressed cache digest."""
        lexicon_file = TESTS_DIR / "cases" / "scaffold" / "fixtures" / "advance-feature" / "domain-lexicon.json"
        digest_1 = bdd_scenario.compute_upstream_cache_digest(lexicon_path=lexicon_file)
        digest_2 = bdd_scenario.compute_upstream_cache_digest(lexicon_path=lexicon_file)
        self.assertEqual(digest_1, digest_2)
        digest_empty = bdd_scenario.compute_upstream_cache_digest()
        self.assertNotEqual(digest_1, digest_empty)

    def test_cli_validator_tool_execution(self):
        """Running bin/validate-bdd-contracts against examples returns 0."""
        cmd = [
            sys.executable,
            str(SKILL_DIR / "bin" / "validate-bdd-contracts"),
            str(EXAMPLES_DIR / "success-flow.json"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("BDD_SCENARIO_VALIDATED_CLEAN", res.stdout)


if __name__ == "__main__":
    unittest.main()
