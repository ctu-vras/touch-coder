"""
adapters/plotting.py
Every plotly figure and CSV table the Analysis button produces.

This is a pure OUTPUT adapter: it receives already-computed domain objects
(`domain.touch_stats.Episode` / `LimbStats` / transition matrices) and turns
them into files under `output_folder`. It never reads an export CSV, never
computes a statistic and never opens a browser — the numbers must be
reproducible without plotly, and the browser is the GUI's business.

Each figure writer returns a `ReportFigure` that `adapters.report_page`
arranges into `master_<name>.html`. Written artifacts (names are a contract:
users bookmark them):

    heatmap_<LIMB>.html       one transition heatmap per limb
    touch_trajectory.html     2x2 click-trajectory grid over the limb diagrams
    analysis_table_frames.csv frame-based per-limb table
    analysis_table_seconds.csv seconds-based per-limb table
    table.html                rendered summary table
    histogram.html            touch length distribution (in onsets)
    histogram_2.html          touch duration distribution
    plotly.min.js             ONE shared plotly bundle (written by plotly via
                              include_plotlyjs="directory"); the master page and
                              every standalone figure file reference it, so the
                              folder must travel together.

UNUSABLE FRAME RATE: when `fps` is None/0/negative every seconds cell is
written as an empty value and the duration histogram falls back to FRAME
buckets with a relabelled axis. Nothing raises and nothing is skipped, so the
master page never links a file that was not written.
"""

import base64
import logging
import math
import os
from typing import Dict, Sequence, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from PIL import Image
from plotly.subplots import make_subplots

from adapters.report_page import ReportFigure
from domain.model import LIMBS
from domain.touch_stats import (
    Episode,
    LimbStats,
    duration_histogram_buckets,
    fps_is_usable,
)
from gui.resource_utils import asset_path


logger = logging.getLogger(__name__)

# Panel titles, in `domain.model.LIMBS` order.
LIMB_TITLES = {"LH": "Left Hand", "RH": "Right Hand", "LL": "Left Leg", "RL": "Right Leg"}

# On-screen panel order (trajectory grid and heatmap grid): mirrored, because
# the limb diagrams face the viewer — the subject's RIGHT hand is on the
# viewer's LEFT, so each panel sits on the side of the page its limb occupies
# on the diagram. Data/CSV order stays `domain.model.LIMBS`; this is display only.
DISPLAY_LIMB_ORDER = ("RH", "LH", "RL", "LL")

TRAJECTORY_FILE = "touch_trajectory.html"
TABLE_FILE = "table.html"
ONSET_HISTOGRAM_FILE = "histogram.html"
DURATION_HISTOGRAM_FILE = "histogram_2.html"
FRAMES_TABLE_CSV = "analysis_table_frames.csv"
SECONDS_TABLE_CSV = "analysis_table_seconds.csv"

# Master-page sections, in display order (report_page groups cards by these).
GROUP_TRAJECTORY = "Trajectory"
GROUP_SUMMARY = "Summary"
GROUP_DISTRIBUTIONS = "Distributions"
GROUP_TRANSITIONS = "Zone transitions"

PLOTLY_TEMPLATE = "plotly_white"

# Shared plotly config: the wheel scrolls the PAGE, never a figure. Zooming is
# an explicit drag-select (double-click resets). Figures that need hover but no
# zoom/pan at all also drop the modebar.
FIGURE_CONFIG = {"scrollZoom": False, "displaylogo": False, "responsive": True}
_STATIC_FIGURE_CONFIG = {**FIGURE_CONFIG, "displayModeBar": False}

# Categorical limb palette, in LIMBS order — a colorblind-validated adjacent
# ordering (worst adjacent-pair CVD dE 9.1, normal-vision dE 22.9 on white).
LIMB_COLORS = {"LH": "#2a78d6", "RH": "#eb6834", "LL": "#1baf7a", "RL": "#eda100"}

# Heatmap height follows the zone-axis length so a ~38-zone matrix gets
# readable rows instead of being crammed into a fixed frame.
HEATMAP_CELL_PX = 18
HEATMAP_CHROME_PX = 160
HEATMAP_MIN_HEIGHT_PX = 420
HEATMAP_MAX_HEIGHT_PX = 1200

TRAJECTORY_GRID = (2, 2)      # rows x cols for the four limb panels
TRAJECTORY_CHROME_PX = 120    # subplot titles + margins on top of the image rows
SUMMARY_TABLE_HEIGHT_PX = 340
TABLE_HEADER_FILL = "#e8edf2"
TABLE_CELL_FILL = "#ffffff"

# Marker styling by EpisodePoint role (see domain.touch_stats.EpisodePoint).
# Role is double-encoded — status color AND symbol — because start (green) and
# open (dark yellow) are near-identical under red-green colorblindness.
_START_COLOR, _MID_COLOR, _END_COLOR, _OPEN_COLOR = "#0ca30c", "#52514e", "#d03b3b", "#c98500"
_START_SYMBOL, _MID_SYMBOL, _END_SYMBOL, _OPEN_SYMBOL = "circle", "circle", "square", "diamond"
_EDGE_SIZE, _MID_SIZE = 15, 8
_TRACE_LINE_COLOR = "#52514e"

_MISSING = "n/a"


def heatmap_file(limb: str) -> str:
    return f"heatmap_{limb}.html"


def limb_image_paths(new_template: bool = False) -> list:
    """The four limb diagrams the trajectory panels are drawn on, in LIMBS order."""
    suffix = "_new_template" if new_template else ""
    return [asset_path(f"icons/{limb}{suffix}.png") for limb in LIMBS]


def display_limb_order(limbs: Sequence[str] = LIMBS) -> list:
    """`limbs` reordered for display: mirrored pairs first (`DISPLAY_LIMB_ORDER`),
    any limb not covered by the mirror rule appended in its original position."""
    ordered = [limb for limb in DISPLAY_LIMB_ORDER if limb in limbs]
    return ordered + [limb for limb in limbs if limb not in ordered]


def heatmap_height(n_zones: int) -> int:
    """Figure height for an `n_zones` x `n_zones` transition matrix, clamped so
    tiny matrices are not postage stamps and huge ones do not dwarf the page."""
    return max(HEATMAP_MIN_HEIGHT_PX,
               min(HEATMAP_MAX_HEIGHT_PX, n_zones * HEATMAP_CELL_PX + HEATMAP_CHROME_PX))


def _write_figure(fig, output_folder: str, filename: str, *,
                  title: str, group: str, half_width: bool = False,
                  config=None) -> ReportFigure:
    """Write the standalone HTML file and return the master-page fragment.

    `include_plotlyjs="directory"` makes plotly write ONE `plotly.min.js` into
    `output_folder` (skipped when present) that every figure file references;
    the returned `div` carries no bundle either — the master page loads it once.
    """
    config = config or FIGURE_CONFIG
    path = os.path.join(output_folder, filename)
    fig.write_html(path, include_plotlyjs="directory", config=config)
    div = fig.to_html(full_html=False, include_plotlyjs=False, config=config)
    logger.debug("wrote %s", path)
    return ReportFigure(title=title, path=path, div=div, group=group, half_width=half_width)


# --- heatmaps ---------------------------------------------------------------

def write_transition_heatmap(transition_df: pd.DataFrame,
                             zones: Sequence[str],
                             limb: str,
                             output_folder: str) -> ReportFigure:
    """One start-zone x end-zone heatmap. The subtitle states the pairwise rule
    because the totals legitimately exceed the touch count (see
    `domain.touch_stats.transitions`)."""
    zones = list(zones)
    # Pin the scale at [0, max] so zero cells are always the lightest step; an
    # all-zero matrix would otherwise degenerate to a solid mid-blue (min==max).
    max_count = float(transition_df.values.max()) if transition_df.size else 0.0
    fig = px.imshow(
        transition_df,
        labels=dict(x="End Zone", y="Start Zone", color="Number of Touches"),
        x=zones,
        y=zones,
        color_continuous_scale="Blues",
        zmin=0,
        zmax=max_count if max_count > 0 else 1.0,
        aspect="auto",
        template=PLOTLY_TEMPLATE,
    )
    fig.update_traces(
        hovertemplate="Start Zone: %{y}<br>End Zone: %{x}<br>Number of Touches: %{z}<extra></extra>"
    )
    fig.update_layout(
        title=(
            f"Touch Transition Heatmap {limb}<br>"
            "<sup>Multi-zone start/end clicks contribute one count per zone pair.</sup>"
        ),
        height=heatmap_height(len(zones)),
        margin=dict(l=50, r=50, t=70, b=50),
        # Counts are integers — never let the colorbar tick at 0.5 steps.
        coloraxis_colorbar=dict(dtick=max(1, math.ceil(max_count / 8))),
    )
    ticks = dict(
        tickmode="array", tickvals=list(range(len(zones))), ticktext=zones,
        tickfont=dict(size=10), automargin=True, fixedrange=True,
    )
    # Vertical x labels: at half-page width a ~38-column axis leaves ~15px per
    # column, where auto-angled labels overlap.
    fig.update_xaxes(tickangle=-90, **ticks)
    fig.update_yaxes(**ticks)
    return _write_figure(
        fig, output_folder, heatmap_file(limb),
        title=f"Transition heatmap — {LIMB_TITLES.get(limb, limb)} ({limb})",
        group=GROUP_TRANSITIONS,
        half_width=True,
        config=_STATIC_FIGURE_CONFIG,
    )


# --- trajectory -------------------------------------------------------------

def _point_style(point, is_open_tail: bool):
    """(color, size, symbol) for one EpisodePoint.

    Preserves the historical scheme: the FIRST click of the onset row is the
    green start marker, the LAST click of the offset row the red end marker,
    everything else a small gray waypoint. The final click of an unterminated
    touch is dark yellow so a censored episode is visible instead of silently
    vanishing from the plot. Symbols repeat the role for colorblind readers.
    """
    if is_open_tail:
        return _OPEN_COLOR, _EDGE_SIZE, _OPEN_SYMBOL
    if point.role == "start" and point.click_index == 1:
        return _START_COLOR, _EDGE_SIZE, _START_SYMBOL
    if point.role == "end" and point.is_last_in_frame:
        return _END_COLOR, _EDGE_SIZE, _END_SYMBOL
    return _MID_COLOR, _MID_SIZE, _MID_SYMBOL


def _hover_text(point, episode) -> str:
    zone_text = ", ".join(point.zones) if point.zones else _MISSING
    state = "OPEN (no offset)" if not episode.closed else (point.onset or "-")
    return (
        f"Frame: {point.frame}<br>"
        f"Point: {point.click_index}/{point.clicks_in_frame}<br>"
        f"X: {point.x}<br>Y: {point.y}<br>"
        f"Onset: {state}<br>Zone: {zone_text}"
    )


def _episode_trace(episode: Episode, name: str):
    """One scatter trace per episode, or None when the episode has no clicks.

    Hover text is built in the SAME loop as the coordinates, so `text` can never
    be longer than `x`/`y` again (it once was, which shifted every hover label
    onto the wrong point).
    """
    if not episode.points:
        return None
    xs, ys, colors, sizes, symbols, texts = [], [], [], [], [], []
    last_index = len(episode.points) - 1
    for idx, point in enumerate(episode.points):
        is_open_tail = (not episode.closed) and idx == last_index
        color, size, symbol = _point_style(point, is_open_tail)
        xs.append(point.x)
        ys.append(point.y)
        colors.append(color)
        sizes.append(size)
        symbols.append(symbol)
        texts.append(_hover_text(point, episode))
    return go.Scatter(
        x=xs,
        y=ys,
        mode="markers+lines",
        marker=dict(color=colors, size=sizes, symbol=symbols,
                    line=dict(width=1, color="#ffffff")),
        line=dict(color=_TRACE_LINE_COLOR, width=2, dash="dot"),
        name=name,
        text=texts,
        hovertemplate="%{text}<extra></extra>",
    )


def write_trajectory_plot(episodes_by_limb: Dict[str, Sequence[Episode]],
                          image_paths: Sequence[str],
                          output_folder: str) -> ReportFigure:
    """2x2 trajectory grid, one trace per episode, drawn over the limb
    diagrams. Open episodes get an `(open)` trace name and a diamond tail
    marker; every click that counts toward the statistics appears here.

    Width is left responsive; `scaleanchor` keeps each panel pixel-true to its
    background image at any container width (plotly letterboxes the rest).
    """
    rows, cols = TRAJECTORY_GRID
    panel_limbs = display_limb_order(LIMBS)
    image_by_limb = dict(zip(LIMBS, image_paths))
    fig = make_subplots(
        rows=rows,
        cols=cols,
        subplot_titles=tuple(LIMB_TITLES[limb] for limb in panel_limbs),
        horizontal_spacing=0.04,
        vertical_spacing=0.08,
    )

    max_img_height = 0
    for i, limb in enumerate(panel_limbs):
        row, col = divmod(i, cols)
        row, col = row + 1, col + 1

        with Image.open(image_by_limb[limb]) as img:
            img_width, img_height = img.size
        max_img_height = max(max_img_height, img_height)
        with open(image_by_limb[limb], "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode()

        for n, episode in enumerate(episodes_by_limb.get(limb, ()), start=1):
            suffix = "" if episode.closed else " (open)"
            trace = _episode_trace(episode, f"{limb} touch {n}{suffix}")
            if trace is not None:
                fig.add_trace(trace, row=row, col=col)

        axis_id = "" if i == 0 else str(i + 1)
        fig.add_layout_image(
            dict(
                source=f"data:image/png;base64,{encoded_image}",
                xref=f"x{axis_id}",
                yref=f"y{axis_id}",
                x=img_width / 2,
                y=img_height / 2,
                xanchor="center",
                yanchor="middle",
                sizex=img_width,
                sizey=img_height,
                sizing="contain",
                opacity=1,
                layer="below",
            ),
            row=row,
            col=col,
        )
        # STATIC ranges: `autorange` must stay off — `autorange="reversed"`
        # recomputes the range from the data, so every panel zoomed to its own
        # point cloud instead of showing the whole diagram. The y range is
        # reversed explicitly (top-left image origin) and pinned to the image.
        fig.update_xaxes(
            visible=False, range=[0, img_width], autorange=False,
            fixedrange=False, row=row, col=col,
        )
        fig.update_yaxes(
            visible=False, range=[img_height, 0], autorange=False,
            fixedrange=False, scaleanchor=f"x{axis_id}", scaleratio=1,
            row=row, col=col,
        )

    fig.update_layout(
        height=rows * max_img_height + TRAJECTORY_CHROME_PX,
        showlegend=False,
        template=PLOTLY_TEMPLATE,
        margin=dict(l=0, r=0, t=50, b=20),
    )
    return _write_figure(
        fig, output_folder, TRAJECTORY_FILE,
        title="Touch trajectories over the limb diagrams",
        group=GROUP_TRAJECTORY,
    )


# --- tables -----------------------------------------------------------------

def write_analysis_tables(stats: Sequence[LimbStats],
                          fps,
                          output_folder: str) -> Tuple[str, str]:
    """Write both per-limb CSV tables and return their paths (frames, seconds).

    Column names are frozen (external tooling reads these CSVs). The only
    addition is `Open (Unterminated) Touches`, which reports episodes censored
    for having no offset — they are excluded from every other figure in the row.
    When `fps` is unusable, every seconds column is empty rather than 0 so a
    reader cannot mistake "unknown" for "zero".
    """
    limbs = [s.limb for s in stats]
    df_frames = pd.DataFrame(
        {
            "Limb": limbs,
            "Total Touches": [s.total_touches for s in stats],
            "Open (Unterminated) Touches": [s.open_touches for s in stats],
            "Touch Durations [Frames]": [list(s.durations_frames) for s in stats],
            "Total Duration [Frames]": [s.total_duration_frames for s in stats],
            "Average Touch Duration [Frames]": [s.mean_duration_frames for s in stats],
            "Percentage Touching": [s.percentage_touching for s in stats],
            "Touch Rate [Touches per 100 Frames]": [s.touch_rate_per_100_frames for s in stats],
            "Standard Deviation [Frames]": [s.stdev_duration_frames for s in stats],
        }
    )
    frames_path = os.path.join(output_folder, FRAMES_TABLE_CSV)
    df_frames.to_csv(frames_path, index=False)
    logger.debug("wrote %s", frames_path)

    if not fps_is_usable(fps):
        logger.warning(
            "frame rate unusable (%r) — seconds-based table columns written as empty values",
            fps,
        )

    df_seconds = pd.DataFrame(
        {
            "Limb": limbs,
            "Total Touches": [s.total_touches for s in stats],
            "Open (Unterminated) Touches": [s.open_touches for s in stats],
            "Touch Durations [Seconds]": [
                list(s.durations_seconds) if s.durations_seconds is not None else None
                for s in stats
            ],
            "Total Duration [Seconds]": [s.total_duration_seconds for s in stats],
            "Average Touch Duration [Seconds]": [s.mean_duration_seconds for s in stats],
            "Percentage Touching": [s.percentage_touching for s in stats],
            "Touch Rate [Touches per Minute]": [s.touch_rate_per_minute for s in stats],
            "Standard Deviation [Seconds]": [s.stdev_duration_seconds for s in stats],
        }
    )
    seconds_path = os.path.join(output_folder, SECONDS_TABLE_CSV)
    df_seconds.to_csv(seconds_path, index=False)
    logger.debug("wrote %s", seconds_path)
    return frames_path, seconds_path


def _fmt(value, integer: bool = False) -> str:
    """Table cell formatting. `None` becomes 'n/a' — never 'None' or 'nan',
    which readers have mistaken for a computed value."""
    if value is None:
        return _MISSING
    if isinstance(value, float) and math.isnan(value):
        return _MISSING
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{int(value)}" if integer else f"{value:.2f}"
    return str(value)


def write_summary_table(stats: Sequence[LimbStats],
                        total_frames: int,
                        fps,
                        output_folder: str) -> ReportFigure:
    """Rendered summary table (table.html).

    The title reports the video length in seconds, or `unknown` when the frame
    rate is unusable. A non-zero open-touch count is called out in the subtitle
    so a censored episode cannot be missed.
    """
    columns = {
        "Limb": [s.limb for s in stats],
        "Total Touches": [_fmt(s.total_touches, integer=True) for s in stats],
        "Open (Unterminated)": [_fmt(s.open_touches, integer=True) for s in stats],
        "Total Duration [Seconds]": [_fmt(s.total_duration_seconds) for s in stats],
        "Average Touch Duration [Seconds]": [_fmt(s.mean_duration_seconds) for s in stats],
        "Standard Deviation [Seconds]": [_fmt(s.stdev_duration_seconds) for s in stats],
        "Percentage Touching": [_fmt(s.percentage_touching) for s in stats],
        "Touch Rate [Touches per Minute]": [_fmt(s.touch_rate_per_minute) for s in stats],
    }

    fig = go.Figure(
        data=[
            go.Table(
                header=dict(values=list(columns.keys()), fill_color=TABLE_HEADER_FILL,
                            align="left", font=dict(size=13)),
                cells=dict(values=list(columns.values()), fill_color=TABLE_CELL_FILL,
                           align="left", height=26),
            )
        ]
    )

    if fps_is_usable(fps):
        length_text = f"{total_frames / float(fps):.2f} Seconds"
    else:
        length_text = f"unknown (frame rate {fps!r}); {total_frames} frames"
    open_total = sum(s.open_touches for s in stats)
    subtitle = (
        f"<br><sup>{open_total} open/unterminated touch(es) excluded from all "
        "durations, means and rates</sup>"
        if open_total
        else ""
    )
    fig.update_layout(
        title=f"Touch Analysis Data (Length of video: {length_text}){subtitle}",
        title_x=0.5,
        template=PLOTLY_TEMPLATE,
        margin=dict(l=10, r=10, t=70, b=10),
        height=SUMMARY_TABLE_HEIGHT_PX,
        font=dict(size=13),
    )
    return _write_figure(
        fig, output_folder, TABLE_FILE,
        title="Summary table",
        group=GROUP_SUMMARY,
        config=_STATIC_FIGURE_CONFIG,
    )


# --- histograms -------------------------------------------------------------

def _stacked_bar_figure(distributions, limbs, hover_unit):
    all_keys = sorted({key for d in distributions for key in d})
    fig = go.Figure()
    for limb, d in zip(limbs, distributions):
        fig.add_trace(
            go.Bar(
                x=list(all_keys),
                y=[d.get(key, 0) for key in all_keys],
                name=limb,
                marker=dict(color=LIMB_COLORS.get(limb),
                            line=dict(width=1, color="#ffffff")),
                hovertext=[
                    f"{limb}<br>Length: {key}{hover_unit}<br>Number of touches: {d.get(key, 0)}"
                    for key in all_keys
                ],
                hoverinfo="text",
            )
        )
    fig.update_layout(template=PLOTLY_TEMPLATE)
    return fig


def write_onset_histogram(stats: Sequence[LimbStats], output_folder: str) -> ReportFigure:
    """Distribution of onsets-per-touch, closed episodes only."""
    fig = _stacked_bar_figure(
        [s.onset_count_distribution for s in stats], [s.limb for s in stats], ""
    )
    fig.update_layout(
        barmode="stack",
        title="Touch length distribution",
        xaxis_title="Length of touch [number of onsets]",
        yaxis_title="Number of touches",
    )
    return _write_figure(
        fig, output_folder, ONSET_HISTOGRAM_FILE,
        title="Touch length distribution",
        group=GROUP_DISTRIBUTIONS,
        half_width=True,
    )


def write_duration_histogram(stats: Sequence[LimbStats], fps, output_folder: str) -> ReportFigure:
    """Distribution of touch durations, closed episodes only.

    Buckets are whole seconds (rounded up) when the frame rate is usable; with
    an unusable frame rate the axis switches to FRAMES and says so, instead of
    dividing by zero (which is exactly where the old code crashed).
    """
    usable = fps_is_usable(fps)
    distributions = [duration_histogram_buckets(s, fps) for s in stats]
    unit = " sec" if usable else " frames"
    fig = _stacked_bar_figure(distributions, [s.limb for s in stats], unit)
    if usable:
        title = "Touch Duration Distribution"
        xaxis_title = "Touch Duration [second]"
    else:
        title = (
            "Touch Duration Distribution<br>"
            f"<sup>frame rate unavailable ({fps!r}) — durations shown in frames</sup>"
        )
        xaxis_title = "Touch Duration [frames]"
        logger.warning("duration histogram fell back to frame buckets (unusable frame rate)")
    fig.update_layout(
        barmode="stack",
        title=title,
        xaxis_title=xaxis_title,
        yaxis_title="Number of Touches",
        xaxis=dict(type="category"),
    )
    return _write_figure(
        fig, output_folder, DURATION_HISTOGRAM_FILE,
        title="Touch duration distribution",
        group=GROUP_DISTRIBUTIONS,
        half_width=True,
    )
