#!/usr/bin/env python3
"""Tests for ground-truth reconciliation of word-valued (enum) authorities."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from lib.ground_truth import verify_ground_truth_reconciliation


class MockEvidence:
    def __init__(self, envelope):
        self.envelope = envelope


class TestGroundTruthWordAuthority(unittest.TestCase):
    def setUp(self):
        self.lexicon = {
            "schema_version": "1.0.0",
            "feature_slug": "test-word-feature",
            "bounded_context": "test",
            "entities": [
                {
                    "name": "Session",
                    "attributes": [
                        {
                            "name": "state",
                            "type": "enum",
                            "enum_ref": "SessionState",
                            "projection_id": "session_state",
                        }
                    ],
                }
            ],
            "state_enums": [
                {
                    "name": "SessionState",
                    "tokens": ["working", "waiting", "idle", "ended"],
                    "terminal_tokens": ["ended"],
                }
            ],
            "projection_bindings": [
                {
                    "projection_id": "session_state",
                    "quantity_or_label": "Session state",
                    "observer_id": "user",
                    "source_type": "PURE_EXPRESSION",
                    "source_reference": "Session.state",
                    "derivation_expression": "state",
                }
            ],
            "value_authorities": [
                {
                    "value_name": "session_state",
                    "authority_model": "LOCAL_AUTHORITATIVE",
                    "upstream_boundary": None,
                    "local_computation": "domain deriveSessionState()",
                    "tolerance_epsilon": None,
                }
            ],
            "value_objects": [],
            "unit_precisions": [],
            "actors_and_observers": {"actors": [], "observers": []},
            "alias_pairs": [],
            "fact_precedences": [],
        }

    def _verify(self, html_markup, envelope_extra=None):
        envelope = {
            "captured_artifact": html_markup,
            "recorded_fixture_values": [],
            "value_bindings": None,
        }
        if envelope_extra:
            envelope.update(envelope_extra)
        report = {"feature_slug": "test-word-feature"}
        with patch("lib.ground_truth.load_domain_lexicon", return_value=(self.lexicon, Path("/fake"), None)):
            return verify_ground_truth_reconciliation(report, {}, [MockEvidence(envelope)])

    def test_word_value_matches_reconciled(self):
        """Word value matches declared token: reconciled cleanly."""
        problems = self._verify('<span class="badge state-working" data-testid="session-state">working</span>')
        self.assertEqual(problems, [])

        # Also when recorded authoritative value is present and matches
        envelope_extra = {
            "value_bindings": [
                {"projection_binding": "session_state", "asserted_value": "working"}
            ]
        }
        problems = self._verify(
            '<span class="badge state-working" data-testid="session-state">working</span>',
            envelope_extra,
        )
        self.assertEqual(problems, [])

    def test_word_value_differs_fails(self):
        """Word value differs from recorded authoritative value: fails reconciliation."""
        envelope_extra = {
            "value_bindings": [
                {"projection_binding": "session_state", "asserted_value": "waiting"}
            ]
        }
        problems = self._verify(
            '<span class="badge state-working" data-testid="session-state">working</span>',
            envelope_extra,
        )
        self.assertTrue(any("GROUND_TRUTH_CONTRADICTED" in p for p in problems))
        self.assertTrue(any("authoritative value is 'waiting'" in p for p in problems))

    def test_empty_text_fails(self):
        """Element with empty or whitespace-only text fails."""
        problems = self._verify('<span data-testid="session-state"></span>')
        self.assertTrue(any("GROUND_TRUTH_UNTRACEABLE" in p for p in problems))
        self.assertTrue(any("carries no parseable word value" in p for p in problems))

        problems_ws = self._verify('<span data-testid="session-state">   </span>')
        self.assertTrue(any("GROUND_TRUTH_UNTRACEABLE" in p for p in problems_ws))

    def test_value_outside_declared_set_fails(self):
        """Element text outside declared state enum tokens fails."""
        problems = self._verify('<span data-testid="session-state">banana</span>')
        self.assertTrue(any("GROUND_TRUTH_CONTRADICTED" in p for p in problems))
        self.assertTrue(any("outside the declared allowed values" in p for p in problems))

    def test_numeric_attribute_with_non_numeric_text_fails_with_numeric_message(self):
        """A numeric attribute rendering non-numeric text like 'N/A' must report 'carries no parseable numeric value'."""
        self.lexicon["entities"][0]["attributes"].append({
            "name": "count",
            "type": "integer",
            "projection_id": "session_count",
        })
        self.lexicon["projection_bindings"].append({
            "projection_id": "session_count",
            "quantity_or_label": "Session count",
            "observer_id": "user",
            "source_type": "PURE_EXPRESSION",
            "source_reference": "Session.count",
            "derivation_expression": "count",
        })
        self.lexicon["value_authorities"].append({
            "value_name": "session_count",
            "authority_model": "LOCAL_AUTHORITATIVE",
            "upstream_boundary": None,
            "local_computation": "sum(item_count)",
            "tolerance_epsilon": 0.001,
        })
        problems = self._verify('<span data-testid="session_count">N/A</span>')
        self.assertTrue(any("carries no parseable numeric value" in p for p in problems))

    def test_an_attribute_typed_by_its_state_enum_reconciles_as_a_word(self):
        """`"type": "SessionState"` names the enum itself; the word is matched exactly, not parsed as a number."""
        attribute = self.lexicon["entities"][0]["attributes"][0]
        attribute["type"] = "SessionState"
        del attribute["enum_ref"]
        problems = self._verify('<span class="badge state-working" data-testid="session-state">working</span>')
        self.assertEqual(problems, [])

        problems = self._verify('<span data-testid="session-state">Working</span>')
        self.assertTrue(any("GROUND_TRUTH_CONTRADICTED" in p for p in problems))
        self.assertFalse(any("numeric" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
