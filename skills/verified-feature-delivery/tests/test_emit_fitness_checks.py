#!/usr/bin/env python3
"""Behavioural tests for the architecture-rules skill.

- bin/emit-fitness-checks emits a checker that the user's repo runs in CI without this skill
  installed; it must pass a clean DAG's repo and refuse an impure one with reason codes.
- bin/validate-execution-dag and bin/decision-rules-cli run on a bare file, no planner-state.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
BIN_DIR = TESTS_DIR.parent / "bin"
STAGE_3 = TESTS_DIR / "cases" / "stage-3"

_loader = SourceFileLoader("emit_fitness_checks_bin", str(BIN_DIR / "emit-fitness-checks"))
_spec = importlib.util.spec_from_loader("emit_fitness_checks_bin", _loader)
emit = importlib.util.module_from_spec(_spec)
_loader.exec_module(emit)


def _emit(argv):
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["emit-fitness-checks"] + argv), \
            patch("sys.stdout", out), patch("sys.stderr", err):
        code = emit.main()
    return code, out.getvalue()


def _run_checker(checker: Path, repo: Path):
    # -I: isolated mode, so nothing from this skill can leak onto the checker's import path.
    result = subprocess.run([sys.executable, "-I", str(checker)], cwd=str(repo),
                            capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout


class EmittedCheckerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _emit_and_check(self, case_dir: Path):
        out = self.tmp / "emitted"
        code, _ = _emit([str(case_dir / "execution-dag.json"), "--out", str(out)])
        self.assertEqual(code, 0)
        return _run_checker(out / "check_architecture.py", case_dir)

    def test_clean_repo_passes(self):
        code, out = self._emit_and_check(STAGE_3 / "s3-valid-dag")
        self.assertEqual(code, 0, out)
        self.assertIn("ARCHITECTURE_CHECK_PASSED", out)

    def test_transitively_impure_pure_module_fails(self):
        code, out = self._emit_and_check(STAGE_3 / "impure-pure-module")
        self.assertEqual(code, 1)
        self.assertIn("IMPURE_PURE_MODULE", out)
        self.assertIn("axios", out)

    def test_checker_agrees_with_the_stage_3_gate_on_purity(self):
        # Every case the gate accepts on purity, the checker accepts too.
        for case in ("s3-valid-dag", "s3-pure-module-prefix-collision-accepted"):
            with self.subTest(case=case):
                self.assertEqual(self._emit_and_check(STAGE_3 / case)[0], 0)

    def _repo(self, files: dict, pure_modules):
        repo = self.tmp / "repo"
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
        dag = {"schema_version": "1.0.0", "feature_slug": "f", "pure_modules": pure_modules,
               "slices": []}
        (repo / "execution-dag.json").write_text(json.dumps(dag), encoding="utf-8")
        return repo

    def test_pure_module_importing_an_adapter_is_an_outward_dependency(self):
        repo = self._repo({
            "src/domain/pricing.ts": 'import { save } from "../adapters/store";\n',
            "src/adapters/store.ts": "export function save() {}\n",
        }, ["src/domain/pricing.ts"])
        code, out = self._emit_and_check(repo)
        self.assertEqual(code, 1)
        self.assertIn("OUTWARD_DEPENDENCY", out)
        self.assertIn("src/adapters/store.ts", out)

    def test_renamed_pure_module_is_reported_not_silently_skipped(self):
        repo = self._repo({"src/domain/other.py": "X = 1\n"}, ["src/domain/pricing.py"])
        code, out = self._emit_and_check(repo)
        self.assertEqual(code, 1)
        self.assertIn("PURE_MODULE_NOT_FOUND", out)

    def test_python_pure_module_importing_requests_fails(self):
        repo = self._repo({"src/domain/pricing.py": "import requests\n"},
                          ["src/domain/pricing.py"])
        code, out = self._emit_and_check(repo)
        self.assertEqual(code, 1)
        self.assertIn("IMPURE_PURE_MODULE", out)


class EmitterTest(unittest.TestCase):
    def test_re_emitting_produces_no_diff(self):
        dag = str(STAGE_3 / "s3-valid-dag" / "execution-dag.json")
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(_emit([dag, "--out", a])[0], 0)
            self.assertEqual(_emit([dag, "--out", b])[0], 0)
            for name in ("check_architecture.py", ".dependency-cruiser.cjs"):
                self.assertEqual((Path(a) / name).read_bytes(), (Path(b) / name).read_bytes())

    def test_stack_selects_linter_config(self):
        dag = str(STAGE_3 / "s3-valid-dag" / "execution-dag.json")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_emit([dag, "--out", tmp, "--stack", "python"])[0], 0)
            importlinter = (Path(tmp) / ".importlinter").read_text(encoding="utf-8")
            self.assertIn("type = forbidden", importlinter)
            self.assertIn("requests", importlinter)
            self.assertFalse((Path(tmp) / ".dependency-cruiser.cjs").exists())

    def test_auto_detects_typescript_from_pure_module_extensions(self):
        dag = str(STAGE_3 / "s3-valid-dag" / "execution-dag.json")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_emit([dag, "--out", tmp])[0], 0)
            cruiser = (Path(tmp) / ".dependency-cruiser.cjs").read_text(encoding="utf-8")
            self.assertIn("pure-module-impure-import", cruiser)
            self.assertIn("formatResult", cruiser)

    def test_dag_without_pure_modules_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "execution-dag.json"
            path.write_text(json.dumps({"feature_slug": "f", "slices": []}), encoding="utf-8")
            code, out = _emit([str(path), "--out", tmp])
            self.assertEqual(code, 1)
            self.assertIn("NO_PURE_MODULES_DECLARED", out)


class StandaloneValidatorsTest(unittest.TestCase):
    """Stage 3 validators run on a bare file from any directory: no state.json, no feature slug."""

    def _run(self, tool, path):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, str(BIN_DIR / tool), str(path)], cwd=cwd,
                                    capture_output=True, text=True, timeout=60)
        return result.returncode, result.stdout

    def test_validate_execution_dag_on_bare_file(self):
        code, out = self._run("validate-execution-dag", STAGE_3 / "s3-valid-dag" / "execution-dag.json")
        self.assertEqual(code, 0, out)
        self.assertIn("EXECUTION_DAG_VALIDATED_CLEAN", out)
        code, out = self._run("validate-execution-dag", STAGE_3 / "dag-cycle" / "execution-dag.json")
        self.assertEqual(code, 1)

    def test_decision_rules_cli_on_bare_file(self):
        decisions = json.loads((STAGE_3 / "s3-dim4-unlisted-strategy-refused" /
                                "slice-invariants.json").read_text(encoding="utf-8"))
        code, out = self._run("decision-rules-cli", STAGE_3 / "s3-dim4-unlisted-strategy-refused" /
                              "slice-invariants.json")
        self.assertEqual(code, 1)
        self.assertIn("DIMENSION_UNRESOLVED", out)
        decisions["locked_dimensions"]["4_idempotency_and_ordering"]["strategy"] = "GENERATION_SEQUENCING"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slice-invariants.json"
            path.write_text(json.dumps(decisions), encoding="utf-8")
            code, out = self._run("decision-rules-cli", path)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
