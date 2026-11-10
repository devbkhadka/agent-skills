#!/usr/bin/env python3
"""Wrapper to run the fleet simulation with a Claude lead and OMP workers.

Usage: python3 run_sim_claude_omp.py

Claude operates with --permission-mode auto, enabling the lead herder
to execute supervision commands without getting blocked on interactive prompts.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
os.environ.setdefault("AGENT_HERDER_GRAPH", str(SIM_DIR / "task_graph_agy_omp.json"))
os.environ.setdefault("AGENT_HERDER_SIM_LEAD_CLI", "claude")
os.environ.setdefault("AGENT_HERDER_SIM_WORKER_CLI", "omp")
os.environ.setdefault("AGENT_HERDER_SIM_WORKER_MODEL", "litellm/gpt-4.1-nano")
runpy.run_path(str(SIM_DIR / "run_simulation.py"), run_name="__main__")
