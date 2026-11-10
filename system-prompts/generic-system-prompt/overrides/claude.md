## Claude Runtime Directives
- **Command Execution**: Run shell commands non-interactively (`PAGER=cat`, `CI=true`, `DEBIAN_FRONTEND=noninteractive`). Never invoke commands that wait for interactive stdin or open interactive pagers/editors (e.g. `less`, `nano`).
- **File Modifications**: Use targeted string replacements or edits rather than rewriting large files.
- **Background Processes**: Do not spawn long-running background processes without lifecycle management.
