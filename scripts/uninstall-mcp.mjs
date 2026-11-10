#!/usr/bin/env node

// Uninstall a managed MCP server configuration from agent config files,
// update the manifest, and commit the rollback state to the git tracking repo.

import { realpath } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";

import {
  agents,
  expandHome,
  parseAgents,
  pathStat,
  repositoryRoot,
} from "./lib/common.mjs";
import {
  getAgentMcpConfigPath,
  hasServerInConfig,
  readAgentConfig,
  removeServerFromConfig,
  writeAgentConfig,
} from "./lib/agent-configs.mjs";
import {
  availableMcpServers,
  backupConfigFile,
  commitMcpTracking,
  ensureGitTrackingRepo,
  isServerManaged,
  mcpInstallRoot,
  readMcpManifest,
  removeInstallationRecord,
  writeMcpManifest,
} from "./lib/mcp.mjs";

function usage() {
  console.log(`Uninstall a managed MCP server from agent configuration files.

Usage:
  just mcp:uninstall
  just mcp:uninstall --server <name> --scope <global|project> --agents <names> [options]

Options:
  --server <name>      MCP server name
  --scope <scope>      global or project
  --agents <names>     Comma-separated: gemini (alias agy),claude,codex,omp (or all)
  --project <path>     Project root; required for project scope
  --force              Remove an unmanaged MCP server from the agent config
  -h, --help           Show this help
`);
}

function parseArguments(argv) {
  const options = { force: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--" || argument === "") {
      continue;
    } else if (argument === "--force") {
      options.force = true;
    } else if (argument === "--help" || argument === "-h") {
      options.help = true;
    } else if (["--server", "--scope", "--agents", "--project"].includes(argument)) {
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

  const trackingRoot = mcpInstallRoot();
  await ensureGitTrackingRepo(trackingRoot);
  const manifest = await readMcpManifest(trackingRoot);
  const repoServers = await availableMcpServers(repositoryRoot);
  const installedServers = Object.keys(manifest.servers);
  const validServers = [...new Set([...repoServers, ...installedServers])].sort();

  if (!validServers.length) {
    console.log("No MCP servers found in the repository or the manifest.");
    return;
  }

  if (options.server && !validServers.includes(options.server)) {
    throw new Error(`Unknown MCP server: ${options.server}. Valid servers: ${validServers.join(", ")}`);
  }

  const needsInput =
    !options.server ||
    !options.scope ||
    !options.agents ||
    (options.scope === "project" && !options.project);

  if (needsInput && !process.stdin.isTTY) {
    throw new Error("Missing required options in a non-interactive shell. Run with --help for usage.");
  }

  const rl = needsInput ? createInterface({ input: process.stdin, output: process.stdout }) : undefined;
  let selectedAgents;
  let projectRoot;

  try {
    const promptServers = installedServers.length > 0 ? installedServers : validServers;
    options.server ??= await chooseOne("Select an MCP server to uninstall", promptServers, rl);
    if (!validServers.includes(options.server)) {
      throw new Error(`Unknown MCP server: ${options.server}. Valid servers: ${validServers.join(", ")}`);
    }

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
    const projectStat = await pathStat(projectRoot);
    if (!projectStat || !projectStat.isDirectory()) {
      throw new Error(`Not a project directory: ${projectRoot}`);
    }
  }

  const serverName = options.server;
  const modifiedAgents = [];

  for (const agentName of selectedAgents) {
    const configPath = getAgentMcpConfigPath(agentName, options.scope, projectRoot);
    const existingConfig = await readAgentConfig(configPath);
    const serverInConfig = hasServerInConfig(existingConfig, serverName);
    const managed = isServerManaged(manifest, serverName, agentName, options.scope, projectRoot);
    const agentLabel = agents[agentName]?.label || agentName;

    if (serverInConfig && !managed && !options.force) {
      throw new Error(
        `MCP server "${serverName}" is defined in ${configPath} (${agentLabel}) and is not managed by this tool. Re-run with --force to remove.`,
      );
    }

    const { updatedConfig, removed } = removeServerFromConfig(existingConfig, serverName);

    if (removed) {
      // Backup current config before removing
      await backupConfigFile(trackingRoot, agentName, options.scope, projectRoot, configPath);
      await writeAgentConfig(configPath, updatedConfig);
      removeInstallationRecord(manifest, serverName, agentName, options.scope, projectRoot);
      modifiedAgents.push(agentLabel);
      console.log(`  ${agentLabel}: removed "${serverName}" from ${configPath}`);
    } else {
      if (managed) {
        removeInstallationRecord(manifest, serverName, agentName, options.scope, projectRoot);
      }
      console.log(`  ${agentLabel}: "${serverName}" was not found in ${configPath}`);
    }
  }

  await writeMcpManifest(manifest, trackingRoot);

  if (modifiedAgents.length > 0) {
    const commitMsg = `Uninstall MCP '${serverName}' from ${modifiedAgents.join(", ")} [${options.scope}]`;
    await commitMcpTracking(trackingRoot, commitMsg);
  }

  console.log(`Uninstallation of "${serverName}" completed.`);
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
