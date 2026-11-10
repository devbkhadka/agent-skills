#!/usr/bin/env python3
"""Wrapper to run the fleet simulation with an AGY lead and OMP workers (no Claude).

Usage: python3 run_sim_agy_omp.py

OMP has no verified --read-only mode, which the main runner handles from the
selected worker CLI. Everything else runs identically.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
os.environ.setdefault("AGENT_HERDER_GRAPH", str(SIM_DIR / "task_graph_agy_omp.json"))
os.environ.setdefault("AGENT_HERDER_SIM_LEAD_CLI", "agy")
os.environ.setdefault("AGENT_HERDER_SIM_WORKER_CLI", "omp")
runpy.run_path(str(SIM_DIR / "run_simulation.py"), run_name="__main__")
