# Reusable React Mockup Kit & Screen Catalogue

A lightweight React mockup kit and screen catalogue designed for visual layout specification, scoped overlay interactivity (modals/drawers/accordions), and targeted numbered annotation badges for non-obvious domain context.

---

## 1. Minimal Layout Primitives

Lightweight baseline styling focused strictly on spatial structure, alignment, and viewport hierarchy:

- **`Box`**: Fundamental container supporting spatial props (`padding`, `margin`, `bg`, `border`, `rounded`, `width`, `height`, `shadow`).
- **`Flex`**: Direction (`row`, `column`), alignment, distribution, wrap, and gap.
- **`Grid`**: CSS grid container with column/row counts and template definitions.
- **`Stack`**: Vertical and horizontal content stacks with configurable item gaps and optional dividers.
- **`Card`**: Structured surface with header, title, subtitle, action slot, body, and footer.
- **`Button`**: Minimal structural buttons with variants (`primary`, `secondary`, `outline`, `ghost`, `danger`) and sizes (`sm`, `md`, `lg`).
- **`TextPlaceholder`**: Multi-line structural skeleton bars with animated gradient pulse.
- **`ImagePlaceholder`**: Aspect-ratio preserving placeholder frames for media assets.

---

## 2. Targeted Numbered Annotation Badge System

- **Selective Placement**: Used strictly for non-obvious UI elements, complex business rules, subtle domain mappings, or hidden state transitions (avoids cluttering self-explanatory elements).
- **Interaction**: Click or hover triggers a rich popover tooltip detailing:
  - **Business Intent**: Why the UI behavior is configured this way.
  - **Domain Entity / Lexicon Mapping**: The domain entity or value object binding.
  - **Projection Source**: State transitions or projection authorities triggering the change.
  - **Subtle Constraints**: Edge cases, precision limits, and SLA rules.
- **Annotation Inspector**: Built-in side panel listing all active screen annotations with click-to-highlight synchronization.

---

## 3. Scoped Local Interactivity

- **No Application Routing**: Prevents complex state simulation or mock-to-reality drift.
- **Inspectable Overlays**: Local interactive toggles restricted to overlay/collapsible containers:
  - `Modal`: Centered modal dialogs with backdrop blur and keyboard dismissal (`Esc`).
  - `Dialog`: Action confirmation and alert dialogs.
  - `Drawer`: Slide-out sidebar panels (right, left, bottom) for inspecting hidden detail layers.
  - `Accordion`: Expandable accordion panels with chevron indicators.
  - `Dropdown`: Menu overlays for option inspection.

---

## 4. Screen Catalogue & Standalone HTML Bundle

- **Central Catalogue Navigation**: Displays feature slices with declared viewport tags (e.g. `desktop_1280x800`, `mobile_375x667`) and annotation counts.
- **Viewport Frame & Zoom**: Preset selector and scale controls (100%, 90%, 80%, 75%, 50%).
- **Self-Contained Bundle**: Builds to `.verified-feature-delivery/<slug>/mockups/index.html` for direct human review without requiring external build dependencies.

---

## 5. Usage & Python Bundle Generator

To generate the standalone Screen Catalogue HTML bundle for a feature:

```python
from lib.mockup_bundle import render_mockup_bundle

# Renders self-contained bundle under .verified-feature-delivery/<slug>/mockups/index.html
out_file = render_mockup_bundle(
    feature_slug="order-settlement",
    feature_title="Order Settlement & Dispute Ledger",
)
```
