# Herdr & Resource Pool Cheat Sheet

**Verified against herdr 0.8.0.** Commands from older versions of this skill
(`herdr agent send`, `herdr wait output`, `herdr wait agent-status`, `herdr pane spawn`,
`herdr list`) **do not exist** — they were the single largest source of wasted calls in
testing. Prefer `scripts/fleet.py`, which wraps all of this and returns compact output.

---

## 1. Session targeting (do this first)

A named session has its **own socket**. Without it, every command silently drives the
*default* session.

```bash
herdr session list                       # name, status, directory, socket
export HERDR_SOCKET_PATH=~/.config/herdr/sessions/<name>/herdr.sock
```

Prefer omitting `--session`; `fleet.py` inherits the herder's `HERDR_SOCKET_PATH`. It rejects a
conflicting explicit session unless `--allow-cross-session` is passed. `HERDR_PANE_ID` /
`HERDR_SOCKET_PATH` tell you which session *you* are in.

Probe the server by parsing JSON, never by grepping prose:

```bash
herdr status server --json    # -> {"status":"running","version":"0.8.0",...}
```

---

## 2. Real 0.8.0 command surface

```bash
# agents  (targets: terminal id, unique agent name, detected label, legacy pane id)
herdr agent list
herdr agent get       <target>
herdr agent read      <target> [--source visible|recent|recent-unwrapped|detection] [--lines N]
herdr agent prompt    <target> <text> [--wait] [--until STATE] [--timeout MS]   # text + Enter, atomic
herdr agent send-keys <target> <key>...      # LOGICAL keys only: esc, enter, ctrl+c, y, n, 1
herdr agent wait      <target> [--until idle|working|blocked|done|unknown] [--timeout MS]
herdr agent start     <name> --kind <kind> --pane <id> [--timeout MS] [-- <argv...>]

# panes  (ids are POSITIONAL and RECYCLED on close -- prefer agent names)
herdr pane list | get <id> | layout | close <id> | split | swap | move | zoom
herdr pane read        <id> [--source ...] [--lines N]
herdr pane send-text   <id> <text>           # literal text, NO Enter
herdr pane send-keys   <id> <key>...         # logical keys; requires a PANE ID, not an agent name
herdr pane run         <id> <command>        # command text + Enter
herdr pane wait-output <id> <--match TEXT|--regex PATTERN> [--source ...] [--timeout MS]

# tabs / status
herdr tab list | create | get <id> | focus <id> | rename <id> | close <id>
herdr status server --json | herdr status client --json
```

Agent states: `working`, `blocked`, `idle`, `done`, `unknown`.

`agent start` requires a pane already sitting at an interactive shell prompt, so the spawn
sequence is `tab create` → `pane` at a prompt → `agent start --kind <k> --pane <id>`.

---

## 3. Gotchas that cost real time

| Gotcha | Detail |
|---|---|
| **`agent send` was removed** | 0.8.0 replaced it with `agent prompt` (text + Enter, atomic, bracketed-paste-safe). For text without Enter you need `pane send-text`, which takes a pane id. |
| **`send-keys` takes keys, not text** | `agent send-keys` validates against a logical key table. Passing a sentence fails; use `agent prompt`. |
| **Pane ids recycle** | Closing panes renumbers the survivors. Resolve `name -> pane_id` immediately before each use, or just use `fleet.py`. |
| **Splitting starves TUIs** | 5 panes in one tab → a 1-row pane that cannot render an approval prompt. One tab per agent. |
| **`agent prompt --wait` does not track turns** | If the agent is already working, that in-flight turn's completion can satisfy the wait. Gate on evidence, not on the wait returning. |
| **Seed via argv instead** | `codex`, `claude`, and `omp` take a positional task; `agy` uses `-i`. Codex gets `--no-alt-screen` for readable scrollback. |
| **Agent names are lowercase-only** | `agent start` rejects a capital with `invalid_agent_name`: lowercase letters, digits, `-` and `_`, 1-32 chars. A `Bead-<id>` convention fails here after the tab exists and the leases are held. |
| **A prompt can be accepted and still not arrive** | `agent prompt` returns success once Herdr has the text, which is not the same as the CLI having it. Submitted right after `agent start`, the initialising TUI drops it silently and the worker sits idle holding leases. Read the pane back to confirm; `fleet.py`'s `_submit` does. |
| **`esc` not `escape`** | `esc` is canonical; `escape` is accepted, but `Escape`/`ESC` → `invalid_key`. |
| **Agents wrap their own output** | `recent-unwrapped` does **not** undo it — the wrap is in the content. Classify with both whitespace-collapsed and wrap-joined readings. |
| **Grepping status prose is fragile** | Use `status server --json` and read `.status`; the human-readable text is not a contract. |
| **`timeout(1)` is absent on macOS** | Enforce deadlines in python, or a missing binary looks like a passing probe. |
| **zsh doesn't word-split vars** | `CMD="python3 x.py"; $CMD args` → "no such file". Use a shell function. |
| **Print mode ≠ interactive tier** | `agy -p` answered fine while interactive panes were quota-blocked. Confirm with `fleet.py status`. |

---

## 4. Wrapper documentation

This file intentionally stops at raw Herdr behavior. Wrapper commands, policy, completion gates,
and leases used to be duplicated here and became stale. Use [fleet_operations.md](fleet_operations.md)
for runtime lifecycle and [resource_leases.md](resource_leases.md) for pool behavior; resolve the
scripts from the directory containing the loaded `SKILL.md`, never a guessed home-directory path.
