// Shared utilities for skills and MCP scripts.

import { execFile } from "node:child_process";
import { chmod, lstat, mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Canonical agent targets. `project` is appended to the project root. */
export const agents = {
  gemini: {
    label: "Gemini / AGY",
    globalSkills: [".gemini", "skills"],
    projectSkills: [".gemini", "skills"],
  },
  claude: {
    label: "Claude",
    globalSkills: [".claude", "skills"],
    projectSkills: [".claude", "skills"],
  },
  codex: {
    label: "Codex",
    globalSkills: [".codex", "skills"],
    projectSkills: [".codex", "skills"],
  },
  omp: {
    label: "OMP",
    globalSkills: [".omp", "agent", "skills"],
    projectSkills: [".omp", "skills"],
  },
};

/** Input names that resolve to a canonical agent key. AGY reuses the Gemini directory. */
export const agentAliases = {
  agy: "gemini",
};

export function expandHome(input) {
  if (input === "~") return homedir();
  if (input.startsWith(`~${path.sep}`)) return path.join(homedir(), input.slice(2));
  return input;
}

export function parseAgents(value) {
  if (value.trim().toLowerCase() === "all") return Object.keys(agents);
  const requested = value
    .split(",")
    .map((name) => name.trim().toLowerCase())
    .filter(Boolean);
  const invalid = requested.filter((name) => !agents[name] && !agentAliases[name]);
  if (!requested.length || invalid.length) {
    throw new Error(`Unknown agent(s): ${invalid.join(", ") || value}`);
  }
  // Aliases collapse onto their canonical target, so agy + gemini link once.
  return [...new Set(requested.map((name) => agentAliases[name] ?? name))];
}

/** Canonical MCP agent targets. */
export const mcpAgents = {
  gemini: {
    label: "Gemini",
  },
  agy: {
    label: "AGY",
  },
  claude: {
    label: "Claude",
  },
  codex: {
    label: "Codex",
  },
  omp: {
    label: "OMP",
  },
};

export function parseMcpAgents(value) {
  if (value.trim().toLowerCase() === "all") return Object.keys(mcpAgents);
  const requested = value
    .split(",")
    .map((name) => name.trim().toLowerCase())
    .filter(Boolean);
  const invalid = requested.filter((name) => !mcpAgents[name]);
  if (!requested.length || invalid.length) {
    throw new Error(`Unknown agent(s): ${invalid.join(", ") || value}`);
  }
  return [...new Set(requested)];
}

export async function pathStat(target) {
  try {
    return await lstat(target);
  } catch (error) {
    if (error.code === "ENOENT") return undefined;
    throw error;
  }
}

export async function git(args, cwd, extraEnv = {}) {
  try {
    const { stdout } = await execFileAsync("git", ["-c", "safe.directory=*", ...args], {
      cwd,
      encoding: "utf8",
      maxBuffer: 32 * 1024 * 1024,
      env: { ...process.env, ...extraEnv },
    });
    return stdout;
  } catch (error) {
    throw new Error(`git ${args.join(" ")} failed: ${(error.stderr || error.message).toString().trim()}`);
  }
}

/** First 12 characters of the most recent commit touching subpath. */
export async function gitVersion(subPath, root = repositoryRoot) {
  const stdout = await git(["log", "-1", "--format=%H", "--", subPath], root);
  const hash = stdout.trim();
  if (!hash) {
    throw new Error(`${subPath} has never been committed; commit it before installing (no version available).`);
  }
  return hash.slice(0, 12);
}

/** Working-tree changes (including untracked files) under subpath. */
export async function gitDirtyPaths(subPath, root = repositoryRoot) {
  const stdout = await git(["status", "--porcelain", "--", subPath], root);
  return stdout.split("\n").map((line) => line.trim()).filter(Boolean);
}

export async function readJsonFile(filePath, defaultValue = undefined) {
  let raw;
  try {
    raw = await readFile(filePath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") {
      if (defaultValue !== undefined) return defaultValue;
      throw error;
    }
    throw error;
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    const parseError = new Error(`${filePath} is not valid JSON`);
    parseError.cause = error;
    throw parseError;
  }
}

// Every file these writers touch can hold an API key or an OAuth token, so the default is
// owner-only. An existing mode is kept only when it is already at least as strict as 0600,
// which preserves a deliberate 0400 without ever widening a file back to 0644.
//
// This is the single writer for both JSON and text configs on purpose: when the mode policy
// lived only in the JSON writer, agent configs went through a separate copy that set no mode
// and silently shipped credentials at 0644.
export async function writeTextFileAtomic(filePath, text) {
  const dir = path.dirname(filePath);
  await mkdir(dir, { recursive: true, mode: 0o700 });

  let targetMode = 0o600;
  const existingStat = await pathStat(filePath);
  if (existingStat) {
    const existingMode = existingStat.mode & 0o777;
    if ((existingMode & ~0o600) === 0) {
      targetMode = existingMode;
    }
  }

  const tempFile = `${filePath}.tmp-${process.pid}-${Date.now()}`;
  try {
    // Mode is set on the temp file before the rename, so the final path is never briefly
    // readable at the default umask.
    await writeFile(tempFile, text.endsWith("\n") ? text : `${text}\n`, {
      encoding: "utf8",
      mode: targetMode,
    });
    await chmod(tempFile, targetMode);
    await rename(tempFile, filePath);
  } catch (error) {
    try {
      await unlink(tempFile);
    } catch {
      // Ignore cleanup error if temp file does not exist
    }
    throw error;
  }
}

export async function writeJsonFileAtomic(filePath, data) {
  await writeTextFileAtomic(filePath, JSON.stringify(data, undefined, 2));
}
