#!/usr/bin/env python3
"""Behavioural tests for change-lane triage (lib/triage.py + bin/triage-change).

Each fixture diff under tests/cases/change-lane/ carries one signal and is triaged against the
fixture baseline in tests/cases/change-lane/repo/docs/domain. Lexicon changes are exercised in a
real temporary git repository, which also covers the default `git diff` input path.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
BIN = SKILL_DIR / "bin" / "triage-change"
CASES = TESTS_DIR / "cases" / "change-lane"
FIXTURE_REPO = CASES / "repo"

sys.path.insert(0, str(SKILL_DIR))
from lib.triage import parse_unified_diff, reverse_apply  # noqa: E402


def triage_cli(*args, repo=FIXTURE_REPO):
    result = subprocess.run([sys.executable, str(BIN), "--repo", str(repo), "--json"] + list(args),
                            capture_output=True, text=True, timeout=60)
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else None
    return result.returncode, payload, result.stdout + result.stderr


def triage_diff(name, *args):
    return triage_cli("--diff-file", str(CASES / name), *args)


def codes(result):
    return [r["code"] for r in result["reasons"]]


class LaneSignalTest(unittest.TestCase):
    def test_pure_css_change_is_a_tweak(self):
        code, result, out = triage_diff("css-tweak.diff")
        self.assertEqual(code, 0, out)
        self.assertEqual((result["lane"], result["verification_tier"]), ("TWEAK", "TIER_1"))
        self.assertEqual(result["reasons"], [])

    def test_logic_using_only_known_tokens_is_a_tweak(self):
        code, result, out = triage_diff("known-token-logic.diff")
        self.assertEqual(result["lane"], "TWEAK", out)

    def test_new_domain_token_is_an_extension_naming_the_tokens(self):
        code, result, out = triage_diff("new-domain-token.diff")
        self.assertEqual(code, 0, out)
        self.assertEqual((result["lane"], result["verification_tier"]), ("EXTENSION", "TIER_2"))
        self.assertEqual(codes(result), ["NEW_DOMAIN_TOKEN"])
        self.assertIn("PAUSED", result["reasons"][0]["detail"])
        self.assertIn("TEAM", result["reasons"][0]["detail"])

    def test_boundary_import_outside_adapters_is_a_leak(self):
        _, result, out = triage_diff("boundary-leak.diff")
        self.assertEqual(result["lane"], "NEW_CAPABILITY", out)
        self.assertEqual(codes(result), ["BOUNDARY_LEAK"])
        self.assertEqual(result["reasons"][0]["paths"], ["src/billing/downgrade.py"])

    def test_adapter_opening_an_unrecorded_boundary_class_is_a_new_boundary(self):
        _, result, out = triage_diff("new-boundary-in-adapter.diff")
        self.assertEqual(result["lane"], "NEW_CAPABILITY", out)
        self.assertEqual(codes(result), ["NEW_BOUNDARY"])
        self.assertIn("DATASTORE", result["reasons"][0]["detail"])

    def test_adapter_using_a_recorded_boundary_class_is_a_tweak(self):
        _, result, out = triage_diff("known-boundary-in-adapter.diff")
        self.assertEqual(result["lane"], "TWEAK", out)

    def test_contract_fixture_change_is_a_new_capability(self):
        _, result, out = triage_diff("contract-change.diff")
        self.assertEqual(result["lane"], "NEW_CAPABILITY", out)
        self.assertEqual(codes(result), ["CONTRACT_CHANGE"])

    def test_unmapped_path_escalates_conservatively(self):
        _, result, out = triage_diff("unmapped-path.diff")
        self.assertEqual(result["lane"], "EXTENSION", out)
        self.assertEqual(codes(result), ["UNMAPPED_PATH"])

    def test_no_baseline_is_a_new_capability(self):
        code, result, out = triage_cli("--diff-file", str(CASES / "css-tweak.diff"), repo=CASES)
        self.assertEqual(code, 0, out)
        self.assertEqual(result["lane"], "NEW_CAPABILITY")
        self.assertEqual(codes(result), ["NO_BASELINE"])
        self.assertTrue(any("domain-baseline" in s for s in result["next_steps"]))


class DeclarationTest(unittest.TestCase):
    def test_underdeclared_lane_is_refused(self):
        code, result, out = triage_diff("new-domain-token.diff", "--declare",
                                        str(CASES / "declare-tweak.json"))
        self.assertEqual(code, 1, out)
        self.assertTrue(result["underdeclared"])
        self.assertEqual(result["lane"], "EXTENSION")
        text = subprocess.run([sys.executable, str(BIN), "--repo", str(FIXTURE_REPO),
                               "--diff-file", str(CASES / "new-domain-token.diff"),
                               "--declare", str(CASES / "declare-tweak.json")],
                              capture_output=True, text=True, timeout=60).stdout
        self.assertIn("LANE_UNDERDECLARED", text)

    def test_declaring_higher_escalates_never_de_escalates(self):
        code, result, out = triage_diff("css-tweak.diff", "--declare",
                                        str(CASES / "declare-new-capability.json"))
        self.assertEqual(code, 0, out)
        self.assertEqual(result["computed_lane"], "TWEAK")
        self.assertEqual(result["lane"], "NEW_CAPABILITY")
        self.assertEqual(result["verification_tier"], "TIER_3")


class GitRepoLexiconTest(unittest.TestCase):
    """Default input: `git diff HEAD` plus untracked files, in a real repository."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        shutil.copytree(FIXTURE_REPO / "docs", self.repo / "docs")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("-c", "user.email=t@example.com", "-c", "user.name=t", "-c",
                 "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
        self.lexicon_path = self.repo / "docs" / "domain" / "domain-lexicon.json"

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo)] + list(args), check=True,
                       capture_output=True, text=True, timeout=60)

    def edit_lexicon(self, mutate):
        lexicon = json.loads(self.lexicon_path.read_text(encoding="utf-8"))
        mutate(lexicon)
        self.lexicon_path.write_text(json.dumps(lexicon, indent=2) + "\n", encoding="utf-8")

    def test_clean_tree_is_a_tweak(self):
        code, result, out = triage_cli(repo=self.repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(result["lane"], "TWEAK")

    def test_new_state_token_in_the_lexicon_is_an_extension(self):
        def add_state(lexicon):
            for enum in lexicon["state_enums"]:
                if enum["name"] == "SubscriptionStatus":
                    enum["tokens"].append("PAUSED")
        self.edit_lexicon(add_state)
        _, result, out = triage_cli(repo=self.repo)
        self.assertEqual(result["lane"], "EXTENSION", out)
        self.assertEqual(codes(result), ["LEXICON_EXTENSION"])
        self.assertIn("changed state_enum:SubscriptionStatus", result["reasons"][0]["detail"])

    def test_new_entity_in_the_lexicon_is_a_new_capability(self):
        def add_entity(lexicon):
            lexicon["entities"].append({"name": "Invoice", "description": "Billing document."})
        self.edit_lexicon(add_entity)
        _, result, out = triage_cli(repo=self.repo)
        self.assertEqual(result["lane"], "NEW_CAPABILITY", out)
        self.assertEqual(codes(result), ["LEXICON_CAPABILITY_CHANGE"])
        self.assertIn("added entity:Invoice", result["reasons"][0]["detail"])

    def test_untracked_file_is_triaged(self):
        (self.repo / "src" / "billing").mkdir(parents=True)
        (self.repo / "src" / "billing" / "pause.ts").write_text(
            'export const next = (s) => s.status === "PAUSED";\n', encoding="utf-8")
        _, result, out = triage_cli(repo=self.repo)
        self.assertEqual(result["lane"], "EXTENSION", out)
        self.assertEqual(codes(result), ["NEW_DOMAIN_TOKEN"])

    def test_invalid_triage_config_is_refused_not_ignored(self):
        (self.repo / "docs" / "domain" / "triage.json").write_text(
            json.dumps({"tweak_path": ["**"]}), encoding="utf-8")
        code, _, out = triage_cli(repo=self.repo)
        self.assertEqual(code, 2)
        self.assertIn("INVALID_TRIAGE_INPUT", out)

    def test_layer_paths_map_otherwise_unmapped_code(self):
        (self.repo / "docs" / "domain" / "triage.json").write_text(
            json.dumps({"layer_paths": {"infra": ["deploy/**"]}}), encoding="utf-8")
        self.git("add", "-A")
        self.git("-c", "user.email=t@example.com", "-c", "user.name=t", "-c",
                 "commit.gpgsign=false", "commit", "-q", "-m", "config")
        (self.repo / "deploy").mkdir()
        (self.repo / "deploy" / "main.tf").write_text("resource \"x\" \"y\" {}\n", encoding="utf-8")
        _, result, out = triage_cli(repo=self.repo)
        self.assertEqual(result["lane"], "TWEAK", out)


class DiffParsingTest(unittest.TestCase):
    def test_reverse_apply_restores_the_old_file(self):
        old = "a\nb\nc\nd\ne\n"
        new = "a\nB\nc\nd\ne\nf\n"
        diff = ("--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
                "@@ -5,1 +5,2 @@\n e\n+f\n")
        [file_diff] = parse_unified_diff(diff)
        self.assertEqual(reverse_apply(new, file_diff), old)

    def test_removed_line_starting_with_dashes_is_content_not_a_header(self):
        diff = ("diff --git a/x.sql b/x.sql\n--- a/x.sql\n+++ b/x.sql\n@@ -1,2 +1,1 @@\n"
                "--- a comment\n keep\n")
        [file_diff] = parse_unified_diff(diff)
        self.assertEqual(file_diff.path, "x.sql")
        self.assertEqual(file_diff.removed_lines(), ["-- a comment"])

    def test_mismatched_diff_is_detected(self):
        [file_diff] = parse_unified_diff("--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-a\n+b\n")
        with self.assertRaises(ValueError):
            reverse_apply("zzz\n", file_diff)


if __name__ == "__main__":
    unittest.main()
