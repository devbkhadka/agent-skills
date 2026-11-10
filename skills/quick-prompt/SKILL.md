---
name: quick-prompt
description: Fill and run reusable prompt templates from a global library. Invoked as /quick-prompt "<selector>" "<instructions>" — the selector is an exact template name or a short description; placeholder values are inferred from the instructions. Also use to add, update, list, show, or remove prompt templates.
---

# Quick Prompt

A global library of prompt templates (`~/.quick-prompt/templates/<name>.md`, override with
`QUICK_PROMPT_HOME`). You select a template, infer its placeholder values from the user's instructions,
render it with the CLI, and deliver the result.

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory install path.
QUICK_PROMPT_DIR=/absolute/path/from-the-skill-source-locator/quick-prompt
QP="$QUICK_PROMPT_DIR/scripts/quick_prompt.py"
test -f "$QP" || exit 1
```

Errors go to stderr as one JSON object `{"error", "message", ...}`. Exit codes: `0` ok, `2` usage,
`3` required values missing, `4` template not found, `5` template invalid.

## Use a template: `/quick-prompt "<selector>" "<instructions>"`

1. **Exact name.** If the selector is a kebab-case slug, try `"$QP" show <selector> --json`. Exit `0` →
   go to step 3. Exit `4` → step 2.
2. **Match on summaries only.** Run `"$QP" index` (one `name<TAB>summary` line per template). Choose by
   meaning against the selector. Never open template bodies to decide.
   - One clear match → use it.
   - Several plausible matches, or a weak best match → ask the user to pick from at most 4 candidates
     (name + summary), plus "none of these".
   - No match → say so and offer to create a template from the instructions (see Manage).
3. **Infer values.** Read `placeholders` from `"$QP" show <name> --json`.
   - Fill a value only from the instructions or facts already established in this session.
   - **Required, not stated → never guess.** Ask the user once for all missing required values together.
   - **Optional, not clearly stated → omit it.** Its default applies, or its `{{#var}}` section is dropped.
   - Pass only declared names; undeclared names are rejected (exit `2`).
4. **Render.** `printf '%s' "$VALUES_JSON" | "$QP" render <name> --values-json -`
   Exit `3` lists `missing` with descriptions — ask the user for exactly those, then render again.
5. **Deliver.** Infer the mode from the instructions; default is **run**:
   | Instruction cue | Action |
   |---|---|
   | none (default) | Treat the rendered prompt as your next task and execute it now. |
   | "show", "draft", "just give me the prompt", "don't run" | Print the rendered prompt in a fenced block and stop. |
   | "send to / hand to / give to <codex\|claude\|agy\|omp\|worker>" | Hand the rendered prompt to `terminal-agent-runner` as the task text. Do not execute it yourself. |

   Treat instruction words that only describe placeholder values as values, not delivery cues.

## Manage templates

The user manages the library through you. Write the template to a scratch file, then:

```bash
"$QP" validate --file /path/to/draft.md     # check before storing
"$QP" add <name> --file /path/to/draft.md   # refuses to overwrite
"$QP" update <name> --file /path/to/draft.md
"$QP" list [--tag T] [--json]
"$QP" show <name>
"$QP" remove <name>
"$QP" validate [name]                        # check the stored library
```

- **Before `update` or `remove`:** show the user the current template (`show <name>`) and what will change,
  and get confirmation. These overwrite or delete the user's files.

## Template format

```markdown
---
name: pr-review                 # kebab-case, equals the file name
summary: Severity-ranked review of a PR, optionally focused on one area   # <= 100 chars; the only text used for matching
tags: [review, git]             # optional inline list
placeholders:
  pr_ref:
    required: true              # required defaults to true when omitted
    description: PR number, URL, or branch
  focus:
    required: false
    description: Area to scrutinise
  tone:
    required: false
    default: balanced           # only optional placeholders may have a default
    description: strict | balanced | light
---
Review {{pr_ref}} with a {{tone}} bar.
{{#focus}}Pay particular attention to {{focus}}.{{/focus}}
```

- `{{var}}` inserts a value. `{{#var}}...{{/var}}` is kept only when `var` has a value. A section that sits
  alone on its line is removed together with that line. Sections cannot be nested.
- Every placeholder used in the body must be declared, and every declared placeholder must be used.
- Write summaries that say what the prompt does, so a short description from the user can match them.
- No includes and no dynamic values: the calling agent supplies every value.
