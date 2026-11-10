import importlib.util
import io
import os
import re
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
TAR_ROOT = Path(__file__).resolve().parents[1]
FLEET_SCRIPT = TAR_ROOT / "scripts" / "fleet.py"

HERD_SCRIPT = TAR_ROOT / "scripts" / "herd.sh"
POOL_SCRIPT = TAR_ROOT / "scripts" / "resource_pool.py"
SCHEDULER_SCRIPT = ROOT / "skills" / "beads-task-scheduler" / "scripts" / "scheduler.py"

AGENT_HERDER_DOC = ROOT / "skills" / "agent-herder" / "SKILL.md"
TAR_DOC = TAR_ROOT / "SKILL.md"


def _block_from(lines, start, end_pattern):
    for j in range(start + 1, len(lines)):
        if re.match(end_pattern, lines[j]):
            return lines[start:j]
    return lines[start:]


def _block(src, start_pattern, end_pattern):
    lines = src.splitlines()
    start = next(i for i, line in enumerate(lines) if re.search(start_pattern, line))
    return _block_from(lines, start, end_pattern)


def _case_flags(block_lines):
    """Flags a bash `case` block matches, and which of them take a separate value.

    herd.sh spells every value-taking option with both a `--flag)` and a
    `--flag=*)` label, so the `=*` label is what marks a flag as taking a value.
    """
    flags, value_flags = set(), set()
    for line in block_lines:
        m = re.match(r"\s*([-\w|=*]+)\)\s*$", line)
        if not m:
            continue
        for label in m.group(1).split("|"):
            if not label.startswith("-"):
                continue
            name, _, suffix = label.partition("=")
            flags.add(name)
            if suffix == "*":
                value_flags.add(name)
    return flags, value_flags


def parse_herd_surface():
    """Derive herd.sh's own CLI surface from the wrapper script.

    fleet.py's argparse is not the authority on options and subcommands herd.sh
    handles itself, so read them out of herd.sh instead of hardcoding a skip
    list that can drift from what the wrapper actually implements.
    """
    content = HERD_SCRIPT.read_text()
    lines = content.splitlines()

    flags, value_flags = _case_flags(
        _block(content, r"^# Parse wrapper arguments", r"^done$")
    )

    # A wrapper option that reappears on an `exec` line is handed to fleet.py,
    # so it stays in the args fleet.py's parser is checked against.
    exec_tokens = set()
    for line in lines:
        if "exec python3" in line:
            exec_tokens.update(line.split())
    consumed_flags = {f for f in flags if f not in exec_tokens}

    subcommands = {}
    for i, line in enumerate(lines):
        for m in re.finditer(r'\$\{ARGS\[0\]\}" == "(\w+)"', line):
            branch = _block_from(lines, i, r"^fi$")
            # A branch that exits ends the wrapper. One that falls through hands
            # the subcommand to fleet.py, which stays the authority on it.
            if not any(re.match(r"\s*exit 0$", b) for b in branch):
                continue
            sub_flags, sub_value_flags = _case_flags(branch)
            subcommands[m.group(1)] = (sub_flags, sub_value_flags)

    return {
        "flags": flags,
        "value_flags": value_flags,
        "consumed_flags": consumed_flags,
        "subcommands": subcommands,
    }


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaptureParser(Exception):
    pass


def get_parser(module, main_func="main"):
    captured = None

    def fake_parse_args(self, *args, **kwargs):
        nonlocal captured
        captured = self
        raise CaptureParser()

    with mock.patch("argparse.ArgumentParser.parse_args", fake_parse_args):
        try:
            getattr(module, main_func)()
        except CaptureParser:
            pass
    return captured


HERD_SURFACE = parse_herd_surface()
HERD_FLAGS = HERD_SURFACE["flags"]
HERD_VALUE_FLAGS = HERD_SURFACE["value_flags"]
# Options herd.sh consumes without forwarding them to fleet.py.
HERD_CONSUMED_FLAGS = HERD_SURFACE["consumed_flags"]
# Subcommands herd.sh runs to completion itself, mapped to the flags each accepts.
HERD_SUBCOMMANDS = HERD_SURFACE["subcommands"]


def strip_consumed_flags(raw_args):
    """Drop the options herd.sh eats, leaving what it forwards to fleet.py."""
    kept, skip_value = [], False
    for arg in raw_args:
        if skip_value:
            skip_value = False
            continue
        name = arg.split("=")[0]
        if name in HERD_CONSUMED_FLAGS:
            skip_value = "=" not in arg and name in HERD_VALUE_FLAGS
            continue
        kept.append(arg)
    return kept


def sanitize_placeholders(arg_str: str) -> str:
    """Replace doc placeholders and shell variables with valid CLI dummy values."""
    # Strip shell line-end comments
    arg_str = re.sub(r"#.*$", "", arg_str).strip()

    # Replace specific placeholders with valid tokens
    replacements = [
        (r"<bead-id>", "b1"),
        (r"<id>", "b1"),
        (r"<bead title \+ acceptance criteria>", "Sample task description"),
        (r"<group>", "repo:/repo"),
        (r"<agent>", "worker-1"),
        (r"<name>", "worker-1"),
        (r"<repo-root>", "/repo"),
        (r"\$SESSION", "fleet-123"),
        (r"\$MY_FLEET", "fleet-123"),
        (r"\$REPO", "/repo"),
        (r"repo:\$REPO", "repo:/repo"),
        (r"\.\.\.", "worker-1"),
    ]
    for pat, rep in replacements:
        arg_str = re.sub(pat, rep, arg_str)

    # Replace any remaining <placeholder> with a generic valid identifier
    arg_str = re.sub(r"<[a-zA-Z0-9_\-\+ ]+>", "dummy_val", arg_str)
    return arg_str.strip()


def extract_invocations_from_fenced_blocks(content: str, file_path: str):
    """Extract multi-line joined commands from markdown code blocks."""
    lines = content.splitlines()
    in_block = False
    block_lines = []
    invocations = []

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_block:
                invocations.extend(_parse_block(block_lines, file_path))
                block_lines = []
                in_block = False
            else:
                in_block = True
            continue

        if in_block:
            block_lines.append((i, line))

    return invocations


def _parse_block(block_lines, file_path):
    invocations = []
    current_cmd = ""
    start_line = 0

    for line_num, line in block_lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue

        if not current_cmd:
            start_line = line_num

        if s.endswith("\\"):
            current_cmd += " " + s[:-1].strip()
        else:
            current_cmd += " " + s
            invocations.append((file_path, start_line, current_cmd.strip()))
            current_cmd = ""

    if current_cmd:
        invocations.append((file_path, start_line, current_cmd.strip()))
    return invocations


def extract_invocations_from_recovery_table(content: str, file_path: str):
    """Extract actionable recovery commands from the recovery table."""
    invocations = []
    lines = content.splitlines()
    in_recovery_table = False

    for line_num, line in enumerate(lines, start=1):
        if "Recovery, by failure mode:" in line:
            in_recovery_table = True
            continue

        if in_recovery_table:
            stripped = line.strip()
            if not stripped or stripped.startswith("##") or stripped.startswith(">"):
                in_recovery_table = False
                continue

            if stripped.startswith("|") and not stripped.startswith("|---") and not "What happened" in stripped:
                parts = [p.strip() for p in stripped.split("|")]
                # In | What happened | Do this |, parts[2] is the "Do this" column
                if len(parts) >= 3:
                    do_this = parts[2]
                    # Extract backticked command fragments or plain commands
                    cmds = re.findall(r"`([^`]+)`", do_this) or [do_this]
                    for cmd_item in cmds:
                        for part in re.split(r",\s*then\s+|;\s*", cmd_item):
                            part = part.strip()
                            if part and not part.startswith("re-run") and not part.startswith("leave"):
                                invocations.append((file_path, line_num, part))

    return invocations


def is_shell_builtin_or_assignment(line: str) -> bool:
    s = line.strip()
    if s.startswith("test ") or s.startswith("export "):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", s):
        return True
    return False


def categorize_and_tokenize(raw_cmd: str):
    """Identify which tool the command targets and return (tool_name, argv_tokens)."""
    if is_shell_builtin_or_assignment(raw_cmd):
        return None, []

    sanitized = sanitize_placeholders(raw_cmd)
    try:
        tokens = shlex.split(sanitized)
    except ValueError:
        return None, []

    if not tokens:
        return None, []

    idx = 0
    target_tool = None
    args = []

    while idx < len(tokens):
        token = tokens[idx]
        if token in ("python3", "python", "bash", "sh"):
            idx += 1
            continue

        # fleet.py
        if re.search(r'(?:^|/|\$)fleet\.py"?$', token) or token in ('"$RUN"', '$RUN', 'RUN'):
            target_tool = "fleet"
            args = tokens[idx + 1:]
            break
        # resource_pool.py
        elif re.search(r'(?:^|/|\$)resource_pool\.py"?$', token) or token in ('"$POOL"', '$POOL', 'POOL'):
            target_tool = "resource_pool"
            args = tokens[idx + 1:]
            break
        # scheduler.py
        elif re.search(r'(?:^|/|\$)scheduler\.py"?$', token) or token in ('"$SCHED"', '$SCHED', 'SCHED'):
            target_tool = "scheduler"
            args = tokens[idx + 1:]
            break
        # herd.sh (strict: not auto_herd.sh or cleanup_fleet.sh)
        elif (re.search(r'(?:^|/)herd\.sh"?$', token) or token in ('"$HERD"', '$HERD', 'HERD')) and not token.endswith("auto_herd.sh"):
            args = strip_consumed_flags(tokens[idx + 1:])
            # Subcommands herd.sh owns itself are never forwarded to fleet.py, so its
            # parser is not the authority on them — the wrapper is.
            if any(a in HERD_SUBCOMMANDS for a in args):
                target_tool = "herd_wrapper"
            else:
                target_tool = "fleet"
            break
        else:
            idx += 1

    return target_tool, args


class TestDocumentedCliSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fleet_mod = load_module("fleet_mod", FLEET_SCRIPT)
        cls.fleet_parser = get_parser(cls.fleet_mod)

        cls.pool_mod = load_module("pool_mod", POOL_SCRIPT)
        cls.pool_parser = get_parser(cls.pool_mod)

        if SCHEDULER_SCRIPT.exists():
            cls.sched_mod = load_module("sched_mod", SCHEDULER_SCRIPT)
            cls.sched_parser = get_parser(cls.sched_mod)
        else:
            cls.sched_parser = None

    def _validate_herd_wrapper(self, file_path, line_num, raw_cmd, args):
        """Validate a wrapper-owned invocation against the surface herd.sh implements."""
        sub = next((a for a in args if a in HERD_SUBCOMMANDS), None)
        sub_flags, sub_value_flags = HERD_SUBCOMMANDS[sub]
        allowed_flags = HERD_FLAGS | sub_flags
        value_flags = HERD_VALUE_FLAGS | sub_value_flags

        unknown_flags, positionals = [], []
        awaiting_value = None
        for arg in args:
            if awaiting_value:
                awaiting_value = None
                continue
            if arg.startswith("-"):
                name = arg.split("=")[0]
                if name not in allowed_flags:
                    unknown_flags.append(arg)
                elif "=" not in arg and name in value_flags:
                    awaiting_value = name
                continue
            positionals.append(arg)

        detail = (
            f"CLI parse error in {file_path}:{line_num}\n"
            f"Command: {raw_cmd}\n"
            f"Target script: herd.sh (wrapper-owned subcommand {sub!r})\n"
            f"Args evaluated: {args}\n"
        )
        self.assertEqual(
            unknown_flags,
            [],
            detail + f"herd.sh accepts only {sorted(allowed_flags)} here",
        )
        self.assertEqual(
            positionals,
            [sub],
            detail + f"herd.sh takes no positional beyond {sub!r}",
        )
        self.assertIsNone(
            awaiting_value,
            detail + f"{awaiting_value} is missing its value",
        )

    def _validate_invocation(self, file_path, line_num, raw_cmd, target_tool, args):
        if not target_tool or not args:
            return

        if target_tool == "herd_wrapper":
            self._validate_herd_wrapper(file_path, line_num, raw_cmd, args)
            return

        parser = None
        if target_tool == "fleet":
            parser = self.fleet_parser
        elif target_tool == "resource_pool":
            parser = self.pool_parser
        elif target_tool == "scheduler":
            parser = self.sched_parser

        if parser is None:
            return

        # Skip --help invocations as they trigger sys.exit(0)
        if "-h" in args or "--help" in args:
            return

        try:
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                parser.parse_args(args)
        except SystemExit as e:
            self.fail(
                f"CLI parse error in {file_path}:{line_num}\n"
                f"Command: {raw_cmd}\n"
                f"Target script: {target_tool}.py\n"
                f"Args evaluated: {args}\n"
                f"Parser exit code: {e.code}"
            )

    def test_documented_invocations_in_skills(self):
        docs = [AGENT_HERDER_DOC, TAR_DOC]
        total_invocations = 0

        for doc_path in docs:
            self.assertTrue(doc_path.exists(), f"Doc file {doc_path} must exist")
            content = doc_path.read_text()

            # 1. Fenced blocks
            fenced = extract_invocations_from_fenced_blocks(content, str(doc_path))
            for fpath, lnum, raw_cmd in fenced:
                target_tool, args = categorize_and_tokenize(raw_cmd)
                if target_tool:
                    total_invocations += 1
                    with self.subTest(file=fpath, line=lnum, cmd=raw_cmd):
                        self._validate_invocation(fpath, lnum, raw_cmd, target_tool, args)

            # 2. Recovery table invocations
            table_invocations = extract_invocations_from_recovery_table(content, str(doc_path))
            for fpath, lnum, raw_cmd in table_invocations:
                target_tool, args = categorize_and_tokenize(raw_cmd)
                if target_tool:
                    total_invocations += 1
                    with self.subTest(file=fpath, line=lnum, cmd=raw_cmd):
                        self._validate_invocation(fpath, lnum, raw_cmd, target_tool, args)

        self.assertGreaterEqual(total_invocations, 10, "Must test at least 10 documented invocations")

    def test_known_drifts_fail_explicitly(self):
        """Negative test ensuring known drifted commands fail validation."""
        # fleet.py reconcile without --group must parse successfully (group is optional)
        parsed = self.fleet_parser.parse_args(["reconcile"])
        self.assertIsNone(parsed.group)

        # resource_pool.py status --json must fail (status outputs JSON by default, has no --json)
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                self.pool_parser.parse_args(["status", "--json"])

        # The wrapper surface must be derived from a herd.sh that really owns init,
        # and fleet.py must really reject it — that pairing is what justifies
        # validating documented init invocations against herd.sh instead.
        self.assertIn("init", HERD_SUBCOMMANDS)
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                self.fleet_parser.parse_args(["init", "--cwd", "/repo"])

        # --session is consumed and re-emitted, so it stays in the forwarded args;
        # --user-explicit-attached never reaches fleet.py.
        self.assertIn("--user-explicit-attached", HERD_CONSUMED_FLAGS)
        self.assertNotIn("--session", HERD_CONSUMED_FLAGS)
        self.assertEqual(
            strip_consumed_flags(["--attached", "--user-explicit-attached", "status"]),
            ["status"],
        )

        # The documented init shape passes wrapper validation; a drifted flag or a
        # stray positional does not.
        self._validate_herd_wrapper("test", 0, "herd.sh init", ["--session", "f1", "init", "--cwd", "/repo"])
        for drifted in (["init", "--workdir", "/repo"], ["init", "status"], ["init", "--cwd"]):
            with self.assertRaises(self.failureException):
                self._validate_herd_wrapper("test", 0, "herd.sh " + " ".join(drifted), drifted)


if __name__ == "__main__":
    unittest.main()
