"""Live browser UI observation runner and Stage 5 delivery observation verification.

Owner: stage-5.

Closed vocabulary for observation target declarations and named refusals:
- OBSERVATION_TARGET_UNDECLARED: Milestone specifies rendered_ui but envelope omits observation_target.
- OBSERVATION_BROWSER_UNAVAILABLE: Headless browser driver (Playwright/Node) cannot be found or launched.
- OBSERVATION_TARGET_UNREACHABLE: Target file, server, or URL cannot be reached within timeout.
- OBSERVATION_SELECTOR_TIMEOUT: Declared wait_for_selector did not appear in the page within timeout.
- OBSERVATION_RENDER_EMPTY: Observed page rendered no observable content or empty DOM.
- OBSERVATION_DISAGREEMENT: Observed rendered DOM normalisation differs from envelope claimed capture.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .normalisation import capture_projection, compute_fingerprint, normalise_rendered_ui
from .primitives import check_closed_key_set, is_vacuous_container

OBSERVATION_TARGET_KEYS = (
    "entry_point",
    "url",
    "server_command",
    "port",
    "wait_for_selector",
    "selector_timeout_ms",
    "timeout_ms",
    "settle_ms",
)

NODE_OBSERVATION_SCRIPT = r"""
import http from 'node:http';
import { spawn } from 'node:child_process';
import { chromium } from '__PLAYWRIGHT_PATH__';

function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(url, res => {
        res.resume();
        resolve();
      });
      req.on('error', () => {
        if (Date.now() > deadline) {
          reject(new Error(`server at ${url} did not become reachable within ${timeoutMs}ms`));
        } else {
          setTimeout(attempt, 200);
        }
      });
    };
    attempt();
  });
}

async function main() {
  const config = JSON.parse(process.env.OBS_CONFIG || '{}');
  let serverProcess = null;
  let browser = null;

  try {
    let targetUrl = config.url || '';
    if (config.server_command) {
      const parts = config.server_command.split(' ');
      const cmd = parts[0];
      const args = parts.slice(1);
      const port = config.port || 4173;
      const env = { ...process.env, PORT: String(port) };
      serverProcess = spawn(cmd, args, {
        cwd: config.cwd || process.cwd(),
        env,
        stdio: ['ignore', 'pipe', 'pipe']
      });
      targetUrl = targetUrl || `http://localhost:${port}`;
      await waitForServer(targetUrl, config.timeout_ms || 10000);
    } else if (config.entry_point) {
      const absPath = config.entry_point.startsWith('/') ? config.entry_point : `${config.cwd || process.cwd()}/${config.entry_point}`;
      targetUrl = `file://${absPath}`;
    }

    if (!targetUrl) {
      console.error('TARGET_ERROR: No URL, entry_point, or server_command specified');
      process.exit(2);
    }

    try {
      browser = await chromium.launch({ headless: true });
    } catch (err) {
      console.error('BROWSER_ERROR: ' + err.message);
      process.exit(3);
    }

    const page = await browser.newPage();
    try {
      await page.goto(targetUrl, { timeout: config.timeout_ms || 10000, waitUntil: 'load' });
    } catch (err) {
      console.error('TARGET_ERROR: Failed to reach target ' + targetUrl + ': ' + err.message);
      process.exit(2);
    }

    if (config.wait_for_selector) {
      const selTimeout = config.selector_timeout_ms || config.timeout_ms || 5000;
      try {
        await page.waitForSelector(config.wait_for_selector, { timeout: selTimeout });
      } catch (err) {
        console.error('SELECTOR_TIMEOUT: Selector ' + JSON.stringify(config.wait_for_selector) + ' did not appear within ' + selTimeout + 'ms');
        process.exit(4);
      }
    } else {
      await page.waitForTimeout(config.settle_ms || 300);
    }

    const bodyContent = await page.$eval('body', el => el.innerHTML).catch(() => '');
    const fullContent = await page.content();
    
    const result = {
      body_html: (bodyContent || '').trim(),
      full_html: (fullContent || '').trim(),
    };
    console.log('OBS_RESULT:' + JSON.stringify(result));
  } catch (err) {
    console.error('OBS_ERROR: ' + err.message);
    process.exit(1);
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
    if (serverProcess) {
      serverProcess.kill();
    }
  }
}

main().catch(err => {
  console.error('FATAL: ' + err.message);
  process.exit(1);
});
"""


def find_playwright_path(base_dir: Optional[Path] = None) -> Optional[str]:
    """Resolve Playwright package path for headless Chromium.

    Resolution strategy:
    1. Direct override via PLAYWRIGHT_PATH environment variable.
    2. Directories listed in NODE_PATH environment variable.
    3. Traversal from base_dir through all ancestor directories.
    4. Traversal from current working directory through all ancestor directories.
    """
    env_pw = os.environ.get("PLAYWRIGHT_PATH")
    if env_pw:
        cand = Path(env_pw)
        if cand.is_file():
            return str(cand.resolve())
        if cand.is_dir():
            idx = cand / "index.mjs"
            return str(idx.resolve()) if idx.is_file() else str(cand.resolve())

    env_node_path = os.environ.get("NODE_PATH")
    if env_node_path:
        for p in env_node_path.split(os.pathsep):
            cand = Path(p) / "playwright"
            if cand.is_dir():
                idx = cand / "index.mjs"
                return str(idx.resolve()) if idx.is_file() else str(cand.resolve())

    roots = []
    if base_dir:
        b_resolved = Path(base_dir).resolve()
        roots.append(b_resolved)
        roots.extend(b_resolved.parents)

    cwd_resolved = Path.cwd().resolve()
    roots.append(cwd_resolved)
    roots.extend(cwd_resolved.parents)

    for r in roots:
        cand = r / "node_modules" / "playwright"
        if cand.is_dir():
            idx = cand / "index.mjs"
            return str(idx.resolve()) if idx.is_file() else str(cand.resolve())

    return None


def is_browser_available(base_dir: Optional[Path] = None) -> bool:
    """Check if Node and Playwright are available for live browser execution."""
    if not shutil.which("node"):
        return False
    return find_playwright_path(base_dir) is not None


def observe_rendered_ui(
    target_config: Dict[str, Any],
    base_dir: Optional[Path] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Execute live browser observation against the declared target.

    Returns: (observed_markup, error_code, error_detail)
    """
    pw_path = find_playwright_path(base_dir)
    if not pw_path or not shutil.which("node"):
        return (
            None,
            "OBSERVATION_BROWSER_UNAVAILABLE",
            "Headless browser runner unavailable (Node.js or Playwright not found)",
        )

    config = dict(target_config)
    effective_base = base_dir or Path.cwd()
    config["cwd"] = str(effective_base.resolve())

    if config.get("entry_point"):
        ep = Path(config["entry_point"])
        if not ep.is_absolute():
            ep = effective_base / ep
        if not ep.is_file():
            return (
                None,
                "OBSERVATION_TARGET_UNREACHABLE",
                "Declared entry_point file does not exist: %s" % ep,
            )
        config["entry_point"] = str(ep.resolve())

    declared_timeout_ms = config.get("timeout_ms") or 10000
    if not isinstance(declared_timeout_ms, (int, float)) or declared_timeout_ms <= 0:
        declared_timeout_ms = 10000
    selector_timeout_ms = config.get("selector_timeout_ms") or 0
    if not isinstance(selector_timeout_ms, (int, float)) or selector_timeout_ms <= 0:
        selector_timeout_ms = 0

    total_wait_sec = (declared_timeout_ms + selector_timeout_ms) / 1000.0
    subprocess_timeout_sec = total_wait_sec + 20.0

    script_code = NODE_OBSERVATION_SCRIPT.replace("__PLAYWRIGHT_PATH__", pw_path)
    env = os.environ.copy()
    env["OBS_CONFIG"] = json.dumps(config)

    try:
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script_code],
            env=env,
            capture_output=True,
            text=True,
            timeout=subprocess_timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return (
            None,
            "OBSERVATION_TARGET_UNREACHABLE",
            "Observation runner timed out waiting for target to render",
        )
    except Exception as e:
        return (
            None,
            "OBSERVATION_BROWSER_UNAVAILABLE",
            "Failed to spawn headless browser runner: %s" % e,
        )

    if proc.returncode == 4:
        return None, "OBSERVATION_SELECTOR_TIMEOUT", proc.stderr.strip()
    if proc.returncode == 3:
        return None, "OBSERVATION_BROWSER_UNAVAILABLE", proc.stderr.strip()
    if proc.returncode == 2:
        return None, "OBSERVATION_TARGET_UNREACHABLE", proc.stderr.strip()
    if proc.returncode != 0:
        return (
            None,
            "OBSERVATION_TARGET_UNREACHABLE",
            "Observation runner failed (exit %d): %s" % (proc.returncode, proc.stderr.strip()),
        )

    for line in proc.stdout.splitlines():
        if line.startswith("OBS_RESULT:"):
            try:
                data = json.loads(line[len("OBS_RESULT:"):])
            except Exception as e:
                return None, "OBSERVATION_RENDER_EMPTY", "Invalid observation output: %s" % e
            body = data.get("body_html", "").strip()
            full = data.get("full_html", "").strip()
            markup = body if body else full
            if not markup:
                return None, "OBSERVATION_RENDER_EMPTY", "Browser observed empty DOM"
            return markup, None, None

    return None, "OBSERVATION_RENDER_EMPTY", "Observation produced no DOM content"


def verify_rendered_ui_observation(
    report: Dict[str, Any],
    planned: Dict[str, Any],
    verified: Sequence[Any],
    base_dir: Optional[Path] = None,
) -> List[str]:
    """Verify that every rendered_ui milestone capture matches live browser observation.

    Refuses unobserved claims, unreachable targets, empty renderings, and disagreements.
    """
    problems: List[str] = []

    for evidence in verified:
        if evidence.planned.verification_medium != "rendered_ui":
            continue

        milestone_id = evidence.planned.milestone_id
        envelope = evidence.envelope
        target = envelope.get("observation_target")

        if target is None or is_vacuous_container(target):
            problems.append(
                "OBSERVATION_TARGET_UNDECLARED: milestone %s verifies rendered_ui but declares no "
                "observation_target in its capture envelope" % milestone_id
            )
            continue

        if not isinstance(target, dict):
            problems.append(
                "OBSERVATION_TARGET_UNDECLARED: milestone %s observation_target must be an object"
                % milestone_id
            )
            continue

        closed = check_closed_key_set(
            target, OBSERVATION_TARGET_KEYS, label="milestone %s observation_target" % milestone_id
        )
        if not closed.is_valid:
            problems.append("UNKNOWN_DELIVERY_ATTRIBUTE: %s" % closed.message)
            continue

        observed_markup, err_code, err_detail = observe_rendered_ui(target, base_dir=base_dir)
        if err_code:
            problems.append("%s: milestone %s — %s" % (err_code, milestone_id, err_detail))
            continue

        recorded_values = envelope.get("recorded_fixture_values") or ()
        try:
            normalised_observed, fingerprint_observed = capture_projection(
                "rendered_ui", observed_markup, recorded_values=recorded_values
            )
        except Exception as e:
            problems.append("OBSERVATION_RENDER_EMPTY: milestone %s — %s" % (milestone_id, e))
            continue

        if normalised_observed == "[empty_dom]":
            problems.append(
                "OBSERVATION_RENDER_EMPTY: milestone %s live browser observation rendered an empty DOM"
                % milestone_id
            )
            continue

        if normalised_observed != evidence.normalised:
            claimed_fp = compute_fingerprint(evidence.normalised)
            problems.append(
                "OBSERVATION_DISAGREEMENT: milestone %s claimed rendered_ui projection %s but live "
                "browser observation produced %s"
                % (milestone_id, claimed_fp, fingerprint_observed)
            )

    return problems
