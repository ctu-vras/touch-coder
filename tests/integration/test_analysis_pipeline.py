"""End-to-end Analysis pipeline: export CSV -> service -> files on disk.

Everything is written under `tmp_path`; the real `data/` tree is never touched
and the browser-open is injected as a recorder, so this test is safe to run
anywhere (see tests/conftest.py).

GROUND TRUTH (the `cat3` regression anchor): an audit run over
`data/cat3/export/cat3_export.csv` — 324 frames @ 25 fps, RH the only limb with
data — produced exactly these numbers for the two CLOSED episodes
(ON@156 -> OFF@165 and ON@261 -> OFF@263, the latter carrying two offset clicks
in zones L and I):

    touches           2
    durations         [9, 2] frames
    total duration    11 frames
    percentage        3.3951 %
    mean              5.5 frames  (0.22 s)
    sample stdev      4.9497 frames
    touch rate        9.2593 / minute
    transitions       Z->K: 1, L->L: 1, L->I: 1   (pairwise over the 2 end zones)

`cat3_frames()` below reconstructs that annotation and the expectations are
pinned literally. If they ever change, the statistics changed — investigate,
do not edit the numbers.
"""

import json
import os
import re

import pandas as pd
import pytest

from adapters import plotting
from adapters.export_writer import export_from_unified, write_export_metadata
from domain.model import empty_bundle
from domain.project import ProjectPaths
from domain.touch_stats import summarize, transitions
from service_layer import analysis_service

CAT3_TOTAL_FRAMES = 324          # frames 0..323
CAT3_FPS = 25.0
EXPECTED_FILES = [
    "heatmap_LH.html", "heatmap_RH.html", "heatmap_LL.html", "heatmap_RL.html",
    "touch_trajectory.html",
    "analysis_table_frames.csv", "analysis_table_seconds.csv",
    "table.html", "histogram.html", "histogram_2.html",
]


# --- fixtures ---------------------------------------------------------------

def _rh(bundle, onset, xs, ys, zones):
    bundle["RH"] = {
        "X": list(xs), "Y": list(ys), "Onset": onset, "Bodypart": "RH",
        "Zones": zones, "Touch": None,
    }
    return bundle


def cat3_frames():
    """The cat3 annotation: two closed RH touches, the second ending on a
    two-click offset frame (zones L and I)."""
    frames = {}
    frames[156] = _rh(empty_bundle(), "ON", [120], [200], [["Z"]])
    frames[165] = _rh(empty_bundle(), "OFF", [130], [210], [["K"]])
    frames[261] = _rh(empty_bundle(), "ON", [140], [220], [["L"]])
    frames[263] = _rh(empty_bundle(), "OFF", [150, 160], [230, 240], [["L"], ["I"]])
    return frames


def write_project(tmp_path, frames, total_frames=CAT3_TOTAL_FRAMES - 1,
                  fps=CAT3_FPS, name="cat3", with_metadata=True):
    """Materialize a project tree under tmp_path using the REAL exporter."""
    paths = ProjectPaths(video_name=name, base_dir=str(tmp_path / "data"))
    os.makedirs(paths.export_dir, exist_ok=True)
    export_from_unified(
        frames, paths.export_csv,
        program_version=8.0, video_name=name, labeling_mode="Normal",
        frame_rate=fps, clothes_list=None, total_frames=total_frames,
    )
    if with_metadata:
        write_export_metadata(
            meta_path=paths.export_metadata,
            program_version=8.0, video_name=name, labeling_mode="Normal",
            frame_rate=fps, clothes_list=None,
        )
    return paths


class BrowserSpy:
    """Injected in place of `webbrowser.open` — the service must never open a
    real browser during tests, and the GUI is the only place that should."""

    def __init__(self):
        self.opened = []

    def __call__(self, path):
        self.opened.append(path)


# --- the cat3 regression anchor --------------------------------------------

def test_cat3_ground_truth_numbers(tmp_path):
    paths = write_project(tmp_path, cat3_frames())
    spy = BrowserSpy()

    result = analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(tmp_path / "plots"),
        open_browser=spy,
    )

    assert result.total_frames == CAT3_TOTAL_FRAMES
    assert result.frame_rate == CAT3_FPS
    assert spy.opened == [result.master_html]

    rh = next(s for s in result.stats if s.limb == "RH")
    assert rh.total_touches == 2
    assert rh.open_touches == 0
    assert list(rh.durations_frames) == [9, 2]
    assert rh.total_duration_frames == 11
    assert rh.percentage_touching == pytest.approx(3.3951, abs=1e-4)
    assert rh.mean_duration_frames == pytest.approx(5.5)
    assert rh.mean_duration_seconds == pytest.approx(0.22)
    assert rh.stdev_duration_frames == pytest.approx(4.9497, abs=1e-4)
    assert rh.touch_rate_per_minute == pytest.approx(9.2593, abs=1e-4)
    assert rh.video_seconds == pytest.approx(12.96)

    assert transitions(result.episodes["RH"]) == {"Z": {"K": 1}, "L": {"L": 1, "I": 1}}

    # The other three limbs are empty, and empty means empty (not "zeros with a
    # silent parse failure" — see test_analysis_validation.py).
    for limb in ("LH", "LL", "RL"):
        stats = next(s for s in result.stats if s.limb == limb)
        assert (stats.total_touches, stats.open_touches, stats.total_duration_frames) == (0, 0, 0)
        assert stats.mean_duration_frames is None


def test_cat3_all_expected_files_written(tmp_path):
    paths = write_project(tmp_path, cat3_frames())
    out = tmp_path / "plots"

    result = analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(out), open_browser=BrowserSpy()
    )

    for filename in EXPECTED_FILES:
        assert (out / filename).is_file(), f"missing {filename}"
    assert (out / "master_cat3.html").is_file()
    assert result.master_html == str(out / "master_cat3.html")
    for path in result.written_files:
        assert os.path.isfile(path)


def test_cat3_written_tables_carry_the_ground_truth(tmp_path):
    paths = write_project(tmp_path, cat3_frames())
    out = tmp_path / "plots"

    analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(out), open_browser=BrowserSpy()
    )

    frames_table = pd.read_csv(out / "analysis_table_frames.csv").set_index("Limb")
    seconds_table = pd.read_csv(out / "analysis_table_seconds.csv").set_index("Limb")

    assert frames_table.at["RH", "Total Touches"] == 2
    assert frames_table.at["RH", "Total Duration [Frames]"] == 11
    assert frames_table.at["RH", "Average Touch Duration [Frames]"] == pytest.approx(5.5)
    assert frames_table.at["RH", "Percentage Touching"] == pytest.approx(3.3951, abs=1e-4)
    assert frames_table.at["RH", "Standard Deviation [Frames]"] == pytest.approx(4.9497, abs=1e-4)
    assert frames_table.at["RH", "Open (Unterminated) Touches"] == 0

    assert seconds_table.at["RH", "Total Duration [Seconds]"] == pytest.approx(0.44)
    assert seconds_table.at["RH", "Average Touch Duration [Seconds]"] == pytest.approx(0.22)
    assert seconds_table.at["RH", "Touch Rate [Touches per Minute]"] == pytest.approx(
        9.2593, abs=1e-4
    )


def test_cat3_heatmap_reflects_pairwise_transitions(tmp_path):
    """3 heatmap counts for 2 touches — the documented cartesian rule."""
    paths = write_project(tmp_path, cat3_frames())
    out = tmp_path / "plots"

    analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(out), open_browser=BrowserSpy()
    )

    html = (out / "heatmap_RH.html").read_text(encoding="utf-8")
    assert "Touch Transition Heatmap RH" in html
    assert "one count per zone pair" in html


# --- report page layout ------------------------------------------------------
# The master page is ONE scrolling document: figure divs inlined (no iframes),
# plotly.js loaded once from a shared plots/plotly.min.js, wheel-zoom disabled
# so the mouse wheel scrolls the page instead of panning a figure.

def _run_cat3(tmp_path):
    paths = write_project(tmp_path, cat3_frames())
    out = tmp_path / "plots"
    analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(out), open_browser=BrowserSpy()
    )
    return out


def test_master_page_is_one_document(tmp_path):
    out = _run_cat3(tmp_path)
    page = (out / "master_cat3.html").read_text(encoding="utf-8")

    assert "<iframe" not in page.lower()
    assert page.count('src="plotly.min.js"') == 1
    assert (out / "plotly.min.js").is_file()
    for title in (
        "Touch trajectories over the limb diagrams",
        "Summary table",
        "Touch length distribution",
        "Touch duration distribution",
        "Transition heatmap — Right Hand (RH)",
    ):
        assert title in page, f"missing card title {title!r}"


def test_figures_share_one_plotly_bundle(tmp_path):
    out = _run_cat3(tmp_path)
    for filename in [f for f in EXPECTED_FILES if f.endswith(".html")]:
        html = (out / filename).read_text(encoding="utf-8")
        assert "plotly.min.js" in html, f"{filename} does not reference the shared bundle"
        size = (out / filename).stat().st_size
        assert size < 1_000_000, f"{filename} is {size} bytes — plotly.js embedded?"


def test_wheel_zoom_disabled_everywhere(tmp_path):
    out = _run_cat3(tmp_path)
    for filename in [f for f in EXPECTED_FILES if f.endswith(".html")]:
        html = (out / filename).read_text(encoding="utf-8")
        assert re.search(r'"scrollZoom"\s*:\s*true', html) is None, filename
        assert re.search(r'"scrollZoom"\s*:\s*false', html), filename


def test_limb_panels_are_mirrored_to_match_the_viewed_diagram(tmp_path):
    """The diagrams face the viewer, so the subject's right limbs render on the
    viewer's left: RH before LH, RL before LL — in the trajectory subplot
    titles and in the heatmap cards on the master page."""
    out = _run_cat3(tmp_path)

    trajectory = (out / "touch_trajectory.html").read_text(encoding="utf-8")
    assert trajectory.index("Right Hand") < trajectory.index("Left Hand")
    assert trajectory.index("Right Leg") < trajectory.index("Left Leg")

    page = (out / "master_cat3.html").read_text(encoding="utf-8")
    assert (page.index("Transition heatmap — Right Hand")
            < page.index("Transition heatmap — Left Hand")
            < page.index("Transition heatmap — Right Leg")
            < page.index("Transition heatmap — Left Leg"))


def test_trajectory_panels_are_pinned_to_the_diagram(tmp_path):
    """Panel ranges must never follow the data: any `autorange` recomputes the
    view from the point cloud, so each panel zoomed to its own points instead
    of showing the whole limb diagram (the y range is reversed EXPLICITLY)."""
    out = _run_cat3(tmp_path)
    traj = (out / "touch_trajectory.html").read_text(encoding="utf-8")
    assert re.search(r'"autorange"\s*:\s*"reversed"', traj) is None
    assert re.search(r'"autorange"\s*:\s*true', traj) is None
    assert '"scaleanchor"' in traj


def test_heatmap_height_follows_zone_count(tmp_path):
    out = _run_cat3(tmp_path)
    html = (out / "heatmap_RH.html").read_text(encoding="utf-8")
    height = re.search(r'"height"\s*:\s*(\d+)', html)
    ticks = re.search(r'"ticktext"\s*:\s*(\[[^\]]*\])', html)
    assert height and ticks, "heatmap layout carries no height/ticktext"
    zones = json.loads(ticks.group(1))
    assert int(height.group(1)) == plotting.heatmap_height(len(zones))


# --- bug 1: unusable frame rate --------------------------------------------

@pytest.mark.parametrize("bad_fps", [0.0, None, -1.0])
def test_zero_or_missing_fps_completes_without_crash(tmp_path, bad_fps):
    """Used to raise ZeroDivisionError AFTER writing several plots, leaving a
    half-populated plots/ dir. Now: full dashboard, frame-based numbers, empty
    seconds columns, WARN logged."""
    paths = write_project(tmp_path, cat3_frames(), fps=0.0, with_metadata=True)
    out = tmp_path / "plots"

    result = analysis_service.run_analysis(
        paths, frame_rate=bad_fps, output_folder=str(out), open_browser=BrowserSpy()
    )

    assert result.frame_rate is None
    assert result.frame_rate_source == "unavailable"
    for filename in EXPECTED_FILES + ["master_cat3.html"]:
        assert (out / filename).is_file(), f"missing {filename}"

    rh = next(s for s in result.stats if s.limb == "RH")
    assert list(rh.durations_frames) == [9, 2]           # frame numbers intact
    assert rh.total_duration_frames == 11
    assert rh.percentage_touching == pytest.approx(3.3951, abs=1e-4)
    assert rh.total_duration_seconds is None             # seconds omitted
    assert rh.touch_rate_per_minute is None

    seconds_table = pd.read_csv(out / "analysis_table_seconds.csv").set_index("Limb")
    assert pd.isna(seconds_table.at["RH", "Total Duration [Seconds]"])
    assert pd.isna(seconds_table.at["RH", "Touch Rate [Touches per Minute]"])

    assert any("Frame rate unavailable" in w for w in result.warnings)
    table_html = (out / "table.html").read_text(encoding="utf-8")
    assert "unknown" in table_html


def test_zero_fps_recovers_frame_rate_from_metadata_sidecar(tmp_path):
    """A 0.0 probe in the GUI is rescued by the sidecar written at save time."""
    paths = write_project(tmp_path, cat3_frames(), fps=CAT3_FPS)

    result = analysis_service.run_analysis(
        paths, frame_rate=0.0, output_folder=str(tmp_path / "plots"),
        open_browser=BrowserSpy(),
    )

    assert (result.frame_rate, result.frame_rate_source) == (CAT3_FPS, "metadata")
    rh = next(s for s in result.stats if s.limb == "RH")
    assert rh.touch_rate_per_minute == pytest.approx(9.2593, abs=1e-4)


# --- bug 4: open episodes are censored, not counted ------------------------

def test_open_touch_does_not_pollute_stats_but_is_reported(tmp_path):
    """The audit's example: an ON at frame 10 of 324 with no OFF used to add a
    ~12.5 s phantom touch that dominated every statistic while the labeler's
    timeline drew nothing for it."""
    frames = cat3_frames()
    # An ON with no following OFF. It must sit after the last offset, otherwise
    # the next OFF would legitimately close it into a (very long) real touch.
    frames[300] = _rh(empty_bundle(), "ON", [90], [90], [["A"]])

    paths = write_project(tmp_path, frames)
    out = tmp_path / "plots"

    result = analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(out), open_browser=BrowserSpy()
    )
    rh = next(s for s in result.stats if s.limb == "RH")

    # Identical to the clean cat3 run: the open episode changed nothing.
    assert rh.total_touches == 2
    assert list(rh.durations_frames) == [9, 2]
    assert rh.total_duration_frames == 11
    assert rh.percentage_touching == pytest.approx(3.3951, abs=1e-4)
    assert rh.mean_duration_frames == pytest.approx(5.5)
    assert "A" not in rh.zone_touch_count
    assert transitions(result.episodes["RH"]) == {"Z": {"K": 1}, "L": {"L": 1, "I": 1}}

    # ...but it is surfaced, not swallowed.
    assert rh.open_touches == 1
    assert rh.open_start_frames == (300,)
    assert any("open/unterminated" in w for w in result.warnings)
    frames_table = pd.read_csv(out / "analysis_table_frames.csv").set_index("Limb")
    assert frames_table.at["RH", "Open (Unterminated) Touches"] == 1
    # plotly serializes the title into JSON (which escapes "/"), so match on a
    # slash-free fragment; the master page is plain HTML we write ourselves.
    assert "unterminated" in (out / "table.html").read_text(encoding="utf-8").lower()
    assert "open/unterminated" in (out / "master_cat3.html").read_text(encoding="utf-8")


# --- bug 2: hover texts stay aligned with plotted points ------------------

def test_trajectory_hover_texts_align_with_points(tmp_path):
    """A stray OFF (no ongoing touch) and a mid-touch click used to desynchronize
    `text` from `x`/`y`, mislabelling every later hover. Traces are now built
    from episodes, so the arrays cannot diverge."""
    frames = {}
    # stray OFF first: belongs to no touch, must contribute nothing
    frames[5] = _rh(empty_bundle(), "OFF", [10], [10], [["A"]])
    frames[20] = _rh(empty_bundle(), "ON", [20], [20], [["B"]])
    frames[25] = _rh(empty_bundle(), "", [30], [30], [["C"]])       # mid-touch click
    frames[30] = _rh(empty_bundle(), "OFF", [40, 50], [40, 50], [["D"], ["E"]])

    paths = write_project(tmp_path, frames, total_frames=40)
    result = analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(tmp_path / "plots"),
        open_browser=BrowserSpy(),
    )

    episodes = result.episodes["RH"]
    assert len(episodes) == 1
    trace = plotting._episode_trace(episodes[0], "t")
    assert len(trace.x) == len(trace.y) == len(trace.text) == 4
    assert list(trace.x) == [20.0, 30.0, 40.0, 50.0]     # stray OFF absent
    assert "Frame: 20" in trace.text[0] and "Zone: B" in trace.text[0]
    assert "Frame: 25" in trace.text[1] and "Zone: C" in trace.text[1]
    assert "Frame: 30" in trace.text[2] and "Zone: D" in trace.text[2]
    assert "Frame: 30" in trace.text[3] and "Zone: E" in trace.text[3]
    # marker roles: start, mid, non-final offset click, end (colours are the
    # module's palette -- the ROLE assignment is what is pinned here)
    assert list(trace.marker.color) == [
        plotting._START_COLOR, plotting._MID_COLOR, plotting._MID_COLOR, plotting._END_COLOR
    ]

    # And the mid-touch click reached the statistics too (both or neither).
    rh = next(s for s in result.stats if s.limb == "RH")
    assert rh.zone_touch_count == {"B": 1, "C": 1, "D": 1, "E": 1}


# --- misc ------------------------------------------------------------------

def test_empty_export_produces_a_full_zero_dashboard(tmp_path):
    paths = write_project(tmp_path, {}, total_frames=10)
    out = tmp_path / "plots"

    result = analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(out), open_browser=BrowserSpy()
    )

    for filename in EXPECTED_FILES + ["master_cat3.html"]:
        assert (out / filename).is_file(), f"missing {filename}"
    assert all(s.total_touches == 0 and s.open_touches == 0 for s in result.stats)
    assert result.total_frames == 11


def test_output_defaults_to_project_plots_dir(tmp_path):
    paths = write_project(tmp_path, cat3_frames())

    result = analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, open_browser=BrowserSpy()
    )

    assert result.output_folder == paths.plots_dir
    assert os.path.isfile(os.path.join(paths.plots_dir, "master_cat3.html"))


def test_service_does_not_open_a_browser_unless_asked(tmp_path):
    paths = write_project(tmp_path, cat3_frames())

    result = analysis_service.run_analysis(
        paths, frame_rate=CAT3_FPS, output_folder=str(tmp_path / "plots")
    )

    assert os.path.isfile(result.master_html)
