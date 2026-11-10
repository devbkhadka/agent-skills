# Herder-to-Fleet Signalling Architecture: Non-Blocking Event Bus & Prompt Notification

## Executive Summary

The `agent-herder` orchestration framework manages concurrent worker agents running in isolated terminal panes driven by `terminal-agent-runner` (`fleet.py`, `herd.sh`, `auto_herd.sh`), `beads-task-scheduler` (`scheduler.py`), and `resource-lease` (`resource_pool.py`).

### The Problem
In multi-agent fleet operations, a lead herder (or supervisor process) and worker agents operate asynchronously. When a worker agent encounters an un-resolvable prompt (e.g. an escalated plan approval or high-risk tool call), hits a fatal runtime error, or completes its assigned task, the herder discovers this state only when it next polls the fleet.

### Motivating Evidence (2026-08-21 Real Run)
During the 2026-08-21 fleet simulation run (session `agent-herder-sim-93314`), worker `sim-docs-audit-bdl` was spawned in read-only mode (`approval_mode=plan`) and immediately encountered Claude's plan approval dialog (*"Ready to code? Here is Claude's plan..."*). Risk policy classified the prompt as `escalate` under default-deny. Because communication was tied to polling and turn-based supervision without an immediate event-driven notification path:
1. `sim-docs-audit-bdl` sat blocked in its pane for **approximately fourteen minutes** before the herder parked it.
2. Two downstream ready beads (`sim-schema-migrate-lpm` and `sim-release-review-d5n`) were starved and never spawned during that window.
3. The overall wave execution stalled despite available concurrency capacity.

### The Core Design Challenge
How can a herding agent and its fleet communicate so that:
1. **Neither blocks the other**: A worker never hangs waiting for an IPC handshake, and a supervisor never deadlocks waiting on an unresponsive worker.
2. **The herder learns promptly**: Events (blocked prompts, task completions, fatal errors) notify the herder immediately (<100ms) rather than on the next polling interval.
3. **Existing repository invariants remain inviolate**: The design must seamlessly coexist with `auto_herd.sh` supervision, `escalations.json` durable parking, `fleet.py status` telemetry, and Herdr 0.8.0's strict refusal of `recent-unwrapped` reads on non-idle agents (`agent_not_idle`).

---

## The Four Existing Repository Surfaces

Any signalling mechanism must integrate directly with the existing operational surfaces of this repository rather than proposing an isolated theoretical architecture.

```
+----------------------------------------------------------------------------------------------------+
|                                      HERDER SUPERVISION LAYER                                      |
|                                                                                                    |
|   +---------------------------------------+             +--------------------------------------+   |
|   | [Surface 1] auto_herd.sh Supervision  |             |     [Surface 3] fleet.py status      |   |
|   | - Bash loop wrapping watch & status   |             | - fleet_rows() scraping visible text |   |
|   | - Gated completion & scheduler close  |             | - Multi-sample settle verification   |   |
|   | - Blocked reason aggregation          |             | - Classifies prompts via policy      |   |
|   +-------------------+-------------------+             +-------------------+------------------+   |
|                       |                                                     |                      |
|                       v                                                     v                      |
|   +--------------------------------------------------------------------------------------------+   |
|   |                            [Surface 2] escalations.json Park Path                          |   |
|   |   - cmd_park() / cmd_escalations() with state_lock(tasks.json)                             |   |
|   |   - Records unapproved prompt subject, task context, and timestamps                        |   |
|   |   - Atomically releases held resource leases via resource_pool.py                          |   |
|   +--------------------------------------------------------------------------------------------+   |
+-------------------------------------------------|--------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
|                                       HERDR RUNTIME ENGINE (0.8.0)                                 |
|                                                                                                    |
|   +--------------------------------------------------------------------------------------------+   |
|   | [Surface 4] Herdr recent-unwrapped Restriction (agent_not_idle error)                      |   |
|   | - herdr agent read --source recent-unwrapped strictly FAILS while agent is working/blocked |   |
|   | - Only --source visible is valid on active/blocked panes                                   |   |
|   | - Outbox/event payloads must be self-contained; cannot force unwrapped pane re-reads       |   |
|   +--------------------------------------------------------------------------------------------+   |
+----------------------------------------------------------------------------------------------------+
```

### Surface 1: `auto_herd.sh` Supervision Loop
- **Implementation**: [`skills/terminal-agent-runner/scripts/auto_herd.sh`](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/auto_herd.sh#L98-L380)
- **Role**: Drives the unattended in-process lifecycle loop:
  1. Calls `herd.sh watch --until actionable --timeout 10`.
  2. Calls `herd.sh status --json` (instant snapshot) and iterates through `.agents[]`.
  3. Evaluates worker state: auto-approves policy-allowed prompts (`approve`), tracks transient frames across `MAX_SETTLE_POLLS=5` (`settling`), executes `herd.sh complete` and `scheduler.py close` on `idle`/`done` agents with valid handoff evidence, and halts with exit code `1` if all agents are blocked.
- **Signalling Requirement**: A new signalling layer must wake `auto_herd.sh` immediately when actionable events occur, eliminating the 10-second sleep floor while preserving its robust fallback and failure exit semantics.

### Surface 2: `escalations.json` Durable Park Path
- **Implementation**: [`skills/terminal-agent-runner/scripts/fleet.py:cmd_park`](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2850-L2907) and [`cmd_escalations`](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2909-L2930)
- **Role**: When an agent prompt escalates or cannot be auto-approved, `fleet.py park --agent <name> --cancel` acquires `state_lock(tasks_file)`, marks `lifecycle: "parked"` in `tasks.json`, appends a detailed entry to `$AGENT_HERDER_STATE_DIR/<session>/escalations.json`, and frees held locks via `resource_pool.py`.
- **Signalling Requirement**: An escalation event must be emitted atomically as part of the park transition so that the lead agent or host harness immediately learns which task was parked and why, allowing instant dispatch of independent DAG branches.

### Surface 3: `fleet.py status` & Telemetry Scraper
- **Implementation**: [`skills/terminal-agent-runner/scripts/fleet.py:fleet_rows`](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2197-L2283) and [`cmd_status`](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2312-L2334)
- **Role**: Inspects live pane text via `read_text(name, session, lines=45)`, extracts modal prompts (`pending_prompt`), classifies against `risk_policy.json`, evaluates health, verifies multi-sample settle stability, and aggregates resource leases from `tasks.json`.
- **Signalling Requirement**: The event bus must not replace `status` as the ultimate source of truth. Events act as asynchronous *wakeup triggers*; `fleet_rows()` remains the authoritative state evaluator to prevent split-brain states or stale cache hazards.

### Surface 4: Herdr `recent-unwrapped` Read Restriction (`agent_not_idle`)
- **Implementation**: Documented in [`skills/terminal-agent-runner/scripts/fleet.py:2458-2466`](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2458-L2466) and [`CONTINUE-UNATTENDED-GATES.md:60-64`](file:///Users/devkhadka/projects/agent-skills/CONTINUE-UNATTENDED-GATES.md#L60-L64)
- **Invariant**: In Herdr 0.8.0, calling `herdr agent read <name> --source recent-unwrapped` raises an `agent_not_idle` error whenever the agent is in `agent_status: working` or `agent_status: blocked`. Only `--source visible` is permitted while an agent is actively running or blocked on a prompt.
- **Signalling Requirement**: No event signalling mechanism may assume or attempt `recent-unwrapped` reads while an agent is active. Worker-emitted notifications must either carry necessary metadata in their payload or rely on `--source visible` pane reads.

---

## Evaluation of Signalling Options

Five candidate architectures are evaluated across latency, CPU/IO overhead, implementation footprint, failure modes, and compatibility with the four repository surfaces.

```
+--------------------------------------------------------------------------------------------------------+
|                                  SIGNALLING OPTIONS ARCHITECTURE MATRIX                                |
+--------------------------------------------------------------------------------------------------------+
| Option A: Durable File-Based Outbox Spool (Hybrid Event Bus)                                           |
|   Worker --> [ Atomic Write: events/evt_<uuid>.json ] --> FS Watcher / kqueue --> Herder Wakeup        |
|   Fallback: Periodic status poll backstop (10s)                                                        |
+--------------------------------------------------------------------------------------------------------+
| Option B: Unix Domain Socket (UDS) IPC Daemon                                                          |
|   Worker --> [ Non-blocking DGRAM to events.sock ] --> select()/epoll() in fleet.py watch              |
|   Fallback: Connection timeout / poll backstop                                                         |
+--------------------------------------------------------------------------------------------------------+
| Option C: Herdr Protocol 19 Native Event Stream Tap                                                    |
|   Herdr Server Daemon (protocol 19) --> [ Long-lived JSON-RPC Socket Stream ] --> fleet.py watch       |
|   Fallback: Stream reconnection & poll backstop                                                        |
+--------------------------------------------------------------------------------------------------------+
| Option D: Adaptive Multi-Frequency Status Polling                                                      |
|   Herder (fleet.py watch) --> [ Dynamic Sleep Interval: 100ms - 5s ] --> Scrapes Visible Text          |
|   Fallback: N/A (Pure Polling)                                                                         |
+--------------------------------------------------------------------------------------------------------+
| Option E: Pseudo-Terminal (PTY) Output Interposition Pipe                                              |
|   Worker CLI --> [ PTY Master/Slave Filter Pipe ] --> Regex Matcher --> SIGUSR1 / Herder Pipe         |
|   Fallback: Pipe buffer drain backstop                                                                 |
+--------------------------------------------------------------------------------------------------------+
```

---

### Option A: Durable File-Based Outbox Spool with Polling Backstop (Hybrid Event Bus)

#### Architecture
1. **Outbox Directory**: Dedicated spool directory at `$AGENT_HERDER_STATE_DIR/<session>/events/`.
2. **Worker Emission (Non-Blocking)**: When a worker agent wrapper or helper needs herder attention (e.g. `prompt_blocked`, `task_complete`, `quota_error`), it writes a small JSON payload to a temporary file in `events/tmp/` and atomically renames it to `events/evt_<timestamp>_<agent>_<uuid>.json`. The write is purely local, synchronous filesystem I/O taking <1ms, with no network handshakes or blocking waits.
3. **Supervisor Listener**: `fleet.py watch` blocks on a filesystem notification descriptor (`kqueue` on macOS / `inotify` on Linux, falling back to a 200ms `os.scandir` check) combined with the standard lease renewal timeout (e.g. 10s).
4. **Consumption**: Upon waking, `fleet.py watch` / `auto_herd.sh` drains the event queue, unlinks processed files, and immediately triggers `fleet_rows()` to action the fleet.

#### Cost Analysis
- **Nominal Runtime Cost**: Extremely low. File creation and atomic rename on modern APFS/ext4 take microseconds. Near-zero CPU utilization while waiting on `kqueue`/`select`.
- **Latency**: <5ms from emission to supervisor wakeup.
- **Failure Modes & Failure Costs**:
  - *Worker crashes before emitting*: The supervisor's 10-second polling backstop (`fleet_rows` status check) detects the unassigned/blocked/dead pane. Maximum delay is bounded by the backstop interval (10s), avoiding permanent hangs.
  - *Disk full or permission failure*: Atomic write fails cleanly with non-zero exit in the worker helper without crashing the core agent loop; supervisor polling catches the state.
  - *Stale event accumulation*: Event files not cleaned up due to an abrupt herder crash are pruned during `herd.sh init` or `fleet.py reconcile`.

#### Code Touchpoints
- **`fleet.py`**:
  - `state_path(name, session)`: Add helper for `events` directory pathing ([fleet.py:35-38](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L35-L38)).
  - `emit_event(name, session, kind, payload)`: Utility to create `tmp` file and `os.replace` into `events/`.
  - `cmd_watch(a)`: Update main loop ([fleet.py:2336-2370](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2336-L2370)) to monitor the `events` directory using `select`/`kqueue` alongside lease renewal timers.
  - `cmd_park(a)`: Call `emit_event("parked", ...)` inside the `state_lock` block ([fleet.py:2882-2904](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2882-L2904)).
  - `cmd_notify(a)`: New CLI subcommand for worker scripts or shell traps to post events.
- **`herd.sh`**:
  - `init`: Ensure `$STATE_DIR/$SESSION_NAME/events` directory is created and purged of stale files during session bootstrap ([herd.sh:144-197](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/herd.sh#L144-L197)).
  - `spawn`: Export `AGENT_HERDER_EVENT_DIR` into worker pane environment.

#### Integration with the Four Surfaces
- **`auto_herd.sh`**: `auto_herd.sh` calls `herd.sh watch`, which instantly wakes up and returns JSON on event arrival. `auto_herd.sh` processes the state without spinning in tight polling loops.
- **`escalations.json`**: When `fleet.py park` writes to `escalations.json`, it also emits an `escalation` event file, notifying the supervisor immediately.
- **`fleet.py status`**: Remains completely unmodified as the authoritative state evaluator. Events trigger `fleet_rows()`; status is never inferred from event payloads alone.
- **Herdr `recent-unwrapped` restriction**: Fully compliant. The supervisor never calls `recent-unwrapped` on active/blocked agents; it reads `--source visible` or parses structured handoffs directly from disk.

---

### Option B: Unix Domain Socket (UDS) IPC Daemon with Non-Blocking Datagrams

#### Architecture
1. **Socket Daemon**: `herd.sh init` or `fleet.py` opens a Unix Domain Socket at `$AGENT_HERDER_STATE_DIR/<session>/events.sock`.
2. **Worker Emission**: Workers send single JSON datagrams (`SOCK_DGRAM` or non-blocking `SOCK_STREAM` with `MSG_DONTWAIT`) to `events.sock`.
3. **Supervisor Listener**: `fleet.py watch` blocks on `select.select([sock_fd], [], [], timeout)`.

#### Cost Analysis
- **Nominal Runtime Cost**: Zero disk I/O; pure memory IPC. Sub-millisecond latency.
- **Failure Modes & Failure Costs**:
  - *Socket buffer exhaustion / Deadlock*: If a worker uses blocking socket calls (`send()`) when the supervisor is un-attached or busy, **the worker freezes indefinitely** (deadlock). To prevent this, sends must be strictly non-blocking (`SO_SNDTIMEO` = 50ms) and discard failures.
  - *macOS Socket Path Length Limit*: On macOS (Darwin), `sockaddr_un.sun_path` is constrained to **104 characters**. Paths like `/Users/devkhadka/.config/.agents/herder/agent-herder-sim-93314/events.sock` (68 chars) approach this limit; in nested temp directories (e.g. `/private/var/folders/.../T/agent-herder-simulation.7ynsvkv8/.../events.sock`, 92+ chars), socket creation crashes with `AF_UNIX path too long`.
  - *Orphaned Sockets*: Stale `.sock` files after ungraceful termination cause connection refusals.

#### Code Touchpoints
- **`fleet.py`**:
  - `cmd_watch`: Create and manage socket server lifecycle with `select.select`.
  - `cmd_notify`: Socket client sender with strict non-blocking timeouts and `AF_UNIX` length validation.
- **`herd.sh`**:
  - `init`: Clean up stale `.sock` files, ensure socket directory exists.

#### Integration with the Four Surfaces
- **`auto_herd.sh`**: Relies on `watch` returning on socket wakeups.
- **`escalations.json`**: Synchronous write to file followed by non-blocking socket dispatch.
- **`fleet.py status`**: Unaffected.
- **Herdr `recent-unwrapped` restriction**: Fully compliant.

---

### Option C: Herdr Protocol 19 Native Event Stream Tap

#### Architecture
1. **Server Stream Subscription**: Herdr 0.8.0 runs a protocol 19 server daemon communicating over `~/.config/herdr/sessions/<session>/herdr.sock`.
2. `fleet.py watch` establishes a long-lived connection to Herdr's internal JSON-RPC socket and subscribes to server-level lifecycle events (pane output mutations, agent status transitions `working` $\rightarrow$ `blocked` $\rightarrow$ `idle`).

#### Cost Analysis
- **Nominal Runtime Cost**: Zero worker-side modification. The worker CLI runs entirely uninstrumented in its terminal.
- **Failure Modes & Failure Costs**:
  - *Protocol Fragility & Coupling*: Herdr's wire protocol is private and un-versioned across minor releases (e.g. breaking changes between 0.6.10 and 0.8.0). Any upstream Herdr update risks breaking fleet supervision.
  - *High Implementation Complexity*: Requires converting `fleet.py`'s stateless CLI client architecture into an asynchronous connection manager (`asyncio` or background reader thread) with reconnection backoff, keep-alives, and stream desynchronization recovery.
  - *TUI Redraw Floods*: Terminal animations (spinners, cursor blinks) generate massive streams of frame redraw events, creating event storms that saturate the supervisor.

#### Code Touchpoints
- **`fleet.py`**:
  - `herdr()`: Major rewrite from `subprocess.run(["herdr", ...])` ([fleet.py:465](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L465)) to an asynchronous socket client.
  - `cmd_watch`: Rewrite to process JSON-RPC notification stream.
- **`herd.sh`**:
  - Injects socket descriptor arguments to long-lived Python processes.

#### Integration with the Four Surfaces
- **`auto_herd.sh`**: Severely strained. `auto_herd.sh` is a Bash script; integrating a streaming socket client requires rewriting `auto_herd.sh` in Python or maintaining a persistent sidecar daemon.
- **`escalations.json`**: Herdr server has no domain awareness of `escalations.json` (owned by `terminal-agent-runner`).
- **`fleet.py status`**: Unaffected.
- **Herdr `recent-unwrapped` restriction**: Fragile. Receiving a `blocked` status stream event does not permit calling `recent-unwrapped`; `read_text` must still restrict itself to `--source visible`.

---

### Option D: Adaptive Multi-Tiered Status Polling with Dynamic Settle Windows

#### Architecture
1. **Pure External Polling**: Retains the stateless scraping model (`herdr agent list` + `read_text(source="visible")`), but replaces rigid sleep intervals with dynamic frequency scaling inside `fleet.py watch`.
2. **Frequency Tiers**:
   - *Active Settle Tier (100ms - 250ms)*: When an agent is in state `settling` or immediately following `spawn` / `prompt` submission.
   - *Steady Working Tier (1000ms - 1500ms)*: When all agents are stably `working`.
   - *Idle/Quiescent Tier (5000ms)*: When no state transitions are detected, aligned with lease renewal cycles.

#### Cost Analysis
- **Nominal Runtime Cost**: Low-to-moderate. Spawns `herdr` CLI subprocesses periodically.
- **Latency**: 100ms - 1500ms reaction latency (sufficient to eliminate the 14-minute stall, but not real-time 0ms).
- **Failure Modes & Failure Costs**:
  - *Zero Architecture Failure Modes*: No sockets to leak, no event files to orphan, no protocol streams to desynchronize.
  - *Subprocess Overhead*: In large fleets (e.g. 8+ agents), high-frequency polling can induce measurable CPU load from repetitive process forks (`subprocess.run`).

#### Code Touchpoints
- **`fleet.py`**:
  - `cmd_watch`: Implement dynamic `interval` selection based on `rows` analysis ([fleet.py:2344-2370](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2344-L2370)).
  - `fleet_rows`: Batch pane reads to minimize subprocess invocations.
- **`herd.sh`**: No changes.

#### Integration with the Four Surfaces
- **`auto_herd.sh`**: 100% transparent and native compatibility.
- **`escalations.json`**: Fully compatible.
- **`fleet.py status`**: Identical execution path.
- **Herdr `recent-unwrapped` restriction**: Perfectly aligned.

---

### Option E: Pseudo-Terminal (PTY) Output Interposition Pipe (Sidecar Pipe Tap)

#### Architecture
1. **PTY Interposition**: Worker agents are spawned inside a PTY interceptor pipe (e.g. `ptyprocess` or custom PTY wrapper) managed by `terminal-agent-runner`.
2. **Stream Filtering**: The PTY wrapper inspects standard output in real time for prompt triggers (ANSI codes, permission questions) and signals the supervisor via `SIGUSR1` or an IPC pipe.

#### Cost Analysis
- **Nominal Runtime Cost**: Low CPU, instantaneous signal.
- **Failure Modes & Failure Costs**:
  - *Deadlock via Pipe Buffer Saturation*: If the supervisor fails to drain the interposition pipe, the worker's `stdout` blocks, **permanently freezing the agent process**.
  - *Terminal Emulation Corruption*: PTY interception frequently corrupts cursor addressing, alternate screen buffers (`smcup`/`rmcup`), window resize signals (`SIGWINCH`), and bracketed paste modes.
  - *Direct Conflict with Herdr*: Herdr already owns pane PTY allocation. Interposing a secondary PTY creates severe nested terminal multiplexing bugs.

#### Code Touchpoints
- **`fleet.py`**: Invasive overhaul of `cmd_spawn` ([fleet.py:2100-2180](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2100-L2180)).
- **`herd.sh`**: PTY wrapper harness injection.

#### Integration with the Four Surfaces
- **`auto_herd.sh`**: Poor; introduces non-deterministic sub-shell interactions.
- **`escalations.json`**: Unaffected.
- **`fleet.py status`**: Interferes with Herdr's native pane scrapers.
- **Herdr `recent-unwrapped` restriction**: Bypasses Herdr but introduces terminal corruption.

---

## Detailed Comparative Analysis

| Dimension | Option A: Durable Outbox Spool (Hybrid Bus) | Option B: Unix Domain Socket (UDS) | Option C: Herdr Protocol Stream | Option D: Adaptive Status Polling | Option E: PTY Pipe Interposition |
|---|---|---|---|---|---|
| **Notification Latency** | **<5 ms** | **<1 ms** | <10 ms | 100 ms – 1500 ms | <1 ms |
| **Worker Deadlock Risk** | **Zero (Atomic Disk Write)** | High if blocking; Zero if DGRAM | Zero (Passive) | Zero (Passive) | Critical (Pipe saturation) |
| **Failure Cost** | **Zero (Falls back to 10s poll)** | High (Socket leak / path limits) | High (Protocol desync) | Zero (Inherent fallback) | Critical (Frozen TUI) |
| **Herdr 0.8.0 Compatibility** | **100% Native** | 100% Native | Low (Private protocol) | 100% Native | Conflicted (Double PTY) |
| **`recent-unwrapped` Safety** | **Total (Self-contained)** | Total | Fragile | Total | Total |
| **Implementation Footprint** | **Minimal (~80 LOC)** | Moderate (~160 LOC) | Heavy (~450+ LOC) | Minimal (~30 LOC) | Excessive (~300+ LOC) |
| **Host Harness Agnostic** | **Yes (Claude/AGY/Codex)** | Partial (Path length limits) | No | Yes | No |

---

## Architectural Recommendation

### The Recommendation: Option A (Durable File-Based Outbox Spool with Polling Backstop) combined with Option D's Adaptive Settle Frequency

The optimal signalling architecture is a **Hybrid Event Bus** composed of:
1. **Primary Push Signalling (Option A)**: Non-blocking atomic event spooling in `$AGENT_HERDER_STATE_DIR/<session>/events/`.
2. **Adaptive Polling Backstop (Option D)**: In-process adaptive polling (100ms settle / 10s lease renewal backstop) in `fleet.py watch`.

### Why This Beats the Alternatives

#### 1. Absolute Immunity to Worker Deadlocks and Starvation
Unlike socket IPC (Option B) or PTY pipes (Option E), file spooling via `mktemp` and atomic `os.replace` is completely asynchronous and decoupled. If the supervisor is busy, restarting, or suspended across turns, a worker emitting a completion or escalation notification never blocks or waits. It writes its payload in <1ms and continues its lifecycle.

#### 2. Complete Elimination of Polling Lag (Prevents the 14-Minute Stall)
When a worker encounters a plan approval or permission prompt (as `sim-docs-audit-bdl` did on 2026-08-21), the worker helper or wrapper immediately drops an `escalation_needed` event into `events/`. `fleet.py watch` wakes instantly via `kqueue`/`select`, classifies the prompt, parks the worker with `fleet.py park --cancel`, records the reason in `escalations.json`, and frees resource leases within milliseconds. Downstream ready beads can then be claimed and spawned in the very same wave without starvation.

#### 3. Seamless Grounding in Durable Repository State
The file-based event bus naturally mirrors the repository's existing durable state patterns:
- `tasks.json` stores assignment state.
- `escalations.json` stores unapproved prompt records.
- `handoffs.json` stores structured evidence.
- `events/` stores transient wakeup signals.
There is zero impedance mismatch: `fleet.py status` remains the sole evaluator of truth, preventing race conditions or split-brain inconsistencies between event payloads and actual pane reality.

#### 4. Total Resilience Against Herdr's `agent_not_idle` Restriction
Because event files carry structured transition metadata (`agent`, `kind`, `timestamp`, `details`), the supervisor does not need to perform illegal `herdr agent read --source recent-unwrapped` calls against active or blocked workers. It uses `--source visible` for prompt inspection and reads structured handoffs from disk, fully complying with Herdr 0.8.0 constraints.

#### 5. Zero Protocol Coupling & Maximum Cross-Platform Portability
Unlike Option C (which couples to Herdr's private JSON-RPC protocol) and Option B (which breaks on macOS 104-character socket path limits in nested temporary directories), file-based atomic spooling functions identically across macOS, Linux, all Herdr versions, and all agent CLIs (Claude Code, AGY, Codex CLI, OMP).

---

## Required Modifications Specification (When Implemented)

*Note: In accordance with task rules, no code is modified in this research bead. The following specifies the exact code paths for future implementation.*

```
+----------------------------------------------------------------------------------------------------+
|                                    SPECIFIED CODE TOUCHPOINTS                                      |
+----------------------------------------------------------------------------------------------------+
| 1. fleet.py:state_path()           --> Adds "events" and "events/tmp" directory resolution         |
| 2. fleet.py:emit_event()           --> Atomic mktemp + rename event emitter helper                 |
| 3. fleet.py:cmd_watch()            --> Integrates kqueue/select directory monitor with 10s timer   |
| 4. fleet.py:cmd_park()             --> Emits "parked" event under state_lock                       |
| 5. herd.sh:init                    --> Provisions and purges events/ directory on session bootstrap|
| 6. auto_herd.sh                    --> Re-enters watch immediately upon non-zero exit 1 resolution |
+----------------------------------------------------------------------------------------------------+
```

1. **`fleet.py:state_path`** ([fleet.py:35-38](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L35-L38)): Resolve `events_dir = state_path("events", session)`.
2. **`fleet.py:emit_event`** (New function):
   ```python
   def emit_event(session: str | None, agent: str, kind: str, payload: dict | None = None) -> Path:
       edir = state_path("events", session)
       tdir = edir / "tmp"
       tdir.mkdir(parents=True, exist_ok=True)
       data = {"agent": agent, "kind": kind, "at": time.time(), "payload": payload or {}}
       tmp_file = tdir / f"{agent}_{time.time_ns()}.json"
       target_file = edir / f"evt_{int(time.time()*1000)}_{agent}_{uuid.uuid4().hex[:8]}.json"
       with open(tmp_file, "w", encoding="utf-8") as f:
           json.dump(data, f)
       os.replace(tmp_file, target_file)
       return target_file
   ```
3. **`fleet.py:cmd_watch`** ([fleet.py:2336-2370](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2336-L2370)): Replace fixed `time.sleep(a.interval)` with a non-blocking drain of `events_dir` paired with a `kqueue`/`select` file descriptor wait bounded by `min(a.interval, time_to_next_lease_renewal)`.
4. **`fleet.py:cmd_park`** ([fleet.py:2881-2906](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/fleet.py#L2881-L2906)): Call `emit_event(a.session, a.agent, "parked", {"reason": entry["reason"], "freed_locks": entry["freed_locks"]})`.
5. **`herd.sh:init`** ([herd.sh:178-197](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/scripts/herd.sh#L178-L197)): Automatically create `$STATE_DIR/$SESSION_NAME/events` and purge stale events from prior runs during session bootstrap.
