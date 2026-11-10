"""Fixtures for the read-only widening of the `reads`, `find-read` and `sed-read` allow rules.

Companion to the risk_policy.json change that lets an unattended fleet clear read-only
inspection prompts without a lead in the loop. The point of these cases is the boundary:
a command that only reads is approved, and the flag that turns the same command into a
mutation must still fall through to escalate or deny.
"""

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fleet = load_module("terminal_agent_runner_fleet", ROOT / "scripts" / "fleet.py")


class ReadOnlyAllowRules(unittest.TestCase):
    def setUp(self):
        self.policy = fleet.load_policy()

    def test_pure_inspection_commands_are_approved(self):
        """These write nothing, so an unattended wave must not stall on them.

        `test` is not in the `reads` alternation: the narrower `file-test-read` rule
        already owns it, so it is expected to keep matching under that rule name.
        """
        for prompt, rule in (
            ("Requesting permission for: test -f dev-plans/plan.md", "file-test-read"),
            ("Requesting permission for: test -d /Users/someone/.config/.agents/herder/s1", "file-test-read"),
            ("Command Requesting permission for: realpath apps/client-api", "reads"),
            ("Requesting permission for: dirname apps/client-api/src/main.ts", "reads"),
            ("Requesting permission for: basename apps/client-api/src/main.ts", "reads"),
            ("Requesting permission for: readlink node_modules/.bin/nx", "reads"),
            ("Requesting permission for: nl apps/x.txt", "reads"),
            ("Requesting permission for: cut -d: -f1 apps/x.txt", "reads"),
            ("Requesting permission for: uniq apps/x.txt", "reads"),
            ("Requesting permission for: printenv PATH", "reads"),
            ("Command Requesting permission for: find apps -name *.spec.ts", "find-read"),
            ("Requesting permission for: sed -n 1,80p apps/client-api/src/main.ts", "sed-read"),
        ):
            with self.subTest(prompt=prompt):
                result = fleet.classify_text(prompt, self.policy)
                self.assertEqual(result["action"], "approve")
                self.assertEqual(result.get("rule"), rule)

    def test_the_mutating_variants_are_never_approved(self):
        """The whole value of the narrow rules is that these still stop the pane."""
        for prompt in (
            "Requesting permission for: find . -name *.tmp -delete",
            "Requesting permission for: find apps -type f -execdir mv {} /tmp ;",
            "Requesting permission for: find apps -name x -fprint out.txt",
            "Requesting permission for: sed -i s/a/b/ apps/x.ts",
            "Requesting permission for: sed -n -i s/a/b/ apps/x.ts",
        ):
            with self.subTest(prompt=prompt):
                result = fleet.classify_text(prompt, self.policy)
                self.assertNotEqual(result["action"], "approve")

    def test_previously_allowed_reads_still_match_their_own_rule(self):
        """Widening the alternation must not have moved existing traffic onto a new rule."""
        for prompt, rule in (
            ("ls -la skills", "reads"),
            ("Command Requesting permission for: pwd", "reads"),
            ("git status", "git-read"),
            ("Command Requesting permission for: grep -rn cutover apps", "grep-read"),
        ):
            with self.subTest(prompt=prompt):
                result = fleet.classify_text(prompt, self.policy)
                self.assertEqual(result["action"], "approve")
                self.assertEqual(result.get("rule"), rule)

    def test_agy_allowlist_covers_the_pure_commands(self):
        """agy should not prompt at all for these; the allow rules are the second net."""
        policy = self.policy
        prefixes = {entry["prefix"] for entry in policy["agy_allow"]}
        for cmd in ("test", "realpath", "basename", "dirname", "readlink"):
            with self.subTest(cmd=cmd):
                self.assertIn(cmd, prefixes)


if __name__ == "__main__":
    unittest.main()
