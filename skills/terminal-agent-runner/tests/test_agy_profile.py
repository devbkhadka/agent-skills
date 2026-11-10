import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "agy_profile" / "bin" / "agy"

# Stands in for the real agy: reports the HOME it was given and the HOME each kind of
# shell it starts ends up with, the way agy runs the agent's commands.
FAKE_AGY = """#!/bin/sh
echo "agy=$HOME"
zsh -c 'echo "zsh=$HOME"'
bash -c 'echo "bash=$HOME"'
"""


@unittest.skipUnless(shutil.which("zsh") and shutil.which("bash"), "needs zsh and bash")
class AgyProfileWrapperTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        base = Path(self._td.name).resolve()
        self.real_home = base / "home"
        self.account = base / "accounts" / "personal"
        self.bin = base / "bin"
        for d in (self.real_home, self.account, self.bin):
            d.mkdir(parents=True)
        fake = self.bin / "agy"
        fake.write_text(FAKE_AGY)
        fake.chmod(0o755)
        (self.real_home / ".gemini" / "config").mkdir(parents=True)
        (self.real_home / ".gemini" / "GEMINI.md").write_text("rules")

    def run_wrapper(self, **extra_env):
        env = {"PATH": f"{WRAPPER.parent}:{self.bin}:/usr/bin:/bin", "HOME": str(self.real_home),
               "AGY_ACCOUNT_HOME": str(self.account), **extra_env}
        proc = subprocess.run([str(WRAPPER)], env=env, capture_output=True, text=True, timeout=30)
        return proc

    def test_agy_gets_the_account_home_and_its_shells_get_the_real_one(self):
        proc = self.run_wrapper()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), [f"agy={self.account}", f"zsh={self.real_home}",
                                               f"bash={self.real_home}"])

    def test_shared_settings_are_linked_into_a_new_account(self):
        self.run_wrapper()
        gemini = self.account / ".gemini"
        self.assertEqual((gemini / "config").resolve(), self.real_home / ".gemini" / "config")
        self.assertEqual((gemini / "GEMINI.md").read_text(), "rules")
        self.assertFalse((gemini / "skills").exists(), "nothing to link when the real one is absent")

    def test_an_account_keeps_its_own_copy(self):
        own = self.account / ".gemini" / "config"
        own.mkdir(parents=True)
        self.run_wrapper()
        self.assertFalse(own.is_symlink())

    def test_worker_tab_runs_the_users_zshrc_with_their_own_zdotdir(self):
        # compinit and similar write caches under ${ZDOTDIR:-$HOME}; the installed
        # zdotdir is read-only, so the user's .zshrc must see its own location.
        (self.real_home / ".zshrc").write_text('touch "${ZDOTDIR:-$HOME}/.zcompdump"\n')
        zdotdir = WRAPPER.parent.parent / "zdotdir"
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.real_home),
               "ZDOTDIR": str(zdotdir), "AGY_SHIM_DIR": str(WRAPPER.parent)}
        proc = subprocess.run(["zsh", "-ic", "whence -p agy"], env=env, capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(proc.stdout.strip(), str(WRAPPER))
        self.assertTrue((self.real_home / ".zcompdump").exists())
        self.assertFalse((zdotdir / ".zcompdump").exists())

    def test_missing_account_home_is_refused(self):
        proc = self.run_wrapper(AGY_ACCOUNT_HOME=str(self.account / "missing"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("does not exist", proc.stderr)
        self.assertNotIn("agy=", proc.stdout)


if __name__ == "__main__":
    unittest.main()
