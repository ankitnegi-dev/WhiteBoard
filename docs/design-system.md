# Design System: LAN Collaborative Whiteboard

Applies the frontend-design skill to a tkinter + ttk desktop client. The
subject is a collaborative whiteboard: chalk, paper, ink, grid, and the
feel of a shared desk. The UI should read as a tool, not a web page.

## 1. Color tokens

Light theme (default):

- bg: #F4F1EA (warm paper)
- surface: #FFFFFF
- surface_alt: #ECE7DC
- ink: #1F2937
- ink_muted: #6B7280
- accent: #1E5B6B (deep teal ink, used for primary actions)
- accent_hover: #174A57
- on_accent: #FFFFFF (text and icons on accent)
- highlight: #E8B923 (yellow marker, used for selection and focus)
- danger: #B83A2E
- border: #D6D0C4
- canvas: #DCD3C1 (surround outside the paper)
- paper: #FBF8F1 (the drawable board, always light)
- paper_border: #8A7F68
- board_accent: #1E5B6B (eraser hover, readable on paper)

Dark theme:

- bg: #1B1F24
- surface: #262B33
- surface_alt: #1F2329
- ink: #E8E6E1
- ink_muted: #9CA3AF
- accent: #5FA8A8
- accent_hover: #72B8B8
- on_accent: #102022 (dark text on the light teal accent)
- highlight: #D6A84F
- danger: #D06050
- border: #3A4048
- canvas: #12161A
- paper: #FBF8F1
- paper_border: #8A7F68
- board_accent: #1E5B6B

The board is light paper in both themes. Only the surround and chrome
change, so ink, user stroke colors, and screenshots stay readable when the
dark theme is active.

Per-user colors are assigned by the server from USER_COLORS in
server/room.py. They are used for strokes, cursor markers, and the
participant list dot. They must never be used for chrome or buttons.

## 2. Typography

Font stack, in order of preference:

1. Segoe UI (Windows)
2. Ubuntu (Linux)
3. DejaVu Sans (Linux fallback)
4. TkDefaultFont (last resort)

Sizes (in points):

- title: 20, bold
- heading: 16, bold
- body: 11, normal
- small: 10, normal
- mono: 10 (for room codes and seq, TkFixedFont family)

Line height is handled by tkinter padding; prefer 4 px of vertical padding
between lines of text rather than a computed leading.

Weights: bold for titles, participant names, and primary button labels;
normal everywhere else. Avoid italic except for empty-state hints.

## 3. Spacing scale

Base unit 4 px. Use only these values: 4, 8, 12, 16, 24, 32, 48.
Padding inside panels is 16. Gap between toolbar buttons is 8.
Canvas outer padding is 12.

## 4. Layout

- Window minimum: 1100 x 700.
- Left rail: 72 px wide, icon + label toolbar, vertical.
- Center: canvas fills remaining space, keeps 16:9 logical aspect.
- Right rail: 320 px fixed width, split between participants (top) and chat
  (bottom). pack_propagate is disabled so content never resizes the rail.
- Status bar: 24 px high, single line, fixed character widths per label.

ASCII wireframe:

    +-----------------------------------------------------------+
    |  toolbar(72)  |  canvas (scaled, 16:9)  | participants  |
    |               |                         | chat          |
    |               |                         |               |
    +-----------------------------------------------------------+
    |  status bar: connection | room code | latency | seq       |
    +-----------------------------------------------------------+

Alignment is left for text, center for the canvas, right for status numbers.

## 5. Component styles (ttk, "clam" base)

- Buttons: flat 1 px outline, accent background, on_accent text, 8 px
  horizontal padding, 4 px vertical. Hover darkens or lightens accent.
- Disabled buttons: surface_alt background and ink_muted text, always at
  least 3:1 contrast, never hidden. Tooltips say "Host only" or
  "Viewers cannot do this" as appropriate.
- Toolbar buttons: flat outline, surface background, ink text. The selected
  tool uses accent background with on_accent text and is visibly different
  in both themes.
- Panels: surface background, 1 px border in border color, 12 px padding.
- List rows: 28 px high, hover uses surface_alt, selected uses accent with
  on_accent text. Each row reserves a fixed drawing-indicator slot.
- Chat message: name in bold user color, timestamp in muted small, body in
  the theme ink color, so the body remains visible after a theme switch.
- Inputs and dropdowns: surface background, 1 px border, ink text, focus
  ring in highlight color (2 px). Dropdown lists use surface and on_accent
  selection colors.
- Canvas: the widget background is the theme surround; a light paper
  rectangle is drawn inside it with a 2 px paper_border outline. Drawing is
  clamped to the paper and a matte hides anything outside it.

## 6. Motion

Only action-driven motion: selection highlight, button press. No entrance
animations. Cursor markers fade in over 80 ms when a new user appears.

## 7. Coordinate transform

All pixel to logical conversions go through client/tools.py:
canvas_transform, window_to_logical, logical_to_window, is_inside_board,
and board_rect. There is exactly one copy of the scale and letterbox math.

- The widget border and highlight thickness are passed as the inset.
- canvasx and canvasy are used before the transform for scroll safety.
- The pen press point, the eraser hit-test, remote cursor markers, the board
  background, and the debug overlay all use this same pair of functions.
- Presses outside the paper are ignored. Motion and release points are
  clamped to 0..1600 by 0..900 before they are drawn or sent.

Ctrl+Shift+D toggles a debug overlay showing the raw event pixel, canvas
pixel, logical coordinate, board rectangle, scale, offset, and inset.

## 8. Accessibility

- Visible focus ring on every interactive widget.
- Contrast: text and icon pairs are at least 3:1 in both themes, including
  disabled button text. Body text targets 4.5:1 or better.
- Disabled actions stay visible and explain their restriction in a tooltip.
- All actions reachable by keyboard; shortcuts use Control on Windows/Linux
  and Command on macOS.

## 9. Theme toggle

A single button in the header (next to the identity chip) switches light/dark.
The board remains light paper in both themes. The choice is stored in memory
only; persistence comes later.

## 10. Toolbar layout (top, two rows)

The toolbar is a top, wrap-free bar above the board. The old left rail is
gone, so the board uses the full width beside the right rail. There is no
scrolling anywhere in the toolbar: a Scrollbar or a canvas scroll container
is never used for toolbar controls.

Row 1, left to right:

- Tools: Pen, Eraser, Text. A Select tool is not present in this build.
- Shapes: a split control. The main button activates the last-used shape
  and shows that shape as a small drawn icon. The small arrow button opens
  the grouped Shapes palette (Basic, Flowchart, Callout).
- Divider.
- Style: Color button plus a clickable color swatch, Width slider with a
  numeric value and a preview dot, Fill toggle, Text size dropdown.

Row 2, left to right:

- Edit: Undo, Redo.
- Divider.
- File: Clear, Save, Load.
- A reserved gap for Export and History. Those buttons are not created
  until those features exist.
- View controls are omitted because this build has no zoom controls.

Every toolbar button uses the same fixed height, currently 37 px (the
design target was about 34 px), and is sized from its content with a
sensible minimum width. Labels and icons are never clipped. Each button
has a tooltip, an active/selected style (accent fill), and a readable
disabled style in both themes. Host-only actions stay visible but disabled
for editors; viewer-disabled actions use the same rule.

Theme and Show cursors live in the identity header in the right rail, on a
second line under the user name and role. Keeping them there means a long
username can never push them off screen. The toggles use full labels and
tooltips.

The layout is verified at 1280x720, 1366x768, and 1920x1080. At 1280 wide,
row 1 requests about 900 px and row 2 about 543 px, so both rows fit without
shortening labels or adding a scrollbar. If a future control cannot fit,
shorten a label or move it to an icon with a tooltip; do not add a toolbar
scrollbar.

The right rail order is identity, room code, participants, host controls,
then chat. Chat text absorbs the remaining height and the chat input is
pinned to the bottom, so host controls and chat input stay reachable at the
minimum supported window height.

