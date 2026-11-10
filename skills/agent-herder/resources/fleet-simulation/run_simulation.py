#!/usr/bin/env python3
"""End-to-end test of the Agent Herder wave loop against a real fleet.

This launches real CLI agents in real Herdr panes, driven by a real Beads graph. It is an
e2e test, not a unit test: what each composed skill does in isolation is already covered by
that skill's own suite, and re-checking it here would only hide the seam.

    python3 -m unittest discover -s ../../../resource-lease/tests
    python3 -m unittest discover -s ../../../terminal-agent-runner/tests
    python3 -m unittest discover -s ../../../beads-task-scheduler/tests

Everything the agents touch is disposable: a temporary fixture directory, a temporary Beads
project, temporary lease and fleet state, and a Herdr workspace created for this run and
closed at the end. Nothing outside the fixture is read or written, and no agent is spawned
into a workspace that holds unrelated work.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock


SIM_DIR = Path(__file__).resolve().parent
SKILL_DIR = SIM_DIR.parents[1]
SKILLS_ROOT = SKILL_DIR.parent
# agent-herder ships no scripts; the pane driver, lease engine, and graph driver all belong
# to the skills it composes. Resolve them as siblings so this fails loudly if one moves.
FLEET = SKILLS_ROOT / "terminal-agent-runner" / "scripts" / "fleet.py"
PREFLIGHT = SKILLS_ROOT / "terminal-agent-runner" / "scripts" / "preflight.sh"
HERD = SKILLS_ROOT / "terminal-agent-runner" / "scripts" / "herd.sh"
AUTO_HERD = SKILLS_ROOT / "terminal-agent-runner" / "scripts" / "auto_herd.sh"
CLEANUP = SKILLS_ROOT / "terminal-agent-runner" / "scripts" / "cleanup_fleet.sh"
BRIEF = SIM_DIR / "herder_brief.md"
POOL = SKILLS_ROOT / "resource-lease" / "scripts" / "resource_pool.py"
SCHEDULER = SKILLS_ROOT / "beads-task-scheduler" / "scripts" / "scheduler.py"
GRAPH = Path(os.environ.get("AGENT_HERDER_GRAPH", str(SIM_DIR / "task_graph_agy.json")))

PREFIX = "sim"
# The lead and workers are independently configurable.
HERDER_CLI = os.environ.get("AGENT_HERDER_SIM_LEAD_CLI", "claude")
WORKER_CLI = os.environ.get("AGENT_HERDER_SIM_WORKER_CLI", "agy")
WORKER_MODEL = os.environ.get("AGENT_HERDER_SIM_WORKER_MODEL", "gemini-3.7-flash-medium")
HERDER_MODEL = os.environ.get("AGENT_HERDER_SIM_LEAD_MODEL", "")
HERDER_TIMEOUT = float(os.environ.get("AGENT_HERDER_SIM_TIMEOUT", "2400"))
# Three constraints pin this string, and every one of them was found by running real agents.
#
# Quote-free: a verification command containing double quotes is what a real agent embeds
# verbatim and unescaped in its handoff JSON, producing a payload the gate can only refuse.
#
# Allow-listed: AGY's `auto` is accept-edits, where file edits pass but every shell command
# prompts. A command the default-deny policy does not recognise escalates, so an unattended
# AGY bead can only ever be parked -- `python3 verify.py` parked all three of them.
#
# Space-free: `close` matches the reported command exactly, and a pane that hard-wraps
# inside the string loses the character at the wrap point -- `git status --short` came back
# as `git status--short` from two different agents. A single token cannot wrap at a space.
CHECK = "pwd"
# Escalated on purpose: proof that the policy still refuses what it does not recognise.
UNKNOWN_CHECK = "python3 verify.py"

SETTLE_BUDGET = 600.0     # seconds a single agent gets to reach a valid handoff
WATCH_TIMEOUT = float(os.environ.get("AGENT_HERDER_SIM_WATCH_TIMEOUT", "60"))
POLL_INTERVAL = 10.0      # seconds between checks on one worker
IDLE_GRACE = 3            # consecutive idle polls with no handoff before re-prompting
NUDGE_LIMIT = 2           # re-prompts before the bead is parked instead of closed


def configure_worker_cli(tasks: list[dict]) -> list[dict]:
    """Apply the requested worker CLI before preflight and bead metadata are created."""
    configured = []
    for original in tasks:
        task = dict(original)
        task["cli"] = WORKER_CLI
        # AGY and OMP have no verified read-only mapping. Their task prose remains
        # non-mutating, but requesting --read-only would make dispatch fail.
        if WORKER_CLI in {"agy", "omp"}:
            task["read_only"] = False
        configured.append(task)
    return configured


class SimulationTerminated(RuntimeError):
    """A catchable termination request that reaches main()'s evidence path."""


def _handle_sigterm(signum, _frame) -> None:
    raise SimulationTerminated(
        f"simulation received SIGTERM ({signum}); capturing failure evidence"
    )


def install_signal_handlers() -> None:
    """Convert the host's normal termination request into a captured failure."""
    signal.signal(signal.SIGTERM, _handle_sigterm)


# ---------------------------------------------------------------- infrastructure

def load_module(path: Path, name: str):
    """Import a composed skill's script to reuse a pure function it already ships."""
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_refusal(action, fragment: str) -> None:
    """A gate that refuses is the assertion; a gate that passes is the failure."""
    try:
        action()
    except AssertionError as exc:
        expect(fragment in str(exc), f"expected {fragment!r} in refusal, got {exc}")
        return
    raise AssertionError(f"expected a refusal containing {fragment!r}")





def log(message: str) -> None:
    print(f"  {message}", flush=True)


def update_lifecycle(env: dict[str, str], **changes: Any) -> None:
    """Update the optional launcher record without making evidence depend on it."""
    raw_path = env.get("AGENT_HERDER_SIM_LIFECYCLE_FILE")
    if not raw_path:
        return
    path = Path(raw_path)
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(current, dict):
            current = {}
        current.update(changes)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    except (OSError, ValueError, TypeError):
        # The standalone simulation must remain usable if an external launcher record
        # was removed or made unwritable.
        return


def run(argv: list[str], env: dict[str, str], cwd: Path | None = None,
        timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          cwd=cwd, timeout=timeout)


def br(repo: Path, env: dict[str, str], *args: str) -> dict | list:
    result = run(["br", *args, "--json"], env, cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(f"br {' '.join(args)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def sched(env: dict[str, str], *args: str, timeout: float = 120.0) -> dict:
    result = run([sys.executable, str(SCHEDULER), *args, "--json"], env, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"scheduler {args[0]} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def sched_expect_refusal(env: dict[str, str], fragment: str, *args: str) -> None:
    result = run([sys.executable, str(SCHEDULER), *args, "--json"], env)
    combined = (result.stderr or "") + " " + (result.stdout or "")
    expect(result.returncode != 0, f"scheduler {args[0]} should have refused: {result.stdout}")
    expect(fragment in combined, f"expected {fragment!r} in output: {combined!r}")


def fleet(env: dict[str, str], *args: str, timeout: float = 180.0) -> subprocess.CompletedProcess:
    """Drive fleet.py against this run's un-attached session.

    Only the harness's own pre-pane checks and teardown use this. The wave loop itself is
    driven by a lead agent following the skill, which reaches fleet.py through herd.sh.
    """
    session = env.get("AGENT_HERDER_SIM_SESSION")
    prefix = ["--session", session, "--allow-cross-session"] if session else []
    return run([sys.executable, str(FLEET), *prefix, *args], env, timeout=timeout)


def watch_for_action(env: dict[str, str], session: str) -> None:
    """Block in the runner until an actionable fleet event or the bounded timeout."""
    result = run(
        ["bash", str(HERD), "--session", session, "watch", "--until", "actionable",
         "--timeout", str(WATCH_TIMEOUT)],
        env,
        timeout=WATCH_TIMEOUT + 30.0,
    )
    if result.returncode == 0:
        return
    try:
        event = json.loads(result.stdout or "{}").get("event")
    except json.JSONDecodeError:
        event = None
    # fleet.py watch uses exit 1 for its ordinary bounded timeout. Any other
    # non-zero result is a runner/session failure and must enter evidence capture.
    if result.returncode == 1 and event == "timeout":
        return
    raise RuntimeError(
        f"watch failed for session {session} (rc={result.returncode}): "
        f"{(result.stderr or result.stdout).strip()}"
    )


def herdr_argv(env: dict[str, str], *args: str) -> list[str]:
    """Every raw herdr call targets this run's own session, never the ambient socket."""
    session = env.get("AGENT_HERDER_SIM_SESSION")
    return ["herdr", "--session", session, *args] if session else ["herdr", *args]


def herdr_json(env: dict[str, str], *args: str) -> dict:
    result = run(herdr_argv(env, *args), env)
    if result.returncode != 0:
        raise RuntimeError(f"herdr {' '.join(args)} failed: {result.stderr.strip()}")
    out = (result.stdout or "").strip()
    try:
        return json.loads(out)
    except Exception:
        return {"text": out}


def pool_status(env: dict[str, str], group: str) -> dict:
    result = run([sys.executable, str(POOL), "status", "--group", group], env)
    data = json.loads(result.stdout or "{}")
    return (data.get(group) or {}).get("resources") or {}


def held_by(env: dict[str, str], group: str, agent: str) -> set[str]:
    return {
        name for name, entry in pool_status(env, group).items()
        if any(h.get("agent") == agent for h in entry.get("holders", []))
    }


def issue_status(repo: Path, env: dict[str, str], bead: str) -> dict:
    shown = br(repo, env, "show", bead)
    return shown[0] if isinstance(shown, list) and shown else shown


# ---------------------------------------------------------------- fixture

def preflight(env: dict[str, str], clis: str, session: str) -> None:
    result = run(
        ["bash", str(PREFLIGHT), "--session", session,
         "--allow-cross-session", "--cli", clis],
        env,
        timeout=600.0,
    )
    sys.stdout.write(result.stdout)
    # rc 2 means at least one CLI is unusable. The graph needs all of them, so an e2e run
    # that continues would report a fleet failure as a herder failure.
    expect(result.returncode == 0,
           f"preflight refused the fleet (rc={result.returncode}); do not spawn")


def build_fixture(repo: Path, env: dict[str, str], tasks: list[dict]) -> dict[str, str]:
    for relative in ("src", "db", "docs"):
        (repo / relative).mkdir(parents=True, exist_ok=True)
    for relative in ("src/api.py", "src/contracts.py", "db/schema.sql", "docs/notes.md"):
        (repo / relative).touch()
    import shutil
    shutil.copytree(SKILLS_ROOT, repo / "skills", dirs_exist_ok=True)
    # A git repository is the normal case for fleet work, and some CLIs treat an
    # untracked scratch directory as untrusted.
    run(["git", "init", "-q"], env, cwd=repo)
    result = run(["br", "init", "--prefix", PREFIX], env, cwd=repo)
    expect(result.returncode == 0, f"br init failed: {result.stderr.strip()}")

    beads: dict[str, str] = {}
    for task in tasks:
        created = br(
            repo, env, "create", "--title", task["id"], "--slug", task["id"],
            "--priority", str(task.get("priority", 1)),
            "--description", task["objective"],
        )
        issue = created[0] if isinstance(created, list) and created else created
        beads[task["id"]] = issue["id"]

    for task in tasks:
        for dep in task.get("deps", []):
            br(repo, env, "dep", "add", beads[task["id"]], beads[dep])

    # The metadata contract: everything the herder needs to turn a bead into runner flags
    # travels under agent_context.herder.
    for task in tasks:
        herder = {"cli": task["cli"], "verification": [CHECK]}
        if task.get("model"):
            herder["model"] = task["model"]
        if task.get("read_only"):
            herder["read_only"] = True
        else:
            herder["resources"] = task["resources"]
        if task.get("group"):
            herder["group"] = task["group"]
        br(repo, env, "update", beads[task["id"]],
           # The bead gate matches the reported command string exactly, so say so. An
           # agent that paraphrases the command passes `complete` and is refused by `close`.
           "--acceptance-criteria",
           f"Run {CHECK} from the repository root. In the handoff, the verification "
           f"command must be reported verbatim as {CHECK} with its exit code.",
           "--agent-context", json.dumps({"herder": herder}))
    return beads


@dataclass
class SessionDaemon:
    session: str
    pid: int
    log_path: Path
    workspace_id: str


def daemon_output(daemon: SessionDaemon, limit: int = 2000) -> str:
    try:
        text = daemon.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"unavailable ({exc})"
    return text[-limit:].replace("\n", " ").strip() or "(no daemon output)"


def assert_session_live(env: dict[str, str], daemon: SessionDaemon, phase: str) -> None:
    """Fail at the ownership boundary when the harness-owned daemon disappears."""
    probe = run(["herdr", "--session", daemon.session, "status", "server", "--json"], env)
    try:
        running = json.loads(probe.stdout or "{}").get("status") == "running"
    except json.JSONDecodeError:
        running = False
    if running:
        return
    raise AssertionError(
        f"un-attached session {daemon.session} stopped during {phase}; "
        f"daemon_pid={daemon.pid}; daemon_output={daemon_output(daemon)}"
    )


def open_session(env: dict[str, str], repo: Path) -> SessionDaemon:
    """Bootstrap through herd.sh and retain its opt-in daemon evidence."""
    session = f"agent-herder-sim-{os.getpid()}"
    env["AGENT_HERDER_SIM_SESSION"] = session
    result = run(
        ["bash", str(HERD), "--session", session, "--emit-daemon-evidence",
         "init", "--cwd", str(repo)],
        env,
        timeout=60.0,
    )
    expect(result.returncode == 0,
           f"un-attached session {session} init failed: "
           f"{(result.stderr or result.stdout).strip()}")
    try:
        evidence = json.loads(result.stdout)
        pid = int(evidence["daemon_pid"])
        log_path = Path(evidence["daemon_log_path"])
        workspace_id = str(evidence["workspace_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AssertionError(
            f"un-attached session {session} init returned incomplete daemon evidence: "
            f"{result.stdout.strip()}"
        ) from exc
    daemon = SessionDaemon(session, pid, log_path, workspace_id)
    assert_session_live(env, daemon, "startup")
    env.pop("HERDR_PANE_ID", None)
    env["HERDR_ACTIVE_WORKSPACE_ID"] = workspace_id
    log(f"un-attached session {session} started through herd.sh")
    update_lifecycle(env, status="session-ready", session=session, workspace_id=workspace_id,
                     daemon_pid=pid, daemon_log_path=str(log_path))
    return daemon


def close_session(env: dict[str, str], daemon: SessionDaemon) -> None:
    result = run(["bash", str(CLEANUP), "--session", daemon.session], env, timeout=120.0)
    if result.returncode != 0:
        raise RuntimeError(
            f"cleanup failed for session {daemon.session} (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )
    listed = run(["herdr", "session", "list"], env)
    expect(daemon.session not in listed.stdout,
           f"cleanup left disposable session {daemon.session} registered: "
           f"{listed.stdout.strip()}")
    log(f"cleanup removed disposable session {daemon.session}")
    update_lifecycle(env, status="cleaned", cleanup="passed", session=daemon.session)
def check_unattached(env: dict[str, str], session: str) -> None:
    """The fleet must live in its own session, not beside the herder's own work.

    This is the assertion the simulation existed without: it drove whatever socket was
    ambient, so a regression that put workers in the herder's attached session passed
    every other check in this file.
    """
    ambient = env.get("HERDR_SOCKET_PATH") or ""
    expect(session not in ambient,
           "the run's session must not be the herder's own attached socket")
    expect(session in run(["herdr", "session", "list"], env).stdout,
           f"session {session} is not registered with herdr")
    log(f"fleet is un-attached: herding into {session}")


def open_workspace(env: dict[str, str], repo: Path) -> str:
    """A Herdr workspace of this run's own, so no tab lands beside unrelated work."""
    data = herdr_json(env, "workspace", "create", "--cwd", str(repo),
                      "--label", "agent-herder-simulation", "--no-focus")
    workspace = data["result"]["workspace"]["workspace_id"]
    # fleet.py resolves the herding workspace from HERDR_PANE_ID first. The lead here is a
    # process, not a pane, so declare the target explicitly rather than let it infer one.
    env.pop("HERDR_PANE_ID", None)
    env["HERDR_ACTIVE_WORKSPACE_ID"] = workspace
    log(f"workspace {workspace} created for this run")
    return workspace


def close_workspace(env: dict[str, str], workspace: str) -> None:
    run(herdr_argv(env, "workspace", "close", workspace), env)


# ---------------------------------------------------------------- the wave loop

def ready_slugs(env: dict[str, str], repo: Path, names: dict[str, str]) -> dict[str, dict]:
    out = sched(env, "ready", "--project", str(repo), "--limit", "20")
    return {names[c["id"]]: c for c in out["ready"]}


def spawn_flags(candidate: dict, repo: Path) -> dict:
    """Translate agent_context.herder into runner flags -- the contract in SKILL.md.

    Nothing but that table validates the mapping: the scheduler treats `herder` as opaque
    and the runner has never heard of it.
    """
    context = candidate["context"]
    expect("error" not in context, f"{candidate['id']} context invalid: {context}")
    return {
        "cli": context["cli"],
        "model": context.get("model"),
        "approval_mode": context.get("approval_mode", "auto"),
        "read_only": context.get("read_only", False),
        "cwd": context["cwd"],
        # A shared cross-repo resource declares its own group; everything else is scoped
        # to the repository the bead came from.
        "group": context.get("group") or f"repo:{repo}",
        "resources": context.get("resources", []),
        "verification": context["verification"],
    }


def session_key(session: str | None) -> str:
    """Stable state namespace for the targeted Herdr session."""
    if session:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", session)
    return "default"


def get_fleet_assignments(env: dict[str, str], session: str | None) -> dict[str, dict]:
    """Read assignment records for all agents in the session from fleet state."""
    state_dir = Path(env.get("AGENT_HERDER_STATE_DIR",
                             str(Path.home() / ".config" / ".agents" / "herder")))
    tasks_file = state_dir / session_key(session) / "tasks.json"
    if not tasks_file.is_file():
        return {}
    try:
        data = json.loads(tasks_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_agent_pane(env: dict[str, str], agent: str,
                    reader: Any = None, lines: int = 500) -> tuple[str, str, bool]:
    """Read agent pane text, trying multiple sources if needed.

    Returns (content, source_used, is_empty).
    If genuinely empty or on error, content explains the condition and sources attempted.
    """
    if reader is not None:
        try:
            res = reader(agent, source="recent-unwrapped", lines=lines)
        except TypeError:
            try:
                res = reader(agent)
            except TypeError:
                res = reader(env, agent)
        if isinstance(res, tuple) and len(res) == 2:
            text, src = res
        elif isinstance(res, str):
            text, src = res, "fake-reader"
        else:
            text, src = str(res or ""), "fake-reader"
        if not text or not text.strip():
            return f"[empty: read via source '{src}' returned no content]", src, True
        return text, src, False

    attempts: list[str] = []
    # Try recent-unwrapped for full dewrapped history, visible screen if working/blocked,
    # and recent buffer as a fallback.
    for source in ("recent-unwrapped", "visible", "recent"):
        try:
            proc = run(herdr_argv(env, "agent", "read", agent, "--source", source,
                                  "--lines", str(lines)), env)
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            text = ""
            if proc.returncode == 0 and out:
                try:
                    data = json.loads(out)
                    if isinstance(data, dict):
                        if "error" in data:
                            attempts.append(f"source '{source}' error: {data['error']}")
                            continue
                        text = data.get("result", {}).get("read", {}).get("text", "")
                    else:
                        text = out
                except Exception:
                    text = out
                if text and text.strip():
                    return text, source, False
                attempts.append(f"source '{source}': empty")
            else:
                reason = err or out or f"exit code {proc.returncode}"
                attempts.append(f"source '{source}' failed: {reason}")
        except Exception as exc:
            attempts.append(f"source '{source}' exception: {exc}")

    summary = "; ".join(attempts) if attempts else "no sources attempted"
    source_used = "recent-unwrapped"
    return f"[empty: {summary}]", source_used, True


def capture_simulation_evidence(
    env: dict[str, str],
    session: str,
    lead_agent: str | None = None,
    dest_dir: Path | None = None,
    reader: Any = None,
    daemon: SessionDaemon | None = None,
) -> dict[str, Path]:
    """Capture lead and worker panes on failure before session teardown.

    Writes pane outputs to a persistent directory, logs status, assignment,
    and printed paths so failed runs can be diagnosed.
    """
    env["_SIM_EVIDENCE_CAPTURED"] = "1"
    if dest_dir is None:
        dest_dir = Path(tempfile.gettempdir()) / f"sim-evidence-{session}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    captured_files: dict[str, Path] = {}

    # Preserve raw session responses even when the daemon is already stopped. A failed
    # response is evidence, not a reason to skip the cleanup decision.
    status = run(["herdr", "--session", session, "status", "server", "--json"], env)
    status_file = dest_dir / "session-status.json"
    status_file.write_text(json.dumps({
        "session": session, "returncode": status.returncode,
        "stdout": status.stdout, "stderr": status.stderr,
    }, indent=2) + "\n", encoding="utf-8")
    captured_files["session-status"] = status_file

    agents = run(herdr_argv(env, "agent", "list"), env)
    agents_file = dest_dir / "agent-list.json"
    agents_file.write_text(json.dumps({
        "session": session, "returncode": agents.returncode,
        "stdout": agents.stdout, "stderr": agents.stderr,
    }, indent=2) + "\n", encoding="utf-8")
    captured_files["agent-list"] = agents_file

    if daemon:
        daemon_file = dest_dir / "session-daemon.log"
        daemon_file.write_text(
            "\n".join((
                f"Session: {daemon.session}",
                f"Daemon PID: {daemon.pid}",
                "--- Bounded Daemon Output ---",
                daemon_output(daemon),
                "",
            )),
            encoding="utf-8",
        )
        captured_files["session-daemon"] = daemon_file
        log(f"saved session daemon evidence to {daemon_file}")

    assignments = get_fleet_assignments(env, session)
    assignments_file = dest_dir / "assignments.json"
    assignments_file.write_text(json.dumps(assignments, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
    captured_files["assignments"] = assignments_file

    leases = run([sys.executable, str(POOL), "status"], env)
    leases_file = dest_dir / "lease-state.json"
    leases_file.write_text(json.dumps({
        "returncode": leases.returncode, "stdout": leases.stdout, "stderr": leases.stderr,
    }, indent=2) + "\n", encoding="utf-8")
    captured_files["lease-state"] = leases_file

    # Discover all agents in the session
    agents_in_session: set[str] = set()
    if lead_agent:
        agents_in_session.add(lead_agent)
    agents_in_session.update(assignments.keys())

    try:
        agent_list_data = herdr_json(env, "agent", "list")
        for entry in agent_list_data.get("result", {}).get("agents", []):
            name = entry.get("name")
            if name:
                agents_in_session.add(name)
    except Exception:
        pass

    if not lead_agent:
        for name in agents_in_session:
            if name.startswith("sim-herder-"):
                lead_agent = name
                break
        if not lead_agent and agents_in_session:
            lead_agent = sorted(agents_in_session)[0]

    ordered_agents: list[str] = []
    if lead_agent and lead_agent in agents_in_session:
        ordered_agents.append(lead_agent)
    ordered_agents.extend(sorted(a for a in agents_in_session if a != lead_agent))

    for name in ordered_agents:
        is_lead = (name == lead_agent)
        prefix = "lead" if is_lead else "worker"

        try:
            agent_entry = herdr_agent(env, name)
            status = (agent_entry.get("agent_status") if agent_entry else "unknown") or "unknown"
        except Exception:
            status = "unknown"
        assignment = assignments.get(name, {})

        if is_lead:
            log(f"lead herder state: agent_status={status!r}, assignment={json.dumps(assignment)}")

        text, source_used, is_empty = read_agent_pane(env, name, reader=reader, lines=500)

        record_lines = [
            f"Agent: {name} ({prefix})",
            f"Session: {session}",
            f"Last Reported agent_status: {status}",
            f"Assignment: {json.dumps(assignment, indent=2)}",
            f"Read Source: {source_used}",
            f"Captured At: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "--- Pane Content ---",
            text,
        ]
        out_file = dest_dir / f"{prefix}-{name}.txt"
        out_file.write_text("\n".join(record_lines) + "\n", encoding="utf-8")
        captured_files[name] = out_file
        log(f"saved {prefix} pane to {out_file}")

        if is_lead:
            if is_empty:
                log(f"lead herder pane tail: {text}")
            else:
                tail = text[-2000:] if len(text) > 2000 else text
                log(f"lead herder pane tail (source: {source_used}):\n{tail}")

    return captured_files


def capture_failure_evidence(env: dict[str, str], session: str,
                             daemon: SessionDaemon) -> dict[str, Path]:
    """Capture the records required before a failed disposable session is removed."""
    captured = capture_simulation_evidence(
        env, session, lead_agent=f"sim-herder-{os.getpid()}", daemon=daemon)
    required = {"session-status", "agent-list", "session-daemon"}
    missing = sorted(required - captured.keys())
    if missing:
        raise RuntimeError(
            f"failure evidence for session {session} is incomplete; missing {missing}"
        )
    return captured


def herdr_agent(env: dict[str, str], agent: str) -> dict | None:
    """Look the agent up by the name `spawn` gave it, not by its CLI label."""
    data = herdr_json(env, "agent", "list")
    for entry in data.get("result", {}).get("agents", []):
        if entry.get("name") == agent:
            return entry
    return None


def valid_report(parsed: object) -> bool:
    """Accept only the full report contract required by herder_brief.md."""
    if not isinstance(parsed, dict):
        return False
    if not all(isinstance(parsed.get(key), list) for key in ("closed", "parked", "errors")):
        return False
    if not all(isinstance(slug, str) for slug in parsed["closed"]):
        return False
    if not all(
        isinstance(entry, dict)
        and all(isinstance(entry.get(key), str) for key in ("slug", "reason", "detail"))
        for entry in parsed["parked"]
    ):
        return False
    return all(
        isinstance(entry, dict)
        and all(isinstance(entry.get(key), str)
                for key in ("phase", "slug", "command", "stderr", "what_i_did"))
        for entry in parsed["errors"]
    )


def read_report_file(path: Path) -> dict:
    """Read the lead's report from disk, the contract the brief asks for."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if valid_report(parsed) else {}


def herder_report(text: str) -> dict:
    """Pull the lead's SIMULATION_REPORT line out of its pane transcript.

    Read from the end: the brief is echoed into the pane before the lead runs, so the
    literal token appears in the instructions too, and the first match is that echo.
    """
    lines = (text or "").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if "SIMULATION_REPORT:" not in lines[index]:
            continue
        # A pane hard-wraps: the report is one logical line but arrives as many, split at
        # the pane width with each continuation indented. Rejoin from the marker to the
        # end and strip the wrap indentation, then find the JSON object inside.
        joined = "".join(part.strip() for part in
                         [lines[index].split("SIMULATION_REPORT:", 1)[1]] + lines[index + 1:])
        start = joined.find("{")
        if start < 0:
            continue
        depth, end = 0, -1
        in_string = escaped = False
        for offset, char in enumerate(joined[start:], start):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = offset + 1
                    break
        if end < 0:
            continue
        try:
            parsed = json.loads(joined[start:end])
        except json.JSONDecodeError:
            continue
        if valid_report(parsed):
            return parsed
    return {}


def check_topology(env: dict[str, str], lead_agent: str,
                   worker_agents: set[str], session: str | None = None) -> None:
    """Prove the requested lead/worker CLI split before the lead is parked."""
    entries: dict[str, dict] = {}
    state_dir = Path(env.get("AGENT_HERDER_STATE_DIR",
                             str(Path.home() / ".config" / ".agents" / "herder")))
    target_session = session or env.get("AGENT_HERDER_SIM_SESSION")
    if target_session:
        spawn_file = state_dir / session_key(target_session) / "spawned_agents.json"
        if spawn_file.is_file():
            try:
                s_data = json.loads(spawn_file.read_text(encoding="utf-8"))
                if isinstance(s_data, dict):
                    entries.update(s_data)
            except Exception:
                pass
        tasks_file = state_dir / session_key(target_session) / "tasks.json"
        if tasks_file.is_file():
            try:
                t_data = json.loads(tasks_file.read_text(encoding="utf-8"))
                if isinstance(t_data, dict):
                    for k, v in t_data.items():
                        if k not in entries and isinstance(v, dict):
                            entries[k] = {"name": k, "agent": v.get("cli", WORKER_CLI)}
            except Exception:
                pass
        handoffs_file = state_dir / session_key(target_session) / "handoffs.json"
        if handoffs_file.is_file():
            try:
                h_data = json.loads(handoffs_file.read_text(encoding="utf-8"))
                if isinstance(h_data, dict):
                    for k, v in h_data.items():
                        if k not in entries and isinstance(v, dict):
                            entries[k] = {"name": k, "agent": v.get("cli", WORKER_CLI)}
            except Exception:
                pass

    try:
        data = herdr_json(env, "agent", "list")
        for entry in data.get("result", {}).get("agents", []):
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                entries[entry["name"]] = entry
    except Exception:
        pass

    missing = sorted(({lead_agent} | worker_agents) - entries.keys())
    expect(not missing, f"topology agents missing from Herdr: {missing}")

    def cli(entry: dict) -> str | None:
        return entry.get("agent") or entry.get("kind") or entry.get("cli")

    expect(cli(entries[lead_agent]) == HERDER_CLI,
           f"lead {lead_agent} used {cli(entries[lead_agent])!r}, expected {HERDER_CLI!r}")
    wrong_workers = {
        name: cli(entries[name]) for name in sorted(worker_agents)
        if cli(entries[name]) != WORKER_CLI
    }
    expect(not wrong_workers,
           f"workers did not use only {WORKER_CLI}: {wrong_workers}")
    if HERDER_CLI == "claude" and WORKER_CLI != "claude":
        claude_workers = sorted(name for name in worker_agents if cli(entries[name]) == "claude")
        expect(not claude_workers, f"unexpected Claude worker process(es): {claude_workers}")
    elif HERDER_CLI != "claude" and WORKER_CLI != "claude":
        claude_agents = sorted(name for name, entry in entries.items() if cli(entry) == "claude")
        expect(not claude_agents, f"unexpected Claude process(es): {claude_agents}")
    claude_tag = "lead" if HERDER_CLI == "claude" else ("workers" if WORKER_CLI == "claude" else "none")
    log(f"topology verified: lead={HERDER_CLI}; "
        f"{len(worker_agents)} worker(s)={WORKER_CLI}; Claude={claude_tag}")


def run_herder(env, repo, daemon: SessionDaemon, temp_root,
               worker_agents: set[str], names: dict[str, str] | None = None) -> dict:
    """Hand the graph to a lead agent following the skill, and read back its report.

    This is the point of the rewrite. The harness used to reimplement the wave loop in
    Python, which meant every improvement to the skill needed a matching edit here and a
    regression in the skill's own prose was invisible. Now the skill is what runs, and the
    harness only sets up the fixture and judges the outcome.
    """
    session = daemon.session
    # Placeholder substitution, not str.format: the brief contains a literal JSON example
    # and every brace in it would have to be doubled.
    fixture_skill_dir = repo / "skills" / "agent-herder"
    brief = BRIEF.read_text(encoding="utf-8")
    for token, value in (("@@SKILL_DIR@@", fixture_skill_dir), ("@@REPO@@", repo),
                         ("@@SESSION@@", session), ("@@SCRATCH@@", temp_root),
                         ("@@WORKER_CLI@@", WORKER_CLI), ("@@WORKER_MODEL@@", WORKER_MODEL)):
        brief = brief.replace(token, str(value))
    expect("@@" not in brief, "the herder brief has an unsubstituted placeholder")
    brief_path = repo / "herder_brief.md"
    brief_path.write_text(brief, encoding="utf-8")

    agent = f"sim-herder-{os.getpid()}"
    task = (f"Read {brief_path} and follow it exactly. It names the agent-herder skill to "
            f"follow, the beads project to herd, and the report line to print when done.")
    # `spawn` offers exactly two shapes: --read-only (leases nothing, but forces plan mode)
    # or --group plus at least one --resource. The lead must run commands, so it takes the
    # second -- with a group and resource of its own, so the leases the drain check looks
    # at at the end are the workers' and never the lead's.
    lead_group = f"lead:{agent}"
    model_flags = ["--model", HERDER_MODEL] if HERDER_MODEL else []
    launched = None
    for attempt in range(1, 4):
        launched = fleet(env, "spawn", "--name", agent, "--cli", HERDER_CLI,
                         "--cwd", str(repo), "--task", task, "--approval-mode", "auto",
                         "--group", lead_group, "--resource", f"role:herder:{agent}",
                         *model_flags)
        startup_blocked = ("agent_not_ready" in launched.stderr or
                           "blocked during startup" in launched.stderr or
                           "did not accept its initial task" in launched.stderr)
        if launched.returncode == 0 or not startup_blocked or attempt == 3:
            break
        time.sleep(2 * attempt)
    expect(launched.returncode == 0,
           f"the {HERDER_CLI} lead could not be spawned: {launched.stderr.strip()}")
    log(f"lead herder {agent} ({HERDER_CLI}) spawned; following the skill from here")
    update_lifecycle(env, phase="herding", status="running", lead_agent=agent,
                     lead_cli=HERDER_CLI, session=session, started_at=time.time())

    assert_session_live(env, daemon, "lead startup before first-wave handoff")
    report_path = temp_root / "simulation_report.json"
    repo_report_path = repo / "simulation_report.json"
    progress_path = temp_root / "simulation_progress.json"
    repo_progress_path = repo / "simulation_progress.json"
    last_reported_wave = 0
    last_reported_closed: set[str] = set()
    deadline = time.time() + HERDER_TIMEOUT
    report: dict = {}
    nudges = 0
    while time.time() < deadline:
        assert_session_live(env, daemon, "lead supervision")
        watch_for_action(env, session)
        assert_session_live(env, daemon, "lead watch handoff")

        # 1. Read progress file if available
        prog_data = {}
        for p in (progress_path, repo_progress_path):
            if p.is_file():
                try:
                    loaded = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        prog_data = loaded
                        break
                except (OSError, json.JSONDecodeError):
                    pass

        # 2. Check durable Beads closed state
        current_closed_slugs: list[str] = []
        try:
            closed_beads_list = br(repo, env, "list", "--status", "closed", "--json")
            if isinstance(closed_beads_list, list):
                current_closed_slugs = [
                    (names or {}).get(item.get("id"), item.get("id", ""))
                    for item in closed_beads_list if isinstance(item, dict)
                ]
        except Exception:
            pass

        wave = prog_data.get("wave") or prog_data.get("current_wave", 0)
        feedback = prog_data.get("feedback") or prog_data.get("wave_reflections", {})
        new_closes = set(current_closed_slugs) - last_reported_closed

        if (wave and wave > last_reported_wave) or new_closes:
            last_reported_closed.update(current_closed_slugs)
            if wave and wave > last_reported_wave:
                last_reported_wave = wave
                log(f"[progress] Wave {wave} milestone: {len(current_closed_slugs)} closed ({', '.join(sorted(current_closed_slugs))})")
                if isinstance(feedback, dict):
                    for k in ("gotchas", "inefficiencies", "suggestions"):
                        items = feedback.get(k, [])
                        if items and isinstance(items, list):
                            log(f"  [wave {wave} {k}] {'; '.join(str(it) for it in items)}")
            elif new_closes:
                log(f"[progress] Newly closed bead(s): {', '.join(sorted(new_closes))} (total closed: {len(current_closed_slugs)})")

            update_lifecycle(
                env,
                phase=f"wave-{wave}" if wave else "herding",
                status="running",
                current_wave=wave,
                closed_beads=sorted(current_closed_slugs),
                latest_feedback=feedback if isinstance(feedback, dict) else {},
                last_heartbeat=time.time(),
            )

        # The file is the contract; the pane is a fallback for a lead that printed the
        # JSON anyway. A pane drops the character at each wrap point, so a long report
        # read back from the terminal can be corrupt in ways no parser can undo.
        report = read_report_file(report_path) or read_report_file(repo_report_path)
        if not report:
            report = herder_report(run(herdr_argv(env, "agent", "read", agent,
                                                  "--source", "recent-unwrapped",
                                                  "--lines", "400"), env).stdout)
        if report:
            break
        entry = herdr_agent(env, agent)
        if entry and entry.get("agent_status") == "idle" and nudges < 2:
            try:
                agents_data = herdr_json(env, "agent", "list")
                active_workers = [
                    a.get("name") for a in agents_data.get("result", {}).get("agents", [])
                    if a.get("name") != agent and a.get("agent_status") not in {"closed", "stopped"}
                ]
            except Exception:
                active_workers = []

            if active_workers:
                continue

            try:
                ready_out = run([sys.executable, str(SCHEDULER), "--project", str(repo), "ready", "--json"], env).stdout
                ready_beads = json.loads(ready_out or "[]")
            except Exception:
                ready_beads = []

            if ready_beads:
                nudges += 1
                log(f"lead herder is idle with {len(ready_beads)} ready beads remaining; nudging to continue ({nudges}/2)")
                fleet(env, "prompt", "--agent", agent, "--related", "--text",
                      "Current wave completed. Continue with scheduler.py ready, claim, spawn, and "
                      "auto_herd.sh for the next wave until ready returns nothing.")
                continue

            nudges += 1
            log(f"lead herder is idle with no report; asking for it ({nudges}/2)")
            fleet(env, "prompt", "--agent", agent, "--related", "--text",
                  f"Write your report to {report_path} now, as valid JSON, then print "
                  f"SIMULATION_REPORT: written")
    if not report:
        capture_simulation_evidence(env, session, lead_agent=agent, daemon=daemon)
    expect(bool(report),
           f"the lead herder never printed a SIMULATION_REPORT line within "
           f"{HERDER_TIMEOUT:.0f}s; nothing can be judged about this run")

    for entry in report.get("errors", []):
        log(f"herder error [{entry.get('phase', '?')}] {entry.get('slug', '-')}: "
            f"{str(entry.get('command', ''))[:100]} -> "
            f"{str(entry.get('stderr', ''))[:160]} | {str(entry.get('what_i_did', ''))[:120]}")
    if isinstance(report.get("retrospective"), dict):
        retro = report["retrospective"]
        log("== herder retrospective ==")
        for section in ("improvements", "inefficiencies", "difficulties"):
            items = retro.get(section, [])
            if items and isinstance(items, list):
                log(f"  [{section.upper()}]")
                for it in items:
                    log(f"    - {it}")
    check_topology(env, agent, worker_agents, session=session)
    # Park frees the lead's own lease and closes its prompt; its group is separate from
    # every worker group, so this cannot disturb work still being judged.
    fleet(env, "park", "--agent", agent, "--group", lead_group,
          "--reason", "simulation complete", "--cancel")
    return report


def results_from_report(env, repo, names, beads, report) -> list[dict]:
    """Judge on durable state, not on what the lead claimed.

    The lead is a real agent and may report optimistically. A bead counts as closed only
    if Beads says it is closed; the report supplies the reason a bead was parked, which
    durable state cannot.
    """
    to_slug = lambda s: names.get(s, s)
    parked = {to_slug(entry.get("slug", "")): entry for entry in report.get("parked", [])
              if isinstance(entry, dict) and entry.get("slug")}
    claimed = {to_slug(s) for s in report.get("closed", []) if isinstance(s, str)}

    errors_by_slug: dict[str, list[dict]] = {}
    for err in report.get("errors", []):
        if isinstance(err, dict):
            s = to_slug(err.get("slug", ""))
            errors_by_slug.setdefault(s, []).append(err)

    results = []
    for slug, bead in beads.items():
        shown = issue_status(repo, env, bead)
        status = shown.get("status")
        closed = status == "closed"
        row = {"slug": slug, "bead": bead, "closed": closed, "durable_status": status,
               "assignee": shown.get("assignee")}
        if not closed:
            detail = parked.get(slug, {})
            row["outcome"] = detail.get("reason") or ("claimed-closed-but-open"
                                                      if slug in claimed else "not-reached")
            row["detail"] = detail.get("detail", "")
        results.append(row)

    overclaimed = sorted(s for s in claimed if not any(
        r["slug"] == s and r["closed"] for r in results))

    if overclaimed:
        lines = [
            f"FAIL: the lead reported {len(overclaimed)} bead(s) closed that Beads still shows open: {overclaimed}",
            "",
            "Overclaimed bead diagnostic breakdown:",
        ]
        for slug in overclaimed:
            bead_id = beads.get(slug, "unknown")
            issue = issue_status(repo, env, bead_id) if bead_id != "unknown" else {}
            st = issue.get("status", "unknown")
            assignee = issue.get("assignee") or "(none)"
            lines.append(f"  • [{slug}] (id={bead_id}): durable_status={st}, assignee={assignee}")
            if slug in parked:
                p = parked[slug]
                lines.append(f"      Report parked entry: reason={p.get('reason')!r}, detail={p.get('detail')!r}")
            else:
                lines.append("      Report parked entry: (none)")
            errs = errors_by_slug.get(slug, [])
            if errs:
                lines.append(f"      Report recorded errors ({len(errs)}):")
                for e in errs:
                    phase = e.get("phase", "?")
                    cmd = e.get("command", "")
                    stderr = e.get("stderr", "")
                    what = e.get("what_i_did", "")
                    lines.append(f"        - [{phase}] {cmd}")
                    if stderr:
                        lines.append(f"          stderr: {stderr}")
                    if what:
                        lines.append(f"          what_i_did: {what}")
            else:
                lines.append("      Report recorded errors: (none)")

        lines.append("")
        lines.append("Full graph durable state vs lead report:")
        for r in results:
            slug = r["slug"]
            bead_id = beads.get(slug, "")
            dur = "CLOSED" if r["closed"] else f"OPEN ({r.get('outcome', 'open')})"
            lead = "claimed-closed" if slug in claimed else ("parked" if slug in parked else "omitted")
            lines.append(f"  [{slug}] (id={bead_id}): durable={dur} | lead_report={lead}")

        diag = "\n".join(lines)
        log(f"\n{diag}\n")
        raise AssertionError(
            f"the lead reported beads closed that Beads still shows open: {overclaimed}\n\n{diag}"
        )

    return results


# ---------------------------------------------------------------- phases

def define_capacities(env: dict[str, str], tasks: list[dict]) -> None:
    """Core loop step 1: only a non-default capacity is declared up front."""
    result = run([sys.executable, str(POOL), "define", "--group", "shared:staging",
                  "--resource", "api:staging", "--capacity", "2"], env)
    data = json.loads(result.stdout or "{}")
    expect(data.get("status") == "ok", f"capacity definition failed: {result.stderr or data}")
    declared = {t.get("group") for t in tasks if t.get("group")}
    expect(declared == {"shared:staging"},
           f"the graph declares a shared group with no capacity defined: {declared}")


def check_task_text(env, repo, names, beads) -> None:
    """No bead's own text may trip the deny rules `spawn` applies to it.

    `spawn` classifies the task it is given, so prose is scanned as if it were a command:
    a semicolon in a bead description reads as compound shell syntax and the spawn is
    refused mid-wave. Catching it here costs nothing; catching it there costs a pane.
    """
    runner = load_module(FLEET, "sim_fleet_policy")       # pure classification, no state
    scheduler = load_module(SCHEDULER, "sim_scheduler")   # the same text `ready` would emit
    policy = runner.load_policy()

    # The fixture's own check must be one the herder can clear unattended, and the policy
    # must still refuse what it does not recognise. Both halves matter: the first is why
    # AGY beads can finish at all, the second is why that is not a widened permission.
    expect(runner.classify_text(CHECK, policy)["action"] == "approve",
           f"the fixture check {CHECK!r} is not auto-approvable; AGY beads can only park")
    expect(runner.classify_text(UNKNOWN_CHECK, policy)["action"] == "escalate",
           f"default-deny stopped escalating {UNKNOWN_CHECK!r}")

    # Every bead, not just today's ready set: a later wave's prose is refused just as hard.
    for slug, bead in beads.items():
        issue = issue_status(repo, env, bead)
        verdict = runner.classify_text(scheduler.extract_bead_task_text(issue), policy)
        expect(verdict["risk"] != "high",
               f"{slug} task text trips {verdict.get('rule')}: {verdict.get('why')}")
    log(f"{len(beads)} bead task texts pass the spawn-time deny rules")


def check_translation(env, repo, names) -> None:
    """Everything the herder can get wrong before a claim becomes a pane."""
    wave = ready_slugs(env, repo, names)
    expect(set(wave) == {"contract-api", "docs-audit", "smoke-a", "smoke-b"},
           f"unexpected first wave: {sorted(wave)}")
    flags = {slug: spawn_flags(c, repo) for slug, c in wave.items()}
    if WORKER_CLI not in {"agy", "omp"} and not os.environ.get("AGENT_HERDER_SKIP_READONLY_CHECK"):
        expect(flags["docs-audit"]["read_only"] and not flags["docs-audit"]["resources"],
               "the read-only bead was given a writable mapping")
    expect(all(f["cwd"].startswith(str(repo)) for f in flags.values()),
           "a bead's cwd escaped its repository root")
    expect(all(f["verification"] == [CHECK] for f in flags.values()),
           "required verification was lost in translation")
    expect(flags["smoke-a"]["group"] == "shared:staging",
           "a shared resource was scoped to one repository")
    log(f"first wave ranked and translated: {', '.join(sorted(wave))}")


def check_bead_gate(env, repo, beads) -> None:
    """The bead gate, checked without spending an agent on evidence it must refuse.

    `spawn` has no `--verification` flag, so the pane gate can only check that every exit
    matched its expectation. Which commands were required is knowable only at `close`.
    """
    bead = beads["contract-api"]
    failing = json.dumps({
        "status": "completed", "decisions": [], "changed_files": [],
        "verification": [{"command": CHECK, "exit_code": 1}],
        "blockers": [], "next_inputs": [],
    })
    sched_expect_refusal(env, "exits do not match expectations",
                         "close", bead, "--actor", "gate-check",
                         "--project", str(repo), "--handoff", "FLEET_HANDOFF: " + failing)
    unrequired = json.dumps({
        "status": "completed", "decisions": [], "changed_files": [],
        "verification": [{"command": "echo something else", "exit_code": 0}],
        "blockers": [], "next_inputs": [],
    })
    sched_expect_refusal(env, "required verification commands missing",
                         "close", bead, "--actor", "gate-check",
                         "--project", str(repo), "--handoff", "FLEET_HANDOFF: " + unrequired)
    expect(issue_status(repo, env, bead)["status"] == "open",
           "a bead moved on evidence its own gate refused")
    log("bead gate refuses failing and unrequired evidence")


def check_rollback(env, repo, names, beads) -> None:
    """Rule 2, forced: a spawn that cannot get its whole scope gives back what it took."""
    slug, other = "smoke-a", "smoke-b"
    bead = beads[slug]
    agent = f"bead-{bead}"
    flags = spawn_flags(ready_slugs(env, repo, names)[slug], repo)
    # Fill both staging slots so the spawn is refused outright.
    for filler in ("Sim-filler-1", "Sim-filler-2"):
        run([sys.executable, str(POOL), "acquire", "--group", flags["group"],
             "--resource", "api:staging", "--agent", filler, "--timeout", "0"], env)

    sched(env, "claim", bead, "--actor", agent, "--project", str(repo))
    result = fleet(env, "spawn", "--name", agent, "--cli", flags["cli"],
                   "--cwd", flags["cwd"], "--task", "blocked on staging",
                   "--group", flags["group"], "--resource", "api:staging",
                   "--approval-mode", "auto")
    expect(result.returncode != 0, "a spawn with no free capacity was allowed")
    expect(not held_by(env, flags["group"], agent), "a refused spawn left a lease behind")
    sched(env, "rollback", bead, "--actor", agent, "--project", str(repo))
    issue = issue_status(repo, env, bead)
    expect(issue["status"] == "open" and not (issue.get("assignee") or ""),
           f"rollback did not return the bead to open: {issue['status']}")
    expect(slug in ready_slugs(env, repo, names),
           "a rolled-back bead did not return to the ready set")
    expect(other in ready_slugs(env, repo, names),
           "independent work stopped because a sibling could not spawn")

    for filler in ("Sim-filler-1", "Sim-filler-2"):
        run([sys.executable, str(POOL), "release", "--group", flags["group"],
             "--resource", "api:staging", "--agent", filler], env)
    log("failed spawn rolled back; independent work still ranked")


def check_rejection_and_interrupted_flow(env: dict[str, str], session: str) -> None:
    """Pre-pane check: verify permission rejection and interrupted worker recovery.

    1. fleet reject --agent <name> --prompt "<text>" submits the alternative prompt
       with escape sequence to unblock an interrupted worker.
    2. fleet reject --agent <name> --halt parks the agent and releases all its leases
       from resource_pool.py.
    3. auto_herd.sh halts with exit code 1 when an agent reports an interrupted status.
    """
    fleet_mod = load_module(FLEET, "sim_fleet_rejection")

    # 1. Assert fleet reject --prompt sends Esc and submits continuation prompt
    args_prompt = argparse.Namespace(
        agent="sim-worker-reject",
        session=session,
        reason="Permission denied by policy",
        prompt="Avoid destructive rm; archive files instead",
        halt=False,
        park=False,
    )
    with mock.patch.object(fleet_mod, "herdr") as mock_herdr, \
         mock.patch.object(fleet_mod, "_submit", return_value=True) as mock_submit, \
         mock.patch.object(fleet_mod.time, "sleep"):
        fleet_mod.cmd_reject(args_prompt)
        expect(mock_herdr.call_args_list and mock_herdr.call_args_list[0].args[0] == [
            "agent", "send-keys", "sim-worker-reject", "esc"
        ], "fleet reject did not send escape sequence to unblock worker")
        expect(mock_submit.called and mock_submit.call_args[0][:3] == (
            "sim-worker-reject", session, "Avoid destructive rm; archive files instead"
        ), "fleet reject did not submit alternative prompt")

    # 2. Assert fleet reject --halt parks the agent and releases its resource leases
    halt_group = "shared:staging"
    halt_agent = f"sim-reject-halt-{os.getpid()}"
    run([sys.executable, str(POOL), "acquire", "--group", halt_group,
         "--resource", "api:staging", "--agent", halt_agent, "--timeout", "0"], env)
    expect(bool(held_by(env, halt_group, halt_agent)),
           "pre-condition failed: lease was not acquired for halt test")

    res_halt = fleet(env, "reject", "--agent", halt_agent, "--group", halt_group,
                     "--halt", "--reason", "Supervisor halted rejected worker")
    expect(res_halt.returncode == 0, f"fleet reject --halt failed: {res_halt.stderr}")
    expect("parked" in res_halt.stdout, f"fleet reject --halt output missing 'parked': {res_halt.stdout}")
    expect(not held_by(env, halt_group, halt_agent),
           "fleet reject --halt did not release resource leases from resource_pool.py")
    fleet_mod.STATE_DIR = Path(env.get("AGENT_HERDER_STATE_DIR",
                                        str(Path.home() / ".config" / ".agents" / "herder")))
    parked_events = [e for e in fleet_mod.drain_events(session) if e.get("agent") == halt_agent]
    expect(len(parked_events) == 1 and parked_events[0].get("kind") == "parked",
           f"expected parked event for {halt_agent} in outbox: {parked_events}")

    # 3. Assert auto_herd.sh halts with exit code 1 when an agent reports an interrupted status
    with tempfile.TemporaryDirectory(prefix="sim-int-test-") as tdir:
        td = Path(tdir)
        stub_herd = td / "stub_herd.sh"
        stub_herd.write_text("""#!/bin/bash
SUBCMD=""
AGENT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    status|read|watch) SUBCMD="$1"; shift ;;
    --agent) AGENT="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [[ "$SUBCMD" == "status" ]]; then
  echo '{"session":"test-int","agents":[{"agent":"sim-worker-int","cli":"agy","state":"idle","health":"ok","action":null,"rule":null,"pending":null}]}'
elif [[ "$SUBCMD" == "read" ]]; then
  echo "Tool call was cancelled by user"
elif [[ "$SUBCMD" == "watch" ]]; then
  exit 0
fi
exit 0
""", encoding="utf-8")
        stub_herd.chmod(0o755)
        int_env = dict(os.environ)
        int_env.update(env)
        int_env["HERD_BIN"] = str(stub_herd)
        int_env["AGENT_HERDER_STATE_DIR"] = str(td / "fleet-state")
        proc = run(["bash", str(AUTO_HERD), "--session", "test-int"], int_env)
        expect(proc.returncode == 1,
               f"auto_herd.sh did not halt with exit code 1 on interrupted worker (rc={proc.returncode})")
        output = (proc.stdout or "") + " " + (proc.stderr or "")
        expect("operation was interrupted or cancelled (requires herding agent guidance)" in output,
               f"auto_herd.sh did not report interrupted status requiring guidance: {output}")

    log("permission rejection and interrupted worker flow pass pre-pane checks")


def check_outbox_events(env: dict[str, str], session: str) -> None:
    """Pre-pane check: verify the atomic file outbox emitter, notify CLI, and event drain."""
    fleet_mod = load_module(FLEET, "sim_fleet_outbox")
    state_dir = Path(env.get("AGENT_HERDER_STATE_DIR",
                             str(Path.home() / ".config" / ".agents" / "herder")))
    fleet_mod.STATE_DIR = state_dir

    res = fleet(env, "notify", "--agent", "sim-probe", "--kind", "probe",
                "--payload", json.dumps({"test": True}))
    expect(res.returncode == 0, f"fleet notify CLI failed: {res.stderr}")
    expect("notified" in res.stdout, f"notify output missing 'notified': {res.stdout}")

    events = fleet_mod.drain_events(session)
    expect(len(events) == 1, f"expected 1 drained event, got {len(events)}")
    expect(events[0].get("agent") == "sim-probe", f"unexpected event agent: {events[0]}")
    expect(events[0].get("kind") == "probe", f"unexpected event kind: {events[0]}")
    expect(events[0].get("payload") == {"test": True}, f"unexpected event payload: {events[0]}")

    remaining = fleet_mod.drain_events(session)
    expect(len(remaining) == 0, f"outbox not cleared after drain: {remaining}")
    log("atomic file outbox and notify CLI pass pre-pane checks")


def check_drained(env, repo, names, groups, results) -> None:
    remaining = ready_slugs(env, repo, names)
    closed = [r["slug"] for r in results if r["closed"]]
    parked = [r["slug"] for r in results if not r["closed"]]
    expect(not remaining or parked,
           f"the graph did not drain and nothing was parked: {sorted(remaining)}")
    # The default topology uses AGY for every worker. A parked bead means the wave loop or
    # its CLI integration gave up; none may be hidden as an acceptable partial result.
    unexpected = parked
    if unexpected:
        details = [f"{r['slug']}: outcome={r.get('outcome')}, detail={r.get('detail')}"
                   for r in results if r["slug"] in unexpected]
        log(f"unexpected parked spine beads: {'; '.join(details)}")
    expect(not unexpected, f"a bead on the dependency spine parked: {unexpected}")
    for slug in ("release-review", "followup-fix"):
        expect(slug in closed, f"the run never reached {slug}; waves 3 and 4 went untested")
    for group in groups:
        holders = {name: entry["holders"] for name, entry in pool_status(env, group).items()
                   if entry.get("holders")}
        expect(not holders, f"leases survived the wave loop in {group}: {holders}")
    log(f"closed {len(closed)}: {', '.join(closed) or 'none'}")
    if parked:
        log(f"parked {len(parked)}: {', '.join(parked)}")


# ---------------------------------------------------------------- entry point

def main() -> int:
    install_signal_handlers()
    for tool in ("br", "git", "herdr"):
        if not shutil.which(tool):
            print(f"SKIP: {tool} is not installed; this e2e test needs a real fleet",
                  file=sys.stderr)
            return 2
    for path in (FLEET, POOL, SCHEDULER, PREFLIGHT, AUTO_HERD):
        expect(path.is_file(), f"composed skill script is missing: {path}")

    expect(HERDER_CLI in {"codex", "omp", "agy", "claude"},
           f"the simulation supports codex, omp, agy, or claude as its lead; {HERDER_CLI!r} is refused")
    expect(WORKER_CLI != "claude" or os.environ.get("AGENT_HERDER_SIM_ALLOW_CLAUDE") == "1",
           "Claude workers require AGENT_HERDER_SIM_ALLOW_CLAUDE=1 while it is unavailable")
    tasks = configure_worker_cli(json.loads(GRAPH.read_text(encoding="utf-8")))
    workspace = None
    session = None
    daemon = None
    env = dict(os.environ)
    # The harness owns a detached named session. Never inherit an unrelated
    # attached pane/socket into wrapper bootstrap or preflight.
    env.pop("HERDR_ENV", None)
    env.pop("HERDR_SOCKET_PATH", None)

    short_dir = "/tmp" if os.path.isdir("/tmp") else None
    with tempfile.TemporaryDirectory(prefix="ah.", dir=short_dir) as raw_root:
        # Resolve before use: on macOS the temp root is /tmp but resolves to /private/tmp,
        # and the scheduler compares a resolved cwd against the repository root.
        temp_root = Path(raw_root).resolve()
        repo = temp_root / "project"
        repo.mkdir()

        env["AGENT_HERDER_POOL_STATE_FILE"] = str(temp_root / "pool.json")
        env["AGENT_HERDER_STATE_DIR"] = str(temp_root / "fleet-state")
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        clis = ",".join(sorted({t["cli"] for t in tasks} | {HERDER_CLI}))
        results: list[dict] = []
        primary_failure: BaseException | None = None
        evidence_captured = False
        try:
            daemon = open_session(env, repo)
            session = daemon.session

            update_lifecycle(env, phase="preflight", status="running")
            print("== preflight ==", flush=True)
            preflight(env, clis, session)

            print("== fixture ==", flush=True)
            beads = build_fixture(repo, env, tasks)
            names = {bead: slug for slug, bead in beads.items()}
            groups = {f"repo:{repo}"} | {t["group"] for t in tasks if t.get("group")}
            define_capacities(env, tasks)
            check_unattached(env, session)
            workspace = daemon.workspace_id
            update_lifecycle(env, phase="fixture-ready", total_tasks=len(tasks))

            print("== before any pane ==", flush=True)
            check_translation(env, repo, names)
            check_task_text(env, repo, names, beads)
            check_bead_gate(env, repo, beads)
            check_rollback(env, repo, names, beads)
            check_outbox_events(env, session)
            check_rejection_and_interrupted_flow(env, session)
            update_lifecycle(env, phase="static-checks-passed")

            print("== herding ==", flush=True)
            # The skill drives from here. The harness sets up the fixture and judges the
            # outcome; it no longer carries its own copy of the wave loop.
            report = run_herder(
                env, repo, daemon, temp_root, {f"bead-{bead}" for bead in beads.values()}, names=names)
            results = results_from_report(env, repo, names, beads, report)

            print("== teardown ==", flush=True)
            fleet(env, "reconcile")
            check_drained(env, repo, names, groups, results)
            update_lifecycle(env, phase="complete", status="passed",
                             closed=[r["slug"] for r in results if r["closed"]],
                             parked=[r["slug"] for r in results if not r["closed"]],
                             retrospective=report.get("retrospective", {}),
                             completed_at=time.time())
        except BaseException as exc:
            primary_failure = exc
            update_lifecycle(env, status="failed-before-session" if not session else "failed",
                             failure=str(exc), session=session)
            if session:
                try:
                    captured = capture_failure_evidence(env, session, daemon)
                    evidence_captured = True
                    update_lifecycle(env, status="failure-evidence-captured", session=session,
                                     evidence_dir=str(next(iter(captured.values())).parent),
                                     evidence={key: str(value) for key, value in captured.items()},
                                     failure=str(exc))
                    log(f"saved failure evidence: {', '.join(sorted(map(str, captured.values())))}")
                except Exception as evidence_exc:
                    log(f"FAIL: evidence capture failed; preserving session {session}: "
                        f"{evidence_exc}")
                    raise RuntimeError(
                        f"simulation failed ({exc}); failure evidence capture also failed "
                        f"({evidence_exc}); preserved session {session} for diagnosis"
                    ) from evidence_exc
            raise
        finally:
            # A successful run always cleans up. A failed run does so only after its
            # session, agent, and daemon records were durably captured.
            if daemon and (primary_failure is None or evidence_captured):
                cleanup_error = None
                try:
                    lead_group = f"lead:sim-herder-{os.getpid()}"
                    for group in {f"repo:{repo}", "shared:staging", lead_group}:
                        for name, entry in pool_status(env, group).items():
                            for holder in entry.get("holders", []):
                                run([sys.executable, str(POOL), "release",
                                     "--group", group, "--resource", name,
                                     "--agent", holder["agent"]], env)
                    if workspace:
                        close_workspace(env, workspace)
                except BaseException as exc:
                    cleanup_error = exc
                try:
                    close_session(env, daemon)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc

                if cleanup_error:
                    if primary_failure is not None:
                        raise RuntimeError(
                            f"simulation failed ({primary_failure}); evidence was captured, "
                            f"but cleanup for {daemon.session} also failed: {cleanup_error}"
                        ) from cleanup_error
                    raise cleanup_error
            elif daemon:
                log(f"preserved disposable session {daemon.session}: failure evidence "
                    f"capture did not complete")
                update_lifecycle(env, status="cleanup-preserved", cleanup="not-run",
                                 session=daemon.session)

    closed = sum(1 for r in results if r["closed"])
    parked = [r["slug"] for r in results if not r["closed"]]
    print(f"\nPASS: Agent Herder wave loop against a real fleet — "
          f"{closed}/{len(results)} beads closed on real agent evidence")
    print("PASS: ranking, metadata translation, claim/lease, capacity, gate refusals, "
          "rollback, real panes, real handoffs, park, dependency waves, lease drain")
    if parked:
        print(f"NOTE: parked rather than closed: {', '.join(parked)}")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except (AssertionError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(code)
