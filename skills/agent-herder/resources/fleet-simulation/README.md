# Fleet simulation — the e2e test

This is the end-to-end test for Agent Herder. It launches real CLI agents in real Herdr panes
against a real Beads graph, and drives them through the whole wave loop: rank, claim, lease,
spawn, watch, gate, close or roll back, re-rank. Only the *work* is fake — the fleet is not.

**The skill is what runs.** The harness does not implement the wave loop; a lead agent does,
by following `SKILL.md` from [herder_brief.md](herder_brief.md). The harness builds the
fixture, asserts what it can before any pane exists, and judges the outcome from durable bead
and lease state afterwards. An earlier version reimplemented the loop in Python, which meant
every improvement to the skill needed a matching edit here and a regression in the skill's own
prose was invisible to the test.

That trade is deliberate: the run is nondeterministic end to end, so a red run is a reason to
read the report before assuming a regression. The `errors` array in the lead's report exists
to make that reading cheap — see [herder_brief.md](herder_brief.md) for the contract it must
satisfy.

There is no offline mode and no faked pane. What each composed skill does in isolation is
already covered by that skill's own suite, and duplicating it here would only hide the seam:

```bash
python3 -m unittest discover -s ../../../resource-lease/tests
python3 -m unittest discover -s ../../../terminal-agent-runner/tests
python3 -m unittest discover -s ../../../beads-task-scheduler/tests
```

## Run it

```bash
python3 resources/fleet-simulation/run_simulation.py
```

For a command host that reaps foreground or shell-background children, use the portable launcher:

```bash
python3 resources/fleet-simulation/launch_simulation.py
```

It immediately prints a JSON record containing the detached PID, log, and lifecycle file. The
launched command is exactly `python3 resources/fleet-simulation/run_simulation.py`, in a new
process session with inherited `HERDR_ENV`, `HERDR_SOCKET_PATH`, and `HERDR_PANE_ID` removed.

### Observability & Progress Tracking

To check progress without reading massive logs into agent context:

```bash
python3 resources/fleet-simulation/status_summary.py --latest
# Or inspect specific run:
python3 resources/fleet-simulation/status_summary.py --run-dir /tmp/agent-herder-sim-run-XXXXX
```

### Prompt File for Agent Orchestration

To run the simulation via an autonomous assistant with strict context discipline, subagent
delegation for fixes, and editorial triage to `skill-improver`, reference:

```bash
# Agent instructions prompt file:
resources/fleet-simulation/SIMULATION_PROMPT.md
```

Needs `br`, `git`, and `herdr`; `HERDR_ENV` is deliberately not required. It runs `preflight.sh`
first and refuses to spawn anything if a CLI is unauthenticated or
out of quota, so a fleet problem is never reported as a herder problem.

The fleet is **un-attached**: the run starts a headless Herdr session of its own
(`agent-herder-sim-<pid>`), herds into it through `herd.sh --session`, and tears it down at the
end — the same path SKILL.md rule 1 requires of a real herder. Nothing lands in the session you
are attached to. Everything else it touches is disposable and created by the run: a temporary
fixture directory, a temporary Beads project, temporary lease and fleet state, and a Herdr
workspace of its own.

Expect it to take several minutes and to consume real CLI quota — it spawns a lead herder plus
seven workers, four waves deep and at most four at a time (`ready --limit 4`). Two environment variables tune it:
`AGENT_HERDER_SIM_LEAD_CLI` (default `claude`) picks the lead, and
`AGENT_HERDER_SIM_WORKER_CLI` (default `agy`) selects every worker. The worker CLI is written
into each bead's `agent_context.herder`, and the brief forbids substituting another CLI.
`AGENT_HERDER_SIM_TIMEOUT` (default 2400s) bounds how long the lead may take. Claude remains
selectable for a future run by setting both CLI variables to `claude` and explicitly setting
`AGENT_HERDER_SIM_ALLOW_CLAUDE=1`.

## The graph

[task_graph_agy.json](task_graph_agy.json) is the default graph. Set `AGENT_HERDER_GRAPH` to
another graph (such as [task_graph.json](task_graph.json)) when deliberately testing a different
task topology; the worker-CLI environment variable has final authority. Seven tasks become seven
beads with real dependencies and real `agent_context.herder` metadata.

| Slug | CLI | Scope | Depends on |
|---|---|---|---|
| `contract-api` | agy | `file:src/api.py`, `file:src/contracts.py` | — |
| `docs-audit` | agy | non-mutating objective | — |
| `smoke-a` | agy | `api:staging` in `shared:staging` | — |
| `smoke-b` | agy | `api:staging` in `shared:staging` | — |
| `schema-migrate` | agy | `file:db/schema.sql`, `db:test-postgres` | `contract-api` |
| `release-review` | agy | non-mutating objective | `contract-api`, `docs-audit`, `smoke-a`, `schema-migrate` |
| `followup-fix` | agy | `file:src/api.py` | `release-review` |

`shared:staging` has capacity two so `smoke-a` and `smoke-b` can hold it at once — the point of
the pair is two *different* CLIs on one shared resource in the same wave. Everything else is
capacity one and is leased by `spawn` itself.

The default topology uses AGY for every worker. Every task is therefore required to complete;
the simulation no longer treats a parked AGY worker as an acceptable partial result.

Every bead requires the same evidence: `pwd`. Nothing in the fixture is verified by running it
— the check exists to prove the gates carry a command string end to end, so the cheapest one
that a policy can approve unattended is the right one. Three constraints pin the string, and
every one of them was found by running real agents:

- **Quote-free.** A verification command containing double quotes is what a real agent embeds
  verbatim and unescaped in its handoff JSON, producing a payload the gate can only refuse —
  observed with Codex, and invisible to any test that writes its own handoff strings.
- **Allow-listed.** AGY's `auto` is accept-edits: file edits pass, but a shell command the
  default-deny policy does not recognise escalates to a human, and an unattended bead can then
  only park. `python3 verify.py` parked all three AGY beads of an earlier graph. The runner
  asserts that `pwd` classifies as `approve` *and* that `python3 verify.py` still escalates —
  the second half is why this is a cheap check, not a widened permission.
- **Single-token.** A pane that hard-wraps inside a JSON string loses the character at the wrap
  point, so `git status --short` came back as `git status--short` from two different agents and
  the bead gate refused it. A one-word command cannot wrap at a space.

## What it asserts

Four checks run before any pane exists, because they are the ones that should never cost an
agent to discover. They are pure translation and policy checks, so they stay in the harness.

| Check | Assertion |
|---|---|
| translation | `ready` returns exactly the four dep-free beads; the contract table turns each into runner flags; the read-only bead gets no writable mapping; no cwd escapes its repository; a shared resource keeps its own group. |
| task text | No bead's own prose trips the deny rules `spawn` applies to it. `spawn` classifies the task it is handed as if it were a command, so a semicolon in a *description* reads as compound shell syntax and the spawn is refused mid-wave. Every bead is checked, not just the ready set. |
| bead gate | `close` refuses a failing verification exit, and refuses evidence that omits a required command — the documented asymmetry, since `spawn` has no `--verification` flag for the pane gate to check. The bead stays open through both. |
| rollback | With both staging slots full, `spawn` is refused, no lease is left behind, `rollback` returns the bead to open and to the ready set, and the sibling bead is still ranked (rules 2 and 5). |
| un-attached | The run's session is not the herder's own socket and is registered with herdr, before a single worker is spawned. |

Then the lead herds the graph for real, and the harness judges what it left behind:

- **outcome over claim** — a bead counts as closed only if Beads says it is closed. If the
  lead's report claims a close that durable state does not show, the run fails on the
  discrepancy rather than trusting the report.
- **drain** — the run ends only when `ready` is empty and no lease survives in any group, and
  `release-review` and `followup-fix` must both be closed. Those two are waves 3 and 4: a run
  that stops before them has tested one wave and re-ranked nothing, which is the whole point.
  Any bead other than `smoke-b` failing to close fails the run.
- **the lead must report** — a run with no `SIMULATION_REPORT` line fails even if every bead
  closed, because nothing can be diagnosed from it.

Every error the lead recorded is echoed into the log under its phase, including ones it
recovered from. A rollback that worked is still evidence about the skill.

## Reading a run

`closed 7: ...` is the clean result. `parked 1: smoke-b` alongside it is a pass with detail, not
a silent partial: a real agent that never emits a well-formed handoff is a genuine observation
about that CLI, and parked beads are named in the final `NOTE:` line rather than hidden.

Because the lead is a real agent, a failure has one more question attached than it used to:
did the *skill* break, or did the *lead* misread it? Read the `herder error [...]` lines first.
Each carries the phase, the command as run, and its stderr, so a CLI-surface break (a flag that
no longer exists, a command that was removed) is distinguishable from a lead that skipped a
step. The brief asks the lead to say which of the two it believes it hit.

Two warnings from `complete` are expected and neither is a failure: trailing prose the evidence
does not cover, and a pane wrap that may have eaten a space inside a command string. The second
is a *may* by construction — the pane strips the space either way, so a wrap that landed
mid-token is indistinguishable from one that ate a space, and the warning errs toward
reporting.

A lead that never prints its report line fails the run even if the graph drained. That is
deliberate: an unreportable run is one nobody can diagnose, and treating it as a pass would
make the test quieter and less useful at exactly the moment it matters.
