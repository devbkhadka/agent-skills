"""Unit tests for the shared rendering harness (lib/render.py).

Uses a throwaway template of its own; the four real templates under templates/docs/ belong to
the wave-3 renderer beads and are not exercised here.
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from lib.render import RenderError, render_document  # noqa: E402


class RenderDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="afp-render-test-"))
        self.template_dir = self.tmp / "templates"
        self.template_dir.mkdir()
        self.probe = self.template_dir / "probe.md.tmpl"
        self.probe.write_text("# ${title}\n\n${body}\n", encoding="utf-8")
        self.ctx = {"title": "Probe Document", "body": "One declared sentence."}
        self.common = dict(artifact_path="domain-lexicon.json", renderer_name="render-probe")

    def tearDown(self):
        for p in self.tmp.rglob("*"):
            try:
                p.chmod(stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _out_dir(self, name):
        d = self.tmp / name
        d.mkdir()
        return d

    def test_renders_with_all_placeholders_filled(self):
        out_dir = self._out_dir("a")
        path = render_document(
            "probe.md.tmpl", dict(self.ctx), out_dir, "probe.md",
            template_dir=self.template_dir, **self.common,
        )
        self.assertTrue(path.is_file())
        body = path.read_text(encoding="utf-8")
        self.assertIn("Probe Document", body)
        self.assertIn("One declared sentence.", body)

    def test_missing_placeholder_value_refuses(self):
        out_dir = self._out_dir("gap")
        with self.assertRaises(RenderError):
            render_document(
                "probe.md.tmpl", {"title": "Only Half"}, out_dir, "probe.md",
                template_dir=self.template_dir, **self.common,
            )
        self.assertFalse((out_dir / "probe.md").exists())

    def test_unused_context_key_refuses(self):
        out_dir = self._out_dir("extra")
        surplus = dict(self.ctx)
        surplus["unused_section"] = "content no template shows"
        with self.assertRaises(RenderError):
            render_document(
                "probe.md.tmpl", surplus, out_dir, "probe.md",
                template_dir=self.template_dir, **self.common,
            )
        self.assertFalse((out_dir / "probe.md").exists())

    def test_missing_template_refuses(self):
        out_dir = self._out_dir("no-template")
        with self.assertRaises(RenderError):
            render_document(
                "does-not-exist.md.tmpl", dict(self.ctx), out_dir, "probe.md",
                template_dir=self.template_dir, **self.common,
            )

    def test_banner_names_artifact_and_renderer_and_says_do_not_edit(self):
        out_dir = self._out_dir("banner")
        path = render_document(
            "probe.md.tmpl", dict(self.ctx), out_dir, "probe.md",
            template_dir=self.template_dir, **self.common,
        )
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:6])
        self.assertIn("domain-lexicon.json", head)
        self.assertIn("render-probe", head)
        self.assertIn("do not edit", head.lower())

    def test_written_mode_is_0444(self):
        out_dir = self._out_dir("mode")
        path = render_document(
            "probe.md.tmpl", dict(self.ctx), out_dir, "probe.md",
            template_dir=self.template_dir, **self.common,
        )
        mode = os.stat(path).st_mode & 0o777
        self.assertEqual(mode, 0o444)

    def test_rerender_over_existing_readonly_document_succeeds(self):
        out_dir = self._out_dir("regen")
        first = render_document(
            "probe.md.tmpl", dict(self.ctx), out_dir, "probe.md",
            template_dir=self.template_dir, **self.common,
        )
        self.assertEqual(os.stat(first).st_mode & 0o777, 0o444)
        second = render_document(
            "probe.md.tmpl", dict(self.ctx), out_dir, "probe.md",
            template_dir=self.template_dir, **self.common,
        )
        self.assertEqual(first, second)
        self.assertEqual(os.stat(second).st_mode & 0o777, 0o444)

    def test_same_template_and_context_render_byte_identical(self):
        out_a = self._out_dir("same-a")
        out_b = self._out_dir("same-b")
        path_a = render_document(
            "probe.md.tmpl", dict(self.ctx), out_a, "probe.md",
            template_dir=self.template_dir, **self.common,
        )
        path_b = render_document(
            "probe.md.tmpl", dict(self.ctx), out_b, "probe.md",
            template_dir=self.template_dir, **self.common,
        )
        self.assertEqual(path_a.read_bytes(), path_b.read_bytes())

    def test_default_template_dir_is_used_when_not_overridden(self):
        out_dir = self._out_dir("default-dir")
        with self.assertRaises(RenderError):
            render_document(
                "probe.md.tmpl", dict(self.ctx), out_dir, "probe.md", **self.common,
            )


if __name__ == "__main__":
    unittest.main()
