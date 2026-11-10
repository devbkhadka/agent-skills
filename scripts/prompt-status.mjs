#!/usr/bin/env node

// Report installed vs repository state for every managed system prompt.
// Non-interactive: it never prompts and never changes anything.

import path from "node:path";
import process from "node:process";

import {
  availablePrompts,
  installRoot,
  pathStat,
  promptDirtyPaths,
  promptVersion,
  readManifest,
  repositoryRoot,
} from "./lib/prompts.mjs";

function usage() {
  console.log(`Show the installed state of every managed system prompt.

Usage:
  just prompt-status

Reports, per prompt, the installed version, current repository version, linked agents, and status.
`);
}

function pad(value, width) {
  return String(value).padEnd(width, " ");
}

async function main() {
  if (process.argv.slice(2).some((argument) => argument === "--help" || argument === "-h")) {
    usage();
    return;
  }

  const root = installRoot();
  const manifest = await readManifest(root);
  const repoPrompts = await availablePrompts(repositoryRoot);
  const names = [...new Set([...repoPrompts, ...Object.keys(manifest.prompts)])].sort();

  console.log(`Repository:   ${repositoryRoot}`);
  console.log(`Install root: ${root}`);
  console.log("");

  if (!names.length) {
    console.log("No system prompts found in the repository or the manifest.");
    return;
  }

  const rows = [];
  for (const name of names) {
    const recorded = manifest.prompts[name];
    const present = Boolean(await pathStat(path.join(root, name)));
    const installedVersion = recorded && present ? recorded.version : recorded ? `${recorded.version} (missing copy)` : "-";
    const inRepo = repoPrompts.includes(name);

    let repoVersion = "-";
    let state;
    if (!inRepo) {
      state = "installed but missing from repo";
    } else {
      const dirtyPaths = await promptDirtyPaths(name, repositoryRoot);
      if (dirtyPaths.length) {
        state = "dirty in repo";
      }
      try {
        repoVersion = await promptVersion(name, repositoryRoot);
      } catch {
        repoVersion = "uncommitted";
      }
      if (!state) {
        if (!recorded || !present) state = "not installed";
        else state = recorded.version === repoVersion ? "up to date" : "update available";
      }
    }

    const linkedAgents = recorded?.agents
      ? Object.keys(recorded.agents).map((a) => `${a} (${recorded.agents[a].mode || "compose"})`).join(", ")
      : "-";

    rows.push({ name, installedVersion, repoVersion, state, linkedAgents });
  }

  const nameWidth = Math.max(6, ...rows.map((row) => row.name.length));
  const installedWidth = Math.max(9, ...rows.map((row) => row.installedVersion.length));
  const repoWidth = Math.max(10, ...rows.map((row) => row.repoVersion.length));
  const stateWidth = Math.max(12, ...rows.map((row) => row.state.length));

  console.log(`${pad("PROMPT", nameWidth)}  ${pad("INSTALLED", installedWidth)}  ${pad("REPO", repoWidth)}  ${pad("STATE", stateWidth)}  AGENTS`);
  for (const row of rows) {
    console.log(
      `${pad(row.name, nameWidth)}  ${pad(row.installedVersion, installedWidth)}  ${pad(row.repoVersion, repoWidth)}  ${pad(row.state, stateWidth)}  ${row.linkedAgents}`,
    );
  }
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
