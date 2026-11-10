// Shared core for the skill install/status commands.
//
// Layout assumptions:
//   <repository root>/skills/<name>/SKILL.md          managed skill source
//   <repository root>/skills/<name>/dependencies.json optional sibling dependencies
//   <install root>/<name>                             installed copy (symlink target)
//   <install root>/installed.json                     installed-state manifest

import { chmod, cp, mkdir, readFile, readdir, readlink, realpath, rm, symlink } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";

import {
  agents as commonAgents,
  agentAliases as commonAgentAliases,
  expandHome,
  git,
  gitDirtyPaths,
  gitVersion,
  parseAgents,
  pathStat,
  readJsonFile,
  repositoryRoot,
  writeJsonFileAtomic,
} from "./common.mjs";

export {
  expandHome,
  parseAgents,
  pathStat,
  repositoryRoot,
};

export const skillsRoot = path.join(repositoryRoot, "skills");
export const manifestFileName = "installed.json";
export const dependenciesFileName = "dependencies.json";

export const agents = {
  gemini: {
    label: "Gemini / AGY",
    global: commonAgents.gemini.globalSkills,
    project: commonAgents.gemini.projectSkills,
  },
  claude: {
    label: "Claude",
    global: commonAgents.claude.globalSkills,
    project: commonAgents.claude.projectSkills,
  },
  codex: {
    label: "Codex",
    global: commonAgents.codex.globalSkills,
    project: commonAgents.codex.projectSkills,
  },
  omp: {
    label: "OMP",
    global: commonAgents.omp.globalSkills,
    project: commonAgents.omp.projectSkills,
  },
};

export const agentAliases = commonAgentAliases;

/** Local install root; overridable with AGENT_SKILLS_HOME so tests never touch the real home. */
export function installRoot() {
  const override = process.env.AGENT_SKILLS_HOME;
  if (override && override.trim()) return path.resolve(expandHome(override.trim()));
  return path.join(homedir(), ".agents", "skills");
}

export function agentDirectory(agentName, scope, projectRoot) {
  const agent = agents[agentName];
  if (!agent) throw new Error(`Unknown agent: ${agentName}`);
  if (scope === "global") return path.join(homedir(), ...agent.global);
  return path.join(projectRoot, ...agent.project);
}

export async function availableSkills(root = repositoryRoot) {
  const directory = path.join(root, "skills");
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
    if (await pathStat(path.join(directory, entry.name, "SKILL.md"))) found.push(entry.name);
  }
  return found.sort();
}

export async function skillExists(name, root = repositoryRoot) {
  return Boolean(await pathStat(path.join(root, "skills", name, "SKILL.md")));
}

/** First 12 characters of the most recent commit touching skills/<name>. */
export async function skillVersion(name, root = repositoryRoot) {
  return gitVersion(path.posix.join("skills", name), root);
}

/** Working-tree changes (including untracked files) under skills/<name>. */
export async function skillDirtyPaths(name, root = repositoryRoot) {
  return gitDirtyPaths(path.posix.join("skills", name), root);
}

export async function readDependencies(name, root = repositoryRoot) {
  const file = path.join(root, "skills", name, dependenciesFileName);
  let parsed;
  try {
    parsed = await readJsonFile(file, undefined);
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
  if (parsed === undefined) return [];
  const dependencies = parsed?.dependencies ?? [];
  if (!Array.isArray(dependencies) || dependencies.some((entry) => typeof entry !== "string" || !entry.trim())) {
    throw new Error(`${file} must contain a "dependencies" array of skill names.`);
  }
  return dependencies.map((entry) => entry.trim());
}

/**
 * Transitive dependency closure, dependencies before dependents.
 * Throws on unknown names and on cycles.
 */
export async function resolveClosure(requested, root = repositoryRoot) {
  const order = [];
  const state = new Map();
  const stack = [];

  async function visit(name, requestedBy) {
    if (!(await skillExists(name, root))) {
      throw requestedBy
        ? new Error(`Skill "${requestedBy}" depends on "${name}", which does not exist under skills/.`)
        : new Error(`Unknown skill: ${name}`);
    }
    const seen = state.get(name);
    if (seen === "done") return;
    if (seen === "visiting") {
      const start = stack.indexOf(name);
      throw new Error(`Dependency cycle detected: ${[...stack.slice(start), name].join(" -> ")}`);
    }
    state.set(name, "visiting");
    stack.push(name);
    for (const dependency of await readDependencies(name, root)) await visit(dependency, name);
    stack.pop();
    state.set(name, "done");
    order.push(name);
  }

  for (const name of requested) await visit(name, undefined);
  return order;
}

export function manifestPath(root = installRoot()) {
  return path.join(root, manifestFileName);
}

export async function readManifest(root = installRoot()) {
  const target = manifestPath(root);
  const parsed = await readJsonFile(target, { skills: {} });
  if (!parsed || typeof parsed !== "object" || typeof parsed.skills !== "object" || parsed.skills === null) {
    return { skills: {} };
  }
  return { skills: parsed.skills };
}

/** Write the manifest atomically. */
export async function writeManifest(manifest, root = installRoot()) {
  const target = manifestPath(root);
  const ordered = {};
  for (const name of Object.keys(manifest.skills).sort()) ordered[name] = manifest.skills[name];
  await writeJsonFileAtomic(target, { skills: ordered });
}

// Installed skills are locked read-only so an agent editing "the skill" edits the
// repository, not the copy it is running. The installer unlocks before it replaces
// a copy, so a reinstall is never blocked by the lock it applied last time.
const lockedFileMask = ~0o222;
const lockedDirectoryMode = 0o555;
const unlockedDirectoryMode = 0o755;

/** Depth-first walk of a directory tree; symlinks are listed but never followed. */
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

/**
 * Lock (`writable === false`) or unlock an installed skill tree in place.
 * Locking strips every write bit and leaves read/execute alone; unlocking restores
 * owner write. Symlinks are skipped — chmod would follow them out of the tree.
 */
export async function setSkillWritable(target, writable) {
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

/** Replace <install root>/<name> with a fresh copy of the repository skill. */
export async function copySkill(name, root = installRoot(), source = path.join(repositoryRoot, "skills", name)) {
  const destination = path.join(root, name);
  await mkdir(root, { recursive: true });
  // A previous install left the tree read-only; rm cannot unlink through a
  // locked directory, so unlock it before replacing it.
  await setSkillWritable(destination, true);
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

/**
 * Symlink an installed skill into an agent directory.
 * Returns "already linked" | "linked" | "relinked".
 * A symlink pointing elsewhere is replaced; a real file or directory needs `force`.
 */
export async function linkSkill(source, destination, force) {
  const existing = await pathStat(destination);
  if (existing?.isSymbolicLink()) {
    const linked = await readlink(destination);
    const resolved = path.resolve(path.dirname(destination), linked);
    if (resolved === source || (await resolvePath(resolved)) === (await resolvePath(source))) {
      return "already linked";
    }
    await rm(destination, { recursive: true, force: true });
    await mkdir(path.dirname(destination), { recursive: true });
    await symlink(source, destination, "dir");
    return "relinked";
  }
  if (existing) {
    const kind = existing.isDirectory() ? "directory" : "file";
    if (!force) {
      throw new Error(`${destination} is an existing ${kind} (not a symlink); remove it or re-run with --force.`);
    }
    await rm(destination, { recursive: true, force: true });
  }
  await mkdir(path.dirname(destination), { recursive: true });
  await symlink(source, destination, "dir");
  return "linked";
}
