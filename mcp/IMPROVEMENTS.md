# MCP Registry — Review Findings and Fix Spec

Review of commit `228d09f` (MCP server registry). Three parallel reviews: correctness,
security, agent compatibility. Every path claim below was re-verified against this machine.

Glossary — terms new to this work are marked (new); the rest come from the codebase.

| Term | Meaning |
| --- | --- |
| tracking repo | the git repo at `~/.agents/mcp-servers/` (`AGENT_MCP_HOME`) holding `installed.json` and `backups/` |
| managed entry | an `mcpServers` entry this tool wrote, recorded in `installed.json` |
| unmanaged entry | an `mcpServers` entry with the same key that the tool did not write |
| agent adapter (new) | per-agent path + serialize/parse/entry-shape hooks, replacing the single shared JSON writer |
| origin backup (new) | the first backup taken of a config, never overwritten by later runs |

## A. Agent compatibility — three of four agents write to files no agent reads

`writeJsonFileAtomic` does `mkdir -p` then writes, so a wrong path **silently succeeds**:
install creates a file nothing reads, reports success, and the server never appears.
`hasServerInConfig` reads the same phantom path, so status and uninstall agree with it.

Verified on this machine: `~/.claude.json` exists (65 KB); `~/.claude/claude.json` does not.
`~/.codex/config.toml` exists (4.6 KB, mode 600) with `[mcp_servers.node_repl]`;
`~/.codex/config.json` does not. `~/.gemini/config/mcp_config.json` exists and is a
different file from `~/.gemini/settings.json`.

| Agent | Code does | Correct | Verdict |
| --- | --- | --- | --- |
| Claude global | `~/.claude/claude.json` | `~/.claude.json` (honor `CLAUDE_CONFIG_DIR`) | wrong |
| Claude project | `<root>/.mcp.json` | same | correct |
| Claude url entry | `{url, headers}` | `type` is **required** for url servers | wrong |
| Codex global | `~/.codex/config.json` | `~/.codex/config.toml`, `[mcp_servers.<name>]` | wrong |
| Codex project | `<root>/.codex/config.json` | `<root>/.codex/config.toml` | wrong |
| Gemini global/project | `~/.gemini/settings.json`, `.gemini/settings.json` | same | correct |
| Gemini http entry | `{url}` | `httpUrl` is HTTP streaming; `url` means SSE | wrong |
| AGY | folded into the Gemini entry | `~/.gemini/config/mcp_config.json`, no project scope | wrong |
| OMP global/project | `~/.omp/agent/mcp.json`, `<root>/.omp/mcp.json` | same | correct |

**AGY is a distinct agent, not an alias for Gemini.** Split it out with its own adapter.
Remote AGY servers use `serverUrl` (a later build accepts `url`; prefer `serverUrl`).
`disabled: true` is its off switch.

> Still open (`agent-skills-mcp-agy-alias-k6o`): the adapter exists and is unit-tested, but no
> CLI path reaches it. `agentAliases` in `scripts/lib/common.mjs` maps `agy` onto `gemini` and
> `parseAgents` collapses the two, so `--agents agy` writes `~/.gemini/settings.json` and never
> touches `~/.gemini/config/mcp_config.json`. The alias is *correct* for the skills registry,
> where AGY and Gemini share a directory, and wrong for MCP, where they are separate files —
> one shared `parseAgents` serving both registries is the actual defect. The install output
> also mislabels a Gemini-only write as "Gemini / AGY".

Codex must never be written as JSON at the `.toml` path — that destroys a real 4.6 KB
config on first install. It needs a TOML serializer that round-trips unrelated tables.

Correct entry shapes:

```json
// Claude — ~/.claude.json and <root>/.mcp.json ("type" required for url servers)
{ "mcpServers": {
  "srv":  { "type": "stdio", "command": "npx", "args": ["-y", "s"], "env": { "K": "v" } },
  "http": { "type": "http",  "url": "https://example.com/mcp", "headers": {} }
} }
```

```toml
# Codex — ~/.codex/config.toml
[mcp_servers.srv]
command = "npx"
args = ["-y", "s"]
[mcp_servers.srv.env]
K = "v"

[mcp_servers.http]
url = "https://example.com/mcp"
bearer_token_env_var = "TOKEN"
```

```json
// Gemini — ~/.gemini/settings.json ("httpUrl" for HTTP, "url" for SSE)
{ "mcpServers": {
  "srv":  { "command": "npx", "args": ["-y", "s"], "env": {}, "timeout": 30000 },
  "http": { "httpUrl": "https://example.com/mcp", "headers": {} },
  "sse":  { "url": "https://example.com/sse" }
} }
```

```json
// AGY — ~/.gemini/config/mcp_config.json (global only)
{ "mcpServers": {
  "srv":    { "command": "npx", "args": ["-y", "s"], "env": {} },
  "remote": { "serverUrl": "https://example.com/sse" }
} }
```

OMP is unchanged; it infers `transport ?? (command ? "stdio" : url ? "http" : "stdio")`.

Accepted constraint: a Claude project-scoped server sits at "Pending approval" until the
user runs `claude` in that directory and accepts it. An installer cannot make it live.
Say so in the install output rather than implying the server is ready.

## B. Security — secrets reach git and the project tree

1. **Tokens committed to the tracking repo.** `backupConfigFile` copies the live agent
   config into `<trackingRoot>/backups/`, then `commitMcpTracking` runs `git add -A` and
   commits. A backup of `~/.claude.json` carries `accessToken` and `primaryApiKey`;
   reproduced with `git grep` finding `"CONTEXT7_API_KEY": "sk_LIVE…"` in a backup blob.
   Uninstalling a compromised server commits the key *before* removing it, so rotation
   never clears git history.
   Fix: keep `backups/` out of the worktree **and** redact `mcpServers.*.env` values plus
   known token fields before copying. Only `installed.json` needs versioning and it holds
   no secrets. Write a `.gitignore` with `backups/` in `ensureGitTrackingRepo`.

2. **Project scope wrote plaintext keys into the project tree.** Decision taken: at project
   scope, prefer a reference over a value, working down this ladder per agent and taking the
   first rung that applies. Global scope is unaffected — those files live under `$HOME`.
   1. Agent expands `${VAR}` (Claude Code, Gemini CLI) — write `${KEY}` and leave the value in
      the git-ignored `mcp/<name>/.env`.
   2. Codex `http` and `sse` servers — emit `bearer_token_env_var = "KEY"`, Codex's native
      env-var reference, instead of the value.
   3. No reference mechanism (Codex `stdio` env, OMP) — **ask** whether to store the value
      directly in the named config file, defaulting to no. On decline, skip the agent and say
      why. On accept, write it and warn, naming the file and recommending a `.gitignore` entry,
      but never edit `.gitignore` automatically. When stdin is not a TTY, skip the agent unless
      an explicit opt-in flag is passed.
   4. AGY is global-only, so say so plainly for a project-scope request naming it.

   Do not classify which variables are sensitive by name — treat every declared variable the
   same way at project scope, so nothing can leak through misclassification.

3. **`--set` accepts undeclared keys.** Every CLI override is copied into `resolvedEnv`
   regardless of `mcp.json`'s `env`. `--set NODE_OPTIONS="--require /tmp/evil.js"` lands in
   the config and executes in the MCP child on next agent start. Symmetrically, the
   `process.env` fallback means a PR adding `"env": {"AWS_SECRET_ACCESS_KEY": {}}` harvests
   that shell secret into agent configs and then into git.
   Fix: restrict `resolvedEnv` keys to `Object.keys(mcpDef.env)` and reject
   `NODE_OPTIONS`, `LD_PRELOAD`, `DYLD_*`, and `PATH` outright.

4. **Default permissions on credential files.** `writeJsonFileAtomic` passes no `mode`, so
   files land 0644 and dirs 0755; a 0600 `claude.json` came back 0644 after install, and
   `~/.codex/config.toml` is 0600 today. Write temp files `0o600`, `mkdir` `0o700`, `chmod`
   backups `0o600`, and preserve the destination's prior mode when the file already exists.

5. **`git add -A` in a foreign repo.** `ensureGitTrackingRepo` skips `git init` when `.git`
   exists, so a mistyped `AGENT_MCP_HOME` commits token-bearing backups into a repo that may
   have a push remote. Decision taken: write a marker file when the tool creates the repo and
   refuse to operate on a git repo lacking it. Scope `git add` to `installed.json`.

6. Recorded, lower severity: `--set KEY=VALUE` is visible in `ps` to any local user and is
   undocumented — add a caveat and prefer `.env`. The JSON parse error at `common.mjs:117`
   interpolates V8's message, which echoes ~20 chars of file content, so a corrupt
   `claude.json` spills a token fragment to stderr and CI logs; report the path, not the
   snippet. Backup filenames slug `/` away, so `my proj` and `myproj` collide and one
   project's backup overwrites the other's — hash the path instead.

Clean, no action: every child process uses `execFile`/`spawnSync` with an argv array and no
shell, so no crafted name crosses a shell. Install validates `--server` against the real
`mcp/` listing and `realpath`s `--project`, so traversal is closed. Install, uninstall, and
status print only names, versions, scopes, and paths — never values.

## C. Correctness

1. **Uninstall ignores ownership.** `removeServerFromConfig` checks only key presence and
   never imports `isServerManaged`. A hand-added `mcpServers.fetcher` is silently deleted,
   asymmetric with install's `--force` gate. Require `--force` to remove an unmanaged entry.
2. **Partial multi-agent failure leaves inconsistent state.** The manifest write and git
   commit run once *after* the whole agent loop. If agent 3 of 4 throws `EACCES`, agents 1–2
   are already mutated but unrecorded: they look unmanaged on the next run, and status
   reports "not installed" for a live server. Persist per agent as each one succeeds, or roll
   back on failure. Document whichever is chosen.
3. **Backups are overwritten every run.** Each install/uninstall re-copies over the previous
   backup, so it reflects the state before the *last* run, not before the tool ever touched
   the file. Keep an origin backup, written once.
4. **Git identity.** `user.name`/`user.email` are set only when `.git` is created, so a
   pre-existing repo without a git identity fails every commit — and the failure is swallowed
   to a `console.warn` while install still reports success. Set identity per commit and
   surface the failure.
5. Also: `git` is a hard prerequisite but undocumented; `chooseOne`/`chooseAgents` and the
   arg-parsing skeleton are duplicated verbatim between install and uninstall despite the
   commit consolidating helpers into `common.mjs`; `readEnvFileIfExists` does an inline
   `await import("node:fs/promises")` when a static import already exists; `parseDotenv`
   keeps inline `#` comments in values and parses `export FOO=bar` as key `"export FOO"`;
   `mcpServerExists` is exported but never called; uninstall never validates `--server`
   against known servers; removing the last entry leaves `"mcpServers": {}` behind.

Verified correct, no action: env priority genuinely resolves `--set` > local `.env` >
`process.env` > `.env.example` > `default`; required-var validation is fail-fast before any
mutation and treats empty string as missing consistently; `writeJsonFileAtomic` is a correct
same-directory temp-file-plus-rename with no truncation window.

## D. Test coverage

24 tests pass (16 skill, 8 MCP); the MCP file is correctly picked up by `node --test`.
Path sandboxing is sound — the fixture sets `HOME`, `GIT_CONFIG_GLOBAL`, and
`AGENT_MCP_HOME` to a `mkdtemp` dir, so a run cannot clobber the real `~/.claude.json`, and
fixture secrets are obviously fake.

Gaps to close:

- The fixture spreads real `process.env` into the child. Because of the `process.env`
  fallback, a developer who exports `DATABASE_URL` breaks the required-var test *and* writes
  their real connection string into fixture configs and git commits. Scrub to an allowlist.
- No test asserts secrets are absent from `backups/` or from `git log -p` in the tracking
  repo. That assertion is what would have caught B1.
- No test asserts file modes are 0600, which would have caught B4.
- No test asserts the real per-agent paths and entry shapes, which is why A went unnoticed:
  assert `~/.claude.json` (not `~/.claude/claude.json`), Codex TOML round-tripping an
  unrelated pre-existing table, Gemini `httpUrl` vs `url`, and AGY's own config file.
- No test covers partial multi-agent failure (make one target unwritable, assert the state is
  either fully rolled back or fully recorded).
