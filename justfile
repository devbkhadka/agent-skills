set positional-arguments

default:
    @just --list

# Interactively install a skill, or pass CLI flags (run with --help for details).
install *args:
    pnpm run skills:install "$@"

# Show installed vs repository versions for every managed skill.
status:
    @pnpm run --silent skills:status

# Review and manage captured skill improvement suggestions.
suggestions *args:
    @pnpm run --silent skills:suggestions "$@"

# Interactively install a system prompt, or pass CLI flags (run with --help for details).
prompt-install *args:
    pnpm run prompts:install "$@"

# Show installed vs repository versions for every managed system prompt.
prompt-status:
    @pnpm run --silent prompts:status

# Interactively install an MCP server, or pass CLI flags (run with --help for details).
mcp-install *args:
    pnpm run mcp:install "$@"

# Show installed vs repository versions for every managed MCP server.
mcp-status:
    @pnpm run --silent mcp:status

# Uninstall an MCP server from agent configuration files.
mcp-uninstall *args:
    pnpm run mcp:uninstall "$@"

# Run the install/status script tests against a temporary HOME.
test:
    @pnpm run --silent test:scripts
