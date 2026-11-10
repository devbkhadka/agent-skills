#!/usr/bin/env node

// Report installed vs repository state for every managed skill.
// Non-interactive: it never prompts and never changes anything.

import path from "node:path";
import process from "node:process";

import {
  availableSkills,
  installRoot,
  pathStat,
  readManifest,
  repositoryRoot,
  skillDirtyPaths,
  skillVersion,
} from "./lib/skills.mjs";

function usage() {
  console.log(`Show the installed state of every managed skill.

Usage:
  just status

Reports, per skill, the installed version, the current repository version, and one of:
  up to date                  installed version matches the repository
  update available            the repository has a newer commit
  not installed               present in ./skills but not in the install root
  installed but missing from repo   in the manifest with no matching ./skills entry
  dirty in repo               skills/<name> has uncommitted changes, so it cannot be installed
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
  const repoSkills = await availableSkills(repositoryRoot);
  const names = [...new Set([...repoSkills, ...Object.keys(manifest.skills)])].sort();

  console.log(`Repository:  ${repositoryRoot}`);
  console.log(`Install root: ${root}`);
  console.log("");

  if (!names.length) {
    console.log("No skills found in the repository or the manifest.");
    return;
  }

  const rows = [];
  for (const name of names) {
    const recorded = manifest.skills[name];
    const present = Boolean(await pathStat(path.join(root, name)));
    const installedVersion = recorded && present ? recorded.version : recorded ? `${recorded.version} (missing copy)` : "-";
    const inRepo = repoSkills.includes(name);

    let repoVersion = "-";
    let state;
    if (!inRepo) {
      state = "installed but missing from repo";
    } else {
      const dirtyPaths = await skillDirtyPaths(name, repositoryRoot);
      if (dirtyPaths.length) {
        state = "dirty in repo";
      }
      try {
        repoVersion = await skillVersion(name, repositoryRoot);
      } catch {
        repoVersion = "uncommitted";
      }
      if (!state) {
        if (!recorded || !present) state = "not installed";
        else state = recorded.version === repoVersion ? "up to date" : "update available";
      }
    }
    rows.push({ name, installedVersion, repoVersion, state });
  }

  const nameWidth = Math.max(6, ...rows.map((row) => row.name.length));
  const installedWidth = Math.max(9, ...rows.map((row) => row.installedVersion.length));
  const repoWidth = Math.max(10, ...rows.map((row) => row.repoVersion.length));

  console.log(`${pad("SKILL", nameWidth)}  ${pad("INSTALLED", installedWidth)}  ${pad("REPO", repoWidth)}  STATE`);
  for (const row of rows) {
    console.log(
      `${pad(row.name, nameWidth)}  ${pad(row.installedVersion, installedWidth)}  ${pad(row.repoVersion, repoWidth)}  ${row.state}`,
    );
  }
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
