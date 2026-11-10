#!/usr/bin/env node

// Install a managed skill: copy it from this repository into the local install
// root, then symlink that copy into each selected agent's skills directory.
// The repository itself is never the symlink target, so editing a skill here
// does not mutate the skill an agent is currently running.

import { lstat, realpath } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";

import {
  agents,
  agentDirectory,
  availableSkills,
  copySkill,
  expandHome,
  installRoot,
  linkSkill,
  parseAgents,
  pathStat,
  readManifest,
  repositoryRoot,
  resolveClosure,
  setSkillWritable,
  skillDirtyPaths,
  skillVersion,
  writeManifest,
} from "./lib/skills.mjs";

function usage() {
  console.log(`Install a managed skill into the local install root and link it into agent skill directories.

Usage:
  just install
  just install --skill <name> --scope <global|project> --agents <names> [options]

Options:
  --skill <name>       Skill directory under ./skills
  --scope <scope>      global or project
  --agents <names>     Comma-separated: gemini (alias agy),claude,codex,omp (or all)
  --project <path>     Project root; required for non-interactive project scope
  --reinstall          Re-copy into the install root even if the version matches
  --force              Replace an existing real directory or file at a link destination
  --writable           Leave the installed copy writable instead of locking it read-only
  -h, --help           Show this help

Behaviour:
  Skills are copied to ${installRoot()} (override with AGENT_SKILLS_HOME),
  together with the transitive closure of skills/<name>/dependencies.json.
  A skill folder with uncommitted changes is refused; its version is the first
  12 characters of the last commit touching skills/<name>.
  The installed copy is locked read-only afterwards, so edits have to be made in
  this repository and reinstalled; --writable opts out for local experiments.
`);
}

function parseArguments(argv) {
  const options = { force: false, reinstall: false, writable: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--" || argument === "") {
      continue;
    } else if (argument === "--force") {
      options.force = true;
    } else if (argument === "--reinstall") {
      options.reinstall = true;
    } else if (argument === "--writable") {
      options.writable = true;
    } else if (argument === "--help" || argument === "-h") {
      options.help = true;
    } else if (["--skill", "--scope", "--agents", "--project"].includes(argument)) {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) {
        throw new Error(`${argument} requires a value.`);
      }
      options[argument.slice(2)] = value;
      index += 1;
    } else if (argument) {
      throw new Error(`Unknown argument: ${argument}`);
    }
  }
  return options;
}

async function chooseOne(prompt, choices, rl) {
  choices.forEach((choice, index) => console.log(`  ${index + 1}) ${choice}`));
  const answer = await rl.question(`${prompt}: `);
  const selected = Number.parseInt(answer, 10) - 1;
  if (!Number.isInteger(selected) || selected < 0 || selected >= choices.length) {
    throw new Error(`Invalid selection: ${answer}`);
  }
  return choices[selected];
}

async function chooseAgents(rl) {
  const keys = Object.keys(agents);
  keys.forEach((key, index) => console.log(`  ${index + 1}) ${agents[key].label}`));
  const answer = (await rl.question("Select agents (comma-separated numbers, or 'all'): ")).trim();
  if (answer.toLowerCase() === "all") return keys;
  const selected = answer.split(",").map((value) => Number.parseInt(value.trim(), 10) - 1);
  if (!selected.length || selected.some((index) => !Number.isInteger(index) || !keys[index])) {
    throw new Error(`Invalid agent selection: ${answer}`);
  }
  return [...new Set(selected.map((index) => keys[index]))];
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    usage();
    return;
  }

  const skills = await availableSkills(repositoryRoot);
  if (!skills.length) throw new Error("No skills containing SKILL.md were found under ./skills.");

  const needsInput = !options.skill || !options.scope || !options.agents ||
    (options.scope === "project" && !options.project);
  if (needsInput && !process.stdin.isTTY) {
    throw new Error("Missing required options in a non-interactive shell. Run with --help for usage.");
  }

  const rl = needsInput ? createInterface({ input: process.stdin, output: process.stdout }) : undefined;
  let selectedAgents;
  let projectRoot;
  try {
    options.skill ??= await chooseOne("Select a skill", skills, rl);
    if (!skills.includes(options.skill)) throw new Error(`Unknown skill: ${options.skill}`);

    options.scope ??= (await chooseOne("Select scope", ["Global", "Project"], rl)).toLowerCase();
    if (!["global", "project"].includes(options.scope)) {
      throw new Error(`Invalid scope: ${options.scope}`);
    }

    selectedAgents = options.agents ? parseAgents(options.agents) : await chooseAgents(rl);
    if (options.scope === "project" && !options.project) {
      options.project = await rl.question("Project folder: ");
    }
  } finally {
    rl?.close();
  }

  if (options.scope === "project") {
    projectRoot = await realpath(path.resolve(expandHome(options.project)));
    const projectStat = await lstat(projectRoot);
    if (!projectStat.isDirectory()) throw new Error(`Not a project directory: ${projectRoot}`);
  }

  const closure = await resolveClosure([options.skill], repositoryRoot);
  const dependencies = closure.filter((name) => name !== options.skill);
  if (dependencies.length) {
    console.log(`Dependencies of ${options.skill}: ${dependencies.join(", ")}`);
  }

  // Resolve every version first: a dirty skill anywhere in the closure aborts
  // the whole install rather than leaving a partial closure behind.
  const versions = new Map();
  const dirty = [];
  for (const name of closure) {
    const dirtyPaths = await skillDirtyPaths(name, repositoryRoot);
    if (dirtyPaths.length) {
      dirty.push({ name, dirtyPaths });
      continue;
    }
    versions.set(name, await skillVersion(name, repositoryRoot));
  }
  if (dirty.length) {
    const detail = dirty
      .map(({ name, dirtyPaths }) => `  ${name}:\n${dirtyPaths.map((line) => `    ${line}`).join("\n")}`)
      .join("\n");
    throw new Error(`Uncommitted changes block the install; commit or stash them first:\n${detail}`);
  }

  const root = installRoot();
  const manifest = await readManifest(root);

  for (const name of closure) {
    const version = versions.get(name);
    const recorded = manifest.skills[name];
    const installedPath = path.join(root, name);
    const present = Boolean(await pathStat(installedPath));
    const upToDate = recorded?.version === version && present;

    if (upToDate && !options.reinstall) {
      console.log(`${name}: already installed at ${version} (skipping copy)`);
    } else {
      await copySkill(name, root, path.join(repositoryRoot, "skills", name));
      manifest.skills[name] = {
        version,
        installedAt: new Date().toISOString(),
        source: repositoryRoot,
      };
      await writeManifest(manifest, root);
      console.log(`${name}: installed ${version} -> ${installedPath}`);
    }

    const source = await realpath(installedPath);
    for (const agentName of selectedAgents) {
      const destination = path.join(agentDirectory(agentName, options.scope, projectRoot), name);
      const result = await linkSkill(source, destination, options.force);
      console.log(`  ${agents[agentName].label}: ${result} ${destination} -> ${source}`);
    }

    // Lock last, and on every run: a copy skipped as up to date may have been
    // left writable by an older install or unlocked by hand since.
    await setSkillWritable(installedPath, options.writable);
    console.log(`  ${options.writable ? "left writable" : "locked read-only"}: ${installedPath}`);
  }

  await writeManifest(manifest, root);
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
