# Core CLI tools and progressive discovery

Load only the smallest command surface needed for the current stage.

## Initial orientation

```bash
herdr --version
codex --version
claude --version
python3 "$S/fleet.py" --help
```

Do not dump every CLI's full help into context at startup.

## Fleet execution

Core commands:

```bash
fleet.py spawn --help
fleet.py watch --help
fleet.py complete --help
fleet.py park --help
fleet.py reset --help
fleet.py status --help
```

Use `fleet.py prompt --help` for same-domain follow-ups.
Use `resource_pool.py <command> --help` only when tuning leases.

## Raw Herdr

Prefer `fleet.py`. Read raw buffers or send keys only when debugging the wrapper. Then load
[herdr_cheatsheet.md](herdr_cheatsheet.md) and verify commands with local `herdr ... --help`.

## Agent CLIs

The wrapper owns common launch modes. Inspect `codex --help` or `claude --help` only for an
explicit model, profile, tool, MCP, or version-specific permission request. `--cli` is a validated
identifier, not an argv escape hatch; embedded flags (including permission/sandbox/bypass flags)
are rejected. Use the wrapper's documented `--approval-mode` and `--read-only` mappings.
