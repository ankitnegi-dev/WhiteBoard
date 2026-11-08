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

A single button in the toolbar switches light/dark. The board remains light
paper in both themes. The choice is stored in memory only; persistence comes
later.
