#!/usr/bin/env node

// Manage, review, and consolidate skill suggestions stored in suggestions/<skill>/*.jsonl.

import { mkdir, readdir, readFile, rename, rmdir, unlink, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export function resolveSuggestionsDir() {
  const custom = process.env.AGENT_SKILLS_SUGGESTIONS_DIR;
  if (custom) {
    return custom.startsWith("~") ? path.join(homedir(), custom.slice(1)) : path.resolve(custom);
  }
  return path.join(repositoryRoot, "suggestions");
}

export async function readAllSuggestions(baseDir) {
  let entries = [];
  try {
    entries = await readdir(baseDir, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }

  const skillDirs = entries.filter((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "archive");
  const suggestions = [];

  async function processSkillDirectory(skillPath, skillName, isManaged = true) {
    let files = [];
    try {
      files = await readdir(skillPath, { withFileTypes: true });
    } catch {
      return;
    }

    for (const file of files) {
      if (!file.isFile() || !file.name.endsWith(".jsonl") || file.name.startsWith(".")) {
        continue;
      }
      const category = path.basename(file.name, ".jsonl");
      const filePath = path.join(skillPath, file.name);
      try {
        const raw = await readFile(filePath, "utf8");
        const lines = raw.split("\n").map((l) => l.trim()).filter(Boolean);
        for (const line of lines) {
          try {
            const parsed = JSON.parse(line);
            parsed._filePath = filePath;
            parsed._skill = skillName;
            parsed._tier = category;
            parsed._isManaged = isManaged;
            suggestions.push(parsed);
          } catch {
            // ignore malformed line
          }
        }
      } catch {
        // ignore read error
      }
    }
  }

  for (const skillDir of skillDirs) {
    if (skillDir.name === "_unmanaged") {
      let unmanagedEntries = [];
      try {
        unmanagedEntries = await readdir(path.join(baseDir, "_unmanaged"), { withFileTypes: true });
      } catch {
        continue;
      }
      for (const unmanagedDir of unmanagedEntries) {
        if (unmanagedDir.isDirectory() && !unmanagedDir.name.startsWith(".")) {
          await processSkillDirectory(path.join(baseDir, "_unmanaged", unmanagedDir.name), `[unmanaged] ${unmanagedDir.name}`, false);
        }
      }
    } else {
      await processSkillDirectory(path.join(baseDir, skillDir.name), skillDir.name, true);
    }
  }

  return suggestions.sort((a, b) => {
    // Sort priority: critical first, then bug, then improvement
    const tierOrder = { critical: 0, bug: 1, improvement: 2 };
    const orderDiff = (tierOrder[a.category] ?? 3) - (tierOrder[b.category] ?? 3);
    if (orderDiff !== 0) return orderDiff;
    return (b.timestamp || "").localeCompare(a.timestamp || "");
  });
}

async function cleanEmptyDir(dirPath) {
  try {
    const remaining = await readdir(dirPath);
    if (remaining.length === 0) {
      await rmdir(dirPath);
      return true;
    }
  } catch {
    // ignore
  }
  return false;
}

export async function archiveSkillSuggestions(baseDir, targetSkill, targetCategory = null) {
  const now = new Date();
  const yearMonth = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
  const timestamp = now.toISOString().replace(/[:.]/g, "-");
  const archiveDir = path.join(baseDir, "archive", yearMonth);
  await mkdir(archiveDir, { recursive: true });

  const archivedFiles = [];

  async function archiveDirFiles(skillPath, skillName) {
    const categories = targetCategory ? [targetCategory] : ["critical", "bug", "improvement"];

    for (const cat of categories) {
      const srcFile = path.join(skillPath, `${cat}.jsonl`);
      try {
        const content = await readFile(srcFile, "utf8");
        if (content.trim().length > 0) {
          const destFile = path.join(archiveDir, `${skillName}_${cat}_${timestamp}.jsonl`);
          await rename(srcFile, destFile);
          archivedFiles.push(destFile);
        } else {
          // Clean up empty 0-byte file
          await unlink(srcFile).catch(() => {});
        }
      } catch {
        // file doesn't exist or already empty
      }
    }

    await cleanEmptyDir(skillPath);
  }

  if (targetSkill === "all") {
    let entries = [];
    try {
      entries = await readdir(baseDir, { withFileTypes: true });
    } catch (error) {
      if (error.code === "ENOENT") return [];
      throw error;
    }

    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith(".") || entry.name === "archive") {
        continue;
      }
      if (entry.name === "_unmanaged") {
        let unmanagedEntries = [];
        try {
          unmanagedEntries = await readdir(path.join(baseDir, "_unmanaged"), { withFileTypes: true });
        } catch {
          continue;
        }
        for (const unmanaged of unmanagedEntries) {
          if (unmanaged.isDirectory() && !unmanaged.name.startsWith(".")) {
            await archiveDirFiles(path.join(baseDir, "_unmanaged", unmanaged.name), `unmanaged_${unmanaged.name}`);
          }
        }
        await cleanEmptyDir(path.join(baseDir, "_unmanaged"));
      } else {
        await archiveDirFiles(path.join(baseDir, entry.name), entry.name);
      }
    }
  } else {
    // Specific target skill
    const skillPath = path.join(baseDir, targetSkill);
    await archiveDirFiles(skillPath, targetSkill);

    // Also check if skill was inside _unmanaged/<targetSkill>
    const unmanagedSkillPath = path.join(baseDir, "_unmanaged", targetSkill);
    try {
      const exists = await readdir(unmanagedSkillPath);
      if (exists) {
        await archiveDirFiles(unmanagedSkillPath, `unmanaged_${targetSkill}`);
        await cleanEmptyDir(path.join(baseDir, "_unmanaged"));
      }
    } catch {
      // not in _unmanaged
    }
  }

  return archivedFiles;
}

export async function readArchivedSuggestions(baseDir, month = null) {
  const normalizedMonth = month ? String(month).trim().replace(/^[/\\]+|[/\\]+$/g, "") : null;
  const archiveDir = normalizedMonth
    ? path.join(baseDir, "archive", normalizedMonth)
    : path.join(baseDir, "archive");
  let entries = [];
  try {
    entries = await readdir(archiveDir, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }

  const suggestions = [];

  async function walkDir(currentDir) {
    let dirEntries = [];
    try {
      dirEntries = await readdir(currentDir, { withFileTypes: true });
    } catch {
      return;
    }

    for (const entry of dirEntries) {
      if (entry.name.startsWith(".")) continue;
      const fullPath = path.join(currentDir, entry.name);
      if (entry.isDirectory()) {
        await walkDir(fullPath);
      } else if (entry.isFile() && entry.name.endsWith(".jsonl")) {
        try {
          const raw = await readFile(fullPath, "utf8");
          const lines = raw.split("\n").map((l) => l.trim()).filter(Boolean);
          for (const line of lines) {
            try {
              const parsed = JSON.parse(line);
              parsed._filePath = fullPath;
              parsed._skill = parsed.skill || path.basename(entry.name).split("_")[0];
              parsed._tier = parsed.category || "improvement";
              parsed._isManaged = parsed.managed !== false;
              parsed._isArchived = true;
              suggestions.push(parsed);
            } catch {
              // ignore malformed line
            }
          }
        } catch {
          // ignore read error
        }
      }
    }
  }

  await walkDir(archiveDir);

  return suggestions.sort((a, b) => {
    const tierOrder = { critical: 0, bug: 1, improvement: 2 };
    const orderDiff = (tierOrder[a.category] ?? 3) - (tierOrder[b.category] ?? 3);
    if (orderDiff !== 0) return orderDiff;
    return (b.timestamp || "").localeCompare(a.timestamp || "");
  });
}

export async function findArchivedSuggestions(baseDir, query, month = null) {
  const all = await readArchivedSuggestions(baseDir, month);
  if (!query) return all;

  const q = String(query).trim();
  const qLower = q.toLowerCase();
  const qBase = path.basename(q);
  const qResolved = path.resolve(q);

  return all.filter((s) => {
    // 1. Match by suggestion id
    if (s.id && (s.id === q || s.id.toLowerCase() === qLower)) {
      return true;
    }
    // 2. Match by file path or filename
    if (s._filePath) {
      if (path.resolve(s._filePath) === qResolved) {
        return true;
      }
      const base = path.basename(s._filePath);
      if (base === q || base === qBase) {
        return true;
      }
      if (path.basename(s._filePath, ".jsonl") === q || path.basename(s._filePath, ".jsonl") === qBase) {
        return true;
      }
      if (base.includes(q)) {
        return true;
      }
    }
    // 3. Match by skill name
    const skill = s.skill || s._skill;
    if (skill && (skill === q || skill.toLowerCase() === qLower)) {
      return true;
    }
    return false;
  });
}

function pad(value, width) {
  return String(value).padEnd(width, " ");
}

export function formatSummaryTable(suggestions) {
  const countsBySkill = {};
  for (const s of suggestions) {
    const key = s._skill || s.skill || "unknown";
    if (!countsBySkill[key]) {
      countsBySkill[key] = { critical: 0, bug: 0, improvement: 0, total: 0 };
    }
    const cat = s.category || s._tier || "improvement";
    if (cat in countsBySkill[key]) {
      countsBySkill[key][cat]++;
    } else {
      countsBySkill[key].improvement++;
    }
    countsBySkill[key].total++;
  }

  const rows = Object.entries(countsBySkill)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([skill, counts]) => ({
      skill,
      critical: String(counts.critical),
      bug: String(counts.bug),
      improvement: String(counts.improvement),
      total: String(counts.total),
    }));

  const totalCrit = suggestions.filter((s) => (s.category || s._tier) === "critical").length;
  const totalBug = suggestions.filter((s) => (s.category || s._tier) === "bug").length;
  const totalImp = suggestions.filter((s) => !["critical", "bug"].includes(s.category || s._tier)).length;

  const totals = {
    skill: "TOTAL",
    critical: String(totalCrit),
    bug: String(totalBug),
    improvement: String(totalImp),
    total: String(suggestions.length),
  };

  const skillWidth = Math.max(5, ...rows.map((r) => r.skill.length), totals.skill.length);
  const critWidth = Math.max(8, ...rows.map((r) => r.critical.length), totals.critical.length);
  const bugWidth = Math.max(3, ...rows.map((r) => r.bug.length), totals.bug.length);
  const impWidth = Math.max(11, ...rows.map((r) => r.improvement.length), totals.improvement.length);
  const totalWidth = Math.max(5, ...rows.map((r) => r.total.length), totals.total.length);

  const lines = [];
  lines.push(
    `${pad("SKILL", skillWidth)}  ${pad("CRITICAL", critWidth)}  ${pad("BUG", bugWidth)}  ${pad("IMPROVEMENT", impWidth)}  ${pad("TOTAL", totalWidth)}`
  );
  lines.push(
    `${"-".repeat(skillWidth)}  ${"-".repeat(critWidth)}  ${"-".repeat(bugWidth)}  ${"-".repeat(impWidth)}  ${"-".repeat(totalWidth)}`
  );
  for (const row of rows) {
    lines.push(
      `${pad(row.skill, skillWidth)}  ${pad(row.critical, critWidth)}  ${pad(row.bug, bugWidth)}  ${pad(row.improvement, impWidth)}  ${pad(row.total, totalWidth)}`
    );
  }
  lines.push(
    `${"-".repeat(skillWidth)}  ${"-".repeat(critWidth)}  ${"-".repeat(bugWidth)}  ${"-".repeat(impWidth)}  ${"-".repeat(totalWidth)}`
  );
  lines.push(
    `${pad(totals.skill, skillWidth)}  ${pad(totals.critical, critWidth)}  ${pad(totals.bug, bugWidth)}  ${pad(totals.improvement, impWidth)}  ${pad(totals.total, totalWidth)}`
  );

  return lines.join("\n");
}

function usage() {
  console.log(`Manage, review, and consolidate skill improvement suggestions.

Usage:
  just suggestions
  just suggestions --skill <name>
  just suggestions --category <critical|bug|improvement>
  just suggestions --priority <critical|high|medium|low>
  just suggestions --agent <name>
  just suggestions --session <id>
  just suggestions --list-archived [--month <YYYY-MM>]
  just suggestions --month <YYYY-MM>
  just suggestions --view-archive <filename|id|skill> [--month <YYYY-MM>]
  just suggestions --json
  just suggestions --archive <skill|all>

Options:
  --skill <name>                Filter suggestions for a specific skill
  --category <cat>              Filter by tier: critical, bug, or improvement
  --priority <prio>             Filter by priority: critical, high, medium, low
  --agent <name>                Filter by reporting agent (gemini, claude, codex, etc.)
  --session <id>                Filter by session ID
  --list-archived               Display archived suggestions from suggestions/archive/
  --month <YYYY-MM>             Filter archived suggestions by month batch (e.g. 2026-08)
  --view-archive <query>        View a specific archive snapshot (filename), ID, or skill
  --json                        Output matching suggestions as JSON array
  --archive <skill|all>         Snapshot and archive suggestions for consolidation
  -h, --help                    Show this help
`);
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes("--help") || args.includes("-h")) {
    usage();
    return;
  }

  const baseDir = resolveSuggestionsDir();

  const archiveIdx = args.indexOf("--archive");
  const archiveTarget = archiveIdx !== -1 && args[archiveIdx + 1] ? args[archiveIdx + 1] : null;

  const viewArchiveIdx = args.indexOf("--view-archive");
  const viewArchiveTarget = viewArchiveIdx !== -1 && args[viewArchiveIdx + 1] ? args[viewArchiveIdx + 1] : null;

  const monthIdx = args.indexOf("--month");
  const filterMonth = monthIdx !== -1 && args[monthIdx + 1] ? args[monthIdx + 1] : null;

  const categoryIdx = args.indexOf("--category");
  const filterCategory = categoryIdx !== -1 && args[categoryIdx + 1] ? args[categoryIdx + 1] : null;

  const skillIdx = args.indexOf("--skill");
  const filterSkill = skillIdx !== -1 && args[skillIdx + 1] ? args[skillIdx + 1] : null;

  const priorityIdx = args.indexOf("--priority");
  const filterPriority = priorityIdx !== -1 && args[priorityIdx + 1] ? args[priorityIdx + 1] : null;

  const agentIdx = args.indexOf("--agent");
  const filterAgent = agentIdx !== -1 && args[agentIdx + 1] ? args[agentIdx + 1] : null;

  let sessionIdx = args.indexOf("--session");
  if (sessionIdx === -1) {
    sessionIdx = args.indexOf("--session-id");
  }
  const filterSession = sessionIdx !== -1 && args[sessionIdx + 1] ? args[sessionIdx + 1] : null;

  const listArchivedFlag = args.includes("--list-archived");
  const jsonFlag = args.includes("--json");

  if (archiveTarget) {
    const archived = await archiveSkillSuggestions(baseDir, archiveTarget, filterCategory);
    if (!archived.length) {
      console.log(`No suggestions found to archive for: ${archiveTarget}`);
      return;
    }
    console.log(`Archived ${archived.length} suggestion file(s) for consolidation:`);
    for (const f of archived) {
      console.log(`  -> ${f}`);
    }
    return;
  }

  let suggestions = [];
  if (viewArchiveTarget) {
    suggestions = await findArchivedSuggestions(baseDir, viewArchiveTarget, filterMonth);
  } else if (listArchivedFlag || filterMonth) {
    suggestions = await readArchivedSuggestions(baseDir, filterMonth);
  } else {
    suggestions = await readAllSuggestions(baseDir);
  }

  if (filterSkill) {
    suggestions = suggestions.filter((s) => (s.skill || s._skill) === filterSkill);
  }
  if (filterCategory) {
    suggestions = suggestions.filter((s) => (s.category || s._tier) === filterCategory);
  }
  if (filterPriority) {
    suggestions = suggestions.filter((s) => s.priority === filterPriority);
  }
  if (filterAgent) {
    suggestions = suggestions.filter((s) => s.agent === filterAgent);
  }
  if (filterSession) {
    suggestions = suggestions.filter((s) => s.session_id === filterSession);
  }

  if (jsonFlag) {
    console.log(JSON.stringify(suggestions, null, 2));
    return;
  }

  if (viewArchiveTarget) {
    console.log(`Archived query:     ${viewArchiveTarget}`);
    if (filterMonth) {
      console.log(`Month filter:       ${filterMonth}`);
    }
    console.log(`Matching reports:   ${suggestions.length}\n`);
  } else if (listArchivedFlag || filterMonth) {
    console.log(`Archived suggestions folder: ${filterMonth ? path.join(baseDir, "archive", filterMonth) : path.join(baseDir, "archive")}`);
    console.log(`Archived reports:            ${suggestions.length}\n`);
  } else {
    console.log(`Suggestions folder: ${baseDir}`);
    console.log(`Pending reports:    ${suggestions.length}\n`);
  }

  if (!suggestions.length) {
    if (viewArchiveTarget) {
      console.log(`No archived suggestions matching '${viewArchiveTarget}'.`);
    } else if (listArchivedFlag || filterMonth) {
      console.log("No archived suggestions found.");
    } else {
      console.log("No pending suggestions found in repository.");
    }
    return;
  }

  console.log(formatSummaryTable(suggestions));
  console.log("");

  const grouped = {};
  for (const s of suggestions) {
    const key = s._skill || s.skill || "unknown";
    if (!grouped[key]) grouped[key] = { critical: [], bug: [], improvement: [] };
    const cat = s.category || s._tier || "improvement";
    if (grouped[key][cat]) {
      grouped[key][cat].push(s);
    } else {
      grouped[key].improvement.push(s);
    }
  }

  for (const [skillName, categories] of Object.entries(grouped)) {
    const total = categories.critical.length + categories.bug.length + categories.improvement.length;
    console.log(`================================================================`);
    console.log(`Skill: ${skillName} (${total} report${total === 1 ? "" : "s"})`);
    console.log(`================================================================`);

    for (const tier of ["critical", "bug", "improvement"]) {
      const items = categories[tier];
      if (!items.length) continue;

      const tag = tier === "critical" ? "🔥 CRITICAL" : tier === "bug" ? "🐛 BUG" : "💡 IMPROVEMENT";
      console.log(`\n  --- [ ${tag} ] (${items.length}) ---`);

      for (const item of items) {
        console.log(`  • [${item.id}] Priority: ${item.priority} | Agent: ${item.agent || "unknown"} | Session: ${item.session_id || "unknown"}`);
        console.log(`    Summary:  ${item.summary}`);
        if (item.detail) {
          console.log(`    Detail:   ${item.detail}`);
        }
        if (item.suggested_fix) {
          console.log(`    Fix:      ${item.suggested_fix}`);
        }
        console.log("");
      }
    }
    console.log("");
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname)) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}

