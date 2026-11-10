#!/usr/bin/env node

// Report installed vs repository state for every managed MCP server.
// Non-interactive: it never prompts and never changes anything.

import path from "node:path";
import process from "node:process";

import { repositoryRoot } from "./lib/common.mjs";
import {
  getAgentMcpConfigPath,
  hasServerInConfig,
  readAgentConfig,
} from "./lib/agent-configs.mjs";
import {
  availableMcpServers,
  mcpInstallRoot,
  mcpServerDirtyPaths,
  mcpServerVersion,
  readMcpManifest,
} from "./lib/mcp.mjs";

function usage() {
  console.log(`Show the installed state of every managed MCP server.

Usage:
  just mcp:status

Reports, per MCP server, the installed version, current repository version, and state:
  up to date                  installed version matches repository commit
  update available            the repository has a newer commit
  not installed               present in ./mcp but not recorded as installed
  installed but missing from repo   in the manifest with no matching ./mcp entry
  dirty in repo               mcp/<name> has uncommitted changes, so it cannot be installed
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

  const root = mcpInstallRoot();
  const manifest = await readMcpManifest(root);
  const repoServers = await availableMcpServers(repositoryRoot);
  const names = [...new Set([...repoServers, ...Object.keys(manifest.servers)])].sort();

  console.log(`Repository:   ${repositoryRoot}`);
  console.log(`MCP Tracking: ${root}`);
  console.log("");

  if (!names.length) {
    console.log("No MCP servers found in the repository or the manifest.");
    return;
  }

  const rows = [];
  for (const name of names) {
    const recorded = manifest.servers[name];
    const inRepo = repoServers.includes(name);

    let activeInstallations = [];
    if (recorded?.installations && Array.isArray(recorded.installations)) {
      for (const inst of recorded.installations) {
        try {
          const configPath = inst.targetFile || getAgentMcpConfigPath(inst.agent, inst.scope, inst.projectRoot);
          const config = await readAgentConfig(configPath);
          if (hasServerInConfig(config, name)) {
            activeInstallations.push(inst);
          }
        } catch {
          // If reading or parsing config fails, skip
        }
      }
    }

    const isPresent = activeInstallations.length > 0;
    const installedVersion = recorded && isPresent ? recorded.version : "-";

    let repoVersion = "-";
    let state;
    if (!inRepo) {
      state = isPresent ? "installed but missing from repo" : "not installed";
    } else {
      const dirtyPaths = await mcpServerDirtyPaths(name, repositoryRoot);
      if (dirtyPaths.length) {
        state = "dirty in repo";
      }
      try {
        repoVersion = await mcpServerVersion(name, repositoryRoot);
      } catch {
        repoVersion = "uncommitted";
      }
      if (!state) {
        if (!recorded || !isPresent) {
          state = "not installed";
        } else {
          state = recorded.version === repoVersion ? "up to date" : "update available";
        }
      }
    }

    const agentsList = isPresent
      ? activeInstallations.map((i) => `${i.agent} (${i.scope})`).join(", ")
      : "-";

    rows.push({ name, installedVersion, repoVersion, state, agentsList });
  }

  const nameWidth = Math.max(10, ...rows.map((row) => row.name.length));
  const installedWidth = Math.max(9, ...rows.map((row) => row.installedVersion.length));
  const repoWidth = Math.max(10, ...rows.map((row) => row.repoVersion.length));
  const stateWidth = Math.max(15, ...rows.map((row) => row.state.length));

  console.log(
    `${pad("MCP SERVER", nameWidth)}  ${pad("INSTALLED", installedWidth)}  ${pad("REPO", repoWidth)}  ${pad("STATE", stateWidth)}  TARGETS`,
  );
  for (const row of rows) {
    console.log(
      `${pad(row.name, nameWidth)}  ${pad(row.installedVersion, installedWidth)}  ${pad(row.repoVersion, repoWidth)}  ${pad(row.state, stateWidth)}  ${row.agentsList}`,
    );
  }
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
