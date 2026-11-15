"""Build a self-contained HTML report from the technical report and SVG diagrams."""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "technical-report.md"
DIAGRAMS = ROOT / "diagrams"
SCREENSHOTS = ROOT / "screenshots"
OUTPUT = ROOT / "technical-report.html"

SCREENSHOT_FILES = [
    ("01-connect.png", "Connect screen."),
    ("02-main-window.png", "Main window with toolbar."),
    ("03-shapes-palette.png", "Shapes palette."),
    ("04-board-stickies-text.png", "Board with stickies and text."),
    ("05-participants-chat.png", "Participant list and chat."),
    ("06-host-controls.png", "Host controls."),
    ("07-export.png", "Export dialog."),
    ("08-dark-theme.png", "Dark theme."),
    ("09-rejoin.png", "Rejoin button."),
    ("10-reserved-name.png", "Reserved name message."),
]


def embed_diagrams(text: str) -> str:
    """Replace diagram image references with inline SVG content."""
    def replace(match):
        name = match.group(1)
        path = DIAGRAMS / name
        if not path.exists():
            return match.group(0)
        return "\n" + path.read_text(encoding="utf-8") + "\n"

    return re.sub(r"!\[[^\]]*\]\(diagrams/([^)]+)\)", replace, text)


def embed_screenshots(html_text: str) -> str:
    for name, caption in SCREENSHOT_FILES:
        path = SCREENSHOTS / name
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            replacement = (f'{name}<br><img src="data:image/png;base64,{data}" '
                           f'alt="{html.escape(caption)}" style="max-width:100%;border:1px solid #ccc;" />')
        else:
            replacement = (f'{name}<br><div class="screenshot-placeholder">'
                           f'Screenshot not provided: {html.escape(name)}<br>{html.escape(caption)}</div>')
        html_text = html_text.replace(name, replacement, 1)
    return html_text


def add_toc(html_text: str) -> str:
    headings = re.findall(r"<h([23])[^>]*id=\"([^\"]+)\"[^>]*>(.*?)</h\1>", html_text)
    if not headings:
        return html_text
    items = []
    for level, hid, title in headings:
        clean = re.sub(r"<[^>]+>", "", title)
        indent = "" if level == "1" else "  "
        items.append(f'{indent}<li><a href="#{hid}">{html.escape(clean)}</a></li>')
    toc = "<nav class=\"toc\"><h2>Contents</h2><ul>" + "".join(items) + "</ul></nav>"
    return toc + html_text


def main() -> None:
    source = REPORT.read_text(encoding="utf-8")
    source = embed_diagrams(source)
    body = markdown.markdown(
        source,
        extensions=["tables", "fenced_code", "toc"],
        extension_configs={"toc": {"permalink": False}},
    )
    body = embed_screenshots(body)
    body = add_toc(body)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>InkLAN Technical Report</title>
<style>
@page {{ size: A4; margin: 20mm; @bottom-center {{ content: "Page " counter(page); }} }}
body {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 11pt; line-height: 1.45; color: #222; }}
h1, h2, h3 {{ color: #1f3864; page-break-after: avoid; }}
h1 {{ page-break-before: always; font-size: 20pt; }}
h2 {{ page-break-before: always; font-size: 15pt; margin-top: 1.2em; }}
table {{ border-collapse: collapse; width: 100%; margin: 0.8em 0; font-size: 9.5pt; }}
th, td {{ border: 1px solid #999; padding: 4px 6px; vertical-align: top; }}
th {{ background: #e8eef7; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
code, pre {{ font-family: Consolas, monospace; font-size: 9.5pt; }}
pre {{ background: #f3f3f3; padding: 8px; overflow-x: auto; }}
nav.toc {{ page-break-after: always; }}
nav.toc ul {{ list-style: none; padding-left: 0; }}
nav.toc li {{ margin: 0.25em 0; }}
nav.toc a {{ text-decoration: none; color: #1f3864; }}
svg {{ width: 100%; max-width: 100%; height: auto; margin: 0.8em 0; }}
.screenshot-placeholder {{ background: #e5e7eb; color: #555; padding: 24px; text-align: center; border: 1px dashed #999; margin: 0.8em 0; }}
@media print {{
  a {{ color: #000; text-decoration: none; }}
}}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    OUTPUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()