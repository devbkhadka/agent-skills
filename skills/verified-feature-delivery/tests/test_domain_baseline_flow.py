#!/usr/bin/env python3
"""End-to-end test of the domain-baseline skill's documented procedure.

Drives bin/validate-lexicon and bin/baseline as a user would, through the CLI, and asserts on
exit codes, reason codes and the persisted baseline — never on internals:

init -> validate -> promote -> approve --all -> check clean -> second feature amends one entity ->
diff shows only that element -> promote without --amend refused (BASELINE_CONFLICT) -> with
--amend accepted -> check reports exactly that element STALE_APPROVAL -> element-scoped
re-approval -> clean again.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
BIN_DIR = TESTS_DIR.parent / "bin"
CASES = TESTS_DIR / "cases" / "baseline"
SEED = CASES / "seed-feature"
AMENDING = CASES / "conflicting-feature"  # changes only entity:Subscription


class DomainBaselineFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.baseline_dir = self.tmp / "docs" / "domain"

    def baseline(self, *args):
        result = subprocess.run(
            [sys.executable, str(BIN_DIR / "baseline"), "--baseline-dir", str(self.baseline_dir)]
            + list(args),
            cwd=str(self.tmp), capture_output=True, text=True, timeout=60)
        return result.returncode, result.stdout + result.stderr

    def index(self):
        return json.loads((self.baseline_dir / "baseline.json").read_text(encoding="utf-8"))

    def test_documented_procedure(self):
        # 1. init
        code, out = self.baseline("init")
        self.assertEqual(code, 0, out)
        self.assertIn("BASELINE_INITIALIZED", out)

        # 2. working lexicon validates
        result = subprocess.run([sys.executable, str(BIN_DIR / "validate-lexicon"),
                                 str(SEED / "domain-lexicon.json")],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 4. promote, 5. approve everything on first creation, 6. CI check clean
        code, out = self.baseline("promote", "--feature-dir", str(SEED))
        self.assertEqual(code, 0, out)
        self.assertIn("BASELINE_PROMOTED", out)
        self.assertTrue((self.baseline_dir / "docs" / "domain-entity-map.md").is_file())
        code, out = self.baseline("approve", "--all")
        self.assertEqual(code, 0, out)
        code, out = self.baseline("check", "--strict-approvals")
        self.assertEqual(code, 0, out)
        approvals_before = dict(self.index()["approvals"])

        # Amendment: diff shows exactly the one changed element
        code, out = self.baseline("diff", "--lexicon", str(AMENDING / "domain-lexicon.json"),
                                  "--json")
        self.assertEqual(code, 0, out)
        diff = json.loads(out)
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])
        self.assertEqual(diff["changed"], ["entity:Subscription"])

        # Changing an approved element without saying so is refused, and nothing is written
        code, out = self.baseline("promote", "--feature-dir", str(AMENDING))
        self.assertEqual(code, 1)
        self.assertIn("BASELINE_CONFLICT", out)
        self.assertEqual(self.index()["approvals"], approvals_before)
        self.assertEqual(self.index()["sources"], ["stripe-subscription-downgrade"])

        code, out = self.baseline("promote", "--feature-dir", str(AMENDING),
                                  "--amend", "entity:Subscription")
        self.assertEqual(code, 0, out)

        # Only the amended element needs re-approval
        code, out = self.baseline("check", "--strict-approvals", "--json")
        self.assertEqual(code, 1)
        audit = json.loads(out[out.index("{"):])
        self.assertEqual(audit["error_code"], "STALE_APPROVAL")
        self.assertEqual(audit["stale_approvals"], ["entity:Subscription"])
        self.assertEqual(audit["unapproved"], [])
        code, out = self.baseline("check")
        self.assertEqual(code, 0, out)  # advisory without --strict-approvals

        code, out = self.baseline("approve", "--element", "entity:Subscription")
        self.assertEqual(code, 0, out)
        code, out = self.baseline("check", "--strict-approvals")
        self.assertEqual(code, 0, out)

    def test_hand_edited_rendered_docs_fail_the_ci_check(self):
        self.assertEqual(self.baseline("init")[0], 0)
        self.assertEqual(self.baseline("promote", "--feature-dir", str(SEED))[0], 0)
        entity_map = self.baseline_dir / "docs" / "domain-entity-map.md"
        entity_map.chmod(0o644)  # rendered docs are written read-only; force the hand edit
        entity_map.write_text(entity_map.read_text(encoding="utf-8") + "\nhand edit\n",
                              encoding="utf-8")
        code, out = self.baseline("check")
        self.assertNotEqual(code, 0)
        self.assertIn("STALE_RENDERED_DOCS", out)


if __name__ == "__main__":
    unittest.main()
