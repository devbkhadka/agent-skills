import assert from "node:assert/strict";
import { chmod, lstat, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import test from "node:test";

import { commitAll, createRepo, git, makeWorkspace, run, writeMcpServer } from "./fixture.mjs";
import { agentAdapters, getAgentMcpConfigPath } from "../lib/agent-configs.mjs";
import { parseMcpAgents } from "../lib/common.mjs";
import { parseDotenv, resolveAndValidateEnv } from "../lib/mcp.mjs";

async function exists(target) {
  try {
    await lstat(target);
    return true;
  } catch {
    return false;
  }
}

async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}

// Paths under `root` whose contents contain `needle`. Used to assert that a resolved value
// never reaches a directory it should not, so the check fails loudly rather than silently
// passing when the write location changes.
async function grepTree(root, needle) {
  const hits = [];
  for (const entry of await readdir(root, { recursive: true, withFileTypes: true })) {
    if (!entry.isFile()) continue;
    const filePath = path.join(entry.parentPath ?? entry.path, entry.name);
    if (filePath.includes(`${path.sep}.git${path.sep}`)) continue;
    let contents;
    try {
      contents = await readFile(filePath, "utf8");
    } catch {
      continue;
    }
    if (contents.includes(needle)) hits.push(path.relative(root, filePath));
  }
  return hits.sort();
}

function installMcp(workspace, args, extraEnv = {}) {
  return run(workspace, "install-mcp.mjs", args, extraEnv);
}

function statusMcp(workspace, args = []) {
  return run(workspace, "mcp-status.mjs", args);
}

function uninstallMcp(workspace, args = []) {
  return run(workspace, "uninstall-mcp.mjs", args);
}

test("installs MCP server into Gemini, AGY, Claude, Codex, and OMP global configs", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      fetcher: {
        command: "uvx",
        args: ["mcp-server-fetch"],
        env: {
          USER_AGENT: {
            required: false,
            description: "Custom user agent",
            default: "TestAgent/1.0",
          },
        },
      },
    },
  );

  const result = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "all",
  ]);
  assert.equal(result.status, 0, result.stderr);

  // Check Gemini config (~/.gemini/settings.json)
  const geminiConfig = await readJson(path.join(workspace.home, ".gemini", "settings.json"));
  assert.deepEqual(geminiConfig.mcpServers.fetcher, {
    command: "uvx",
    args: ["mcp-server-fetch"],
    env: { USER_AGENT: "TestAgent/1.0" },
  });

  // Check AGY config (~/.gemini/config/mcp_config.json)
  const agyConfig = await readJson(path.join(workspace.home, ".gemini", "config", "mcp_config.json"));
  assert.deepEqual(agyConfig.mcpServers.fetcher, {
    command: "uvx",
    args: ["mcp-server-fetch"],
    env: { USER_AGENT: "TestAgent/1.0" },
  });

  // Check Claude config (~/.claude.json)
  const claudeConfig = await readJson(path.join(workspace.home, ".claude.json"));
  assert.deepEqual(claudeConfig.mcpServers.fetcher, {
    command: "uvx",
    args: ["mcp-server-fetch"],
    env: { USER_AGENT: "TestAgent/1.0" },
  });

  // Check Codex config (~/.codex/config.toml)
  const codexToml = await readFile(path.join(workspace.home, ".codex", "config.toml"), "utf8");
  assert.match(codexToml, /\[mcp_servers\.fetcher\]/);
  assert.match(codexToml, /command = "uvx"/);
  assert.match(codexToml, /args = \["mcp-server-fetch"\]/);
  assert.match(codexToml, /\[mcp_servers\.fetcher\.env\]/);
  assert.match(codexToml, /USER_AGENT = "TestAgent\/1\.0"/);

  // Check OMP config
  const ompConfig = await readJson(path.join(workspace.home, ".omp", "agent", "mcp.json"));
  assert.deepEqual(ompConfig.mcpServers.fetcher, {
    command: "uvx",
    args: ["mcp-server-fetch"],
    env: { USER_AGENT: "TestAgent/1.0" },
  });

  // Check manifest and tracking git repository
  const manifest = await readJson(path.join(workspace.mcpTrackingRoot, "installed.json"));
  assert.ok(manifest.servers.fetcher);
  assert.match(manifest.servers.fetcher.version, /^[0-9a-f]{12}$/);
  assert.equal(manifest.servers.fetcher.installations.length, 5);

  // Check that .git exists in tracking root
  assert.ok(await exists(path.join(workspace.mcpTrackingRoot, ".git")));
});

test("installs project-scoped MCP server into project config directories", async (t) => {
  const workspace = await makeWorkspace(t);
  const projectDir = path.join(workspace.root, "my-app");
  await mkdir(projectDir, { recursive: true });

  await createRepo(
    workspace,
    {},
    {
      context7: {
        command: "npx",
        args: ["-y", "@upstash/context7-mcp"],
        env: {
          CONTEXT7_API_KEY: {
            required: true,
            description: "API Key",
          },
        },
      },
    },
  );

  const result = installMcp(workspace, [
    "--server", "context7",
    "--scope", "project",
    "--project", projectDir,
    "--agents", "claude,gemini",
    "--set", "CONTEXT7_API_KEY=sk_test_12345",
  ]);
  assert.equal(result.status, 0, result.stderr);

  // Project scope references the variable instead of inlining the value, because these files
  // live in the user's repo where no .gitignore rule covers them. Claude and Gemini both
  // expand ${VAR} at launch, so the value stays in the git-ignored mcp/<name>/.env.
  const claudeProject = await readJson(path.join(projectDir, ".mcp.json"));
  assert.deepEqual(claudeProject.mcpServers.context7, {
    command: "npx",
    args: ["-y", "@upstash/context7-mcp"],
    env: { CONTEXT7_API_KEY: "${CONTEXT7_API_KEY}" },
  });

  // Gemini project file is .gemini/settings.json
  const geminiProject = await readJson(path.join(projectDir, ".gemini", "settings.json"));
  assert.deepEqual(geminiProject.mcpServers.context7, {
    command: "npx",
    args: ["-y", "@upstash/context7-mcp"],
    env: { CONTEXT7_API_KEY: "${CONTEXT7_API_KEY}" },
  });

  // The resolved value must not appear anywhere under the project directory.
  const leaked = await grepTree(projectDir, "sk_test_12345");
  assert.deepEqual(leaked, [], `resolved value leaked into: ${leaked.join(", ")}`);
});

test("enforces required environment variables and fails with clear message", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      dbServer: {
        command: "npx",
        args: ["-y", "db-mcp"],
        env: {
          DATABASE_URL: {
            required: true,
            description: "Postgres connection string",
          },
          POOL_SIZE: {
            required: false,
            default: "10",
          },
        },
      },
    },
  );

  const result = installMcp(workspace, [
    "--server", "dbServer",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Missing required environment variable/);
  assert.match(result.stderr, /DATABASE_URL: Postgres connection string/);
});

test("resolves environment variables from local .env and --set overrides", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      secured: {
        command: "uvx",
        args: ["secured-server"],
        env: {
          AUTH_TOKEN: { required: true },
          OPTIONAL_PARAM: { required: false },
        },
        envLocal: "AUTH_TOKEN=from_local_dotenv\nOPTIONAL_PARAM=val1\n",
      },
    },
  );

  // Without override, uses local .env
  const res1 = installMcp(workspace, [
    "--server", "secured",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(res1.status, 0, res1.stderr);
  let claudeConfig = await readJson(path.join(workspace.home, ".claude.json"));
  assert.equal(claudeConfig.mcpServers.secured.env.AUTH_TOKEN, "from_local_dotenv");
  assert.equal(claudeConfig.mcpServers.secured.env.OPTIONAL_PARAM, "val1");

  // With --set override
  const res2 = installMcp(workspace, [
    "--server", "secured",
    "--scope", "global",
    "--agents", "claude",
    "--set", "AUTH_TOKEN=cli_override_token",
  ]);
  assert.equal(res2.status, 0, res2.stderr);
  claudeConfig = await readJson(path.join(workspace.home, ".claude.json"));
  assert.equal(claudeConfig.mcpServers.secured.env.AUTH_TOKEN, "cli_override_token");
});

test("duplicate server: auto-upgrades managed server, blocks unmanaged without --force", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      customServer: {
        command: "npx",
        args: ["custom-v1"],
      },
    },
  );

  // Pre-seed an unmanaged server in ~/.claude.json
  const claudePath = path.join(workspace.home, ".claude.json");
  await writeFile(
    claudePath,
    JSON.stringify({
      mcpServers: {
        customServer: { command: "custom-unmanaged" },
        otherServer: { command: "keep-me" },
      },
    }),
    "utf8",
  );

  // Install without force should fail
  const failed = installMcp(workspace, [
    "--server", "customServer",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /already defined in .* and is not managed by this tool/);

  // With --force, should succeed and create a backup of pre-existing config
  const forced = installMcp(workspace, [
    "--server", "customServer",
    "--scope", "global",
    "--agents", "claude",
    "--force",
  ]);
  assert.equal(forced.status, 0, forced.stderr);

  const claudeConfig = await readJson(claudePath);
  assert.deepEqual(claudeConfig.mcpServers.customServer, {
    command: "npx",
    args: ["custom-v1"],
  });
  // Other server untouched
  assert.deepEqual(claudeConfig.mcpServers.otherServer, { command: "keep-me" });

  // Backup exists in ~/.agents/mcp-servers/backups/ (both origin and latest)
  const backupsDir = path.join(workspace.mcpTrackingRoot, "backups");
  const backupFiles = await readdir(backupsDir);
  assert.ok(backupFiles.some((f) => f.startsWith("claude_global_") && f.endsWith(".claude.json.origin")));
  assert.ok(backupFiles.some((f) => f.startsWith("claude_global_") && f.endsWith(".claude.json.latest")));

  // Now that it's managed, re-installing (auto-upgrade) should succeed without --force
  const upgraded = installMcp(workspace, [
    "--server", "customServer",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(upgraded.status, 0, upgraded.stderr);
});

test("supports SSE / HTTP transport configuration", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      remoteDocs: {
        transport: "sse",
        url: "https://mcp.example.com/sse",
        headers: {
          Authorization: "Bearer token_123",
        },
      },
    },
  );

  const result = installMcp(workspace, [
    "--server", "remoteDocs",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(result.status, 0, result.stderr);

  const claudeConfig = await readJson(path.join(workspace.home, ".claude.json"));
  assert.deepEqual(claudeConfig.mcpServers.remoteDocs, {
    type: "sse",
    url: "https://mcp.example.com/sse",
    headers: { Authorization: "Bearer token_123" },
  });
});

test("status reporter shows installed, up to date, and update available", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      srv1: { command: "srv1" },
      srv2: { command: "srv2" },
    },
  );

  // Install srv1
  assert.equal(
    installMcp(workspace, ["--server", "srv1", "--scope", "global", "--agents", "claude"]).status,
    0,
  );

  // srv1 moves ahead in repo
  await writeMcpServer(workspace, "srv1", { command: "srv1-v2" });
  commitAll(workspace, "update srv1");

  const status = statusMcp(workspace);
  assert.equal(status.status, 0, status.stderr);
  assert.match(status.stdout, /^srv1\s+\S+\s+\S+\s+update available/m);
  assert.match(status.stdout, /^srv2\s+-\s+\S+\s+not installed/m);
});

test("uninstall removes server from agent config and updates manifest", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      fetcher: { command: "fetcher" },
    },
  );

  assert.equal(
    installMcp(workspace, ["--server", "fetcher", "--scope", "global", "--agents", "claude,gemini"]).status,
    0,
  );

  // Verify both have it
  let claudeConfig = await readJson(path.join(workspace.home, ".claude.json"));
  assert.ok(claudeConfig.mcpServers.fetcher);

  // Uninstall from claude only
  const uninstClaude = uninstallMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(uninstClaude.status, 0, uninstClaude.stderr);

  claudeConfig = await readJson(path.join(workspace.home, ".claude.json"));
  assert.ok(!claudeConfig.mcpServers.fetcher);

  // Gemini should still have it
  let geminiConfig = await readJson(path.join(workspace.home, ".gemini", "settings.json"));
  assert.ok(geminiConfig.mcpServers.fetcher);

  // Uninstall from gemini
  const uninstGemini = uninstallMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "gemini",
  ]);
  assert.equal(uninstGemini.status, 0, uninstGemini.stderr);

  geminiConfig = await readJson(path.join(workspace.home, ".gemini", "settings.json"));
  assert.ok(!geminiConfig.mcpServers.fetcher);

  // Manifest should be empty now
  const manifest = await readJson(path.join(workspace.mcpTrackingRoot, "installed.json"));
  assert.deepEqual(manifest.servers, {});
});

test("pre-existing unrelated Codex TOML table survives install and uninstall", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      fetcher: {
        command: "uvx",
        args: ["mcp-server-fetch"],
        env: {
          USER_AGENT: {
            required: false,
            default: "TestAgent/1.0",
          },
        },
      },
    },
  );

  const codexDir = path.join(workspace.home, ".codex");
  await mkdir(codexDir, { recursive: true });
  const codexConfigPath = path.join(codexDir, "config.toml");

  const initialToml = `# User settings
model = "o3-mini"
temperature = 0.2

[editor]
tab_size = 2

[features]
code_search = true
`;
  await writeFile(codexConfigPath, initialToml, "utf8");

  // Install fetcher for codex
  const installRes = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "codex",
  ]);
  assert.equal(installRes.status, 0, installRes.stderr);

  let updatedToml = await readFile(codexConfigPath, "utf8");
  assert.match(updatedToml, /model = "o3-mini"/);
  assert.match(updatedToml, /temperature = 0\.2/);
  assert.match(updatedToml, /\[editor\]/);
  assert.match(updatedToml, /tab_size = 2/);
  assert.match(updatedToml, /\[features\]/);
  assert.match(updatedToml, /code_search = true/);
  assert.match(updatedToml, /\[mcp_servers\.fetcher\]/);
  assert.match(updatedToml, /command = "uvx"/);
  assert.match(updatedToml, /\[mcp_servers\.fetcher\.env\]/);
  assert.match(updatedToml, /USER_AGENT = "TestAgent\/1\.0"/);

  // Uninstall fetcher from codex
  const uninstallRes = uninstallMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "codex",
  ]);
  assert.equal(uninstallRes.status, 0, uninstallRes.stderr);

  const finalToml = await readFile(codexConfigPath, "utf8");
  assert.match(finalToml, /model = "o3-mini"/);
  assert.match(finalToml, /temperature = 0\.2/);
  assert.match(finalToml, /\[editor\]/);
  assert.match(finalToml, /tab_size = 2/);
  assert.match(finalToml, /\[features\]/);
  assert.match(finalToml, /code_search = true/);
  assert.ok(!finalToml.includes("mcp_servers.fetcher"));
});

test("honors CLAUDE_CONFIG_DIR when set for global scope", async (t) => {
  const workspace = await makeWorkspace(t);
  const customClaudeDir = path.join(workspace.root, "custom-claude-dir");
  await mkdir(customClaudeDir, { recursive: true });

  await createRepo(
    workspace,
    {},
    {
      fetcher: { command: "uvx", args: ["fetch"] },
    },
  );

  const res = installMcp(
    workspace,
    ["--server", "fetcher", "--scope", "global", "--agents", "claude"],
    { CLAUDE_CONFIG_DIR: customClaudeDir },
  );
  assert.equal(res.status, 0, res.stderr);

  // Config should be created in customClaudeDir/.claude.json
  const customConfig = await readJson(path.join(customClaudeDir, ".claude.json"));
  assert.ok(customConfig.mcpServers.fetcher);

  // Standard home path should NOT exist
  assert.equal(await exists(path.join(workspace.home, ".claude.json")), false);
});

test("Gemini formats HTTP streaming as httpUrl and SSE as url", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      httpServer: {
        transport: "http",
        url: "https://example.com/http-stream",
        headers: { "X-Test": "1" },
      },
      sseServer: {
        transport: "sse",
        url: "https://example.com/sse-stream",
      },
    },
  );

  const res1 = installMcp(workspace, [
    "--server", "httpServer",
    "--scope", "global",
    "--agents", "gemini",
  ]);
  assert.equal(res1.status, 0, res1.stderr);

  const res2 = installMcp(workspace, [
    "--server", "sseServer",
    "--scope", "global",
    "--agents", "gemini",
  ]);
  assert.equal(res2.status, 0, res2.stderr);

  const geminiConfig = await readJson(path.join(workspace.home, ".gemini", "settings.json"));
  assert.deepEqual(geminiConfig.mcpServers.httpServer, {
    httpUrl: "https://example.com/http-stream",
    headers: { "X-Test": "1" },
  });
  assert.deepEqual(geminiConfig.mcpServers.sseServer, {
    url: "https://example.com/sse-stream",
  });
});

test("AGY adapter uses ~/.gemini/config/mcp_config.json, serverUrl for remote, and rejects project scope", () => {
  const agy = agentAdapters.agy;
  assert.ok(agy);
  assert.equal(agy.label, "AGY");

  // Global config path
  const globalPath = agy.getConfigPath("global");
  assert.match(globalPath, /\.gemini\/config\/mcp_config\.json$/);

  // Project scope throws
  assert.throws(() => agy.getConfigPath("project", "/tmp/project"), /not support project-scoped/);

  // Stdio entry formatting
  const stdioEntry = agy.formatEntry({
    command: "npx",
    args: ["-y", "test-mcp"],
    env: { KEY: "value" },
  });
  assert.deepEqual(stdioEntry, {
    command: "npx",
    args: ["-y", "test-mcp"],
    env: { KEY: "value" },
  });

  // Remote entry formatting uses serverUrl
  const remoteEntry = agy.formatEntry({
    url: "https://example.com/sse",
    headers: { "X-Auth": "abc" },
  });
  assert.deepEqual(remoteEntry, {
    serverUrl: "https://example.com/sse",
    headers: { "X-Auth": "abc" },
  });
});

test("install with --agents agy writes ~/.gemini/config/mcp_config.json and NOT ~/.gemini/settings.json, with AGY label", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      fetcher: {
        command: "uvx",
        args: ["mcp-server-fetch"],
        env: {
          USER_AGENT: {
            required: false,
            default: "AgyTest/1.0",
          },
        },
      },
    },
  );

  const result = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "agy",
  ]);
  assert.equal(result.status, 0, result.stderr);

  // Assert ~/.gemini/config/mcp_config.json is written
  const agyConfigPath = path.join(workspace.home, ".gemini", "config", "mcp_config.json");
  assert.ok(await exists(agyConfigPath), "mcp_config.json must exist");
  const agyConfig = await readJson(agyConfigPath);
  assert.deepEqual(agyConfig.mcpServers.fetcher, {
    command: "uvx",
    args: ["mcp-server-fetch"],
    env: { USER_AGENT: "AgyTest/1.0" },
  });

  // Assert ~/.gemini/settings.json is NOT written
  const geminiSettingsPath = path.join(workspace.home, ".gemini", "settings.json");
  assert.equal(await exists(geminiSettingsPath), false, "settings.json must NOT exist for agy install");

  // Assert install output label is AGY, not Gemini / AGY
  assert.match(result.stdout, /^\s*AGY:\s+configured\s+"fetcher"/m);
  assert.doesNotMatch(result.stdout, /Gemini \/ AGY:/);
  assert.doesNotMatch(result.stdout, /^\s*Gemini:\s+configured/m);

  // Check manifest record
  const manifest = await readJson(path.join(workspace.mcpTrackingRoot, "installed.json"));
  assert.equal(manifest.servers.fetcher.installations.length, 1);
  assert.equal(manifest.servers.fetcher.installations[0].agent, "agy");
  assert.equal(manifest.servers.fetcher.installations[0].targetFile, agyConfigPath);
});

test("install with --agents gemini writes ~/.gemini/settings.json and NOT ~/.gemini/config/mcp_config.json, with Gemini label", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      fetcher: {
        command: "uvx",
        args: ["mcp-server-fetch"],
        env: {
          USER_AGENT: {
            required: false,
            default: "GeminiTest/1.0",
          },
        },
      },
    },
  );

  const result = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "gemini",
  ]);
  assert.equal(result.status, 0, result.stderr);

  // Assert ~/.gemini/settings.json is written
  const geminiSettingsPath = path.join(workspace.home, ".gemini", "settings.json");
  assert.ok(await exists(geminiSettingsPath), "settings.json must exist");
  const geminiConfig = await readJson(geminiSettingsPath);
  assert.deepEqual(geminiConfig.mcpServers.fetcher, {
    command: "uvx",
    args: ["mcp-server-fetch"],
    env: { USER_AGENT: "GeminiTest/1.0" },
  });

  // Assert ~/.gemini/config/mcp_config.json is NOT written
  const agyConfigPath = path.join(workspace.home, ".gemini", "config", "mcp_config.json");
  assert.equal(await exists(agyConfigPath), false, "mcp_config.json must NOT exist for gemini install");

  // Assert install output label is Gemini, not Gemini / AGY
  assert.match(result.stdout, /^\s*Gemini:\s+configured\s+"fetcher"/m);
  assert.doesNotMatch(result.stdout, /Gemini \/ AGY:/);
  assert.doesNotMatch(result.stdout, /^\s*AGY:\s+configured/m);

  // Check manifest record
  const manifest = await readJson(path.join(workspace.mcpTrackingRoot, "installed.json"));
  assert.equal(manifest.servers.fetcher.installations.length, 1);
  assert.equal(manifest.servers.fetcher.installations[0].agent, "gemini");
  assert.equal(manifest.servers.fetcher.installations[0].targetFile, geminiSettingsPath);
});

test("install with --agents agy rejects project scope", async (t) => {
  const workspace = await makeWorkspace(t);
  const projectDir = path.join(workspace.root, "my-app");
  await mkdir(projectDir, { recursive: true });

  await createRepo(
    workspace,
    {},
    {
      fetcher: {
        command: "uvx",
        args: ["mcp-server-fetch"],
      },
    },
  );

  const result = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "project",
    "--project", projectDir,
    "--agents", "agy",
  ]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /AGY does not support project-scoped MCP configuration/);
});

test("parseMcpAgents treats agy and gemini as distinct agents and rejects unknown agents", () => {
  assert.deepEqual(parseMcpAgents("agy"), ["agy"]);
  assert.deepEqual(parseMcpAgents("gemini"), ["gemini"]);
  assert.deepEqual(parseMcpAgents("agy,gemini"), ["agy", "gemini"]);
  assert.deepEqual(parseMcpAgents("all"), ["gemini", "agy", "claude", "codex", "omp"]);
  assert.deepEqual(parseMcpAgents("claude, codex, omp"), ["claude", "codex", "omp"]);
  assert.throws(() => parseMcpAgents("unknown"), /Unknown agent\(s\): unknown/);
});

test("Claude merges into existing ~/.claude.json preserving unrelated user state", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      fetcher: { command: "uvx", args: ["fetch"] },
    },
  );

  const claudePath = path.join(workspace.home, ".claude.json");
  const existingState = {
    theme: "dark",
    fontSize: 14,
    recentProjects: ["/foo/bar", "/baz/qux"],
    customPrompts: { greeting: "Hello agent" },
    mcpServers: {
      existingManaged: { command: "existing" },
    },
  };
  await writeFile(claudePath, JSON.stringify(existingState, null, 2), "utf8");

  const res = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(res.status, 0, res.stderr);

  const updatedClaude = await readJson(claudePath);
  assert.equal(updatedClaude.theme, "dark");
  assert.equal(updatedClaude.fontSize, 14);
  assert.deepEqual(updatedClaude.recentProjects, ["/foo/bar", "/baz/qux"]);
  assert.deepEqual(updatedClaude.customPrompts, { greeting: "Hello agent" });
  assert.ok(updatedClaude.mcpServers.existingManaged);
  assert.ok(updatedClaude.mcpServers.fetcher);
});

test("Codex project scope installs into <projectRoot>/.codex/config.toml", async (t) => {
  const workspace = await makeWorkspace(t);
  const projectDir = path.join(workspace.root, "codex-proj");
  await mkdir(projectDir, { recursive: true });

  await createRepo(
    workspace,
    {},
    {
      fetcher: {
        command: "uvx",
        args: ["mcp-server-fetch"],
        env: { FOO: { required: false, default: "bar" } },
      },
    },
  );

  // A Codex stdio server has no way to reference an environment variable, so project scope
  // cannot write a placeholder. Rather than inlining the value into a file inside the user's
  // repo, install skips the agent and says why. Under a non-TTY it cannot ask, so skipping is
  // the only safe outcome; --inline-project-env is the explicit opt-in.
  const res = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "project",
    "--project", projectDir,
    "--agents", "codex",
  ]);
  assert.equal(res.status, 0, res.stderr);
  assert.match(res.stdout, /Skipped Codex/);
  assert.match(res.stdout, /--inline-project-env/);
  assert.equal(await exists(path.join(projectDir, ".codex", "config.toml")), false);

  // With the opt-in, the value is written and the TOML shape is the one Codex reads.
  const optIn = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "project",
    "--project", projectDir,
    "--agents", "codex",
    "--inline-project-env",
  ]);
  assert.equal(optIn.status, 0, optIn.stderr);

  const codexProjectToml = await readFile(path.join(projectDir, ".codex", "config.toml"), "utf8");
  assert.match(codexProjectToml, /\[mcp_servers\.fetcher\]/);
  assert.match(codexProjectToml, /command = "uvx"/);
  assert.match(codexProjectToml, /\[mcp_servers\.fetcher\.env\]/);
  assert.match(codexProjectToml, /FOO = "bar"/);
});

test("redacts secrets before writing backups and keeps secrets out of tracking git repo (B1)", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      secureServer: {
        command: "uvx",
        args: ["secure-server"],
        env: {
          API_KEY: { required: true, description: "Live secret API key" },
          OTHER_CONFIG: { required: false, default: "normal_val" },
        },
      },
    },
  );

  // Pre-seed agent configs containing live secrets
  const claudePath = path.join(workspace.home, ".claude.json");
  await writeFile(
    claudePath,
    JSON.stringify(
      {
        accessToken: "secret_access_token_LIVE_12345",
        refreshToken: "secret_refresh_token_LIVE_67890",
        primaryApiKey: "sk-live-anthropic-key-abcdef",
        theme: "dark",
        mcpServers: {
          oldServer: {
            command: "old-cmd",
            env: { CONTEXT7_API_KEY: "sk-live-context7-secret-key-999" },
          },
        },
      },
      null,
      2,
    ),
    "utf8",
  );

  const codexDir = path.join(workspace.home, ".codex");
  await mkdir(codexDir, { recursive: true });
  const codexPath = path.join(codexDir, "config.toml");
  await writeFile(
    codexPath,
    `model = "o3-mini"\napiKey = "sk-live-codex-key-1111"\n\n[mcp_servers.legacy]\ncommand = "legacy"\n\n[mcp_servers.legacy.env]\nSECRET_DB_PASS = "super_secret_db_pass_2222"\n`,
    "utf8",
  );

  // Install secureServer with --set and --force
  const installRes = installMcp(workspace, [
    "--server", "secureServer",
    "--scope", "global",
    "--agents", "claude,codex",
    "--set", "API_KEY=sk_live_override_secret_3333",
    "--force",
  ]);
  assert.equal(installRes.status, 0, installRes.stderr);

  // 1. Check that backups/ is in .gitignore in tracking root
  const gitignoreContent = await readFile(path.join(workspace.mcpTrackingRoot, ".gitignore"), "utf8");
  assert.match(gitignoreContent, /^backups\/?$/m);

  // 2. Check all files under backups/ for secrets
  const backupsDir = path.join(workspace.mcpTrackingRoot, "backups");
  const backupFiles = await readdir(backupsDir);
  assert.ok(backupFiles.length >= 2, "Backups should have been created");

  const secretStrings = [
    "secret_access_token_LIVE_12345",
    "secret_refresh_token_LIVE_67890",
    "sk-live-anthropic-key-abcdef",
    "sk-live-context7-secret-key-999",
    "sk-live-codex-key-1111",
    "super_secret_db_pass_2222",
    "sk_live_override_secret_3333",
  ];

  for (const file of backupFiles) {
    const filePath = path.join(backupsDir, file);
    const content = await readFile(filePath, "utf8");
    const stat = await lstat(filePath);
    // Check file permission is 0o600
    assert.equal(stat.mode & 0o777, 0o600, `Backup file ${file} should have 0o600 permissions`);

    for (const secret of secretStrings) {
      assert.ok(
        !content.includes(secret),
        `Backup file ${file} must not contain secret "${secret}". Found in content:\n${content}`,
      );
    }
  }

  // Check structure is preserved in Claude backup
  const claudeOriginFile = backupFiles.find((f) => f.startsWith("claude_global_") && f.endsWith(".origin"));
  assert.ok(claudeOriginFile, "Claude origin backup must exist");
  const claudeBackup = JSON.parse(await readFile(path.join(backupsDir, claudeOriginFile), "utf8"));
  assert.equal(claudeBackup.theme, "dark");
  assert.equal(claudeBackup.accessToken, "[redacted]");
  assert.equal(claudeBackup.primaryApiKey, "[redacted]");
  assert.equal(claudeBackup.mcpServers.oldServer.env.CONTEXT7_API_KEY, "[redacted]");

  // 3. Check git log -p in the tracking repo contains no secrets
  const gitLog = git({ repo: workspace.mcpTrackingRoot, home: workspace.home }, ["log", "-p"]);
  for (const secret of secretStrings) {
    assert.ok(
      !gitLog.includes(secret),
      `Tracking repo git log -p must not contain secret "${secret}". Log:\n${gitLog}`,
    );
  }
});

test("refuses to operate on pre-existing git repo missing the tracking marker (B5)", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      fetcher: { command: "uvx", args: ["fetch"] },
    },
  );

  // Pre-initialize trackingRoot as a standard git repo without the marker file
  await mkdir(workspace.mcpTrackingRoot, { recursive: true });
  git({ repo: workspace.mcpTrackingRoot, home: workspace.home }, ["init", "-q", "-b", "main"]);
  await writeFile(path.join(workspace.mcpTrackingRoot, "notes.txt"), "important personal notes\n", "utf8");
  git({ repo: workspace.mcpTrackingRoot, home: workspace.home }, ["add", "notes.txt"]);
  git({ repo: workspace.mcpTrackingRoot, home: workspace.home }, ["commit", "-q", "-m", "init notes", "--no-verify"]);

  // Attempt to install into foreign repo
  const res = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(res.status, 1);
  assert.match(res.stderr, /Refusing to operate on git repository at/);
  assert.match(res.stderr, /\.agent-mcp-tracking/);

  // Check that notes.txt was not modified and no new commits were created
  const notesContent = await readFile(path.join(workspace.mcpTrackingRoot, "notes.txt"), "utf8");
  assert.equal(notesContent, "important personal notes\n");
});

test("preserves origin backup across successive installs and uses path hash (C3)", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      srvA: { command: "srvA" },
      srvB: { command: "srvB" },
    },
  );

  const claudePath = path.join(workspace.home, ".claude.json");
  await writeFile(
    claudePath,
    JSON.stringify({ customKey: "initial_unmanaged_state", mcpServers: {} }, null, 2),
    "utf8",
  );

  // 1. First install: srvA
  const res1 = installMcp(workspace, [
    "--server", "srvA",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(res1.status, 0, res1.stderr);

  const backupsDir = path.join(workspace.mcpTrackingRoot, "backups");
  const filesAfterFirst = await readdir(backupsDir);
  const originFile = filesAfterFirst.find((f) => f.startsWith("claude_global_") && f.endsWith(".origin"));
  const latestFile = filesAfterFirst.find((f) => f.startsWith("claude_global_") && f.endsWith(".latest"));
  assert.ok(originFile);
  assert.ok(latestFile);

  const originContent1 = JSON.parse(await readFile(path.join(backupsDir, originFile), "utf8"));
  assert.equal(originContent1.customKey, "initial_unmanaged_state");
  assert.deepEqual(originContent1.mcpServers, {});

  // 2. Second install: srvB
  const res2 = installMcp(workspace, [
    "--server", "srvB",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(res2.status, 0, res2.stderr);

  // Origin backup MUST NOT have been overwritten
  const originContent2 = JSON.parse(await readFile(path.join(backupsDir, originFile), "utf8"));
  assert.deepEqual(originContent2, originContent1, "Origin backup must remain untouched across subsequent runs");

  // Latest backup reflects state before srvB (which had srvA)
  const latestContent2 = JSON.parse(await readFile(path.join(backupsDir, latestFile), "utf8"));
  assert.ok(latestContent2.mcpServers.srvA);
  assert.ok(!latestContent2.mcpServers.srvB);

  // 3. Uninstall srvA
  const res3 = uninstallMcp(workspace, [
    "--server", "srvA",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(res3.status, 0, res3.stderr);

  // Origin backup STILL untouched
  const originContent3 = JSON.parse(await readFile(path.join(backupsDir, originFile), "utf8"));
  assert.deepEqual(originContent3, originContent1);
});

test("git commits use per-commit identity and scope commit to installed.json (C4)", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      fetcher: { command: "fetcher" },
    },
  );

  const res = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(res.status, 0, res.stderr);

  const logStdout = git({ repo: workspace.mcpTrackingRoot, home: workspace.home }, [
    "log",
    "-1",
    "--format=%an <%ae>",
  ]);
  assert.equal(logStdout.trim(), "Agent MCP Manager <mcp@agents.local>");
});

test("fixture does not leak a developer's real exported environment variable into installed configs", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(
    workspace,
    {},
    {
      envProbe: {
        command: "npx",
        args: ["-y", "@test/env-probe"],
        env: {
          SHARED_SECRET_VAR: { required: false, description: "Optional value some servers declare" },
        },
      },
    },
  );

  // Simulate a developer who happens to have this variable exported in their real shell.
  // Because the fixture used to spread real process.env into the child, this value would
  // previously have been picked up by resolveAndValidateEnv's extraEnv fallback and written
  // into the config (and committed to the fixture's tracking repo).
  const previous = process.env.SHARED_SECRET_VAR;
  process.env.SHARED_SECRET_VAR = "leaked-from-developer-shell";
  t.after(() => {
    if (previous === undefined) delete process.env.SHARED_SECRET_VAR;
    else process.env.SHARED_SECRET_VAR = previous;
  });

  const result = installMcp(workspace, ["--server", "envProbe", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 0, result.stderr);

  const claudeConfig = await readJson(path.join(workspace.home, ".claude.json"));
  assert.deepEqual(claudeConfig.mcpServers.envProbe, {
    command: "npx",
    args: ["-y", "@test/env-probe"],
  });
  assert.equal(claudeConfig.mcpServers.envProbe.env, undefined, "an unset, non-required var must not be harvested");

  const leaked = await grepTree(workspace.home, "leaked-from-developer-shell");
  assert.deepEqual(leaked, [], `developer shell value leaked into: ${leaked.join(", ")}`);

  const manifestLeaked = await grepTree(workspace.mcpTrackingRoot, "leaked-from-developer-shell");
  assert.deepEqual(manifestLeaked, [], `developer shell value leaked into tracking root: ${manifestLeaked.join(", ")}`);
});

test("parseDotenv handles comments, quoting, export, CRLF, duplicates, and empty values (ported)", () => {
  // 1. Inline comments stripped from unquoted value
  assert.equal(parseDotenv("FOO=bar # note").FOO, "bar");

  // 2. Hash without preceding whitespace preserved
  assert.equal(parseDotenv("FOO=a#b").FOO, "a#b");

  // 3. Hash with preceding whitespace stripped even if other hashes exist
  assert.equal(parseDotenv("FOO=a#b # note").FOO, "a#b");

  // 4. Quoted values with hash preserved
  assert.equal(parseDotenv('FOO="bar # note"').FOO, "bar # note");
  assert.equal(parseDotenv("FOO='bar # note'").FOO, "bar # note");

  // 5. Quoted values with inline comment after quote
  assert.equal(parseDotenv('FOO="bar" # trailing comment').FOO, "bar");
  assert.equal(parseDotenv("FOO='bar' # trailing comment").FOO, "bar");

  // 6. Export prefix stripped
  assert.equal(parseDotenv("export FOO=bar").FOO, "bar");
  assert.equal(parseDotenv("export   FOO=\"bar # note\" # trailing").FOO, "bar # note");

  // 7. Equals sign inside values
  assert.equal(
    parseDotenv("URL=https://example.com/path?a=1&b=2 # comment").URL,
    "https://example.com/path?a=1&b=2",
  );

  // 8. CRLF line endings
  const crlf = parseDotenv("FOO=bar\r\nBAZ=qux\r\nexport QUX=\"hello\"\r\n");
  assert.equal(crlf.FOO, "bar");
  assert.equal(crlf.BAZ, "qux");
  assert.equal(crlf.QUX, "hello");

  // 9. Blank lines and comments
  assert.deepEqual(
    parseDotenv("\n\n# whole line comment\n  # spaced comment\n\nFOO=bar\n"),
    { FOO: "bar" },
  );

  // 10. Duplicate keys (last one wins)
  assert.equal(parseDotenv("FOO=first\nFOO=second").FOO, "second");

  // 11. Empty values
  const empties = parseDotenv("EMPTY=\nEMPTY_Q=\"\"\nEMPTY_S=''\nEMPTY_C= # comment");
  assert.equal(empties.EMPTY, "");
  assert.equal(empties.EMPTY_Q, "");
  assert.equal(empties.EMPTY_S, "");
  assert.equal(empties.EMPTY_C, "");
});

test("resolveAndValidateEnv enforces priority, fail-fast required checks, and forbidden/undeclared keys (ported)", async () => {
  const def = {
    name: "test-server",
    env: {
      API_KEY: { required: true, description: "API Key" },
      REGION: { required: false, default: "us-east-1" },
    },
  };

  // 1. Successful resolution
  assert.deepEqual(await resolveAndValidateEnv(def, "/tmp", { API_KEY: "secret" }, {}), {
    API_KEY: "secret",
    REGION: "us-east-1",
  });

  // 2. Reject undeclared CLI override
  await assert.rejects(
    () => resolveAndValidateEnv(def, "/tmp", { API_KEY: "secret", EXTRA_VAR: "bad" }, {}),
    /Undeclared environment variable "EXTRA_VAR"/,
  );

  // 3. Reject NODE_OPTIONS via --set
  await assert.rejects(
    () => resolveAndValidateEnv(def, "/tmp", { NODE_OPTIONS: "--require evil" }, {}),
    /Forbidden environment variable "NODE_OPTIONS"/,
  );

  // 4. Reject LD_PRELOAD via --set
  await assert.rejects(
    () => resolveAndValidateEnv(def, "/tmp", { LD_PRELOAD: "/lib/evil.so" }, {}),
    /Forbidden environment variable "LD_PRELOAD"/,
  );

  // 5. Reject PATH via --set
  await assert.rejects(
    () => resolveAndValidateEnv(def, "/tmp", { PATH: "/tmp/evil" }, {}),
    /Forbidden environment variable "PATH"/,
  );

  // 6. Reject DYLD_* via --set
  await assert.rejects(
    () => resolveAndValidateEnv(def, "/tmp", { DYLD_INSERT_LIBRARIES: "/tmp/evil.dylib" }, {}),
    /Forbidden environment variable "DYLD_INSERT_LIBRARIES"/,
  );

  // 7. Reject forbidden variable in mcp.json declaration
  const evilDef = { name: "evil-server", env: { NODE_OPTIONS: { required: false } } };
  await assert.rejects(
    () => resolveAndValidateEnv(evilDef, "/tmp", {}, {}),
    /Forbidden environment variable "NODE_OPTIONS" declared/,
  );

  const dyldDef = { name: "dyld-server", env: { DYLD_LIBRARY_PATH: { required: false } } };
  await assert.rejects(
    () => resolveAndValidateEnv(dyldDef, "/tmp", {}, {}),
    /Forbidden environment variable "DYLD_LIBRARY_PATH" declared/,
  );

  // 8. Reject missing required variable
  await assert.rejects(
    () => resolveAndValidateEnv(def, "/tmp", {}, {}),
    /Missing required environment variable\(s\)/,
  );

  // 9. Extra process.env variables not in mcp.json are NOT harvested
  const shellEnv = {
    API_KEY: "from-shell",
    AWS_SECRET_ACCESS_KEY: "secret-not-declared",
    NODE_OPTIONS: "--require /tmp/something",
  };
  const resolved = await resolveAndValidateEnv(def, "/tmp", {}, shellEnv);
  assert.deepEqual(resolved, { API_KEY: "from-shell", REGION: "us-east-1" });
  assert.equal(resolved.AWS_SECRET_ACCESS_KEY, undefined);
  assert.equal(resolved.NODE_OPTIONS, undefined);
});

test("tracking repo scaffolding and manifest get restrictive permissions; a stricter mode is preserved (D: file modes, B4)", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, { fetcher: { command: "uvx", args: ["fetch"] } });

  const result = installMcp(workspace, ["--server", "fetcher", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 0, result.stderr);

  // The tracking root (mkdir'd by ensureGitTrackingRepo) and the manifest it protects
  // (installed.json, written via writeJsonFileAtomic) are the parts of the permissions
  // contract this codebase currently gets right.
  const trackingStat = await lstat(workspace.mcpTrackingRoot);
  assert.equal(trackingStat.mode & 0o777, 0o700, "tracking root directory should be 0700");

  const manifestPath = path.join(workspace.mcpTrackingRoot, "installed.json");
  const manifestStat = await lstat(manifestPath);
  assert.equal(manifestStat.mode & 0o777, 0o600, "installed.json manifest should be 0600");

  // A pre-existing stricter mode on the manifest must be preserved, not widened, across a
  // later write (writeJsonFileAtomic only widens up to 0600, never down).
  await chmod(manifestPath, 0o400);
  const secondResult = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "claude",
    "--reinstall",
  ]);
  assert.equal(secondResult.status, 0, secondResult.stderr);
  const manifestStatAfter = await lstat(manifestPath);
  assert.equal(manifestStatAfter.mode & 0o777, 0o400, "pre-existing stricter manifest mode must be preserved");
  await chmod(manifestPath, 0o600); // restore so workspace cleanup can remove it

  // The agent configs themselves carry the credentials, so they get the same treatment as the
  // manifest. These once landed 0644 because the agent-config writer was a separate copy that
  // set no mode; both writers now share one implementation in common.mjs.
  const claudeMode = (await lstat(path.join(workspace.home, ".claude.json"))).mode & 0o777;
  assert.equal(claudeMode, 0o600, "installed Claude config must be owner-only");

  // Directory modes are covered by the tracking-root assertion above, which is a directory this
  // code creates; the Claude global config's parent here is the mkdtemp workspace root.

  // A deliberately stricter agent config must survive a later install rather than be widened.
  const claudePath = path.join(workspace.home, ".claude.json");
  await chmod(claudePath, 0o400);
  const reinstall = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(reinstall.status, 0, reinstall.stderr);
  assert.equal((await lstat(claudePath)).mode & 0o777, 0o400,
    "pre-existing stricter agent config mode must be preserved");
  await chmod(claudePath, 0o600); // restore so workspace cleanup can remove it
});

test("partial multi-agent failure records successful agents without rollback and exits non-zero (C2)", async (t) => {
  if (process.getuid && process.getuid() === 0) {
    t.skip("cannot verify permission denial while running as root");
    return;
  }

  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, { fetcher: { command: "uvx", args: ["fetch"] } });

  // Make Codex's target directory unwritable so its install step fails with EACCES while the
  // agents ahead of it in the list (claude, gemini) have already succeeded.
  const codexDir = path.join(workspace.home, ".codex");
  await mkdir(codexDir, { recursive: true });
  await chmod(codexDir, 0o500);
  t.after(async () => {
    await chmod(codexDir, 0o700).catch(() => {});
  });

  let writeDenied = false;
  try {
    const probe = path.join(codexDir, "probe.tmp");
    await writeFile(probe, "x");
    await rm(probe, { force: true });
  } catch (error) {
    if (error.code === "EACCES" || error.code === "EPERM") writeDenied = true;
  }
  if (!writeDenied) {
    t.skip("chmod 0500 does not deny writes on this platform/filesystem");
    return;
  }

  const result = installMcp(workspace, [
    "--server", "fetcher",
    "--scope", "global",
    "--agents", "claude,gemini,codex",
  ]);

  assert.notEqual(result.status, 0, "a partial failure must exit non-zero");
  assert.match(result.stderr, /Failed: Codex/);
  assert.match(result.stderr, /Succeeded: Claude, Gemini/);

  // Intended behavior is accurately recorded partial success, not rollback: the agents that
  // succeeded stay recorded and configured.
  const manifest = await readJson(path.join(workspace.mcpTrackingRoot, "installed.json"));
  assert.ok(manifest.servers.fetcher, "successful installations must still be recorded");
  const recordedAgents = manifest.servers.fetcher.installations.map((installation) => installation.agent).sort();
  assert.deepEqual(recordedAgents, ["claude", "gemini"]);

  const claudeConfig = await readJson(path.join(workspace.home, ".claude.json"));
  assert.ok(claudeConfig.mcpServers.fetcher, "claude config must not be rolled back");
  const geminiConfig = await readJson(path.join(workspace.home, ".gemini", "settings.json"));
  assert.ok(geminiConfig.mcpServers.fetcher, "gemini config must not be rolled back");

  // mcp-status must report the succeeded agents as installed, not missing.
  const status = statusMcp(workspace);
  assert.equal(status.status, 0, status.stderr);
  const fetcherLine = status.stdout.split("\n").find((line) => line.startsWith("fetcher"));
  assert.ok(fetcherLine, "mcp-status must report the fetcher row");
  assert.match(fetcherLine, /up to date/);
  assert.match(fetcherLine, /claude \(global\)/);
  assert.match(fetcherLine, /gemini \(global\)/);
  assert.ok(!fetcherLine.includes("codex"), "codex never succeeded and must not be reported as installed");
});

test("Codex http/sse project scope emits bearer_token_env_var and never writes the token value", async (t) => {
  const workspace = await makeWorkspace(t);
  const projectDir = path.join(workspace.root, "codex-remote-proj");
  await mkdir(projectDir, { recursive: true });

  await createRepo(
    workspace,
    {},
    {
      remoteApi: {
        transport: "sse",
        url: "https://mcp.example.com/sse",
        env: {
          API_TOKEN: { required: true, description: "Bearer token" },
        },
      },
    },
  );

  const result = installMcp(workspace, [
    "--server", "remoteApi",
    "--scope", "project",
    "--project", projectDir,
    "--agents", "codex",
    "--set", "API_TOKEN=sk_secret_codex_remote_999",
  ]);
  assert.equal(result.status, 0, result.stderr);

  const codexProjectToml = await readFile(path.join(projectDir, ".codex", "config.toml"), "utf8");
  assert.match(codexProjectToml, /\[mcp_servers\.remoteApi\]/);
  assert.match(codexProjectToml, /url = "https:\/\/mcp\.example\.com\/sse"/);
  assert.match(codexProjectToml, /bearer_token_env_var = "API_TOKEN"/);
  assert.ok(!codexProjectToml.includes("sk_secret_codex_remote_999"));

  const leaked = await grepTree(projectDir, "sk_secret_codex_remote_999");
  assert.deepEqual(leaked, [], `token value leaked into: ${leaked.join(", ")}`);
});

test("OMP installs into ~/.omp/agent/mcp.json at global scope and <project>/.omp/mcp.json at project scope", async (t) => {
  const workspace = await makeWorkspace(t);
  const projectDir = path.join(workspace.root, "omp-proj");
  await mkdir(projectDir, { recursive: true });

  await createRepo(
    workspace,
    {},
    {
      ompServer: { command: "npx", args: ["-y", "@test/omp-server"] },
    },
  );

  const globalResult = installMcp(workspace, ["--server", "ompServer", "--scope", "global", "--agents", "omp"]);
  assert.equal(globalResult.status, 0, globalResult.stderr);
  const ompGlobal = await readJson(path.join(workspace.home, ".omp", "agent", "mcp.json"));
  assert.deepEqual(ompGlobal.mcpServers.ompServer, {
    command: "npx",
    args: ["-y", "@test/omp-server"],
  });

  const projectResult = installMcp(workspace, [
    "--server", "ompServer",
    "--scope", "project",
    "--project", projectDir,
    "--agents", "omp",
  ]);
  assert.equal(projectResult.status, 0, projectResult.stderr);
  const ompProject = await readJson(path.join(projectDir, ".omp", "mcp.json"));
  assert.deepEqual(ompProject.mcpServers.ompServer, {
    command: "npx",
    args: ["-y", "@test/omp-server"],
  });
});
