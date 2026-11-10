#!/usr/bin/env python3
"""Unit tests for Stage 0 domain-lexicon validator."""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import tempfile
import unittest

# Import validate-lexicon executable via SourceFileLoader
TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN_DIR = SKILL_DIR / "bin"
VALIDATE_LEXICON_PATH = str(BIN_DIR / "validate-lexicon")

loader = SourceFileLoader("validate_lexicon", VALIDATE_LEXICON_PATH)
spec = importlib.util.spec_from_loader("validate_lexicon", loader)
validate_lexicon = importlib.util.module_from_spec(spec)
loader.exec_module(validate_lexicon)


class TestValidateLexicon(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = SKILL_DIR / "tests" / "cases" / "stage-0"

    def load_fixture(self, filename):
        path = self.fixtures_dir / filename
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_valid_lexicon_passes(self):
        data = self.load_fixture("valid.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "LEXICON_VALIDATED_CLEAN")

    def test_alias_declared_passes(self):
        data = self.load_fixture("alias-declared.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertTrue(is_valid)
        self.assertIsNone(code)
        self.assertEqual(msg, "LEXICON_VALIDATED_CLEAN")

    def test_token_collision_rejection(self):
        data = self.load_fixture("token-collision.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "LEXICON_TOKEN_COLLISION")

    def test_undefined_state_token_rejection(self):
        data = self.load_fixture("undefined-state-token.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "UNDEFINED_STATE_TOKEN")

    def test_missing_unit_precision_rejection(self):
        data = self.load_fixture("missing-unit-precision.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_UNIT_PRECISION")

    def test_empty_observer_register_rejection(self):
        data = self.load_fixture("empty-observer-register.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "EMPTY_OBSERVER_REGISTER")

    def test_unbound_projection_quantity_rejection(self):
        data = self.load_fixture("unbound-projection-quantity.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "UNBOUND_PROJECTION_QUANTITY")

    def test_missing_value_authority_rejection(self):
        data = self.load_fixture("missing-value-authority.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_VALUE_AUTHORITY")

    def test_missing_fact_precedence_rejection(self):
        data = self.load_fixture("missing-fact-precedence.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_FACT_PRECEDENCE")

    def test_undeclared_alias_rejection(self):
        data = self.load_fixture("undeclared-alias.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "UNDECLARED_ALIAS")

    def test_missing_unit_precision_generic_numeric_rejection(self):
        data = self.load_fixture("s0-missing-unit-precision-generic-numeric.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_UNIT_PRECISION")

    def test_dimensionless_numeric_attribute_passes(self):
        data = self.load_fixture("valid.lexicon.json")
        # Add an explicitly dimensionless attribute
        data["entities"][0]["attributes"].append({
            "name": "retry_attempt",
            "type": "integer",
            "dimensionless": True,
            "description": "Explicitly dimensionless retry count."
        })
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertTrue(is_valid)
        self.assertIsNone(code)

    def test_unbound_projection_quantity_untagged_rejection(self):
        data = self.load_fixture("s0-unbound-projection-quantity-untagged.lexicon.json")
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "UNBOUND_PROJECTION_QUANTITY")

    def test_observer_with_single_projection_passes(self):
        data = self.load_fixture("valid.lexicon.json")
        # Single observer perceiving a single projection
        data["actors_and_observers"]["observers"] = [
            {
                "id": "SingleObserver",
                "kind": "ACTIVE",
                "channels": ["rendered_ui"],
                "provenance": "FEATURE_INTRODUCED",
                "repo_reference": None,
                "glossary_visibility": "INCLUDE",
                "plain_language_summary": "Perceives a single state word.",
                "description": "Perceives a single state word."
            }
        ]
        data["projection_bindings"] = [
            {
                "projection_id": "single_state_projection",
                "quantity_or_label": "Subscription Status Display",
                "observer_id": "SingleObserver",
                "source_type": "CONTRACT_FIELD",
                "source_reference": "Subscription.status",
                "derivation_expression": "Subscription.status"
            }
        ]
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertTrue(is_valid)
        self.assertIsNone(code)

    def test_missing_plain_language_summary_on_included_value_object(self):
        data = self.load_fixture("valid.lexicon.json")
        data["value_objects"].append({
            "name": "CustomAmount",
            "provenance": "FEATURE_INTRODUCED",
            "repo_reference": None,
            "glossary_visibility": "INCLUDE",
            "description": "A monetary amount value object"
        })
        is_valid, code, msg = validate_lexicon.validate_lexicon_dict(data)
        self.assertFalse(is_valid)
        self.assertEqual(code, "MISSING_PLAIN_LANGUAGE_SUMMARY")


if __name__ == "__main__":
    unittest.main()
