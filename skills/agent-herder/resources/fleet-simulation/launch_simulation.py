#!/usr/bin/env python3
"""Launch the real fleet simulation outside a short-lived command-host process.

The launcher intentionally runs the normal ``run_simulation.py`` entry point.  It only
owns process detachment and the durable launch record; the simulation still owns the
real Herdr, Beads, and lease lifecycle.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SIM_DIR = Path(__file__).resolve().parent
RUN_SIMULATION = SIM_DIR / "run_simulation.py"


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def launch() -> dict:
    run_dir = Path(tempfile.mkdtemp(prefix="agent-herder-sim-run-"))
    run_dir.mkdir(parents=True, exist_ok=True)
    record = run_dir / "lifecycle.json"
    log_path = run_dir / "simulation.log"
    env = dict(os.environ)
    for name in ("HERDR_ENV", "HERDR_SOCKET_PATH", "HERDR_PANE_ID"):
        env.pop(name, None)
    env["AGENT_HERDER_SIM_LIFECYCLE_FILE"] = str(record)

    metadata = {
        "launcher_pid": os.getpid(),
        "launched_at": time.time(),
        "status": "launching",
        "command": [sys.executable, str(RUN_SIMULATION)],
        "log_path": str(log_path),
        "herdr_environment_cleared": True,
    }
    write_json(record, metadata)
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen(
            metadata["command"], cwd=SIM_DIR.parents[3], env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    metadata.update({"status": "running", "simulation_pid": child.pid})
    write_json(record, metadata)
    return {"run_dir": str(run_dir), "lifecycle": str(record),
            "log": str(log_path), "pid": child.pid}


if __name__ == "__main__":
    print(json.dumps(launch(), sort_keys=True), flush=True)
