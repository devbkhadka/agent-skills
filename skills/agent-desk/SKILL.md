---
name: agent-desk
description: Report progress to the user and get decisions from them through the Agent Desk page with the `agent-desk` shell CLI. Use at the start of every coding task (start a Session), to post a one-line Update at each milestone, and to raise an Ask (a question, or a risk before anything destructive or irreversible) instead of guessing; the call waits for the user's Reply. Works the same from Claude Code, Codex and AGY.
---

# Agent Desk

The user watches every agent on one local page (`http://127.0.0.1:4318`). You talk to it only
through the `agent-desk` shell command. It never prompts: it prints one plain-text block to stdout
and exits with a code that tells you what to do next.

Words have one meaning here: a **Workstream** is the unit of work (usually `<repo>:<branch>`), a
**Session** is your run, an **Update** is a one-line progress note, an **Ask** is something that needs
the user (kind `question` or `risk`), a **Reply** is the user's response (`answer` or `clarify`), and a
**Detail** is text you add to the same Ask after a `clarify`.

Artifact review uses its own words, never Reply or Detail. An **Artifact** is a markdown file you
submit for review, identified by `art_<n>`. A **Revision** is one immutable snapshot of it, taken
each time you submit. A **Thread** is a conversation anchored to a line range of one Revision (or to
the whole Artifact), `thr_<n>`. A **Comment** is one message in a Thread, from `user` or `agent`. A
**Review** is the user's submission of their Comments on an Artifact, with a verdict: `approve` or
`changes`.

## Commands

```bash
agent-desk start --agent claude|codex|agy --title "short title of the task" [--workstream NAME]
agent-desk update "one-line status" [--body TEXT]
agent-desk ask "question" [--risk] [--choices a,b,c] [--no-wait]
agent-desk detail <ask-id> "more detail"
agent-desk wait <ask-id>
agent-desk inbox
agent-desk withdraw <ask-id...>
agent-desk withdraw --all
agent-desk withdraw art_<n...>
agent-desk submit <path> [--title T] [--kind K] [--no-wait]
agent-desk reviews [art_<n>] [--wait]
agent-desk comment <thr_id> "text" [--resolve] [--wait]
agent-desk end [--summary TEXT]
```

Every command except `start` also takes `--session ID`.

## 1. Start a Session

Run `agent-desk start` once, before any other call. Pass `--agent` for the harness you are in. On
`main`/`master` or outside git, pass `--workstream <short-name>` from your task; otherwise the
Workstream is `<repo>:<branch>`.

`start` prints the Session id (`ses_<n>`), then the Workstream line, then any **open Asks left by
earlier Sessions** of this Workstream (`ask_<n> (kind): text`). Any Session of the Workstream may
pick one up: run `agent-desk wait <ask-id>` when its answer matters to your task, or
`agent-desk withdraw <ask-id>` when it is no longer needed. It also lists Artifacts with unreceived Reviews
(`art_<n> rN status: title (path)`); `agent-desk reviews` fetches them (section 7).

**Which Session a call speaks for:** `--session ID` > `$AGENT_DESK_SESSION` > the agent's own session
id variable (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`, `ANTIGRAVITY_CONVERSATION_ID`), which
`start` maps to your Session. So later calls normally need no flag. If `start` prints
`No agent session id in this shell: pass --session ses_<n> ...`, pass `--session ses_<n>` on every
later call: `export` does not survive between your shell calls.

## 2. Post an Update

Prefix milestone Updates with `[k/n]` step progress or a phase tag so the user can see progress at
a glance on the Agent Desk page:
`agent-desk update "[2/4] Tests green; starting the migration"`.

Post an Update at every phase transition (planning done, implementation slice complete, verification
started/passed, blocker hit, work handed off). Put verification output or multi-line context in
`--body`. An Update never gets a Reply. Don't wait after it.

## 3. Raise an Ask

Ask instead of guessing when only the user can decide and the choice changes the outcome:

- `agent-desk ask "JWT or session cookies?" --choices jwt,cookies` for a **question**. Always pass
  `--choices a,b,c` (2–4 short options) whenever the decision is between known options so the user
  can answer in one click on the page without typing.
- `agent-desk ask --risk "Dropping table orders_old deletes 2 GB of data. Go ahead?"` before anything
  destructive or irreversible, or when you find a **risk** the user must know about. The user can
  answer a risk with **Accept**, **Stop** or free text.

The call waits for the Reply (up to 9 minutes) and then exits. Act on its exit code:

| Exit | Meaning | Next step |
| :-- | :-- | :-- |
| `0` | answered | stdout is the answer. Act on it. (`start`, `update`, `inbox`, `withdraw`, `end` and `ask --no-wait` also exit 0 on success.) |
| `1` | error | Read the message. `Unknown session` → run `agent-desk start` (or pass `--session`). Server not reachable → tell the user the desk is down and carry on without it; do not retry in a loop. `a newer agent-desk wait <ask-id> took over this wait` → another call now holds that Ask; do not re-run this one. |
| `2` | timed out, still waiting | The Ask stays open. Run `agent-desk wait <ask-id>` again right away (the id is in the message). Never re-ask the same thing. |
| `3` | clarify | stdout is the user's clarify text. Answer it on the same Ask: `agent-desk detail <ask-id> "..."`, which waits again. Loop on its exit code. |
| `4` | withdrawn or closed | The Ask was withdrawn, or the user closed its Workstream. No answer is coming. Don't re-ask; continue without it or stop that line of work. |

## 4. Long-running commands, per agent

`ask`, `wait`, `detail`, `submit` (without `--no-wait`), `reviews --wait` and `comment --wait` block for up to 9 minutes (540 s), below every agent's shell limit.

- **Claude Code:** run them with the Bash tool parameter `timeout: 600000`. Without it the Bash tool
  moves the command to the background after 120 s; you are then notified when it completes, and you
  read the stdout and exit code from that notice.
- **Codex:** `exec_command` yields while the command runs. Keep polling the same process (empty
  `write_stdin`, `yield_time_ms` up to 300000) until it exits, then read stdout and the exit code. Do
  not start a second `wait` for the same Ask meanwhile: it takes over and the first exits 1.
- **AGY:** `run_command` goes to the background. Do other work or end the turn; you are notified
  when the task completes. Read stdout and the exit code from the completion notice or task log.
  Headless AGY (`agy -p`) needs a persisted allow rule for the `agent-desk` command.

**Long waits.** If you have other useful work, raise the Ask with `--no-wait`: it prints the
`ask-id` and exits 0 at once. Carry on, then run `agent-desk inbox` (or `agent-desk wait <ask-id>`)
later. `inbox` prints every answer in your Workstream not yet received, whichever Session raised the
Ask, and marks each received.

## 5. Withdraw Asks you no longer need

Proactively run `agent-desk withdraw <ask-id>` right away whenever an open `--no-wait` Ask (or an Ask
carried over from an earlier Session) is answered by code inspection or made obsolete by a change in
plan, so irrelevant open Asks never clutter the desk.

- `agent-desk withdraw ask_3 ask_4` withdraws those Asks.
- `agent-desk withdraw --all` withdraws every open Ask in your Workstream. Use it only when the user
  asks for it.

It prints `Withdrawn: <ids>` and one `Skipped <id> (<status>)` line per Ask that was already
answered, received or withdrawn, and exits 0. Any call still waiting on a withdrawn Ask exits 4.

## 6. End the Session

`agent-desk end --summary "Migrated orders; 2 follow-ups in PR"` when your task is done. Open Asks
stay open on the desk for a later Session to `wait` on or `withdraw`. Later calls for an ended
Session exit 1; `agent-desk start` makes a new one.

## 7. Submit an Artifact for review

Submit a markdown file the user should read and judge before you go on: a plan, a code-review
report, or a test flow written as markdown. Use an Update for progress and an Ask for a decision;
an Artifact is for a document the user reviews in full. Submit the file's path and the CLI uploads
its content.

```bash
agent-desk submit dev-plans/foo/PLAN.md --title "Rollout plan" --kind plan
```

The first line is `art_<n> r<N> to_review`, with ` (new)` for a new Artifact or ` (unchanged)` when
the content is identical (no new Revision). Then the call waits for a Review. It exits:

| Exit | Meaning | Next step |
| :-- | :-- | :-- |
| 0 | approved | stdout is `approved art_<n> rev <N>` plus any non-blocking Comments. Carry on. |
| 3 | changes requested | stdout is the Comment block (below). Act on it, then submit again. |
| 2 | timed out, still waiting | The Artifact stays open. Run `agent-desk reviews art_<n> --wait` right away (the id is in the message). Never re-submit to keep waiting. |
| 4 | withdrawn or closed | The user withdrew the Artifact, or closed its Workstream. No Review is coming. Continue without it. |
| 1 | error | Read the one-line reason. Not `.md` or `.markdown`, over 1 MB, not UTF-8, or unknown Session (run `agent-desk start`). `a newer agent-desk reviews ... took over` means another call holds the wait; do not re-run this one. |

The Comment block names each Thread with its id, line range and state, then the quoted lines it is
about, then the user's text:

```
Review rev_2 of art_3 (/abs/PLAN.md): changes
thr_7 L12-18 open outdated (quote from r1)
  > ## Rollout
  > Ship behind a flag …
  user: Why a flag? …
thr_8 (whole artifact) open
  user: Add a rollback section.
```

Act on each Thread in one of three ways:

- **Change the plan.** Edit the file, then run `agent-desk submit <path>` again on the same path. It
  adds the next Revision and the user sees one Artifact with history.
- **Answer and close.** `agent-desk comment thr_7 "Switched to branch deploy in §3" --resolve`.
- **Ask back.** `agent-desk comment thr_7 "Does staging have a slot?" --wait` keeps the Thread open
  and waits for the user's answer. Exit 0 prints their text; exit 4 means they resolved the Thread;
  exit 2 means run `agent-desk comment thr_7 --wait` again.

Resolving every Thread does not approve the Artifact. Only the user approves. Review traffic is never a **Reply** or a **Detail**; those belong to Asks only.

To fetch Reviews without a listener, run `agent-desk reviews` (every unreceived Review in your
Workstream, marked received) or `agent-desk reviews art_<n>`. Add `--wait` to block as `submit` does.
Withdraw an obsolete Artifact with `agent-desk withdraw art_<n>`.

## Setup (once per machine)

- Server and CLI live in `~/projects/tools/agent-desk`: `npm run build`, `npm start` (port 4318;
  `AGENT_DESK_PORT`, `AGENT_DESK_DB`), and `npm link` to put `agent-desk` on `PATH`.
- A desk on another address: set `AGENT_DESK_URL` for the CLI.
