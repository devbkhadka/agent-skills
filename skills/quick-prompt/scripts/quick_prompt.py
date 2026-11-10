#!/usr/bin/env python3
"""
Quick Prompt: a global library of reusable prompt templates for agents.

Templates live in $QUICK_PROMPT_HOME/templates/<name>.md (default ~/.quick-prompt).
Each file is Markdown with a small frontmatter block:

    ---
    name: pr-review
    summary: Strict code review of a PR focused on one area
    tags: [review, git]
    placeholders:
      pr_number:
        required: true
        description: PR number to review
      tone:
        required: false
        default: balanced
        description: strict | balanced | light
    ---
    Review PR #{{pr_number}} with a {{tone}} tone.
    {{#focus}}Pay particular attention to {{focus}}.{{/focus}}

Errors are written to stderr as one JSON object: {"error": <code>, "message": ...}.

Exit codes:
    0  ok
    2  usage error
    3  required values missing (stderr JSON lists them under "missing")
    4  template not found
    5  template invalid
"""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3
EXIT_NOT_FOUND = 4
EXIT_INVALID = 5

SUMMARY_MAX = 100
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VAR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VAR_RE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
SECTION_TAG_RE = re.compile(r"\{\{([#/])\s*([a-z][a-z0-9_]*)\s*\}\}")
# A section alone on its line (lead/trail captured) is removed together with that line.
SECTION_RE = re.compile(
    r"(?P<lead>^[ \t]*)?\{\{#\s*(?P<name>[a-z][a-z0-9_]*)\s*\}\}"
    r"(?P<inner>.*?)"
    r"\{\{/\s*(?P=name)\s*\}\}(?P<trail>[ \t]*\n)?",
    re.MULTILINE | re.DOTALL,
)
TOP_LEVEL_KEYS = {"name", "summary", "tags", "placeholders"}
PLACEHOLDER_KEYS = {"required", "default", "description"}


class QuickPromptError(Exception):
    def __init__(self, code, exit_code, message, **extra):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.message = message
        self.extra = extra

    def to_json(self):
        return json.dumps({"error": self.code, "message": self.message, **self.extra})


def invalid(message, problems=None):
    return QuickPromptError("invalid_template", EXIT_INVALID, message, problems=problems or [message])


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _scalar(raw):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def _inline_list(raw):
    value = raw.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    return [_scalar(item) for item in inner.split(",")] if inner else []


def parse_frontmatter(lines):
    """Parse the supported frontmatter subset: scalars, inline lists, and a two-level
    `placeholders` block (placeholder name at indent 2, attributes at indent 4)."""
    meta = {}
    current_placeholder = None
    in_placeholders = False
    for lineno, line in enumerate(lines, start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent + 1]:
            raise invalid(f"frontmatter line {lineno}: tabs are not allowed for indentation")
        key, sep, rest = line.strip().partition(":")
        if not sep:
            raise invalid(f"frontmatter line {lineno}: expected 'key: value'")
        key = key.strip()

        if indent == 0:
            in_placeholders = key == "placeholders"
            current_placeholder = None
            if key in meta:
                raise invalid(f"frontmatter line {lineno}: duplicate key '{key}'")
            if in_placeholders:
                if rest.strip():
                    raise invalid(f"frontmatter line {lineno}: 'placeholders' must be a block, not inline")
                meta["placeholders"] = {}
            else:
                as_list = _inline_list(rest)
                meta[key] = as_list if as_list is not None else _scalar(rest)
        elif indent == 2 and in_placeholders:
            if rest.strip():
                raise invalid(f"frontmatter line {lineno}: placeholder '{key}' must be followed by indented attributes")
            if key in meta["placeholders"]:
                raise invalid(f"frontmatter line {lineno}: duplicate placeholder '{key}'")
            meta["placeholders"][key] = {}
            current_placeholder = key
        elif indent == 4 and current_placeholder is not None:
            attrs = meta["placeholders"][current_placeholder]
            if key in attrs:
                raise invalid(f"frontmatter line {lineno}: duplicate attribute '{key}'")
            attrs[key] = _scalar(rest)
        else:
            raise invalid(f"frontmatter line {lineno}: unexpected indentation")
    return meta


def parse_template(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise invalid("template must start with a '---' frontmatter block")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise invalid("frontmatter block is not closed with '---'")
    meta = parse_frontmatter(lines[1:end])
    body = "\n".join(lines[end + 1:]).strip("\n") + "\n"
    return meta, body


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def body_problems(body, declared):
    problems = []
    used = set(VAR_RE.findall(body))

    stack = []
    for kind, name in SECTION_TAG_RE.findall(body):
        used.add(name)
        if kind == "#":
            if stack:
                problems.append(f"nested section '{{{{#{name}}}}}' inside '{{{{#{stack[-1]}}}}}' is not supported")
            stack.append(name)
        elif not stack or stack[-1] != name:
            problems.append(f"section close '{{{{/{name}}}}}' has no matching open")
        else:
            stack.pop()
    for name in stack:
        problems.append(f"section '{{{{#{name}}}}}' is never closed")

    for name in sorted(used - declared):
        problems.append(f"body uses undeclared placeholder '{name}'")
    for name in sorted(declared - used):
        problems.append(f"placeholder '{name}' is declared but never used in the body")
    return problems


def validate_template(meta, body, expected_name=None):
    """Return a normalised template dict or raise QuickPromptError listing every problem."""
    problems = []

    for key in sorted(set(meta) - TOP_LEVEL_KEYS):
        problems.append(f"unknown frontmatter key '{key}'")

    name = meta.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        problems.append("'name' must be a kebab-case slug (a-z, 0-9, single hyphens)")
    elif expected_name is not None and name != expected_name:
        problems.append(f"'name' is '{name}' but expected '{expected_name}'")

    summary = meta.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        problems.append("'summary' is required")
    elif len(summary) > SUMMARY_MAX:
        problems.append(f"'summary' is {len(summary)} chars; keep it to {SUMMARY_MAX} or fewer")

    tags = meta.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
        problems.append("'tags' must be an inline list of strings, e.g. [review, git]")
        tags = []

    placeholders = {}
    for pname, attrs in (meta.get("placeholders") or {}).items():
        if not VAR_NAME_RE.match(pname):
            problems.append(f"placeholder '{pname}' must be snake_case starting with a letter")
        for key in sorted(set(attrs) - PLACEHOLDER_KEYS):
            problems.append(f"placeholder '{pname}' has unknown attribute '{key}'")
        required = attrs.get("required", True)
        if not isinstance(required, bool):
            problems.append(f"placeholder '{pname}' 'required' must be true or false")
            required = True
        default = attrs.get("default")
        if default is not None:
            default = str(default)
            if required:
                problems.append(f"placeholder '{pname}' is required, so it cannot have a default")
        placeholders[pname] = {
            "required": required,
            "default": default,
            "description": str(attrs.get("description", "")),
        }

    if not body.strip():
        problems.append("template body is empty")
    problems.extend(body_problems(body, set(placeholders)))

    if problems:
        raise invalid(f"template has {len(problems)} problem(s)", problems)
    return {"name": name, "summary": summary.strip(), "tags": tags, "placeholders": placeholders, "body": body}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _present(value):
    return value is not None and str(value) != ""


def render(template, values):
    placeholders = template["placeholders"]

    unknown = sorted(set(values) - set(placeholders))
    if unknown:
        raise QuickPromptError(
            "unknown_placeholders", EXIT_USAGE,
            f"template '{template['name']}' does not declare: {', '.join(unknown)}",
            unknown=unknown, declared=sorted(placeholders),
        )

    resolved = {}
    missing = []
    for pname, spec in placeholders.items():
        value = values.get(pname)
        if _present(value):
            resolved[pname] = value if isinstance(value, str) else json.dumps(value) if isinstance(value, (list, dict)) else str(value)
        elif spec["default"] is not None:
            resolved[pname] = spec["default"]
        elif spec["required"]:
            missing.append(pname)
        else:
            resolved[pname] = ""
    if missing:
        raise QuickPromptError(
            "missing_required", EXIT_MISSING,
            f"missing required value(s): {', '.join(missing)}",
            missing=[{"name": m, "description": placeholders[m]["description"]} for m in missing],
        )

    def section(match):
        lead, trail = match.group("lead"), match.group("trail")
        if _present(resolved.get(match.group("name"))):
            return (lead or "") + match.group("inner") + (trail or "")
        if lead is not None and trail is not None:
            return ""  # section occupied whole line(s): drop the line too
        return (lead or "") + (trail or "")

    text = SECTION_RE.sub(section, template["body"])
    # Single pass so values containing '{{...}}' are never re-expanded.
    return VAR_RE.sub(lambda m: resolved[m.group(1)], text)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TemplateStore:
    def __init__(self, home=None):
        root = home or os.environ.get("QUICK_PROMPT_HOME") or Path.home() / ".quick-prompt"
        self.dir = Path(root).expanduser() / "templates"

    def path(self, name):
        if not NAME_RE.match(name):
            raise QuickPromptError("invalid_name", EXIT_USAGE, f"'{name}' is not a valid template name (kebab-case slug)")
        return self.dir / f"{name}.md"

    def load(self, name):
        path = self.path(name)
        if not path.is_file():
            raise QuickPromptError("not_found", EXIT_NOT_FOUND, f"no template named '{name}'", name=name)
        meta, body = parse_template(path.read_text(encoding="utf-8"))
        return validate_template(meta, body, expected_name=name)

    def names(self):
        if not self.dir.is_dir():
            return []
        return sorted(p.stem for p in self.dir.glob("*.md") if p.is_file())

    def load_all(self):
        """Yield (name, template|None, error|None) so one bad file does not hide the rest."""
        for name in self.names():
            try:
                yield name, self.load(name), None
            except QuickPromptError as exc:
                yield name, None, exc

    def write(self, name, text, must_exist):
        path = self.path(name)
        exists = path.is_file()
        if must_exist and not exists:
            raise QuickPromptError("not_found", EXIT_NOT_FOUND, f"no template named '{name}'", name=name)
        if not must_exist and exists:
            raise QuickPromptError("already_exists", EXIT_USAGE, f"template '{name}' already exists; use update", name=name)
        meta, body = parse_template(text)
        validate_template(meta, body, expected_name=name)
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text if text.endswith("\n") else text + "\n")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path

    def remove(self, name):
        path = self.path(name)
        if not path.is_file():
            raise QuickPromptError("not_found", EXIT_NOT_FOUND, f"no template named '{name}'", name=name)
        path.unlink()
        return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_source(source):
    if source == "-":
        return sys.stdin.read()
    path = Path(source).expanduser()
    if not path.is_file():
        raise QuickPromptError("file_not_found", EXIT_USAGE, f"file not found: {source}")
    return path.read_text(encoding="utf-8")


def _warn_skipped(name, exc):
    print(json.dumps({"warning": "skipped_invalid_template", "name": name, "problems": exc.extra.get("problems", [exc.message])}), file=sys.stderr)


def cmd_index(store, args):
    for name, template, exc in store.load_all():
        if exc:
            _warn_skipped(name, exc)
        else:
            print(f"{name}\t{template['summary']}")


def cmd_list(store, args):
    rows = []
    for name, template, exc in store.load_all():
        if exc:
            _warn_skipped(name, exc)
        elif not args.tag or args.tag in template["tags"]:
            rows.append({"name": name, "summary": template["summary"], "tags": template["tags"]})
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            tags = f"  [{', '.join(row['tags'])}]" if row["tags"] else ""
            print(f"{row['name']}\t{row['summary']}{tags}")


def cmd_show(store, args):
    if args.json:
        print(json.dumps(store.load(args.name), indent=2))
    else:
        store.load(args.name)  # validates
        print(store.path(args.name).read_text(encoding="utf-8"), end="")


def cmd_render(store, args):
    template = store.load(args.name)
    values = {}
    if args.values_json:
        raw = sys.stdin.read() if args.values_json == "-" else args.values_json
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise QuickPromptError("bad_values_json", EXIT_USAGE, f"--values-json is not valid JSON: {exc}")
        if not isinstance(values, dict):
            raise QuickPromptError("bad_values_json", EXIT_USAGE, "--values-json must be a JSON object")
    print(render(template, values), end="")


def cmd_add(store, args):
    path = store.write(args.name, _read_source(args.file), must_exist=False)
    print(json.dumps({"added": args.name, "path": str(path)}))


def cmd_update(store, args):
    path = store.write(args.name, _read_source(args.file), must_exist=True)
    print(json.dumps({"updated": args.name, "path": str(path)}))


def cmd_remove(store, args):
    path = store.remove(args.name)
    print(json.dumps({"removed": args.name, "path": str(path)}))


def cmd_validate(store, args):
    if args.file:
        meta, body = parse_template(_read_source(args.file))
        template = validate_template(meta, body)
        print(json.dumps({"valid": [template["name"]]}))
        return
    names = [args.name] if args.name else store.names()
    report = {"valid": [], "invalid": {}}
    for name in names:
        try:
            store.load(name)
            report["valid"].append(name)
        except QuickPromptError as exc:
            if exc.exit_code == EXIT_NOT_FOUND:
                raise
            report["invalid"][name] = exc.extra.get("problems", [exc.message])
    print(json.dumps(report, indent=2))
    if report["invalid"]:
        raise QuickPromptError("invalid_template", EXIT_INVALID, f"{len(report['invalid'])} invalid template(s)",
                               invalid=sorted(report["invalid"]))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise QuickPromptError("usage", EXIT_USAGE, message)


def build_parser():
    parser = _Parser(prog="quick_prompt.py", description="Global prompt template library for agents.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    sub.add_parser("index", help="name<TAB>summary per template (matching input)").set_defaults(func=cmd_index)

    p = sub.add_parser("list", help="list templates")
    p.add_argument("--tag")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="print a template")
    p.add_argument("name")
    p.add_argument("--json", action="store_true", help="parsed spec incl. placeholders and body")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("render", help="fill a template and print the prompt")
    p.add_argument("name")
    p.add_argument("--values-json", help="JSON object of placeholder values, or '-' for stdin")
    p.set_defaults(func=cmd_render)

    for command, func, help_text in (("add", cmd_add, "create a template"), ("update", cmd_update, "replace a template")):
        p = sub.add_parser(command, help=help_text)
        p.add_argument("name")
        p.add_argument("--file", required=True, help="template file path, or '-' for stdin")
        p.set_defaults(func=func)

    p = sub.add_parser("remove", help="delete a template")
    p.add_argument("name")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("validate", help="check stored templates, or a file before adding it")
    group = p.add_mutually_exclusive_group()
    group.add_argument("name", nargs="?")
    group.add_argument("--file", help="validate a template file without storing it ('-' for stdin)")
    p.set_defaults(func=cmd_validate)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        args.func(TemplateStore(), args)
        return EXIT_OK
    except QuickPromptError as exc:
        print(exc.to_json(), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
