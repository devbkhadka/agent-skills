#!/usr/bin/env python3
"""Unit tests for Project Domain Baseline engine and CLI.

Milestone: M1 — Project baseline engine
Covers:
- lib/baseline.py:
  - element_digest (canonical JSON sha256)
  - element_index (taxonomy of 15 domain element kinds)
  - diff_lexicons (added, changed, removed element IDs)
  - merge (pure, additive, BASELINE_CONFLICT detection, --amend resolution, alias normalization)
  - storage and layout helpers (init_baseline_layout, render_baseline_docs, check_rendered_docs_fresh, merge_contract_fixtures)
- bin/baseline:
  - init, show [--json], diff [--json], promote [--amend], approve [--all | --element], check [--json, --strict-approvals]
"""

import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN_DIR = SKILL_DIR / "bin"
LIB_DIR = SKILL_DIR / "lib"
CASES_DIR = TESTS_DIR / "cases" / "baseline"

# Load bin/baseline executable via SourceFileLoader
BASELINE_BIN_PATH = str(BIN_DIR / "baseline")
loader = SourceFileLoader("baseline_bin", BASELINE_BIN_PATH)
spec = importlib.util.spec_from_loader("baseline_bin", loader)
baseline_bin = importlib.util.module_from_spec(spec)
loader.exec_module(baseline_bin)

# Load lib/baseline module directly
from lib.baseline import (
    DEFAULT_BASELINE_DIR,
    SCHEMA_VERSION,
    BaselineConflictError,
    BaselineError,
    check_rendered_docs_fresh,
    diff_lexicons,
    element_digest,
    element_index,
    init_baseline_layout,
    load_baseline_index,
    load_baseline_lexicon,
    merge,
    merge_contract_fixtures,
    normalize_element_id,
    render_baseline_docs,
    save_baseline_index,
    save_baseline_lexicon,
    validate_baseline_lexicon,
)


class TestBaselineLibrary(unittest.TestCase):
    """Unit tests for lib/baseline.py pure functions and helpers."""

    def setUp(self):
        self.seed_lex_path = CASES_DIR / "seed-feature" / "domain-lexicon.json"
        self.seed_lexicon = json.loads(self.seed_lex_path.read_text(encoding="utf-8"))

    def test_element_digest_deterministic_canonical_json(self):
        payload1 = {"name": "TestEntity", "attributes": [{"type": "string", "name": "id"}], "active": True}
        payload2 = {"active": True, "attributes": [{"name": "id", "type": "string"}], "name": "TestEntity"}
        digest1 = element_digest(payload1)
        digest2 = element_digest(payload2)

        self.assertEqual(len(digest1), 64)
        self.assertEqual(digest1, digest2)

        # Different payload produces different digest
        payload3 = {"name": "TestEntity", "attributes": [{"name": "id", "type": "int"}], "active": True}
        self.assertNotEqual(digest1, element_digest(payload3))

    def test_element_index_extracts_all_kinds(self):
        index = element_index(self.seed_lexicon)
        self.assertIsInstance(index, dict)
        self.assertGreaterEqual(len(index), 20)

        expected_prefixes = {
            "entity:",
            "value_object:",
            "action:",
            "state_enum:",
            "monotonic_clock:",
            "unit_precision:",
            "actor:",
            "observer:",
            "alias_pair:",
            "projection_binding:",
            "value_authority:",
            "fact_precedence:",
            "boundary:",
            "entity_relationship:",
            "simulated_dependency:",
        }
        found_prefixes = {k.split(":", 1)[0] + ":" for k in index.keys()}
        for prefix in expected_prefixes:
            self.assertIn(prefix, found_prefixes, f"Missing element kind prefix: {prefix}")

        # Check specific known elements from valid seed
        self.assertIn("entity:Subscription", index)
        self.assertIn("entity:Organization", index)
        self.assertIn("value_object:ProrationInvoice", index)
        self.assertIn("action:DowngradeSubscription", index)
        self.assertIn("state_enum:SubscriptionStatus", index)
        self.assertIn("monotonic_clock:StripeEventClock", index)
        self.assertIn("unit_precision:integer_cents", index)
        self.assertIn("actor:StripeWebhookDispatcher", index)
        self.assertIn("observer:OrgAdmin", index)
        self.assertIn("alias_pair:CustomerIdentifier", index)
        self.assertIn("projection_binding:quota_warning_banner", index)
        self.assertIn("value_authority:proration_amount", index)
        self.assertIn("fact_precedence:subscription_cancellation_status", index)
        self.assertIn("boundary:billing_and_entitlements", index)
        self.assertIn("entity_relationship:Subscription->Organization", index)
        self.assertIn("simulated_dependency:NETWORK_API", index)

    def test_element_index_empty_or_none(self):
        self.assertEqual(element_index({}), {})
        self.assertEqual(element_index(None), {})
        self.assertEqual(element_index("not_a_dict"), {})

    def test_diff_lexicons(self):
        idx_seed = element_index(self.seed_lexicon)

        # 1. Empty vs Full -> all added
        diff_from_empty = diff_lexicons({}, self.seed_lexicon)
        self.assertEqual(diff_from_empty["added"], set(idx_seed.keys()))
        self.assertEqual(diff_from_empty["changed"], set())
        self.assertEqual(diff_from_empty["removed"], set())

        # 2. Full vs Empty -> all removed
        diff_to_empty = diff_lexicons(self.seed_lexicon, {})
        self.assertEqual(diff_to_empty["added"], set())
        self.assertEqual(diff_to_empty["changed"], set())
        self.assertEqual(diff_to_empty["removed"], set(idx_seed.keys()))

        # 3. Identical -> nothing added, changed, or removed
        diff_identical = diff_lexicons(self.seed_lexicon, self.seed_lexicon)
        self.assertEqual(diff_identical["added"], set())
        self.assertEqual(diff_identical["changed"], set())
        self.assertEqual(diff_identical["removed"], set())

        # 4. Modified lexicon: entity modified, action added
        modified_lex = json.loads(json.dumps(self.seed_lexicon))
        modified_lex["entities"][0]["plain_language_summary"] = "A modified summary"
        modified_lex["actions"].append({
            "name": "NewAction",
            "description": "Newly added action.",
            "provenance": "FEATURE_INTRODUCED",
            "plain_language_summary": "Newly added action.",
            "glossary_visibility": "INCLUDE",
            "repo_reference": None,
        })
        diff_mod = diff_lexicons(self.seed_lexicon, modified_lex)
        self.assertEqual(diff_mod["added"], {"action:NewAction"})
        self.assertEqual(diff_mod["changed"], {f"entity:{modified_lex['entities'][0]['name']}"})
        self.assertEqual(diff_mod["removed"], set())

        # 5. Accepts pre-computed element indices directly
        idx_mod = element_index(modified_lex)
        diff_indices = diff_lexicons(idx_seed, idx_mod)
        self.assertEqual(diff_indices["added"], {"action:NewAction"})
        self.assertEqual(diff_indices["changed"], {f"entity:{modified_lex['entities'][0]['name']}"})

    def test_merge_pure_and_additive(self):
        ext_lex_path = CASES_DIR / "extension-feature" / "domain-lexicon.json"
        ext_lexicon = json.loads(ext_lex_path.read_text(encoding="utf-8"))

        copy_seed = json.loads(json.dumps(self.seed_lexicon))
        copy_ext = json.loads(json.dumps(ext_lexicon))

        merged = merge(copy_seed, copy_ext)

        # Pure: inputs not modified
        self.assertEqual(copy_seed, self.seed_lexicon)
        self.assertEqual(copy_ext, ext_lexicon)

        # Additive: contains all base elements plus extension elements
        idx_merged = element_index(merged)
        self.assertIn("action:PauseSubscription", idx_merged)
        self.assertIn("observer:SupportAgent", idx_merged)
        self.assertIn("projection_binding:support_agent_status_view", idx_merged)
        self.assertIn("entity:Subscription", idx_merged)
        self.assertIn("entity:Organization", idx_merged)

    def test_merge_conflict_unauthorized_raises(self):
        conf_lex_path = CASES_DIR / "conflicting-feature" / "domain-lexicon.json"
        conf_lexicon = json.loads(conf_lex_path.read_text(encoding="utf-8"))

        with self.assertRaises(BaselineConflictError) as ctx:
            merge(self.seed_lexicon, conf_lexicon)

        self.assertEqual(ctx.exception.code, "BASELINE_CONFLICT")
        self.assertIn("entity:Subscription", ctx.exception.conflicts)

    def test_merge_conflict_authorized_in_amend(self):
        conf_lex_path = CASES_DIR / "conflicting-feature" / "domain-lexicon.json"
        conf_lexicon = json.loads(conf_lex_path.read_text(encoding="utf-8"))

        merged = merge(self.seed_lexicon, conf_lexicon, amend=["entity:Subscription"])
        idx_merged = element_index(merged)
        self.assertEqual(
            idx_merged["entity:Subscription"]["plain_language_summary"],
            "Modified plain language summary for Subscription entity.",
        )

    def test_merge_alias_normalization(self):
        # Test normalize_element_id
        self.assertEqual(normalize_element_id("entity_boundary:billing"), "boundary:billing")
        self.assertEqual(normalize_element_id("boundary:billing"), "boundary:billing")
        self.assertEqual(normalize_element_id("entity:Sub"), "entity:Sub")

        # Modifying a boundary
        mod_boundary_lex = json.loads(json.dumps(self.seed_lexicon))
        mod_boundary_lex["entity_boundaries"][0]["name"] = "Updated Boundary Name"

        # Authorized using alias "entity_boundary:billing_and_entitlements"
        merged = merge(
            self.seed_lexicon,
            mod_boundary_lex,
            amend=["entity_boundary:billing_and_entitlements"],
        )
        idx_merged = element_index(merged)
        self.assertEqual(
            idx_merged["boundary:billing_and_entitlements"]["name"],
            "Updated Boundary Name",
        )

    def test_init_baseline_layout(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td) / "docs" / "domain"
            init_baseline_layout(base_dir)

            self.assertTrue((base_dir / "baseline.json").is_file())
            self.assertTrue((base_dir / "domain-lexicon.json").is_file())
            self.assertTrue((base_dir / "contract-fixtures" / "manifest.json").is_file())
            self.assertTrue((base_dir / "docs" / "ubiquitous-language-glossary.md").is_file())
            self.assertTrue((base_dir / "docs" / "domain-entity-map.md").is_file())

            # Baseline index validation
            index_data = load_baseline_index(base_dir)
            self.assertEqual(index_data["schema_version"], SCHEMA_VERSION)
            self.assertEqual(index_data["sources"], [])
            self.assertEqual(index_data["element_digests"], {})
            self.assertEqual(index_data["approvals"], {})

            # Empty lexicon validation
            lex_data = load_baseline_lexicon(base_dir)
            is_valid, err_code, _ = validate_baseline_lexicon(lex_data)
            self.assertTrue(is_valid)
            self.assertIsNone(err_code)

            # Check rendered docs fresh
            fresh, reason = check_rendered_docs_fresh(base_dir)
            self.assertTrue(fresh, f"Rendered docs not fresh: {reason}")

    def test_check_rendered_docs_fresh_detects_drift(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td) / "docs" / "domain"
            init_baseline_layout(base_dir)

            glossary_path = base_dir / "docs" / "ubiquitous-language-glossary.md"
            glossary_path.chmod(0o644)
            glossary_path.write_text("Hand edited content\n", encoding="utf-8")

            fresh, reason = check_rendered_docs_fresh(base_dir)
            self.assertFalse(fresh)
            self.assertIn("ubiquitous-language-glossary.md is stale", reason)

    def test_merge_contract_fixtures(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td) / "docs" / "domain"
            init_baseline_layout(base_dir)

            feature_fixtures = CASES_DIR / "seed-feature" / "contract-fixtures"
            merge_contract_fixtures(base_dir, feature_fixtures)

            manifest_path = base_dir / "contract-fixtures" / "manifest.json"
            self.assertTrue(manifest_path.is_file())
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))

            classes = {row["class_name"]: row for row in mdata["boundary_classes"]}
            self.assertEqual(classes["NETWORK_API"]["verdict"], "PRESENT")
            self.assertEqual(classes["SUBPROCESS_CLI"]["verdict"], "NOT_PRESENT")

            # Check that fixture file was copied
            copied_fixture = base_dir / "contract-fixtures" / "network_api-search_endpoint.json"
            self.assertTrue(copied_fixture.is_file())


class TestBaselineCLI(unittest.TestCase):
    """Unit tests for bin/baseline CLI subcommands."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmpdir.name) / "docs" / "domain"
        self.seed_dir = CASES_DIR / "seed-feature"
        self.ext_dir = CASES_DIR / "extension-feature"
        self.conf_dir = CASES_DIR / "conflicting-feature"
        self.inv_dir = CASES_DIR / "invalid-feature"

    def tearDown(self):
        self.tmpdir.cleanup()

    def run_cli(self, args):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            ret = baseline_bin.main(args)
            return ret, mock_stdout.getvalue(), mock_stderr.getvalue()

    def test_cli_init_and_show(self):
        # 1. Init
        ret, stdout, stderr = self.run_cli(["init", "--baseline-dir", str(self.base_dir)])
        self.assertEqual(ret, 0)
        self.assertIn("BASELINE_INITIALIZED", stdout)

        # 2. Show (Text)
        ret, stdout, stderr = self.run_cli(["show", "--baseline-dir", str(self.base_dir)])
        self.assertEqual(ret, 0)
        self.assertIn("Elements: 0 total", stdout)

        # 3. Show (JSON)
        ret, stdout, stderr = self.run_cli(["show", "--baseline-dir", str(self.base_dir), "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(stdout)
        self.assertEqual(data["total_elements"], 0)
        self.assertEqual(data["approved_count"], 0)
        self.assertEqual(data["unapproved_count"], 0)

    def test_cli_diff(self):
        self.run_cli(["init", "--baseline-dir", str(self.base_dir)])

        lex_path = self.seed_dir / "domain-lexicon.json"
        # 1. Diff (Text)
        ret, stdout, stderr = self.run_cli([
            "diff",
            "--baseline-dir", str(self.base_dir),
            "--lexicon", str(lex_path),
        ])
        self.assertEqual(ret, 0)
        self.assertIn("Added (", stdout)
        self.assertIn("+ entity:Subscription", stdout)

        # 2. Diff (JSON)
        ret, stdout, stderr = self.run_cli([
            "diff",
            "--baseline-dir", str(self.base_dir),
            "--lexicon", str(lex_path),
            "--json",
        ])
        self.assertEqual(ret, 0)
        diff_data = json.loads(stdout)
        self.assertIn("entity:Subscription", diff_data["added"])
        self.assertEqual(diff_data["changed"], [])
        self.assertEqual(diff_data["removed"], [])

    def test_cli_promote_fresh_feature(self):
        self.run_cli(["init", "--baseline-dir", str(self.base_dir)])

        # Promote seed-feature
        ret, stdout, stderr = self.run_cli([
            "promote",
            "--baseline-dir", str(self.base_dir),
            "--feature-dir", str(self.seed_dir),
        ])
        self.assertEqual(ret, 0)
        self.assertIn("BASELINE_PROMOTED: stripe-subscription-downgrade", stdout)

        # Verify index was updated
        index_data = load_baseline_index(self.base_dir)
        self.assertIn("stripe-subscription-downgrade", index_data["sources"])
        self.assertIn("entity:Subscription", index_data["element_digests"])
        self.assertGreaterEqual(len(index_data["element_digests"]), 20)

        # Verify rendered docs exist in baseline
        self.assertTrue((self.base_dir / "docs" / "ubiquitous-language-glossary.md").is_file())
        self.assertTrue((self.base_dir / "docs" / "domain-entity-map.md").is_file())

        # Verify check reports unapproved elements before approval
        ret, stdout, stderr = self.run_cli(["check", "--baseline-dir", str(self.base_dir)])
        self.assertEqual(ret, 0)
        self.assertIn("UNAPPROVED_ELEMENT: entity:Subscription", stdout)
        self.assertIn("BASELINE_CHECK_PASSED", stdout)

        # Strict check should fail on unapproved elements
        ret, stdout, stderr = self.run_cli([
            "check",
            "--baseline-dir", str(self.base_dir),
            "--strict-approvals",
        ])
        self.assertEqual(ret, 1)
        self.assertIn("UNAPPROVED_ELEMENT", stdout)

    def test_cli_approve_all_and_check_clean(self):
        self.run_cli(["init", "--baseline-dir", str(self.base_dir)])
        self.run_cli(["promote", "--baseline-dir", str(self.base_dir), "--feature-dir", str(self.seed_dir)])

        # Approve all
        ret, stdout, stderr = self.run_cli(["approve", "--baseline-dir", str(self.base_dir), "--all"])
        self.assertEqual(ret, 0)
        self.assertIn("BASELINE_APPROVED:", stdout)

        # Check clean
        ret, stdout, stderr = self.run_cli(["check", "--baseline-dir", str(self.base_dir)])
        self.assertEqual(ret, 0)
        self.assertIn("BASELINE_CHECK_CLEAN", stdout)

        # Check clean under strict approvals
        ret, stdout, stderr = self.run_cli([
            "check",
            "--baseline-dir", str(self.base_dir),
            "--strict-approvals",
        ])
        self.assertEqual(ret, 0)
        self.assertIn("BASELINE_CHECK_CLEAN", stdout)

        # JSON mode check
        ret, stdout, stderr = self.run_cli(["check", "--baseline-dir", str(self.base_dir), "--json"])
        self.assertEqual(ret, 0)
        chk = json.loads(stdout)
        self.assertEqual(chk["status"], "PASS")
        self.assertEqual(chk["unapproved"], [])
        self.assertEqual(chk["stale_approvals"], [])

    def test_cli_promote_conflict_and_amend(self):
        # Setup approved baseline
        self.run_cli(["init", "--baseline-dir", str(self.base_dir)])
        self.run_cli(["promote", "--baseline-dir", str(self.base_dir), "--feature-dir", str(self.seed_dir)])
        self.run_cli(["approve", "--baseline-dir", str(self.base_dir), "--all"])

        # 1. Promote conflicting-feature without --amend -> refuses BASELINE_CONFLICT
        ret, stdout, stderr = self.run_cli([
            "promote",
            "--baseline-dir", str(self.base_dir),
            "--feature-dir", str(self.conf_dir),
        ])
        self.assertEqual(ret, 1)
        self.assertIn("BASELINE_CONFLICT", stdout)

        # 2. Promote with --amend entity:Subscription -> succeeds
        ret, stdout, stderr = self.run_cli([
            "promote",
            "--baseline-dir", str(self.base_dir),
            "--feature-dir", str(self.conf_dir),
            "--amend", "entity:Subscription",
        ])
        self.assertEqual(ret, 0)
        self.assertIn("BASELINE_PROMOTED: subscription-schema-change", stdout)

        # 3. Check now reports STALE_APPROVAL for entity:Subscription
        ret, stdout, stderr = self.run_cli(["check", "--baseline-dir", str(self.base_dir)])
        self.assertEqual(ret, 0)
        self.assertIn("STALE_APPROVAL: entity:Subscription", stdout)

        # Strict check fails on stale approval
        ret, stdout, stderr = self.run_cli([
            "check",
            "--baseline-dir", str(self.base_dir),
            "--strict-approvals",
        ])
        self.assertEqual(ret, 1)
        self.assertIn("STALE_APPROVAL: entity:Subscription", stdout)

        # 4. Approve individual element
        ret, stdout, stderr = self.run_cli([
            "approve",
            "--baseline-dir", str(self.base_dir),
            "--element", "entity:Subscription",
        ])
        self.assertEqual(ret, 0)
        self.assertIn("BASELINE_APPROVED: 1 elements", stdout)

        # Strict check passes now
        ret, stdout, stderr = self.run_cli([
            "check",
            "--baseline-dir", str(self.base_dir),
            "--strict-approvals",
        ])
        self.assertEqual(ret, 0)
        self.assertIn("BASELINE_CHECK_CLEAN", stdout)

    def test_cli_promote_invalid_lexicon_refuses(self):
        self.run_cli(["init", "--baseline-dir", str(self.base_dir)])
        ret, stdout, stderr = self.run_cli([
            "promote",
            "--baseline-dir", str(self.base_dir),
            "--feature-dir", str(self.inv_dir),
        ])
        self.assertEqual(ret, 1)
        self.assertIn("LEXICON_TOKEN_COLLISION", stdout)

    def test_cli_check_stale_docs_refuses(self):
        self.run_cli(["init", "--baseline-dir", str(self.base_dir)])
        self.run_cli(["promote", "--baseline-dir", str(self.base_dir), "--feature-dir", str(self.seed_dir)])
        self.run_cli(["approve", "--baseline-dir", str(self.base_dir), "--all"])

        # Tamper with rendered glossary
        glossary_path = self.base_dir / "docs" / "ubiquitous-language-glossary.md"
        glossary_path.chmod(0o644)
        glossary_path.write_text("Modified text outside pipeline\n", encoding="utf-8")

        ret, stdout, stderr = self.run_cli(["check", "--baseline-dir", str(self.base_dir)])
        self.assertEqual(ret, 1)
        self.assertIn("STALE_RENDERED_DOCS", stdout)


if __name__ == "__main__":
    unittest.main()
