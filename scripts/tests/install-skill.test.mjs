import assert from "node:assert/strict";
import { lstat, mkdir, readFile, readdir, readlink, rm, symlink, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { commitAll, createRepo, forceRemove, makeWorkspace, run, unlockTree, writeSkill } from "./fixture.mjs";

async function exists(target) {
  try {
    await lstat(target);
    return true;
  } catch {
    return false;
  }
}

async function readManifest(workspace) {
  return JSON.parse(await readFile(path.join(workspace.installRoot, "installed.json"), "utf8"));
}

function install(workspace, args) {
  return run(workspace, "install-skill.mjs", args);
}

test("installs the transitive dependency closure and links every skill", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {
    top: { dependencies: ["middle"] },
    middle: { dependencies: ["leaf"] },
    leaf: {},
    unrelated: {},
  });

  const result = install(workspace, ["--skill", "top", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 0, result.stderr);

  for (const name of ["top", "middle", "leaf"]) {
    assert.ok(await exists(path.join(workspace.installRoot, name)), `${name} missing from install root`);
    const destination = path.join(workspace.home, ".claude", "skills", name);
    assert.ok((await lstat(destination)).isSymbolicLink(), `${name} not symlinked`);
    assert.equal(await readlink(destination), path.join(workspace.installRoot, name));
  }
  assert.ok(!(await exists(path.join(workspace.installRoot, "unrelated"))));

  const manifest = await readManifest(workspace);
  assert.deepEqual(Object.keys(manifest.skills).sort(), ["leaf", "middle", "top"]);
  for (const entry of Object.values(manifest.skills)) {
    assert.match(entry.version, /^[0-9a-f]{12}$/);
    assert.match(entry.installedAt, /^\d{4}-\d{2}-\d{2}T/);
    assert.equal(entry.source, workspace.repo);
  }
});

test("gives each skill its own version", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { top: { dependencies: ["leaf"] }, leaf: {} });
  await writeSkill(workspace, "top", { dependencies: ["leaf"], body: "# top v2\n" });
  commitAll(workspace, "touch top only");

  assert.equal(install(workspace, ["--skill", "top", "--scope", "global", "--agents", "claude"]).status, 0);
  const manifest = await readManifest(workspace);
  assert.notEqual(manifest.skills.top.version, manifest.skills.leaf.version);
});

test("detects dependency cycles instead of looping", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, {
    alpha: { dependencies: ["beta"] },
    beta: { dependencies: ["alpha"] },
  });

  const result = install(workspace, ["--skill", "alpha", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Dependency cycle detected: alpha -> beta -> alpha/);
  assert.ok(!(await exists(path.join(workspace.installRoot, "alpha"))));
});

test("names the dependent and the missing dependency", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: { dependencies: ["ghost"] } });

  const result = install(workspace, ["--skill", "alpha", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /"alpha" depends on "ghost"/);
});

test("refuses to install when a skill in the closure is dirty", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { top: { dependencies: ["leaf"] }, leaf: {} });
  await writeFile(path.join(workspace.repo, "skills", "leaf", "notes.md"), "untracked\n", "utf8");

  const result = install(workspace, ["--skill", "top", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Uncommitted changes/);
  assert.match(result.stderr, /skills\/leaf\/notes\.md/);
  // Aborts the whole closure rather than installing part of it.
  assert.ok(!(await exists(path.join(workspace.installRoot, "top"))));
  assert.ok(!(await exists(path.join(workspace.installRoot, "leaf"))));
});

test("fails clearly when a skill has never been committed", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  await writeSkill(workspace, "fresh", {});
  // Stage it so the tree is clean but the folder has no commit of its own yet.
  const result = install(workspace, ["--skill", "fresh", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Uncommitted changes|never been committed/);
});

test("skips the copy when the version is unchanged and --reinstall forces it", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  const args = ["--skill", "alpha", "--scope", "global", "--agents", "claude"];
  assert.equal(install(workspace, args).status, 0);

  const marker = path.join(workspace.installRoot, "alpha", "MARKER");
  await unlockTree(path.join(workspace.installRoot, "alpha"));
  await writeFile(marker, "local\n", "utf8");

  const second = install(workspace, args);
  assert.equal(second.status, 0, second.stderr);
  assert.match(second.stdout, /already installed at [0-9a-f]{12} \(skipping copy\)/);
  assert.ok(await exists(marker), "copy should have been skipped");
  // Symlinks are still reconciled on a skip.
  assert.match(second.stdout, /already linked/);

  const third = install(workspace, [...args, "--reinstall"]);
  assert.equal(third.status, 0, third.stderr);
  assert.ok(!(await exists(marker)), "--reinstall should re-copy the skill");
});

test("re-copies when the repository version moves on", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  const args = ["--skill", "alpha", "--scope", "global", "--agents", "claude"];
  assert.equal(install(workspace, args).status, 0);
  const first = (await readManifest(workspace)).skills.alpha.version;

  await writeSkill(workspace, "alpha", { body: "# alpha v2\n" });
  commitAll(workspace, "alpha v2");
  assert.equal(install(workspace, args).status, 0);

  const manifest = await readManifest(workspace);
  assert.notEqual(manifest.skills.alpha.version, first);
  assert.equal(await readFile(path.join(workspace.installRoot, "alpha", "SKILL.md"), "utf8"), "# alpha v2\n");
});

test("agy and gemini resolve to one Gemini link", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });

  const result = install(workspace, ["--skill", "alpha", "--scope", "global", "--agents", "agy,gemini"]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal((result.stdout.match(/Gemini \/ AGY:/g) ?? []).length, 1);
  assert.deepEqual(await readdir(path.join(workspace.home, ".gemini", "skills")), ["alpha"]);
  assert.ok((await lstat(path.join(workspace.home, ".gemini", "skills", "alpha"))).isSymbolicLink());
});

test("refuses a real directory at the destination unless --force", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  const destination = path.join(workspace.home, ".claude", "skills", "alpha");
  await mkdir(destination, { recursive: true });
  await writeFile(path.join(destination, "SKILL.md"), "# stale frozen copy\n", "utf8");

  const args = ["--skill", "alpha", "--scope", "global", "--agents", "claude"];
  const blocked = install(workspace, args);
  assert.equal(blocked.status, 1);
  assert.match(blocked.stderr, /existing directory/);
  assert.ok((await lstat(destination)).isDirectory());
  assert.ok(!(await lstat(destination)).isSymbolicLink(), "the real directory must be left untouched");

  const forced = install(workspace, [...args, "--force"]);
  assert.equal(forced.status, 0, forced.stderr);
  assert.ok((await lstat(destination)).isSymbolicLink());
});

test("replaces a symlink that points somewhere else without --force", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  const skillsDirectory = path.join(workspace.home, ".claude", "skills");
  await mkdir(skillsDirectory, { recursive: true });
  const stale = path.join(workspace.root, "stale");
  await mkdir(stale, { recursive: true });
  await symlink(stale, path.join(skillsDirectory, "alpha"), "dir");

  const result = install(workspace, ["--skill", "alpha", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /relinked/);
  assert.equal(
    await readlink(path.join(skillsDirectory, "alpha")),
    path.join(workspace.installRoot, "alpha"),
  );
});

test("project scope links into the project while copying to the shared install root", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { top: { dependencies: ["leaf"] }, leaf: {} });
  const project = path.join(workspace.root, "consumer");
  await mkdir(project, { recursive: true });

  const result = install(workspace, [
    "--skill", "top", "--scope", "project", "--project", project, "--agents", "claude,omp",
  ]);
  assert.equal(result.status, 0, result.stderr);
  for (const name of ["top", "leaf"]) {
    assert.ok((await lstat(path.join(project, ".claude", "skills", name))).isSymbolicLink());
    assert.ok((await lstat(path.join(project, ".omp", "skills", name))).isSymbolicLink());
    assert.ok(await exists(path.join(workspace.installRoot, name)));
  }
  // Global agent directories are untouched by a project-scope install.
  assert.ok(!(await exists(path.join(workspace.home, ".claude", "skills"))));
});

test("rejects unknown agents and unknown skills", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });

  const badAgent = install(workspace, ["--skill", "alpha", "--scope", "global", "--agents", "nope"]);
  assert.equal(badAgent.status, 1);
  assert.match(badAgent.stderr, /Unknown agent\(s\): nope/);

  const badSkill = install(workspace, ["--skill", "ghost", "--scope", "global", "--agents", "claude"]);
  assert.equal(badSkill.status, 1);
  assert.match(badSkill.stderr, /Unknown skill: ghost/);
});

test("status reports every install state without prompting", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {}, beta: {}, gamma: {} });
  assert.equal(install(workspace, ["--skill", "alpha", "--scope", "global", "--agents", "claude"]).status, 0);
  assert.equal(install(workspace, ["--skill", "beta", "--scope", "global", "--agents", "claude"]).status, 0);

  // beta moves ahead in the repo; gamma is never installed; delta only exists installed.
  await writeSkill(workspace, "beta", { body: "# beta v2\n" });
  commitAll(workspace, "beta v2");
  await writeFile(path.join(workspace.repo, "skills", "gamma", "scratch.md"), "dirty\n", "utf8");
  const manifest = await readManifest(workspace);
  manifest.skills.delta = { version: "0123456789ab", installedAt: new Date().toISOString(), source: workspace.repo };
  await writeFile(path.join(workspace.installRoot, "installed.json"), JSON.stringify(manifest), "utf8");
  await mkdir(path.join(workspace.installRoot, "delta"), { recursive: true });

  const status = run(workspace, "skill-status.mjs", []);
  assert.equal(status.status, 0, status.stderr);
  assert.match(status.stdout, /^alpha\s+\S+\s+\S+\s+up to date$/m);
  assert.match(status.stdout, /^beta\s+\S+\s+\S+\s+update available$/m);
  assert.match(status.stdout, /^gamma\s+\S+\s+\S+\s+dirty in repo$/m);
  assert.match(status.stdout, /^delta\s+\S+\s+\S+\s+installed but missing from repo$/m);
});

test("status reports not installed for an untouched repository", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  const status = run(workspace, "skill-status.mjs", []);
  assert.equal(status.status, 0, status.stderr);
  assert.match(status.stdout, /^alpha\s+-\s+[0-9a-f]{12}\s+not installed$/m);
});

test("nothing is written outside the temporary workspace", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  assert.equal(install(workspace, ["--skill", "alpha", "--scope", "global", "--agents", "all"]).status, 0);
  const entries = await readdir(workspace.home);
  assert.deepEqual(entries.sort(), [".claude", ".codex", ".gemini", ".omp"]);
  await forceRemove(workspace.installRoot);
});

test("locks the installed copy read-only so an agent cannot edit it in place", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { top: { dependencies: ["leaf"] }, leaf: {} });

  const result = install(workspace, ["--skill", "top", "--scope", "global", "--agents", "claude"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /locked read-only: .*\/top$/m);

  // Every skill in the closure is locked, not just the requested one.
  for (const name of ["top", "leaf"]) {
    const directory = path.join(workspace.installRoot, name);
    assert.equal((await lstat(directory)).mode & 0o222, 0, `${name} directory still writable`);
    const skillFile = path.join(directory, "SKILL.md");
    assert.equal((await lstat(skillFile)).mode & 0o222, 0, `${name}/SKILL.md still writable`);
    await assert.rejects(writeFile(skillFile, "edited\n", "utf8"), { code: "EACCES" });
    await assert.rejects(writeFile(path.join(directory, "NEW.md"), "new\n", "utf8"), { code: "EACCES" });
  }

  // Reading through the agent symlink still works.
  const linked = path.join(workspace.home, ".claude", "skills", "top", "SKILL.md");
  assert.equal(await readFile(linked, "utf8"), "# top\n");
});

test("--writable leaves the installed copy editable", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });

  const result = install(workspace, ["--skill", "alpha", "--scope", "global", "--agents", "claude", "--writable"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /left writable: .*\/alpha$/m);
  await writeFile(path.join(workspace.installRoot, "alpha", "SKILL.md"), "edited\n", "utf8");
});

test("a locked copy does not block the next install", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  const args = ["--skill", "alpha", "--scope", "global", "--agents", "claude"];
  assert.equal(install(workspace, args).status, 0);

  await writeSkill(workspace, "alpha", { body: "# alpha v2\n" });
  commitAll(workspace, "alpha v2");

  const second = install(workspace, args);
  assert.equal(second.status, 0, second.stderr);
  assert.equal(await readFile(path.join(workspace.installRoot, "alpha", "SKILL.md"), "utf8"), "# alpha v2\n");
  assert.equal((await lstat(path.join(workspace.installRoot, "alpha", "SKILL.md"))).mode & 0o222, 0);

  // And --reinstall over the lock, too.
  assert.equal(install(workspace, [...args, "--reinstall"]).status, 0);
});

test("locks a copy that an earlier install left writable", async (t) => {
  const workspace = await makeWorkspace(t);
  await createRepo(workspace, { alpha: {} });
  const args = ["--skill", "alpha", "--scope", "global", "--agents", "claude"];
  assert.equal(install(workspace, [...args, "--writable"]).status, 0);

  const second = install(workspace, args);
  assert.equal(second.status, 0, second.stderr);
  assert.match(second.stdout, /already installed at [0-9a-f]{12} \(skipping copy\)/);
  assert.equal((await lstat(path.join(workspace.installRoot, "alpha", "SKILL.md"))).mode & 0o222, 0);
});
