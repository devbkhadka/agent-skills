import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "quick_prompt.py"

VALID = """---
name: greet
summary: Greet someone
tags: [demo]
placeholders:
  who:
    required: true
    description: Person to greet
  mood:
    required: false
    default: cheerful
    description: Tone
  note:
    required: false
    description: Extra line
---
Hello {{who}}, in a {{mood}} way.
{{#note}}Note: {{note}}{{/note}}
Bye.
"""


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self.home = Path(self._home.name)
        self.addCleanup(self._home.cleanup)

    def run_cli(self, *args, stdin=None):
        env = {**os.environ, "QUICK_PROMPT_HOME": str(self.home), "HOME": str(self.home)}
        proc = subprocess.run(
            [sys.executable, str(CLI), *args],
            input=stdin, capture_output=True, text=True, env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def error(self, stderr):
        return json.loads(stderr.strip().splitlines()[-1])

    def add(self, name, text):
        code, out, err = self.run_cli("add", name, "--file", "-", stdin=text)
        self.assertEqual(code, 0, err)

    def write_raw(self, name, text):
        d = self.home / "templates"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(text, encoding="utf-8")


class RenderTests(CliTestCase):
    def test_render_fills_required_and_default_and_drops_empty_optional_section_line(self):
        self.add("greet", VALID)
        code, out, _ = self.run_cli("render", "greet", "--values-json", '{"who": "Ana"}')
        self.assertEqual(code, 0)
        self.assertEqual(out, "Hello Ana, in a cheerful way.\nBye.\n")

    def test_render_keeps_section_when_optional_value_given_and_override_default(self):
        self.add("greet", VALID)
        values = json.dumps({"who": "Ana", "mood": "dry", "note": "be brief"})
        code, out, _ = self.run_cli("render", "greet", "--values-json", values)
        self.assertEqual(code, 0)
        self.assertEqual(out, "Hello Ana, in a dry way.\nNote: be brief\nBye.\n")

    def test_render_reads_values_from_stdin(self):
        self.add("greet", VALID)
        code, out, _ = self.run_cli("render", "greet", "--values-json", "-", stdin='{"who": "Ana"}')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Hello Ana,"))

    def test_render_inline_section_removed_without_touching_line(self):
        self.add("inline", """---
name: inline
summary: Inline section
placeholders:
  ref:
    required: true
  repo:
    required: false
---
Review {{ref}}{{#repo}} in {{repo}}{{/repo}} now.
""")
        _, without, _ = self.run_cli("render", "inline", "--values-json", '{"ref": "#1"}')
        _, with_repo, _ = self.run_cli("render", "inline", "--values-json", '{"ref": "#1", "repo": "api"}')
        self.assertEqual(without, "Review #1 now.\n")
        self.assertEqual(with_repo, "Review #1 in api now.\n")

    def test_render_missing_required_exits_3_and_lists_all_missing(self):
        self.add("two", """---
name: two
summary: Two required
placeholders:
  a:
    required: true
    description: first
  b:
    required: true
    description: second
---
{{a}} {{b}}
""")
        code, out, err = self.run_cli("render", "two", "--values-json", '{"a": ""}')
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        payload = self.error(err)
        self.assertEqual(payload["error"], "missing_required")
        self.assertEqual([m["name"] for m in payload["missing"]], ["a", "b"])
        self.assertEqual(payload["missing"][1]["description"], "second")

    def test_render_rejects_undeclared_value_names(self):
        self.add("greet", VALID)
        code, _, err = self.run_cli("render", "greet", "--values-json", '{"who": "Ana", "wh0": "x"}')
        self.assertEqual(code, 2)
        self.assertEqual(self.error(err)["unknown"], ["wh0"])

    def test_render_does_not_reexpand_braces_inside_values(self):
        self.add("greet", VALID)
        code, out, _ = self.run_cli("render", "greet", "--values-json", '{"who": "{{mood}}"}')
        self.assertEqual(code, 0)
        self.assertIn("Hello {{mood}},", out)

    def test_render_non_string_values_are_stringified(self):
        self.add("greet", VALID)
        _, out, _ = self.run_cli("render", "greet", "--values-json", '{"who": 482}')
        self.assertIn("Hello 482,", out)

    def test_render_bad_json_is_usage_error(self):
        self.add("greet", VALID)
        code, _, err = self.run_cli("render", "greet", "--values-json", "{not json")
        self.assertEqual(code, 2)
        self.assertEqual(self.error(err)["error"], "bad_values_json")

    def test_render_unknown_template_exits_4(self):
        code, _, err = self.run_cli("render", "nope")
        self.assertEqual(code, 4)
        self.assertEqual(self.error(err)["error"], "not_found")


class ValidationTests(CliTestCase):
    def assert_invalid(self, text, fragment, name="bad"):
        code, _, err = self.run_cli("add", name, "--file", "-", stdin=text)
        self.assertEqual(code, 5, err)
        problems = self.error(err)["problems"]
        self.assertTrue(any(fragment in p for p in problems), problems)
        self.assertFalse((self.home / "templates" / f"{name}.md").exists())

    def test_undeclared_placeholder_in_body(self):
        self.assert_invalid("---\nname: bad\nsummary: s\n---\nHi {{who}}\n", "undeclared placeholder 'who'")

    def test_declared_placeholder_never_used(self):
        self.assert_invalid("---\nname: bad\nsummary: s\nplaceholders:\n  who:\n    required: true\n---\nHi\n",
                            "declared but never used")

    def test_required_with_default_is_contradictory(self):
        self.assert_invalid(
            "---\nname: bad\nsummary: s\nplaceholders:\n  who:\n    required: true\n    default: x\n---\n{{who}}\n",
            "cannot have a default")

    def test_name_mismatch_with_target(self):
        self.assert_invalid(VALID, "expected 'bad'")

    def test_summary_too_long(self):
        self.assert_invalid(f"---\nname: bad\nsummary: {'x' * 101}\n---\nHi\n", "keep it to 100")

    def test_unknown_frontmatter_key(self):
        self.assert_invalid("---\nname: bad\nsummary: s\ndescripton: typo\n---\nHi\n", "unknown frontmatter key")

    def test_unclosed_section(self):
        self.assert_invalid(
            "---\nname: bad\nsummary: s\nplaceholders:\n  x:\n    required: false\n---\n{{#x}}open\n",
            "never closed")

    def test_nested_sections_rejected(self):
        self.assert_invalid(
            "---\nname: bad\nsummary: s\nplaceholders:\n  x:\n    required: false\n  y:\n    required: false\n"
            "---\n{{#x}}{{#y}}{{y}}{{/y}}{{x}}{{/x}}\n",
            "nested section")

    def test_missing_frontmatter(self):
        self.assert_invalid("Just a body\n", "frontmatter")

    def test_invalid_name_argument(self):
        code, _, err = self.run_cli("add", "Bad_Name", "--file", "-", stdin=VALID)
        self.assertEqual(code, 2)
        self.assertEqual(self.error(err)["error"], "invalid_name")

    def test_validate_file_without_storing(self):
        code, out, _ = self.run_cli("validate", "--file", "-", stdin=VALID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["valid"], ["greet"])
        self.assertFalse((self.home / "templates").exists())

    def test_validate_store_reports_each_invalid_template(self):
        self.add("greet", VALID)
        self.write_raw("broken", "---\nname: broken\n---\n{{x}}\n")
        code, out, _ = self.run_cli("validate")
        self.assertEqual(code, 5)
        report = json.loads(out)
        self.assertEqual(report["valid"], ["greet"])
        self.assertIn("broken", report["invalid"])


class StoreTests(CliTestCase):
    def test_index_lists_name_tab_summary_and_skips_invalid_with_warning(self):
        self.add("greet", VALID)
        self.write_raw("broken", "no frontmatter\n")
        code, out, err = self.run_cli("index")
        self.assertEqual(code, 0)
        self.assertEqual(out, "greet\tGreet someone\n")
        self.assertIn("skipped_invalid_template", err)

    def test_index_on_empty_store_is_empty(self):
        code, out, _ = self.run_cli("index")
        self.assertEqual((code, out), (0, ""))

    def test_list_filters_by_tag(self):
        self.add("greet", VALID)
        self.add("other", "---\nname: other\nsummary: Other\ntags: [misc]\n---\nHi\n")
        _, out, _ = self.run_cli("list", "--tag", "demo", "--json")
        self.assertEqual([r["name"] for r in json.loads(out)], ["greet"])

    def test_show_json_exposes_placeholder_spec(self):
        self.add("greet", VALID)
        _, out, _ = self.run_cli("show", "greet", "--json")
        spec = json.loads(out)
        self.assertEqual(spec["placeholders"]["who"], {"required": True, "default": None, "description": "Person to greet"})
        self.assertEqual(spec["placeholders"]["mood"]["default"], "cheerful")
        self.assertFalse(spec["placeholders"]["note"]["required"])

    def test_placeholder_required_defaults_to_true(self):
        self.add("implicit", "---\nname: implicit\nsummary: s\nplaceholders:\n  x:\n    description: d\n---\n{{x}}\n")
        code, _, err = self.run_cli("render", "implicit")
        self.assertEqual(code, 3)
        self.assertEqual(self.error(err)["missing"][0]["name"], "x")

    def test_add_refuses_overwrite_and_update_replaces(self):
        self.add("greet", VALID)
        code, _, err = self.run_cli("add", "greet", "--file", "-", stdin=VALID)
        self.assertEqual(code, 2)
        self.assertEqual(self.error(err)["error"], "already_exists")

        code, _, _ = self.run_cli("update", "greet", "--file", "-", stdin=VALID.replace("Greet someone", "Say hi"))
        self.assertEqual(code, 0)
        _, out, _ = self.run_cli("index")
        self.assertEqual(out, "greet\tSay hi\n")

    def test_update_invalid_content_keeps_previous_version(self):
        self.add("greet", VALID)
        code, _, _ = self.run_cli("update", "greet", "--file", "-", stdin=VALID.replace("{{who}}", "{{whom}}"))
        self.assertEqual(code, 5)
        _, out, _ = self.run_cli("render", "greet", "--values-json", '{"who": "Ana"}')
        self.assertIn("Hello Ana", out)

    def test_update_missing_template_exits_4(self):
        code, _, _ = self.run_cli("update", "greet", "--file", "-", stdin=VALID)
        self.assertEqual(code, 4)

    def test_remove_deletes_and_then_not_found(self):
        self.add("greet", VALID)
        self.assertEqual(self.run_cli("remove", "greet")[0], 0)
        self.assertEqual(self.run_cli("show", "greet")[0], 4)
        self.assertEqual(self.run_cli("remove", "greet")[0], 4)

    def test_usage_error_is_json_exit_2(self):
        code, _, err = self.run_cli("render")
        self.assertEqual(code, 2)
        self.assertEqual(self.error(err)["error"], "usage")


FULL = """---
name: brief
summary: Brief a worker on a task
tags: [demo, delegation]
placeholders:
  task:
    description: What to do
  scope:
    required: false
    description: Paths the worker may modify
  refs:
    required: false
    description: Files to read first
  acceptance:
    required: false
    default: tests pass
    description: Proof of done
---
Task: {{task}}
{{#scope}}
Scope: modify only {{scope}}.{{/scope}}
{{#refs}}Read first: {{refs}}{{/refs}}

Rules for {{task}}:
- Stay in scope.

Done when: {{acceptance}}.
"""


class FullTemplateTests(CliTestCase):
    def test_full_template_renders_with_every_value(self):
        self.add("brief", FULL)
        values = {"task": "add export", "scope": "src/export/**", "refs": "PLAN.md", "acceptance": "npm test passes"}
        code, out, err = self.run_cli("render", "brief", "--values-json", json.dumps(values))
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out,
            "Task: add export\n\nScope: modify only src/export/**.\nRead first: PLAN.md\n\n"
            "Rules for add export:\n- Stay in scope.\n\nDone when: npm test passes.\n",
        )

    def test_full_template_renders_with_only_required_value(self):
        self.add("brief", FULL)
        code, out, err = self.run_cli("render", "brief", "--values-json", json.dumps({"task": "add export"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "Task: add export\n\nRules for add export:\n- Stay in scope.\n\nDone when: tests pass.\n")


if __name__ == "__main__":
    unittest.main()
