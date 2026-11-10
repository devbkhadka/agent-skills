# Command contract and output limits

The CLI's command schema generates help and validates flags. Run `dbg --help` or
`dbg scope --help` for the current syntax. `dbg` means the executable in [SKILL.md](../SKILL.md).

| Commands | Purpose |
| :--- | :--- |
| `targets`, `start`, `status`, `stop` | Discover, attach, inspect lifecycle, clean up. |
| `break file:line[:col] --if expr`, `logpoint file:line[:col] expr` | Set source probes; line/column are one-based. |
| `list`, `remove id`, `clear` | Inspect or remove probes. |
| `wait --timeout 300`, `pause`, `continue` | Await, request, or release a pause. |
| `scope --frame 0 --scope local --vars a,b --depth 2` | Select scope type/name and variable names; exact matching. |
| `stack --offset 0 --limit 20` | Return raw frame pages with original indices and `nextOffset`, `hasMore`, `total`. |
| `eval expr --frame 0 --depth 2`, `step over\|into\|out --timeout 30` | Evaluate or step; `eval` is global when running. |
| `exceptions none\|uncaught\|all`, `reload` | Configure exception pauses or reload a supported page/app. |
| `logs --since 0 --limit 20`, `logs --tail --limit 5` | Oldest unread page or explicit newest samples. |

Global flags: `--session name`, `--pretty`, `--max-output-bytes N` and `--help`; unknown flags
return `BAD_ARGS`. Session names use `[A-Za-z0-9._-]` (max 64) and must not start with `.`. An `eval` expression starting with `--` needs a separator: `dbg eval -- --x`.
Explicitly passed session settings must match when reusing a daemon; mismatches return
`SESSION_CONFLICT`. Inspect its requested and running configuration before choosing another name
or stopping it. Running `start` from a different directory without `--cwd` is not a conflict: it
reuses the session, whose `config.cwd` shows the directory it resolves paths against. Concurrent
starts serialize; a waiter allows the running start its full `--attach-timeout` plus margin.

A pause snapshot shows up to 10 user frames from the first 60, collapsing adjacent library frames.
Default scope snapshots summarize closure/module wrappers or scopes with more than 15 variables.
Other scopes show at most 50 variables. `scope --all` expands summaries, at most 200 variables per
scope. Both keep 10 properties per object/array, strings around 200 characters, and depth at most 5.
Use `--vars` to fetch just named variables even from a summarized closure. `stack --all` expands
library frames within the first 60; page additional frames with `--offset`, limit 1..60.

The log ring retains 500 hits. Page limit is 1..500 (default 20). `since` is exclusive;
`nextSeq` is the last returned hit, or your original cursor for an empty page. `hasMore` indicates
unread retained hits after an ordinary page. Tail mode intentionally skips older matching hits;
its `hasMore` is false. `omitted` counts matching hits outside the returned page (including hits
trimmed by the output budget), `dropped` counts
hits overwritten before your cursor, and `latestSeq`/legacy `lastSeq` report highest observed seq.

Output defaults to compact JSON bounded to 16,384 bytes; budgets may range from 1,024 to 1,048,576.
Pretty output obeys the same bound. `outputTruncated` reports original size, budget and trimming.
Evidence can be reduced to preserve state/cursors; narrow a scope/expression or increase the budget
before drawing conclusions from missing values. Log cursors track returned hits after trimming.
Timeouts use seconds; startup idle timeout accepts durations such as `30m`.

Common errors: `BAD_ARGS` (invalid/unsupported flags), `NOT_RUNNING`, `NO_TARGET`,
`ATTACH_TIMEOUT`, `NOT_PAUSED`, `INTERNAL_PAUSE` (binding in progress; retry),
`RELOAD_UNSUPPORTED` (follow its hint), `SESSION_CONFLICT` (explicit flags differ from the running
session; `conflicts` names them), `START_BUSY` (another start outlived its attach window; retry), and
`NODE_VERSION` (the CLI needs Node >=22). Runtime state recovery is in the main skill.
