import assert from "node:assert/strict";
import { lstat, mkdir, readFile, readdir, readlink, rm, symlink, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { commitAll, createRepo, forceRemove, makeWorkspace, run, unlockTree, writeSystemPrompt } from "./fixture.mjs";

async function exists(target) {
  try {
    await lstat(target);
    return true;
  } catch {
    return false;
  }
}

async function readManifest(workspace) {
  return JSON.parse(await readFile(path.join(workspace.promptsInstallRoot, "installed.json"), "utf8"));
}

function installPrompt(workspace, args) {
  return run(workspace, "install-prompt.mjs", args);
}

function promptStatus(workspace, args = []) {
  return run(workspace, "prompt-status.mjs", args);
}

test("installs system prompt and links for claude and gemini in replace mode", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-prompt": { body: "# Generic Rules\nRule 1: Orient before you edit.\n" },
  });

  const result = installPrompt(workspace, [
    "--prompt", "generic-prompt",
    "--scope", "global",
    "--agents", "claude,gemini",
    "--mode", "replace",
  ]);
  assert.equal(result.status, 0, result.stderr);

  const stagedPrompt = path.join(workspace.promptsInstallRoot, "generic-prompt", "system-prompt.md");
  assert.ok(await exists(stagedPrompt), "Staged prompt missing from install root");

  // Check Claude symlink
  const claudeDest = path.join(workspace.home, ".claude", "CLAUDE.md");
  assert.ok((await lstat(claudeDest)).isSymbolicLink(), "Claude destination not a symlink");
  assert.equal(await readlink(claudeDest), stagedPrompt);

  // Check Gemini / AGY symlink
  const geminiDest = path.join(workspace.home, ".gemini", "GEMINI.md");
  assert.ok((await lstat(geminiDest)).isSymbolicLink(), "Gemini destination not a symlink");
  assert.equal(await readlink(geminiDest), stagedPrompt);

  // Staged prompt is locked read-only
  const stat = await lstat(stagedPrompt);
  assert.equal(stat.mode & 0o222, 0, "Staged prompt should be read-only");

  // Manifest record
  const manifest = await readManifest(workspace);
  assert.ok(manifest.prompts["generic-prompt"]);
  assert.match(manifest.prompts["generic-prompt"].version, /^[0-9a-f]{12}$/);
});

test("backs up pre-existing regular file and creates .orig snapshot on first install", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-prompt": { body: "# Generic Rules\n" },
  });

  // Create an existing real file in ~/.claude/CLAUDE.md
  const claudeDir = path.join(workspace.home, ".claude");
  await mkdir(claudeDir, { recursive: true });
  const originalContent = "# Persona\nDev skims first.\n";
  await writeFile(path.join(claudeDir, "CLAUDE.md"), originalContent, "utf8");

  const result = installPrompt(workspace, [
    "--prompt", "generic-prompt",
    "--scope", "global",
    "--agents", "claude",
    "--mode", "replace",
  ]);
  assert.equal(result.status, 0, result.stderr);

  // Check .orig file
  const origFile = path.join(claudeDir, "CLAUDE.md.orig");
  assert.ok(await exists(origFile), "CLAUDE.md.orig was not created");
  assert.equal(await readFile(origFile, "utf8"), originalContent);

  // Check backup under backups/claude/
  const backupsDir = path.join(workspace.promptsInstallRoot, "backups", "claude");
  assert.ok(await exists(backupsDir), "Backups directory was not created");
  const backupEntries = await readdir(backupsDir);
  assert.ok(backupEntries.length > 0, "No backup file found");
  assert.match(backupEntries[0], /^CLAUDE\.md\..*\.bak$/);
  assert.equal(await readFile(path.join(backupsDir, backupEntries[0]), "utf8"), originalContent);

  // And target is now a symlink
  const claudeDest = path.join(claudeDir, "CLAUDE.md");
  assert.ok((await lstat(claudeDest)).isSymbolicLink());
});

test("compose mode preserves original persona and appends domain prompt", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-prompt": { body: "# Engineering Rules\nVerify behavior.\n" },
  });

  const claudeDir = path.join(workspace.home, ".claude");
  await mkdir(claudeDir, { recursive: true });
  const persona = "# Persona\nSenior Thought Partner.\n";
  await writeFile(path.join(claudeDir, "CLAUDE.md"), persona, "utf8");

  const result = installPrompt(workspace, [
    "--prompt", "generic-prompt",
    "--scope", "global",
    "--agents", "claude",
    "--mode", "compose",
  ]);
  assert.equal(result.status, 0, result.stderr);

  const composedPath = path.join(workspace.promptsInstallRoot, "active", "claude-generic-prompt.md");
  assert.ok(await exists(composedPath), "Composed file missing");

  const composedContent = await readFile(composedPath, "utf8");
  assert.ok(composedContent.includes("Senior Thought Partner."));
  assert.ok(composedContent.includes("Engineering Rules"));
  assert.ok(composedContent.includes("Verify behavior."));

  // Target symlinks to the active composed file
  const claudeDest = path.join(claudeDir, "CLAUDE.md");
  assert.ok((await lstat(claudeDest)).isSymbolicLink());
  assert.equal(await readlink(claudeDest), composedPath);
});

test("configures codex config.toml model_instructions_file and preserves other sections", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-prompt": { body: "# Codex Prompt\n" },
  });

  const codexDir = path.join(workspace.home, ".codex");
  await mkdir(codexDir, { recursive: true });
  const originalToml = `model = "gpt-5.6-terra"\nsandbox_mode = "danger-full-access"\n\n[network]\nallow_unix_sockets = ["/tmp"]\n`;
  await writeFile(path.join(codexDir, "config.toml"), originalToml, "utf8");

  const result = installPrompt(workspace, [
    "--prompt", "generic-prompt",
    "--scope", "global",
    "--agents", "codex",
  ]);
  assert.equal(result.status, 0, result.stderr);

  // Backup of config.toml was created
  const origToml = path.join(codexDir, "config.toml.orig");
  assert.ok(await exists(origToml), "config.toml.orig missing");
  assert.equal(await readFile(origToml, "utf8"), originalToml);

  // Read updated config.toml
  const updatedToml = await readFile(path.join(codexDir, "config.toml"), "utf8");
  assert.ok(updatedToml.includes("model_instructions_file = "));
  assert.ok(updatedToml.includes('model = "gpt-5.6-terra"'));
  assert.ok(updatedToml.includes("[network]"));

  // Active instructions link exists
  const activeCodexLink = path.join(workspace.promptsInstallRoot, "active", "codex-instructions.md");
  assert.ok((await lstat(activeCodexLink)).isSymbolicLink());
});

test("project scope links into project root", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-prompt": { body: "# Project System Prompt\n" },
  });

  const testProject = path.join(workspace.root, "test-project");
  await mkdir(testProject, { recursive: true });

  const result = installPrompt(workspace, [
    "--prompt", "generic-prompt",
    "--scope", "project",
    "--project", testProject,
    "--agents", "claude,codex,gemini",
  ]);
  assert.equal(result.status, 0, result.stderr);

  const claudeLink = path.join(testProject, "CLAUDE.md");
  const geminiLink = path.join(testProject, "GEMINI.md");
  const codexLink = path.join(testProject, "AGENTS.md");

  assert.ok((await lstat(claudeLink)).isSymbolicLink(), "project CLAUDE.md not a link");
  assert.ok((await lstat(geminiLink)).isSymbolicLink(), "project GEMINI.md not a link");
  assert.ok((await lstat(codexLink)).isSymbolicLink(), "project AGENTS.md not a link");
});

test("refuses to install when prompt working tree is dirty", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-prompt": { body: "# Prompt v1\n" },
  });

  // Introduce uncommitted change
  await writeFile(path.join(workspace.repo, "system-prompts", "generic-prompt", "uncommitted.txt"), "dirty\n", "utf8");

  const result = installPrompt(workspace, [
    "--prompt", "generic-prompt",
    "--scope", "global",
    "--agents", "claude",
  ]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Uncommitted changes block the install/);
});

test("prompt-status reports status correctly", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-prompt": { body: "# Prompt v1\n" },
  });

  const res1 = promptStatus(workspace);
  assert.equal(res1.status, 0);
  assert.match(res1.stdout, /generic-prompt/);
  assert.match(res1.stdout, /not installed/);

  installPrompt(workspace, [
    "--prompt", "generic-prompt",
    "--scope", "global",
    "--agents", "claude",
  ]);

  const res2 = promptStatus(workspace);
  assert.equal(res2.status, 0);
  assert.match(res2.stdout, /up to date/);
  assert.match(res2.stdout, /claude/);
});

test("suggest.py recognizes system-prompts as managed targets", async (t) => {
  const { execFileSync } = await import("node:child_process");
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-system-prompt": { body: "# Generic Rules\n" },
  });

  const suggestionsDir = path.join(workspace.repo, "suggestions");
  const suggestScript = path.resolve(import.meta.dirname, "..", "..", "skills", "skill-improver", "scripts", "suggest.py");

  const env = {
    ...process.env,
    HOME: workspace.home,
    AGENT_SKILLS_HOME: workspace.repo,
    AGENT_SKILLS_SUGGESTIONS_DIR: suggestionsDir,
  };

  const output = execFileSync(
    "python3",
    [
      suggestScript,
      "--skill", "generic-system-prompt",
      "--summary", "Clarify database migration boundaries",
      "--category", "improvement",
      "--priority", "medium",
      "--agent", "gemini",
    ],
    { encoding: "utf8", env }
  );

  const parsed = JSON.parse(output);
  assert.equal(parsed.status, "recorded");
  assert.equal(parsed.skill, "generic-system-prompt");

  // Verify file was written to suggestions/generic-system-prompt/improvement.jsonl
  const targetFile = path.join(suggestionsDir, "generic-system-prompt", "improvement.jsonl");
  assert.ok(await exists(targetFile), "improvement.jsonl not created under suggestions/generic-system-prompt");
});

test("appends compile-time overrides for specific agents", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {}, {}, {
    "generic-prompt": {
      body: "# Core DDD Invariants\nBoundaries point inward.\n",
      overrides: {
        claude: "## Claude Overlay\nRun non-interactively.\n",
        codex: "## Codex Overlay\nSandbox rules apply.\n",
      },
    },
  });

  const result = installPrompt(workspace, [
    "--prompt", "generic-prompt",
    "--scope", "global",
    "--agents", "claude,codex",
    "--mode", "replace",
  ]);
  assert.equal(result.status, 0, result.stderr);

  // Check Claude target
  const claudeDest = path.join(workspace.home, ".claude", "CLAUDE.md");
  const claudeContent = await readFile(claudeDest, "utf8");
  assert.ok(claudeContent.includes("Core DDD Invariants"));
  assert.ok(claudeContent.includes("Claude Overlay"));
  assert.ok(!claudeContent.includes("Codex Overlay"), "Claude target should not leak Codex overlay");

  // Check Codex target
  const codexInstructions = path.join(workspace.promptsInstallRoot, "active", "codex-instructions.md");
  const codexContent = await readFile(codexInstructions, "utf8");
  assert.ok(codexContent.includes("Core DDD Invariants"));
  assert.ok(codexContent.includes("Codex Overlay"));
  assert.ok(!codexContent.includes("Claude Overlay"), "Codex target should not leak Claude overlay");
});

