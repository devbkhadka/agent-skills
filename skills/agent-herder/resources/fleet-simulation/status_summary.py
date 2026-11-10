#!/usr/bin/env python3
"""status_summary.py — Observability & Progress Inspector for Fleet Simulation.

Provides high-signal, token-efficient visibility into a running or completed
fleet simulation without polluting an LLM agent's context with raw logs.

Usage:
  python3 status_summary.py --run-dir /tmp/agent-herder-sim-run-XXXXX
  python3 status_summary.py --lifecycle /path/to/lifecycle.json
  python3 status_summary.py --latest
  python3 status_summary.py --latest --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def find_latest_run_dir() -> Path | None:
    """Find the most recently modified agent-herder-sim-run-* directory in /tmp."""
    tmp = Path("/tmp")
    candidates = list(tmp.glob("agent-herder-sim-run-*"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


def render_summary(data: dict, log_path: Path | None = None) -> str:
    now = time.time()
    status = data.get("status", "unknown").upper()
    phase = data.get("phase", data.get("status", "unknown"))
    pid = data.get("simulation_pid", data.get("launcher_pid", "N/A"))
    session = data.get("session", "N/A")
    
    launched_at = data.get("launched_at")
    last_heartbeat = data.get("last_heartbeat") or data.get("last_progress_update") or data.get("updated_at")
    
    elapsed_str = format_duration(now - launched_at) if launched_at else "unknown"
    heartbeat_str = f"{int(now - last_heartbeat)}s ago" if last_heartbeat else "N/A"
    
    lines = [
        "=" * 68,
        f"FLEET SIMULATION STATUS: {status} (Phase: {phase})",
        "=" * 68,
        f"PID:        {pid} | Session: {session} | Elapsed: {elapsed_str}",
        f"Heartbeat:  {heartbeat_str}",
    ]
    
    # Progress info
    current_wave = data.get("current_wave")
    if current_wave is not None:
        lines.append(f"Wave:       {current_wave}")
        
    closed = data.get("closed_beads", data.get("closed", []))
    parked = data.get("parked_beads", data.get("parked", []))
    lines.append(f"Closed:     [{', '.join(closed) if closed else 'None'}] ({len(closed)} total)")
    if parked:
        lines.append(f"Parked:     [{', '.join(parked)}] ({len(parked)} total)")
        
    # Wave feedback / suggestions
    feedback = data.get("latest_feedback") or data.get("wave_feedback")
    if isinstance(feedback, dict) and any(feedback.values()):
        lines.append("-" * 68)
        lines.append("Latest Wave Feedback & Reflections:")
        for cat in ("gotchas", "inefficiencies", "suggestions"):
            items = feedback.get(cat, [])
            if items and isinstance(items, list):
                lines.append(f"  [{cat.upper()}]:")
                for item in items:
                    lines.append(f"    • {item}")
                    
    # Final retrospective
    retro = data.get("retrospective")
    if isinstance(retro, dict) and any(retro.values()):
        lines.append("-" * 68)
        lines.append("Simulation Retrospective:")
        for cat in ("improvements", "inefficiencies", "difficulties"):
            items = retro.get(cat, [])
            if items and isinstance(items, list):
                lines.append(f"  [{cat.upper()}]:")
                for item in items:
                    lines.append(f"    • {item}")

    # Failure details
    if "failure" in data or status in ("FAILED", "FAILED-BEFORE-SESSION"):
        lines.append("-" * 68)
        lines.append(f"FAILURE DETAILS:")
        lines.append(f"  Error: {data.get('failure', 'Unknown error')}")
        if "evidence_dir" in data:
            lines.append(f"  Evidence: {data.get('evidence_dir')}")

    lines.append("-" * 68)
    lines.append("TRIGGERING AGENT INSTRUCTIONS:")
    if status in ("RUNNING", "SESSION-READY", "FIXTURE-READY"):
        lines.append("  * DO NOT read raw simulation.log. Wait 30-45s and check status again.")
        lines.append("  * If blocked or stalled, dispatch a subagent to diagnose in isolation.")
    elif status in ("COMPLETED", "PASSED", "CLEANED"):
        lines.append("  * Simulation succeeded. Triage retrospective suggestions above:")
        lines.append("    - REJECT minor/one-off quirks or cosmetic preferences.")
        lines.append("    - FILE high-value systemic issues to skill-improver via suggest.py.")
    elif status in ("FAILED", "FAILED-BEFORE-SESSION"):
        lines.append("  * Simulation failed. MUST invoke a subagent to diagnose and apply quick fix.")
        lines.append("  * Subagent reports back: (1) Root Cause, (2) Fix applied.")
    lines.append("=" * 68)

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check fleet simulation status efficiently.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-dir", type=Path, help="Simulation run directory")
    group.add_argument("--lifecycle", type=Path, help="Simulation lifecycle.json path")
    group.add_argument("--latest", action="store_true", help="Inspect latest run in /tmp")
    parser.add_argument("--json", action="store_true", help="Output compact JSON format")
    
    args = parser.parse_args()
    
    lifecycle_path: Path | None = None
    log_path: Path | None = None
    
    if args.lifecycle:
        lifecycle_path = args.lifecycle
    elif args.run_dir:
        lifecycle_path = args.run_dir / "lifecycle.json"
        log_path = args.run_dir / "simulation.log"
    else:
        # Default or --latest
        latest_dir = find_latest_run_dir()
        if not latest_dir:
            print("No fleet simulation run directories found in /tmp.", file=sys.stderr)
            return 1
        lifecycle_path = latest_dir / "lifecycle.json"
        log_path = latest_dir / "simulation.log"
        
    if not lifecycle_path or not lifecycle_path.is_file():
        print(f"Lifecycle file not found: {lifecycle_path}", file=sys.stderr)
        return 1
        
    try:
        data = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Failed to read lifecycle file: {e}", file=sys.stderr)
        return 1
        
    if args.json:
        # Add quick computed fields
        now = time.time()
        out = dict(data)
        if data.get("launched_at"):
            out["elapsed_seconds"] = round(now - data["launched_at"], 1)
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(render_summary(data, log_path=log_path))
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
