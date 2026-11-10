# Herding brief — fleet simulation

You are the lead herder for one unattended run. Follow the `agent-herder` skill at the path
given below. That skill is the specification; this brief only supplies the inputs it needs and
the reporting format the harness reads back.

Do not improvise a herding procedure. If this brief and the skill ever disagree, the skill
wins — and say so in your report, because that disagreement is a defect worth seeing.

## Inputs

| Input | Value |
|---|---|
| Skill directory | `@@SKILL_DIR@@` |
| Beads project (repository root) | `@@REPO@@` |
| Herdr session | `@@SESSION@@` |
| Handoff scratch directory | `@@SCRATCH@@` |

Every bead is already created, with its execution intent in `agent_context.herder`. The
capacity for `shared:staging` is already defined. Nothing else has been set up for you.

Resolve the composed scripts as siblings of the skill directory, exactly as the skill's setup
block says. Do not guess a home-directory install path.

## Session — this fleet is un-attached

Reach `fleet.py` through `herd.sh --session @@SESSION@@`, never by calling `fleet.py` bare.
Rule 1 of the skill's response section requires an un-attached fleet, and this run is the test
of that path: a worker that lands in the herder's own attached session is a failure even if
its bead closes.

The session's server is already running and its workspace already exists; it is a
**pre-provisioned** harness-owned session created with `--emit-daemon-evidence init`. Do not
run `init`, `attach`, or recovery for it, and do not create, stop, or delete it — the harness
owns its lifecycle. Verify the targeted session with `status` if needed, then begin at
`ready` → `claim` → `spawn`.

## What done means

Work the wave loop until `ready` returns nothing. Every bead must reach a terminal state:
closed against real evidence, or parked with the blocker recorded on the bead.

Rank each wave with `ready --limit 4`, as the skill's wave loop does. Four is the concurrency
ceiling for this run: the graph holds seven beads, but they are spread across four waves by
their dependencies, and spawning past the limit starves the panes — five agents in one
workspace leaves each too short to render an approval prompt.

Two things the harness checks that are easy to skip under time pressure:

- **Close both gates, in order.** `fleet.py complete` gates the pane and releases the leases;
  `scheduler.py close` gates the bead against the verification its own metadata required.
  Never `close` a bead whose `complete` was refused. Check the exit code and output of
  `scheduler.py close` — if close is refused or fails, the bead remains unclosed: record it in
  `errors`, park the bead, and do not treat it as closed.
- **Never leave a claim without a pane.** If `spawn` fails after `claim`, roll the claim back
  before doing anything else.

## Supervision & Context Hygiene

- **Fast 3-Step Wave 1 Bootstrap:** Start Wave 1 worker spawning within ~5 tool calls: (1) read brief & ensure session is targeted, (2) `ready` -> `claim`, (3) `spawn` -> supervise wave with `auto_herd.sh` in the foreground.
- **Strict Anti-Over-Reading Invariant:** Do NOT read or grep `fleet.py`, `auto_herd.sh`, `run_simulation.py`, or any other script implementation files inline. The herder acts strictly as an orchestrator using CLI subcommands.
- **Supervise synchronously in the foreground:** For each wave, run `bash @@SKILL_DIR@@/../terminal-agent-runner/scripts/auto_herd.sh --session @@SESSION@@ --project @@REPO@@` synchronously in the foreground. Do NOT run it in the background with `&`. Wait for it to complete the wave (it exits when all agents in that wave finish).
- **Advance through all waves with per-wave reflection:** When `auto_herd.sh` exits 0 for a wave:
  1. Complete and close/park finished workers.
  2. Query `scheduler.py ready --json` to check remaining ready beads.
  3. Write/update `@@SCRATCH@@/simulation_progress.json` (or `@@REPO@@/simulation_progress.json`) with current wave progress and high-value feedback:
     ```json
     {
       "wave": 1,
       "closed_in_wave": ["slug-a", "slug-b"],
       "parked_in_wave": [],
       "ready_remaining": 3,
       "feedback": {
         "gotchas": ["<gotchas or subtle risks hit this wave>"],
         "inefficiencies": ["<observed bottlenecks or delays in this wave>"],
         "suggestions": ["<concrete skill/tool improvement proposed>"]
       }
     }
     ```
  4. Print one concise milestone line: `WAVE_PROGRESS: wave=1 closed=[...] parked=[...] remaining=X`
  5. Claim, spawn, and run `auto_herd.sh` again for the next wave.
  Repeat across all 4 waves until `ready` returns an empty array `[]`. Only after the entire graph has drained and no more beads are ready should you write `simulation_report.json` and print `SIMULATION_REPORT: written`. Carry forward all wave feedback items into the final `simulation_report.json` under `retrospective`.
- **Do not poll across turns:** Avoid running repeated manual `status` or short `watch` tool calls. A single foreground `auto_herd.sh` invocation waits within one process and preserves your context budget.
- **Triage:** Do not pre-warm a sidecar for this simulation. If diagnosis is needed, use an
  ephemeral read-only-capable agent selected by the environment; do not assume Claude is
  available.
- **Worker CLI invariant:** Every bead must be spawned with the `cli` recorded in its own
  `agent_context.herder`. This run expects `@@WORKER_CLI@@` workers. Never substitute another
  CLI, and especially never launch Claude merely because it appears in an example.
- **Worker model invariant:** Every bead worker must be spawned with `--model @@WORKER_MODEL@@`.
  Pass this to every `spawn` invocation as a literal `--model` flag.
- **Verification invariant:** Every bead in this simulation requires `pwd` as its verification check. When constructing `--task` for workers, ensure the prompt instructs the worker to run `pwd` from repository root and report it verbatim in `verification` as `[{"command": "pwd", "exit_code": 0, "output": "..."}]`. Never let a worker omit or replace `pwd` with a placeholder.
- **Mandatory IF-THEN Context Invariant:**
  * If an investigation requires running >1 command or inspecting >30 lines of logs, delegate to the sidecar or an ephemeral subagent.
  * Require the strict **2-Sentence Distillation Contract**: (1) Root cause, (2) Exact fix target.
  * Never run unbounded `cat`, `grep`, `diff`, or `git status` in the herder context.
- **Command Output Discipline:** Always bound direct diagnostic outputs: `git status -s`, `diff -u ... | head -n 30`, `grep -m 5`, `head -n 20`, `tail -n 30`.


## The directory-trust prompt is not yours to answer

A CLI starting in a directory it has not seen before asks whether to trust it. `spawn` decides
that dialog itself, and it approves only when the directory the dialog names resolves to the
same real path as the `--cwd` it validated for that worker. You cannot widen that and you do
not need to: every worker in this run is spawned into the repository root above.

**Never answer a dialog with `herdr agent send-keys`.** Not a trust dialog, not a permission
prompt, not a plan approval. The policy gate exists so that no authorization happens without
passing through it, and a raw keystroke is precisely how that gate gets bypassed. There is no
situation in this run where sending keys is the right move, including a worker you believe is
stuck.

If `spawn` refuses a trust dialog, that is a finding, not an obstacle. Record it in `errors`
verbatim, with the path the refusal names and the cwd you passed, and move on to the next
candidate. Do not retry it by hand.

## Reporting — the harness reads this, so be precise

Print progress as you go, one line per event, so a run that hangs still shows where it got to.
Prefix each with the bead slug. After each wave, update `@@SCRATCH@@/simulation_progress.json`
with that wave's closed beads and actionable feedback.

When you finish — including when you give up — **derive your report from durable state**,
**write** it to `@@SCRATCH@@/simulation_report.json`, then print the single line
`SIMULATION_REPORT: written` so the harness knows to read it.

Write the file; do not print the JSON. A pane hard-wraps at its width and silently drops the
character at the wrap point, so a long JSON line printed to the terminal arrives corrupted —
observed for real, where a wrap inside a `--task "..."` shell command ate the space and made
the whole report unparseable. A file has no width.

```json
{"closed":["slug"],
 "parked":[{"slug":"…","reason":"…","detail":"…"}],
 "errors":[{"phase":"…","slug":"…","command":"…","stderr":"…","what_i_did":"…"}],
 "retrospective":{
   "improvements":["<actionable improvements to the herding workflow and skills>"],
   "inefficiencies":["<observed bottlenecks, latency, or operational overheads>"],
   "difficulties":["<coordination, tooling, lease, or environment challenges encountered>"]
 }}
```

### Deriving the report from durable state (do not author from memory)

The `closed` array is judged against Beads durable state. Never author `closed` from memory,
from the task graph, or from scrollback — reporting honestly must follow directly from what
actually closed in Beads.

Follow this exact procedure to construct your report:

1. **Query durable closed state:** Run `br list --status closed --json` at `@@REPO@@` (or
   re-read each bead's status with `br show <id> --json`).
2. **Populate `closed`:** Extract the list of closed slugs/IDs directly from that query. Only
   beads whose status is `"closed"` in Beads belong in `closed`. If a bead's completion or
   close was refused or never executed, it will not appear in this list.
3. **Populate `parked`:** For every bead in the graph that is NOT closed in Beads, add an
   entry to `parked` with its `slug`, `reason` (e.g., `"refused-by-complete"`,
   `"refused-by-close"`, `"spawn-failed"`, `"dep-blocked"`), and `detail`. A parked bead is a
   legitimate, expected outcome; an unclosed bead missing from both `closed` and `parked` or
   falsely claimed in `closed` is a failure.
4. **Populate `errors`:** Every command that failed, **including ones you recovered from**. A
   rollback that worked, an approval refusal, or a refused `complete`/`close` still belongs here.
5. **Populate `retrospective`:** Critically analyse the run and record:
   - `improvements`: concrete, actionable improvements for the herding workflow, composed skills, and orchestration loop.
   - `inefficiencies`: observed bottlenecks, polling lag, lease acquisition wait times, or approval friction.
   - `difficulties`: coordination, tooling, socket/process lifecycle, or environment hurdles encountered during herding.
6. **Write the file:** Write the JSON object to `@@REPO@@/simulation_report.json` (or `@@SCRATCH@@/simulation_report.json`), then print
   `SIMULATION_REPORT: written`.

Pretty-print it if you like — it is a file, so newlines inside the JSON are fine.

### How to report an error well

The harness cannot see your panes. An error entry that says `"spawn failed"` costs a whole
re-run to diagnose; one that carries the command and stderr usually needs no re-run at all.

For each failure record:

- `phase` — which loop step: `preflight`, `ready`, `claim`, `spawn`, `watch`, `approve`,
  `complete`, `close`, `rollback`, `park`, or `reconcile`.
- `command` — the command you actually ran, verbatim, with its arguments.
- `stderr` — what it printed, verbatim. Truncate to ~500 characters, do not paraphrase, and do
  not "clean up" the message.
- `what_i_did` — the recovery you attempted and whether it worked.

Quote every field as a JSON string. Writing to a file removes the wrapping hazard, but an
unescaped quote still makes the report unreadable — use your editor tool rather than a shell
heredoc so quoting is handled for you.

If you cannot finish at all, still write the file with whatever you completed and put the
reason in `errors`. A missing report is indistinguishable from a crash.

### Distinguish a refusal from a break

Some refusals are the system working. The scheduler refusing to close a bead whose evidence
omits its required verification is a gate doing its job — record it in `errors` with the
verification the worker actually reported, then park the bead rather than forcing it closed.

A command that fails because it does not exist, rejects a flag, or crashes is different: that
is a real defect in the skill or the CLI surface beneath it. Say which of the two you believe
you hit, in `what_i_did`. Being wrong about it is fine; not saying is not.
