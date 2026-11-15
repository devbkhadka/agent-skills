"""Change-lane triage — route a code change to the cheapest verification lane that is still safe.

Pure: inputs are data (a parsed unified diff, the old/new baseline lexicon, the baseline contract
manifest, the triage config). No git, no filesystem. bin/triage-change gathers the inputs.

Lanes escalate, never de-escalate:
  TWEAK (TIER_1) < EXTENSION (TIER_2) < NEW_CAPABILITY (TIER_3)
The computed lane is the highest lane any reason asks for.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from lib.baseline import diff_lexicons
from lib.verification_policy import TIER_1, TIER_2, TIER_3, classify_file_diff

TWEAK = "TWEAK"
EXTENSION = "EXTENSION"
NEW_CAPABILITY = "NEW_CAPABILITY"

LANES = (TWEAK, EXTENSION, NEW_CAPABILITY)
LANE_RANK = {lane: rank for rank, lane in enumerate(LANES)}
LANE_TIER = {TWEAK: TIER_1, EXTENSION: TIER_2, NEW_CAPABILITY: TIER_3}

# Lexicon element kinds whose addition, change or removal is a new capability: they define what
# exists, where it is bounded, and how values are measured. Other kinds extend existing entities.
CAPABILITY_ELEMENT_KINDS = {
    "entity", "value_object", "boundary", "unit_precision", "value_authority",
    "fact_precedence", "monotonic_clock", "simulated_dependency",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "tweak_paths": [
        "**/*.css", "**/*.scss", "**/*.sass", "**/*.less", "**/*.md", "**/*.mdx",
        "docs/**", "**/i18n/**", "**/locales/**", "**/*.po",
        "tests/**", "**/tests/**", "**/__tests__/**", "**/*.test.*", "**/*.spec.*",
    ],
    "layer_paths": {},
    "adapter_paths": ["**/adapters/**", "**/adapter/**", "**/infrastructure/**", "**/infra/**"],
    "boundary_import_patterns": [
        {"boundary_class": "NETWORK_API",
         "pattern": r"^\s*(?:import|from)\s+(?:requests|httpx|aiohttp|urllib3)\b"},
        {"boundary_class": "NETWORK_API",
         "pattern": r"(?:from\s+|require\(\s*)['\"](?:axios|node-fetch|got|ky|superagent)['\"]"},
        {"boundary_class": "NETWORK_API", "pattern": r"\bfetch\("},
        {"boundary_class": "DATASTORE",
         "pattern": r"^\s*(?:import|from)\s+(?:psycopg2?|sqlalchemy|pymongo|redis|sqlite3|asyncpg)\b"},
        {"boundary_class": "DATASTORE",
         "pattern": r"(?:from\s+|require\(\s*)['\"](?:pg|mysql2?|mongoose|ioredis|redis|@prisma/client|typeorm|sequelize|knex)['\"]"},
        {"boundary_class": "MESSAGE_TRANSPORT",
         "pattern": r"^\s*(?:import|from)\s+(?:pika|kafka|aiokafka|celery)\b"},
        {"boundary_class": "MESSAGE_TRANSPORT",
         "pattern": r"(?:from\s+|require\(\s*)['\"](?:amqplib|kafkajs|bullmq)['\"]"},
        {"boundary_class": "NETWORK_API",
         "pattern": r"^\s*(?:import|from)\s+(?:boto3|botocore)\b"},
        {"boundary_class": "NETWORK_API",
         "pattern": r"(?:from\s+|require\(\s*)['\"]@aws-sdk/[^'\"]+['\"]"},
    ],
    "ignore_tokens": [
        "API", "CSV", "DELETE", "GET", "HEAD", "HTML", "HTTP", "HTTPS", "JSON", "JWT", "NULL",
        "OPTIONS", "PATCH", "POST", "PUT", "SQL", "TODO", "FIXME", "URL", "URI", "UTC", "UTF",
        "UUID", "XML", "YAML", "NOTE", "DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL",
        "ASC", "DESC", "AND", "NOT",
    ],
}

# Domain tokens are UPPER_SNAKE values code compares against: string literals ('PAST_DUE') and
# enum member access (Status.PAST_DUE). Bare constants (MAX_RETRIES = 3) are not domain tokens.
_TOKEN_LITERAL = re.compile(r"""(?<![\w])["'`]([A-Z][A-Z0-9_]{2,})["'`]""")
_TOKEN_MEMBER = re.compile(r"""\b[A-Za-z_][A-Za-z0-9_]*\.([A-Z][A-Z0-9_]{2,})\b""")

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: List[str] = field(default_factory=list)  # each starts with ' ', '+', '-' or '\\'


@dataclass
class FileDiff:
    path: str  # path after the change; the old path for a deletion
    old_path: Optional[str] = None
    is_new: bool = False
    is_deleted: bool = False
    hunks: List[Hunk] = field(default_factory=list)

    def added_lines(self) -> List[str]:
        return [l[1:] for h in self.hunks for l in h.lines if l.startswith("+")]

    def removed_lines(self) -> List[str]:
        return [l[1:] for h in self.hunks for l in h.lines if l.startswith("-")]


def _strip_prefix(path: str) -> str:
    path = path.strip().split("\t", 1)[0]
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_unified_diff(text: str) -> List[FileDiff]:
    """Parse `git diff` / unified diff output into per-file changes."""
    files: List[FileDiff] = []
    current: Optional[FileDiff] = None
    hunk: Optional[Hunk] = None
    old_left = new_left = 0
    old_name: Optional[str] = None

    for line in text.splitlines():
        # Inside a hunk, its header's line counts decide what is content.
        if hunk is not None and (old_left > 0 or new_left > 0):
            marker = line[:1] if line else " "
            if marker == " ":
                old_left, new_left = old_left - 1, new_left - 1
            elif marker == "-":
                old_left -= 1
            elif marker == "+":
                new_left -= 1
            hunk.lines.append(line if line else " ")
            continue
        if line.startswith("\\") and hunk is not None:
            hunk.lines.append(line)
            continue
        hunk = None
        if line.startswith("diff --git "):
            parts = line.split(" ")
            current = FileDiff(path=_strip_prefix(parts[-1]), old_path=_strip_prefix(parts[-2]))
            files.append(current)
            old_name = None
        elif line.startswith("--- "):
            old_name = line[4:].strip()
        elif line.startswith("+++ ") and old_name is not None:
            new_name = line[4:].strip()
            if current is None or current.hunks:
                current = FileDiff(path="")
                files.append(current)
            old_path = None if old_name == "/dev/null" else _strip_prefix(old_name)
            new_path = None if new_name == "/dev/null" else _strip_prefix(new_name)
            current.old_path = old_path
            current.is_new = current.is_new or old_path is None
            current.is_deleted = current.is_deleted or new_path is None
            current.path = new_path or old_path or current.path
            old_name = None
        elif current is None:
            continue
        elif line.startswith("new file mode"):
            current.is_new = True
        elif line.startswith("deleted file mode"):
            current.is_deleted = True
        elif line.startswith("rename to "):
            current.path = line[len("rename to "):].strip()
        else:
            match = _HUNK_HEADER.match(line)
            if match:
                hunk = Hunk(
                    old_start=int(match.group(1)),
                    old_len=int(match.group(2)) if match.group(2) is not None else 1,
                    new_start=int(match.group(3)),
                    new_len=int(match.group(4)) if match.group(4) is not None else 1,
                )
                old_left, new_left = hunk.old_len, hunk.new_len
                current.hunks.append(hunk)
    return files


def reverse_apply(new_text: str, file_diff: FileDiff) -> str:
    """Reconstruct the pre-change file from the post-change file and its diff.

    Raises ValueError when the diff does not match new_text (the file moved on since the diff).
    """
    if file_diff.is_new:
        return ""
    new_lines = new_text.splitlines()
    result = list(new_lines)
    for hunk in sorted(file_diff.hunks, key=lambda h: h.new_start, reverse=True):
        after = [l[1:] for l in hunk.lines if l[:1] in (" ", "+")]
        before = [l[1:] for l in hunk.lines if l[:1] in (" ", "-")]
        start = hunk.new_start - 1 if hunk.new_len else hunk.new_start
        if result[start:start + len(after)] != after:
            raise ValueError("diff hunk @@ +%d,%d does not match %s"
                             % (hunk.new_start, hunk.new_len, file_diff.path))
        result[start:start + len(after)] = before
    trailing = "\n" if new_text.endswith("\n") or not new_text else ""
    return "\n".join(result) + (trailing if result else "")


def glob_match(path: str, pattern: str) -> bool:
    """Glob with `**` spanning directories and `*` / `?` within one path segment."""
    regex = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            regex += ".*"
            i += 2
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    return re.fullmatch(regex, path) is not None


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_match(path, p) for p in patterns)


def merge_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Overlay a triage.json on the defaults. Lists replace defaults, except ignore_tokens which
    extends them (a project adds constants; it never needs HTTP flagged)."""
    merged = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in DEFAULT_CONFIG.items()}
    if config is not None and not isinstance(config, dict):
        raise ValueError("triage config must be a JSON object")
    for key, value in (config or {}).items():
        if key not in DEFAULT_CONFIG:
            raise ValueError("unknown triage config key %r (allowed: %s)"
                             % (key, sorted(DEFAULT_CONFIG)))
        if not isinstance(value, type(DEFAULT_CONFIG[key])):
            raise ValueError("triage config %r must be a JSON %s"
                             % (key, "object" if isinstance(DEFAULT_CONFIG[key], dict) else "array"))
        if key == "ignore_tokens":
            merged[key] = sorted(set(merged[key]) | set(value))
        elif key in merged:
            merged[key] = value
    return merged


def lexicon_strings(lexicon: Any) -> Set[str]:
    """Every string anywhere in a lexicon: names, ids, state tokens, aliases."""
    found: Set[str] = set()
    stack = [lexicon]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            found.add(item)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def domain_tokens(lines: Iterable[str]) -> Set[str]:
    tokens: Set[str] = set()
    for line in lines:
        tokens.update(_TOKEN_LITERAL.findall(line))
        tokens.update(_TOKEN_MEMBER.findall(line))
    return tokens


def present_boundary_classes(manifest: Optional[Dict[str, Any]]) -> Set[str]:
    classes: Set[str] = set()
    for entry in (manifest or {}).get("boundary_classes") or []:
        if isinstance(entry, dict) and entry.get("verdict") == "PRESENT" and entry.get("class_name"):
            classes.add(entry["class_name"])
    return classes


def _reason(code: str, lane: str, detail: str, paths: Iterable[str]) -> Dict[str, Any]:
    return {"code": code, "lane": lane, "detail": detail, "paths": sorted(set(paths))}


def _lexicon_reasons(old_lexicon: Dict[str, Any], new_lexicon: Dict[str, Any],
                     lexicon_path: str) -> List[Dict[str, Any]]:
    delta = diff_lexicons(old_lexicon or {}, new_lexicon or {})
    capability: List[str] = []
    extension: List[str] = []
    for change in ("added", "changed", "removed"):
        for element_id in sorted(delta[change]):
            kind = element_id.split(":", 1)[0]
            label = "%s %s" % (change, element_id)
            if change == "removed" or kind in CAPABILITY_ELEMENT_KINDS:
                capability.append(label)
            else:
                extension.append(label)
    reasons = []
    if capability:
        reasons.append(_reason("LEXICON_CAPABILITY_CHANGE", NEW_CAPABILITY,
                               "; ".join(capability), [lexicon_path]))
    if extension:
        reasons.append(_reason("LEXICON_EXTENSION", EXTENSION, "; ".join(extension),
                               [lexicon_path]))
    return reasons


def next_steps(lane: str) -> List[str]:
    if lane == TWEAK:
        return [
            "Run the repo's tests and the architecture fitness checks (architecture-rules).",
            "Run `baseline check` (domain-baseline). No planner-state, no judge packets.",
        ]
    if lane == EXTENSION:
        return [
            "Run feature-gaps on the affected flows; iterate until GAPS_CLEAN.",
            "If slices or pure modules change, update the execution DAG and re-emit fitness "
            "checks (architecture-rules).",
            "Promote any new state/action/actor terms into the baseline (domain-baseline).",
            "Verify at TIER_2.",
        ]
    return [
        "Amend the project baseline first (domain-baseline): new entities, boundaries or contracts.",
        "Deliver through the full verified-feature-delivery pipeline (TIER_3).",
    ]


def triage(
    files: List[FileDiff],
    baseline_rel: Optional[str],
    old_lexicon: Optional[Dict[str, Any]] = None,
    new_lexicon: Optional[Dict[str, Any]] = None,
    manifest: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    declared_lane: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the lane for a change. baseline_rel is the baseline directory relative to the repo
    root, or None when the project has no baseline."""
    cfg = merge_config(config)
    reasons: List[Dict[str, Any]] = []

    if baseline_rel is None:
        reasons.append(_reason(
            "NO_BASELINE", NEW_CAPABILITY,
            "no project domain baseline; build one with domain-baseline so later changes can "
            "be triaged below NEW_CAPABILITY", []))
    else:
        prefix = baseline_rel.rstrip("/") + "/"
        lexicon_path = prefix + "domain-lexicon.json"
        known_tokens = lexicon_strings(old_lexicon) | lexicon_strings(new_lexicon)
        known_tokens |= set(cfg["ignore_tokens"])
        boundary_present = present_boundary_classes(manifest)
        compiled = [(p["boundary_class"], re.compile(p["pattern"]))
                    for p in cfg["boundary_import_patterns"]]

        unmapped: List[str] = []
        shared_scope: List[str] = []
        contract: List[str] = []
        config_change: List[str] = []
        new_tokens: Dict[str, Set[str]] = {}
        leaks: Dict[str, Set[str]] = {}
        new_boundaries: Dict[str, Set[str]] = {}

        for f in files:
            path = f.path
            if path == lexicon_path:
                reasons.extend(_lexicon_reasons(old_lexicon, new_lexicon, path))
                continue
            if path.startswith(prefix + "contract-fixtures/") or "/contract-fixtures/" in "/" + path:
                contract.append(path)
                continue
            if path == prefix + "triage.json":
                config_change.append(path)
                continue
            if path.startswith(prefix):
                continue  # baseline.json approvals and rendered docs follow the lexicon

            is_tweak_path = _matches_any(path, cfg["tweak_paths"])
            is_layer_path = any(_matches_any(path, globs) for globs in cfg["layer_paths"].values())
            is_adapter = _matches_any(path, cfg["adapter_paths"])
            if not (is_tweak_path or is_layer_path):
                scope = classify_file_diff(path)
                if scope == "UNMAPPED":
                    unmapped.append(path)
                elif scope in ("CORE_SHARED", "FEATURE_SCHEMA") and not is_adapter:
                    shared_scope.append(path)

            if is_tweak_path:
                continue  # styles, docs, copy and tests carry no domain tokens or boundaries
            added = f.added_lines()
            for token in domain_tokens(added) - domain_tokens(f.removed_lines()) - known_tokens:
                new_tokens.setdefault(token, set()).add(path)
            for line in added:
                for boundary_class, pattern in compiled:
                    if not pattern.search(line):
                        continue
                    if not is_adapter:
                        leaks.setdefault(boundary_class, set()).add(path)
                    elif boundary_class not in boundary_present:
                        new_boundaries.setdefault(boundary_class, set()).add(path)

        if contract:
            reasons.append(_reason("CONTRACT_CHANGE", NEW_CAPABILITY,
                                   "external boundary contract fixtures changed", contract))
        for boundary_class in sorted(leaks):
            reasons.append(_reason("BOUNDARY_LEAK", NEW_CAPABILITY,
                                   "%s import outside adapter paths" % boundary_class,
                                   leaks[boundary_class]))
        for boundary_class in sorted(new_boundaries):
            reasons.append(_reason("NEW_BOUNDARY", NEW_CAPABILITY,
                                   "%s is not a PRESENT boundary class in the baseline contract "
                                   "fixtures" % boundary_class, new_boundaries[boundary_class]))
        if new_tokens:
            reasons.append(_reason("NEW_DOMAIN_TOKEN", EXTENSION,
                                   "tokens not in the baseline lexicon: %s"
                                   % ", ".join(sorted(new_tokens)),
                                   {p for ps in new_tokens.values() for p in ps}))
        if shared_scope:
            reasons.append(_reason("SHARED_SCOPE_PATH", EXTENSION,
                                   "shared core, migration or feature-schema paths", shared_scope))
        if unmapped:
            reasons.append(_reason("UNMAPPED_PATH", EXTENSION,
                                   "paths covered by neither tweak_paths, layer_paths nor the "
                                   "verification-policy classifier", unmapped))
        if config_change:
            reasons.append(_reason("TRIAGE_CONFIG_CHANGE", EXTENSION,
                                   "the triage rules themselves changed; review them", config_change))

    computed = max((r["lane"] for r in reasons), key=LANE_RANK.__getitem__, default=TWEAK)
    lane = computed
    underdeclared = False
    if declared_lane is not None:
        if LANE_RANK[declared_lane] < LANE_RANK[computed]:
            underdeclared = True
        elif LANE_RANK[declared_lane] > LANE_RANK[computed]:
            lane = declared_lane
            reasons.append(_reason("LANE_DECLARED", declared_lane,
                                   "declared lane is above the computed %s" % computed, []))

    return {
        "lane": lane,
        "computed_lane": computed,
        "declared_lane": declared_lane,
        "verification_tier": LANE_TIER[lane],
        "underdeclared": underdeclared,
        "reasons": reasons,
        "next_steps": next_steps(lane),
    }
