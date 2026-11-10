// Agent MCP configuration file paths and serializers.

import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";

import { expandHome, pathStat, writeTextFileAtomic } from "./common.mjs";

const ADAPTER_SYM = Symbol.for("mcp_agent_adapter");
const FILE_PATH_SYM = Symbol.for("mcp_file_path");
const SECTIONS_SYM = Symbol.for("mcp_toml_sections");

function parseTomlValue(valStr) {
  const trimmed = valStr.trim();
  if (trimmed.startsWith('"') && trimmed.endsWith('"')) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return trimmed.slice(1, -1);
    }
  }
  if (trimmed.startsWith("'") && trimmed.endsWith("'")) {
    return trimmed.slice(1, -1);
  }
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    try {
      return JSON.parse(trimmed);
    } catch {
      const inner = trimmed.slice(1, -1).trim();
      if (!inner) return [];
      return inner.split(",").map((s) => parseTomlValue(s.trim()));
    }
  }
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  return trimmed;
}

function formatTomlValue(val) {
  if (typeof val === "string") {
    return JSON.stringify(val);
  }
  if (typeof val === "boolean") {
    return val ? "true" : "false";
  }
  if (typeof val === "number") {
    return String(val);
  }
  if (Array.isArray(val)) {
    return `[${val.map(formatTomlValue).join(", ")}]`;
  }
  if (val && typeof val === "object") {
    return JSON.stringify(val);
  }
  return JSON.stringify(String(val));
}

function formatTomlKey(key) {
  if (/^[a-zA-Z0-9_-]+$/.test(key)) {
    return key;
  }
  return JSON.stringify(key);
}

function parseTableHeader(line) {
  const trimmed = line.trim();
  const match = trimmed.match(/^\[\s*([^\]]+)\s*\]$/);
  if (!match) return null;
  const fullHeader = match[1].trim();

  if (fullHeader === "mcp_servers" || fullHeader.startsWith("mcp_servers.")) {
    const rest = fullHeader.slice("mcp_servers.".length);
    let serverName = "";
    let subTable = null;

    if (rest.startsWith('"')) {
      const closingQuote = rest.indexOf('"', 1);
      if (closingQuote !== -1) {
        try {
          serverName = JSON.parse(rest.slice(0, closingQuote + 1));
        } catch {
          serverName = rest.slice(1, closingQuote);
        }
        const after = rest.slice(closingQuote + 1).trim();
        if (after.startsWith(".")) {
          subTable = after.slice(1).trim();
        }
      } else {
        serverName = rest;
      }
    } else {
      const dotIndex = rest.indexOf(".");
      if (dotIndex !== -1) {
        serverName = rest.slice(0, dotIndex);
        subTable = rest.slice(dotIndex + 1);
      } else {
        serverName = rest;
      }
    }

    return {
      header: fullHeader,
      rawHeaderLine: line,
      isMcp: true,
      serverName,
      subTable,
    };
  }

  return {
    header: fullHeader,
    rawHeaderLine: line,
    isMcp: false,
    serverName: null,
    subTable: null,
  };
}

function parseTomlDocument(rawText) {
  const lines = rawText.split("\n");
  const sections = [];
  let currentSection = {
    header: "",
    rawHeaderLine: "",
    lines: [],
    isMcp: false,
    serverName: null,
    subTable: null,
  };

  for (const line of lines) {
    const headerInfo = parseTableHeader(line);
    if (headerInfo) {
      if (currentSection.rawHeaderLine || currentSection.lines.length > 0) {
        sections.push(currentSection);
      }
      currentSection = {
        header: headerInfo.header,
        rawHeaderLine: line,
        lines: [],
        isMcp: headerInfo.isMcp,
        serverName: headerInfo.serverName,
        subTable: headerInfo.subTable,
      };
    } else {
      currentSection.lines.push(line);
    }
  }

  if (currentSection.rawHeaderLine || currentSection.lines.length > 0) {
    sections.push(currentSection);
  }

  const mcpServers = {};
  for (const section of sections) {
    if (!section.isMcp || !section.serverName) continue;
    const name = section.serverName;
    mcpServers[name] = mcpServers[name] || {};

    const keyValues = {};
    for (const line of section.lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eqIdx = trimmed.indexOf("=");
      if (eqIdx === -1) continue;
      let key = trimmed.slice(0, eqIdx).trim();
      if (key.startsWith('"') && key.endsWith('"')) {
        try {
          key = JSON.parse(key);
        } catch {
          key = key.slice(1, -1);
        }
      }
      const rawVal = trimmed.slice(eqIdx + 1).trim();
      keyValues[key] = parseTomlValue(rawVal);
    }

    if (section.subTable === "env") {
      mcpServers[name].env = keyValues;
    } else if (!section.subTable) {
      Object.assign(mcpServers[name], keyValues);
    }
  }

  return { sections, mcpServers };
}

function formatCodexServerSections(serverName, payload) {
  const sections = [];
  const serverHeader = `mcp_servers.${formatTomlKey(serverName)}`;
  const mainLines = [];

  if (payload.command) {
    mainLines.push(`command = ${formatTomlValue(payload.command)}`);
  }
  if (payload.args && Array.isArray(payload.args)) {
    mainLines.push(`args = ${formatTomlValue(payload.args)}`);
  }
  if (payload.url) {
    mainLines.push(`url = ${formatTomlValue(payload.url)}`);
  }
  if (payload.bearer_token_env_var) {
    mainLines.push(`bearer_token_env_var = ${formatTomlValue(payload.bearer_token_env_var)}`);
  }

  sections.push({
    header: serverHeader,
    rawHeaderLine: `[${serverHeader}]`,
    lines: mainLines,
    isMcp: true,
    serverName,
    subTable: null,
  });

  if (payload.env && typeof payload.env === "object" && Object.keys(payload.env).length > 0) {
    const envHeader = `mcp_servers.${formatTomlKey(serverName)}.env`;
    const envLines = [];
    for (const [k, v] of Object.entries(payload.env)) {
      envLines.push(`${formatTomlKey(k)} = ${formatTomlValue(v)}`);
    }
    sections.push({
      header: envHeader,
      rawHeaderLine: `[${envHeader}]`,
      lines: envLines,
      isMcp: true,
      serverName,
      subTable: "env",
    });
  }

  return sections;
}

function serializeTomlSections(sections) {
  const chunks = [];
  for (const section of sections) {
    const sectionLines = [];
    if (section.rawHeaderLine) {
      sectionLines.push(section.rawHeaderLine);
    }
    for (const line of section.lines) {
      sectionLines.push(line);
    }
    const text = sectionLines.join("\n").trimEnd();
    if (text.trim()) {
      chunks.push(text.trim());
    }
  }
  return chunks.length > 0 ? `${chunks.join("\n\n")}\n` : "";
}

class ClaudeAdapter {
  name = "claude";
  label = "Claude";

  getConfigPath(scope, projectRoot) {
    if (scope === "global") {
      const customDir = process.env.CLAUDE_CONFIG_DIR;
      if (customDir && customDir.trim()) {
        return path.join(expandHome(customDir.trim()), ".claude.json");
      }
      return path.join(homedir(), ".claude.json");
    }
    if (scope === "project") {
      if (!projectRoot) {
        throw new Error("Project root is required for project-scoped MCP installation.");
      }
      return path.join(projectRoot, ".mcp.json");
    }
    throw new Error(`Invalid scope: ${scope}`);
  }

  formatEntry(payload) {
    if (payload.command) {
      const entry = {
        command: payload.command,
        args: Array.isArray(payload.args) ? payload.args : [],
      };
      if (payload.env && Object.keys(payload.env).length > 0) {
        entry.env = payload.env;
      }
      return entry;
    }
    if (payload.url) {
      const type = payload.type || payload.transport || "http";
      const entry = {
        type,
        url: payload.url,
      };
      if (payload.headers && typeof payload.headers === "object" && Object.keys(payload.headers).length > 0) {
        entry.headers = payload.headers;
      }
      return entry;
    }
    return { ...payload };
  }

  createEmpty() {
    return { mcpServers: {} };
  }

  parse(rawText, filePath) {
    if (!rawText || !rawText.trim()) return { mcpServers: {} };
    try {
      return JSON.parse(rawText);
    } catch (error) {
      throw new Error(`${filePath} is not valid JSON: ${error.message}`);
    }
  }

  serialize(configData) {
    return `${JSON.stringify(configData, undefined, 2)}\n`;
  }

  hasServer(configData, serverName) {
    return Boolean(configData?.mcpServers && serverName in configData.mcpServers);
  }

  applyServer(configData, serverName, serverPayload) {
    const next = { ...configData };
    next.mcpServers = { ...(next.mcpServers || {}) };
    next.mcpServers[serverName] = this.formatEntry(serverPayload);
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return next;
  }

  removeServer(configData, serverName) {
    if (!configData?.mcpServers || !(serverName in configData.mcpServers)) {
      return { updatedConfig: configData, removed: false };
    }
    const next = { ...configData };
    next.mcpServers = { ...next.mcpServers };
    delete next.mcpServers[serverName];
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return { updatedConfig: next, removed: true };
  }
}

class GeminiAdapter {
  name = "gemini";
  label = "Gemini";

  getConfigPath(scope, projectRoot) {
    if (scope === "global") {
      return path.join(homedir(), ".gemini", "settings.json");
    }
    if (scope === "project") {
      if (!projectRoot) {
        throw new Error("Project root is required for project-scoped MCP installation.");
      }
      return path.join(projectRoot, ".gemini", "settings.json");
    }
    throw new Error(`Invalid scope: ${scope}`);
  }

  formatEntry(payload) {
    if (payload.command) {
      const entry = {
        command: payload.command,
        args: Array.isArray(payload.args) ? payload.args : [],
      };
      if (payload.env && Object.keys(payload.env).length > 0) {
        entry.env = payload.env;
      }
      if (payload.timeout !== undefined) entry.timeout = payload.timeout;
      if (payload.trust !== undefined) entry.trust = payload.trust;
      return entry;
    }
    if (payload.transport === "http" || (payload.httpUrl && !payload.url)) {
      const entry = {
        httpUrl: payload.httpUrl || payload.url,
      };
      if (payload.headers && typeof payload.headers === "object" && Object.keys(payload.headers).length > 0) {
        entry.headers = payload.headers;
      }
      if (payload.timeout !== undefined) entry.timeout = payload.timeout;
      if (payload.trust !== undefined) entry.trust = payload.trust;
      return entry;
    }
    if (payload.transport === "sse" || payload.url) {
      const entry = {
        url: payload.url,
      };
      if (payload.headers && typeof payload.headers === "object" && Object.keys(payload.headers).length > 0) {
        entry.headers = payload.headers;
      }
      if (payload.timeout !== undefined) entry.timeout = payload.timeout;
      if (payload.trust !== undefined) entry.trust = payload.trust;
      return entry;
    }
    return { ...payload };
  }

  createEmpty() {
    return { mcpServers: {} };
  }

  parse(rawText, filePath) {
    if (!rawText || !rawText.trim()) return { mcpServers: {} };
    try {
      return JSON.parse(rawText);
    } catch (error) {
      throw new Error(`${filePath} is not valid JSON: ${error.message}`);
    }
  }

  serialize(configData) {
    return `${JSON.stringify(configData, undefined, 2)}\n`;
  }

  hasServer(configData, serverName) {
    return Boolean(configData?.mcpServers && serverName in configData.mcpServers);
  }

  applyServer(configData, serverName, serverPayload) {
    const next = { ...configData };
    next.mcpServers = { ...(next.mcpServers || {}) };
    next.mcpServers[serverName] = this.formatEntry(serverPayload);
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return next;
  }

  removeServer(configData, serverName) {
    if (!configData?.mcpServers || !(serverName in configData.mcpServers)) {
      return { updatedConfig: configData, removed: false };
    }
    const next = { ...configData };
    next.mcpServers = { ...next.mcpServers };
    delete next.mcpServers[serverName];
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return { updatedConfig: next, removed: true };
  }
}

class AgyAdapter {
  name = "agy";
  label = "AGY";

  getConfigPath(scope, _projectRoot) {
    if (scope === "global") {
      return path.join(homedir(), ".gemini", "config", "mcp_config.json");
    }
    if (scope === "project") {
      throw new Error("AGY does not support project-scoped MCP configuration.");
    }
    throw new Error(`Invalid scope: ${scope}`);
  }

  formatEntry(payload) {
    if (payload.command) {
      const entry = {
        command: payload.command,
        args: Array.isArray(payload.args) ? payload.args : [],
      };
      if (payload.env && Object.keys(payload.env).length > 0) {
        entry.env = payload.env;
      }
      if (payload.disabled !== undefined) entry.disabled = Boolean(payload.disabled);
      return entry;
    }
    if (payload.url || payload.serverUrl || payload.transport === "http" || payload.transport === "sse") {
      const entry = {
        serverUrl: payload.serverUrl || payload.url,
      };
      if (payload.headers && typeof payload.headers === "object" && Object.keys(payload.headers).length > 0) {
        entry.headers = payload.headers;
      }
      if (payload.disabled !== undefined) entry.disabled = Boolean(payload.disabled);
      return entry;
    }
    return { ...payload };
  }

  createEmpty() {
    return { mcpServers: {} };
  }

  parse(rawText, filePath) {
    if (!rawText || !rawText.trim()) return { mcpServers: {} };
    try {
      return JSON.parse(rawText);
    } catch (error) {
      throw new Error(`${filePath} is not valid JSON: ${error.message}`);
    }
  }

  serialize(configData) {
    return `${JSON.stringify(configData, undefined, 2)}\n`;
  }

  hasServer(configData, serverName) {
    return Boolean(configData?.mcpServers && serverName in configData.mcpServers);
  }

  applyServer(configData, serverName, serverPayload) {
    const next = { ...configData };
    next.mcpServers = { ...(next.mcpServers || {}) };
    next.mcpServers[serverName] = this.formatEntry(serverPayload);
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return next;
  }

  removeServer(configData, serverName) {
    if (!configData?.mcpServers || !(serverName in configData.mcpServers)) {
      return { updatedConfig: configData, removed: false };
    }
    const next = { ...configData };
    next.mcpServers = { ...next.mcpServers };
    delete next.mcpServers[serverName];
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return { updatedConfig: next, removed: true };
  }
}

class CodexAdapter {
  name = "codex";
  label = "Codex";

  getConfigPath(scope, projectRoot) {
    if (scope === "global") {
      return path.join(homedir(), ".codex", "config.toml");
    }
    if (scope === "project") {
      if (!projectRoot) {
        throw new Error("Project root is required for project-scoped MCP installation.");
      }
      return path.join(projectRoot, ".codex", "config.toml");
    }
    throw new Error(`Invalid scope: ${scope}`);
  }

  formatEntry(payload) {
    if (payload.command) {
      const entry = {
        command: payload.command,
        args: Array.isArray(payload.args) ? payload.args : [],
      };
      if (payload.env && Object.keys(payload.env).length > 0) {
        entry.env = payload.env;
      }
      return entry;
    }
    if (payload.url) {
      const entry = {
        url: payload.url,
      };
      if (payload.bearer_token_env_var) {
        entry.bearer_token_env_var = payload.bearer_token_env_var;
      }
      return entry;
    }
    return { ...payload };
  }

  createEmpty() {
    const config = { mcpServers: {} };
    config[SECTIONS_SYM] = [];
    return config;
  }

  parse(rawText) {
    if (!rawText || !rawText.trim()) {
      const config = { mcpServers: {} };
      config[SECTIONS_SYM] = [];
      return config;
    }
    const { sections, mcpServers } = parseTomlDocument(rawText);
    const config = { mcpServers };
    config[SECTIONS_SYM] = sections;
    return config;
  }

  serialize(configData) {
    const sections = configData[SECTIONS_SYM];
    if (sections && Array.isArray(sections)) {
      return serializeTomlSections(sections);
    }
    const allSections = [];
    for (const [name, payload] of Object.entries(configData.mcpServers || {})) {
      allSections.push(...formatCodexServerSections(name, payload));
    }
    return serializeTomlSections(allSections);
  }

  hasServer(configData, serverName) {
    return Boolean(configData?.mcpServers && serverName in configData.mcpServers);
  }

  applyServer(configData, serverName, serverPayload) {
    const entry = this.formatEntry(serverPayload);
    const next = { ...configData };
    next.mcpServers = { ...(next.mcpServers || {}) };
    next.mcpServers[serverName] = entry;

    const existingSections = [...(configData[SECTIONS_SYM] || [])];
    const newSections = formatCodexServerSections(serverName, entry);

    const firstIndex = existingSections.findIndex(
      (s) => s.isMcp && s.serverName === serverName,
    );

    let updatedSections;
    if (firstIndex !== -1) {
      const filtered = existingSections.filter(
        (s) => !(s.isMcp && s.serverName === serverName),
      );
      const insertIdx = existingSections
        .slice(0, firstIndex)
        .filter((s) => !(s.isMcp && s.serverName === serverName)).length;
      filtered.splice(insertIdx, 0, ...newSections);
      updatedSections = filtered;
    } else {
      const lastMcpIndex = existingSections.findLastIndex((s) => s.isMcp);
      if (lastMcpIndex !== -1) {
        existingSections.splice(lastMcpIndex + 1, 0, ...newSections);
        updatedSections = existingSections;
      } else {
        updatedSections = [...existingSections, ...newSections];
      }
    }

    next[SECTIONS_SYM] = updatedSections;
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return next;
  }

  removeServer(configData, serverName) {
    if (!configData?.mcpServers || !(serverName in configData.mcpServers)) {
      return { updatedConfig: configData, removed: false };
    }
    const next = { ...configData };
    next.mcpServers = { ...next.mcpServers };
    delete next.mcpServers[serverName];

    const existingSections = configData[SECTIONS_SYM] || [];
    const updatedSections = existingSections.filter(
      (s) => !(s.isMcp && s.serverName === serverName),
    );

    next[SECTIONS_SYM] = updatedSections;
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return { updatedConfig: next, removed: true };
  }
}

class OmpAdapter {
  name = "omp";
  label = "OMP";

  getConfigPath(scope, projectRoot) {
    if (scope === "global") {
      return path.join(homedir(), ".omp", "agent", "mcp.json");
    }
    if (scope === "project") {
      if (!projectRoot) {
        throw new Error("Project root is required for project-scoped MCP installation.");
      }
      return path.join(projectRoot, ".omp", "mcp.json");
    }
    throw new Error(`Invalid scope: ${scope}`);
  }

  formatEntry(payload) {
    if (payload.command) {
      const entry = {
        command: payload.command,
        args: Array.isArray(payload.args) ? payload.args : [],
      };
      if (payload.env && Object.keys(payload.env).length > 0) {
        entry.env = payload.env;
      }
      return entry;
    }
    if (payload.url) {
      const entry = {
        url: payload.url,
      };
      if (payload.headers && typeof payload.headers === "object" && Object.keys(payload.headers).length > 0) {
        entry.headers = payload.headers;
      }
      return entry;
    }
    return { ...payload };
  }

  createEmpty() {
    return { mcpServers: {} };
  }

  parse(rawText, filePath) {
    if (!rawText || !rawText.trim()) return { mcpServers: {} };
    try {
      return JSON.parse(rawText);
    } catch (error) {
      throw new Error(`${filePath} is not valid JSON: ${error.message}`);
    }
  }

  serialize(configData) {
    return `${JSON.stringify(configData, undefined, 2)}\n`;
  }

  hasServer(configData, serverName) {
    return Boolean(configData?.mcpServers && serverName in configData.mcpServers);
  }

  applyServer(configData, serverName, serverPayload) {
    const next = { ...configData };
    next.mcpServers = { ...(next.mcpServers || {}) };
    next.mcpServers[serverName] = this.formatEntry(serverPayload);
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return next;
  }

  removeServer(configData, serverName) {
    if (!configData?.mcpServers || !(serverName in configData.mcpServers)) {
      return { updatedConfig: configData, removed: false };
    }
    const next = { ...configData };
    next.mcpServers = { ...next.mcpServers };
    delete next.mcpServers[serverName];
    next[ADAPTER_SYM] = this;
    if (configData[FILE_PATH_SYM]) next[FILE_PATH_SYM] = configData[FILE_PATH_SYM];
    return { updatedConfig: next, removed: true };
  }
}

export const agentAdapters = {
  claude: new ClaudeAdapter(),
  gemini: new GeminiAdapter(),
  agy: new AgyAdapter(),
  codex: new CodexAdapter(),
  omp: new OmpAdapter(),
};

export const agentMcpConfigs = {
  gemini: {
    label: "Gemini",
    global: [".gemini", "settings.json"],
    project: [".gemini", "settings.json"],
  },
  agy: {
    label: "AGY",
    global: [".gemini", "config", "mcp_config.json"],
    project: null,
  },
  claude: {
    label: "Claude",
    global: [".claude.json"],
    project: [".mcp.json"],
  },
  codex: {
    label: "Codex",
    global: [".codex", "config.toml"],
    project: [".codex", "config.toml"],
  },
  omp: {
    label: "OMP",
    global: [".omp", "agent", "mcp.json"],
    project: [".omp", "mcp.json"],
  },
};

export function getAdapterForPath(filePath) {
  const base = path.basename(filePath);
  if (base.endsWith(".toml") || filePath.includes(".codex")) {
    return agentAdapters.codex;
  }
  if (base === "mcp_config.json" || filePath.includes(path.join(".gemini", "config"))) {
    return agentAdapters.agy;
  }
  if (base === ".claude.json" || base === ".mcp.json" || filePath.includes(".claude")) {
    return agentAdapters.claude;
  }
  if (filePath.includes(".gemini")) {
    return agentAdapters.gemini;
  }
  if (filePath.includes(".omp")) {
    return agentAdapters.omp;
  }
  return agentAdapters.gemini;
}

export function getAgentMcpConfigPath(agentName, scope, projectRoot) {
  const adapter = agentAdapters[agentName];
  if (!adapter) throw new Error(`Unknown agent: ${agentName}`);
  return adapter.getConfigPath(scope, projectRoot);
}

export function formatMcpServerPayload(definition, resolvedEnv = {}, agentName = undefined) {
  const transport = definition.transport || "stdio";
  if (transport === "stdio") {
    if (!definition.command) {
      throw new Error(`MCP definition "${definition.name}" is missing required "command" for stdio transport.`);
    }
    const payload = {
      transport: "stdio",
      command: definition.command,
      args: Array.isArray(definition.args) ? definition.args : [],
    };
    if (Object.keys(resolvedEnv).length > 0) {
      payload.env = resolvedEnv;
    }
    if (definition.timeout !== undefined) payload.timeout = definition.timeout;
    if (definition.trust !== undefined) payload.trust = definition.trust;
    if (definition.disabled !== undefined) payload.disabled = definition.disabled;

    if (agentName && agentAdapters[agentName]) {
      return agentAdapters[agentName].formatEntry(payload);
    }
    return payload;
  }

  if (transport === "sse" || transport === "http") {
    if (!definition.url) {
      throw new Error(`MCP definition "${definition.name}" is missing required "url" for ${transport} transport.`);
    }
    const payload = {
      transport,
      url: definition.url,
    };
    if (definition.headers && typeof definition.headers === "object") {
      payload.headers = definition.headers;
    }
    if (definition.bearer_token_env_var) {
      payload.bearer_token_env_var = definition.bearer_token_env_var;
    }
    if (definition.timeout !== undefined) payload.timeout = definition.timeout;
    if (definition.trust !== undefined) payload.trust = definition.trust;
    if (definition.disabled !== undefined) payload.disabled = definition.disabled;

    if (agentName && agentAdapters[agentName]) {
      return agentAdapters[agentName].formatEntry(payload);
    }
    return payload;
  }

  throw new Error(`Unsupported transport "${transport}" in MCP definition "${definition.name}".`);
}

export async function readAgentConfig(filePath) {
  const stat = await pathStat(filePath);
  const adapter = getAdapterForPath(filePath);
  if (!stat) {
    const emptyConfig = adapter.createEmpty();
    emptyConfig[ADAPTER_SYM] = adapter;
    emptyConfig[FILE_PATH_SYM] = filePath;
    return emptyConfig;
  }

  const rawText = await readFile(filePath, "utf8");
  const configData = adapter.parse(rawText, filePath);
  configData[ADAPTER_SYM] = adapter;
  configData[FILE_PATH_SYM] = filePath;
  return configData;
}

export function hasServerInConfig(configData, serverName) {
  const adapter = configData?.[ADAPTER_SYM];
  if (adapter && typeof adapter.hasServer === "function") {
    return adapter.hasServer(configData, serverName);
  }
  return Boolean(configData?.mcpServers && serverName in configData.mcpServers);
}

export function applyServerToConfig(configData, serverName, serverPayload) {
  const adapter = configData?.[ADAPTER_SYM];
  if (adapter && typeof adapter.applyServer === "function") {
    return adapter.applyServer(configData, serverName, serverPayload);
  }
  const next = { ...configData };
  next.mcpServers = { ...(next.mcpServers || {}) };
  next.mcpServers[serverName] = serverPayload;
  return next;
}

export function removeServerFromConfig(configData, serverName) {
  const adapter = configData?.[ADAPTER_SYM];
  if (adapter && typeof adapter.removeServer === "function") {
    return adapter.removeServer(configData, serverName);
  }
  if (!configData?.mcpServers || !(serverName in configData.mcpServers)) {
    return { updatedConfig: configData, removed: false };
  }
  const next = { ...configData };
  next.mcpServers = { ...next.mcpServers };
  delete next.mcpServers[serverName];
  return { updatedConfig: next, removed: true };
}

export async function writeAgentConfig(filePath, configData) {
  const adapter = configData?.[ADAPTER_SYM] || getAdapterForPath(filePath);
  const content = adapter.serialize(configData, filePath);
  await writeTextFileAtomic(filePath, content);
}
