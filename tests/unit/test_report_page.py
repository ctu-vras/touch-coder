"""
Unit tests for `adapters.report_page` — the master-page renderer.

Black-box against hand-built `ReportFigure` fragments; no plotly involved. The
page contract these pin down: one document (no iframes), one shared plotly.js
reference, groups in first-appearance order, and everything user-supplied
HTML-escaped except the figure `div` (trusted plotly output, embedded verbatim).
"""
import os

import pytest

from adapters.report_page import PLOTLY_JS_FILENAME, ReportFigure, master_file, write_master_html


def _figure(**overrides):
    defaults = dict(
        title="Trajectory",
        path=os.path.join("plots", "touch_trajectory.html"),
        div='<div id="fig1"><script>Plotly.newPlot();</script></div>',
        group="Trajectory",
        half_width=False,
    )
    defaults.update(overrides)
    return ReportFigure(**defaults)


def _render(tmp_path, figures, **kwargs):
    path = write_master_html("cat3", str(tmp_path), figures, **kwargs)
    with open(path, encoding="utf-8") as fh:
        return path, fh.read()


def test_master_filename(tmp_path):
    path, _ = _render(tmp_path, [_figure()])
    assert os.path.basename(path) == master_file("cat3") == "master_cat3.html"


def test_single_plotly_js_reference_and_no_iframes(tmp_path):
    _, page = _render(tmp_path, [_figure(), _figure(title="Other", group="Summary")])
    assert page.count(f'src="{PLOTLY_JS_FILENAME}"') == 1
    assert "<iframe" not in page.lower()


def test_div_embedded_verbatim(tmp_path):
    div = '<div id="weird"><script>let a = 1 < 2 && "x";</script></div>'
    _, page = _render(tmp_path, [_figure(div=div)])
    assert div in page


def test_groups_render_in_first_appearance_order_with_nav(tmp_path):
    figures = [
        _figure(title="A", group="Trajectory"),
        _figure(title="B", group="Zone transitions"),
        _figure(title="C", group="Trajectory"),
    ]
    _, page = _render(tmp_path, figures)
    assert page.index('id="trajectory"') < page.index('id="zone-transitions"')
    assert page.count("<section") == 2
    assert '<a href="#trajectory">Trajectory</a>' in page
    assert '<a href="#zone-transitions">Zone transitions</a>' in page
    # both Trajectory cards live in the one Trajectory section
    section = page[page.index('id="trajectory"'):page.index('id="zone-transitions"')]
    assert "<h3>A" in section and "<h3>C" in section


def test_split_grid_only_when_half_width(tmp_path):
    _, page = _render(tmp_path, [_figure()])
    assert "grid split" not in page
    _, page = _render(
        tmp_path,
        [_figure(half_width=True), _figure(title="Other", half_width=True)],
    )
    assert 'class="grid split"' in page


def test_titles_notes_and_subtitle_are_escaped(tmp_path):
    _, page = _render(
        tmp_path,
        [_figure(title="a<b> & c")],
        notes=["1 open <touch>"],
        subtitle="25 fps <metadata>",
    )
    assert "a&lt;b&gt; &amp; c" in page
    assert "1 open &lt;touch&gt;" in page
    assert "25 fps &lt;metadata&gt;" in page
    assert "<b>" not in page


def test_resize_dispatched_after_load(tmp_path):
    """Plotly's `responsive` only reacts to WINDOW resizes; figures render
    mid-parse before the layout settles, so the page must fire one synthetic
    resize on load or early figures keep a stale width and get clipped."""
    _, page = _render(tmp_path, [_figure()])
    assert 'window.dispatchEvent(new Event("resize"))' in page


def test_notes_block_absent_without_notes(tmp_path):
    _, page = _render(tmp_path, [_figure()])
    assert 'class="notes"' not in page


def test_open_separately_link_uses_basename(tmp_path):
    _, page = _render(tmp_path, [_figure(path=os.path.join("some", "dir", "table.html"))])
    assert '<a href="table.html" target="_blank">' in page


def test_open_separately_link_omitted_for_pathless_figure(tmp_path):
    _, page = _render(tmp_path, [_figure(path="")])
    assert "open separately" not in page
