#!/usr/bin/env node

// Install a managed MCP server configuration into agent configuration files
// with atomic writes, environment validation, backup snapshots, and git version tracking.

import { realpath } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";

import {
  expandHome,
  mcpAgents,
  parseMcpAgents,
  pathStat,
  repositoryRoot,
} from "./lib/common.mjs";
import {
  applyServerToConfig,
  formatMcpServerPayload,
  getAgentMcpConfigPath,
  hasServerInConfig,
  readAgentConfig,
  writeAgentConfig,
} from "./lib/agent-configs.mjs";
import {
  availableMcpServers,
  backupConfigFile,
  commitMcpTracking,
  ensureGitTrackingRepo,
  isServerManaged,
  mcpInstallRoot,
  mcpServerDirtyPaths,
  mcpServerVersion,
  readMcpDefinition,
  readMcpManifest,
  recordInstallation,
  resolveAndValidateEnv,
  writeMcpManifest,
} from "./lib/mcp.mjs";

function usage() {
  console.log(`Install a managed MCP server into agent configuration files.

Usage:
  just mcp:install
  just mcp:install --server <name> --scope <global|project> --agents <names> [options]

Prerequisites:
  git is a hard prerequisite and must be installed on PATH for tracking and version history.

Options:
  --server <name>           MCP server directory under ./mcp
  --scope <scope>           global or project
  --agents <names>          Comma-separated: gemini,agy,claude,codex,omp (or all)
  --project <path>          Project root; required for project scope
  --set <KEY=VALUE>         Override or provide environment variable (can be repeated).
                            Caveat: CLI arguments are visible via 'ps'; prefer local .env for sensitive secrets.
  --inline-project-env      Opt-in to write unexpanded secrets directly into project configs for agents without env-var expansion (Codex stdio, OMP)
  --force                   Replace an unmanaged existing MCP server in the agent config
  --reinstall               Re-apply configuration even if version is unchanged
  -h, --help                Show this help

Behaviour:
  Validates required environment variables from .env.example, local .env, and --set flags.
  At project scope, writes \${KEY} placeholders (Claude/Gemini) or bearer_token_env_var (Codex HTTP/SSE) to protect against git leakage.
  Backs up agent configuration files to \${mcpInstallRoot()}/backups before modifying.
  Maintains version history and rollback commits in a local git tracking repository.
`);
}

function parseArguments(argv) {
  const options = { force: false, reinstall: false, inlineProjectEnv: false, set: {} };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--" || argument === "") {
      continue;
    } else if (argument === "--force") {
      options.force = true;
    } else if (argument === "--reinstall") {
      options.reinstall = true;
    } else if (argument === "--inline-project-env") {
      options.inlineProjectEnv = true;
    } else if (argument === "--help" || argument === "-h") {
      options.help = true;
    } else if (argument === "--set") {
      const pair = argv[index + 1];
      if (!pair || pair.startsWith("--") || !pair.includes("=")) {
        throw new Error(`--set requires a KEY=VALUE pair.`);
      }
      const equalsIndex = pair.indexOf("=");
      const key = pair.slice(0, equalsIndex).trim();
      const val = pair.slice(equalsIndex + 1).trim();
      options.set[key] = val;
      index += 1;
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
  const keys = Object.keys(mcpAgents);
  keys.forEach((key, index) => console.log(`  ${index + 1}) ${mcpAgents[key].label}`));
  const answer = (await rl.question("Select agents (comma-separated numbers, or 'all'): ")).trim();
  if (answer.toLowerCase() === "all") return keys;
  const selected = answer.split(",").map((value) => Number.parseInt(value.trim(), 10) - 1);
  if (!selected.length || selected.some((index) => !Number.isInteger(index) || !keys[index])) {
    throw new Error(`Invalid agent selection: ${answer}`);
  }
  return [...new Set(selected.map((index) => keys[index]))];
}

async function askYesNo(questionText) {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = await rl.question(questionText);
    return /^y(es)?$/i.test(answer.trim());
  } finally {
    rl.close();
  }
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    usage();
    return;
  }

  const servers = await availableMcpServers(repositoryRoot);
  if (!servers.length) {
    throw new Error("No MCP servers containing mcp.json were found under ./mcp.");
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
    options.server ??= await chooseOne("Select an MCP server", servers, rl);
    if (!servers.includes(options.server)) {
      throw new Error(`Unknown MCP server: ${options.server}`);
    }

    options.scope ??= (await chooseOne("Select scope", ["Global", "Project"], rl)).toLowerCase();
    if (!["global", "project"].includes(options.scope)) {
      throw new Error(`Invalid scope: ${options.scope}`);
    }

    selectedAgents = options.agents ? parseMcpAgents(options.agents) : await chooseAgents(rl);
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
  const dirtyPaths = await mcpServerDirtyPaths(serverName, repositoryRoot);
  if (dirtyPaths.length) {
    const detail = dirtyPaths.map((line) => `    ${line}`).join("\n");
    throw new Error(
      `Uncommitted changes block installing MCP server "${serverName}"; commit or stash them first:\n  ${serverName}:\n${detail}`,
    );
  }

  const version = await mcpServerVersion(serverName, repositoryRoot);
  const mcpDef = await readMcpDefinition(serverName, repositoryRoot);
  const serverDir = path.join(repositoryRoot, "mcp", serverName);
  const resolvedEnv = await resolveAndValidateEnv(mcpDef, serverDir, options.set, process.env);

  const trackingRoot = mcpInstallRoot();
  await ensureGitTrackingRepo(trackingRoot);
  const manifest = await readMcpManifest(trackingRoot);

  const modifiedAgents = [];
  const failedAgents = [];

  for (const agentName of selectedAgents) {
    try {
      let serverPayload;
      let shouldWarnGitignore = false;

      if (options.scope === "project") {
        // Rung 4: AGY is global-only
        if (agentName === "agy") {
          throw new Error("AGY does not support project-scoped MCP configuration. Install at global scope instead.");
        }

        // Rung 1: Agent expands ${VAR} (Claude Code and Gemini CLI)
        if (agentName === "claude" || agentName === "gemini") {
          const placeholderEnv = {};
          for (const key of Object.keys(resolvedEnv)) {
            placeholderEnv[key] = `\${${key}}`;
          }
          serverPayload = formatMcpServerPayload(mcpDef, placeholderEnv, agentName);
        }
        // Rung 2: Codex HTTP and SSE servers (native bearer_token_env_var)
        else if (
          agentName === "codex" &&
          (mcpDef.transport === "http" || mcpDef.transport === "sse" || (!mcpDef.transport && mcpDef.url))
        ) {
          const tokenVar =
            mcpDef.bearer_token_env_var ||
            Object.keys(mcpDef.env || {})[0] ||
            Object.keys(resolvedEnv)[0];
          const codexDef = { ...mcpDef };
          if (tokenVar) {
            codexDef.bearer_token_env_var = tokenVar;
          }
          serverPayload = formatMcpServerPayload(codexDef, {}, agentName);
        }
        // Rung 3: Codex stdio servers and OMP (unverified env expansion)
        else {
          const configPath = getAgentMcpConfigPath(agentName, options.scope, projectRoot);
          const hasEnvVars = Object.keys(resolvedEnv).length > 0;

          if (!hasEnvVars) {
            serverPayload = formatMcpServerPayload(mcpDef, {}, agentName);
          } else if (options.inlineProjectEnv) {
            serverPayload = formatMcpServerPayload(mcpDef, resolvedEnv, agentName);
            shouldWarnGitignore = true;
          } else if (process.stdin.isTTY) {
            const agentLabel = mcpAgents[agentName]?.label || agentName;
            const transportName = mcpDef.transport || "stdio";
            const confirmed = await askYesNo(
              `${agentLabel} cannot reference an environment variable for a ${transportName} server. Write the value directly into ${configPath}? [y/N]: `,
            );
            if (confirmed) {
              serverPayload = formatMcpServerPayload(mcpDef, resolvedEnv, agentName);
              shouldWarnGitignore = true;
            } else {
              console.log(
                `  Skipped ${agentLabel}: cannot reference environment variables at project scope in ${configPath}. Install at global scope instead.`,
              );
              continue;
            }
          } else {
            const agentLabel = mcpAgents[agentName]?.label || agentName;
            console.log(
              `  Skipped ${agentLabel}: cannot reference environment variables at project scope in ${configPath}. Use global scope or pass --inline-project-env to store values directly.`,
            );
            continue;
          }
        }
      } else {
        // Global scope: write real resolved values
        serverPayload = formatMcpServerPayload(mcpDef, resolvedEnv, agentName);
      }

      const configPath = getAgentMcpConfigPath(agentName, options.scope, projectRoot);
      const existingConfig = await readAgentConfig(configPath);
      const serverAlreadyInConfig = hasServerInConfig(existingConfig, serverName);
      const managed = isServerManaged(manifest, serverName, agentName, options.scope, projectRoot);

      if (serverAlreadyInConfig && !managed) {
        if (!options.force) {
          throw new Error(
            `MCP server "${serverName}" is already defined in ${configPath} and is not managed by this tool. Re-run with --force to overwrite.`,
          );
        }
      }

      // Backup existing configuration file before mutation
      await backupConfigFile(trackingRoot, agentName, options.scope, projectRoot, configPath);

      // Apply updated server payload
      const updatedConfig = applyServerToConfig(existingConfig, serverName, serverPayload);
      await writeAgentConfig(configPath, updatedConfig);

      // Record in manifest and persist immediately per agent so manifest never lags reality (C2)
      recordInstallation(manifest, serverName, version, agentName, options.scope, projectRoot, configPath);
      await writeMcpManifest(manifest, trackingRoot);

      const agentLabel = mcpAgents[agentName]?.label || agentName;
      modifiedAgents.push(agentLabel);
      console.log(`  ${agentLabel}: configured "${serverName}" (${version}) in ${configPath}`);
      if (shouldWarnGitignore) {
        console.warn(
          `  Warning: Stored unexpanded environment values in ${configPath}. It is strongly recommended to add this file to .gitignore.`,
        );
      }
    } catch (error) {
      // Deliberate design decision (C2): Do not attempt a full rollback of already-written agent configs
      // on partial failure. Partial success that is accurately recorded in the manifest is safer and more
      // reliable than a rollback mechanism that can itself fail halfway.
      failedAgents.push({
        agent: mcpAgents[agentName]?.label || agentName,
        error: error.message,
      });
      break;
    }
  }

  // Commit changes for any successfully modified agents to the tracking repo
  if (modifiedAgents.length > 0) {
    const commitMsg = `Install MCP '${serverName}' (${version}) for ${modifiedAgents.join(", ")} [${options.scope}]`;
    await commitMcpTracking(trackingRoot, commitMsg);
  }

  if (failedAgents.length > 0) {
    const succeededStr = modifiedAgents.length > 0 ? modifiedAgents.join(", ") : "none";
    const failedStr = failedAgents.map((f) => `${f.agent} (${f.error})`).join(", ");
    throw new Error(
      `MCP server "${serverName}" installation failed partially.\n  Succeeded: ${succeededStr}\n  Failed: ${failedStr}`,
    );
  }

  if (modifiedAgents.length > 0) {
    console.log(`MCP server "${serverName}" (${version}) successfully installed.`);
  }

  if (options.scope === "project" && Object.keys(resolvedEnv).length > 0) {
    console.log(`\nNote: For project-scoped MCP servers, ensure the following environment variable(s) are exported in your shell:`);
    for (const key of Object.keys(resolvedEnv)) {
      console.log(`  - ${key}`);
    }
  }
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
