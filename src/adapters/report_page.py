"""
adapters/report_page.py
The `master_<name>.html` index page: layout and styling only.

This module knows nothing about plotly, statistics or the filesystem layout of
a project. It receives ready-made `ReportFigure` fragments (produced by
`adapters.plotting`) and arranges them into one self-contained page.

WHY THE FIGURES ARE INLINED RATHER THAN IFRAMED: the page used to embed each
artifact in a fixed-height `<iframe>`. Every frame loaded its own copy of
plotly.js (~4.7 MB x 8) and scrolled independently, so the mouse wheel panned a
figure instead of the page and every plot needed manual repositioning. Inlining
the `<div>`s and loading `plotly.min.js` ONCE gives one document, one scrollbar
and one script.
"""

import html
import logging
import os
from dataclasses import dataclass
from typing import Optional, Sequence

# Written into the output folder by plotly itself (`include_plotlyjs="directory"`);
# the page and every standalone figure file reference it instead of embedding a copy.
PLOTLY_JS_FILENAME = "plotly.min.js"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportFigure:
    """One figure as the report sees it.

    `div` is a self-contained HTML fragment (a `<div>` plus its `<script>`)
    with NO plotly.js of its own. `group` is the page section the figure is
    filed under; figures sharing a group appear under one heading, side by side
    when `half_width` is set.
    """

    title: str
    path: str
    div: str
    group: str
    half_width: bool = False


_STYLESHEET = """
:root {
    --bg: #f4f6f8;
    --card: #ffffff;
    --ink: #1c2b36;
    --muted: #5b6b7a;
    --line: #dde4ea;
    --accent: #2c6e9b;
    --warn-bg: #fff8e1;
    --warn-line: #f0d48a;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: "Segoe UI", -apple-system, Roboto, Helvetica, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.5;
}
header {
    background: var(--card);
    border-bottom: 1px solid var(--line);
    padding: 24px 32px 0 32px;
}
header h1 {
    margin: 0;
    font-size: 26px;
    font-weight: 600;
    letter-spacing: -0.01em;
}
header p.subtitle {
    margin: 4px 0 16px 0;
    color: var(--muted);
    font-size: 14px;
}
nav {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
}
nav a {
    color: var(--muted);
    text-decoration: none;
    font-size: 14px;
    padding: 8px 12px;
    border-bottom: 2px solid transparent;
}
nav a:hover {
    color: var(--accent);
    border-bottom-color: var(--accent);
}
main {
    max-width: 1500px;
    margin: 0 auto;
    padding: 24px 32px 64px 32px;
}
section { margin-bottom: 32px; scroll-margin-top: 16px; }
section h2 {
    font-size: 15px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin: 0 0 12px 0;
}
.grid { display: grid; grid-template-columns: 1fr; gap: 16px; }
/* Fixed track count, NOT auto-fit: figures render while the page is still
   parsing, and an auto-fit grid with one parsed card would hand the first
   figure the full page width for its initial (sticky) plotly render. */
.grid.split { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (max-width: 1100px) {
    .grid.split { grid-template-columns: 1fr; }
}
.card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 8px 12px 12px 12px;
    overflow: hidden;
}
.card h3 {
    font-size: 14px;
    font-weight: 600;
    margin: 4px 4px 8px 4px;
    color: var(--ink);
}
.card h3 a {
    float: right;
    font-weight: 400;
    font-size: 12px;
    color: var(--muted);
    text-decoration: none;
}
.card h3 a:hover { color: var(--accent); text-decoration: underline; }
.notes {
    background: var(--warn-bg);
    border: 1px solid var(--warn-line);
    border-radius: 10px;
    padding: 12px 16px 12px 32px;
    margin: 0 0 24px 0;
}
.notes li { margin: 4px 0; }
footer {
    max-width: 1500px;
    margin: 0 auto;
    padding: 0 32px 48px 32px;
    color: var(--muted);
    font-size: 13px;
}
"""


def master_file(name: str) -> str:
    return f"master_{name}.html"


def _anchor(group: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in group.lower()).strip("-")


def _grouped(figures: Sequence[ReportFigure]):
    """`[(group, [figure, ...]), ...]` in first-appearance order."""
    order, buckets = [], {}
    for figure in figures:
        if figure.group not in buckets:
            order.append(figure.group)
            buckets[figure.group] = []
        buckets[figure.group].append(figure)
    return [(group, buckets[group]) for group in order]


def _card(figure: ReportFigure) -> str:
    link = html.escape(os.path.basename(figure.path) if figure.path else "")
    open_link = f'<a href="{link}" target="_blank">open separately</a>' if link else ""
    return (
        '<div class="card">'
        f"<h3>{html.escape(figure.title)}{open_link}</h3>"
        f"{figure.div}"
        "</div>"
    )


def _section(group: str, figures: Sequence[ReportFigure]) -> str:
    split = " split" if any(f.half_width for f in figures) else ""
    cards = "".join(_card(f) for f in figures)
    return (
        f'<section id="{_anchor(group)}">'
        f"<h2>{html.escape(group)}</h2>"
        f'<div class="grid{split}">{cards}</div>'
        "</section>"
    )


def write_master_html(name: str,
                      output_folder: str,
                      figures: Sequence[ReportFigure],
                      notes: Optional[Sequence[str]] = None,
                      subtitle: str = "") -> str:
    """Write `master_<name>.html` and return its path.

    `notes` render as a warning block at the top — censored open touches and an
    unusable frame rate travel with the dashboard instead of living only in the
    console log.
    """
    sections = _grouped(figures)
    nav = "".join(
        f'<a href="#{_anchor(group)}">{html.escape(group)}</a>' for group, _ in sections
    )
    note_block = ""
    if notes:
        items = "".join(f"<li>{html.escape(str(note))}</li>" for note in notes)
        note_block = f'<ul class="notes">{items}</ul>'

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(str(name))} — touch analysis</title>
<style>{_STYLESHEET}</style>
<script src="{PLOTLY_JS_FILENAME}" charset="utf-8"></script>
</head>
<body>
<header>
<h1>{html.escape(str(name))}</h1>
<p class="subtitle">{html.escape(subtitle)}</p>
<nav>{nav}</nav>
</header>
<main>
{note_block}
{"".join(_section(group, figs) for group, figs in sections)}
</main>
<footer>
Every figure is also written as a standalone file in this folder; they share
<code>{PLOTLY_JS_FILENAME}</code>, so keep the folder together when sharing.
</footer>
<script>
/* Figures render mid-parse, before the final layout settles (grid tracks,
   the scrollbar appearing) — and plotly's `responsive` flag only reacts to
   WINDOW resizes, so an early render keeps its stale width and is clipped
   by its card. One synthetic resize after load snaps every plot to its
   card's final width. */
window.addEventListener("load", function () {{
    window.dispatchEvent(new Event("resize"));
}});
</script>
</body>
</html>
"""
    path = os.path.join(output_folder, master_file(name))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    logger.debug("wrote %s", path)
    return path
