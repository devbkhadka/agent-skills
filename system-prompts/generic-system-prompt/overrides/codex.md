## Codex Runtime Directives
- **Sandbox Compliance**: Strictly operate within workspace and permission boundaries (`sandbox_mode`). Do not attempt to read or modify files outside authorized roots.
- **TUI & Streams**: Avoid operations that disrupt the terminal UI or hijack terminal screen buffers.
- **Diff Hygiene**: Prefer generating standard, minimal unified diffs (`git apply` compatible) for automated verification.
