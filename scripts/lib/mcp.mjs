// Core library for MCP server management, environment validation, and git tracking.

import { createHash } from "node:crypto";
import { chmod, cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";

import {
  expandHome,
  git,
  gitDirtyPaths,
  gitVersion,
  pathStat,
  readJsonFile,
  repositoryRoot,
  writeJsonFileAtomic,
} from "./common.mjs";

export const mcpManifestFileName = "installed.json";
export const mcpDirName = "mcp";
export const mcpTrackingMarkerFileName = ".agent-mcp-tracking";

export function mcpInstallRoot() {
  const override = process.env.AGENT_MCP_HOME;
  if (override && override.trim()) return path.resolve(expandHome(override.trim()));
  return path.join(homedir(), ".agents", "mcp-servers");
}

export function mcpManifestPath(trackingRoot = mcpInstallRoot()) {
  return path.join(trackingRoot, mcpManifestFileName);
}

export async function readMcpManifest(trackingRoot = mcpInstallRoot()) {
  const target = mcpManifestPath(trackingRoot);
  const parsed = await readJsonFile(target, { servers: {} });
  if (!parsed || typeof parsed !== "object" || typeof parsed.servers !== "object" || parsed.servers === null) {
    return { servers: {} };
  }
  return { servers: parsed.servers };
}

export async function writeMcpManifest(manifest, trackingRoot = mcpInstallRoot()) {
  const target = mcpManifestPath(trackingRoot);
  const ordered = {};
  for (const name of Object.keys(manifest.servers).sort()) {
    ordered[name] = manifest.servers[name];
  }
  await writeJsonFileAtomic(target, { servers: ordered });
}

export async function availableMcpServers(root = repositoryRoot) {
  const directory = path.join(root, mcpDirName);
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
    if (await pathStat(path.join(directory, entry.name, "mcp.json"))) {
      found.push(entry.name);
    }
  }
  return found.sort();
}

export async function readMcpDefinition(name, root = repositoryRoot) {
  const file = path.join(root, mcpDirName, name, "mcp.json");
  const data = await readJsonFile(file);
  if (!data || typeof data !== "object") {
    throw new Error(`${file} must contain a JSON object.`);
  }
  if (!data.name || typeof data.name !== "string") {
    data.name = name;
  }
  return data;
}

export async function mcpServerVersion(name, root = repositoryRoot) {
  return gitVersion(path.posix.join(mcpDirName, name), root);
}

export async function mcpServerDirtyPaths(name, root = repositoryRoot) {
  return gitDirtyPaths(path.posix.join(mcpDirName, name), root);
}

export function parseDotenv(content) {
  const result = {};
  if (!content) return result;
  const lines = content.split(/\r?\n/);
  for (let line of lines) {
    let trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    if (/^export\s+/.test(trimmed)) {
      trimmed = trimmed.replace(/^export\s+/, "").trim();
    }
    if (!trimmed || trimmed.startsWith("#")) continue;

    const equalsIndex = trimmed.indexOf("=");
    if (equalsIndex === -1) continue;

    const key = trimmed.slice(0, equalsIndex).trim();
    if (!key) continue;

    const afterEquals = trimmed.slice(equalsIndex + 1).trim();
    let value = "";

    if (afterEquals.startsWith('"') || afterEquals.startsWith("'")) {
      const quote = afterEquals[0];
      let closingIndex = -1;
      let isEscaped = false;
      for (let i = 1; i < afterEquals.length; i++) {
        const ch = afterEquals[i];
        if (ch === "\\" && quote === '"') {
          isEscaped = !isEscaped;
          continue;
        }
        if (ch === quote && !isEscaped) {
          closingIndex = i;
          break;
        }
        isEscaped = false;
      }

      if (closingIndex !== -1) {
        value = afterEquals.slice(1, closingIndex);
      } else {
        value = afterEquals.replace(/\s+#.*$/, "").trim();
      }
    } else if (afterEquals.startsWith("#")) {
      value = "";
    } else {
      value = afterEquals.replace(/\s+#.*$/, "").trim();
    }

    result[key] = value;
  }
  return result;
}

export async function readEnvFileIfExists(filePath) {
  const stat = await pathStat(filePath);
  if (!stat) return {};
  try {
    const content = await readFile(filePath, "utf8");
    return parseDotenv(content);
  } catch {
    return {};
  }
}

function isForbiddenEnvKey(key) {
  return key === "NODE_OPTIONS" || key === "LD_PRELOAD" || key === "PATH" || key.startsWith("DYLD_");
}

export async function resolveAndValidateEnv(
  mcpDef,
  serverDir,
  cliOverrides = {},
  extraEnv = process.env,
) {
  const exampleEnv = await readEnvFileIfExists(path.join(serverDir, ".env.example"));
  const localEnv = await readEnvFileIfExists(path.join(serverDir, ".env"));

  const definedVars = mcpDef.env || {};
  const declaredKeys = Object.keys(definedVars);

  for (const key of declaredKeys) {
    if (isForbiddenEnvKey(key)) {
      throw new Error(
        `Forbidden environment variable "${key}" declared for MCP server "${mcpDef.name}". Loader and search path variables (NODE_OPTIONS, LD_PRELOAD, PATH, DYLD_*) cannot be configured.`,
      );
    }
  }

  for (const key of Object.keys(cliOverrides)) {
    if (isForbiddenEnvKey(key)) {
      throw new Error(
        `Forbidden environment variable "${key}" cannot be configured for MCP server "${mcpDef.name}". Loader and search path variables (NODE_OPTIONS, LD_PRELOAD, PATH, DYLD_*) cannot be configured.`,
      );
    }
    if (!(key in definedVars)) {
      const declaredMsg = declaredKeys.length > 0 ? declaredKeys.join(", ") : "(none)";
      throw new Error(
        `Undeclared environment variable "${key}" passed via --set for MCP server "${mcpDef.name}". Declared variables: ${declaredMsg}.`,
      );
    }
  }

  const resolvedEnv = {};
  const missing = [];

  for (const [varName, spec] of Object.entries(definedVars)) {
    const isRequired = Boolean(spec?.required);
    const defaultVal = spec?.default !== undefined ? String(spec.default) : "";

    // Priority: CLI override > local .env > extraEnv/process.env > .env.example > default
    let value = defaultVal;
    if (exampleEnv[varName] !== undefined && exampleEnv[varName] !== "") {
      value = exampleEnv[varName];
    }
    if (extraEnv[varName] !== undefined && extraEnv[varName] !== "") {
      value = extraEnv[varName];
    }
    if (localEnv[varName] !== undefined && localEnv[varName] !== "") {
      value = localEnv[varName];
    }
    if (cliOverrides[varName] !== undefined && cliOverrides[varName] !== "") {
      value = cliOverrides[varName];
    }

    if (isRequired && (!value || value.trim() === "")) {
      missing.push({
        name: varName,
        description: spec?.description || "No description provided",
      });
    } else if (value && value.trim() !== "") {
      resolvedEnv[varName] = value.trim();
    }
  }

  if (missing.length > 0) {
    const errorLines = missing.map(
      (m) => `  - ${m.name}: ${m.description}`,
    );
    throw new Error(
      `Missing required environment variable(s) for MCP server "${mcpDef.name}":\n${errorLines.join("\n")}\nProvide them via --set KEY=VALUE, a local .env file, or your shell environment.`,
    );
  }

  return resolvedEnv;
}

export function sanitizePathSlug(inputPath) {
  return inputPath
    .replace(/^[\/\\]+/, "")
    .replace(/[\/\\:]+/g, "_")
    .replace(/[^a-zA-Z0-9_\-\.]/g, "");
}

export async function ensureGitTrackingRepo(trackingRoot) {
  await mkdir(trackingRoot, { recursive: true, mode: 0o700 });
  const gitDir = path.join(trackingRoot, ".git");
  const gitStat = await pathStat(gitDir);
  const markerPath = path.join(trackingRoot, mcpTrackingMarkerFileName);
  const markerStat = await pathStat(markerPath);

  if (gitStat) {
    if (!markerStat) {
      throw new Error(
        `Refusing to operate on git repository at "${trackingRoot}": missing marker file "${mcpTrackingMarkerFileName}".`,
      );
    }
  } else {
    try {
      await git(["init", "-q", "-b", "main"], trackingRoot);
    } catch (error) {
      if (error.message && (error.message.includes("ENOENT") || error.code === "ENOENT")) {
        throw new Error(`Git is a required prerequisite for MCP tracking repository operations, but git was not found in PATH.`);
      }
      throw error;
    }
    await writeFile(markerPath, "managed by agent-skills mcp\n", { encoding: "utf8", mode: 0o600 });
  }

  const gitignorePath = path.join(trackingRoot, ".gitignore");
  const gitignoreStat = await pathStat(gitignorePath);
  if (!gitignoreStat) {
    await writeFile(gitignorePath, "backups/\n", { encoding: "utf8", mode: 0o600 });
  } else {
    const existing = await readFile(gitignorePath, "utf8");
    const lines = existing.split("\n").map((l) => l.trim());
    if (!lines.includes("backups") && !lines.includes("backups/")) {
      await writeFile(gitignorePath, `${existing.trimEnd()}\nbackups/\n`, { encoding: "utf8", mode: 0o600 });
    }
  }
}

// Tension: A redacted backup cannot be used to restore credentials verbatim.
// This is intended — the backup exists to recover structure, and the user's own
// agent config is the source of truth for secrets.
function redactJsonValue(value, keyName = "") {
  if (value === null || value === undefined) return value;

  if (typeof value === "string") {
    const lowerKey = keyName.toLowerCase();
    if (
      [
        "accesstoken",
        "refreshtoken",
        "primaryapikey",
        "apikey",
        "authtoken",
        "apitoken",
        "bearertoken",
        "bearer_token",
        "token",
        "secret",
        "password",
      ].includes(lowerKey)
    ) {
      return "[redacted]";
    }
    let redacted = value;
    redacted = redacted.replace(/Bearer\s+[A-Za-z0-9_\-\.~+/]+=*/gi, "Bearer [redacted]");
    redacted = redacted.replace(/sk[-_][A-Za-z0-9_\-\.]{4,}/gi, "[redacted]");
    redacted = redacted.replace(/ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/gi, "[redacted]");
    return redacted;
  }

  if (Array.isArray(value)) {
    return value.map((item) => redactJsonValue(item, keyName));
  }

  if (typeof value === "object") {
    const result = {};
    for (const [k, v] of Object.entries(value)) {
      const lowerK = k.toLowerCase();
      if (k === "mcpServers" && v && typeof v === "object" && !Array.isArray(v)) {
        result[k] = {};
        for (const [srvName, srvObj] of Object.entries(v)) {
          if (srvObj && typeof srvObj === "object" && !Array.isArray(srvObj)) {
            const srvCopy = { ...srvObj };
            if (srvCopy.env && typeof srvCopy.env === "object" && !Array.isArray(srvCopy.env)) {
              srvCopy.env = {};
              for (const envKey of Object.keys(srvObj.env)) {
                srvCopy.env[envKey] = "[redacted]";
              }
            }
            if (srvCopy.headers && typeof srvCopy.headers === "object" && !Array.isArray(srvCopy.headers)) {
              srvCopy.headers = {};
              for (const [hKey, hVal] of Object.entries(srvObj.headers)) {
                const lowerHKey = hKey.toLowerCase();
                if (
                  lowerHKey.includes("auth") ||
                  lowerHKey.includes("token") ||
                  lowerHKey.includes("key") ||
                  lowerHKey.includes("secret") ||
                  lowerHKey.includes("cookie") ||
                  (typeof hVal === "string" && (/^bearer\s+/i.test(hVal) || /sk[-_]/i.test(hVal)))
                ) {
                  srvCopy.headers[hKey] = "[redacted]";
                } else {
                  srvCopy.headers[hKey] = redactJsonValue(hVal, hKey);
                }
              }
            }
            for (const [propKey, propVal] of Object.entries(srvCopy)) {
              if (propKey !== "env" && propKey !== "headers") {
                srvCopy[propKey] = redactJsonValue(propVal, propKey);
              }
            }
            result[k][srvName] = srvCopy;
          } else {
            result[k][srvName] = redactJsonValue(srvObj, srvName);
          }
        }
        continue;
      }

      if (
        [
          "accesstoken",
          "refreshtoken",
          "primaryapikey",
          "apikey",
          "authtoken",
          "apitoken",
          "bearertoken",
          "bearer_token",
          "token",
          "secret",
          "password",
        ].includes(lowerK)
      ) {
        result[k] = "[redacted]";
      } else {
        result[k] = redactJsonValue(v, k);
      }
    }
    return result;
  }

  return value;
}

function redactTomlOrText(content) {
  const lines = content.split("\n");
  let inMcpEnvSection = false;
  const resultLines = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
      const header = trimmed.slice(1, -1).trim();
      inMcpEnvSection = /^mcp_servers(\.[^\]]+)?\.env$/i.test(header);
      resultLines.push(line);
      continue;
    }

    if (inMcpEnvSection) {
      if (trimmed && !trimmed.startsWith("#")) {
        const eqIdx = line.indexOf("=");
        if (eqIdx !== -1) {
          const keyPart = line.slice(0, eqIdx);
          resultLines.push(`${keyPart}= "[redacted]"`);
          continue;
        }
      }
    }

    const eqIdx = line.indexOf("=");
    if (eqIdx !== -1 && !trimmed.startsWith("#")) {
      const keyName = line.slice(0, eqIdx).trim().replace(/^["']|["']$/g, "").toLowerCase();
      if (
        [
          "accesstoken",
          "refreshtoken",
          "primaryapikey",
          "apikey",
          "authtoken",
          "apitoken",
          "bearertoken",
          "bearer_token",
          "token",
          "secret",
          "password",
        ].includes(keyName)
      ) {
        const keyPart = line.slice(0, eqIdx);
        resultLines.push(`${keyPart}= "[redacted]"`);
        continue;
      }
    }

    let modified = line;
    modified = modified.replace(/Bearer\s+[A-Za-z0-9_\-\.~+/]+=*/gi, "Bearer [redacted]");
    modified = modified.replace(/sk[-_][A-Za-z0-9_\-\.]{4,}/gi, "[redacted]");
    modified = modified.replace(/ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/gi, "[redacted]");
    resultLines.push(modified);
  }

  return resultLines.join("\n");
}

export function redactConfigContent(rawContent, filePath = "") {
  if (!rawContent || !rawContent.trim()) return rawContent;
  try {
    const parsed = JSON.parse(rawContent);
    const redacted = redactJsonValue(parsed);
    return `${JSON.stringify(redacted, undefined, 2)}\n`;
  } catch {
    return redactTomlOrText(rawContent);
  }
}

export async function backupConfigFile(trackingRoot, agentName, scope, projectRoot, sourceFilePath) {
  const backupsDir = path.join(trackingRoot, "backups");
  await mkdir(backupsDir, { recursive: true, mode: 0o700 });

  const resolvedSourcePath = path.resolve(sourceFilePath);
  const pathHash = createHash("sha256").update(resolvedSourcePath).digest("hex").slice(0, 8);
  const scopePrefix = scope === "global" ? "global" : `project_${sanitizePathSlug(projectRoot || "")}`;
  const baseName = path.basename(sourceFilePath);

  const originBackupFileName = `${agentName}_${scopePrefix}_${pathHash}_${baseName}.origin`;
  const latestBackupFileName = `${agentName}_${scopePrefix}_${pathHash}_${baseName}.latest`;
  const originBackupPath = path.join(backupsDir, originBackupFileName);
  const latestBackupPath = path.join(backupsDir, latestBackupFileName);

  const stat = await pathStat(sourceFilePath);
  if (stat) {
    const rawContent = await readFile(sourceFilePath, "utf8");
    const redacted = redactConfigContent(rawContent, sourceFilePath);

    const originStat = await pathStat(originBackupPath);
    if (!originStat) {
      await writeFile(originBackupPath, redacted, { encoding: "utf8", mode: 0o600 });
      await chmod(originBackupPath, 0o600);
    }

    await writeFile(latestBackupPath, redacted, { encoding: "utf8", mode: 0o600 });
    await chmod(latestBackupPath, 0o600);
  } else {
    await rm(latestBackupPath, { force: true });
  }

  return latestBackupPath;
}

export async function commitMcpTracking(trackingRoot, commitMessage) {
  try {
    await git(["add", mcpManifestFileName], trackingRoot);
    const status = await git(["status", "--porcelain", "--", mcpManifestFileName], trackingRoot);
    if (status.trim()) {
      await git(
        [
          "-c",
          "user.name=Agent MCP Manager",
          "-c",
          "user.email=mcp@agents.local",
          "commit",
          "-q",
          "-m",
          commitMessage,
          "--no-verify",
        ],
        trackingRoot,
        {
          GIT_AUTHOR_NAME: "Agent MCP Manager",
          GIT_AUTHOR_EMAIL: "mcp@agents.local",
          GIT_COMMITTER_NAME: "Agent MCP Manager",
          GIT_COMMITTER_EMAIL: "mcp@agents.local",
        },
      );
    }
  } catch (error) {
    if (error.message && (error.message.includes("ENOENT") || error.code === "ENOENT")) {
      throw new Error(`Git is a required prerequisite for MCP tracking repository operations, but git was not found in PATH.`);
    }
    throw error;
  }
}

export function isServerManaged(manifest, serverName, agentName, scope, projectRoot) {
  const serverEntry = manifest?.servers?.[serverName];
  if (!serverEntry || !Array.isArray(serverEntry.installations)) return false;
  return serverEntry.installations.some((inst) => {
    if (inst.agent !== agentName || inst.scope !== scope) return false;
    if (scope === "project" && inst.projectRoot !== projectRoot) return false;
    return true;
  });
}

export function recordInstallation(manifest, serverName, version, agentName, scope, projectRoot, targetFile) {
  manifest.servers = manifest.servers || {};
  manifest.servers[serverName] = manifest.servers[serverName] || {
    version,
    installedAt: new Date().toISOString(),
    source: repositoryRoot,
    installations: [],
  };

  manifest.servers[serverName].version = version;
  manifest.servers[serverName].installedAt = new Date().toISOString();
  manifest.servers[serverName].source = repositoryRoot;

  // Filter out any existing matching installation record
  manifest.servers[serverName].installations = manifest.servers[serverName].installations.filter(
    (inst) => !(inst.agent === agentName && inst.scope === scope && (scope !== "project" || inst.projectRoot === projectRoot)),
  );

  manifest.servers[serverName].installations.push({
    agent: agentName,
    scope,
    ...(scope === "project" ? { projectRoot } : {}),
    targetFile,
  });
}

export function removeInstallationRecord(manifest, serverName, agentName, scope, projectRoot) {
  if (!manifest?.servers?.[serverName]) return;
  manifest.servers[serverName].installations = (manifest.servers[serverName].installations || []).filter(
    (inst) => !(inst.agent === agentName && inst.scope === scope && (scope !== "project" || inst.projectRoot === projectRoot)),
  );
  if (manifest.servers[serverName].installations.length === 0) {
    delete manifest.servers[serverName];
  }
}
