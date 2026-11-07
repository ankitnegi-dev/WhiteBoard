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
- highlight: #E8B923 (yellow marker, used for selection and focus)
- danger: #B83A2E
- border: #D6D0C4

Dark theme:

- bg: #1B1F24
- surface: #262B33
- surface_alt: #1F2329
- ink: #E8E6E1
- ink_muted: #9CA3AF
- accent: #5FA8A8
- accent_hover: #72B8B8
- highlight: #D6A84F
- danger: #D06050
- border: #3A4048

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
- Right rail: 280 px wide, split between participants (top) and chat (bottom).
- Status bar: 24 px high, single line.

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

- Buttons: flat, 6 px radius via clam, accent background, white text, 8 px
  horizontal padding, 4 px vertical. Hover darkens accent by 10 percent.
- Toolbar buttons: 56 x 56, icon above label, selected state uses highlight
  background with ink text.
- Panels: surface background, 1 px border in border color, 12 px padding.
- List rows: 28 px high, hover uses surface_alt, selected uses accent with
  white text.
- Chat message: name in bold user color, timestamp in muted small, body in
  ink with a 4 px left indent.
- Inputs: surface background, 1 px border, 4 px padding, focus ring in
  highlight color (2 px).
- Canvas: bg color from theme, no border, crosshair cursor while drawing.

## 6. Motion

Only action-driven motion: selection highlight, button press. No entrance
animations. Cursor markers fade in over 80 ms when a new user appears.

## 7. Accessibility

- Visible focus ring on every interactive widget.
- Contrast: ink on bg must be at least 4.5:1 in both themes.
- All actions reachable by keyboard; shortcuts use Control on Windows/Linux
  and Command on macOS.

## 8. Theme toggle

A single button in the toolbar switches light/dark. The choice is stored
in memory only for Day 3; persistence comes later.
