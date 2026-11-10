"""Per-medium normalisation and projection fingerprinting — the Stage 4 normalisation table.

The spec's six verification media are a closed set, and each one states what a normalised
projection KEEPS, what it DROPS as incidental, and what it MASKS as data:

    rendered_ui       keep role / accessible name / visible text / aria states / document order
                      drop class, style, test attributes, framework keys
                      mask numerals -> "#", recorded fixture values -> "<value>"
    api_response      keep status code, envelope key set, key types, contracted headers
                      drop headers the contract does not name
                      mask scalar payload values -> their type tag
    process_exit      keep exit code, stream line templates, warning classes
                      drop timestamps, pids, absolute paths
                      mask interpolated values -> "<val>"
    datastore_state   keep changed column set per table and constraint outcomes
                      drop physical row identity
                      mask cell values -> their type tag
    device_actuation  keep command opcodes and their order
                      drop timing jitter
                      mask operands -> "<operand>"
    agent_transcript  keep tool-call names and order, final message template
                      drop token counts, latencies, step timestamps
                      mask interpolated values -> "<val>"

Masking is what makes the fingerprint correct rather than merely strict: two renderings of one
state differing only in data must fingerprint alike, while two states differing only in wording
must not. Two consequences run through every normaliser here:

  * KEEPING is a keep-list, never a drop-list. An attribute, header or record field that is not
    named as kept is dropped by never being read. Nothing is recognised by matching a project's
    own vocabulary — no attribute name like "data-testid", no log phrase, no column name — so
    renaming the thing does not defeat the drop.
  * DROPPING incidental noise is done by FORMAT, not by name: a timestamp is recognised as an
    ISO-8601 shape, a path as a path shape, a pid or a byte count as a number. A stream that
    calls its process id something else is masked all the same.

Nothing in here reads a fingerprint, a normalised projection, or a verdict from its input. The
fingerprint is computed from the captured artifact, which is the only reason it can refute the
Stage 2 prediction rather than restate it.
"""

import hashlib
import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .primitives import check_closed_key_set, is_vacuous_container

# The six media of the normalisation table. A closed set: an unrecognised medium is refused,
# never skipped, because a milestone whose medium is misspelt would otherwise skip capture.
CANONICAL_MEDIA = (
    "rendered_ui",
    "api_response",
    "process_exit",
    "datastore_state",
    "device_actuation",
    "agent_transcript",
)

# Keys a planner-authored projection capture record may carry. There is deliberately no
# fingerprint key and no normalised-projection key: both are computed here from the captured
# artifact, so declaring either is refused rather than believed.
PROJECTION_RECORD_KEYS = (
    "evidence_key",
    "observer_id",
    "captured_artifact",
    "captured_artifact_path",
    "recorded_fixture_values",
    "slice_id",
    "milestone_id",
)

NUMERAL_MASK = "#"
VALUE_MASK = "<value>"
STREAM_MASK = "<val>"
OPERAND_MASK = "<operand>"
PATH_MASK = "<path>"
EMPTY_MARK = "<empty>"
NONE_MARK = "<none>"


class NormalisationRefused(Exception):
    """An artifact could not be normalised, carrying the bare token to print for it."""

    def __init__(self, code: str, detail: str):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Format-recognised noise, and value masking
# ---------------------------------------------------------------------------

_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_CLOCK = re.compile(r"\b\d{1,3}:\d{2}:\d{2}(?:\.\d+)?\b")
_ABS_PATH = re.compile(r"(?:[A-Za-z]:)?/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+")
_HEX_WORD = re.compile(r"\b0[xX][0-9a-fA-F]+\b")
_NUMBER = re.compile(r"\d[\d,_]*(?:\.\d+)?")
_WHITESPACE = re.compile(r"\s+")


def compute_fingerprint(normalised: str) -> str:
    """SHA-256 of a normalised projection string."""
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def mask_recorded_values(text: str, recorded_values: Sequence[Any], mask: str) -> str:
    """Replace values the run's recorded fixture supplied with the mask.

    Longest first, so a value that contains another does not leave a fragment behind.
    """
    out = text
    for raw in sorted((str(v) for v in recorded_values or () if str(v).strip()), key=len, reverse=True):
        if raw in out:
            out = out.replace(raw, mask)
    return out


def mask_data_in_text(
    text: str,
    recorded_values: Sequence[Any],
    numeral_mask: str,
    value_mask: str = VALUE_MASK,
) -> str:
    """Mask the data in a line of observable text while preserving its template wording."""
    out = mask_recorded_values(text, recorded_values, value_mask)
    out = _HEX_WORD.sub(numeral_mask, out)
    out = _NUMBER.sub(numeral_mask, out)
    return _WHITESPACE.sub(" ", out).strip()


def drop_stream_noise(line: str) -> str:
    """Eliminate the incidental parts of a stream line by format: timestamps, paths, addresses."""
    out = _ISO_TIMESTAMP.sub("", line)
    out = _ABS_PATH.sub(PATH_MASK, out)
    out = _CLOCK.sub(STREAM_MASK, out)
    out = _HEX_WORD.sub(STREAM_MASK, out)
    out = _NUMBER.sub(STREAM_MASK, out)
    out = re.sub(r"\[\s*\]", "", out)
    return _WHITESPACE.sub(" ", out).strip()


def type_signature(value: Any) -> str:
    """The structural type signature of a value, with every scalar masked to its type.

    Used both to mask api_response and datastore_state payload values and to compute the
    distinct recorded shapes of an UNBOUNDED region from the samples that recorded them.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        if not value:
            return "array[]"
        inner = sorted({type_signature(v) for v in value})
        return "array[%s]" % "|".join(inner)
    if isinstance(value, dict):
        inner = ",".join("%s:%s" % (k, type_signature(value[k])) for k in sorted(value))
        return "object{%s}" % inner
    return "unknown"


# ---------------------------------------------------------------------------
# 1. rendered_ui
# ---------------------------------------------------------------------------

# The HTML-to-ARIA role mapping. This is the platform's own semantics, not any project's
# vocabulary: it says what a <button> IS to an observer, which is exactly the "element role"
# the table keeps.
_IMPLICIT_ROLES = {
    "h1": "heading", "h2": "heading", "h3": "heading", "h4": "heading",
    "h5": "heading", "h6": "heading",
    "button": "button",
    "a": "link",
    "ul": "list", "ol": "list", "menu": "list",
    "li": "listitem",
    "textarea": "textbox",
    "select": "combobox",
    "option": "option",
    "table": "table", "tr": "row", "td": "cell", "th": "columnheader",
    "img": "image",
    "form": "form",
    "nav": "navigation",
    "dialog": "dialog",
    "progress": "progressbar",
    "output": "status",
    "fieldset": "group",
    "legend": "legend",
}

# Containers with no role of their own: their text is kept, they contribute no token.
_TRANSPARENT_TAGS = frozenset({
    "div", "span", "section", "article", "p", "main", "header", "footer", "aside",
    "body", "html", "tbody", "thead", "tfoot", "em", "strong", "small", "b", "i",
    "u", "label", "figure", "figcaption", "br", "hr", "pre", "code", "blockquote",
    "dl", "dt", "dd", "picture", "source", "template", "slot", "font", "center",
})

# Elements whose content is not observable at all.
_OPAQUE_TAGS = frozenset({"script", "style", "head", "meta", "link", "title", "noscript", "svg"})

# Accessible-name attributes and state attributes, in keep-list form.
_NAME_ATTRS = ("aria-label", "alt", "aria-placeholder", "placeholder")
_STATE_ATTRS = (
    "aria-busy", "aria-disabled", "aria-expanded", "aria-checked", "aria-selected",
    "aria-current", "aria-invalid", "aria-pressed", "aria-hidden", "aria-live",
)
_BOOLEAN_AFFORDANCE_ATTRS = ("disabled", "checked", "readonly", "required", "open")


class _RenderedUiParser(HTMLParser):
    """Walk the markup in document order, keeping only what an observer can distinguish."""

    def __init__(self, recorded_values: Sequence[Any]):
        super().__init__(convert_charrefs=True)
        self.recorded_values = recorded_values
        self.tokens: List[str] = []
        self._opaque_stack: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if self._opaque_stack:
            return
        if tag in _OPAQUE_TAGS:
            self._opaque_stack.append(tag)
            return

        attr = {k.lower(): (v if v is not None else "") for k, v in attrs}
        role = (attr.get("role") or "").strip().lower()
        if not role:
            if tag == "input":
                role = "input:%s" % ((attr.get("type") or "text").strip().lower())
            elif tag in _IMPLICIT_ROLES:
                role = _IMPLICIT_ROLES[tag]
            elif tag in _TRANSPARENT_TAGS:
                role = ""
            else:
                role = tag

        if not role:
            return

        states = []
        for name in _STATE_ATTRS:
            if name in attr and attr[name].strip():
                states.append("%s=%s" % (name[len("aria-"):], attr[name].strip().lower()))
        for name in _BOOLEAN_AFFORDANCE_ATTRS:
            if name in attr:
                states.append("%s=true" % name)
        if tag == "a" and (attr.get("href") or "").strip():
            states.append("href=present")

        accessible_name = ""
        for name in _NAME_ATTRS:
            if (attr.get(name) or "").strip():
                accessible_name = mask_data_in_text(attr[name], self.recorded_values, NUMERAL_MASK)
                break

        parts = [role] + sorted(states)
        if accessible_name:
            parts.append("name=%s" % accessible_name)
        self.tokens.append("[%s]" % "|".join(parts))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._opaque_stack and self._opaque_stack[-1] == tag:
            self._opaque_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._opaque_stack:
            return
        masked = mask_data_in_text(data, self.recorded_values, NUMERAL_MASK)
        if masked:
            self.tokens.append("[text|%s]" % masked)


def normalise_rendered_ui(artifact: Any, recorded_values: Sequence[Any] = ()) -> str:
    """Normalise a captured rendering: the markup an observer was actually shown."""
    if not isinstance(artifact, str):
        raise NormalisationRefused(
            "UNNORMALISABLE_ARTIFACT",
            "a rendered_ui capture is the markup the observer was shown, so it must be text; got %s"
            % type(artifact).__name__,
        )
    parser = _RenderedUiParser(recorded_values)
    parser.feed(artifact)
    parser.close()
    if not parser.tokens:
        return "[empty_dom]"
    return "".join(parser.tokens)


# ---------------------------------------------------------------------------
# 2. api_response
# ---------------------------------------------------------------------------

def _split_http_capture(artifact: Any) -> Tuple[int, Dict[str, str], Any]:
    if isinstance(artifact, dict):
        status = artifact.get("status_code")
        if status is None and isinstance(artifact.get("status"), int):
            status = artifact.get("status")
        if not isinstance(status, int):
            raise NormalisationRefused(
                "UNNORMALISABLE_ARTIFACT",
                "an api_response capture keeps its status code, so the capture must record one",
            )
        raw_headers = artifact.get("headers")
        headers = (
            {str(k).lower(): str(v) for k, v in raw_headers.items()}
            if isinstance(raw_headers, dict) else {}
        )
        body = artifact.get("body", artifact.get("payload"))
        return status, headers, body

    if isinstance(artifact, str) and artifact.lstrip().startswith("HTTP/"):
        lines = artifact.strip().splitlines()
        match = re.search(r"HTTP/\d(?:\.\d)?\s+(\d{3})", lines[0])
        if not match:
            raise NormalisationRefused(
                "UNNORMALISABLE_ARTIFACT", "the response status line carries no status code"
            )
        status = int(match.group(1))
        headers: Dict[str, str] = {}
        body_lines: List[str] = []
        in_headers = True
        for line in lines[1:]:
            if in_headers:
                if not line.strip():
                    in_headers = False
                elif ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
            else:
                body_lines.append(line)
        body_text = "\n".join(body_lines).strip()
        body: Any = None
        if body_text:
            try:
                body = json.loads(body_text)
            except ValueError:
                body = body_text
        return status, headers, body

    raise NormalisationRefused(
        "UNNORMALISABLE_ARTIFACT",
        "an api_response capture is a recorded response (status, headers, body) or raw HTTP text",
    )


def captured_status_code(artifact: Any) -> Optional[int]:
    """The status an api_response capture recorded, read the way the normaliser reads it.

    None when the capture carries no usable status; the normaliser refuses such a capture.
    """
    try:
        status, _headers, _body = _split_http_capture(artifact)
    except NormalisationRefused:
        return None
    return None if isinstance(status, bool) else status


def normalise_api_response(artifact: Any, contract_headers: Iterable[str] = ()) -> str:
    """Normalise a captured response: status, contracted headers, envelope key set and types.

    `contract_headers` is the set of header names the contract fixtures recorded. A header the
    contract does not name is dropped, so a request id or a clock cannot make two states look
    different from one another.
    """
    status, headers, body = _split_http_capture(artifact)
    named = {str(h).lower() for h in contract_headers or ()}
    kept = ["header:%s=%s" % (key, headers[key]) for key in sorted(headers) if key in named]
    header_segment = ("|" + "|".join(kept)) if kept else ""

    if isinstance(body, dict):
        keys = ",".join("%s(%s)" % (k, type_signature(body[k])) for k in sorted(body))
        body_segment = "keys:%s" % (keys if keys else EMPTY_MARK)
    elif body is None:
        body_segment = "body:%s" % EMPTY_MARK
    else:
        body_segment = "body:%s" % type_signature(body)

    return "status:%d%s|%s" % (status, header_segment, body_segment)


# ---------------------------------------------------------------------------
# 3. process_exit
# ---------------------------------------------------------------------------

def _normalise_stream(text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        return EMPTY_MARK
    lines = [drop_stream_noise(line) for line in text.splitlines()]
    kept = [line for line in lines if line]
    return "\\n".join(kept) if kept else EMPTY_MARK


def normalise_process_exit(artifact: Any, warning_classes: Iterable[str] = ()) -> str:
    """Normalise a captured process result: exit code, stream templates, warning classes.

    `warning_classes` are the classes the caller matched against the Stage 1 failure envelope
    census. They are kept in the projection because a run that warns is observably not the same
    state as a run that does not.
    """
    if not isinstance(artifact, dict) or not isinstance(artifact.get("exit_code"), int):
        raise NormalisationRefused(
            "UNNORMALISABLE_ARTIFACT",
            "a process_exit capture keeps its exit code, so the capture must record one",
        )
    classes = sorted({str(w) for w in warning_classes or () if str(w).strip()})
    return "exit:%d|stdout:%s|stderr:%s|warnings:%s" % (
        artifact["exit_code"],
        _normalise_stream(artifact.get("stdout")),
        _normalise_stream(artifact.get("stderr")),
        ",".join(classes) if classes else NONE_MARK,
    )


# ---------------------------------------------------------------------------
# 4. datastore_state
# ---------------------------------------------------------------------------

def normalise_datastore_state(artifact: Any) -> str:
    """Normalise a captured mutation: changed columns per table and constraint outcomes.

    Physical row identity is dropped by never being read, and each cell value is masked to its
    type, so the same downgrade applied to two tenants is one state.
    """
    if not isinstance(artifact, dict) or not isinstance(artifact.get("mutations"), list):
        raise NormalisationRefused(
            "UNNORMALISABLE_ARTIFACT",
            "a datastore_state capture records its mutations as a list of changed tables",
        )

    tables: Dict[str, Dict[str, str]] = {}
    for mutation in artifact["mutations"]:
        if not isinstance(mutation, dict):
            continue
        table = str(mutation.get("table") or "").strip()
        if not table:
            continue
        columns = tables.setdefault(table, {})
        changed = mutation.get("changed_columns")
        if not isinstance(changed, list):
            continue
        for column in changed:
            if not isinstance(column, dict):
                continue
            name = str(column.get("name") or "").strip()
            if not name:
                continue
            if "value" in column:
                columns[name] = type_signature(column["value"])
            else:
                columns[name] = str(column.get("type") or "unknown")

    table_parts = []
    for table in sorted(tables):
        cols = ",".join("%s:%s" % (name, tables[table][name]) for name in sorted(tables[table]))
        table_parts.append("%s(%s)" % (table, cols if cols else EMPTY_MARK))

    outcomes: Dict[str, str] = {}
    raw_constraints = artifact.get("constraints")
    if isinstance(raw_constraints, list):
        for constraint in raw_constraints:
            if isinstance(constraint, dict):
                name = str(constraint.get("name") or "").strip()
                if name:
                    outcomes[name] = str(constraint.get("outcome") or "").strip() or "unknown"
    constraint_parts = ["%s(%s)" % (name, outcomes[name]) for name in sorted(outcomes)]

    return "tables:%s|constraints:%s" % (
        ",".join(table_parts) if table_parts else NONE_MARK,
        ",".join(constraint_parts) if constraint_parts else NONE_MARK,
    )


# ---------------------------------------------------------------------------
# 5. device_actuation
# ---------------------------------------------------------------------------

_COMMAND_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\((.*)\)\s*$")


def _split_operands(text: str) -> List[str]:
    depth = 0
    current = ""
    operands = []
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            operands.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        operands.append(current)
    return [operand for operand in operands if operand.strip()]


def normalise_device_actuation(artifact: Any) -> str:
    """Normalise a captured actuation trace: opcode sequence with operands masked.

    Timing is dropped by never being read (structured form) or by being eliminated as a
    numeric/bracketed prefix (text form), so bus jitter cannot split one state into two.
    """
    if isinstance(artifact, dict):
        commands = artifact.get("commands")
    else:
        commands = artifact
    if isinstance(commands, str):
        commands = commands.splitlines()
    if not isinstance(commands, list):
        raise NormalisationRefused(
            "UNNORMALISABLE_ARTIFACT",
            "a device_actuation capture records an ordered list of commands",
        )

    opcodes = []
    for command in commands:
        if isinstance(command, dict):
            opcode = str(command.get("opcode") or "").strip()
            if not opcode:
                continue
            operands = command.get("operands")
            count = len(operands) if isinstance(operands, (list, tuple)) else (0 if operands is None else 1)
            opcodes.append("%s(%s)" % (opcode, ",".join([OPERAND_MASK] * count)))
            continue
        if not isinstance(command, str) or not command.strip():
            continue
        line = re.sub(r"^\s*\[[^\]]*\]\s*", "", command).strip()
        match = _COMMAND_CALL.search(line)
        if match:
            operands = _split_operands(match.group(2))
            opcodes.append("%s(%s)" % (match.group(1), ",".join([OPERAND_MASK] * len(operands))))
        else:
            word = line.split()[0] if line.split() else ""
            if word:
                opcodes.append("%s()" % word)

    return "opcodes:[%s]" % ",".join(opcodes)


# ---------------------------------------------------------------------------
# 6. agent_transcript
# ---------------------------------------------------------------------------

def normalise_agent_transcript(artifact: Any, recorded_values: Sequence[Any] = ()) -> str:
    """Normalise a captured transcript: tool-call names and order, final message template.

    Token counts, latencies and step timestamps are dropped by never being read; argument
    values and interpolated entities in the final message are masked, so the same reasoning
    over a different query is one state.
    """
    if not isinstance(artifact, dict):
        raise NormalisationRefused(
            "UNNORMALISABLE_ARTIFACT",
            "an agent_transcript capture records its tool calls and its final message",
        )
    raw_calls = artifact.get("tool_calls")
    if not isinstance(raw_calls, list):
        raise NormalisationRefused(
            "UNNORMALISABLE_ARTIFACT",
            "an agent_transcript capture keeps tool-call order, so the calls must be a list",
        )

    calls = []
    for call in raw_calls:
        if isinstance(call, dict):
            name = str(call.get("name") or call.get("tool") or "").strip()
            if not name:
                continue
            arguments = call.get("arguments")
            if isinstance(arguments, dict):
                inner = ",".join("%s=%s" % (key, STREAM_MASK) for key in sorted(arguments))
            elif isinstance(arguments, (list, tuple)):
                inner = ",".join([STREAM_MASK] * len(arguments))
            else:
                inner = ""
            calls.append("%s(%s)" % (name, inner))
        elif isinstance(call, str) and call.strip():
            calls.append("%s()" % call.strip())

    final = artifact.get("final_message")
    if isinstance(final, str) and final.strip():
        # The table masks every interpolated value in a transcript to one placeholder, so a
        # recorded value and a numeral both read as <val> here rather than as two spellings.
        template = mask_data_in_text(final, recorded_values, STREAM_MASK, value_mask=STREAM_MASK)
    else:
        template = EMPTY_MARK

    return "tool_calls:[%s]|final_template:%s" % (",".join(calls), template)


# ---------------------------------------------------------------------------
# captureProjection
# ---------------------------------------------------------------------------

def refuse_unrecognised_medium(medium: Any, label: str = "verification medium") -> None:
    """Refuse a medium outside the closed set of six, using the shared closed-key-set check."""
    result = check_closed_key_set({str(medium): None}, CANONICAL_MEDIA, label=label)
    if not result.is_valid:
        raise NormalisationRefused("UNKNOWN_VERIFICATION_MEDIUM", result.message)


def capture_projection(
    medium: str,
    captured_artifact: Any,
    recorded_values: Sequence[Any] = (),
    contract_headers: Iterable[str] = (),
    warning_classes: Iterable[str] = (),
) -> Tuple[str, str]:
    """Normalise one captured artifact for its medium and fingerprint the result.

    Returns (normalised_projection, fingerprint). Raises NormalisationRefused, carrying the bare
    token to print, when the medium is outside the closed set or the artifact cannot be
    normalised for it.
    """
    refuse_unrecognised_medium(medium)
    if is_vacuous_container(captured_artifact):
        raise NormalisationRefused(
            "EMPTY_CAPTURED_ARTIFACT",
            "the captured artifact for medium %r carries no content, so nothing was captured" % medium,
        )

    if medium == "rendered_ui":
        normalised = normalise_rendered_ui(captured_artifact, recorded_values)
    elif medium == "api_response":
        normalised = normalise_api_response(captured_artifact, contract_headers)
    elif medium == "process_exit":
        normalised = normalise_process_exit(captured_artifact, warning_classes)
    elif medium == "datastore_state":
        normalised = normalise_datastore_state(captured_artifact)
    elif medium == "device_actuation":
        normalised = normalise_device_actuation(captured_artifact)
    else:
        normalised = normalise_agent_transcript(captured_artifact, recorded_values)

    return normalised, compute_fingerprint(normalised)


# ---------------------------------------------------------------------------
# The projection gate: coverage differencing and pairwise collision
# ---------------------------------------------------------------------------

def captured_stream_text(captured_artifact: Any) -> str:
    """Every observable stream of a capture, flattened so census markers can be looked for in it."""
    if isinstance(captured_artifact, str):
        return captured_artifact
    if not isinstance(captured_artifact, dict):
        return ""
    parts: List[str] = []
    for key in ("stdout", "stderr", "log"):
        value = captured_artifact.get(key)
        if isinstance(value, str):
            parts.append(value)
    headers = captured_artifact.get("headers")
    if isinstance(headers, dict):
        parts.extend("%s: %s" % (key, value) for key, value in headers.items())
    body = captured_artifact.get("body", captured_artifact.get("payload"))
    if isinstance(body, str):
        parts.append(body)
    elif body is not None:
        parts.append(json.dumps(body, sort_keys=True))
    return "\n".join(parts)


def census_markers(contract_fixture: Any) -> List[Tuple[str, str]]:
    """The warning classes a failure envelope census recorded, and the text each one shows as.

    Both halves come out of the census: the class it recorded, and the text it recorded for that
    envelope. Nothing is matched against a phrase written into this file, so a boundary that
    words its warning differently is still caught, as long as its own census recorded that
    wording — and a boundary whose census is empty was already refused at Gate 1.
    """
    markers: List[Tuple[str, str]] = []
    if not isinstance(contract_fixture, dict):
        return markers
    census = contract_fixture.get("failure_envelope_census")
    if not isinstance(census, list):
        return markers
    for entry in census:
        if not isinstance(entry, dict) or is_vacuous_container(entry):
            continue
        warning_class = str(entry.get("error_class") or entry.get("envelope_id") or "").strip()
        if not warning_class:
            continue
        texts: List[str] = []
        if entry.get("error_class"):
            texts.append(str(entry["error_class"]))
        payload = entry.get("payload", entry.get("sample"))
        if isinstance(payload, str):
            texts.append(payload)
        elif isinstance(payload, dict):
            texts.extend(str(value) for value in payload.values() if isinstance(value, str))
        for text in texts:
            if len(text.strip()) >= 4 and (warning_class, text.strip()) not in markers:
                markers.append((warning_class, text.strip()))
    return markers


def matched_warning_classes(
    captured_artifact: Any,
    markers: Iterable[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """Census warning classes observable in what a run captured."""
    haystack = captured_stream_text(captured_artifact).lower()
    if not haystack:
        return []
    matched: List[Tuple[str, str]] = []
    for warning_class, marker in markers:
        if marker.lower() in haystack and (warning_class, marker) not in matched:
            matched.append((warning_class, marker))
    return matched


def observer_media(matrix: Any) -> Tuple[Dict[str, str], List[str]]:
    """Map each observer in the matrix to its declared medium, refusing media outside the six.

    The medium comes from the observer register rather than from the capture record, so the
    normaliser a capture is run through is chosen by the pipeline's own artifact.
    """
    media: Dict[str, str] = {}
    problems: List[str] = []
    observers = matrix.get("observers") if isinstance(matrix, dict) else None
    if not isinstance(observers, list) or is_vacuous_container(observers):
        problems.append("the matrix declares no observers, so no capture can be normalised")
        return media, problems
    for observer in observers:
        if not isinstance(observer, dict):
            continue
        observer_id = str(observer.get("observer_id") or "").strip()
        medium = observer.get("medium")
        if not observer_id:
            problems.append("an observer row carries no observer_id")
            continue
        try:
            refuse_unrecognised_medium(medium, label="observer %r medium" % observer_id)
        except NormalisationRefused as refused:
            problems.append("%s: %s" % (refused.code, refused.detail))
            continue
        media[observer_id] = str(medium)
    return media, problems


def matrix_projection_targets(matrix: Any) -> Set[Tuple[str, str]]:
    """Every (evidence_key, observer_id) the matrix says is observable.

    Coverage is differenced against this set. It is keyed per observer because the collision
    check is per observer: an observer whose projections were never captured has had nothing
    refuted, however many other captures the run produced.
    """
    targets: Set[Tuple[str, str]] = set()
    states = matrix.get("states") if isinstance(matrix, dict) else None
    if not isinstance(states, list):
        return targets
    for state in states:
        if not isinstance(state, dict):
            continue
        evidence_key = str(state.get("evidence_key") or "").strip()
        if not evidence_key:
            continue
        projections = state.get("observable_projections")
        if not isinstance(projections, list):
            continue
        for projection in projections:
            if not isinstance(projection, dict):
                continue
            observer_id = str(projection.get("observer_id") or "").strip()
            if observer_id:
                targets.add((evidence_key, observer_id))
    return targets


# The one discriminator atom per medium that states the outcome code an observer sees. Only the
# two media whose capture carries a numeric outcome have one; every other medium has none.
OUTCOME_ATOMS = {
    "process_exit": ("exit_code",),
    "api_response": ("status_code", "status"),
}


def _declared_code(value: Any, medium: str) -> Optional[int]:
    """A discriminator outcome atom read as an integer code, or None when it names no code.

    The matrix writes codes as strings ("1", "409"); a non-numeric atom ("pending", "none")
    declares a state that never produces a code, so it declares nothing to compare against.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        code = value
    elif isinstance(value, str) and re.fullmatch(r"\s*\d{1,3}\s*", value):
        code = int(value)
    else:
        return None
    if medium == "api_response" and not 100 <= code <= 599:
        return None
    if medium == "process_exit" and not 0 <= code <= 255:
        return None
    return code


def matrix_declared_outcomes(matrix: Any) -> Dict[Tuple[str, str], Tuple[str, int]]:
    """Every (evidence_key, observer_id) whose approved projection declares one outcome code.

    Maps to (medium, code): the exit code a process_exit projection declares, or the HTTP
    status an api_response projection declares. The medium is the observer register's, the
    same one the capture is normalised under. A target is ambiguous, and declares nothing, when
    any of its projections lacks a parseable code atom ("pending", "4xx", "1-4", absent), when
    two projections disagree on the code, or when a projection's medium contradicts the
    register. The caller then falls back to the default success rule rather than picking one of
    several answers. An observer the register cannot resolve declares nothing either; the
    harness refuses such a register itself (see bin/milestone-harness), so this fails closed.
    """
    media, _problems = observer_media(matrix)
    found: Dict[Tuple[str, str], Set[Tuple[str, int]]] = {}
    ambiguous: Set[Tuple[str, str]] = set()
    states = matrix.get("states") if isinstance(matrix, dict) else None
    if not isinstance(states, list):
        return {}
    for state in states:
        if not isinstance(state, dict):
            continue
        evidence_key = str(state.get("evidence_key") or "").strip()
        projections = state.get("observable_projections")
        if not evidence_key or not isinstance(projections, list):
            continue
        for projection in projections:
            if not isinstance(projection, dict):
                continue
            observer_id = str(projection.get("observer_id") or "").strip()
            medium = media.get(observer_id)
            atoms = OUTCOME_ATOMS.get(medium or "")
            if not atoms:
                continue
            target = (evidence_key, observer_id)
            discriminator = projection.get("discriminator")
            if projection.get("medium") not in (None, medium) or not isinstance(discriminator, dict):
                ambiguous.add(target)
                continue
            present = [atom for atom in atoms if atom in discriminator]
            code = _declared_code(discriminator[present[0]], medium) if present else None
            if code is None:
                ambiguous.add(target)
                continue
            found.setdefault(target, set()).add((medium, code))
    return {
        target: next(iter(codes))
        for target, codes in found.items()
        if len(codes) == 1 and target not in ambiguous
    }


def contract_header_names(contract_fixtures: Iterable[Any]) -> Set[str]:
    """Header names the contract fixtures recorded — the only headers a projection keeps."""
    names: Set[str] = set()
    for fixture in contract_fixtures:
        if not isinstance(fixture, dict):
            continue
        for group in ("success_samples", "failure_envelope_census"):
            samples = fixture.get(group)
            if not isinstance(samples, list):
                continue
            for sample in samples:
                if isinstance(sample, dict) and isinstance(sample.get("headers"), dict):
                    names.update(str(k).lower() for k in sample["headers"])
    return names


def compute_captured_projections(
    records: Any,
    media_by_observer: Dict[str, str],
    contract_headers: Iterable[str] = (),
    artifact_root: Optional[Any] = None,
    warning_markers: Iterable[Tuple[str, str]] = (),
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Compute one projection per capture record, from the artifact each record captured.

    Every record is checked against the closed key set for a capture record, so a record that
    declares its own fingerprint or its own normalised projection is refused rather than
    believed.
    """
    computed: List[Dict[str, Any]] = []
    problems: List[str] = []

    if not isinstance(records, list) or is_vacuous_container(records):
        problems.append("PROJECTION_COVERAGE_GAP: no captured projection carries any content")
        return computed, problems

    for index, record in enumerate(records):
        label = "captured_projections[%d]" % index
        if not isinstance(record, dict) or is_vacuous_container(record):
            problems.append("PROJECTION_COVERAGE_GAP: %s captures nothing" % label)
            continue

        closed = check_closed_key_set(record, PROJECTION_RECORD_KEYS, label=label)
        if not closed.is_valid:
            problems.append("UNKNOWN_PROJECTION_ATTRIBUTE: %s" % closed.message)
            continue

        evidence_key = str(record.get("evidence_key") or "").strip()
        observer_id = str(record.get("observer_id") or "").strip()
        if not evidence_key or not observer_id:
            problems.append(
                "PROJECTION_COVERAGE_GAP: %s names no evidence_key and observer to attribute a capture to"
                % label
            )
            continue
        if observer_id not in media_by_observer:
            problems.append(
                "UNREGISTERED_OBSERVER: %s attributes a capture to observer %r, which the matrix does not register"
                % (label, observer_id)
            )
            continue

        artifact = record.get("captured_artifact")
        if artifact is None and record.get("captured_artifact_path") and artifact_root is not None:
            path = artifact_root / str(record["captured_artifact_path"])
            if not path.is_file():
                problems.append(
                    "PROJECTION_COVERAGE_GAP: %s points at a captured artifact that is not there: %s"
                    % (label, record["captured_artifact_path"])
                )
                continue
            text = path.read_text(encoding="utf-8")
            if path.suffix.lower() == ".json":
                try:
                    artifact = json.loads(text)
                except ValueError:
                    artifact = text
            else:
                artifact = text

        medium = media_by_observer[observer_id]
        # A warning class the census recorded is part of what the observer saw, so it is kept in
        # the projection — computed from the capture against the census, never declared beside it.
        warnings = [
            warning_class
            for warning_class, _marker in matched_warning_classes(artifact, warning_markers)
        ]
        try:
            normalised, fingerprint = capture_projection(
                medium,
                artifact,
                recorded_values=record.get("recorded_fixture_values") or (),
                contract_headers=contract_headers,
                warning_classes=warnings,
            )
        except NormalisationRefused as refused:
            problems.append("%s: %s (%s)" % (refused.code, refused.detail, label))
            continue

        computed.append({
            "evidence_key": evidence_key,
            "observer_id": observer_id,
            "medium": medium,
            "normalised_projection": normalised,
            "fingerprint": fingerprint,
        })

    return computed, problems


def coverage_gaps(
    targets: Set[Tuple[str, str]],
    computed: List[Dict[str, Any]],
) -> List[Tuple[str, str]]:
    """Matrix projection targets with no captured projection: the coverage difference."""
    captured = {(entry["evidence_key"], entry["observer_id"]) for entry in computed}
    return sorted(targets - captured)


def fingerprint_collisions(computed: List[Dict[str, Any]]) -> List[Tuple[str, str, str, str]]:
    """Distinct evidence keys that fingerprint alike for one observer.

    Returns (observer_id, evidence_key_a, evidence_key_b, fingerprint), sorted.
    """
    by_observer: Dict[str, Dict[str, Set[str]]] = {}
    for entry in computed:
        keys = by_observer.setdefault(entry["observer_id"], {}).setdefault(entry["fingerprint"], set())
        keys.add(entry["evidence_key"])

    collisions = []
    for observer_id in sorted(by_observer):
        for fingerprint in sorted(by_observer[observer_id]):
            keys = sorted(by_observer[observer_id][fingerprint])
            for i in range(len(keys)):
                for j in range(i + 1, len(keys)):
                    collisions.append((observer_id, keys[i], keys[j], fingerprint))
    return collisions
