// Test fixtures: a throwaway git repository plus a throwaway HOME, so nothing
// in these tests can reach the real home directory or this repository's history.

import { execFileSync, spawnSync } from "node:child_process";
import { chmod, cp, lstat, mkdir, mkdtemp, readdir, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Restore owner write across a tree so a read-only install copy can be removed. */
export async function unlockTree(target) {
  let stat;
  try {
    stat = await lstat(target);
  } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }
  if (stat.isSymbolicLink()) return;
  await chmod(target, (stat.mode & 0o777) | 0o200 | (stat.isDirectory() ? 0o100 : 0));
  if (!stat.isDirectory()) return;
  for (const entry of await readdir(target)) await unlockTree(path.join(target, entry));
}

/** rm -rf that survives the read-only tree the installer leaves behind. */
export async function forceRemove(target) {
  await unlockTree(target);
  await rm(target, { recursive: true, force: true });
}

export async function makeWorkspace(t) {
  // realpath so /var vs /private/var never makes symlink comparisons ambiguous.
  const root = await realpath(await mkdtemp(path.join(tmpdir(), "agent-skills-test-")));
  t.after(() => forceRemove(root));
  const workspace = {
    root,
    repo: path.join(root, "repo"),
    home: path.join(root, "home"),
    installRoot: path.join(root, "agents", "skills"),
    promptsInstallRoot: path.join(root, "agents", "system-prompts"),
    mcpTrackingRoot: path.join(root, "agents", "mcp-servers"),
  };
  await mkdir(workspace.home, { recursive: true });
  return workspace;
}

// Only what Node and git need to run at all get passed through from the real environment.
// Everything else a developer happens to export (DATABASE_URL, API keys, ...) must not reach
// the child process: `resolveAndValidateEnv`'s process.env fallback would otherwise harvest it
// into a written agent config and commit it to the fixture's git repo.
const PASSTHROUGH_ENV_KEYS = ["PATH", "TMPDIR"];

function baseEnv(workspace) {
  const passthrough = {};
  for (const key of PASSTHROUGH_ENV_KEYS) {
    if (process.env[key] !== undefined) passthrough[key] = process.env[key];
  }
  return {
    ...passthrough,
    HOME: workspace.home,
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_CONFIG_GLOBAL: path.join(workspace.home, ".gitconfig"),
    GIT_AUTHOR_NAME: "Fixture",
    GIT_AUTHOR_EMAIL: "fixture@example.com",
    GIT_COMMITTER_NAME: "Fixture",
    GIT_COMMITTER_EMAIL: "fixture@example.com",
  };
}

function gitEnv(workspace) {
  return baseEnv(workspace);
}

export function git(workspace, args) {
  return execFileSync("git", args, { cwd: workspace.repo, encoding: "utf8", env: gitEnv(workspace) });
}

/**
 * Build a fixture repository containing the real scripts, skills, and mcp servers.
 */
export async function createRepo(workspace, skills = {}, mcpServers = {}, systemPrompts = {}) {
  await mkdir(path.join(workspace.repo, "scripts"), { recursive: true });
  await cp(path.join(scriptsRoot, "install-skill.mjs"), path.join(workspace.repo, "scripts", "install-skill.mjs"));
  await cp(path.join(scriptsRoot, "skill-status.mjs"), path.join(workspace.repo, "scripts", "skill-status.mjs"));
  await cp(path.join(scriptsRoot, "install-prompt.mjs"), path.join(workspace.repo, "scripts", "install-prompt.mjs"));
  await cp(path.join(scriptsRoot, "prompt-status.mjs"), path.join(workspace.repo, "scripts", "prompt-status.mjs"));
  await cp(path.join(scriptsRoot, "install-mcp.mjs"), path.join(workspace.repo, "scripts", "install-mcp.mjs"));
  await cp(path.join(scriptsRoot, "mcp-status.mjs"), path.join(workspace.repo, "scripts", "mcp-status.mjs"));
  await cp(path.join(scriptsRoot, "uninstall-mcp.mjs"), path.join(workspace.repo, "scripts", "uninstall-mcp.mjs"));
  await cp(path.join(scriptsRoot, "lib"), path.join(workspace.repo, "scripts", "lib"), { recursive: true });

  for (const [name, spec] of Object.entries(skills)) await writeSkill(workspace, name, spec);
  for (const [name, spec] of Object.entries(mcpServers)) await writeMcpServer(workspace, name, spec);
  for (const [name, spec] of Object.entries(systemPrompts)) await writeSystemPrompt(workspace, name, spec);

  git(workspace, ["init", "-q", "-b", "main"]);
  commitAll(workspace, "fixture");
}

export async function writeSkill(workspace, name, spec = {}) {
  const directory = path.join(workspace.repo, "skills", name);
  await mkdir(directory, { recursive: true });
  await writeFile(path.join(directory, "SKILL.md"), spec.body ?? `# ${name}\n`, "utf8");
  if (spec.dependencies) {
    await writeFile(
      path.join(directory, "dependencies.json"),
      `${JSON.stringify({ dependencies: spec.dependencies }, undefined, 2)}\n`,
      "utf8",
    );
  }
}

export async function writeMcpServer(workspace, name, spec = {}) {
  const directory = path.join(workspace.repo, "mcp", name);
  await mkdir(directory, { recursive: true });
  await writeFile(path.join(directory, "MCP.md"), spec.doc ?? `# ${name}\n`, "utf8");

  const mcpJson = {
    name,
    description: spec.description ?? `MCP server for ${name}`,
    transport: spec.transport ?? "stdio",
    command: spec.command ?? "npx",
    args: spec.args ?? ["-y", `@test/${name}`],
    ...(spec.url ? { url: spec.url } : {}),
    ...(spec.headers ? { headers: spec.headers } : {}),
    env: spec.env ?? {},
  };
  await writeFile(path.join(directory, "mcp.json"), `${JSON.stringify(mcpJson, undefined, 2)}\n`, "utf8");

  if (spec.envExample) {
    await writeFile(path.join(directory, ".env.example"), spec.envExample, "utf8");
  }
  if (spec.envLocal) {
    await writeFile(path.join(directory, ".env"), spec.envLocal, "utf8");
  }
}

export async function writeSystemPrompt(workspace, name, spec = {}) {
  const directory = path.join(workspace.repo, "system-prompts", name);
  await mkdir(directory, { recursive: true });
  await writeFile(path.join(directory, "system-prompt.md"), spec.body ?? `# ${name}\n`, "utf8");
  if (spec.overrides) {
    const overridesDir = path.join(directory, "overrides");
    await mkdir(overridesDir, { recursive: true });
    for (const [agent, text] of Object.entries(spec.overrides)) {
      await writeFile(path.join(overridesDir, `${agent}.md`), text, "utf8");
    }
  }
}

export function commitAll(workspace, message) {
  git(workspace, ["add", "-A"]);
  git(workspace, ["commit", "-q", "-m", message, "--no-verify"]);
}

export function run(workspace, script, args = [], extraEnv = {}) {
  const outcome = spawnSync(process.execPath, [path.join(workspace.repo, "scripts", script), ...args], {
    encoding: "utf8",
    cwd: workspace.repo,
    env: {
      ...baseEnv(workspace),
      AGENT_SKILLS_HOME: workspace.installRoot,
      AGENT_PROMPTS_HOME: workspace.promptsInstallRoot,
      AGENT_MCP_HOME: workspace.mcpTrackingRoot,
      ...extraEnv,
    },
  });
  return { status: outcome.status, stdout: outcome.stdout ?? "", stderr: outcome.stderr ?? "" };
}
