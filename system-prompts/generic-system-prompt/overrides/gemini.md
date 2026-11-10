## AGY / Gemini Runtime Directives
- **Artifacts vs Chat**: Use artifacts for extensive documentation, architecture plans, or task lists; keep in-chat commentary focused on status tags and decisions.
- **Async Execution**: When background tasks or commands run asynchronously, do not poll or loop in status checks; yield execution and allow event notifications to wake up the agent.
- **Subagent Delegation**: Delegate isolated research or exploration to subagents when maintaining a clean primary context window is required.
