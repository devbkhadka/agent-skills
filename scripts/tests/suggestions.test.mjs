import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { makeWorkspace, run } from "./fixture.mjs";
import { archiveSkillSuggestions, findArchivedSuggestions, formatSummaryTable, readAllSuggestions, readArchivedSuggestions } from "../skill-suggestions.mjs";

test("suggest.py partitions suggestions into critical/bug/improvement jsonl in repo suggestions directory", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestScript = path.resolve(import.meta.dirname, "..", "..", "skills", "skill-improver", "scripts", "suggest.py");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  // 1. Record a critical blocker
  const outCrit = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--summary",
      "Infinite recursion in scheduler",
      "--detail",
      "Soft tags with cycles cause recursion depth error",
      "--category",
      "critical",
      "--priority",
      "critical",
      "--agent",
      "gemini",
      "--session-id",
      "session-101",
    ],
    { encoding: "utf8", env }
  );

  const parsedCrit = JSON.parse(outCrit);
  assert.equal(parsedCrit.status, "recorded");
  assert.equal(parsedCrit.category, "critical");

  // 2. Record a bug
  const outBug = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--summary",
      "Trailing whitespace in email",
      "--detail",
      "Regex failed on untrimmed email",
      "--category",
      "bug",
      "--priority",
      "high",
      "--agent",
      "claude",
      "--session-id",
      "session-102",
      "--fix",
      "Add .strip()",
    ],
    { encoding: "utf8", env }
  );

  const parsedBug = JSON.parse(outBug);
  assert.equal(parsedBug.status, "recorded");
  assert.equal(parsedBug.category, "bug");

  // 3. Record an improvement
  const outImp = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--summary",
      "Add dry-run flag",
      "--detail",
      "Dry-run flag would help preview changes",
      "--category",
      "improvement",
      "--priority",
      "low",
      "--agent",
      "codex",
      "--session-id",
      "session-103",
    ],
    { encoding: "utf8", env }
  );

  const parsedImp = JSON.parse(outImp);
  assert.equal(parsedImp.status, "recorded");
  assert.equal(parsedImp.category, "improvement");

  // Verify file existence in suggestions/timelog-generator/
  const skillDir = path.join(suggestionsDir, "timelog-generator");
  const files = (await readdir(skillDir)).sort();
  assert.deepEqual(files, ["bug.jsonl", "critical.jsonl", "improvement.jsonl"]);

  // Verify contents
  const critLines = (await readFile(path.join(skillDir, "critical.jsonl"), "utf8")).trim().split("\n");
  assert.equal(critLines.length, 1);
  const critObj = JSON.parse(critLines[0]);
  assert.equal(critObj.summary, "Infinite recursion in scheduler");
  assert.equal(critObj.session_id, "session-101");
  assert.equal(critObj.category, "critical");

  const bugLines = (await readFile(path.join(skillDir, "bug.jsonl"), "utf8")).trim().split("\n");
  assert.equal(bugLines.length, 1);
  const bugObj = JSON.parse(bugLines[0]);
  assert.equal(bugObj.summary, "Trailing whitespace in email");
  assert.equal(bugObj.suggested_fix, "Add .strip()");
});

test("readAllSuggestions sorts critical first and archiveSkillSuggestions snapshots correctly", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestScript = path.resolve(import.meta.dirname, "..", "..", "skills", "skill-improver", "scripts", "suggest.py");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  // Add an improvement and a critical issue
  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "beads-task-scheduler",
      "--priority",
      "low",
      "--summary",
      "Minor prompt typo",
      "--detail",
      "Typo in SKILL.md",
      "--category",
      "improvement",
    ],
    { encoding: "utf8", env }
  );

  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "beads-task-scheduler",
      "--priority",
      "critical",
      "--summary",
      "Hard crash on exit",
      "--detail",
      "Crash trace on exit",
      "--category",
      "critical",
    ],
    { encoding: "utf8", env }
  );

  const all = await readAllSuggestions(suggestionsDir);
  assert.equal(all.length, 2);
  // Critical should come first in sort order
  assert.equal(all[0].category, "critical");
  assert.equal(all[1].category, "improvement");

  // Archive critical only
  const archived = await archiveSkillSuggestions(suggestionsDir, "beads-task-scheduler", "critical");
  assert.equal(archived.length, 1);
  assert.ok(archived[0].includes("beads-task-scheduler_critical_"));

  // Remaining suggestions should only be the improvement
  const remaining = await readAllSuggestions(suggestionsDir);
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0].category, "improvement");
});

test("suggest.py enforces required priority, resolves aliases, and records unmanaged critical audit only", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestScript = path.resolve(import.meta.dirname, "..", "..", "skills", "skill-improver", "scripts", "suggest.py");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  // 1. Alias: "timelog" should resolve to canonical "timelog-generator"
  const outAlias = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog",
      "--priority",
      "medium",
      "--summary",
      "Alias test",
      "--detail",
      "Testing alias resolution",
    ],
    { encoding: "utf8", env }
  );

  const parsedAlias = JSON.parse(outAlias);
  assert.equal(parsedAlias.skill, "timelog-generator");
  assert.equal(parsedAlias.managed, true);

  // 2. Unrelated / unmanaged skill with non-critical priority: should be cleanly ignored
  const outUnmanagedNonCrit = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "random-external-tool",
      "--priority",
      "medium",
      "--summary",
      "Unrelated tool minor issue",
    ],
    { encoding: "utf8", env }
  );

  const parsedIgnored = JSON.parse(outUnmanagedNonCrit);
  assert.equal(parsedIgnored.status, "ignored");
  assert.equal(parsedIgnored.reason, "unmanaged_non_critical");

  // 3. Unrelated / unmanaged skill with critical priority: should be recorded as audit-only summary
  const outUnmanagedCrit = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "random-external-tool",
      "--priority",
      "critical",
      "--summary",
      "External tool hard failure",
      "--detail",
      "Long detailed traceback that should be omitted in audit",
    ],
    { encoding: "utf8", env }
  );

  const parsedCrit = JSON.parse(outUnmanagedCrit);
  assert.equal(parsedCrit.status, "recorded");
  assert.equal(parsedCrit.managed, false);
  assert.equal(parsedCrit.audit_only, true);
  assert.ok(parsedCrit.file.includes("_unmanaged/random-external-tool"));

  // Check readAllSuggestions reports managed and unmanaged audit correctly
  const all = await readAllSuggestions(suggestionsDir);
  assert.equal(all.length, 2);

  const unmanagedAudit = all.find((s) => !s.managed);
  assert.ok(unmanagedAudit);
  assert.equal(unmanagedAudit.summary, "External tool hard failure");
  assert.equal(unmanagedAudit.priority, "critical");
  assert.equal(unmanagedAudit.detail, undefined); // Detail omitted from unmanaged audit
});

test("suggest.py infers session and agent from ANTIGRAVITY_CONVERSATION_ID, HERDR_SESSION_NAME, HERDR_AGENT_NAME, and AGY", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestScript = path.resolve(import.meta.dirname, "..", "..", "skills", "skill-improver", "scripts", "suggest.py");

  // 1. Antigravity env: ANTIGRAVITY_CONVERSATION_ID
  const envAgy = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
    ANTIGRAVITY_CONVERSATION_ID: "agy-conv-987",
  };
  delete envAgy.GEMINI_CLI;
  delete envAgy.AGY;
  delete envAgy.HERDR_AGENT_NAME;
  delete envAgy.HERDR_SESSION_NAME;
  delete envAgy.CONVERSATION_ID;
  delete envAgy.SESSION_ID;
  delete envAgy.CLAUDE_SESSION_ID;
  delete envAgy.TASK_ID;

  const outAgy = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "low",
      "--summary",
      "Antigravity report",
    ],
    { encoding: "utf8", env: envAgy }
  );
  const parsedAgy = JSON.parse(outAgy);
  assert.equal(parsedAgy.status, "recorded");

  // 2. Herdr env: HERDR_SESSION_NAME and HERDR_AGENT_NAME
  const envHerdr = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
    HERDR_SESSION_NAME: "herdr-sess-456",
    HERDR_AGENT_NAME: "bead-worker-01",
  };
  delete envHerdr.ANTIGRAVITY_CONVERSATION_ID;
  delete envHerdr.GEMINI_CLI;
  delete envHerdr.AGY;
  delete envHerdr.CONVERSATION_ID;
  delete envHerdr.SESSION_ID;
  delete envHerdr.CLAUDE_SESSION_ID;
  delete envHerdr.TASK_ID;

  const outHerdr = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "medium",
      "--summary",
      "Herdr worker report",
    ],
    { encoding: "utf8", env: envHerdr }
  );
  const parsedHerdr = JSON.parse(outHerdr);
  assert.equal(parsedHerdr.status, "recorded");

  // 3. AGY=1 env: agent should infer to gemini
  const envAgyFlag = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
    AGY: "1",
  };
  delete envAgyFlag.ANTIGRAVITY_CONVERSATION_ID;
  delete envAgyFlag.GEMINI_CLI;
  delete envAgyFlag.HERDR_AGENT_NAME;
  delete envAgyFlag.HERDR_SESSION_NAME;

  const outAgyFlag = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "high",
      "--summary",
      "AGY flag report",
    ],
    { encoding: "utf8", env: envAgyFlag }
  );
  const parsedAgyFlag = JSON.parse(outAgyFlag);
  assert.equal(parsedAgyFlag.status, "recorded");

  const all = await readAllSuggestions(suggestionsDir);
  assert.equal(all.length, 3);

  const agyItem = all.find((s) => s.summary === "Antigravity report");
  assert.ok(agyItem);
  assert.equal(agyItem.agent, "gemini");
  assert.equal(agyItem.session_id, "agy-conv-987");

  const herdrItem = all.find((s) => s.summary === "Herdr worker report");
  assert.ok(herdrItem);
  assert.equal(herdrItem.agent, "bead-worker-01");
  assert.equal(herdrItem.session_id, "herdr-sess-456");

  const agyFlagItem = all.find((s) => s.summary === "AGY flag report");
  assert.ok(agyFlagItem);
  assert.equal(agyFlagItem.agent, "gemini");
});

test("skill-suggestions.mjs filters by --agent and --session and prints summary table", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestScript = path.resolve(import.meta.dirname, "..", "..", "skills", "skill-improver", "scripts", "suggest.py");
  const suggestionsScript = path.resolve(import.meta.dirname, "..", "skill-suggestions.mjs");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  // Record 3 suggestions with different agents and sessions
  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "high",
      "--summary",
      "Claude session alpha issue",
      "--agent",
      "claude",
      "--session-id",
      "session-alpha",
    ],
    { encoding: "utf8", env }
  );

  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "critical",
      "--summary",
      "Gemini session beta issue",
      "--agent",
      "gemini",
      "--session-id",
      "session-beta",
    ],
    { encoding: "utf8", env }
  );

  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "beads-task-scheduler",
      "--priority",
      "low",
      "--summary",
      "Claude session beta improvement",
      "--agent",
      "claude",
      "--session-id",
      "session-beta",
    ],
    { encoding: "utf8", env }
  );

  // Filter by agent: claude -> 2 items
  const outAgentClaude = execFileSync(
    "node",
    [suggestionsScript, "--agent", "claude", "--json"],
    { encoding: "utf8", env }
  );
  const parsedClaude = JSON.parse(outAgentClaude);
  assert.equal(parsedClaude.length, 2);
  assert.ok(parsedClaude.every((s) => s.agent === "claude"));

  // Filter by session: session-beta -> 2 items (gemini critical + claude improvement)
  const outSessionBeta = execFileSync(
    "node",
    [suggestionsScript, "--session", "session-beta", "--json"],
    { encoding: "utf8", env }
  );
  const parsedBeta = JSON.parse(outSessionBeta);
  assert.equal(parsedBeta.length, 2);
  assert.ok(parsedBeta.every((s) => s.session_id === "session-beta"));

  // Filter by both agent and session
  const outBoth = execFileSync(
    "node",
    [suggestionsScript, "--agent", "gemini", "--session", "session-beta", "--json"],
    { encoding: "utf8", env }
  );
  const parsedBoth = JSON.parse(outBoth);
  assert.equal(parsedBoth.length, 1);
  assert.equal(parsedBoth[0].summary, "Gemini session beta issue");

  // Format summary table check
  const all = await readAllSuggestions(suggestionsDir);
  const tableText = formatSummaryTable(all);
  assert.match(tableText, /SKILL\s+CRITICAL\s+BUG\s+IMPROVEMENT\s+TOTAL/);
  assert.match(tableText, /timelog-generator\s+1\s+1\s+0\s+2/);
  assert.match(tableText, /beads-task-scheduler\s+0\s+0\s+1\s+1/);
  assert.match(tableText, /TOTAL\s+1\s+1\s+1\s+3/);

  // CLI non-JSON output includes summary table and reports
  const outCli = execFileSync(
    "node",
    [suggestionsScript],
    { encoding: "utf8", env }
  );
  assert.match(outCli, /Pending reports:\s+3/);
  assert.match(outCli, /SKILL\s+CRITICAL\s+BUG\s+IMPROVEMENT\s+TOTAL/);
  assert.match(outCli, /TOTAL\s+1\s+1\s+1\s+3/);
});

test("skill-suggestions.mjs --list-archived lists archived suggestions with filters", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestScript = path.resolve(import.meta.dirname, "..", "..", "skills", "skill-improver", "scripts", "suggest.py");
  const suggestionsScript = path.resolve(import.meta.dirname, "..", "skill-suggestions.mjs");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  // Initially --list-archived should report no archived suggestions
  const emptyOut = execFileSync(
    "node",
    [suggestionsScript, "--list-archived"],
    { encoding: "utf8", env }
  );
  assert.match(emptyOut, /No archived suggestions found\./);

  // Record suggestions
  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "critical",
      "--summary",
      "Critical timelog bug",
      "--agent",
      "gemini",
      "--session-id",
      "sess-1",
    ],
    { encoding: "utf8", env }
  );

  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "low",
      "--summary",
      "Timelog improvement",
      "--agent",
      "claude",
      "--session-id",
      "sess-2",
    ],
    { encoding: "utf8", env }
  );

  // Archive timelog-generator
  const archived = await archiveSkillSuggestions(suggestionsDir, "timelog-generator");
  assert.equal(archived.length, 2);

  // Verify readArchivedSuggestions function
  const archivedItems = await readArchivedSuggestions(suggestionsDir);
  assert.equal(archivedItems.length, 2);
  assert.equal(archivedItems[0].category, "critical");
  assert.equal(archivedItems[1].category, "improvement");

  // CLI --list-archived --json
  const outJson = execFileSync(
    "node",
    [suggestionsScript, "--list-archived", "--json"],
    { encoding: "utf8", env }
  );
  const parsedJson = JSON.parse(outJson);
  assert.equal(parsedJson.length, 2);

  // CLI --list-archived with --agent filter
  const outAgent = execFileSync(
    "node",
    [suggestionsScript, "--list-archived", "--agent", "gemini", "--json"],
    { encoding: "utf8", env }
  );
  const parsedAgent = JSON.parse(outAgent);
  assert.equal(parsedAgent.length, 1);
  assert.equal(parsedAgent[0].agent, "gemini");

  // CLI --list-archived default output
  const outDefault = execFileSync(
    "node",
    [suggestionsScript, "--list-archived"],
    { encoding: "utf8", env }
  );
  assert.match(outDefault, /Archived reports:\s+2/);
  assert.match(outDefault, /SKILL\s+CRITICAL\s+BUG\s+IMPROVEMENT\s+TOTAL/);
  assert.match(outDefault, /TOTAL\s+1\s+0\s+1\s+2/);
  assert.match(outDefault, /Critical timelog bug/);
});

test("archiveSkillSuggestions cleans up empty suggestion skill directories and unmanaged directories", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestScript = path.resolve(import.meta.dirname, "..", "..", "skills", "skill-improver", "scripts", "suggest.py");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  // 1. Record suggestions for multiple skills
  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "critical",
      "--summary",
      "Critical timelog bug",
    ],
    { encoding: "utf8", env }
  );

  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "timelog-generator",
      "--priority",
      "medium",
      "--summary",
      "Bug timelog",
    ],
    { encoding: "utf8", env }
  );

  execFileSync(
    "python3",
    [
      suggestScript,
      "--skill",
      "beads-task-scheduler",
      "--priority",
      "low",
      "--summary",
      "Improvement beads",
    ],
    { encoding: "utf8", env }
  );

  const timelogDir = path.join(suggestionsDir, "timelog-generator");
  const beadsDir = path.join(suggestionsDir, "beads-task-scheduler");

  assert.ok((await readdir(timelogDir)).length > 0);
  assert.ok((await readdir(beadsDir)).length > 0);

  // Partial archive: archive only critical for timelog-generator
  // timelog-generator/bug.jsonl remains, so timelogDir should NOT be removed
  const archivedCrit = await archiveSkillSuggestions(suggestionsDir, "timelog-generator", "critical");
  assert.equal(archivedCrit.length, 1);
  const remainingTimelogFiles = await readdir(timelogDir);
  assert.deepEqual(remainingTimelogFiles, ["bug.jsonl"]);

  // Full archive of remaining category for timelog-generator
  // Now timelogDir becomes empty and should be cleaned up!
  const archivedBug = await archiveSkillSuggestions(suggestionsDir, "timelog-generator", "bug");
  assert.equal(archivedBug.length, 1);

  // Verify timelogDir no longer exists
  let timelogDirExists = true;
  try {
    await readdir(timelogDir);
  } catch (err) {
    if (err.code === "ENOENT") timelogDirExists = false;
  }
  assert.equal(timelogDirExists, false);

  // Archive all remaining
  const archivedAll = await archiveSkillSuggestions(suggestionsDir, "all");
  assert.equal(archivedAll.length, 1);

  // Verify beadsDir is also cleaned up
  let beadsDirExists = true;
  try {
    await readdir(beadsDir);
  } catch (err) {
    if (err.code === "ENOENT") beadsDirExists = false;
  }
  assert.equal(beadsDirExists, false);

  // Verify only archive directory remains in suggestionsDir
  const remainingInBase = await readdir(suggestionsDir);
  assert.deepEqual(remainingInBase, ["archive"]);
});

test("skill-suggestions.mjs --month filters archived suggestions by month batch", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestionsScript = path.resolve(import.meta.dirname, "..", "skill-suggestions.mjs");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  // Seed two distinct archived month batches
  const archiveDir202607 = path.join(suggestionsDir, "archive", "2026-07");
  const archiveDir202608 = path.join(suggestionsDir, "archive", "2026-08");
  await mkdir(archiveDir202607, { recursive: true });
  await mkdir(archiveDir202608, { recursive: true });

  const itemJuly = {
    id: "sug_20260715_100000_111111",
    timestamp: "2026-07-15T10:00:00.000Z",
    skill: "timelog-generator",
    priority: "critical",
    category: "critical",
    summary: "July critical issue",
    agent: "gemini",
    session_id: "sess-july",
  };

  const itemAugust = {
    id: "sug_20260815_100000_222222",
    timestamp: "2026-08-15T10:00:00.000Z",
    skill: "beads-task-scheduler",
    priority: "high",
    category: "bug",
    summary: "August bug issue",
    agent: "claude",
    session_id: "sess-august",
  };

  await writeFile(
    path.join(archiveDir202607, "timelog-generator_critical_2026-07-15T10-00-00-000Z.jsonl"),
    JSON.stringify(itemJuly) + "\n",
    "utf8"
  );
  await writeFile(
    path.join(archiveDir202608, "beads-task-scheduler_bug_2026-08-15T10-00-00-000Z.jsonl"),
    JSON.stringify(itemAugust) + "\n",
    "utf8"
  );

  // 1. readArchivedSuggestions with month parameter
  const allArchived = await readArchivedSuggestions(suggestionsDir);
  assert.equal(allArchived.length, 2);

  const julyOnly = await readArchivedSuggestions(suggestionsDir, "2026-07");
  assert.equal(julyOnly.length, 1);
  assert.equal(julyOnly[0].id, "sug_20260715_100000_111111");
  assert.equal(julyOnly[0].summary, "July critical issue");

  const augustOnly = await readArchivedSuggestions(suggestionsDir, "2026-08");
  assert.equal(augustOnly.length, 1);
  assert.equal(augustOnly[0].id, "sug_20260815_100000_222222");
  assert.equal(augustOnly[0].summary, "August bug issue");

  const nonExistentMonth = await readArchivedSuggestions(suggestionsDir, "2026-09");
  assert.deepEqual(nonExistentMonth, []);

  // 2. CLI --month with --json
  const outJulyJson = execFileSync(
    "node",
    [suggestionsScript, "--month", "2026-07", "--json"],
    { encoding: "utf8", env }
  );
  const parsedJulyJson = JSON.parse(outJulyJson);
  assert.equal(parsedJulyJson.length, 1);
  assert.equal(parsedJulyJson[0].summary, "July critical issue");

  // 3. CLI --list-archived --month with formatted table output
  const outAugustText = execFileSync(
    "node",
    [suggestionsScript, "--list-archived", "--month", "2026-08"],
    { encoding: "utf8", env }
  );
  assert.match(outAugustText, /archive\/2026-08/);
  assert.match(outAugustText, /Archived reports:\s+1/);
  assert.match(outAugustText, /August bug issue/);
  assert.doesNotMatch(outAugustText, /July critical issue/);

  // 4. CLI --month for non-existent month
  const outEmptyMonth = execFileSync(
    "node",
    [suggestionsScript, "--month", "2026-09"],
    { encoding: "utf8", env }
  );
  assert.match(outEmptyMonth, /No archived suggestions found\./);
});

test("skill-suggestions.mjs --view-archive views specific snapshot, id, or skill", async (t) => {
  const workspace = await makeWorkspace(t);
  const suggestionsDir = path.join(workspace.home, "projects", "agent-skills", "suggestions");
  const suggestionsScript = path.resolve(import.meta.dirname, "..", "skill-suggestions.mjs");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  // Seed archived files in archive/2026-08
  const archiveDir = path.join(suggestionsDir, "archive", "2026-08");
  await mkdir(archiveDir, { recursive: true });

  const item1 = {
    id: "sug_20260820_120000_aaa111",
    timestamp: "2026-08-20T12:00:00.000Z",
    skill: "timelog-generator",
    priority: "critical",
    category: "critical",
    summary: "Scheduler deadlock",
    detail: "Locks are held indefinitely",
    suggested_fix: "Use timeout on lock",
    agent: "gemini",
    session_id: "conv-1",
  };

  const item2 = {
    id: "sug_20260820_120500_bbb222",
    timestamp: "2026-08-20T12:05:00.000Z",
    skill: "timelog-generator",
    priority: "low",
    category: "improvement",
    summary: "Add help examples",
    detail: "Examples in readme",
    agent: "claude",
    session_id: "conv-2",
  };

  const item3 = {
    id: "sug_20260821_140000_ccc333",
    timestamp: "2026-08-21T14:00:00.000Z",
    skill: "browser-app-debugger",
    priority: "high",
    category: "bug",
    summary: "Port 9222 connection refused",
    agent: "codex",
    session_id: "conv-3",
  };

  const filename1 = "timelog-generator_critical_2026-08-20T12-00-00-000Z.jsonl";
  const filename2 = "timelog-generator_improvement_2026-08-20T12-05-00-000Z.jsonl";
  const filename3 = "browser-app-debugger_bug_2026-08-21T14-00-00-000Z.jsonl";

  await writeFile(path.join(archiveDir, filename1), JSON.stringify(item1) + "\n", "utf8");
  await writeFile(path.join(archiveDir, filename2), JSON.stringify(item2) + "\n", "utf8");
  await writeFile(path.join(archiveDir, filename3), JSON.stringify(item3) + "\n", "utf8");

  // 1. findArchivedSuggestions by ID
  const foundById = await findArchivedSuggestions(suggestionsDir, "sug_20260820_120000_aaa111");
  assert.equal(foundById.length, 1);
  assert.equal(foundById[0].summary, "Scheduler deadlock");

  // 2. findArchivedSuggestions by filename
  const foundByFile = await findArchivedSuggestions(suggestionsDir, filename1);
  assert.equal(foundByFile.length, 1);
  assert.equal(foundByFile[0].id, "sug_20260820_120000_aaa111");

  // 3. findArchivedSuggestions by skill
  const foundBySkill = await findArchivedSuggestions(suggestionsDir, "timelog-generator");
  assert.equal(foundBySkill.length, 2);

  // 4. CLI --view-archive <id> --json
  const outIdJson = execFileSync(
    "node",
    [suggestionsScript, "--view-archive", "sug_20260820_120000_aaa111", "--json"],
    { encoding: "utf8", env }
  );
  const parsedId = JSON.parse(outIdJson);
  assert.equal(parsedId.length, 1);
  assert.equal(parsedId[0].id, "sug_20260820_120000_aaa111");
  assert.equal(parsedId[0].summary, "Scheduler deadlock");

  // 5. CLI --view-archive <filename> (formatted view)
  const outFilename = execFileSync(
    "node",
    [suggestionsScript, "--view-archive", filename3],
    { encoding: "utf8", env }
  );
  assert.match(outFilename, /Archived query:\s+browser-app-debugger_bug/);
  assert.match(outFilename, /Port 9222 connection refused/);

  // 6. CLI --view-archive <skill>
  const outSkill = execFileSync(
    "node",
    [suggestionsScript, "--view-archive", "timelog-generator", "--json"],
    { encoding: "utf8", env }
  );
  const parsedSkill = JSON.parse(outSkill);
  assert.equal(parsedSkill.length, 2);

  // 7. CLI --view-archive with non-matching query
  const outNotFound = execFileSync(
    "node",
    [suggestionsScript, "--view-archive", "unknown-item-123"],
    { encoding: "utf8", env }
  );
  assert.match(outNotFound, /No archived suggestions matching 'unknown-item-123'\./);
});


