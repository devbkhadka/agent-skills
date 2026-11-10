# UI Layout Milestone

**Stage:** `STAGE_3_VERTICAL_SLICING`
**Sits between:** the Stage 3 gate and `STAGE_4_MILESTONE_EXECUTION`, per UI slice
**Renderer:** `bin/render-ui-mockup` (only for the `AGENT_MOCKUP` source, below)
**Approval state:** `AWAITING_UI_LAYOUT_APPROVAL` (improvement plan §5.1)

---

## 1. When a slice needs a layout

Any slice whose `verification_medium` is a UI medium (`rendered_ui`) must carry a `ui_layout` on
its declaration before Stage 4 may execute it. `bin/validate-execution-dag` raises
`UI_SLICE_WITHOUT_LAYOUT` for a UI-medium slice with none.

A slice can be implemented before anyone has seen the layout it renders; this milestone is where a
human looks at the screen and says yes, before that slice reaches Stage 4.

## 2. The three sources, in preference order

`ui_layout.source` is a closed choice:

1. **`FIGMA`** — the human supplies Figma links per UI flow. The agent resolves them through the
   Figma MCP and records `figma_node_refs` as file key plus node id, not a URL, so the reference is
   stable across a redesign that moves the file. If the project carries a Figma-to-UI skill, Stage
   4 uses it for that slice, and the skill name is recorded on the slice so the evidence says which
   path produced the code.
2. **`SCREENSHOT`** — an existing screen or hand-drawn layout, referenced by path under
   `.verified-feature-delivery/<slug>/ui-references/`.
3. **`AGENT_MOCKUP`** — the agent lays the screen out itself, mockup fidelity only, rendered by
   `bin/render-ui-mockup` from the slice's declared `layout_template` (one of the closed six in
   `templates/mockups/`, each parameterised by its regions) or a bare `layout_tree` when no
   template fits. Deliberately absent: styling, interaction, state, and any high-fidelity
   rendering — see §5.2 of the improvement plan for why a design tool is rejected here and an
   unstyled HTML file is not.

## 3. Approval is per slice, not per feature

Unlike `FLOW_SIMULATION` and `ENTITY_BOUNDARIES`, which each gate the whole feature once,
`UI_LAYOUT` approval is scoped to one slice's `ui_layout` at a time: a feature with three UI slices
carries three independent approval records, and Stage 4 can execute the slices whose layouts are
already approved while another slice's layout is still under review.

`bin/record-approval --milestone UI_LAYOUT --slice <slice_id>` pins a digest over that slice's
`ui_layout`. Editing an approved `ui_layout` invalidates only that slice's record, returns the
feature to `AWAITING_UI_LAYOUT_APPROVAL`, and deletes the `AGENT_MOCKUP` mockup rendered from it —
a stale rendered document is worse than none, per R1.
