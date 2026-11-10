# Core CLI tools and progressive discovery

Load only the smallest command surface needed for the current stage.

## Initial orientation

```bash
br --version
command -v bvr
command -v bv
```

Use `bvr` when present; otherwise use `bv`.

Do not dump every CLI's full help into context at startup.

## Beads planning and state

Core commands:

```bash
br scheduler --json       # ranked ready work for dispatch
br show ID --json         # one task and agent_context
br update ID --claim      # atomic claim
br close ID               # workflow-gated completion
br coordination status --json # inspect claim coordination state
bv --robot-plan           # or bvr: parallel graph tracks
```

For specific advanced needs, ask that surface directly:

```bash
br scheduler --help
br update --help
br close --help
br capabilities --format json --command update
br robot-docs guide
bv --robot-help
bv --robot-schema --schema-command robot-plan
```

Use `bvr` when installed; fall back to legacy `bv`. Never assume their version or output flags
are identical without checking local help.

## Standing rule

Never invent CLI flags or command subcommands from memory. Always inspect the live command's
`--help` or machine documentation if a flag is not among the verified core flags.
