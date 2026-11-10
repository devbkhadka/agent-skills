#!/usr/bin/env node

// Install a managed system prompt: copy it from this repository into the local
// install root, back up pre-existing agent configurations on first install,
// and symlink into each selected agent's target (supporting compose or replace).

import { lstat, realpath } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";

import {
  availablePrompts,
  copyPrompt,
  expandHome,
  installPromptForAgent,
  installRoot,
  parseAgents,
  promptAgents,
  promptDirtyPaths,
  promptExists,
  promptVersion,
  readManifest,
  repositoryRoot,
  setPromptWritable,
  writeManifest,
} from "./lib/prompts.mjs";

function usage() {
  console.log(`Install a managed system prompt into the local install root and link it for agents.

Usage:
  just prompt-install
  just prompt-install --prompt <name> --scope <global|project> --agents <names> [options]

Options:
  --prompt <name>      Prompt directory under ./system-prompts
  --scope <scope>      global or project
  --agents <names>     Comma-separated: gemini (alias agy),claude,codex,omp (or all)
  --mode <mode>        compose (keep existing style/persona, default) or replace
  --project <path>     Project root; required for non-interactive project scope
  --reinstall          Re-copy into the install root even if the version matches
  --force              Replace an existing real file at link destination without confirmation
  --writable           Leave the installed copy writable instead of locking it read-only
  -h, --help           Show this help

Behaviour:
  Prompts are copied to ${installRoot()} (override with AGENT_PROMPTS_HOME).
  Pre-existing target files are safely backed up on first install before linking.
  In compose mode (default), personal persona/style directives are preserved and layered.
  The installed copy is locked read-only afterwards.
`);
}

function parseArguments(argv) {
  const options = { force: false, reinstall: false, writable: false, mode: "compose" };
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
    } else if (["--prompt", "--scope", "--agents", "--project", "--mode"].includes(argument)) {
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
  const keys = Object.keys(promptAgents);
  keys.forEach((key, index) => console.log(`  ${index + 1}) ${promptAgents[key].label}`));
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

  const prompts = await availablePrompts(repositoryRoot);
  if (!prompts.length) throw new Error("No system prompts containing system-prompt.md were found under ./system-prompts.");

  const needsInput = !options.prompt || !options.scope || !options.agents ||
    (options.scope === "project" && !options.project);
  if (needsInput && !process.stdin.isTTY) {
    throw new Error("Missing required options in a non-interactive shell. Run with --help for usage.");
  }

  const rl = needsInput ? createInterface({ input: process.stdin, output: process.stdout }) : undefined;
  let selectedAgents;
  let projectRoot;
  try {
    options.prompt ??= await chooseOne("Select a system prompt", prompts, rl);
    if (!prompts.includes(options.prompt)) throw new Error(`Unknown system prompt: ${options.prompt}`);

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

  const name = options.prompt;
  const dirtyPaths = await promptDirtyPaths(name, repositoryRoot);
  if (dirtyPaths.length) {
    throw new Error(`Uncommitted changes block the install; commit or stash them first:\n  ${dirtyPaths.join("\n  ")}`);
  }

  const version = await promptVersion(name, repositoryRoot);
  const root = installRoot();
  const manifest = await readManifest(root);

  const recorded = manifest.prompts[name];
  const installedPath = path.join(root, name);
  const exists = await promptExists(name, repositoryRoot);
  if (!exists) throw new Error(`Prompt does not exist: ${name}`);

  const upToDate = recorded?.version === version && Boolean(await lstat(installedPath).catch(() => false));

  if (upToDate && !options.reinstall) {
    console.log(`${name}: already installed at ${version} (skipping copy)`);
  } else {
    await copyPrompt(name, root, path.join(repositoryRoot, "system-prompts", name));
    manifest.prompts[name] = {
      version,
      installedAt: new Date().toISOString(),
      source: repositoryRoot,
      agents: manifest.prompts[name]?.agents ?? {},
    };
    await writeManifest(manifest, root);
    console.log(`${name}: installed ${version} -> ${installedPath}`);
  }

  for (const agentName of selectedAgents) {
    const res = await installPromptForAgent({
      agentName,
      promptName: name,
      scope: options.scope,
      projectRoot,
      mode: options.mode,
      force: options.force,
      root,
    });

    if (res.backup) {
      console.log(`  ${res.agent}: backed up original -> ${res.backup}`);
    }
    console.log(`  ${res.agent}: ${res.action}`);

    manifest.prompts[name].agents ??= {};
    manifest.prompts[name].agents[agentName] = {
      scope: options.scope,
      mode: options.mode,
      installedAt: new Date().toISOString(),
      target: res.target,
    };
  }

  await setPromptWritable(installedPath, options.writable);
  console.log(`  ${options.writable ? "left writable" : "locked read-only"}: ${installedPath}`);

  await writeManifest(manifest, root);
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
