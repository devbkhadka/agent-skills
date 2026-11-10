# Runtime Adapters Guide

Concrete dispatch patterns for Antigravity (AGY), Claude Code, and Codex CLI agents.

---

## 1. Antigravity (AGY)

Native subagent lifecycle via tool calls:

```json
// Sidecar (Investigator / Flash Model)
invoke_subagent({
  "Subagents": [{
    "TypeName": "research",
    "Role": "Codebase Investigator",
    "Prompt": "Investigate existing implementations of [Module]. Return only file paths, interfaces, and error types.",
    "Model": "flash"
  }]
})

// Worker (Implementer / Inherit Model)
invoke_subagent({
  "Subagents": [{
    "TypeName": "self",
    "Role": "Feature Implementer",
    "Prompt": "Implement [Milestone]. Modify only [Target Files]. Run [Test Command] locally. Return structured summary.",
    "Model": "inherit"
  }]
})
```
- **Rules**: Never poll `manage_subagents`; await reactive notification. Write long-term state/plans to artifacts (`brain/<id>`). Use `send_message` only for mid-flight corrections.

---

## 2. Claude (Claude Code / Task Tool)

Task tool delegation with boundary prompts:

```markdown
Task: Implement user session invalidation in auth/session_service.py.
Boundaries:
- Target files: auth/session_service.py, tests/test_session_service.py
- Forbidden: auth/core.py
- Command: pytest tests/test_session_service.py
- Return: Modified paths, 2-line flow summary, and test pass evidence. Do not output full files.
```
- **Rules**: Subagents are self-contained. Always pass absolute/repo paths and explicit test commands. Sync progress in `.feature-state.md`.

---

## 3. Codex / CLI Agents (Headless Subprocess)

Subprocess invocation with file-based IPC:

```bash
# Research spike:
codex exec --sandbox "Find interfaces for module X; output findings to stdout" > .scratch/research.txt

# Worker milestone:
codex exec --sandbox "Execute milestone M1 per prompt in stdin" < .scratch/task_m1.md
```
- **Rules**: Rely on child process exit codes (0 = pass). Pass structured prompt via stdin, read structured return summary from stdout. Bound processes with timeouts.

---

## Universal Worker Prompt Template

```markdown
You are a Worker Subagent executing an engineering milestone.

### OBJECTIVE:
[Brief description of target behavioral change]

### BOUNDARIES:
- Target: [Paths to modify]
- Forbidden: [Untouched core paths]
- Invariants: [Domain constraints to preserve]

### VERIFICATION:
Run: `[exact test command]`

### RETURN CONTRACT:
- Status: [SUCCESS | BLOCKED]
- Touched Files: [List of paths]
- Summary: [2-3 sentences on flow changes]
- Tier-1 Result: [Passed X tests in Ys]
- Tactical Decisions: [Conventions or local choices made]
```
