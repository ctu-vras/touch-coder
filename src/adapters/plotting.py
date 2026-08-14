"""
adapters/plotting.py
Every plotly figure, CSV table and HTML page the Analysis button produces.

This is a pure OUTPUT adapter: it receives already-computed domain objects
(`domain.touch_stats.Episode` / `LimbStats` / transition matrices) and turns
them into files under `output_folder`. It never reads an export CSV, never
computes a statistic and never opens a browser — the numbers must be
reproducible without plotly, and the browser is the GUI's business.

Written artifacts (names are a contract: `write_master_html` links them and
users bookmark them):

    heatmap_<LIMB>.html       one transition heatmap per limb
    touch_trajectory.html     4-panel click trajectory over the limb diagrams
    analysis_table_frames.csv frame-based per-limb table
    analysis_table_seconds.csv seconds-based per-limb table
    table.html                rendered summary table
    histogram.html            touch length distribution (in onsets)
    histogram_2.html          touch duration distribution
    master_<name>.html        index page embedding all of the above

UNUSABLE FRAME RATE: when `fps` is None/0/negative every seconds cell is
written as an empty value and the duration histogram falls back to FRAME
buckets with a relabelled axis. Nothing raises and nothing is skipped, so the
master page never links a file that was not written.
"""

import base64
import html
import math
import os
from typing import Dict, Optional, Sequence

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from PIL import Image
from plotly.subplots import make_subplots

from domain.model import LIMBS
from domain.touch_stats import (
    Episode,
    LimbStats,
    duration_histogram_buckets,
    fps_is_usable,
)
from gui.resource_utils import asset_path

# Panel titles, in `domain.model.LIMBS` order.
LIMB_TITLES = {"LH": "Left Hand", "RH": "Right Hand", "LL": "Left Leg", "RL": "Right Leg"}

TRAJECTORY_FILE = "touch_trajectory.html"
TABLE_FILE = "table.html"
ONSET_HISTOGRAM_FILE = "histogram.html"
DURATION_HISTOGRAM_FILE = "histogram_2.html"
FRAMES_TABLE_CSV = "analysis_table_frames.csv"
SECONDS_TABLE_CSV = "analysis_table_seconds.csv"

# Marker styling by EpisodePoint role (see domain.touch_stats.EpisodePoint).
_START_COLOR, _MID_COLOR, _END_COLOR, _OPEN_COLOR = "green", "black", "red", "orange"
_EDGE_SIZE, _MID_SIZE = 15, 8

_MISSING = "n/a"


def heatmap_file(limb: str) -> str:
    return f"heatmap_{limb}.html"


def master_file(name: str) -> str:
    return f"master_{name}.html"


def limb_image_paths(new_template: bool = False) -> list:
    """The four limb diagrams the trajectory panels are drawn on, in LIMBS order."""
    suffix = "_new_template" if new_template else ""
    return [asset_path(f"icons/{limb}{suffix}.png") for limb in LIMBS]


# --- heatmaps ---------------------------------------------------------------

def write_transition_heatmap(transition_df: pd.DataFrame,
                             zones: Sequence[str],
                             limb: str,
                             output_folder: str) -> str:
    """One start-zone x end-zone heatmap. The subtitle states the pairwise rule
    because the totals legitimately exceed the touch count (see
    `domain.touch_stats.transitions`)."""
    zones = list(zones)
    fig = px.imshow(
        transition_df,
        labels=dict(x="End Zone", y="Start Zone", color="Number of Touches"),
        x=zones,
        y=zones,
        color_continuous_scale="Blues",
        aspect="auto",
    )
    fig.update_traces(
        hovertemplate="Start Zone: %{y}<br>End Zone: %{x}<br>Number of Touches: %{z}<extra></extra>"
    )
    fig.update_layout(
        title=(
            f"Touch Transition Heatmap {limb}<br>"
            "<sup>Multi-zone start/end clicks contribute one count per zone pair.</sup>"
        ),
        xaxis_title="End Zone",
        yaxis_title="Start Zone",
        coloraxis_colorbar=dict(title="Number of Touches"),
        height=1000,
        margin=dict(l=50, r=50, t=50, b=150),
    )
    fig.update_yaxes(
        tickmode="array", tickvals=list(range(len(zones))), ticktext=zones, automargin=True
    )
    path = os.path.join(output_folder, heatmap_file(limb))
    fig.write_html(path)
    print(f"INFO: wrote {path}")
    return path


# --- trajectory -------------------------------------------------------------

def _point_style(point, is_open_tail: bool):
    """(color, size) for one EpisodePoint.

    Preserves the historical scheme: the FIRST click of the onset row is the
    green start marker, the LAST click of the offset row the red end marker,
    everything else a small black waypoint. The final click of an unterminated
    touch is orange so a censored episode is visible instead of silently
    vanishing from the plot.
    """
    if is_open_tail:
        return _OPEN_COLOR, _EDGE_SIZE
    if point.role == "start" and point.click_index == 1:
        return _START_COLOR, _EDGE_SIZE
    if point.role == "end" and point.is_last_in_frame:
        return _END_COLOR, _EDGE_SIZE
    return _MID_COLOR, _MID_SIZE


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
    xs, ys, colors, sizes, texts = [], [], [], [], []
    last_index = len(episode.points) - 1
    for idx, point in enumerate(episode.points):
        is_open_tail = (not episode.closed) and idx == last_index
        color, size = _point_style(point, is_open_tail)
        xs.append(point.x)
        ys.append(point.y)
        colors.append(color)
        sizes.append(size)
        texts.append(_hover_text(point, episode))
    return go.Scatter(
        x=xs,
        y=ys,
        mode="markers+lines",
        marker=dict(color=colors, size=sizes),
        line=dict(color="black", width=2, dash="dot"),
        name=name,
        text=texts,
        hovertemplate="%{text}<extra></extra>",
    )


def write_trajectory_plot(episodes_by_limb: Dict[str, Sequence[Episode]],
                          image_paths: Sequence[str],
                          output_folder: str) -> str:
    """4-panel trajectory plot, one trace per episode, drawn over the limb
    diagrams. Open episodes get an `(open)` trace name and an orange tail
    marker; every click that counts toward the statistics appears here."""
    fig = make_subplots(
        rows=1,
        cols=len(LIMBS),
        subplot_titles=tuple(LIMB_TITLES[limb] for limb in LIMBS),
        horizontal_spacing=0.02,
    )

    with Image.open(image_paths[0]) as img:
        img_width, img_height = img.size

    for i, limb in enumerate(LIMBS):
        with open(image_paths[i], "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode()

        for n, episode in enumerate(episodes_by_limb.get(limb, ()), start=1):
            suffix = "" if episode.closed else " (open)"
            trace = _episode_trace(episode, f"{limb} touch {n}{suffix}")
            if trace is not None:
                fig.add_trace(trace, row=1, col=i + 1)

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
            row=1,
            col=i + 1,
        )
        fig.update_xaxes(
            visible=False, range=[0, img_width], autorange=False,
            fixedrange=False, row=1, col=i + 1,
        )
        fig.update_yaxes(
            visible=False, range=[0, img_height], autorange="reversed",
            fixedrange=False, scaleanchor="x", scaleratio=1, row=1, col=i + 1,
        )

    fig.update_layout(
        autosize=False,
        height=img_height + 200,
        width=img_width * len(LIMBS),
        showlegend=False,
        margin=dict(l=0, r=0, t=50, b=50),
        dragmode="pan",
    )
    path = os.path.join(output_folder, TRAJECTORY_FILE)
    fig.write_html(path, config={"scrollZoom": True})
    print(f"INFO: wrote {path}")
    return path


# --- tables -----------------------------------------------------------------

def write_analysis_tables(stats: Sequence[LimbStats],
                          total_frames: int,
                          fps,
                          output_folder: str) -> pd.DataFrame:
    """Write both per-limb CSV tables and return the seconds DataFrame.

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
    print(f"INFO: wrote {frames_path}")

    if not fps_is_usable(fps):
        print(
            "WARN: frame rate unusable "
            f"({fps!r}) — seconds-based table columns written as empty values"
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
    print(f"INFO: wrote {seconds_path}")
    return df_seconds


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
                        output_folder: str) -> str:
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
                header=dict(values=list(columns.keys()), fill_color="paleturquoise", align="left"),
                cells=dict(values=list(columns.values()), fill_color="lavender", align="left"),
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
        margin=dict(l=10, r=10, t=70, b=10),
        width=1400,
        height=700,
        font=dict(size=14),
    )
    path = os.path.join(output_folder, TABLE_FILE)
    fig.write_html(path)
    print(f"INFO: wrote {path}")
    return path


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
                hovertext=[
                    f"{limb}<br>Length: {key}{hover_unit}<br>Number of touches: {d.get(key, 0)}"
                    for key in all_keys
                ],
                hoverinfo="text",
            )
        )
    return fig


def write_onset_histogram(stats: Sequence[LimbStats], output_folder: str) -> str:
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
    path = os.path.join(output_folder, ONSET_HISTOGRAM_FILE)
    fig.write_html(path)
    print(f"INFO: wrote {path}")
    return path


def write_duration_histogram(stats: Sequence[LimbStats], fps, output_folder: str) -> str:
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
        print("WARN: duration histogram fell back to frame buckets (unusable frame rate)")
    fig.update_layout(
        barmode="stack",
        title=title,
        xaxis_title=xaxis_title,
        yaxis_title="Number of Touches",
        xaxis=dict(type="category"),
    )
    path = os.path.join(output_folder, DURATION_HISTOGRAM_FILE)
    fig.write_html(path)
    print(f"INFO: wrote {path}")
    return path


# --- master page ------------------------------------------------------------

def master_graph_files(limbs: Sequence[str] = LIMBS) -> list:
    """The artifacts the master page embeds, in display order."""
    return [TRAJECTORY_FILE, TABLE_FILE, ONSET_HISTOGRAM_FILE, DURATION_HISTOGRAM_FILE] + [
        heatmap_file(limb) for limb in limbs
    ]


def write_master_html(name: str,
                      output_folder: str,
                      limbs: Sequence[str] = LIMBS,
                      notes: Optional[Sequence[str]] = None) -> str:
    """Write `master_<name>.html`, the index page linking every artifact.

    `notes` are rendered as a warning block — used for censored open touches and
    for an unusable frame rate, so the caveats travel with the dashboard instead
    of living only in the console log.
    """
    escaped_name = html.escape(str(name))
    note_block = ""
    if notes:
        items = "".join(f"<li>{html.escape(str(n))}</li>" for n in notes)
        note_block = f'<ul class="notes">{items}</ul>'

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{escaped_name}</title>
        <style>
            body {{
                text-align: center;
                font-family: Arial, sans-serif;
            }}
            iframe {{
                width: 90%;
                border: none;
                display: block;
                margin: 0 auto;
            }}
            h2 {{
                text-align: center;
            }}
            .notes {{
                display: inline-block;
                text-align: left;
                background: #fff8e1;
                border: 1px solid #ffe082;
                padding: 8px 24px;
                margin: 0 auto 16px auto;
                border-radius: 6px;
            }}
            .row {{
                display: flex;
                gap: 16px;
                justify-content: center;
                align-items: stretch;
                flex-wrap: wrap;
                margin: 0 auto;
                width: 95%;
            }}
            .half {{
                flex: 1 1 45%;
                min-width: 420px;
            }}
            .half iframe {{
                width: 100%;
            }}
        </style>
    </head>
    <body>
        <h1>{escaped_name}</h1>
        {note_block}
    """

    for graph in master_graph_files(limbs):
        if graph in (ONSET_HISTOGRAM_FILE, DURATION_HISTOGRAM_FILE):
            continue  # rendered side by side below
        height = "1200px" if graph == TRAJECTORY_FILE else "800px"
        html_content += f"""
        <h2>{graph}</h2>
        <iframe src="{graph}" style="height: {height};"></iframe>
        """

    html_content += f"""
        <h2>Histograms</h2>
        <div class="row">
            <div class="half">
                <iframe src="{ONSET_HISTOGRAM_FILE}" style="height: 700px;"></iframe>
            </div>
            <div class="half">
                <iframe src="{DURATION_HISTOGRAM_FILE}" style="height: 700px;"></iframe>
            </div>
        </div>
    """

    html_content += """
    </body>
    </html>
    """

    path = os.path.join(output_folder, master_file(name))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    print(f"INFO: wrote {path}")
    return path
