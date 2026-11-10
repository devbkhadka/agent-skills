// Shared core for system prompt installation, status, backup, and compose.
//
// Layout assumptions:
//   <repo root>/system-prompts/<name>/system-prompt.md    managed prompt source
//   <install root>/<name>/system-prompt.md               installed copy (staging)
//   <install root>/active/<agent>-composed.md            composed prompt (base + domain)
//   <install root>/active/codex-instructions.md          codex instructions link
//   <install root>/backups/<agent>/<file>.<ts>.bak       first-time backups
//   <install root>/installed.json                        installed manifest

import { chmod, cp, mkdir, readFile, readdir, readlink, realpath, rm, symlink } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";

import {
  agents as commonAgents,
  agentAliases as commonAgentAliases,
  expandHome,
  gitDirtyPaths,
  gitVersion,
  parseAgents,
  pathStat,
  readJsonFile,
  repositoryRoot,
  writeJsonFileAtomic,
  writeTextFileAtomic,
} from "./common.mjs";

export {
  expandHome,
  parseAgents,
  pathStat,
  repositoryRoot,
};

export const promptsRoot = path.join(repositoryRoot, "system-prompts");
export const manifestFileName = "installed.json";

export const promptAgents = {
  claude: {
    label: "Claude",
    global: [".claude", "CLAUDE.md"],
    project: ["CLAUDE.md"],
  },
  gemini: {
    label: "Gemini / AGY",
    global: [".gemini", "GEMINI.md"],
    project: ["GEMINI.md"],
  },
  codex: {
    label: "Codex",
    globalConfig: [".codex", "config.toml"],
    project: ["AGENTS.md"],
  },
  omp: {
    label: "OMP",
    global: [".omp", "AGENTS.md"],
    project: ["AGENTS.md"],
  },
};

export const agentAliases = commonAgentAliases;

/** Local install root; overridable with AGENT_PROMPTS_HOME so tests never touch real home. */
export function installRoot() {
  const override = process.env.AGENT_PROMPTS_HOME;
  if (override && override.trim()) return path.resolve(expandHome(override.trim()));
  return path.join(homedir(), ".agents", "system-prompts");
}

export function backupRoot(root = installRoot()) {
  return path.join(root, "backups");
}

export function activeRoot(root = installRoot()) {
  return path.join(root, "active");
}

export function manifestPath(root = installRoot()) {
  return path.join(root, manifestFileName);
}

export async function readManifest(root = installRoot()) {
  const target = manifestPath(root);
  const parsed = await readJsonFile(target, { prompts: {} });
  if (!parsed || typeof parsed !== "object" || typeof parsed.prompts !== "object" || parsed.prompts === null) {
    return { prompts: {} };
  }
  return { prompts: parsed.prompts };
}

export async function writeManifest(manifest, root = installRoot()) {
  const target = manifestPath(root);
  const ordered = {};
  for (const name of Object.keys(manifest.prompts).sort()) ordered[name] = manifest.prompts[name];
  await writeJsonFileAtomic(target, { prompts: ordered });
}

export async function availablePrompts(root = repositoryRoot) {
  const directory = path.join(root, "system-prompts");
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
  const found = [];
  for (const entry of entries) {
    if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
    if (await pathStat(path.join(directory, entry.name, "system-prompt.md"))) found.push(entry.name);
  }
  return found.sort();
}

export async function promptExists(name, root = repositoryRoot) {
  return Boolean(await pathStat(path.join(root, "system-prompts", name, "system-prompt.md")));
}

export async function promptVersion(name, root = repositoryRoot) {
  return gitVersion(path.posix.join("system-prompts", name), root);
}

export async function promptDirtyPaths(name, root = repositoryRoot) {
  return gitDirtyPaths(path.posix.join("system-prompts", name), root);
}

// Locking read-only
const lockedFileMask = ~0o222;
const lockedDirectoryMode = 0o555;
const unlockedDirectoryMode = 0o755;

async function walkTree(target, visit, options = {}) {
  const stat = await pathStat(target);
  if (!stat) return;
  if (stat.isSymbolicLink()) return;
  if (!stat.isDirectory()) {
    await visit(target, stat);
    return;
  }
  if (options.parentFirst) await visit(target, stat);
  for (const entry of await readdir(target)) {
    await walkTree(path.join(target, entry), visit, options);
  }
  if (!options.parentFirst) await visit(target, stat);
}

export async function setPromptWritable(target, writable) {
  await walkTree(
    target,
    async (entry, stat) => {
      if (stat.isDirectory()) {
        await chmod(entry, writable ? unlockedDirectoryMode : lockedDirectoryMode);
        return;
      }
      const mode = stat.mode & 0o777;
      await chmod(entry, writable ? mode | 0o200 : mode & lockedFileMask);
    },
    { parentFirst: writable },
  );
}

export async function copyPrompt(name, root = installRoot(), source = path.join(repositoryRoot, "system-prompts", name)) {
  const destination = path.join(root, name);
  await mkdir(root, { recursive: true });
  await setPromptWritable(destination, true);
  await rm(destination, { recursive: true, force: true });
  await cp(source, destination, { recursive: true, verbatimSymlinks: true });
  return destination;
}

async function resolvePath(target) {
  try {
    return await realpath(target);
  } catch {
    return path.resolve(target);
  }
}

/** Backup a real target file before overwriting/symlinking. */
export async function backupTargetFile(targetPath, agentName, root = installRoot()) {
  const stat = await pathStat(targetPath);
  if (!stat || stat.isSymbolicLink()) {
    return { backedUp: false, reason: stat?.isSymbolicLink() ? "symlink" : "not_found" };
  }

  const agentBackupDir = path.join(backupRoot(root), agentName);
  await mkdir(agentBackupDir, { recursive: true });

  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const fileName = path.basename(targetPath);
  const backupPath = path.join(agentBackupDir, `${fileName}.${ts}.bak`);
  await cp(targetPath, backupPath);

  // In-place original snapshot if not already saved
  const origPath = `${targetPath}.orig`;
  const origStat = await pathStat(origPath);
  if (!origStat) {
    await cp(targetPath, origPath);
  }

  return { backedUp: true, backupPath, origPath };
}

/** Link helper that safely replaces or throws if regular file and !force. */
export async function linkFile(source, destination, force) {
  const existing = await pathStat(destination);
  if (existing?.isSymbolicLink()) {
    const linked = await readlink(destination);
    const resolved = path.resolve(path.dirname(destination), linked);
    if (resolved === source || (await resolvePath(resolved)) === (await resolvePath(source))) {
      return "already linked";
    }
    await rm(destination, { force: true });
    await mkdir(path.dirname(destination), { recursive: true });
    await symlink(source, destination);
    return "relinked";
  }
  if (existing) {
    if (!force) {
      throw new Error(`${destination} is an existing regular file; remove it or re-run with --force.`);
    }
    await rm(destination, { force: true });
  }
  await mkdir(path.dirname(destination), { recursive: true });
  await symlink(source, destination);
  return "linked";
}

/** Update or insert model_instructions_file in Codex config.toml without altering other sections. */
export async function updateCodexConfigInstructions(configPath, instructionsPath) {
  let content = "";
  try {
    content = await readFile(configPath, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  const keyPattern = /^\s*model_instructions_file\s*=\s*.*$/m;
  const newLine = `model_instructions_file = ${JSON.stringify(instructionsPath)}`;

  let updated;
  if (keyPattern.test(content)) {
    updated = content.replace(keyPattern, newLine);
  } else {
    // Insert at top or before the first [table]
    const firstTableMatch = content.search(/^\s*\[/m);
    if (firstTableMatch === -1) {
      updated = content.trim() ? `${content.trimEnd()}\n${newLine}\n` : `${newLine}\n`;
    } else {
      const before = content.slice(0, firstTableMatch);
      const after = content.slice(firstTableMatch);
      updated = `${before.trimEnd()}\n${newLine}\n\n${after}`;
    }
  }

  await mkdir(path.dirname(configPath), { recursive: true });
  await writeTextFileAtomic(configPath, updated);
}

/** Read compile-time override for an agent if present under <promptDir>/overrides/<agent>.md. */
export async function readAgentOverride(promptDir, agentName) {
  const candidate = path.join(promptDir, "overrides", `${agentName}.md`);
  try {
    const content = await readFile(candidate, "utf8");
    return content.trim();
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return "";
  }
}

/**
 * Install a prompt for an agent.
 * Handles backup on first install, compile-time overrides, mode (compose vs replace), and Codex config.
 */
export async function installPromptForAgent({
  agentName,
  promptName,
  scope,
  projectRoot,
  mode = "compose",
  force = false,
  root = installRoot(),
}) {
  const agent = promptAgents[agentName];
  if (!agent) throw new Error(`Unknown agent: ${agentName}`);

  const stagedPrompt = path.join(root, promptName, "system-prompt.md");
  const promptContent = await readFile(stagedPrompt, "utf8");
  const promptDir = path.dirname(stagedPrompt);
  const override = await readAgentOverride(promptDir, agentName);

  if (agentName === "codex" && scope === "global") {
    const configPath = path.join(homedir(), ...agent.globalConfig);
    const backupRes = await backupTargetFile(configPath, "codex", root);

    const activeDir = activeRoot(root);
    await mkdir(activeDir, { recursive: true });

    let sourceToLink = stagedPrompt;
    if (override) {
      const compiledFile = path.join(activeDir, `codex-${promptName}.md`);
      const compiledContent = `${promptContent.trim()}\n\n---\n\n${override}\n`;
      await writeTextFileAtomic(compiledFile, compiledContent);
      sourceToLink = compiledFile;
    }

    const activeTarget = path.join(activeDir, "codex-instructions.md");
    const linkRes = await linkFile(sourceToLink, activeTarget, true);
    await updateCodexConfigInstructions(configPath, activeTarget);

    return {
      agent: agent.label,
      target: configPath,
      action: `configured model_instructions_file -> ${activeTarget} (${linkRes}${override ? " with compile-time overlay" : ""})`,
      backup: backupRes.backedUp ? backupRes.backupPath : null,
    };
  }

  // File-based agent targets (claude, gemini, omp, codex project-scope)
  const destPath = scope === "global"
    ? path.join(homedir(), ...agent.global)
    : path.join(projectRoot, ...agent.project);

  const backupRes = await backupTargetFile(destPath, agentName, root);

  const chunks = [];
  if (scope === "global" && mode === "compose") {
    let basePersona = "";
    if (backupRes.backedUp && backupRes.origPath) {
      try {
        basePersona = await readFile(backupRes.origPath, "utf8");
      } catch {
        // ignore
      }
    } else {
      const origCandidate = `${destPath}.orig`;
      const origStat = await pathStat(origCandidate);
      if (origStat && !origStat.isSymbolicLink()) {
        try {
          basePersona = await readFile(origCandidate, "utf8");
        } catch {
          // ignore
        }
      }
    }

    if (basePersona.trim()) {
      chunks.push(basePersona.trim());
    }
  }

  chunks.push(promptContent.trim());
  if (override) {
    chunks.push(override);
  }

  let sourceToLink = stagedPrompt;
  if (chunks.length > 1 || override) {
    const activeDir = activeRoot(root);
    await mkdir(activeDir, { recursive: true });
    const qualifier = scope === "global" ? agentName : `${agentName}-${path.basename(projectRoot)}`;
    const compiledFile = path.join(activeDir, `${qualifier}-${promptName}.md`);
    await writeTextFileAtomic(compiledFile, `${chunks.join("\n\n---\n\n")}\n`);
    sourceToLink = compiledFile;
  }

  const result = await linkFile(sourceToLink, destPath, force || backupRes.backedUp);

  return {
    agent: agent.label,
    target: destPath,
    action: `${result} -> ${sourceToLink}${override ? " (with compile-time overlay)" : ""}`,
    backup: backupRes.backedUp ? backupRes.backupPath : null,
  };
}
