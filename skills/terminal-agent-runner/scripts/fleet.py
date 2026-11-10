#!/usr/bin/env python3
"""
Fleet driver for terminal-agent-runner: one compact command per herding action.

Exists to remove the three things that dominate a herding session's cost:
  1. JSON envelopes  - `herdr agent read` returns escaped-newline JSON. Every
                       command here prints plain text or one small JSON object.
  2. Round trips     - spawn/prompt/approve each collapse a multi-step dance
                       (tab create -> start -> close stray pane; send -> verify
                       landed -> Enter -> verify submitted) into one call.
  3. Re-derivation   - `classify` applies scripts/risk_policy.json instead of
                       re-reasoning about each prompt from scratch.

Verified against herdr 0.6.10. Addresses agents BY NAME: pane ids are
positional and are recycled when a pane closes.
"""

import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
import itertools
import re
import select
import subprocess
import sys
import time
import uuid
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
POLICY_FILE = SKILL_DIR / "risk_policy.json"
POOL = SKILL_DIR / "resource_pool.py"
STATE_DIR = Path(os.environ.get(
    "AGENT_HERDER_STATE_DIR",
    str(Path.home() / ".config" / ".agents" / "herder"),
))
HERDR_CALL_TIMEOUT = 60.0


def subject_digest(subject: str) -> str:
    """Stable hex digest of a subject string for prompt binding and review authorization."""
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]


def subject_matches_reviewed(live_subject: str, reviewed_subject: str) -> bool:
    """Check if the live subject matches the reviewed subject or its digest."""
    if not reviewed_subject or not live_subject:
        return False
    rev = reviewed_subject.strip()
    if rev == live_subject.strip():
        return True
    full_hash = hashlib.sha256(live_subject.encode("utf-8")).hexdigest()
    short_hash = full_hash[:16]
    if rev.lower() == full_hash.lower() or rev.lower() == short_hash.lower():
        return True
    if len(rev) >= 8 and full_hash.lower().startswith(rev.lower()):
        return True
    return False


# Modal dialog definitions. Each entry defines a dialog category, its matching
# patterns (evaluated against raw, normalized, and dewrapped readings), and its kind.
# Adding a new dialog shape requires only a new entry here.
DIALOG_KINDS = (
    {
        "kind": "trust",
        # Anchored, and matched only against the raw text. A trust dialog renders its
        # header, question and options as whole lines; a worker's report describing this
        # very dialog mentions the same words mid-sentence. Unanchored, any pane
        # discussing trust dialogs read as a trust dialog -- observed live, and it wedged
        # `complete` for the worker that wrote the trust fix, because health stuck at
        # trust-prompt on the strength of its own summary bullet.
        "anchored": True,
        "patterns": (
            r"^[ \t]*Accessing workspace:",
            r"^[ \t]*(?:[\u276f>][ \t]*)?(?:Do you )?trust the contents",
            r"^[ \t]*(?:[\u276f>][ \t]*)?(?:Yes, I )?trust this (?:folder|directory|project)",
        ),
    },
    {
        "kind": "plan",
        # Anchored, and matched only against the raw text. A plan approval dialog
        # renders its header, question and options as whole lines; a worker's report
        # describing this dialog mentions the same phrases mid-sentence. Unanchored,
        # any pane discussing plan approval would detect as a plan dialog.
        "anchored": True,
        "patterns": (
            r"^[ \t]*Ready to code\?\s*$",
            r"^[ \t]*Here is Claude's plan:\s*$",
            r"^[ \t]*(?:Claude has written up a plan and is ready to execute\.\s+|execute\.\s+)?Would you like to proceed\?\s*$",
            r"^[ \t]*(?:[\u276f>›][ \t]*)?(?:1[\.\)]\s+)?Yes, and use auto mode\s*$",
            r"^[ \t]*(?:[\u276f>›][ \t]*)?(?:2[\.\)]\s+)?Yes, manually approve edits\s*$",
            r"^[ \t]*(?:[\u276f>›][ \t]*)?(?:3[\.\)]\s+)?Tell Claude what to change\s*$",
            r"^[ \t]*(?:[\u276f>›][ \t]*)?(?:1[\.\)]\s+)?Write full plan to the plan-mode file only(?:\s*\(Recommended\))?\s*$",
            r"^[ \t]*(?:[\u276f>›][ \t]*)?(?:2[\.\)]\s+)?Treat this as a fleet task\b",
            r"^[ \t]*How would you like to handle this plan\?\s*$",
        ),
    },
    {
        "kind": "file",
        "patterns": (
            r"Allow (?:creation|editing|modification|deletion|writing|reading|access)(?: of this file|\b)",
            r"Allow (?:file )?access",
            r"File access(?: request)?",
            r"Permission to (?:read|access|write|create|edit|modify|delete) (?:this )?file",
            r"Access requested for (?:this )?file",
            r"Accept this file edit",
            r"Create file",
            r"Edit file",
            r"Write file",
            r"Read file",
            r"Pending edit",
        ),
    },
    {
        "kind": "survey",
        "patterns": (
            r"Help improve our CLI!\s+Take a (?:quick )?survey\?",
            r"Feedback survey:",
            r"Rate your experience",
            r"How satisfied are you with the tool\?",
            r"\bTake a (?:quick )?survey\?",
        ),
    },
    {
        "kind": "privacy",
        "patterns": (
            r"Privacy Notice",
            r"Terms of Service",
            r"Data collection notice",
            r"Before using (?:Gemini|Claude|Codex|OMP) CLI",
            r"Do you agree to the terms",
        ),
    },
    {
        "kind": "usage-limit",
        "patterns": (
            r"Individual quota reached",
            r"Usage limit reached",
            r"Rate limit exceeded",
            r"Resets in \d+",
        ),
    },
    {
        "kind": "command",
        "patterns": (
            r"Requesting permission for:",
            r"Do you want to proceed\?",
            r"requires approval",
            r"\[y/N\]",
            r"\(y/n\)",
            r"Bash command",
            r"Tool use",
        ),
    },
    {
        "kind": "question",
        "patterns": (
            r"Please specify",
            r"Select an option",
            r"Which file",
            r"How would you like",
        ),
    },
)


def _detect_dialog_kind(text: str) -> str | None:
    readings = (text, re.sub(r"\s+", " ", text), re.sub(r"\n[ \t]*", "", text))
    for entry in DIALOG_KINDS:
        # An anchored entry is checked against the raw text only. The other two readings
        # join lines together, which would put an anchored pattern back in the middle of
        # a line and defeat the anchor.
        cand = (text,) if entry.get("anchored") else readings
        flags = re.IGNORECASE | (re.MULTILINE if entry.get("anchored") else 0)
        for pat in entry["patterns"]:
            if any(re.search(pat, r, flags) for r in cand):
                return entry["kind"]
    return None


CLI_IDS = ("codex", "claude", "agy", "omp")
APPROVAL_MODES = ("auto", "edit", "manual", "plan", "default")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FORBIDDEN_CLI_FLAGS = re.compile(
    r"(?:^|\s)--?(?:dangerously-|dangerous-|bypass|yolo|full-auto|approval|approve|"
    r"permission|sandbox|trust|allow)(?:[-_=\s]|$)", re.IGNORECASE,
)


class StateError(RuntimeError):
    """Persistent fleet state is unavailable or corrupt; never continue empty."""


def cli_id(value: str) -> str:
    """Accept one supported executable identifier, never an argv fragment."""
    if not isinstance(value, str) or value not in CLI_IDS:
        detail = ""
        if isinstance(value, str) and (FORBIDDEN_CLI_FLAGS.search(value) or any(c.isspace() for c in value)):
            detail = "; embedded permission/bypass flags are forbidden"
        raise argparse.ArgumentTypeError(
            f"CLI must be one of {', '.join(CLI_IDS)}{detail}"
        )
    return value


def identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"{label} must match {IDENTIFIER_RE.pattern}"
        )
    return value


# Herdr 0.8.0 rejects anything else at `agent start` (invalid_agent_name). Validating here
# turns a mid-spawn failure -- after the tab exists and leases are held -- into an argument
# error before anything is created.
AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def agent_id(value: str) -> str:
    if not isinstance(value, str) or not AGENT_NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "agent name must start with a lowercase letter and contain only lowercase "
            "letters, digits, '-' or '_' (1-32 characters)"
        )
    return value


def session_id(value: str) -> str:
    return identifier(value, "session name")


def approval_choice(value: str) -> str:
    if not re.fullmatch(r"[1-9]", value or ""):
        raise argparse.ArgumentTypeError("approval choice must be one digit from 1 to 9")
    return value


def scoped_id(value: str, label: str = "scope") -> str:
    if (not isinstance(value, str) or not value or len(value) > 512
            or value.startswith("-") or any(ord(c) < 32 for c in value)):
        raise argparse.ArgumentTypeError(
            f"{label} must be non-empty, at most 512 characters, contain no controls, "
            "and not begin with '-'"
        )
    return value


MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/() +-]{0,127}$")
EMBEDDED_FLAG_RE = re.compile(r"(?:^|\s)-")
FORBIDDEN_MODEL_FLAGS = re.compile(
    r"(?:^|\s)--?(?:dangerously|dangerous|bypass|yolo|full-auto|approval|approve|"
    r"permission|sandbox|trust|allow)", re.IGNORECASE,
)
_AGY_MODELS_CACHE: dict[str, str] | None = None


def validate_model_identifier(value: str) -> str:
    """Validate that value is a plausible model identifier, never an option or shell injection."""
    if not isinstance(value, str) or not value.strip():
        raise argparse.ArgumentTypeError("model must be a non-empty string")
    if EMBEDDED_FLAG_RE.search(value) or FORBIDDEN_MODEL_FLAGS.search(value) or FORBIDDEN_CLI_FLAGS.search(value):
        raise argparse.ArgumentTypeError(
            f"invalid model {value!r}: option-like flags and embedded permission/bypass flags are forbidden"
        )
    if not MODEL_NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"invalid model {value!r}: not a plausible model identifier (must match {MODEL_NAME_RE.pattern})"
        )
    if any(c in ";|&><$`\\\"'\n\r\t\0" or ord(c) < 32 for c in value):
        raise argparse.ArgumentTypeError(
            f"invalid model {value!r}: contains forbidden shell or control characters"
        )
    return value


def get_agy_models(cache_file: Path | None = None) -> dict[str, str]:
    """Retrieve and cache the AGY model enumeration (id -> id and display name -> id).

    Uses an in-memory cache and a state-dir cache file to avoid waiting on subprocess
    execution on every spawn.
    Refuses with RuntimeError if the enumeration cannot be obtained.
    """
    global _AGY_MODELS_CACHE
    if _AGY_MODELS_CACHE is not None:
        return _AGY_MODELS_CACHE

    target_cache_file = cache_file or (STATE_DIR / "agy_models_cache.json")
    if target_cache_file.exists():
        try:
            data = json.loads(target_cache_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                _AGY_MODELS_CACHE = data
                return _AGY_MODELS_CACHE
        except Exception:
            pass

    try:
        r = subprocess.run(["agy", "models"], capture_output=True, text=True, timeout=10.0)
    except Exception as e:
        raise RuntimeError(f"cannot obtain AGY models enumeration from 'agy models': {e}") from e

    if r.returncode != 0:
        err = r.stderr.strip() or r.stdout.strip()
        raise RuntimeError(f"'agy models' exited with code {r.returncode}: {err}")

    output = r.stdout.strip()
    if not output:
        raise RuntimeError("'agy models' returned empty output")

    mapping: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        model_id_val = parts[0].strip()
        display_name_val = parts[1].strip() if len(parts) > 1 else model_id_val
        if model_id_val:
            mapping[model_id_val] = model_id_val
        if display_name_val:
            mapping[display_name_val] = model_id_val

    if not mapping:
        raise RuntimeError("'agy models' output could not be parsed into model entries")

    try:
        target_cache_file.parent.mkdir(parents=True, exist_ok=True)
        target_cache_file.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    except Exception:
        pass

    _AGY_MODELS_CACHE = mapping
    return _AGY_MODELS_CACHE


def resolve_agy_model(model: str, models_map: dict[str, str] | None = None) -> str:
    """Validate and resolve an AGY model name or ID against the enumeration.

    Accepts both canonical model IDs and display names, resolving to the canonical
    model ID that the CLI option expects.
    Refuses if the model is not listed in the enumeration, naming the value.
    """
    if models_map is None:
        try:
            models_map = get_agy_models()
        except Exception as e:
            raise SystemExit(f"cannot validate AGY model {model!r}: {e}") from e

    if model in models_map:
        return models_map[model]

    raise SystemExit(
        f"AGY model {model!r} is not listed in 'agy models' enumeration"
    )


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return parsed

# Conditions that make an agent unusable no matter what you send it. A
# print-mode auth probe can pass while the interactive tier is quota-blocked,
# so the pane itself is the authority on whether an agent can actually work.
ERROR_BANNERS = [
    (r"^[ \t]*(?:(?:[❯>❌⚠️✖\-\*!]|error:?|warning:?|api error:?)[ \t]+)?(?:you(?:'ve| have)? (?:reached|hit) (?:your )?(?:quota|usage|rate) limit|(?:monthly |daily |api |user |account )?(?:usage |rate )?quota (?:limit )?(?:reached|exceeded)|(?:monthly |daily |api |user |account )?usage limit (?:reached|exceeded)|(?:monthly |daily |api |user |account )?rate limit (?:reached|exceeded)|rate_limit_exceeded|upgrade (?:to (?:claude )?pro|(?:your )?subscription)|(?:api |http )?429\b|(?:anthropic|openai|gemini) quota exceeded|too many requests|rate_limit_error)\b", "quota"),
    (r"^[ \t]*(?:[❯>❌⚠️✖\-\*!]|error:?|warning:?)?[ \t]*(?:welcome to the antigravity cli\.\s+you are currently not logged in|you are currently not (?:logged in|authenticated)|not (?:logged in|authenticated)|please (?:log ?in|sign ?in)|unauthorized:?|invalid (?:api )?key|error getting token source|select login method)", "auth"),
    # Anchored for the same reason as the trust dialog kind: see DIALOG_KINDS.
    (r"^[ \t]*(?:[\u276f>][ \t]*)?(?:Do you )?trust the contents|"
     r"^[ \t]*(?:[\u276f>][ \t]*)?(?:Yes, I )?trust this (?:folder|directory|project)", "trust-prompt"),
    (r"^[ \t]*(?:[❯>❌⚠️✖\-\*!]|error:?|warning:?)?[ \t]*(?:context (?:window )?(?:is )?full|maximum context length (?:reached|exceeded)|too many tokens for context window)", "context-full"),
]


def health_of(text: str) -> str | None:
    if not text:
        return None
    clean = text
    if HANDOFF_MARKER in clean:
        idx = clean.find(HANDOFF_MARKER)
        clean = clean[:idx]
    for pat, label in ERROR_BANNERS:
        if re.search(pat, clean, re.IGNORECASE | re.MULTILINE):
            return label
    return None


def _is_startup_banner_only(text: str) -> bool:
    """Return True if text contains only CLI startup banners and an empty prompt."""
    if not text or not text.strip():
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True
    banner_patterns = (
        # CLI launch command and shell prompt echo
        r"^(?:agy|codex|claude|omp)\s+--",
        r"^.*(?:%|\$)\s+(?:agy|codex|claude|omp)\b",
        r"^(?:ts|edits|mode|plan|medium|high|low|auto|manual|danger-full-access|workspace-write)\b",

        # CLI brand headers, ASCII logo, email, model, directory lines
        r"^[─━═\-_= \t╌┄┈╭╮╯╰│┃┆┇┊┋┌┐└┘├┤┬┴┼▄▀█▌▐░▒▓▛▜▝▘\s]*(?:AGY|Antigravity|Claude Code|Codex|OpenAI Codex|omp|Oh My Prompt)\b.*",
        r"^[─━═\-_= \t╌┄┈╭╮╯╰│┃┆┇┊┋┌┐└┘├┤┬┴┼▄▀█▌▐░▒▓▛▜▝▘\s]+$",
        r"^[─━═\-_= \t╌┄┈╭╮╯╰│┃┆┇┊┋┌┐└┘├┤┬┴┼▄▀█▌▐░▒▓▛▜▝▘\s]*\S+@\S+\.\S+.*",
        r"^[─━═\-_= \t╌┄┈╭╮╯╰│┃┆┇┊┋┌┐└┘├┤┬┴┼▄▀█▌▐░▒▓▛▜▝▘\s]*(?:Gemini|Claude|GPT|Codex|OpenAI|Anthropic|Opus|Sonnet|Haiku)\b.*",
        r"^[─━═\-_= \t╌┄┈╭╮╯╰│┃┆┇┊┋┌┐└┘├┤┬┴┼▄▀█▌▐░▒▓▛▜▝▘\s]*(?:~|/|[A-Za-z]:)[/\w\.\-]*[─━═\-_= \t╌┄┈╭╮╯╰│┃┆┇┊┋┌┐└┘├┤┬┴┼▄▀█▌▐░▒▓▛▜▝▘\s]*$",
        r"^[│┃\s]*Welcome\b.*",
        r"^[│┃\s]*Claude Team[│┃\s]*$",
        r"^[│┃\s]*$",
        r"^Welcome\b",
        r"^[└├•\-\*\s]*(?:Tips?|Model|Directory|Workspace|Project|Session|Tokens|Mode|Version|Logged in as|Auth|User|Status)\b:?",

        # Box-drawing dividers and borders
        r"^[─━═\-_= \t╌┄┈╭╮╯╰│┃┆┇┊┋┌┐└┘├┤┬┴┼]+$",

        # Bare prompt symbols and placeholder input prompts
        r"^[❯>$#•\-\*]\s*$",
        r"^[❯>$#•\-\*]\s*(?:(?:Accept-edits|Plan|Planning|Default|Auto-approved)\s+mode|Auto-approved|Type|Press|Ask|Enter|\(shift\+tab|\? for|Try\b)\b",
        r"^\(empty pane\)$",

        # Shortcuts and footers
        r"^(?:\?|\b(?:help|Press|Type|Use|esc)\b).*",
        r"^Ready\b",
        r"^https?://\S+$",
        r"^(?:Shift\+Tab|ctrl\+[a-z]|esc)\b",
        r"^.*(?:accept-edits|bypass|auto|plan|gemini|claude|gpt|codex|sonnet|opus|haiku)\s+·\s+.*",
        r"^(?:Update available|New version available)\b.*",
        r"^[⚠ℹ💡\s]*(?:Transcript saving|Notice|Warning|Note)\b.*",
        r"^[⏵▶►\s]*(?:auto|plan|accept-edits|manual)\s+mode\b.*",
    )
    for ln in lines:
        if not any(re.search(pat, ln, re.IGNORECASE) for pat in banner_patterns):
            return False
    return True


DEFAULT_SETTLE_SAMPLES = int(os.environ.get(
    "AGENT_HERDER_SETTLE_SAMPLES",
    os.environ.get("FLEET_SETTLE_SAMPLES", 2),
))
DEFAULT_SETTLE_DELAY = float(os.environ.get(
    "AGENT_HERDER_SETTLE_DELAY",
    os.environ.get("FLEET_SETTLE_DELAY", 0.3),
))


SPINNER_CHARS = set("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷◐◓◑◒◴◵◶◷◜◝◞◟")

FOREGROUND_PATTERNS = (
    r"^[ \t]*(?:Thinking|Analyzing|Running|Executing|Generating|Building|Compiling|Searching|Processing|Working|Testing)(?!(?:[-_]tree|\s+tree|\s+director|\s+cop))\b",
    r"(?:^|\s)(?:Thinking|Analyzing|Running|Executing|Generating|Building|Compiling|Searching|Processing|Working|Testing)\.{3,}",
    r"^[ \t]*[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\-\|/]\s*(?:Thinking|Analyzing|Running|Executing|Working|Generating|Building|Compiling|Searching|Processing|Testing|\w+ing\b)",
    r"^[ \t]*[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷◐◓◑◒◴◵◶◷◜◝◞◟]",
    r"\[running(?:\s+[^\]]+)?\]|\(running(?:\s+[^\)]+)?\)",
    r"\[in progress\]|\(in progress\)",
    r"(?:esc|ctrl\+c)\s+to\s+(?:interrupt|cancel|stop|abort)|press\s+(?:esc|ctrl\+c)\s+to\s+(?:cancel|interrupt|stop)|cancel with esc",
)


def has_foreground_execution(text: str) -> bool:
    """Check if pane text contains active foreground execution indicators (spinners, running markers)."""
    if not isinstance(text, str) or not text or not text.strip() or text.strip() == "(empty pane)":
        return False
    if bool(_detect_dialog_kind(text)):
        return False
    if any(c in SPINNER_CHARS for c in text):
        return True
    for pat in FOREGROUND_PATTERNS:
        if re.search(pat, text, re.IGNORECASE | re.MULTILINE):
            return True
    return False


def is_settled_frame(text: str, allow_startup_banner: bool = False) -> bool:
    """Check if a scraped frame is complete and settled (not empty, whitespace, spinner, or mid-render)."""
    if not isinstance(text, str) or not text or not text.strip() or text.strip() == "(empty pane)":
        return False
    if has_foreground_execution(text):
        return False
    if not allow_startup_banner and _is_startup_banner_only(text):
        return False
    return True


def stable_trust_decision(name, session, expected_cwd, attempts: int = 8,
                          reader=None) -> dict:
    """Decide a trust dialog on a settled reading of the pane, not a single snapshot.

    A pane read taken while the CLI is still painting yields a truncated path, and a
    truncated path is indistinguishable from a genuine mismatch: both just fail to equal
    the validated cwd. That is how a worker was refused during a passing simulation run
    with `trust dialog path '/private/var/folders/fg/vw_k` -- 28 characters, which is not
    a wrap boundary in that 54-column pane but is exactly what a half-drawn line looks
    like. The pane had the right path; the read was early.

    An approving reading is decisive immediately, since a partial path cannot resolve to
    the expected one. A refusing reading has to be confirmed: the same path must come back
    twice in a row before the spawn is failed. A still-painting pane changes between
    reads; a real mismatch does not.
    """
    read = reader or (lambda: read_text(name, session, lines=45, source="recent-unwrapped"))
    last_path = None
    last = None
    for attempt in range(attempts):
        try:
            txt = read()
        except (Exception, SystemExit):
            time.sleep(0.25)
            continue
        dec = decide_trust_dialog(txt, expected_cwd)
        dec["text"] = txt
        if dec["action"] == "approve":
            return dec
        if last is not None and dec["extracted_path"] is not None and dec["extracted_path"] == last_path:
            return dec
        last, last_path = dec, dec["extracted_path"]
        time.sleep(0.25)
    if last is not None:
        last["why"] = (f"{last['why']}; the reading never settled over {attempts} reads, "
                       "so the pane may still have been painting")
        return last
    return {"action": "refuse", "extracted_path": None,
            "expected_path": str(expected_cwd) if expected_cwd else None,
            "text": "",
            "why": f"the pane could not be read over {attempts} attempts; give me full path and request permission again"}


def _wait_dialog_cleared(name, session=None, retries: int = 50, reader=None) -> bool:
    """Wait until a modal dialog is no longer visible in the pane.

    Observes the actual pane state rather than depending on a fixed sleep, surviving
    varying TUI transition durations (e.g. Claude Code post-trust redraw).
    """
    read = reader or (lambda: read_text(name, session, lines=45))
    for _ in range(retries):
        try:
            txt = read()
            if not pending_prompt(txt) and health_of(txt) != "trust-prompt":
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def pre_submit_check(name: str, session: str | None, expected_cwd: str | Path | None,
                     policy: dict | None = None, task: str | None = None,
                     read_only: bool = False,
                     settle_samples: int = DEFAULT_SETTLE_SAMPLES,
                     settle_delay: float = 0.5,
                     reader=None, attempts: int = 120) -> bool:
    """Verify the agent pane is ready and handle/clear any modal dialogs before prompt submission.

    Repeatedly samples the pane across the settle window to distinguish a slow-painting modal
    dialog from a CLI that opens no dialog. If a modal dialog appears, it is decided and cleared
    before submission. If no dialog appears and the pane settles cleanly, the gate passes.
    If the pane genuinely never becomes ready, fails and names what it was waiting for.
    """
    if policy is None:
        policy = load_policy()

    def default_reader():
        try:
            return read_text(name, session, lines=45, source="recent-unwrapped")
        except (Exception, SystemExit):
            return read_text(name, session, lines=45)

    read = reader or default_reader
    consecutive_no_dialog = 0

    for attempt in range(attempts):
        try:
            txt = read()
        except (Exception, SystemExit) as e:
            if attempt < attempts - 1:
                time.sleep(settle_delay)
                continue
            raise RuntimeError(
                f"agent {name} pane could not be read over {attempts} attempts "
                f"(waiting for interactive readiness): {e}"
            ) from e

        if not is_settled_frame(txt, allow_startup_banner=True):
            if attempt < attempts - 1:
                time.sleep(settle_delay)
                continue
            raise RuntimeError(
                f"agent {name} pane never became ready over {attempts} attempts "
                f"(waiting for interactive prompt)"
            )

        health = health_of(txt)
        if health in ("quota", "auth", "context-full"):
            raise RuntimeError(
                f"agent {name} opened in unhealthy state: {health} "
                f"(waiting for healthy interactive session)"
            )

        pend = pending_prompt(txt)
        is_trust = (pend and pend.get("kind") == "trust") or health == "trust-prompt" or _detect_dialog_kind(txt) == "trust"

        if is_trust:
            dec = stable_trust_decision(name, session, expected_cwd, attempts=attempts, reader=read)
            txt_dec = dec.get("text") or txt
            pend_dec = pending_prompt(txt_dec) or pend or {"kind": "trust", "options": []}
            if dec["action"] == "approve":
                keys = dialog_accept_keys(pend_dec, txt_dec)
                herdr(["agent", "send-keys", name, *keys], session, check=True)
                _wait_dialog_cleared(name, session, reader=read)
                return True
            else:
                raise RuntimeError(
                    f"agent {name} opened with a modal dialog (trust) "
                    f"that policy will not auto-approve: {dec['why']}"
                )
        elif pend:
            cls = classify_text(pend["subject"], policy, task, read_only=read_only, kind=pend.get("kind"))
            if cls["action"] == "approve":
                keys = dialog_accept_keys(pend, txt)
                herdr(["agent", "send-keys", name, *keys], session, check=True)
                _wait_dialog_cleared(name, session, reader=read)
                return True
            else:
                raise RuntimeError(
                    f"agent {name} opened with a modal dialog ({pend['kind']}) "
                    f"that policy will not auto-approve: {cls['why']}"
                )
        else:
            consecutive_no_dialog += 1
            if consecutive_no_dialog >= settle_samples:
                return True
            if attempt < attempts - 1:
                time.sleep(settle_delay)

    raise RuntimeError(
        f"agent {name} pane never became ready over {attempts} attempts "
        f"(waiting for interactive prompt)"
    )


# ---------------- herdr plumbing ----------------

def socket_for(session: str | None) -> str | None:
    if session == "default":
        return str(Path.home() / ".config" / "herdr" / "herdr.sock")
    if session:
        return str(Path.home() / ".config" / "herdr" / "sessions" / session / "herdr.sock")
    return os.environ.get("HERDR_SOCKET_PATH")


def session_key(session: str | None) -> str:
    """Stable state namespace for the targeted Herdr session."""
    if session:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", session)
    sock = socket_for(None)
    if sock:
        p = Path(sock)
        if p.parent.parent.name == "sessions":
            return re.sub(r"[^A-Za-z0-9_.-]", "_", p.parent.name)
    return "default"


def validate_session_affinity(session: str | None, allow_cross_session: bool = False):
    """Refuse the most expensive Herdr mistake: driving another workspace."""
    current = os.environ.get("HERDR_SOCKET_PATH")
    target = socket_for(session)
    if current and target and Path(current).resolve() != Path(target).resolve() and not allow_cross_session:
        raise SystemExit(
            "refusing cross-session fleet operation: the herder is attached to "
            f"{current}, but --session targets {target}. Omit --session to use the "
            "current Herdr workspace, or pass --allow-cross-session intentionally."
        )


def validate_cwd(value: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"working directory does not exist or is not a directory: {path}")
    return str(path)


def current_herder_workspace(session: str | None = None) -> str:
    """Return the workspace containing the lead/herding pane.

    Workspace identity belongs to Herdr, not to a filesystem path. Prefer the
    pane identity exported by Herdr and use its ``workspace_id``; the active
    workspace environment variable is a fallback for clients that do not expose
    the pane in ``pane list``.
    """
    pane_id = os.environ.get("HERDR_PANE_ID")
    if pane_id:
        data = herdr(["pane", "list"], session, check=True)
        panes = data.get("result", {}).get("panes") if isinstance(data, dict) else None
        if not isinstance(panes, list):
            raise RuntimeError("herdr pane list returned an invalid schema")
        pane = next((p for p in panes
                     if isinstance(p, dict) and p.get("pane_id") == pane_id), None)
        if pane and isinstance(pane.get("workspace_id"), str) and pane["workspace_id"]:
            return pane["workspace_id"]
        raise SystemExit(
            f"herding pane {pane_id!r} is not present in the targeted Herdr session; "
            "refusing to infer a workspace from the task folder. Pass --workspace-id to target a specific workspace."
        )

    workspace_id = os.environ.get("HERDR_ACTIVE_WORKSPACE_ID")
    if workspace_id:
        return workspace_id
    raise SystemExit(
        "cannot determine the herding agent's Herdr workspace; run fleet.py from its "
        "Herdr pane so HERDR_PANE_ID is available, or pass --workspace-id to target an explicit workspace"
    )


def herdr(args: list, session: str | None = None, check: bool = False, timeout: float | None = None) -> dict | str:
    env = dict(os.environ)
    sock = socket_for(session)
    if sock:
        env["HERDR_SOCKET_PATH"] = sock
    elif session == "default":
        env.pop("HERDR_SOCKET_PATH", None)
    call_timeout = timeout or HERDR_CALL_TIMEOUT
    if "--timeout" in args:
        try:
            val = float(args[args.index("--timeout") + 1])
            sec = val / 1000.0 if val >= 1000 else val
            call_timeout = max(call_timeout, sec + 15.0)
        except (ValueError, IndexError):
            pass
    try:
        r = subprocess.run(["herdr", *args], capture_output=True, text=True, env=env,
                           timeout=call_timeout)
    except subprocess.TimeoutExpired as e:
        raise SystemExit(f"herdr {' '.join(args)} timed out after {call_timeout:g}s") from e
    out = (r.stdout or "").strip()
    try:
        data = json.loads(out)
    except Exception:
        if check and r.returncode != 0:
            raise SystemExit(f"herdr {' '.join(args)} failed: {r.stderr.strip() or out}")
        return out
    if check and (r.returncode != 0 or "error" in data):
        raise SystemExit(
            f"herdr {' '.join(args)} failed ({r.returncode}): "
            f"{data.get('error') or r.stderr.strip() or out}"
        )
    return data


def agents(session=None) -> list:
    d = herdr(["agent", "list"], session, check=True)
    result = d.get("result", {}).get("agents") if isinstance(d, dict) else None
    if not isinstance(result, list):
        raise SystemExit("herdr agent list returned an invalid schema")
    names = [a.get("name") for a in result if a.get("name")]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise SystemExit(f"Herdr returned duplicate agent names: {', '.join(duplicates)}")
    return result


def find_agent(name, session=None) -> dict | None:
    return next((a for a in agents(session) if a.get("name") == name), None)


def find_agent_other_session(name: str, current_session: str | None = None) -> str | None:
    """Check if an agent exists in another Herdr session on disk."""
    base = STATE_DIR
    if not base.is_dir():
        return None
    curr = current_session or "default"
    for sess_dir in base.iterdir():
        if sess_dir.is_dir() and sess_dir.name != curr:
            tasks_file = sess_dir / "tasks.json"
            if tasks_file.is_file():
                try:
                    tasks = _load_json_unlocked(tasks_file, {})
                    if name in tasks:
                        return sess_dir.name
                except Exception:
                    pass
    return None


def wait_interactive(name, session=None, timeout: float = 60.0) -> bool:
    """Wait until Herdr reports the agent as ready for input.

    Note what this does NOT guarantee: `interactive_ready` flips true within a few seconds
    of `agent start`, well before a TUI like Claude has finished drawing, so it is not a
    sufficient gate for submitting a prompt. `_submit` handles delivery; use this only
    when you want to know Herdr has detected the agent.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        agent = find_agent(name, session)
        if agent and agent.get("interactive_ready"):
            return True
        time.sleep(0.5)
    return False


def start_agent_when_ready(start_cmd: list[str], session=None, attempts: int = 10,
                           delay: float = 0.5) -> None:
    """Start an agent after a tab becomes accepting, without replacing that tab.

    Herdr can acknowledge ``tab create`` before the root pane is ready to host an
    agent. An ``agent_not_ready`` response can still reserve the agent name, so
    reconcile that partial start instead of sending another start for the same name.
    """
    name = start_cmd[2] if len(start_cmd) > 2 else "<unknown>"
    pane_id = start_cmd[start_cmd.index("--pane") + 1] if "--pane" in start_cmd else None

    def pane_context() -> str:
        try:
            data = herdr(["pane", "list"], session, check=True)
            panes = data.get("result", {}).get("panes", []) if isinstance(data, dict) else []
            pane = next((p for p in panes if p.get("pane_id") == pane_id), None)
            if pane:
                fields = {key: pane.get(key) for key in ("pane_id", "terminal_id", "status")
                          if pane.get(key) is not None}
                return json.dumps(fields, sort_keys=True)
        except BaseException as exc:
            return f"unavailable ({exc})"
        return f"pane_id={pane_id!r} not listed"

    for attempt in range(attempts):
        try:
            herdr(start_cmd, session, check=True)
            return
        except SystemExit as exc:
            failure = str(exc)
            if "agent_not_ready" in failure:
                for _ in range(attempts):
                    if find_agent(name, session):
                        return
                    time.sleep(delay)
                raise SystemExit(
                    f"{failure}; partial start did not register agent {name!r}; "
                    f"pane context: {pane_context()}"
                ) from exc
            if "timeout" in failure or "timed out" in failure:
                for _ in range(attempts):
                    if find_agent(name, session):
                        return
                    time.sleep(delay)
                if attempt == attempts - 1:
                    raise SystemExit(
                        f"{failure}; start timed out and did not register agent {name!r}; "
                        f"pane context: {pane_context()}"
                    ) from exc
                continue
            if "agent_pane_busy" not in failure or find_agent(name, session) or attempt == attempts - 1:
                if find_agent(name, session):
                    return
                raise
            time.sleep(delay)


def pane_of(name, session=None) -> str:
    a = find_agent(name, session)
    if not a:
        raise SystemExit(
            f"agent '{name}' does not exist (completion ends an agent; "
            "follow-up work needs a new spawn)"
        )
    return a["pane_id"]


ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])"
)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_pane_text(text: str) -> str:
    """Strip ANSI/CSI escapes and C0 control characters (preserving \n and \t)."""
    if not isinstance(text, str) or not text:
        return text or ""
    # Strip ANSI escape sequences
    text = ANSI_ESCAPE_RE.sub("", text)
    # Normalize carriage returns
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip unprintable C0 control characters and DEL
    text = CONTROL_CHAR_RE.sub("", text)
    return text


def read_text(name, session=None, lines=40, source="visible") -> str:
    d = herdr(["agent", "read", name, "--source", source, "--lines", str(lines)],
              session, check=False)
    if isinstance(d, dict) and "error" in d:
        err = d.get("error")
        err_code = err.get("code") if isinstance(err, dict) else err
        if err_code == "agent_not_found" or "not found" in str(err):
            return ""
        raise SystemExit(f"herdr agent read {name} failed: {err}")
    raw = None
    if isinstance(d, str):
        raw = d
    elif isinstance(d, dict):
        text = d.get("result", {}).get("read", {}).get("text")
        if isinstance(text, str):
            raw = text
    if raw is None:
        raise SystemExit(f"herdr agent read returned an invalid schema for {name}")
    return sanitize_pane_text(raw)


# ---------------- prompt parsing ----------------

def _is_rule_line(line: str) -> bool:
    s = line.strip()
    return len(s) >= 2 and not s.strip("─━═-_= \t╌┄┈")


# Only the generic "shall I go ahead" phrasings. A dialog whose question IS the subject --
# "Do you trust the contents of this project?" -- must keep it, or trust prompts lose their text.
CONFIRMATION_QUESTION = re.compile(
    r"^\s*(?:Do you want to proceed\??|.*\brequires approval\b.*)\s*$", re.IGNORECASE)


def _drop_confirmation_question(subject: str) -> str:
    return "\n".join(l for l in subject.splitlines()
                     if not CONFIRMATION_QUESTION.match(l))


# A dialog's options are consecutive lines. Scanning back from the end and stopping at the
# first gap wider than one line isolates the live dialog from any numbered prose above it.
MAX_OPTION_GAP = 1


def _last_option_group_start(opt_indices: list) -> int:
    start = opt_indices[-1]
    for prev, cur in zip(reversed(opt_indices[:-1]), reversed(opt_indices[1:])):
        if cur - prev > MAX_OPTION_GAP + 1:
            break
        start = prev
    return start


def pending_prompt(text: str) -> dict | None:
    """Extract the pending approval/question block from a TUI buffer."""
    kind = _detect_dialog_kind(text)
    if not kind:
        return None
    lines = text.splitlines()

    # Options look like "❯ 1. Yes" / "› 1. Yes" / " 2. ..." / "[1] Good"
    # or prose options: "> Yes, I trust this folder" / "  No, exit"
    opts = []
    opt_indices = []
    for i, ln in enumerate(lines):
        inline_brackets = re.findall(r"\[(\d+)\]\s+([A-Za-z]+)", ln)
        if len(inline_brackets) >= 2:
            for k, lbl in inline_brackets:
                opts.append({"key": k, "label": lbl.strip(), "selected": False})
                opt_indices.append(i)
            continue
        m = re.match(r"\s*[❯>›]?\s*(\d+)[\.\)]\s+(.*\S)", ln) or \
            re.match(r"\s*\[(\d+)\]\s+(.*\S)", ln)
        if m:
            opts.append({"key": m.group(1), "label": m.group(2).strip(),
                         "selected": bool(re.match(r"\s*[❯>›]", ln))})
            opt_indices.append(i)

    if not opts:
        for i, ln in enumerate(lines):
            m = re.match(r"^\s*([❯>›])\s+([A-Za-z].*)$", ln)
            if m:
                label = m.group(2).strip()
                if not any(label.lower().startswith(p) for p in ("do you want to proceed", "requesting permission")):
                    # A sibling option sits at roughly the selected option's indentation.
                    # AGY's status bar is right-aligned with a hundred-odd leading spaces
                    # and was otherwise collected as an option: observed live, it became
                    # the only option in the live group, which moved the anchor past the
                    # trust header and lost the workspace path from the subject.
                    anchor_indent = len(ln) - len(ln.lstrip())
                    opts.append({"key": None, "label": label, "selected": True})
                    opt_indices.append(i)
                    for j in range(i + 1, min(i + 5, len(lines))):
                        sub = lines[j]
                        if not sub.strip() or _is_rule_line(sub):
                            continue
                        # Keyboard-hint lines end the option group. They may open with a
                        # glyph, so strip leading non-letters before testing.
                        hint = re.sub(r"^[^A-Za-z]+", "", sub.strip())
                        if re.search(r"^(?:Navigate|enter\b|Ctrl|Esc|tab\b)", hint, re.IGNORECASE) or \
                           re.search(r"^(?:❯|>|›|\d+[\.\)])", sub.strip()):
                            break
                        if len(sub) - len(sub.lstrip()) > anchor_indent + 4:
                            break
                        m_sub = re.match(r"^\s{2,}([A-Za-z].*)$", sub)
                        if m_sub:
                            sub_label = m_sub.group(1).strip()
                            if not re.search(r"\bConfirm\b", sub_label, re.IGNORECASE):
                                opts.append({"key": None, "label": sub_label, "selected": False})
                                opt_indices.append(j)
                    break

    # The LAST option group is the live dialog; everything above it is history --
    # including the herder's own prompt echoed into the pane. A herder brief containing
    # a numbered list ("1. Do this") matches the option regex, so anchoring on the FIRST
    # match pointed the subject at the echoed brief. Observed live: the real command was
    # `omp --help`, but the extracted subject was a paragraph of the herder's own prose,
    # and a backtick in it tripped the shell-composition deny rule. Same principle as
    # parse_handoff: only the last marker is authoritative.
    first_opt = _last_option_group_start(opt_indices) if opt_indices else len(lines)

    # Options must come from the live group only. Numbered prose above it (a herder brief's
    # "1. Do this") would otherwise land in the option list, producing duplicate keys and
    # letting `approve --choice N` select the wrong entry.
    opts = [o for o, i in zip(opts, opt_indices) if i >= first_opt]
    opt_indices = [i for i in opt_indices if i >= first_opt]

    # Subject = the block describing the prompt/command above the options.
    subject = ""
    # 1. Forward headers where subject follows the header (e.g. Requesting permission for, Do you trust, Allow creation, Accept this file edit)
    # Last header before the live option group, for the same reason.
    header_idx = None
    if kind == "trust":
        header_idx = next((i for i in range(first_opt - 1, -1, -1)
                           if re.search(r"Accessing workspace:", lines[i], re.IGNORECASE)), None)
        if header_idx is None:
            header_idx = next((i for i in range(first_opt - 1, -1, -1)
                               if re.search(r"^[ \t│─❯>]*You are in\b", lines[i], re.IGNORECASE)), None)
        if header_idx is None:
            header_idx = next((i for i in range(first_opt - 1, -1, -1)
                               if re.search(r"Do you trust|trust this (?:folder|directory|project)", lines[i], re.IGNORECASE)), None)
    elif kind == "plan":
        header_idx = next((i for i in range(first_opt - 1, -1, -1)
                           if re.search(r"Ready to code\?", lines[i], re.IGNORECASE)), None)
        if header_idx is None:
            header_idx = next((i for i in range(first_opt - 1, -1, -1)
                               if re.search(r"Here is Claude's plan:|Claude has written up a plan|Write full plan to the plan-mode file only|Treat this as a fleet task|How would you like to handle this plan", lines[i], re.IGNORECASE)), None)
    elif kind == "file":
        header_idx = next((i for i in range(first_opt - 1, -1, -1)
                           if any(re.search(pat, lines[i], re.IGNORECASE)
                                  for pat in (r"Allow (?:creation|editing|modification|deletion|writing|reading|access)(?: of this file|\b)",
                                              r"Allow (?:file )?access",
                                              r"File access(?: request)?",
                                              r"Permission to (?:read|access|write|create|edit|modify|delete) (?:this )?file",
                                              r"Access requested for (?:this )?file",
                                              r"Accept this file edit",
                                              r"Create file",
                                              r"Edit file",
                                              r"Write file",
                                              r"Read file",
                                              r"Pending edit"))), None)
    elif kind == "survey":
        header_idx = next((i for i in range(first_opt - 1, -1, -1)
                           if any(re.search(pat, lines[i], re.IGNORECASE)
                                  for pat in (r"Help improve our CLI",
                                              r"Feedback survey",
                                              r"Rate your experience",
                                              r"How satisfied are you",
                                              r"How is the CLI experience so far",
                                              r"\bTake a (?:quick )?survey"))), None)
    elif kind == "privacy":
        header_idx = next((i for i in range(first_opt - 1, -1, -1)
                           if any(re.search(pat, lines[i], re.IGNORECASE)
                                  for pat in (r"Privacy Notice",
                                              r"Terms of Service",
                                              r"Data collection notice",
                                              r"Before using",
                                              r"Do you agree"))), None)
    elif kind == "usage-limit":
        header_idx = next((i for i in range(first_opt - 1, -1, -1)
                           if any(re.search(pat, lines[i], re.IGNORECASE)
                                  for pat in (r"Individual quota reached",
                                              r"Usage limit reached",
                                              r"Rate limit exceeded",
                                              r"Resets in"))), None)

    if header_idx is None:
        header_idx = next((i for i in range(first_opt - 1, -1, -1)
                           if any(re.search(pat, lines[i], re.IGNORECASE)
                                  for pat in (r"Accessing workspace:",
                                              r"^[ \t│─❯>]*You are in\b",
                                              r"Requesting permission for:",
                                              r"Do you trust the contents",
                                              r"trust this (?:folder|directory|project)",
                                              r"Allow (?:creation|editing|modification|deletion|writing|reading|access)",
                                              r"Allow (?:file )?access",
                                              r"File access",
                                              r"Permission to (?:read|access|write|create|edit|modify|delete) (?:this )?file",
                                              r"Accept this file edit",
                                              r"Ready to code\?",
                                              r"Here is Claude's plan:",
                                              r"Claude has written up a plan",
                                              r"Write full plan to the plan-mode file only",
                                              r"Treat this as a fleet task",
                                              r"Which file",
                                              r"Select an option",
                                              r"Please specify",
                                              r"How would you like"))), None)
        if header_idx is not None:
            # `Accessing workspace:` is the outermost header of a trust dialog and the only
            # one the workspace path follows, so any trust-flavoured header found lower down
            # must walk back to it. Matching only on "Do you trust" missed the option line
            # `> Yes, I trust this folder`, which is what the reverse scan reaches first --
            # observed live, and it left the subject with no path to compare.
            # Similarly, current Codex displays "You are in <cwd>" before the question.
            if re.search(r"Do you trust|trust this (?:folder|directory|project)",
                         lines[header_idx], re.IGNORECASE):
                acc_idx = next((i for i in range(header_idx - 1, -1, -1)
                                if re.search(r"Accessing workspace:", lines[i], re.IGNORECASE)), None)
                if acc_idx is not None:
                    header_idx = acc_idx
                else:
                    you_in_idx = next((i for i in range(header_idx - 1, -1, -1)
                                       if re.search(r"^[ \t│─❯>]*You are in\b", lines[i], re.IGNORECASE)), None)
                    if you_in_idx is not None:
                        header_idx = you_in_idx
            if re.search(r"Claude has written up a plan|Here is Claude's plan:",
                         lines[header_idx], re.IGNORECASE):
                plan_idx = next((i for i in range(header_idx - 1, -1, -1)
                                 if re.search(r"Here is Claude's plan:", lines[i], re.IGNORECASE)), None)
                if plan_idx is not None:
                    header_idx = plan_idx
                ready_idx = next((i for i in range(header_idx - 1, -1, -1)
                                  if re.search(r"Ready to code\?", lines[i], re.IGNORECASE)), None)
                if ready_idx is not None:
                    header_idx = ready_idx
    if header_idx is not None:
        block = lines[header_idx:first_opt]
        subject = "\n".join(b.rstrip() for b in block if b.strip() and not _is_rule_line(b))

    # 2. Bottom prompts where subject precedes the question (e.g. Do you want to proceed?, requires approval)
    if not subject:
        idx = next((i for i in range(first_opt - 1, -1, -1)
                    if any(re.search(pat, lines[i], re.IGNORECASE)
                           for pat in (r"Do you want to proceed", r"requires approval"))), None)
        if idx is not None:
            head = None
            for i in range(idx - 1, -1, -1):
                if re.match(r"\s*(Bash command|Read file|Edit file|Write file|Pending edit|Tool use|.*\bcommand\b)\b", lines[i], re.IGNORECASE) or \
                   re.search(r"\+\d+\s+-\d+", lines[i]) or \
                   re.search(r"Requesting permission for:", lines[i], re.IGNORECASE):
                    head = i
                    break
            block = lines[head if head is not None else max(0, idx - 15):idx + 1]
            subject = "\n".join(b.rstrip() for b in block if b.strip()
                                and not any(m in b for m in ("requires approval", "Do you want to proceed"))
                                and not _is_rule_line(b))

    # 3. Fallback: take up to 6 non-rule lines before first_opt
    if not subject:
        tail = [l.rstrip() for l in lines[:first_opt] if l.strip() and not _is_rule_line(l)][-6:]
        subject = "\n".join(tail)

    # The confirmation question is chrome, not part of the command being judged, and the
    # allow rules are end-anchored. Branch 2 already dropped it; branch 1 did not, so a
    # subject captured after AGY rendered `Do you want to proceed?` read as
    # `Requesting permission for: claude --version Do you want to proceed?` and matched no
    # allow rule -- while the same command captured a moment earlier, before that line
    # appeared, was approved. Filter it in one place so every branch agrees and approval
    # stops depending on when the pane was scraped.
    subject = _drop_confirmation_question(subject)

    # `subject` keeps its newlines so the classifier can undo TUI wrapping;
    # `subject_display` is the flattened form for humans and tables.
    subject_clean = sanitize_pane_text(subject.strip())
    subject_display_clean = sanitize_pane_text(re.sub(r"\s+", " ", strip_dialog_chrome(subject)).strip())
    return {"kind": kind, "subject": subject_clean,
            "subject_display": subject_display_clean,
            "options": opts}


def dialog_accept_keys(pend: dict, text: str = "") -> list[str]:
    """Determine the keystrokes needed to confirm/approve a dialog.

    Numbered dialogs ('1. Yes') use ['1', 'enter'].
    Prose dialogs with an active selection ('> Yes...') or 'enter Confirm'
    use ['enter'].
    """
    opts = pend.get("options") or []
    if opts and opts[0].get("key") and str(opts[0]["key"]).isdigit():
        return [str(opts[0]["key"]), "enter"]
    return ["enter"]


def extract_trust_path(text: str) -> str | None:
    """Extract the workspace directory path from a trust dialog.

    Handles standard, headerless, and hard-wrapped layouts:
      1. Lines starting with 'You are in' before 'Do you trust...' (current Codex)
      2. Lines between 'Accessing workspace:' and 'Do you trust...' (AGY / Claude Code)
      3. Lines between 'Do you trust...' and 'Antigravity CLI...' or options
    Returns the dewrapped path string, or None if no path is present.
    """
    if not text:
        return None
    lines = [l.strip() for l in text.splitlines()]

    # The LAST header is the live dialog; an earlier one is scrollback that must not
    # supply the path being compared. Same rule as pending_prompt and parse_handoff.
    acc_idx = next((i for i in range(len(lines) - 1, -1, -1)
                    if re.search(r"Accessing workspace:", lines[i], re.IGNORECASE)), None)
    trust_idx = next((i for i in range(len(lines) - 1, -1, -1)
                      if re.search(r"Do you trust", lines[i], re.IGNORECASE)), None)
    you_in_idx = next((i for i in range(len(lines) - 1, -1, -1)
                       if re.search(r"^[ \t│─❯>]*You are in\b", lines[i], re.IGNORECASE)), None)
    folder_idx = next((i for i in range(len(lines) - 1, -1, -1)
                       if re.fullmatch(r"[ \t│─❯>]*Folder access", lines[i], re.IGNORECASE)), None)

    # Codex >= 0.160: a 'Folder access' header, the path (possibly hard-wrapped), then
    # 'Trust this folder?'. Only the latest header counts, as for the layouts below.
    if folder_idx is not None and max(i for i in (folder_idx, acc_idx, you_in_idx) if i is not None) == folder_idx:
        j = folder_idx + 1
        while j < len(lines) and (not lines[j] or _is_rule_line(lines[j])):
            j += 1
        block = []
        while (j < len(lines) and lines[j] and not _is_rule_line(lines[j])
               and not re.match(r"Trust this folder\b", lines[j], re.IGNORECASE)):
            block.append(lines[j])
            j += 1
        candidate = "".join(block).strip()
        if candidate.startswith(("/", "~")) and j < len(lines):
            return candidate

    # Current Codex: directory appears before the question as a line beginning
    # 'You are in' or 'You are in workspace' followed by absolute cwd (possibly hard-wrapped), then a blank line.
    if you_in_idx is not None and (acc_idx is None or you_in_idx > acc_idx):
        prefix_match = re.match(r"^[ \t│─❯>]*You are in(?:\s+workspace)?:?[ \t]*(.*)$", lines[you_in_idx], re.IGNORECASE)
        first_part = prefix_match.group(1).strip() if prefix_match else ""
        block = [first_part] if first_part else []
        j = you_in_idx + 1
        if not block:
            while j < len(lines) and (not lines[j] or _is_rule_line(lines[j])):
                if trust_idx is not None and j >= trust_idx:
                    break
                j += 1
        while j < len(lines) and lines[j] and not _is_rule_line(lines[j]):
            if trust_idx is not None and j >= trust_idx:
                break
            block.append(lines[j])
            j += 1
        if block:
            candidate = "".join(block).strip()
            blank_terminated = j < len(lines)
            if candidate.startswith(("/", "~")) and (blank_terminated or (trust_idx is not None and you_in_idx < trust_idx) or " " not in candidate):
                return candidate

    # `Accessing workspace:` introduces the path as its own blank-delimited block, in
    # both AGY's dialog and Claude Code's. Read it from the header alone, because the
    # branches below need a "Do you trust" line and Claude Code does not print one -- it
    # asks "Is this a project you created or one you trust?", so both branches fell
    # through and the simulation refused its own lead with "could not extract directory
    # path". Consecutive lines are joined so a path wrapped by a narrow pane still
    # resolves, and a blank line ends the block so a path containing spaces survives.
    if acc_idx is not None:
        j = acc_idx + 1
        while j < len(lines) and (not lines[j] or _is_rule_line(lines[j])):
            j += 1
        block = []
        while j < len(lines) and lines[j] and not _is_rule_line(lines[j]):
            block.append(lines[j])
            j += 1
        candidate = "".join(block).strip()
        # A blank line must have closed the block. Callers that pass an already-flattened
        # subject have no blank lines left, so the block would run to the end of the text
        # and swallow the dialog's prose; those fall through to the branches below.
        blank_terminated = j < len(lines)
        if candidate.startswith(("/", "~")) and (blank_terminated or " " not in candidate):
            return candidate

    if acc_idx is not None and trust_idx is not None and acc_idx < trust_idx:
        j = acc_idx + 1
        while j < trust_idx and (not lines[j] or _is_rule_line(lines[j])):
            j += 1
        block = []
        while j < trust_idx and lines[j] and not _is_rule_line(lines[j]):
            block.append(lines[j])
            j += 1
        if block:
            candidate = "".join(block).strip()
            if candidate.startswith(("/", "~")) or candidate:
                return candidate

    if trust_idx is not None:
        start_idx = trust_idx + 1
        while start_idx < len(lines) and re.search(
            r"^(?:(?:the\s+)?contents\b.*|of\s+this\b.*|(?:this\s+)?(?:folder|directory|project)\??|oject\??|ectory\??)$",
            lines[start_idx], re.IGNORECASE
        ):
            start_idx += 1

        j = start_idx
        while j < len(lines) and (not lines[j] or _is_rule_line(lines[j])):
            j += 1
        block = []
        while j < len(lines) and lines[j] and not _is_rule_line(lines[j]):
            if re.search(r"^(?:Antigravity CLI|Codex|OpenAI Codex|❯|>|\d+[\.\)]|\[\d+\]|Yes\b|No\b|Navigate\b)", lines[j], re.IGNORECASE):
                break
            block.append(lines[j])
            j += 1
        if block:
            candidate = "".join(block).strip()
            blank_terminated = j < len(lines)
            if candidate.startswith(("/", "~")) and (blank_terminated or " " not in candidate):
                return candidate
            if candidate.startswith(("/", "~")):
                return candidate

    return None


def decide_trust_dialog(text_or_subject: str, expected_cwd: str | Path | None) -> dict:
    """Approve trust dialog if and only if its path matches the validated cwd.

    Fails closed: returns action='refuse' if expected_cwd is absent, no path
    can be extracted, or the resolved paths differ.
    """
    if not expected_cwd:
        return {
            "action": "refuse",
            "extracted_path": None,
            "expected_path": None,
            "why": "no validated cwd available to compare against trust dialog; give me full path and request permission again",
        }

    extracted = extract_trust_path(text_or_subject)
    if not extracted:
        return {
            "action": "refuse",
            "extracted_path": None,
            "expected_path": str(expected_cwd),
            "why": f"could not extract directory path from trust dialog (expected {expected_cwd}); give me full path and request permission again",
        }

    try:
        dialog_resolved = Path(extracted).expanduser().resolve()
    except Exception as exc:
        return {
            "action": "refuse",
            "extracted_path": extracted,
            "expected_path": str(expected_cwd),
            "why": f"invalid directory path in trust dialog: {extracted!r} ({exc}); give me full path and request permission again",
        }

    try:
        expected_resolved = Path(expected_cwd).expanduser().resolve()
    except Exception as exc:
        return {
            "action": "refuse",
            "extracted_path": str(dialog_resolved),
            "expected_path": str(expected_cwd),
            "why": f"invalid expected cwd: {expected_cwd!r} ({exc})",
        }

    if dialog_resolved == expected_resolved:
        return {
            "action": "approve",
            "extracted_path": str(dialog_resolved),
            "expected_path": str(expected_resolved),
            "why": f"trust directory matches validated cwd: {dialog_resolved}",
        }

    exp_str = str(expected_resolved)
    dlg_str = str(dialog_resolved)
    raw_exp = str(expected_cwd)
    raw_ext = str(extracted)

    is_truncation = (
        (exp_str.startswith(dlg_str) and len(dlg_str) < len(exp_str) and dialog_resolved not in expected_resolved.parents)
        or (raw_exp.startswith(raw_ext) and len(raw_ext) < len(raw_exp) and Path(raw_ext).expanduser() not in Path(raw_exp).expanduser().parents)
    )

    if is_truncation:
        return {
            "action": "refuse",
            "extracted_path": str(dialog_resolved),
            "expected_path": str(expected_resolved),
            "why": (
                f"trust dialog truncated its path to the pane width ('{dialog_resolved}'); "
                "the full path cannot be recovered from the pane; "
                "give me full path and request permission again"
            ),
        }

    return {
        "action": "refuse",
        "extracted_path": str(dialog_resolved),
        "expected_path": str(expected_resolved),
        "why": f"trust dialog path '{dialog_resolved}' does not match expected cwd '{expected_resolved}'; give me full path and request permission again",
    }


FORBIDDEN_PREFIX_CHARS = set(";\n\r|&><`$()")


def load_policy() -> dict:
    try:
        policy = json.loads(POLICY_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise StateError(f"cannot load risk policy {POLICY_FILE}: {e}") from e
    if (not isinstance(policy, dict) or not isinstance(policy.get("deny"), list)
            or not isinstance(policy.get("allow"), list)
            or not isinstance(policy.get("task_deny"), list)
            or not isinstance(policy.get("agy_allow"), list)):
        raise StateError(f"invalid risk policy schema: {POLICY_FILE}")
    try:
        for rule in policy["deny"] + policy["allow"] + policy["task_deny"]:
            if not isinstance(rule, dict) or not all(isinstance(rule.get(k), str) and rule[k] for k in ("id", "pattern", "why")):
                raise ValueError("rule requires non-empty id, pattern, and why")
            if "reviewable" in rule and type(rule["reviewable"]) is not bool:
                raise ValueError("rule reviewable property must be a boolean")
            re.compile(rule["pattern"], re.IGNORECASE)
        if "settings" in policy and isinstance(policy["settings"], dict):
            if "reviewable" in policy["settings"] and type(policy["settings"]["reviewable"]) is not bool:
                raise ValueError("settings.reviewable property must be a boolean")
        seen_ids = set()
        seen_prefixes = set()
        prefixes = []
        for entry in policy["agy_allow"]:
            if not isinstance(entry, dict):
                raise ValueError("agy_allow entry must be an object")
            if not all(isinstance(entry.get(k), str) and entry[k] for k in ("id", "prefix", "why")):
                raise ValueError("agy_allow entry requires non-empty id, prefix, and why")
            eid = entry["id"]
            prefix = entry["prefix"]
            if eid in seen_ids:
                raise ValueError(f"duplicate agy_allow id: {eid}")
            seen_ids.add(eid)
            if prefix in seen_prefixes:
                raise ValueError(f"duplicate agy_allow prefix: {prefix}")
            seen_prefixes.add(prefix)
            if any(c in prefix for c in FORBIDDEN_PREFIX_CHARS):
                raise ValueError(f"agy_allow prefix contains forbidden character: {prefix}")
            prefixes.append(prefix)
        for i, p1 in enumerate(prefixes):
            for j, p2 in enumerate(prefixes):
                if i != j and p2.startswith(p1):
                    raise ValueError(f"agy_allow prefix {p1!r} is a strict prefix of {p2!r}")
    except (AttributeError, TypeError, ValueError, re.error) as e:
        raise StateError(f"invalid risk policy rule in {POLICY_FILE}: {e}") from e
    return policy


def agy_allow_prefixes(policy: dict) -> list[str]:
    return [entry["prefix"] for entry in policy.get("agy_allow", [])]


# agy_permissions is a sibling script, not an installed package, so it is loaded
# by path rather than by name -- fleet.py itself is routinely loaded that way by
# the test suite, and a bare `import agy_permissions` would then miss sys.path.
_AGY_PERM = None


def agy_permissions_module():
    global _AGY_PERM
    if _AGY_PERM is None:
        import importlib.util
        path = SKILL_DIR / "agy_permissions.py"
        spec = importlib.util.spec_from_file_location("tar_agy_permissions", path)
        if spec is None or spec.loader is None:
            raise StateError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _AGY_PERM = mod
    return _AGY_PERM


def install_agy_allowlist(policy: dict, env: dict[str, str] | None = None) -> dict:
    """Give an AGY worker its standing authorizations before it starts.

    Without this an AGY worker stops at a human confirmation for even a bare
    `ls`, which is what made an unattended AGY fleet impossible. The prefixes
    come from the policy file and nowhere else, and every one of them is
    asserted by the suite to be a prefix classify_text already approves, so
    installing them cannot widen what the fleet permits.

    Fails closed: an AGY worker launched without its allowlist would stall on
    its first command and hold its leases while doing nothing, so a settings
    file we cannot safely write is a refusal to spawn, not a warning.

    A profile runs AGY under an account home, which has its own settings file, so
    the allowlist goes where that account's AGY will read it.
    """
    agyp = agy_permissions_module()
    account_home = (env or {}).get(AGY_ACCOUNT_HOME)
    settings_path = (Path(account_home) / ".gemini" / "antigravity-cli" / "settings.json"
                     if account_home else None)
    try:
        return agyp.sync_allowlist(agy_allow_prefixes(policy), settings_path=settings_path)
    except agyp.AgyPermissionsError as e:
        raise StateError(f"cannot install AGY allowlist: {e}") from e


def check_task_text(task: str, policy: dict) -> dict:
    """Judge herder-authored task text against task_deny rules.

    Task text and pane text differ fundamentally in trust and in shape:
    task text is authored directly by the herder and arrives intact without
    TUI wrapping artifacts, whereas pane text is scraped from a terminal and
    may contain arbitrary agent output with mid-token line wraps. Task text
    must be judged against rules matching instruction shape (e.g. explicit
    control flags with leading dashes or full destructive command invocations)
    rather than bare vocabulary, and does not require wrap-interpretation
    readings or prose punctuation filters.
    """
    for r in policy.get("task_deny", []):
        if (m := re.search(r["pattern"], task, re.IGNORECASE)):
            return {"action": "refuse", "risk": "high", "rule": r["id"], "why": r["why"],
                    "matched": m.group(0).strip()[:80]}
    return {"action": "pass", "risk": "low", "rule": None, "why": None}


def _variants(subject: str) -> tuple:
    """Two readings of a TUI subject, because agents hard-wrap their own output.

    A pane can contain `rm\\n  -rf x` (wrap at a space) or `resource_poo\\n  l`
    (wrap mid-token). Matching only the space-joined form lets a mid-token wrap
    split `.env` into `.e` + `nv` and slip past the deny list.

      normalized -> whitespace collapsed to single spaces (catches `rm\\n -rf`)
      dewrapped  -> the newline AND its continuation indent removed, other
                    spaces kept, which is the exact inverse of how the TUI
                    wraps: `resource_poo\\n  l` -> `resource_pool`,
                    `npm te\\n  st` -> `npm test`, `.e\\n  nv` -> `.env`

    Both readings are checked, because a wrap at a space is only recoverable by
    `normalized` and a wrap mid-token only by `dewrapped`.
    """
    normalized = re.sub(r"\s+", " ", subject).strip()
    dewrapped = re.sub(r"\n[ \t]*", "", subject).strip()
    return tuple(dict.fromkeys(
        [normalized, dewrapped] + _wrap_readings(subject)
    ))


# `normalized` and `dewrapped` each apply ONE interpretation to EVERY newline in
# the subject, which fails whenever a single prompt wraps both ways. A 54-column
# headless pane produced:
#
#     Requesting permission for:\n   bash\nskills/.../test_auto_her\nd_empty.sh
#
# The first wrap is at a space (only `normalized` recovers it) and the second is
# mid-token (only `dewrapped` recovers it), so neither reading reconstructs
# `bash skills/.../test_auto_herd_empty.sh` and a pre-approved command escalated.
# Decide each wrap independently instead: with k wraps there are 2**k readings,
# and the true command is always one of them.
MAX_WRAPS = 10


def _wrap_readings(subject: str) -> list:
    """Every per-newline space/join interpretation of a wrapped subject."""
    parts = re.split(r"\n[ \t]*", subject)
    gaps = len(parts) - 1
    if gaps < 1 or gaps > MAX_WRAPS:
        return []
    out = []
    for choices in itertools.product((" ", ""), repeat=gaps):
        s = parts[0]
        for sep, nxt in zip(choices, parts[1:]):
            s += sep + nxt
        out.append(re.sub(r"[ \t]+", " ", s).strip())
    return out


# Terminal dialog chrome (selection cursors, option lists, key hints, separators)
# is rendering from the terminal user interface, not part of the command being judged.
# If scraped into the subject, chrome markers like `> 1. Yes` trip shell-composition
# on the cursor `>` and shadow allow rules. Strip dialog chrome before classification.
CHROME_OPTION_NUMBERED = re.compile(r"^\s*[❯>]?\s*(?:\d+[\.\)]|\[\d+\])\s+\S+")
CHROME_OPTION_PROSE = re.compile(r"^\s*[❯>]\s+(?:Yes|No|I trust)\b", re.IGNORECASE)
CHROME_OPTION_PROSE_INDENTED = re.compile(r"^\s{2,}(?:Yes|No)\b", re.IGNORECASE)
CHROME_KEY_HINTS = re.compile(
    r"^(?:Navigate|enter\b|Ctrl|Esc|tab\b|\? for shortcuts|\? for help)",
    re.IGNORECASE,
)
CHROME_INLINE_TRAILING = (
    re.compile(r"\s+[❯>]\s*(?:\d+[\.\)]|\[\d+\])\s+.*$"),
    re.compile(r"\s+[❯>]\s+(?:Yes|No|I trust)\b.*$", re.IGNORECASE),
    re.compile(r"\s+(?:Navigate\s*·\s*enter Confirm|\? for shortcuts.*)$", re.IGNORECASE),
)


def _is_dialog_chrome_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _is_rule_line(line):
        return True
    if CHROME_OPTION_NUMBERED.match(line):
        return True
    if CHROME_OPTION_PROSE.match(line) or CHROME_OPTION_PROSE_INDENTED.match(line):
        return True
    hint = re.sub(r"^[^A-Za-z?]+", "", s)
    if CHROME_KEY_HINTS.search(hint) or re.search(r"(?:enter Confirm|\? for shortcuts)", s, re.IGNORECASE):
        return True
    return False


def strip_dialog_chrome(subject: str) -> str:
    """Remove terminal user interface chrome (cursors, option lines, key hints, separators).

    A terminal dialog's own rendering—such as the selection cursor `>` on a highlighted
    option line, numbered option lists, key hints, or box-drawing rules—must never reach
    classification. If chrome is scraped into the subject, characters like `>` trip
    `shell-composition` and shadow legitimate allow rules.

    Leaves genuine commands intact, including commands legitimately beginning with a digit
    or redirection in shell position.
    """
    if not subject:
        return ""
    lines = subject.splitlines()
    cleaned_lines = []
    for line in lines:
        if _is_dialog_chrome_line(line):
            continue
        cleaned = re.sub(r"^\s*[\u276f]\s*", "", line)
        cleaned_lines.append(cleaned)
    res = "\n".join(cleaned_lines)
    for pat in CHROME_INLINE_TRAILING:
        res = pat.sub("", res)
    return res.strip()


def mask_quotes(text: str) -> str:
    """Mask characters inside single and double quotes to prevent false shell-composition hits.

    Metacharacters sitting inside quoted strings (e.g. `;` in `python3 -c "import json; ..."`,
    `|` in `git log --grep="a | b"`, `>` in `git commit -m "a > b"`) are inert arguments,
    not shell operators in shell position.

    Masks ONLY characters inside quotes, replacing them with a neutral placeholder ('_')
    to preserve character offsets and string length. Characters outside quotes—including
    metacharacters immediately preceding or following quotes (e.g. `"foo";bar` or `'a'>b`)—
    are preserved unchanged.

    CRITICAL SAFETY INVARIANT:
    This masking MUST ONLY be applied to position-sensitive shell syntax rules
    (specifically `shell-composition`). It MUST NEVER be applied to content deny rules
    (such as `recursive-delete`, `privilege`, `git-destructive`, `remote-exec`, `secrets`),
    so that dangerous payloads wrapped in quotes cannot evade content inspection.

    SAFETY COST & RESIDUAL RISK:
    A compound command nested inside a quoted argument to an interpreter (e.g. `bash -c "cmd1; cmd2"`)
    no longer trips `shell-composition` on the inner separator ';'. Destructive payloads (such as `rm -rf /`,
    `git reset --hard`, `curl ... | sh`) remain caught by their respective content deny rules
    (`recursive-delete`, `git-destructive`, `remote-exec`, `privilege`). Benign compound commands
    inside quotes that trigger no content deny rule escalate under default-deny unless matched
    by an allow rule.
    """
    chars = list(text)
    n = len(chars)
    i = 0
    in_quote = None  # None, "'" or '"'

    while i < n:
        c = chars[i]
        if in_quote is None:
            if c == "'":
                in_quote = "'"
            elif c == '"':
                in_quote = '"'
            elif c == "\\":
                # Escaped character outside quotes (e.g. \; or \>)
                i += 1  # Skip the escaped character
        elif in_quote == "'":
            if c == "'":
                in_quote = None
            else:
                chars[i] = "_"
        elif in_quote == '"':
            if c == "\\":
                chars[i] = "_"
                if i + 1 < n:
                    i += 1
                    chars[i] = "_"
            elif c == '"':
                in_quote = None
            else:
                chars[i] = "_"
        i += 1

    return "".join(chars)


# A CLI's own shell instrumentation is not the agent's command. AGY appends
# `; echo "Exit: $?"` to every command it runs so it can read the exit code back
# out of the pane. Classifying that suffix as "compound shell syntax" made every
# AGY command escalate, which made an unattended AGY fleet impossible. Strip the
# suffix -- and only that exact suffix -- before the deny list sees the subject.
INSTRUMENTATION_SUFFIX = re.compile(
    r"""\s*;\s*echo\s+["']?Exit:\s*\$\?["']?\s*$"""
)


def strip_instrumentation(subject: str) -> str:
    """Remove a trailing runner-added exit-code echo. Nothing else."""
    return INSTRUMENTATION_SUFFIX.sub("", subject)


def classify_text(subject: str, policy: dict, task: str = None, read_only: bool = False, kind: str = None) -> dict:
    """DENY wins over ALLOW; unmatched escalates (default-deny).

    `task` is the agent's assigned objective. A benign-looking prompt from an
    agent whose TASK is destructive still escalates -- otherwise an unattended
    herder happily approves each recon step on the way to the deletion.
    """
    raw_readings = [strip_instrumentation(v) for v in _variants(subject)]
    clean_subject = strip_dialog_chrome(subject)
    positional_readings = [strip_instrumentation(strip_dialog_chrome(v)) for v in _variants(clean_subject)]

    def _hits(rule, texts):
        return any(re.search(rule["pattern"], x, re.IGNORECASE) for x in texts)

    plan_rule = next((r for r in policy.get("read_only_allow", []) if r.get("id") == "plan-approval"), None)
    is_plan_dialog = bool(plan_rule and _hits(plan_rule, positional_readings))
    is_file_dialog = (kind == "file") or any(
        re.search(r"^(?:Allow (?:creation|editing|modification|deletion|writing|reading|access)|File access|Considering File Access|Permission to (?:read|access|write|create|edit|modify|delete)|Access requested for (?:this )?file|Accept this file edit|Create file|Edit file|Write file|Read file|Pending edit)\b", x, re.IGNORECASE)
        for x in positional_readings
    )

    def _eval_readings_for_deny(rule):
        # Hard safety constraint: quote masking and chrome stripping apply ONLY
        # to position-sensitive syntax rules (shell-composition and write-output-option).
        # Dangerous content deny rules (embedded-cli-bypass, recursive-delete, privilege,
        # git-destructive, remote-exec, secrets, etc.) MUST ALWAYS inspect the unmasked,
        # unstripped raw readings so that dangerous payloads or bypass flags in quoted
        # arguments or dialog option lines cannot evade content inspection.
        #
        # Safety cost & residual risk: A compound command nested inside a quoted argument
        # (e.g. bash -c "cmd1; cmd2") no longer trips shell-composition on the inner separator.
        # Destructive payloads (rm -rf, git reset --hard, curl|sh) remain caught by content
        # deny rules (recursive-delete, git-destructive, remote-exec). Benign compound
        # arguments without content deny hits escalate under default-deny unless allowed.
        # Positional syntax rules (shell-composition and write-output-option) do not apply
        # to plan dialogs or file creation/edit dialogs whose subjects embed payload text.
        if rule["id"] == "shell-composition":
            return [] if (is_plan_dialog or is_file_dialog) else [mask_quotes(x) for x in positional_readings]
        elif rule["id"] == "write-output-option":
            return [] if (is_plan_dialog or is_file_dialog) else positional_readings
        else:
            return raw_readings

    if task:
        task_check = check_task_text(task, policy)
        if task_check["risk"] == "high":
            return {"action": "escalate", "risk": "high", "rule": task_check["rule"],
                    "why": f"assigned task is high-risk ({task_check['why']}); "
                           f"every prompt from this agent needs a human",
                    "tainted_by_task": True,
                    "reviewable": False,
                    "ineligible_rule": task_check["rule"]}

    if kind == "privacy":
        return {
            "action": "approve",
            "risk": "low",
            "rule": "privacy-notice-accept",
            "choice": "1",
            "why": "auto-accept vendor privacy notice",
        }
    if kind == "usage-limit":
        return {
            "action": "escalate",
            "risk": "high",
            "rule": "usage-limit-overlay",
            "why": "vendor usage limit or quota reached",
            "reviewable": False,
            "ineligible_rule": "usage-limit-overlay",
        }

    matching_deny_rules = []
    for r in policy["deny"]:
        eval_readings = _eval_readings_for_deny(r)
        if _hits(r, eval_readings):
            matching_deny_rules.append(r)

    if matching_deny_rules:
        first_r = matching_deny_rules[0]
        ineligible_matches = [r for r in matching_deny_rules if not r.get("reviewable", False)]
        is_reviewable = len(ineligible_matches) == 0
        ineligible_rule = ineligible_matches[0]["id"] if ineligible_matches else None
        return {
            "action": "escalate",
            "risk": "high",
            "rule": first_r["id"],
            "why": first_r["why"],
            "reviewable": is_reviewable,
            "ineligible_rule": ineligible_rule,
        }

    for r in policy["allow"]:
        if _hits(r, positional_readings):
            res = {"action": "approve", "risk": "low",
                    "rule": r["id"], "why": r["why"]}
            if "sanctioned_choice" in r or "choice" in r:
                res["choice"] = str(r.get("sanctioned_choice") or r.get("choice"))
            return res

    if read_only:
        for r in policy.get("read_only_allow", []):
            if _hits(r, positional_readings):
                res = {"action": "approve", "risk": "low",
                        "rule": r["id"], "why": r["why"]}
                if "sanctioned_choice" in r or "choice" in r:
                    res["choice"] = str(r.get("sanctioned_choice") or r.get("choice"))
                return res

    is_reviewable = policy.get("settings", {}).get("reviewable", False)
    return {
        "action": "escalate",
        "risk": "unknown",
        "rule": None,
        "why": "matched no allow rule (default-deny)",
        "reviewable": is_reviewable,
        "ineligible_rule": None if is_reviewable else "default-deny",
    }


def get_assignment(agent: str, session: str | None = None) -> dict:
    data = load_json(state_path("tasks.json", session), {})
    value = data.get(agent, {})
    if not isinstance(value, dict):
        raise StateError(f"invalid assignment for {agent!r}")
    return value


def get_task(agent: str, session: str | None = None) -> str | None:
    return get_assignment(agent, session).get("task")


def set_task(agent: str, task: str, session: str | None = None,
             group: str | None = None, resources: list | None = None,
             read_only: bool = False, approval_mode: str | None = None,
             workspace: str | None = None, model: str | None = None,
             cwd: str | None = None, bead_id: str | None = None,
             profile: str | None = None):
    handoff_path(agent, session).unlink(missing_ok=True)
    p = state_path("tasks.json", session)
    now = time.time()
    def update(d):
        current = d.get(agent, {})
        for stale in ("parked_resources", "parked_at", "cleanup_error",
                      "old_group", "old_resources"):
            current.pop(stale, None)
        ws = workspace or current.get("workspace")
        c = cwd or current.get("cwd")
        current.update({"task": task, "group": group, "resources": resources or [],
                        "read_only": read_only, "approval_mode": approval_mode,
                        "lifecycle": "active",
                        "task_time": now,
                        "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        if model:
            current["model"] = model
        if profile:
            current["profile"] = profile
        if ws:
            current["workspace"] = ws
        if c:
            current["cwd"] = c
        if bead_id:
            # Beads metadata is authoritative; this is a session-local recovery
            # cache allowing a lost short pane name to be traced without guessing.
            current["bead_id"] = bead_id
        d[agent] = current
        return d
    update_json(p, {}, update)


# ---------------- state ----------------

def state_path(kind: str, session: str | None = None) -> Path:
    p = STATE_DIR / session_key(session)
    p.mkdir(parents=True, exist_ok=True)
    target = p / kind
    if kind in ("events", "events/tmp"):
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def emit_event(session: str | None, agent: str, kind: str, payload: dict | None = None) -> Path:
    """Emit an atomic outbox event via temp file and atomic os.replace."""
    tmp_dir = state_path("events/tmp", session)
    events_dir = state_path("events", session)
    t_ns = time.time_ns()
    uid = uuid.uuid4().hex[:8]
    tmp_file = tmp_dir / f"{agent}_{t_ns}_{uid}.json"
    now = time.time()
    event_data = {
        "agent": agent,
        "kind": kind,
        "at": now,
        "payload": payload or {},
    }
    tmp_file.write_text(json.dumps(event_data, indent=2), encoding="utf-8")
    target_path = events_dir / f"evt_{int(now * 1000)}_{agent}_{uid}.json"
    os.replace(tmp_file, target_path)
    return target_path


def drain_events(session: str | None = None) -> list[dict]:
    """Drain, parse, and unlink all event outbox files in state_path('events', session)."""
    events_dir = state_path("events", session)
    if not events_dir.is_dir():
        return []

    events: list[dict] = []
    try:
        candidates = sorted(events_dir.glob("evt_*.json"))
    except OSError:
        return []

    for file_path in candidates:
        try:
            if not file_path.is_file():
                continue
            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                continue
            data = json.loads(content)
            try:
                file_path.unlink()
            except OSError:
                pass
            if isinstance(data, dict):
                events.append(data)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            # Ignores corrupted or in-flight temp files gracefully
            continue

    def _event_timestamp(e: dict):
        for key in ("timestamp", "at", "time", "ts"):
            val = e.get(key)
            if isinstance(val, (int, float)):
                return (0, float(val))
            if isinstance(val, str) and val:
                try:
                    return (0, float(val))
                except ValueError:
                    return (1, val)
        return (2, 0.0)

    events.sort(key=_event_timestamp)
    return events


def _has_event_files(events_dir: Path, session_dir: Path | None = None) -> bool:
    try:
        if events_dir.is_dir():
            with os.scandir(events_dir) as it:
                for entry in it:
                    if entry.name.startswith("evt_") and entry.name.endswith(".json"):
                        return True
        if session_dir and session_dir.is_dir():
            with os.scandir(session_dir) as it:
                for entry in it:
                    if entry.name.startswith("handoff-") and entry.name.endswith(".json"):
                        return True
    except OSError:
        pass
    return False


def wait_for_events(session: str | None = None, timeout: float = 2.0) -> bool:
    """Wait up to timeout seconds for event or handoff files to appear.

    Uses platform-native filesystem monitoring (kqueue on macOS/BSD, inotify on Linux)
    and smoothly falls back to sub-interval polling across all platforms.
    Returns True if an event or handoff signal was detected, False if timed out.
    """
    if timeout <= 0.0:
        return False

    events_dir = state_path("events", session)
    session_dir = events_dir.parent if events_dir.name == "events" else state_path("", session)
    if _has_event_files(events_dir, session_dir):
        return True

    # Attempt 1: macOS / BSD kqueue
    if hasattr(select, "kqueue"):
        try:
            kq = select.kqueue()
            fds = []
            evs = []
            for d in (events_dir, session_dir):
                if d.is_dir():
                    try:
                        fd = os.open(str(d), os.O_RDONLY)
                        fds.append(fd)
                        evs.append(
                            select.kevent(
                                fd,
                                filter=select.KQ_FILTER_VNODE,
                                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                                fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_LINK | select.KQ_NOTE_ATTRIB,
                            )
                        )
                    except Exception:
                        pass
            try:
                if _has_event_files(events_dir, session_dir):
                    return True
                if evs:
                    res = kq.control(evs, len(evs), timeout)
                    if res:
                        return True
                return _has_event_files(events_dir, session_dir)
            finally:
                for fd in fds:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
                kq.close()
        except Exception:
            pass

    # Attempt 2: Linux inotify via libc ctypes
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            libc = None
            try:
                libc = ctypes.CDLL(None)
            except Exception:
                pass
            if libc is None or not hasattr(libc, "inotify_init1"):
                try:
                    libc = ctypes.CDLL("libc.so.6")
                except Exception:
                    pass
            if libc and hasattr(libc, "inotify_init1") and hasattr(libc, "inotify_add_watch"):
                IN_NONBLOCK = 0x00000800
                IN_CLOEXEC = 0x00080000
                IN_CREATE = 0x00000100
                IN_MOVED_TO = 0x00000080
                IN_CLOSE_WRITE = 0x00000008
                fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
                if fd >= 0:
                    try:
                        wds = []
                        for d in (events_dir, session_dir):
                            if d.is_dir():
                                wd = libc.inotify_add_watch(
                                    fd,
                                    str(d).encode("utf-8"),
                                    IN_CREATE | IN_MOVED_TO | IN_CLOSE_WRITE,
                                )
                                if wd >= 0:
                                    wds.append(wd)
                        if wds:
                            if _has_event_files(events_dir, session_dir):
                                return True
                            r, _, _ = select.select([fd], [], [], timeout)
                            if r:
                                return True
                            return _has_event_files(events_dir, session_dir)
                    finally:
                        os.close(fd)
        except Exception:
            pass

    # Attempt 3: Universal fallback using responsive sub-interval polling (50ms)
    end_time = time.monotonic() + timeout
    while time.monotonic() < end_time:
        if _has_event_files(events_dir, session_dir):
            return True
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.05, remaining))
    return _has_event_files(events_dir, session_dir)


def handoff_path(agent: str, session: str | None = None) -> Path:
    return state_path(f"handoff-{agent}.json", session)


REPO_HANDOFF_DIR = ".herder"


def repo_handoff_path(agent: str, cwd: str | Path | None) -> Path | None:
    """The in-project handoff location, for a CLI that cannot write outside its project root.

    Verified against AGY during round 2 of the agentic-feature-planner build: five writes to
    the state-directory path were each reported as successful in the pane and produced no file
    anywhere on the machine, while the identical JSON written inside the project root landed on
    the first attempt. Combined with pane text that wraps mid-token -- which corrupts the
    scraped `FLEET_HANDOFF:` payload with a control character -- such a worker has no way to
    hand off at all, and a lead is left moving the file by hand to close a verified bead.

    So the runner looks here too. This is transport, not a deliverable: `complete` removes the
    file once it has accepted it, and the directory is dot-prefixed so it stays out of the way
    of the work.
    """
    if not cwd:
        return None
    return Path(cwd) / REPO_HANDOFF_DIR / f"handoff-{agent}.json"


def failed_handoff_raw_path(agent: str, cwd: str | Path | None = None) -> Path:
    """The path to persist unparsed raw pane evidence on schema validation failure."""
    target_dir = Path(cwd) if cwd else Path.cwd()
    return target_dir / REPO_HANDOFF_DIR / f"failed-handoff-{agent}.raw"


def _persist_failed_handoff_raw(agent: str, raw_text: str, cwd: str | Path | None = None) -> Path | None:
    try:
        p = failed_handoff_raw_path(agent, cwd)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw_text, encoding="utf-8")
        return p
    except Exception:
        return None


@contextlib.contextmanager
def state_lock(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_path = p.parent / ".fleet-state.lock"
    try:
        fd = open(lock_path, "a+")
    except OSError as e:
        raise StateError(f"cannot open fleet state lock {lock_path}: {e}") from e
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _load_json_unlocked(p: Path, default):
    if not p.exists():
        return copy.deepcopy(default)
    try:
        raw = p.read_text()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        raise StateError(f"cannot read valid fleet state {p}: {e}") from e
    if default is not None and not isinstance(data, type(default)):
        raise StateError(
            f"invalid fleet state type in {p}: expected {type(default).__name__}"
        )
    _validate_fleet_state(p, data)
    return data


def _validate_fleet_state(p: Path, data):
    if p.name == "tasks.json" and isinstance(data, dict):
        for agent, assignment in data.items():
            if not IDENTIFIER_RE.fullmatch(agent) or not isinstance(assignment, dict):
                raise StateError(f"malformed task assignment in {p}: {agent!r}")
            resources = assignment.get("resources", [])
            if not isinstance(resources, list) or not all(isinstance(x, str) for x in resources):
                raise StateError(f"assignment {agent!r} has invalid resources")
            if "read_only" in assignment and type(assignment["read_only"]) is not bool:
                raise StateError(f"assignment {agent!r} has invalid read_only")
            if assignment.get("group") is not None and not isinstance(assignment.get("group"), str):
                raise StateError(f"assignment {agent!r} has invalid group")
            if resources and not assignment.get("group"):
                raise StateError(f"assignment {agent!r} has resources without a group")
            if assignment.get("read_only", False) and resources:
                raise StateError(f"read-only assignment {agent!r} unexpectedly owns resources")
            if (assignment.get("approval_mode") is not None
                    and assignment.get("approval_mode") not in APPROVAL_MODES):
                raise StateError(f"assignment {agent!r} has invalid approval_mode")
            if assignment.get("lifecycle", "active") not in ("active", "parked", "dead"):
                raise StateError(f"assignment {agent!r} has invalid lifecycle")
            if "lost_at" in assignment and not isinstance(assignment["lost_at"], (int, float)):
                raise StateError(f"assignment {agent!r} has invalid lost_at")
            if "lost_reason" in assignment and not isinstance(assignment["lost_reason"], str):
                raise StateError(f"assignment {agent!r} has invalid lost_reason")
            if "task_time" in assignment and not isinstance(assignment["task_time"], (int, float)):
                raise StateError(f"assignment {agent!r} has invalid task_time")
            if assignment.get("workspace") is not None and not isinstance(assignment.get("workspace"), str):
                raise StateError(f"assignment {agent!r} has invalid workspace")
            if assignment.get("bead_id") is not None and not isinstance(assignment.get("bead_id"), str):
                raise StateError(f"assignment {agent!r} has invalid bead_id")
            if "reviewed_approvals" in assignment:
                revs = assignment["reviewed_approvals"]
                if not isinstance(revs, list) or not all(
                    isinstance(r, dict)
                    and isinstance(r.get("who"), str) and r["who"]
                    and isinstance(r.get("subject"), str)
                    and (isinstance(r.get("rule"), str) or r.get("rule") is None)
                    and (isinstance(r.get("timestamp"), str) or isinstance(r.get("at"), str))
                    and (isinstance(r.get("choice"), str) or isinstance(r.get("option"), str))
                    for r in revs
                ):
                    raise StateError(f"assignment {agent!r} has invalid reviewed_approvals")
    elif p.name == "escalations.json":
        if not all(isinstance(item, dict) for item in data):
            raise StateError(f"malformed escalation state in {p}")
    elif p.name == "handoffs.json":
        if not all(isinstance(agent, str) and isinstance(item, dict)
                   for agent, item in data.items()):
            raise StateError(f"malformed handoff state in {p}")


def load_json(p: Path, default):
    with state_lock(p):
        return _load_json_unlocked(p, default)


def _save_json_unlocked(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    _validate_fleet_state(p, data)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        dir_fd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise StateError(f"cannot persist fleet state {p}: {e}") from e


def save_json(p: Path, data):
    with state_lock(p):
        _save_json_unlocked(p, data)


def update_json(p: Path, default, updater):
    with state_lock(p):
        data = _load_json_unlocked(p, default)
        result = updater(data)
        _save_json_unlocked(p, data if result is None else result)
        return data if result is None else result


def pool(*args) -> dict:
    timeout = HERDR_CALL_TIMEOUT
    if "--timeout" in args:
        try:
            timeout = max(timeout, float(args[args.index("--timeout") + 1]) + 10)
        except (ValueError, IndexError):
            pass
    try:
        r = subprocess.run([sys.executable, str(POOL), *args],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"resource pool timed out after {timeout:g}s: {' '.join(args)}") from e
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"resource pool returned invalid JSON ({r.returncode}): "
            f"{r.stderr.strip() or r.stdout.strip()}"
        ) from e
    if r.returncode != 0 and data.get("status") not in ("locked", "lost"):
        raise RuntimeError(
            f"resource pool failed ({r.returncode}): {data.get('error') or r.stderr.strip()}"
        )
    return data


def annotate_assignment(agent: str, session: str | None, **fields):
    p = state_path("tasks.json", session)
    def update(data):
        if agent not in data or not isinstance(data[agent], dict):
            raise StateError(f"cannot annotate missing assignment for {agent!r}")
        data[agent].update(fields)
        return data
    update_json(p, {}, update)


def remove_assignment(agent: str, session: str | None):
    p = state_path("tasks.json", session)
    removed = []
    def update(data):
        removed.append(data.pop(agent, None))
        return data
    update_json(p, {}, update)
    return removed[0]


def approval_args(executable: str, mode: str, read_only: bool = False) -> list[str]:
    """Translate one fleet policy into each supported CLI's safe permission flags."""
    if read_only:
        mappings = {
            "codex": ["--sandbox", "read-only", "--ask-for-approval", "never"],
            "claude": ["--permission-mode", "plan"],
            "agy": ["--mode", "plan"],
        }
        if executable not in mappings:
            raise SystemExit(
                f"--read-only is unsupported for {executable}: no verified enforced mapping"
            )
        return mappings[executable]
    modes = {
        "codex": {
            "auto": ["--sandbox", "danger-full-access", "--ask-for-approval", "never"]
                    if sys.platform == "darwin" else ["--approve-for-me"],
            "edit": ["--sandbox", "workspace-write", "--ask-for-approval", "on-request"],
            "manual": ["--sandbox", "workspace-write", "--ask-for-approval", "untrusted"],
            "plan": ["--sandbox", "read-only", "--ask-for-approval", "never"],
            "default": ["--sandbox", "workspace-write", "--ask-for-approval", "untrusted"],
        },
        "claude": {
            "auto": ["--permission-mode", "auto"],
            "edit": ["--permission-mode", "acceptEdits"],
            "manual": ["--permission-mode", "manual"],
            "plan": ["--permission-mode", "plan"],
            "default": ["--permission-mode", "manual"],
        },
        "agy": {
            # agy 'accept-edits' auto-accepts file edits only; shell commands still prompt.
            # There is no non-bypass agy flag for shell approval, so 'auto' == 'edit' here.
            "auto": ["--mode", "accept-edits"],
            "edit": ["--mode", "accept-edits"],
            "plan": ["--mode", "plan"],
        },
    }
    # OMP does not have a verified equivalent. Keep its native defaults.
    return modes.get(executable, {}).get(mode, [])


def cli_argv(spec: str, task: str | None = None,
             approval_mode: str = "auto", read_only: bool = False,
             model: str | None = None) -> list[str]:
    """Build a CLI invocation from a validated identifier, never caller-supplied flags."""
    try:
        executable = cli_id(spec)
    except argparse.ArgumentTypeError as e:
        raise SystemExit(str(e)) from e
    argv = [executable]
    if executable == "codex":
        argv.append("--no-alt-screen")
    if model is not None:
        try:
            val_model = validate_model_identifier(model)
        except argparse.ArgumentTypeError as e:
            raise SystemExit(str(e)) from e
        if executable == "agy":
            resolved_model = resolve_agy_model(val_model)
            argv.extend(["--model", resolved_model])
        else:
            # For codex, claude, and omp, pass the validated identifier through
            # directly and let the CLI validate/reject it, as only AGY exposes
            # an enumeration to validate against.
            argv.extend(["--model", val_model])
    argv.extend(approval_args(executable, approval_mode, read_only))
    if task:
        argv += ["-i", task] if executable == "agy" else [task]
    return argv


# ---------------- account profiles ----------------
#
# A profile names an account (work, personal, ...) and maps each CLI to the environment
# that isolates that account, mirroring shell aliases such as
# `codex-work() { CODEX_HOME=~/.codex-work command codex "$@" }`. Aliases do not reach
# Herdr panes, so the environment is applied to the worker's tab instead.
PROFILES_FILE = Path(os.environ.get(
    "AGENT_HERDER_PROFILES_FILE",
    str(Path.home() / ".config" / ".agents" / "profiles.json"),
))
NO_PROFILE = "none"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# AGY has no account switch of its own (it ignores ANTIGRAVITY_APP_DATA_DIR), so an AGY
# profile names an account home instead, and the worker's tab routes `agy` through
# scripts/agy_profile/bin/agy. That wrapper gives only agy the account HOME; the startup
# files in agy_profile/zdotdir restore the real HOME in every shell agy starts.
AGY_PROFILE_DIR = Path(__file__).resolve().parent / "agy_profile"
AGY_ACCOUNT_HOME = "AGY_ACCOUNT_HOME"
AGY_TOKEN_FILE = Path(".gemini") / "antigravity-cli" / "antigravity-oauth-token"
# Set by the runner or the wrapper; a profile that set them would break the restore.
AGY_RESERVED_ENV = frozenset({"ZDOTDIR", "AGY_SHIM_DIR", "AGY_REAL_HOME", "AGY_REAL_ZDOTDIR",
                              "BASH_ENV", "HOME", "ANTIGRAVITY_APP_DATA_DIR"})


def profile_name(value: str) -> str:
    return identifier(value, "profile name")


def load_profiles(path: Path | None = None) -> dict:
    """Load and validate the profiles file; a missing file means no profiles."""
    path = path or PROFILES_FILE
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"default": None, "profiles": {}}
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"cannot load profiles file {path}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("profiles", {}), dict):
        raise SystemExit(f"invalid profiles file {path}: expected {{\"default\", \"profiles\"}}")
    profiles = data.get("profiles", {})
    default = data.get("default")
    if default is not None and (not isinstance(default, str) or default not in profiles):
        raise SystemExit(f"invalid profiles file {path}: default {default!r} is not a defined profile")
    for name, clis in profiles.items():
        if name == NO_PROFILE or not IDENTIFIER_RE.fullmatch(name):
            raise SystemExit(f"invalid profiles file {path}: bad profile name {name!r}")
        if not isinstance(clis, dict):
            raise SystemExit(f"invalid profiles file {path}: profile {name!r} must map CLIs to env")
        for cli, env in clis.items():
            if cli not in CLI_IDS:
                raise SystemExit(f"invalid profiles file {path}: profile {name!r} has unknown CLI {cli!r}")
            if not isinstance(env, dict) or not env:
                raise SystemExit(f"invalid profiles file {path}: {name}.{cli} must be a non-empty env object")
            for key, value in env.items():
                if not ENV_NAME_RE.fullmatch(key) or key.upper().startswith("HERDR_"):
                    raise SystemExit(f"invalid profiles file {path}: {name}.{cli} has forbidden env name {key!r}")
                if not isinstance(value, str) or any(c in value for c in "\0\n\r"):
                    raise SystemExit(f"invalid profiles file {path}: {name}.{cli}.{key} must be a single-line string")
            if cli == "agy":
                reserved = sorted(AGY_RESERVED_ENV & set(env))
                if reserved:
                    raise SystemExit(f"invalid profiles file {path}: {name}.agy must not set {', '.join(reserved)}; "
                                     f"set {AGY_ACCOUNT_HOME} to the account's home folder")
                if AGY_ACCOUNT_HOME not in env:
                    raise SystemExit(f"invalid profiles file {path}: {name}.agy must set {AGY_ACCOUNT_HOME}")
    return {"default": default, "profiles": profiles}


def resolve_profile(cli: str, requested: str | None,
                    path: Path | None = None) -> tuple[str, dict[str, str]]:
    """Resolve the account profile for one launch: explicit, else default, else none.

    An explicitly requested profile must define this CLI; the configured default only
    applies to the CLIs it defines, so other CLIs launch with their normal account.
    """
    if requested == NO_PROFILE:
        return NO_PROFILE, {}
    config = load_profiles(path)
    profiles = config["profiles"]
    if requested is not None:
        if requested not in profiles:
            known = ", ".join(sorted(profiles)) or "none defined"
            raise SystemExit(f"unknown profile {requested!r} (known: {known}; file: {path or PROFILES_FILE})")
        if cli not in profiles[requested]:
            raise SystemExit(f"profile {requested!r} does not define CLI {cli!r}")
        name = requested
    elif config["default"] and cli in profiles[config["default"]]:
        name = config["default"]
    else:
        return NO_PROFILE, {}
    env = {k: os.path.expanduser(os.path.expandvars(v)) for k, v in profiles[name][cli].items()}
    if cli == "agy":
        # Without its token file agy would open a browser login in an unattended pane.
        token = Path(env[AGY_ACCOUNT_HOME]) / AGY_TOKEN_FILE
        if not token.is_file():
            raise SystemExit(f"profile {name!r}: agy account is not logged in ({token} missing); "
                             f"log in once with `AGY_ACCOUNT_HOME={env[AGY_ACCOUNT_HOME]} "
                             f"{AGY_PROFILE_DIR / 'bin' / 'agy'}`")
    return name, env


def tab_env(cli: str, profile_env: dict[str, str]) -> dict[str, str]:
    """The environment a worker's tab needs so its CLI runs under the resolved profile."""
    if cli != "agy" or not profile_env:
        return dict(profile_env)
    return {**profile_env,
            "ZDOTDIR": str(AGY_PROFILE_DIR / "zdotdir"),
            "AGY_SHIM_DIR": str(AGY_PROFILE_DIR / "bin")}


def task_contract(task: str, resources: list[str], read_only: bool,
                  agent: str | None = None, session: str | None = None,
                  cwd: str | Path | None = None) -> str:
    scope = "read-only; do not modify files" if read_only else \
        "write only within these leased resources: " + ", ".join(resources)
    # Three routes, because two of them fail silently on some CLIs: a sandbox that discards
    # writes outside the project root loses the first, and a pane that wraps mid-token
    # corrupts the third. Naming all three means one of them lands.
    repo_hp = repo_handoff_path(agent, cwd) if agent else None
    if agent and repo_hp is not None:
        handoff_instruction = (
            f"- Write your handoff JSON to `{handoff_path(agent, session)}`. If your tools "
            f"cannot write outside this project, write it to `{repo_hp}` instead -- the runner "
            "reads either one. Then end with exactly `FLEET_HANDOFF: {json}` where json "
            "contains: "
        )
    elif agent:
        handoff_instruction = (
            f"- Write your handoff JSON to `{handoff_path(agent, session)}` and end with "
            "exactly `FLEET_HANDOFF: {json}` where json contains: "
        )
    else:
        handoff_instruction = (
            "- End with exactly `FLEET_HANDOFF: {json}` where json contains: "
        )
    return (
        f"{task}\n\n"
        "Fleet contract:\n"
        f"- Scope: {scope}.\n"
        "- Do not run git checkout/switch/reset/clean/stash, create/remove worktrees, "
        "or discard unrelated changes. Escalate if one is required.\n"
        "- Stage and commit only your assigned leased paths by name (e.g. `git add <path>`); never run `git add -A`, `git add .`, or commit unleased files.\n"
        "- Keep investigation task-local; ask for missing decisions instead of broad exploration.\n"
        # Without these two, an agent routes around its own edit tool and writes
        # files with `cat > path << EOF`, which the risk policy refuses as compound
        # shell. Observed with AGY: every heredoc attempt was a NEW file, while
        # edits to existing files went through the edit tool and never prompted.
        "- Create and modify files, including new ones, only with your own file "
        "edit tool. Shell redirects and here-documents are refused by the approval "
        "policy and will stall you, so retrying one in any form wastes the run.\n"
        "- Run one plain command per shell call. Do not chain with `;`, `&&` or "
        "`||`, do not pipe, do not redirect, and do not use command substitution; "
        "compound shell is not eligible for automatic approval.\n"
        f"{handoff_instruction}"
        "status (`completed` or `blocked`), decisions (array), changed_files (array), "
        "verification (array of {command, exit_code, output}), blockers (array), and "
        "next_inputs (array). Completion is refused without this evidence.\n"
        "- A verification entry whose passing case is a nonzero exit (for example a "
        "leak-check grep that must find nothing) adds expected_exit; it defaults to 0.\n"
        "- A verification entry whose passing case produces empty output (for example "
        "tsc --noEmit or py_compile) sets expected_empty to true; it defaults to false."
    )


def acquire_assignment(agent: str, group: str | None, resources: list[str],
                       read_only: bool, timeout: float = 0,
                       quiet: bool = False, json_mode: bool = False) -> list[str]:
    if read_only:
        if resources:
            raise SystemExit("--read-only cannot be combined with --resource")
        return []
    if not group or not resources:
        raise SystemExit(
            "mutating fleet tasks require --group and at least one --resource; "
            "use --read-only only for work that cannot modify the workspace"
        )
    if len(resources) != len(set(resources)):
        raise SystemExit("duplicate --resource values are not allowed")
    acquired = []
    for resource in resources:
        result = pool("acquire", "--resource", resource, "--agent", agent,
                      "--group", group, "--timeout", str(timeout))
        if result.get("status") not in ("acquired", "already_held"):
            for held in acquired:
                pool("release", "--resource", held, "--agent", agent, "--group", group)
            if json_mode or not quiet:
                print(json.dumps({"agent": agent, "assigned": False,
                                  "blocked_on": resource, "pool": result}, indent=2))
            sys.exit(3)
        acquired.append(resource)
    return acquired


def release_resources(agent: str, group: str, resources: list[str]):
    failures = []
    for resource in resources:
        result = pool("release", "--resource", resource, "--agent", agent, "--group", group)
        if result.get("status") not in ("released", "dequeued", "not_found"):
            failures.append({"resource": resource, "result": result})
    if failures:
        raise RuntimeError(f"failed to release resources: {failures}")


def release_agent_strict(agent: str, group: str) -> dict:
    result = pool("release-agent", "--agent", agent, "--group", group)
    if result.get("status") != "ok" or not isinstance(result.get("freed", []), list):
        raise RuntimeError(f"resource release failed for {agent}: {result}")
    return result


HANDOFF_FIELDS = {
    "status", "decisions", "changed_files", "verification", "blockers", "next_inputs",
}

HANDOFF_MARKER = "FLEET_HANDOFF:"
DEFAULT_MAX_HANDOFF_CHARS = 16000
HANDOFF_STALENESS_TOLERANCE = 2.0
TERMINAL_BOUNDARY = re.compile(
    r"^[ \t]*[─━═]{3,}(?:[ \t]+Worked for "
    r"(?:(?:\d+h[ \t]+)?(?:\d+m[ \t]+)?\d+s)"
    r"[ \t]+[─━═]{3,})?[ \t]*$",
    re.MULTILINE,
)


def _dewrap(text: str, joiner: str = "") -> str:
    return re.sub(r"\n[ \t]*", joiner, text)


def _handoff_candidates(payload: str) -> tuple[str, ...]:
    """Keep terminal boundaries intact while de-wrapping agent-rendered JSON.

    De-wrapping is lossy and cannot be made exact: a hard wrap falling on a space inside a
    string value is indistinguishable from one falling mid-token, so joining with "" turns
    `"git status --short"` into `"git status--short"` while joining with " " would break a
    mid-token wrap. The empty join stays authoritative -- guessing would fabricate evidence
    the agent never printed -- and `_dewrap_is_ambiguous` reports when the join could have
    dropped a character.
    """
    boundary = next(TERMINAL_BOUNDARY.finditer(payload), None)
    if boundary:
        prefix = _dewrap(payload[:boundary.start()])
        return payload.lstrip(), (prefix + payload[boundary.start():]).lstrip()
    return payload.lstrip(), _dewrap(payload).lstrip()


def _dewrap_is_ambiguous(payload: str) -> bool:
    """True when a wrap fell inside a string value, so the tight join may have eaten a space.

    Deliberately imprecise in one direction: a wrap that landed mid-token rejoins exactly
    and still reports True, because nothing in the captured text distinguishes it from a
    wrap that ate a space -- the pane strips the trailing space either way. Wraps between
    JSON tokens never report True, so the warning stays off ordinary pretty-printed output.
    """
    boundary = next(TERMINAL_BOUNDARY.finditer(payload), None)
    subject = payload[:boundary.start()] if boundary else payload
    if "\n" not in subject:
        return False
    decoder = json.JSONDecoder()
    try:
        tight, _ = decoder.raw_decode(_dewrap(subject).lstrip())
        spaced, _ = decoder.raw_decode(_dewrap(subject, " ").lstrip())
    except json.JSONDecodeError:
        return False
    return tight != spaced


def trailing_prose(suffix: str) -> str:
    """Agent text after the handoff JSON, excluding terminal chrome.

    The handoff is meant to be the agent's last word, so prose after it can
    contradict the evidence ("I also changed another file"). Refusing it blocked
    correct handoffs every time a CLI signed off or redrew its prompt, so it is
    surfaced to the herder instead of refused -- and never silently dropped.
    """
    lines = []
    for line in suffix.splitlines():
        if TERMINAL_BOUNDARY.fullmatch(line):
            break
        if line.strip():
            lines.append(line.strip())
    return " ".join(lines)


# Terminal chrome an agent draws between the marker and its JSON. The contract
# asks for `FLEET_HANDOFF: {json}` on one line, but agents routinely put the
# marker on its own line and start the object after a horizontal rule. Refusing
# that layout discards a correct, verified handoff over cosmetics, so skip a
# bounded run of chrome-only lines before decoding.
CHROME_ONLY = re.compile(r"[\u2500-\u257F\-=_~*\u2022\s]+")
MAX_CHROME_LINES = 4


def _skip_chrome_before_json(payload: str) -> str:
    consumed = 0
    chrome_seen = 0
    for line in payload.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            consumed += len(line)
            continue
        if stripped.startswith("{"):
            break
        if chrome_seen < MAX_CHROME_LINES and CHROME_ONLY.fullmatch(stripped):
            chrome_seen += 1
            consumed += len(line)
            continue
        break
    return payload[consumed:]


def _validate_handoff_dict(handoff: dict, assignment: dict, expected_agent: str | None = None):
    if not isinstance(handoff, dict) or not HANDOFF_FIELDS.issubset(handoff):
        missing = sorted(HANDOFF_FIELDS - set(handoff) if isinstance(handoff, dict) else HANDOFF_FIELDS)
        raise SystemExit(f"completion refused: handoff missing structured fields {missing}; locks retained")
    for field in ("decisions", "changed_files", "verification", "blockers", "next_inputs"):
        if not isinstance(handoff[field], list):
            raise SystemExit(f"completion refused: handoff.{field} must be an array; locks retained")
    if "reviewed_approvals" in handoff:
        if not isinstance(handoff["reviewed_approvals"], list) or not all(
            isinstance(r, dict)
            and isinstance(r.get("who"), str) and r["who"]
            and isinstance(r.get("subject"), str)
            and (isinstance(r.get("rule"), str) or r.get("rule") is None)
            and (isinstance(r.get("timestamp"), str) or isinstance(r.get("at"), str))
            and (isinstance(r.get("choice"), str) or isinstance(r.get("option"), str))
            for r in handoff["reviewed_approvals"]
        ):
            raise SystemExit("completion refused: handoff.reviewed_approvals must be an array of valid approval records; locks retained")
    if handoff.get("status") == "done":
        handoff["status"] = "completed"
    if handoff["status"] != "completed":
        raise SystemExit(
            f"completion refused: handoff status is {handoff['status']!r}, not 'completed'; locks retained"
        )
    if handoff["blockers"]:
        raise SystemExit("completion refused: completed handoff still lists blockers; locks retained")
    if expected_agent and isinstance(handoff.get("agent"), str) and handoff["agent"] != expected_agent:
        raise SystemExit(
            f"completion refused: handoff agent {handoff['agent']!r} does not match expected agent {expected_agent!r}; locks retained"
        )
    evidence = handoff["verification"]
    if not all(isinstance(v, dict) and isinstance(v.get("command"), str)
               and v.get("command", "").strip()
               and type(v.get("exit_code")) is int
               and type(v.get("expected_exit", 0)) is int
               and type(v.get("expected_empty", False)) is bool for v in evidence):
        raise SystemExit(
            "completion refused: each verification entry needs string command, integer exit_code, "
            "integer expected_exit when present, and boolean expected_empty when present; locks retained"
        )
    for v in evidence:
        cmd = v.get("command", "").strip()
        # A whole-command `<...>` is a template slot echoed back from a brief or
        # re-prompt, never a command the worker actually ran.
        if cmd in {"...", "…", "none"} or re.fullmatch(r"<[^<>]+>", cmd):
            raise SystemExit(
                f"completion refused: placeholder verification command {cmd!r} is invalid; locks retained"
            )
    # `expected_exit` defaults to 0 and exists for checks whose passing case is a
    # nonzero exit -- a leak-check `grep` exits 1 when it finds nothing. The agent
    # declares it, so read it as part of the evidence rather than as a guarantee.
    failed = [v for v in evidence if v["exit_code"] != v.get("expected_exit", 0)]
    if failed:
        raise SystemExit(
            f"completion refused: verification exits do not match expectations {failed}; locks retained"
        )
    missing_output = [
        v.get("command") or str(v)
        for v in evidence
        if not v.get("expected_empty", False)
        and (not isinstance(v.get("output"), str) or not v.get("output", "").strip())
    ]
    if missing_output:
        raise SystemExit(
            f"completion refused: verification entry missing captured output for {missing_output}; locks retained"
        )
    required = assignment.get("verification", [])
    reported = {v["command"] for v in evidence}
    missing = [command for command in required if command not in reported]
    if missing:
        raise SystemExit(
            f"completion refused: required verification evidence missing for {missing}; locks retained"
        )
    if not evidence:
        raise SystemExit("completion refused: task has no verification evidence; locks retained")


def parse_handoff_file(path: Path | str, assignment: dict, expected_agent: str | None = None) -> tuple[dict, str]:
    """Read, parse, and validate a structured handoff directly from a worker's handoff file."""
    p = Path(path)
    if expected_agent is not None:
        expected_filename = f"handoff-{expected_agent}.json"
        if p.name != expected_filename:
            raise SystemExit(
                f"completion refused: handoff file {p.name!r} does not belong to agent {expected_agent!r}; locks retained"
            )
    if not p.is_file():
        raise SystemExit(f"completion refused: handoff file not found: {p}; locks retained")
    try:
        stat = p.stat()
        raw = sanitize_pane_text(p.read_text())
    except OSError as e:
        raise SystemExit(f"completion refused: cannot read handoff file {p}: {e}; locks retained") from e

    task_time = assignment.get("task_time")
    if task_time is not None and stat.st_mtime < task_time - HANDOFF_STALENESS_TOLERANCE:
        raise SystemExit(
            f"completion refused: handoff file {p} is stale (modified before current task started); locks retained"
        )

    stripped = raw.strip()
    if not stripped:
        raise SystemExit("completion refused: handoff file is empty; locks retained")

    if HANDOFF_MARKER in stripped:
        idx = stripped.rfind(HANDOFF_MARKER)
        payload = stripped[idx + len(HANDOFF_MARKER):].strip()
    else:
        payload = stripped

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise SystemExit(f"completion refused: invalid FLEET_HANDOFF JSON ({e}); locks retained") from e

    _validate_handoff_dict(data, assignment, expected_agent)
    canonical = "FLEET_HANDOFF: " + json.dumps(data, sort_keys=True)
    return data, canonical


def parse_handoff(text: str, assignment: dict) -> tuple[dict, str]:
    """Parse and validate the final structured handoff before freeing ownership."""
    markers = list(re.finditer(
        rf"(?m)^[ \t]*(?:•[ \t]+)?{re.escape(HANDOFF_MARKER)}", text
    ))
    if not markers:
        raise SystemExit("completion refused: missing `FLEET_HANDOFF: {json}`; locks retained")
    # Pane history can contain failed/old attempts. Only the last line-level
    # marker is authoritative; everything before it is historical context.
    payload = _skip_chrome_before_json(text[markers[-1].end():])
    decoder = json.JSONDecoder()
    decoded = None
    parsed = False
    last_error = None
    # Agent-rendered output can hard-wrap a single-line JSON object. Parse the
    # literal form first, then de-wrap only the content before terminal chrome.
    trailing = ""
    for candidate in _handoff_candidates(payload):
        try:
            decoded, end = decoder.raw_decode(candidate)
            trailing = trailing_prose(candidate[end:])
            parsed = True
            break
        except json.JSONDecodeError as e:
            last_error = str(e)
    if not parsed:
        raise SystemExit(
            f"completion refused: invalid FLEET_HANDOFF JSON ({last_error}); locks retained"
        )
    handoff = decoded
    _validate_handoff_dict(handoff, assignment)
    if trailing:
        print(f"warning: agent text after the handoff, not covered by its evidence: {trailing}",
              file=sys.stderr)
    if _dewrap_is_ambiguous(payload):
        # The pane hard-wrapped inside a string value, so rejoining it may have eaten a space.
        # Say so: the evidence is the agent's, but this rendering of it may not be exact.
        print("warning: handoff was rebuilt from hard-wrapped pane output and a wrap fell "
              "inside a string value; command text may be missing a space. Compare it "
              "against the required verification before trusting it.", file=sys.stderr)
    canonical = "FLEET_HANDOFF: " + json.dumps(handoff, sort_keys=True)
    return handoff, canonical


def has_completion_evidence(name: str, session: str | None = None, max_chars: int = DEFAULT_MAX_HANDOFF_CHARS) -> bool:
    """Check if structured completion evidence exists for the agent and is within cap."""
    assignment = get_assignment(name, session)
    for hf in (handoff_path(name, session),
               repo_handoff_path(name, (assignment or {}).get("cwd"))):
        if hf is None or not hf.is_file():
            continue
        try:
            data, canonical = parse_handoff_file(hf, assignment, expected_agent=name)
            if data and len(canonical) <= max_chars:
                return True
        except (SystemExit, Exception):
            pass
    try:
        txt = read_text(name, session, lines=100, source="recent-unwrapped")
    except (SystemExit, Exception):
        try:
            txt = read_text(name, session, lines=100, source="visible")
        except (SystemExit, Exception):
            txt = ""
    if txt and HANDOFF_MARKER in txt:
        try:
            data, canonical = parse_handoff(txt, assignment)
            if data and len(canonical) <= max_chars:
                return True
        except (SystemExit, Exception):
            pass
    return False


# ---------------- commands ----------------

def cmd_spawn(a):
    """Create a dedicated tab, start the CLI in it, drop the stray pane.

    One tab per agent is mandatory: stacking N panes in one tab shrinks each to
    a couple of rows, and a TUI agent cannot render its approval prompt there.
    """
    a.cwd = validate_cwd(a.cwd)
    handoff_path(a.name, a.session).unlink(missing_ok=True)
    if (stale_repo_hf := repo_handoff_path(a.name, a.cwd)):
        stale_repo_hf.unlink(missing_ok=True)
    if (stale_raw := failed_handoff_raw_path(a.name, a.cwd)):
        stale_raw.unlink(missing_ok=True)
    policy = load_policy()
    if a.task:
        cls = check_task_text(a.task, policy)
        if cls["risk"] == "high":
            print(json.dumps({"agent": a.name, "spawned": False,
                              "refused": "task matches a deny rule", **cls}, indent=2))
            sys.exit(2)
    model = getattr(a, "model", None)
    profile, profile_env = resolve_profile(a.cli, getattr(a, "profile", None))
    agy_allowlist = install_agy_allowlist(policy, profile_env) if a.cli == "agy" else None
    preview = task_contract(a.task, a.resource, a.read_only, agent=a.name, session=a.session,
                            cwd=a.cwd) if a.task else None
    cli_argv(a.cli, preview, a.approval_mode, a.read_only, model=model)
    if find_agent(a.name, a.session):
        raise SystemExit(f"agent name already exists in this session: {a.name}")
    leased = []
    preserved = set(getattr(a, "preserve_resources", []) or [])
    if a.task:
        leased = acquire_assignment(a.name, a.group, a.resource, a.read_only,
                                    a.lock_timeout, getattr(a, "quiet", False),
                                    json_mode=getattr(a, "json", False))
    initial = task_contract(a.task, leased, a.read_only, agent=a.name, session=a.session,
                            cwd=a.cwd) if a.task else None
    if initial:
        initial = initial.replace("\n", " ")
    argv = cli_argv(a.cli, None, a.approval_mode, a.read_only, model=model)

    tab_id = None
    pane_id = None
    workspace_id = None
    try:
        existing_assignment = get_assignment(a.name, a.session)
        existing_ws = existing_assignment.get("workspace") if existing_assignment else None
        workspace_id = getattr(a, "workspace_id", None) or existing_ws or current_herder_workspace(a.session)
        env_args = [arg for k, v in tab_env(a.cli, profile_env).items()
                    for arg in ("--env", f"{k}={v}")]
        tab = herdr(["tab", "create", "--workspace", workspace_id,
                     "--cwd", a.cwd, "--label", a.name, "--no-focus", *env_args],
                    a.session, check=True)
        tab_id = tab["result"]["tab"]["tab_id"]
        pane_id = tab["result"]["root_pane"]["pane_id"]
        start_cmd = ["agent", "start", a.name, "--kind", a.cli, "--pane", pane_id,
                     "--", *argv[1:]]
        start_agent_when_ready(start_cmd, a.session)
        ag = find_agent(a.name, a.session)
        if not ag:
            raise RuntimeError(f"agent {a.name} did not register")
        if a.task:
            set_task(a.name, a.task, a.session, a.group, leased, a.read_only,
                     "plan" if a.read_only else a.approval_mode,
                     workspace=workspace_id, model=model, profile=profile, cwd=a.cwd,
                     bead_id=getattr(a, "bead_id", None))
            settle_samples = getattr(a, "settle_samples", DEFAULT_SETTLE_SAMPLES)
            settle_delay = getattr(a, "settle_delay", DEFAULT_SETTLE_DELAY)
            pre_submit_check(a.name, a.session, a.cwd, policy, task=a.task,
                             read_only=a.read_only, settle_samples=settle_samples,
                             settle_delay=settle_delay)
            if initial:
                # Belt-and-suspenders: after our regex-based pre_submit_check passes,
                # ask Herdr's own state machine to confirm idle before we send the prompt.
                # This closes the narrow window where the banner regex says "ready" but
                # Herdr's process-authority check hasn't transitioned yet.
                # Non-fatal: if wait times out or errors we still attempt _submit as usual.
                try:
                    herdr(["agent", "wait", a.name, "--until", "idle", "--timeout", "3000"],
                          a.session)
                except (Exception, SystemExit):
                    pass
                # _submit returning False means every path (agent prompt, then the pane
                # fallback) failed. Spawning a pane that never receives its task leaves a
                # worker holding leases with nothing to do, so fail the spawn instead.
                if not _submit(a.name, a.session, initial, task=a.task):
                    raise RuntimeError(f"agent {a.name} did not accept its initial task")
        else:
            set_task(a.name, None, a.session, a.group, leased, a.read_only,
                     "plan" if a.read_only else a.approval_mode,
                     workspace=workspace_id, model=model, profile=profile, cwd=a.cwd,
                     bead_id=getattr(a, "bead_id", None))
    except BaseException:
        if a.task:
            try:
                remove_assignment(a.name, a.session)
            except BaseException:
                pass
        if pane_id:
            try:
                herdr(["pane", "close", pane_id], a.session, check=True)
            except BaseException:
                pass
        if tab_id:
            try:
                herdr(["tab", "close", tab_id], a.session, check=True)
            except BaseException:
                pass
        if a.group:
            release_resources(a.name, a.group, [r for r in leased if r not in preserved])
        raise

    result = {"agent": a.name, "cli": a.cli, "session": session_key(a.session),
              "workspace": workspace_id, "tab": tab_id,
              "pane": ag["pane_id"],
              "group": a.group, "resources": leased, "read_only": a.read_only,
              "approval_mode": "plan" if a.read_only else a.approval_mode}
    if model:
        result["model"] = model
    result["profile"] = profile
    if agy_allowlist is not None:
        result["agy_allowlist"] = agy_allowlist
    spawn_record = {"name": a.name, "agent": a.cli, "cli": a.cli, "model": model,
                    "profile": profile, "group": a.group, "resources": leased, "read_only": a.read_only,
                    "spawned_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    def _record_spawn(d):
        d[a.name] = spawn_record
        return d
    update_json(state_path("spawned_agents.json", a.session), {}, _record_spawn)
    if getattr(a, "json", False) or not getattr(a, "quiet", False):
        print(json.dumps(result))
    return result


def fleet_rows(session=None, settle_samples: int | None = None) -> list[dict]:
    if settle_samples is None:
        settle_samples = DEFAULT_SETTLE_SAMPLES
    policy = load_policy()
    rows = []
    named_agents = [ag for ag in agents(session) if ag.get("name")]
    for ag in sorted(named_agents, key=lambda x: x["name"]):
        name = ag["name"]
        txt = read_text(name, session, lines=45)
        pend = pending_prompt(txt)
        assignment = get_assignment(name, session)
        task = assignment.get("task")
        read_only = assignment.get("read_only", False)
        if pend and pend.get("kind") == "trust":
            cls = decide_trust_dialog(txt, assignment.get("cwd"))
            cls["rule"] = None
            cls["reviewable"] = False
        elif pend:
            cls = classify_text(pend["subject"], policy, task, read_only=read_only, kind=pend.get("kind"))
        else:
            cls = None
        raw_state = ag.get("agent_status")
        health = health_of(txt) or "ok"
        is_parked = assignment.get("lifecycle") == "parked"

        has_handoff = False
        if not is_parked and not pend and health == "ok":
            has_handoff = has_completion_evidence(name, session)

        if not is_parked and not pend and health == "ok" and task and not has_handoff and not has_foreground_execution(txt):
            if _is_startup_banner_only(txt):
                health = "delivery-error"

        # Determine agent state accurately:
        # 1. A parked agent is reported as parked.
        # 2. A pane waiting on a prompt or dialog is blocked, never idle or done.
        # 3. An agent with no assigned task is unassigned, never idle or working.
        # 4. An unhealthy agent is blocked.
        # 5. A completed pane that produced structured handoff evidence is classified as done.
        # 6. Panes with active foreground execution indicators (spinners, running markers)
        #    are classified as working, not done.
        # 7. For done/idle candidate states, require stability across N consecutive samples.
        # 8. Otherwise use the reported agent status.
        if is_parked:
            state = "parked"
        elif has_handoff:
            state = "done"
        elif has_foreground_execution(txt):
            state = "working"
            pend = None
            cls = None
        elif pend or health == "trust-prompt":
            state = "blocked"
        elif not task:
            state = "unassigned"
        elif health != "ok":
            state = "blocked"
        elif raw_state in ("done", "idle"):
            if not is_settled_frame(txt):
                state = "settling"
            elif settle_samples <= 1:
                state = raw_state
            else:
                settled_ok = True
                for _ in range(settle_samples - 1):
                    time.sleep(DEFAULT_SETTLE_DELAY)
                    cur_ag = find_agent(name, session)
                    cur_raw = cur_ag.get("agent_status") if cur_ag else None
                    cur_txt = read_text(name, session, lines=45)
                    cur_health = health_of(cur_txt) or "ok"
                    cur_pend = pending_prompt(cur_txt)
                    if cur_pend or cur_health == "trust-prompt":
                        pend = cur_pend
                        if pend and pend.get("kind") == "trust":
                            cls = decide_trust_dialog(cur_txt, assignment.get("cwd"))
                            cls["rule"] = None
                            cls["reviewable"] = False
                        elif pend:
                            cls = classify_text(pend["subject"], policy, task, read_only=read_only, kind=pend.get("kind"))
                        else:
                            cls = None
                        health = cur_health
                        state = "blocked"
                        settled_ok = False
                        break
                    if cur_health == "ok" and task and not has_completion_evidence(name, session) and not has_foreground_execution(cur_txt) and _is_startup_banner_only(cur_txt):
                        cur_health = "delivery-error"
                    if cur_health != "ok":
                        health = cur_health
                        state = "blocked"
                        settled_ok = False
                        break
                    if has_completion_evidence(name, session):
                        has_handoff = True
                        state = "done"
                        settled_ok = True
                        break
                    if cur_raw == "working" or has_foreground_execution(cur_txt):
                        raw_state = "working"
                        state = "working"
                        settled_ok = False
                        break
                    if not is_settled_frame(cur_txt) or cur_raw not in ("done", "idle"):
                        state = "settling"
                        settled_ok = False
                        break
                if settled_ok:
                    state = "done" if has_handoff else raw_state
        else:
            state = raw_state

        revs = assignment.get("reviewed_approvals", [])

        rows.append({
            "agent": name, "cli": ag.get("agent"), "state": state,
            "health": health,
            "pending": (pend["subject_display"][:100] if pend else None),
            "pending_digest": (subject_digest(pend["subject"]) if pend and pend.get("subject") else None),
            "kind": (pend["kind"] if pend else None),
            "action": (cls["action"] if cls else None),
            "rule": (cls["rule"] if cls else None),
            "resources": assignment.get("resources", []),
            "approval_mode": assignment.get("approval_mode"),
            "parked": is_parked,
            "reviewed_approvals": len(revs),
            "has_handoff": has_handoff,
        })
    return rows


def renew_fleet_leases(rows: list[dict], session=None) -> list[dict]:
    lost = []
    for row in rows:
        assignment = get_assignment(row["agent"], session)
        if not assignment or assignment.get("lifecycle") != "active":
            continue
        group = assignment.get("group")
        if not group:
            continue
        for resource in assignment.get("resources", []):
            result = pool("renew", "--resource", resource, "--agent", row["agent"],
                          "--group", group)
            if result.get("status") != "renewed":
                cur = get_assignment(row["agent"], session)
                if not cur or cur.get("lifecycle") != "active":
                    continue
                lost.append({"agent": row["agent"], "resource": resource,
                             "group": group, "result": result})
    return lost


def fleet_actionable(rows: list[dict]) -> bool:
    return any(r["action"] or r["health"] != "ok"
               or r["state"] in ("blocked", "done", "unassigned", "parked") for r in rows)


def fleet_terminal(rows: list[dict]) -> bool:
    return bool(rows) and all(
        r["state"] in ("done", "idle") and r["health"] == "ok" and not r["action"]
        for r in rows
    )


def cmd_status(a):
    settle_samples = getattr(a, "settle_samples", DEFAULT_SETTLE_SAMPLES)
    rows = fleet_rows(a.session, settle_samples=settle_samples)
    sk = session_key(a.session)
    if a.json:
        print(json.dumps({"session": sk, "agents": rows}, indent=2))
        return
    print(f"Session: {sk}")
    if not rows:
        print("(no agents)")
        return
    print(f"{'AGENT':10} {'CLI':7} {'STATE':8} {'HEALTH':12} {'ACTION':9} {'RULE':16} {'REVIEWED':8} PENDING")
    for r in rows:
        rev_count = r.get("reviewed_approvals", 0)
        rev_str = str(rev_count) if rev_count > 0 else "-"
        print(f"{r['agent']:10} {(r['cli'] or '-'):7} {(r['state'] or '?'):8} "
              f"{r['health']:12} {(r['action'] or '-'):9} {(r['rule'] or '-'):16} "
              f"{rev_str:8} {(r['pending'] or '')[:60]}")
    dead = [r["agent"] for r in rows if r["health"] not in ("ok", "trust-prompt")]
    if dead:
        print(f"\nUNUSABLE: {', '.join(dead)} -- reassign their tasks; "
              f"do not keep prompting them.")


def cmd_watch(a):
    """Wait inside one process and emit one compact snapshot when attention is useful."""
    started = time.monotonic()
    sk = session_key(a.session)
    settle_samples = getattr(a, "settle_samples", DEFAULT_SETTLE_SAMPLES)
    initial = fleet_rows(a.session, settle_samples=settle_samples)
    initial_key = json.dumps(initial, sort_keys=True)
    last_renewal = 0.0
    while True:
        drained = drain_events(a.session)
        rows = fleet_rows(a.session, settle_samples=settle_samples)
        now = time.monotonic()
        if now - last_renewal >= a.renew_interval:
            lost = renew_fleet_leases(rows, a.session)
            last_renewal = time.monotonic()
            if lost:
                print(json.dumps({"event": "lock-lost", "session": sk, "fleet": rows,
                                  "lost_leases": lost}, indent=2))
                sys.exit(2)
        changed = json.dumps(rows, sort_keys=True) != initial_key
        actionable = fleet_actionable(rows)
        terminal = fleet_terminal(rows)
        hit = ((a.until == "change" and changed) or
               (a.until == "actionable" and actionable) or
               (a.until == "terminal" and terminal))
        if hit:
            print(json.dumps({"event": a.until, "session": sk,
                              "elapsed_seconds": round(time.monotonic() - started, 1),
                              "fleet": rows}, indent=2))
            return
        now = time.monotonic()
        if now - started >= a.timeout:
            print(json.dumps({"event": "timeout", "session": sk,
                              "elapsed_seconds": a.timeout,
                              "fleet": rows}, indent=2))
            sys.exit(1)

        time_to_renewal = max(0.0, a.renew_interval - (now - last_renewal))
        time_to_timeout = max(0.0, a.timeout - (now - started))
        max_wait = min(a.interval, time_to_renewal, time_to_timeout)
        if max_wait > 0.0:
            wait_for_events(a.session, timeout=max_wait)


def record_reviewed_approval(agent: str, session: str | None, record: dict):
    p = state_path("tasks.json", session)
    def update(data):
        if agent not in data or not isinstance(data[agent], dict):
            data[agent] = {"task": None, "lifecycle": "active"}
        data[agent].setdefault("reviewed_approvals", []).append(record)
        return data
    update_json(p, {}, update)


def cmd_read(a):
    lines = getattr(a, "lines", 20)
    raw = read_text(a.agent, a.session, lines, a.source)
    cleaned = sanitize_pane_text(raw).rstrip()
    print(cleaned)


def cmd_classify(a):
    txt = read_text(a.agent, a.session, 45, "recent-unwrapped" if a.unwrapped else "visible")
    pend = pending_prompt(txt)
    if not pend:
        print(json.dumps({"agent": a.agent, "pending": False}))
        return
    assignment = get_assignment(a.agent, a.session)
    if pend.get("kind") == "trust":
        cls = decide_trust_dialog(txt, assignment.get("cwd"))
        cls["rule"] = None
        cls["reviewable"] = False
    else:
        cls = classify_text(pend["subject"], load_policy(), assignment.get("task"), read_only=assignment.get("read_only", False), kind=pend.get("kind"))
    digest = subject_digest(pend["subject"])
    print(json.dumps({"agent": a.agent, "pending": True, "digest": digest, **pend, **cls}, indent=2))
    sys.exit(0 if cls["action"] == "approve" else 2)


def _probe_matches(seen_text: str, probe: str) -> bool:
    """Check if probe is present in seen_text, tolerating line wraps and mid-token splits."""
    if not probe:
        return True
    probe_norm = re.sub(r"\s+", " ", probe).strip()
    if not probe_norm:
        return True
    normalized = re.sub(r"\s+", " ", seen_text).strip()
    dewrapped = re.sub(r"\n[ \t]*", "", seen_text).strip()
    if probe_norm in normalized or probe_norm in dewrapped:
        return True

    lines = seen_text.splitlines()
    for window_size in range(1, min(7, len(lines) + 1)):
        for i in range(len(lines) - window_size + 1):
            chunk = "\n".join(lines[i:i + window_size])
            for v in _variants(chunk):
                if probe_norm in v:
                    return True
    return False


def _landed(name, session, text, pre_submit: str | None = None, task: str | None = None) -> bool:
    """Did the submitted text actually reach the pane?

    `agent prompt` returns success once Herdr has accepted the text, which is not the same
    as the CLI having received it: submitted too soon after `agent start` or dialog dismissal,
    the TUI is still initialising and the text is dropped with no error anywhere. The only
    reliable check is to read the pane back and positively find the probe text.

    In a wide pane, the head of the task is visible. In a narrow ~54-column pane (such as
    un-attached sessions created by herd.sh), long text with appended fleet contracts
    overflows the screen and scrolls the head away, leaving only the tail visible.
    Delivery is confirmed if either the head probe or the tail probe is found.

    To prevent a stale fleet contract from a prior prompt from confirming a new prompt
    that never landed, the tail probe is only accepted if it was not already present in
    the pane prior to submission (pre_submit).
    """
    full_norm = " ".join((text or "").split())
    if not full_norm:
        return True

    task_norm = " ".join((task or "").split()) if task else ""
    if not task_norm and "\n\nFleet contract:" in (text or ""):
        task_norm = " ".join(text.split("\n\nFleet contract:")[0].split())
    probe_head = task_norm[:40] if task_norm else full_norm[:40]
    probe_tail = full_norm[-40:]

    # A tail probe matching identical fleet contract text from a prior prompt must not confirm
    # a new prompt. If the tail probe was already visible in the pane before submission,
    # disqualify the tail probe so only the unique head probe can confirm delivery.
    tail_valid = True
    if probe_tail != probe_head and pre_submit:
        if _probe_matches(pre_submit, probe_tail):
            tail_valid = False

    for _ in range(64):
        time.sleep(0.25)
        seen_text = None
        try:
            seen_text = read_text(name, session, lines=80, source="recent-unwrapped")
        except (Exception, SystemExit):
            # Herdr refuses `recent-unwrapped` with "agent_not_idle" while an agent is
            # working. Fall back to `--source visible`, which Herdr allows regardless of
            # agent_status. Delivery is confirmed only by finding positive evidence of the
            # probe in the pane, never inferred from agent_status alone (both AGY and Claude Code
            # report "working" during startup before any prompt is received).
            try:
                seen_text = read_text(name, session, lines=80, source="visible")
            except (Exception, SystemExit):
                continue

        if not seen_text:
            continue

        # A pane holding only the CLI's startup banner or a modal trust dialog has not received
        # its task, even if agent_status reports "working".
        if _is_startup_banner_only(seen_text):
            continue

        pend = pending_prompt(seen_text)
        if pend and pend.get("kind") == "trust":
            continue

        # If the agent is presenting a tool/command permission prompt (e.g. pytest, bash)
        # or has active foreground execution (spinners, running markers), the task has
        # landed and is actively executing.
        if pend and pend.get("kind") != "trust":
            return True

        if has_foreground_execution(seen_text):
            return True

        # Check if head probe landed in the pane (wide pane case)
        if _probe_matches(seen_text, probe_head):
            return True

        # Check if tail probe landed in the pane (narrow pane case, if tail is not stale)
        if tail_valid and _probe_matches(seen_text, probe_tail):
            return True

    return False


def _submit(name, session, text, tries=3, task: str | None = None):
    """Send prompt to agent via herdr agent prompt (primary path), and verify it landed.

    Primary delivery: herdr agent prompt <name> <text>, followed by mandatory
    corroboration via _landed pane check to verify delivery and avoid swallowed text.

    Fallback: pane send-text (literal text) + pane send-keys enter (logical key).
    In Herdr 0.8.0 pane send-keys only accepts logical key names, not arbitrary text.
    """
    pre_submit = None
    try:
        pre_submit = read_text(name, session, lines=80, source="visible")
    except (Exception, SystemExit):
        try:
            pre_submit = read_text(name, session, lines=80, source="recent-unwrapped")
        except (Exception, SystemExit):
            pre_submit = None

    try:
        herdr(["agent", "prompt", name, text], session)
        if _landed(name, session, text, pre_submit=pre_submit, task=task):
            return True
    except (Exception, SystemExit):
        pass

    for _ in range(tries):
        try:
            herdr(["agent", "prompt", name, text], session)
            if _landed(name, session, text, pre_submit=pre_submit, task=task):
                return True
        except (Exception, SystemExit):
            pass
        try:
            pane = pane_of(name, session)
            herdr(["pane", "send-text", pane, text], session, check=True)
            time.sleep(0.25)
            herdr(["pane", "send-keys", pane, "enter"], session, check=True)
            if _landed(name, session, text, pre_submit=pre_submit, task=task):
                return True
        except (Exception, SystemExit):
            time.sleep(0.5)
    return False



def cmd_prompt(a):
    """Assign work. The task text is recorded so later prompts from this agent
    are judged against its objective, not just the keystroke in front of us."""
    cls = check_task_text(a.text, load_policy())
    if cls["risk"] == "high":
        print(json.dumps({"agent": a.agent, "submitted": False,
                          "refused": "task itself matches a deny rule", **cls},
                         indent=2))
        sys.exit(2)
    previous = get_assignment(a.agent, a.session)
    if previous and previous.get("lifecycle", "active") != "active":
        raise SystemExit("prompt refused: assignment is not active; use reset after resolving it")
    if previous.get("task") and not a.related:
        raise SystemExit(
            "prompt would reuse an existing context for a new task. Pass --related for a "
            "same-domain follow-up; otherwise use 'reset' to start a fresh context with a handoff."
        )
    try:
        pane_of(a.agent, a.session)
    except SystemExit:
        other = find_agent_other_session(a.agent, a.session)
        if other:
            raise SystemExit(
                f"agent '{a.agent}' not found in session '{a.session or 'default'}', but found in session '{other}'; "
                f"pass --session {other} --allow-cross-session"
            )
        raise
    group = a.group or previous.get("group")
    resources = a.resource or previous.get("resources", [])
    read_only = a.read_only or previous.get("read_only", False)
    acquired_by_this_call = []
    if previous:
        if group != previous.get("group") or set(resources) != set(previous.get("resources", [])):
            raise SystemExit("changing group/resources requires reset; existing state and leases preserved")
        if read_only != previous.get("read_only", False):
            raise SystemExit("changing read-only mode requires reset")
        leased = list(previous.get("resources", []))
    else:
        leased = acquire_assignment(a.agent, group, resources, read_only, a.lock_timeout)
        acquired_by_this_call = list(leased)

    try:
        handoff_path(a.agent, a.session).unlink(missing_ok=True)
        if (stale_repo_hf := repo_handoff_path(a.agent, (previous or {}).get("cwd"))):
            stale_repo_hf.unlink(missing_ok=True)
        if (stale_raw := failed_handoff_raw_path(a.agent, (previous or {}).get("cwd"))):
            stale_raw.unlink(missing_ok=True)
        contract = task_contract(a.text, leased, read_only, agent=a.agent, session=a.session,
                                 cwd=(previous or {}).get("cwd"))
        ok = _submit(a.agent, a.session, contract, task=a.text)
        if not ok:
            raise SystemExit("prompt submission could not be verified; assignment state was not changed")
        recorded_task = (previous.get("task", "") + "\n\nRelated follow-up:\n" + a.text).strip() \
            if previous else a.text
        set_task(a.agent, recorded_task, a.session, group, leased, read_only,
                 previous.get("approval_mode") if previous else None,
                 cwd=previous.get("cwd") if previous else None,
                 workspace=previous.get("workspace") if previous else None)
    except BaseException:
        if group and acquired_by_this_call:
            release_resources(a.agent, group, acquired_by_this_call)
        raise

    print(json.dumps({"agent": a.agent, "submitted": True, "task_recorded": True,
                       "group": group, "resources": leased, "read_only": read_only}))


def cmd_approve(a):
    """Approve if policy allows, or if a valid reviewed-approval overrides an eligible escalation."""
    txt = read_text(a.agent, a.session, 45)
    if has_foreground_execution(txt):
        print(json.dumps({
            "agent": a.agent,
            "approved": False,
            "reason": "agent has active foreground execution; cannot send approval keys",
            "action": "refuse",
        }))
        sys.exit(2)
    pend = pending_prompt(txt)
    if not pend:
        other = find_agent_other_session(a.agent, a.session)
        if other:
            raise SystemExit(
                f"agent '{a.agent}' not found in session '{a.session or 'default'}', but found in session '{other}'; "
                f"pass --session {other} --allow-cross-session"
            )
        print(json.dumps({"agent": a.agent, "approved": False, "reason": "no pending prompt"}))
        sys.exit(1)
    assignment = get_assignment(a.agent, a.session)
    if pend.get("kind") == "trust":
        cls = decide_trust_dialog(txt, assignment.get("cwd"))
        cls["rule"] = None
        cls["reviewable"] = False
    else:
        cls = classify_text(pend["subject"], load_policy(), assignment.get("task"), read_only=assignment.get("read_only", False), kind=pend.get("kind"))

    is_reviewed_attempt = bool(getattr(a, "reviewed_by", None) or getattr(a, "reviewed_subject", None))

    if cls["action"] == "approve":
        fresh_txt = read_text(a.agent, a.session, 45)
        if has_foreground_execution(fresh_txt):
            print(json.dumps({
                "agent": a.agent,
                "approved": False,
                "reason": "agent has active foreground execution; cannot send approval keys",
                "action": "refuse",
            }))
            sys.exit(2)
        if pend.get("kind") == "trust":
            keys = dialog_accept_keys(pend, txt)
            chosen_key = keys[0] if keys else "enter"
        else:
            chosen_key = cls.get("choice") if getattr(a, "choice", "1") == "1" and cls.get("choice") else a.choice
            keys = [chosen_key, "enter"]
        herdr(["agent", "send-keys", a.agent, *keys], a.session, check=True)
        print(json.dumps({"agent": a.agent, "approved": True, "choice": chosen_key,
                          "kind": pend["kind"],
                          "rule": cls.get("rule")}))
        return

    if not is_reviewed_attempt:
        print(json.dumps({"agent": a.agent, "approved": False, "reason": "policy",
                          "kind": pend["kind"],
                          **cls, "subject": pend["subject"][:200]}, indent=2))
        sys.exit(2)

    if not getattr(a, "reviewed_by", None):
        print(json.dumps({
            "agent": a.agent, "approved": False,
            "reason": "reviewed approval requires reviewer identity (--reviewed-by)",
            "kind": pend["kind"], **cls,
        }, indent=2))
        sys.exit(2)

    if not getattr(a, "reviewed_subject", None):
        print(json.dumps({
            "agent": a.agent, "approved": False,
            "reason": "reviewed approval requires reviewed subject/digest (--reviewed-subject)",
            "kind": pend["kind"], **cls,
        }, indent=2))
        sys.exit(2)

    # 1. Eligibility check (Decision 4 & Herder guidance)
    is_reviewable = cls.get("reviewable", False)
    if not is_reviewable:
        refusal_rule = cls.get("ineligible_rule") or cls.get("rule") or "default-deny"
        print(json.dumps({
            "agent": a.agent,
            "approved": False,
            "reason": f"rule '{refusal_rule}' is not reviewable",
            "rule": refusal_rule,
            "kind": pend["kind"],
            "action": "refuse",
        }, indent=2))
        sys.exit(2)

    # 2. Subject match check (Decision 1)
    if not subject_matches_reviewed(pend["subject"], a.reviewed_subject):
        print(json.dumps({
            "agent": a.agent,
            "approved": False,
            "reason": "reviewed subject mismatch: live subject does not match reviewed subject",
            "live_digest": subject_digest(pend["subject"]),
            "reviewed_digest": a.reviewed_subject,
            "kind": pend["kind"],
            "action": "refuse",
        }, indent=2))
        sys.exit(2)

    # 3. Persisted One-Shot Replay Prevention Check (Decision 1 & Herder guidance)
    assignment = get_assignment(a.agent, a.session)
    live_digest = subject_digest(pend["subject"])
    consumed_digests = {
        r.get("subject_digest") for r in assignment.get("reviewed_approvals", []) if r.get("consumed")
    } | {
        subject_digest(r["subject"]) for r in assignment.get("reviewed_approvals", []) if "subject" in r and r.get("consumed", True)
    }
    allow_regrant = bool(getattr(a, "regrant", False) or getattr(a, "re_grant", False))
    if live_digest in consumed_digests and not allow_regrant:
        print(json.dumps({
            "agent": a.agent,
            "approved": False,
            "reason": "reviewed approval has already been consumed (one-shot grant cannot be reused; pass --regrant to authorize repeated execution)",
            "live_digest": live_digest,
            "kind": pend["kind"],
            "action": "refuse",
        }, indent=2))
        sys.exit(2)

    # 4. Send keys to pane
    herdr(["agent", "send-keys", a.agent, a.choice, "enter"], a.session, check=True)

    # 5. Persist record in fleet state (Decision 2)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    record_rule = cls["rule"] if cls.get("rule") is not None else "default-deny"
    record = {
        "who": a.reviewed_by,
        "subject": pend["subject"],
        "subject_digest": live_digest,
        "rule": record_rule,
        "timestamp": now_str,
        "at": now_str,
        "choice": a.choice,
        "option": a.choice,
        "consumed": True,
    }
    if allow_regrant:
        record["regrant"] = True
    record_reviewed_approval(a.agent, a.session, record)

    # 6. Surface output distinctly (Decision 3)
    print(json.dumps({
        "agent": a.agent,
        "approved": True,
        "reviewed": True,
        "reviewed_by": a.reviewed_by,
        "choice": a.choice,
        "kind": pend["kind"],
        "rule": record_rule,
        "subject": pend["subject"],
        "timestamp": now_str,
    }, indent=2))


def cmd_cancel(a):
    try:
        herdr(["agent", "send-keys", a.agent, "esc"], a.session, check=True)
    except (Exception, SystemExit):
        other = find_agent_other_session(a.agent, a.session)
        if other:
            raise SystemExit(
                f"agent '{a.agent}' not found in session '{a.session or 'default'}', but found in session '{other}'; "
                f"pass --session {other} --allow-cross-session"
            )
    print(json.dumps({"agent": a.agent, "cancelled": True}))


def cmd_reject(a):
    """Reject a pending dialog/permission and either send instructions on how to continue or halt/park the agent."""
    try:
        herdr(["agent", "send-keys", a.agent, "esc"], a.session, check=True)
    except (Exception, SystemExit):
        other = find_agent_other_session(a.agent, a.session)
        if other:
            raise SystemExit(
                f"agent '{a.agent}' not found in session '{a.session or 'default'}', but found in session '{other}'; "
                f"pass --session {other} --allow-cross-session"
            )

    time.sleep(0.5)

    reason = getattr(a, "reason", None) or "Permission rejected by supervisor/policy"

    if getattr(a, "halt", False) or getattr(a, "park", False):
        # Park the agent: release its resource leases and mark it parked
        park_args = argparse.Namespace(
            agent=a.agent,
            session=a.session,
            reason=reason,
            cancel=False,  # Esc was already sent above
            group=getattr(a, "group", None),
        )
        return cmd_park(park_args)

    prompt_text = getattr(a, "prompt", None) or (
        f"The requested operation or permission was rejected ({reason}). "
        "Please continue your assigned work using an alternative approach that does not require this action, "
        "or emit your final structured FLEET_HANDOFF if no alternative is available."
    )

    submitted = _submit(a.agent, a.session, prompt_text, tries=2)
    print(json.dumps({
        "agent": a.agent,
        "rejected": True,
        "reason": reason,
        "prompt_submitted": submitted,
    }, indent=2))


def cmd_complete(a):
    """Require structured evidence, then close durable work and release leases."""
    assignment = get_assignment(a.agent, a.session)
    if not assignment:
        raise SystemExit(f"completion refused: no recorded assignment for {a.agent}")
    if assignment.get("lifecycle", "active") != "active":
        raise SystemExit("completion refused: assignment is not active; locks/state retained")
    try:
        visible = read_text(a.agent, a.session, min(a.lines, 45), "visible")
    except SystemExit:
        visible = None
    if visible:
        health = health_of(visible)
        if health:
            raise SystemExit(
                f"completion refused: agent health is {health}; resolve the prompt/error first; locks retained"
            )

    accepted_repo_handoff = None
    handoff_override = getattr(a, "handoff", None)

    if handoff_override:
        raw_override = handoff_override.strip()
        if raw_override.startswith("@"):
            override_path = Path(raw_override[1:])
            if not override_path.is_file():
                raise SystemExit(
                    f"completion refused: handoff override file not found: {override_path}; locks retained"
                )
            try:
                raw_content = override_path.read_text(encoding="utf-8")
            except OSError as e:
                raise SystemExit(
                    f"completion refused: cannot read handoff override file {override_path}: {e}; locks retained"
                ) from e
        else:
            raw_content = raw_override

        stripped = sanitize_pane_text(raw_content).strip()
        if HANDOFF_MARKER in stripped:
            idx = stripped.rfind(HANDOFF_MARKER)
            payload = stripped[idx + len(HANDOFF_MARKER):].strip()
        else:
            payload = stripped

        try:
            handoff_data = json.loads(payload)
        except json.JSONDecodeError as e:
            raise SystemExit(f"completion refused: invalid FLEET_HANDOFF JSON ({e}); locks retained") from e

        _validate_handoff_dict(handoff_data, assignment, expected_agent=a.agent)
        handoff = "FLEET_HANDOFF: " + json.dumps(handoff_data, sort_keys=True)
    else:
        hf = handoff_path(a.agent, a.session)
        repo_hf = repo_handoff_path(a.agent, assignment.get("cwd"))
        if hf.is_file():
            try:
                handoff_data, handoff = parse_handoff_file(hf, assignment, expected_agent=a.agent)
            except SystemExit:
                try:
                    raw_pane = read_text(a.agent, a.session, a.lines, "recent-unwrapped")
                except Exception:
                    try:
                        raw_pane = hf.read_text()
                    except Exception:
                        raw_pane = ""
                _persist_failed_handoff_raw(a.agent, raw_pane, assignment.get("cwd"))
                raise
        elif repo_hf is not None and repo_hf.is_file():
            try:
                handoff_data, handoff = parse_handoff_file(repo_hf, assignment, expected_agent=a.agent)
                accepted_repo_handoff = repo_hf
            except SystemExit:
                try:
                    raw_pane = read_text(a.agent, a.session, a.lines, "recent-unwrapped")
                except Exception:
                    try:
                        raw_pane = repo_hf.read_text()
                    except Exception:
                        raw_pane = ""
                _persist_failed_handoff_raw(a.agent, raw_pane, assignment.get("cwd"))
                raise
        else:
            try:
                text = read_text(a.agent, a.session, a.lines, "recent-unwrapped")
            except SystemExit:
                raise SystemExit("completion refused: agent pane unavailable and no handoff captured; locks retained")
            try:
                handoff_data, handoff = parse_handoff(text, assignment)
            except SystemExit:
                _persist_failed_handoff_raw(a.agent, text, assignment.get("cwd"))
                raise

    # Surface reviewed approvals in handoff
    if assignment.get("reviewed_approvals"):
        existing_rev = handoff_data.get("reviewed_approvals", [])
        combined = list(existing_rev)
        for r in assignment["reviewed_approvals"]:
            if r not in combined:
                combined.append(r)
        handoff_data["reviewed_approvals"] = combined
        handoff = "FLEET_HANDOFF: " + json.dumps(handoff_data, sort_keys=True)

    if len(handoff) > a.max_chars:
        raise SystemExit(f"completion refused: structured handoff exceeds {a.max_chars} characters")

    handoff_records_path = state_path("handoffs.json", a.session)
    def record_handoff(data):
        data[a.agent] = {"task": assignment.get("task"), "handoff": handoff_data,
                         "source": "lead" if handoff_override else "worker",
                         "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        return data
    update_json(handoff_records_path, {}, record_handoff)

    hf = handoff_path(a.agent, a.session)
    if hf.exists():
        try:
            hf.unlink(missing_ok=True)
        except OSError:
            pass

    # An in-repo handoff sits inside the work tree, so leaving it behind turns transport into
    # an uncommitted file the next reader has to explain. Remove the file and the directory
    # with it when nothing else is in there.
    if accepted_repo_handoff is not None:
        try:
            accepted_repo_handoff.unlink(missing_ok=True)
            parent = accepted_repo_handoff.parent
            if parent.name == REPO_HANDOFF_DIR and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
    elif (repo_hf := repo_handoff_path(a.agent, assignment.get("cwd"))):
        if repo_hf.exists():
            try:
                repo_hf.unlink(missing_ok=True)
                parent = repo_hf.parent
                if parent.name == REPO_HANDOFF_DIR and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

    failed_raw = failed_handoff_raw_path(a.agent, assignment.get("cwd"))
    if failed_raw.exists():
        try:
            failed_raw.unlink(missing_ok=True)
            parent = failed_raw.parent
            if parent.name == REPO_HANDOFF_DIR and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    group = a.group or assignment.get("group")
    try:
        freed = release_agent_strict(a.agent, group) if group else {"freed": []}
    except RuntimeError as e:
        raise SystemExit(f"completion release failed; assignment retained: {e}") from e
    try:
        annotate_assignment(a.agent, a.session, lifecycle="completed", completed_at=time.time())
    except Exception:
        remove_assignment(a.agent, a.session)

    ag = find_agent(a.agent, a.session)
    if ag and ag.get("pane_id"):
        try:
            herdr(["pane", "close", ag["pane_id"]], a.session, check=True)
        except BaseException as e:
            print(f"warning: failed to close pane for {a.agent}: {e}", file=sys.stderr)
    emit_event(a.session, a.agent, "completed", {
        "handoff": handoff_data,
        "summary": handoff_data.get("summary", "") if isinstance(handoff_data, dict) else "",
        "freed_locks": freed.get("freed", []),
    })
    print(json.dumps({
        "agent": a.agent,
        "status": "completed",
        "freed_locks": freed.get("freed", []),
        "handoff_path": str(handoff_records_path),
    }, indent=2))


def cmd_reset(a):
    """Close an old pane and spawn a fresh context seeded only with a handoff."""
    old = get_assignment(a.agent, a.session)
    old_group = a.old_group or (old.get("group") if old else None)
    new_group = a.group
    new_resources = list(a.resource)
    # Validate the complete new launch and acquire its scope before destroying context.
    cls = check_task_text(a.task, load_policy())
    if cls["risk"] == "high":
        raise SystemExit(f"reset refused: new task matches deny rule {cls['rule']}")
    handoff_path(a.agent, a.session).unlink(missing_ok=True)
    stale_repo_handoff = repo_handoff_path(
        a.agent, getattr(a, "cwd", None) or (old.get("cwd") if old else None))
    if stale_repo_handoff is not None:
        stale_repo_handoff.unlink(missing_ok=True)
    model = getattr(a, "model", None) or (old.get("model") if old else None)
    a.model = model
    # Keep the worker on the same account across resets unless a profile is named.
    a.profile = getattr(a, "profile", None) or (old.get("profile") if old else None)
    resolve_profile(a.cli, a.profile)
    cli_argv(a.cli, task_contract(a.task, new_resources, a.read_only, agent=a.agent,
                                  session=a.session, cwd=getattr(a, "cwd", None) or (old.get("cwd") if old else None)),
             a.approval_mode, a.read_only, model=model)
    leased = acquire_assignment(a.agent, new_group, new_resources, a.read_only,
                                a.lock_timeout)
    preserved = set(old.get("resources", [])) if (old and old_group == new_group) else set()
    ag = find_agent(a.agent, a.session)
    if not getattr(a, "workspace_id", None):
        ws = old.get("workspace") if old else None
        if not ws and ag and ag.get("pane_id"):
            try:
                pane_data = herdr(["pane", "list"], a.session)
                if isinstance(pane_data, dict):
                    panes = pane_data.get("result", {}).get("panes", [])
                    pane = next((p for p in panes if isinstance(p, dict) and p.get("pane_id") == ag["pane_id"]), None)
                    if pane and pane.get("workspace_id"):
                        ws = pane["workspace_id"]
            except Exception:
                pass
        if ws:
            a.workspace_id = ws
    if ag and ag.get("pane_id"):
        try:
            herdr(["pane", "close", ag["pane_id"]], a.session, check=True)
        except BaseException:
            if new_group:
                release_resources(a.agent, new_group, [r for r in leased if r not in preserved])
            raise
        for _ in range(20):
            if not find_agent(a.agent, a.session):
                break
            time.sleep(0.1)
        else:
            if new_group:
                release_resources(a.agent, new_group, [r for r in leased if r not in preserved])
            raise SystemExit(f"agent '{a.agent}' did not stop; refusing to mix old and new contexts")
    a.name = a.agent
    a.task = f"{a.task}\n\nPrior-task handoff (facts only):\n{a.handoff}" if a.handoff else a.task
    a.preserve_resources = list(preserved)
    try:
        result = cmd_spawn(a)
    except BaseException:
        if new_group:
            release_resources(a.agent, new_group, leased)
        if old_group and old_group != new_group:
            release_agent_strict(a.agent, old_group)
        raise
    try:
        if old_group and old_group != new_group:
            release_agent_strict(a.agent, old_group)
        elif old_group and old:
            release_resources(a.agent, old_group,
                              [r for r in old.get("resources", []) if r not in set(new_resources)])
    except RuntimeError as e:
        annotate_assignment(a.agent, a.session, cleanup_error=str(e),
                            old_group=old_group,
                            old_resources=old.get("resources", []) if old else [])
        raise SystemExit(
            f"reset launched the new context but old lease cleanup failed: {e}"
        ) from e
    emit_event(a.session, a.agent, "reset", {
        "task": a.task,
        "agent": a.agent,
    })
    return result


def cmd_park(a):
    """Park an agent for human review WITHOUT stalling the fleet.

    Releasing the parked agent's locks is the whole point: a parked agent that
    keeps its leases deadlocks every task queued behind it.
    """
    if a.reason:
        txt = ""
        pend = None
    else:
        try:
            txt = read_text(a.agent, a.session, 45)
            pend = pending_prompt(txt)
        except SystemExit:
            txt = ""
            pend = None

    pend = pend or {
        "subject": a.reason or "(no prompt captured)",
        "subject_display": a.reason or "(no prompt captured)", "options": []}
    assignment = get_assignment(a.agent, a.session)
    if not assignment:
        assignment = {"task": None, "resources": [], "group": a.group, "cwd": None, "read_only": False}
    task = assignment.get("task")
    read_only = assignment.get("read_only", False)
    group = a.group or assignment.get("group")
    if a.cancel:
        try:
            herdr(["agent", "send-keys", a.agent, "esc"], a.session, check=True)
        except (Exception, SystemExit):
            pass

    entry = {
        "agent": a.agent, "group": group,
        "subject": pend["subject_display"][:500],
        "task": task,
        "options": pend.get("options", []),
        "reason": a.reason or (
            decide_trust_dialog(txt, assignment.get("cwd"))["why"]
            if pend and pend.get("kind") == "trust"
            else classify_text(pend["subject"], load_policy(), task, read_only=read_only, kind=pend.get("kind"))["why"]
        ),
        "freed_locks": [],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tasks_file = state_path("tasks.json", a.session)
    esc_file = state_path("escalations.json", a.session)
    with state_lock(tasks_file):
        tasks = _load_json_unlocked(tasks_file, {})
        items = _load_json_unlocked(esc_file, [])
        old_tasks = copy.deepcopy(tasks)
        old_items = copy.deepcopy(items)
        current = tasks.get(a.agent)
        if not isinstance(current, dict):
            current = {"task": None, "resources": [], "group": group, "read_only": False, "cwd": None}
            tasks[a.agent] = current
        current["lifecycle"] = "parked"
        current["parked_resources"] = list(current.get("resources", []))
        current["resources"] = []
        current["parked_at"] = entry["at"]
        items.append(entry)
        _save_json_unlocked(tasks_file, tasks)
        try:
            _save_json_unlocked(esc_file, items)
            freed = release_agent_strict(a.agent, group) if group else {"freed": []}
            entry["freed_locks"] = freed.get("freed", [])
            _save_json_unlocked(esc_file, items)
            emit_event(a.session, a.agent, "parked", {
                "reason": entry["reason"],
                "freed_locks": freed.get("freed", []),
            })
        except BaseException:
            _save_json_unlocked(tasks_file, old_tasks)
            _save_json_unlocked(esc_file, old_items)
            raise
    print(json.dumps({"parked": a.agent, "freed_locks": freed.get("freed", []),
                      "escalations": len(items), "file": str(esc_file)}, indent=2))


def cmd_notify(a):
    """Emit an atomic outbox event from CLI or worker hook."""
    payload = {}
    if getattr(a, "payload", None):
        if isinstance(a.payload, str):
            try:
                payload = json.loads(a.payload)
            except json.JSONDecodeError as e:
                raise SystemExit(f"invalid JSON payload: {e}")
        elif isinstance(a.payload, dict):
            payload = a.payload
    if not isinstance(payload, dict):
        raise SystemExit("payload must be a JSON object")
    event_path = emit_event(a.session, a.agent, a.kind, payload)
    print(json.dumps({
        "notified": True,
        "agent": a.agent,
        "kind": a.kind,
        "file": str(event_path),
    }, indent=2))


def cmd_escalations(a):
    items = load_json(state_path("escalations.json", a.session), [])
    if a.clear:
        update_json(state_path("escalations.json", a.session), [], lambda _: [])
        print(json.dumps({"cleared": len(items)}))
        return
    if a.json:
        print(json.dumps(items, indent=2))
        return
    if not items:
        print("(no escalations)")
        return
    print(f"{len(items)} escalation(s) awaiting human decision:\n")
    for i, e in enumerate(items, 1):
        print(f"{i}. [{e['agent']}] {e['reason']}")
        if e.get("task"):
            print(f"   task:   {e['task'][:140]}")
        print(f"   prompt: {e['subject'][:160]}")
        if e["freed_locks"]:
            print(f"   released locks: {', '.join(e['freed_locks'])}")
        print()


def mark_missing_assignments_dead(session, live, group=None,
                                  reason="missing from Herdr session during reconcile"):
    """Persist loss of active assignments whose managed agents vanished from Herdr."""
    tasks_path = state_path("tasks.json", session)
    lost = []
    now = time.time()

    def update(data):
        for agent, assignment in data.items():
            if not isinstance(assignment, dict) or agent in live:
                continue
            if assignment.get("lifecycle", "active") != "active":
                continue
            if group is not None and assignment.get("group") != group:
                continue
            assignment["lifecycle"] = "dead"
            assignment["lost_at"] = now
            assignment["lost_reason"] = reason
            for key in ("pane", "pane_id", "tab", "tab_id", "pending_prompt", "pending"):
                assignment.pop(key, None)
            lost.append(agent)
        return data

    update_json(tasks_path, {}, update)
    for agent in lost:
        emit_event(session, agent, "worker_lost", {"reason": reason, "lost_at": now})
    return sorted(lost)


def cmd_reconcile(a):
    """Reclaim locks held by agents that no longer exist in the session.

    herdr's agent list IS the liveness signal: a pane that died is gone from it.
    TTL expiry in resource_pool is the backstop for a dead herder.
    """
    live = {ag.get("name") for ag in agents(a.session) if ag.get("name")}
    dead_agents = mark_missing_assignments_dead(a.session, live, group=a.group)
    known_dead = []
    try:
        all_tasks = load_json(state_path("tasks.json", a.session), {})
        if isinstance(all_tasks, dict):
            for name, asgn in sorted(all_tasks.items()):
                if isinstance(asgn, dict) and asgn.get("lifecycle") == "dead":
                    known_dead.append({"agent": name, "bead_id": asgn.get("bead_id"), "lost_reason": asgn.get("lost_reason")})
    except Exception:
        pass

    if a.group is not None:
        st = pool("status", "--group", a.group)
        if a.group not in st or not isinstance(st[a.group].get("resources"), dict):
            raise RuntimeError(f"resource pool status returned invalid group data for {a.group}")
        holders = set()
        for res in st.get(a.group, {}).get("resources", {}).values():
            for h in res.get("holders", []):
                holders.add(h["agent"])
            holders.update(res.get("waiting", []))
        ghosts = sorted(h for h in holders if h not in live)
        freed = [release_agent_strict(g, a.group) for g in ghosts]
        reaped = pool("reap", "--group", a.group)
        if reaped.get("status") != "ok":
            raise RuntimeError(f"resource pool reap failed: {reaped}")
        print(json.dumps({"live_agents": sorted(x for x in live if x),
                          "ghost_holders": ghosts,
                          "dead_agents": dead_agents,
                          "known_dead_agents": known_dead,
                          "freed": [f.get("freed") for f in freed],
                          "expired_reaped": reaped.get("reaped_count", 0)}, indent=2))
        return

    # Multi-group reconciliation across all active groups in session
    session_groups = set()
    try:
        tasks = load_json(state_path("tasks.json", a.session), {})
        if isinstance(tasks, dict):
            for t in tasks.values():
                if isinstance(t, dict):
                    grp = t.get("group")
                    if isinstance(grp, str) and grp:
                        session_groups.add(grp)
    except Exception:
        pass

    pool_groups = set()
    try:
        pg = pool("list-groups")
        if isinstance(pg, list):
            for g in pg:
                if isinstance(g, str) and g:
                    pool_groups.add(g)
    except Exception:
        pass

    all_groups = sorted(session_groups | pool_groups)
    groups_report = {}
    for g in all_groups:
        if g in session_groups:
            # Session-owned group: this session's agent list is authoritative
            try:
                st = pool("status", "--group", g)
                if g not in st or not isinstance(st[g].get("resources"), dict):
                    raise RuntimeError(f"resource pool status returned invalid group data for {g}")
                holders = set()
                for res in st.get(g, {}).get("resources", {}).values():
                    for h in res.get("holders", []):
                        if isinstance(h, dict) and "agent" in h:
                            holders.add(h["agent"])
                    for w in res.get("waiting", []):
                        holders.add(w)
                ghosts = sorted(h for h in holders if h not in live)
                freed = [release_agent_strict(ghost, g) for ghost in ghosts]
                reaped = pool("reap", "--group", g)
                if not isinstance(reaped, dict) or reaped.get("status") != "ok":
                    raise RuntimeError(f"resource pool reap failed for {g}: {reaped}")
                groups_report[g] = {
                    "ghost_holders": ghosts,
                    "freed": [f.get("freed") for f in freed],
                    "expired_reaped": reaped.get("reaped_count", 0),
                }
            except Exception as e:
                groups_report[g] = {
                    "error": str(e),
                }
        else:
            # Pool-only group: liveness cannot be established from this session
            try:
                reaped = pool("reap", "--group", g)
                reaped_count = reaped.get("reaped_count", 0) if isinstance(reaped, dict) and reaped.get("status") == "ok" else 0
                groups_report[g] = {
                    "reconciled": False,
                    "reason": "group not in session task state; liveness cannot be established from this session",
                    "expired_reaped": reaped_count,
                }
            except Exception as e:
                groups_report[g] = {
                    "error": str(e),
                }

    print(json.dumps({
        "live_agents": sorted(x for x in live if x),
        "dead_agents": dead_agents,
        "known_dead_agents": known_dead,
        "groups": groups_report,
    }, indent=2))


def cmd_plan(a):
    """Given a task file and a blocked set, print what is STILL runnable.

    This is what keeps an unattended run going after an escalation: work that
    does not depend on the parked decision must continue.
    """
    # Read the plan file directly. It is a caller-supplied input, not fleet state, so
    # taking the state lock would drop a `.fleet-state.lock` into the caller's directory.
    try:
        tasks = json.loads(Path(a.file).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"cannot read task file: {a.file}: {e}") from e
    if (not isinstance(tasks, list) or not all(isinstance(t, dict) for t in tasks)
            or not all(isinstance(t.get("id"), str) and t.get("id") for t in tasks)):
        raise SystemExit("plan file must be a JSON list of tasks with non-empty string ids")
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise SystemExit("plan file contains duplicate task ids")
    invalid = [t.get("id", "?") for t in tasks
               if not t.get("read_only", False) and not t.get("resources")]
    if invalid:
        raise SystemExit("mutating tasks missing resources: " + ", ".join(invalid))
    done = set(a.done or [])
    blocked = set(a.blocked or [])

    def is_blocked(t, seen=None):
        seen = seen or set()
        if t["id"] in blocked:
            return True
        for d in t.get("deps", []):
            if d in blocked:
                return True
            dep = next((x for x in tasks if x["id"] == d), None)
            if dep and d not in seen and is_blocked(dep, seen | {d}):
                return True
        return False

    runnable, waiting, dead = [], [], []
    for t in tasks:
        if t["id"] in done:
            continue
        if is_blocked(t):
            dead.append(t["id"])
        elif all(d in done for d in t.get("deps", [])):
            runnable.append(t)
        else:
            waiting.append(t["id"])
    print(json.dumps({
        "runnable_now": [{"id": t["id"], "agent": t.get("agent"),
                          "resources": t.get("resources", [])} for t in runnable],
        "waiting_on_deps": waiting,
        "blocked_by_escalation": dead,
        "verdict": ("work remains" if runnable else
                    ("all remaining work is blocked on human decisions" if dead
                     else "nothing runnable")),
    }, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Terminal Agent Runner fleet driver (herdr 0.6.10)")
    ap.add_argument("--session", type=session_id, default=os.environ.get("HERDR_SESSION"),
                    help="herdr session name (resolves its socket)")
    ap.add_argument("--allow-cross-session", action="store_true",
                    help="intentionally control a session other than the herder's current workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("spawn", help="Spawn an agent in its own tab")
    p.add_argument("--name", required=True, type=agent_id)
    p.add_argument("--cli", required=True, type=cli_id,
                    help="one of: codex, claude, agy, omp (flags are rejected)")
    p.add_argument("--model", type=validate_model_identifier, default=None,
                    help="model identifier for the CLI session")
    p.add_argument("--profile", type=profile_name, default=None,
                    help="account profile from the profiles file; 'none' forces the plain CLI")
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--task", default=None,
                    help="initial prompt, passed via argv (no Enter race) and recorded for taint checks")
    p.add_argument("--bead-id", type=lambda x: scoped_id(x, "Beads ID"), default=None,
                    help="durable Beads ID mapped to this worker; persisted in session state for recovery")
    p.add_argument("--group", type=lambda x: scoped_id(x, "resource group"),
                    help="resource-pool group (required for mutating tasks)")
    p.add_argument("--resource", action="append", default=[], type=lambda x: scoped_id(x, "resource"),
                    help="leased task scope; repeat for files/folders/services")
    p.add_argument("--read-only", action="store_true",
                    help="declare that the task cannot modify workspace state")
    p.add_argument("--lock-timeout", type=nonnegative_float, default=0,
                    help="seconds to wait for all assignment leases")
    p.add_argument("--approval-mode", choices=APPROVAL_MODES,
                    default="auto",
                    help="launch permission policy (Codex/Claude default: auto)")
    p.add_argument("--workspace-id", help="explicit workspace ID for cross-session spawning")
    p.add_argument("--json", action="store_true", default=False,
                    help="always output structured JSON result (takes precedence over quiet mode)")
    p.set_defaults(fn=cmd_spawn)

    p = sub.add_parser("status", help="Compact fleet table with classification")
    p.add_argument("--json", action="store_true")
    p.add_argument("--settle-samples", type=positive_int, default=DEFAULT_SETTLE_SAMPLES,
                    help="consecutive samples required before reporting done/idle")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("watch", help="Wait for a fleet change/action without repeated status output")
    p.add_argument("--until", choices=["change", "actionable", "terminal"], default="actionable")
    # nonnegative_float: 0 means emit the current snapshot immediately (first poll hits the
    # timeout check before sleep). Positive values wait up to that many seconds.
    # Previously positive_float caused `watch --timeout 0` to be rejected with an argparse
    # error on stderr — silently swallowed when callers used `2>/dev/null`.
    p.add_argument("--timeout", type=nonnegative_float, default=60)
    p.add_argument("--interval", type=positive_float, default=2)
    p.add_argument("--renew-interval", type=positive_float, default=60,
                    help="seconds between assignment lease heartbeats")
    p.add_argument("--settle-samples", type=positive_int, default=DEFAULT_SETTLE_SAMPLES,
                    help="consecutive samples required before reporting done/idle")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("notify", help="Emit an atomic outbox event")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--kind", required=True)
    p.add_argument("--payload", default="{}", help="JSON payload object")
    p.set_defaults(fn=cmd_notify)

    p = sub.add_parser("read", help="Plain-text pane buffer (no JSON envelope)")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--lines", type=positive_int, default=20)
    p.add_argument("--source", default="visible",
                    choices=["visible", "recent", "recent-unwrapped"])
    p.set_defaults(fn=cmd_read)

    p = sub.add_parser("classify", help="Classify the pending prompt (exit 0=approve, 2=escalate)")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--unwrapped", action="store_true")
    p.set_defaults(fn=cmd_classify)

    p = sub.add_parser("prompt", help="Send a prompt and confirm it submitted")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--text", required=True)
    p.add_argument("--related", action="store_true",
                    help="confirm this is a same-domain continuation in the existing context")
    p.add_argument("--group", type=lambda x: scoped_id(x, "resource group"),
                    help="resource group; defaults to the current assignment")
    p.add_argument("--resource", action="append", default=[], type=lambda x: scoped_id(x, "resource"),
                    help="replacement/continued leased scope; repeat as needed")
    p.add_argument("--read-only", action="store_true")
    p.add_argument("--lock-timeout", type=nonnegative_float, default=0)
    p.set_defaults(fn=cmd_prompt)

    p = sub.add_parser("approve", help="Approve a pending prompt if policy allows or reviewed escalation")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--choice", default="1", type=approval_choice)
    p.add_argument("--reviewed-by", "--who", dest="reviewed_by", default=None,
                   help="identity of the human reviewer authorizing this escalation")
    p.add_argument("--reviewed-subject", "--reviewed-digest", "--subject-digest",
                   dest="reviewed_subject", default=None,
                   help="digest or text of the prompt subject that was reviewed")
    p.add_argument("--regrant", "--re-grant", dest="regrant", action="store_true",
                   help="authorize re-execution of a previously approved prompt subject")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("cancel", help="Send Esc to cancel a pending prompt")
    p.add_argument("--agent", required=True, type=agent_id)
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("reject", help="Reject a pending prompt with Esc and instruct worker on continuation or halt")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--reason", default=None, help="Explanation of why the permission/operation was rejected")
    p.add_argument("--prompt", default=None, help="Custom continuation prompt for the worker")
    p.add_argument("--halt", "--park", dest="halt", action="store_true",
                   help="Halt/park the worker after rejection instead of submitting a continuation prompt")
    p.add_argument("--group", type=lambda x: scoped_id(x, "resource group"), default=None,
                   help="Resource group to use if halting/parking")
    p.set_defaults(fn=cmd_reject)

    p = sub.add_parser("complete", help="Verify structured handoff/evidence, then close and release")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--group", type=lambda x: scoped_id(x, "resource group"),
                    help="override the recorded resource group")
    p.add_argument("--lines", type=positive_int, default=100)
    p.add_argument("--max-chars", type=positive_int, default=DEFAULT_MAX_HANDOFF_CHARS)
    p.add_argument("--handoff", default=None,
                    help="lead-supplied handoff override as raw JSON or @<file>")
    p.set_defaults(fn=cmd_complete)

    p = sub.add_parser("reset", help="Start a fresh agent context for a new task")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--cli", required=True, type=cli_id)
    p.add_argument("--model", type=validate_model_identifier, default=None,
                    help="model identifier for the CLI session")
    p.add_argument("--profile", type=profile_name, default=None,
                    help="account profile from the profiles file; 'none' forces the plain CLI")
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--task", required=True)
    p.add_argument("--handoff", required=True,
                    help="minimal decisions/results/inputs carried from the previous context")
    p.add_argument("--old-group", type=lambda x: scoped_id(x, "old resource group"),
                    help="override the old assignment group to release")
    p.add_argument("--group", type=lambda x: scoped_id(x, "resource group"),
                    help="resource-pool group for the new task")
    p.add_argument("--resource", action="append", default=[], type=lambda x: scoped_id(x, "resource"))
    p.add_argument("--read-only", action="store_true")
    p.add_argument("--lock-timeout", type=nonnegative_float, default=0)
    p.add_argument("--approval-mode", choices=APPROVAL_MODES,
                    default="auto")
    p.add_argument("--workspace-id", help="explicit workspace ID for cross-session spawning")
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("park", help="Escalate to human, free locks, keep fleet moving")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--group", type=lambda x: scoped_id(x, "resource group"),
                    help="override the agent's recorded resource group")
    p.add_argument("--reason", default=None)
    p.add_argument("--cancel", action="store_true", help="also Esc the pending prompt")
    p.set_defaults(fn=cmd_park)

    p = sub.add_parser("escalations", help="List/clear pending human decisions")
    p.add_argument("--json", action="store_true")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(fn=cmd_escalations)

    p = sub.add_parser("reconcile", help="Reclaim locks from dead/absent agents")
    p.add_argument("--group", default=None, type=lambda x: scoped_id(x, "resource group"),
                   help="resource group (omit to reconcile all active groups in session)")
    p.set_defaults(fn=cmd_reconcile)

    p = sub.add_parser("plan", help="Show what is still runnable given blocked tasks")
    p.add_argument("--file", required=True, help="JSON list of {id, agent, deps, resources}")
    p.add_argument("--done", nargs="*", default=[])
    p.add_argument("--blocked", nargs="*", default=[])
    p.set_defaults(fn=cmd_plan)

    a = ap.parse_args()
    try:
        if a.cmd != "plan":
            validate_session_affinity(a.session, a.allow_cross_session)
        a.fn(a)
    except (StateError, RuntimeError, OSError) as e:
        print(json.dumps({"error": str(e), "command": a.cmd}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
