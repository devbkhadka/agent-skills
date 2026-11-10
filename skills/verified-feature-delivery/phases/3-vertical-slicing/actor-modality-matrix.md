# Actor Modality Matrix & Headless Fallbacks

**Stage:** `STAGE_3_VERTICAL_SLICING`  
**Owner:** `stage-3`  
**Purpose:** Defines input modalities, environment floors, attention-return edges, and headless fallbacks for actor-facing slices in the Execution DAG.

---

## 1. Modality Dimensions

For any vertical slice whose declared verification medium is actor-facing (`rendered_ui`, `device_actuation`, `agent_transcript`), the slice definition must carry an **Actor Modality Matrix**:

### 1.1 Input Modalities
Declares the interaction channels through which the slice's trigger can be initiated and completed:
* `pointer`: Mouse, trackpad, pen clicks, drags, hover states.
* `keyboard`: Tab focus traversal, keyboard shortcuts, Enter/Space actuation.
* `touch`: Tap, swipe, pinch gestures on mobile or touchscreens.
* `voice`: Speech recognition, voice command triggers.
* `physical_control`: Hardware buttons, dials, switches, serial triggers.
* `api_agent_caller`: Programmatic headless or tool-driven invocations.

### 1.2 Environment Floor
Defines the minimum operational environment under which the slice must function:
* `viewport_floor`: Minimum viewport dimensions (e.g. `viewport_360x640`).
* `network_floor`: Offline resilience, high-latency (e.g. 3G simulation), or degraded-link behavior.
* `resource_floor`: Throttled CPU, low-memory, or battery-saver execution state.

### 1.3 Attention Return
Cross-slice focus and state transitions modeled as directed DAG edges:
* Format: `(source_slice, destination_slice, focus_target)`
* The DAG validator enforces that `destination_slice` is an existing slice ID in `slices[]`.
* Prevents dangling focus states when dismissals or error recoveries occur.

### 1.4 Headless / Non-Interactive Fallback
Specifies behavior when executed unattended without an interactive TTY or display:
* `stdin` closed / `isatty(stdin) == false`: Clean exit or non-blocking default behavior without hanging prompts.
* `no_display`: Fallback to headless logs, JSON stdout, or mock drivers.

---

## 2. Validation & Enforcement

* `bin/validate-execution-dag` evaluates `attention_return` cross-slice tuples against declared slice IDs (`DANGLING_ATTENTION_RETURN`).
* `bin/validate-execution-dag` validates declared `verification_medium` against the closed canonical set (`MISSING_VERIFICATION_MEDIUM`).
* Slices omitting executable verification scripts fail Gate 3 (`SLICE_MISSING_VERIFICATION_SCRIPT`).
