"""Contract tests for the pure analysis domain (`domain.touch_stats`).

Covers episode reconstruction edge cases, the half-open `[ON, OFF)` duration
convention, the summarize math (including the "unusable frame rate" path) and
the pairwise multi-zone transition rule. Everything here is pure: no files, no
plotly, no config.
"""

import json
import math

import pandas as pd
import pytest

from domain.model import LIMBS
from domain.touch_stats import (
    NO_ZONE,
    Episode,
    ExportSchemaError,
    duration_histogram_buckets,
    flatten_zones,
    fps_is_usable,
    normalize_onset,
    parse_export,
    parse_xy_list,
    summarize,
    transition_matrix,
    transition_zone_axis,
    transitions,
    zone_sort_key,
)


# --- helpers ---------------------------------------------------------------

def make_df(rows, limb="RH"):
    """Build a schema-complete export DataFrame for one limb.

    `rows` items are dicts: {"frame": int, "onset": str, "zones": list-of-buckets,
    "x": [...], "y": [...]}. Every other limb gets empty cells.
    """
    columns = ["Frame", "Time_ms"] + [
        f"{other}_{suffix}" for other in LIMBS for suffix in ("X", "Y", "Onset", "Zones")
    ]
    records = []
    for row in rows:
        rec = {"Frame": row["frame"], "Time_ms": float(row["frame"])}
        for other in LIMBS:
            rec[f"{other}_X"] = ""
            rec[f"{other}_Y"] = ""
            rec[f"{other}_Onset"] = ""
            rec[f"{other}_Zones"] = "[]"
        rec[f"{limb}_Onset"] = row.get("onset", "")
        rec[f"{limb}_Zones"] = json.dumps(row.get("zones", []))
        rec[f"{limb}_X"] = ",".join(str(v) for v in row.get("x", []))
        rec[f"{limb}_Y"] = ",".join(str(v) for v in row.get("y", []))
        records.append(rec)
    # A header-only export (no annotated frames) must keep its columns.
    return pd.DataFrame(records, columns=columns)


def episodes_of(rows, limb="RH"):
    return parse_export(make_df(rows, limb)).episodes[limb]


def on(frame, zones=None, x=None, y=None):
    return {"frame": frame, "onset": "ON", "zones": zones or [], "x": x or [], "y": y or []}


def off(frame, zones=None, x=None, y=None):
    return {"frame": frame, "onset": "OFF", "zones": zones or [], "x": x or [], "y": y or []}


def blank(frame, zones=None, x=None, y=None):
    return {"frame": frame, "onset": "", "zones": zones or [], "x": x or [], "y": y or []}


# --- schema ----------------------------------------------------------------

def test_parse_export_requires_frame_time_and_per_limb_columns():
    with pytest.raises(ExportSchemaError) as caught:
        parse_export(pd.DataFrame({"Frame": [0, 1]}))

    message = str(caught.value)
    assert "Time_ms" in message
    for limb in LIMBS:
        assert f"{limb}_Onset" in message
        assert f"{limb}_Zones" in message


def test_parse_export_empty_table_yields_no_episodes():
    df = make_df([])
    data = parse_export(df)

    assert data.total_frames == 0
    assert data.row_count == 0
    assert data.last_frame is None
    assert all(data.episodes[limb] == [] for limb in LIMBS)
    # summarize on nothing must not divide by anything
    stats = summarize([], fps=25.0, total_frames=0, limb="RH")
    assert stats.total_touches == 0
    assert stats.percentage_touching == 0.0
    assert stats.touch_rate_per_100_frames == 0.0
    assert stats.mean_duration_frames is None


def test_parse_export_tolerates_missing_xy_columns_and_reports_them():
    df = make_df([on(1, [["A"]]), off(3, [["B"]])]).drop(columns=["RH_X", "RH_Y"])
    data = parse_export(df)

    assert "RH_X" in data.missing_optional_columns
    episodes = data.episodes["RH"]
    # Stats still correct; only the plottable points are gone.
    assert len(episodes) == 1 and episodes[0].duration_frames == 2
    assert episodes[0].points == ()


def test_parse_export_sorts_rows_by_frame_and_skips_unusable_frame(capsys):
    df = make_df([off(9, [["B"]]), on(4, [["A"]])])
    data = parse_export(df)
    episodes = data.episodes["RH"]

    assert len(episodes) == 1
    assert (episodes[0].start_frame, episodes[0].end_frame) == (4, 9)

    bad = make_df([on(1, [["A"]])])
    bad["Frame"] = bad["Frame"].astype(object)
    bad.loc[0, "Frame"] = "not-a-frame"
    assert parse_export(bad).row_count == 0
    assert "WARN" in capsys.readouterr().out


# --- episode reconstruction ------------------------------------------------

def test_half_open_duration_convention():
    """Duration is `OFF - ON`: active ON the onset frame, offset EXCLUDED."""
    episodes = episodes_of([on(10, [["A"]]), off(20, [["A"]])])

    assert episodes[0].duration_frames == 10
    assert episodes[0].duration_seconds(10.0) == 1.0


def test_single_frame_episode_is_one_frame_not_zero():
    """The shortest closed touch (ON at f, OFF at f+1) is 1 frame."""
    episodes = episodes_of([on(7, [["A"]]), off(8, [["A"]])])

    assert episodes[0].duration_frames == 1
    stats = summarize(episodes, fps=25.0, total_frames=100)
    assert stats.durations_frames == (1,)
    assert stats.total_duration_frames == 1


def test_consecutive_ons_extend_one_episode_and_count_onsets():
    """A second ON while a touch is open does NOT start a new touch."""
    episodes = episodes_of(
        [on(2, [["A"]]), on(4, [["B"]]), on(6, [["C"]]), off(9, [["D"]])]
    )

    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.n_onsets == 3
    assert episode.start_frame == 2 and episode.end_frame == 9
    assert episode.duration_frames == 7
    # start/end zones come from the first ON and the OFF; the ONs in between
    # are "mid" zones (they count toward zone_touch_count, not transitions).
    assert episode.zones_start == ("A",)
    assert episode.zones_mid == ("B", "C")
    assert episode.zones_end == ("D",)
    assert transitions(episodes) == {"A": {"D": 1}}
    assert summarize(episodes, total_frames=10).zone_touch_count == {
        "A": 1, "B": 1, "C": 1, "D": 1
    }


def test_off_without_on_is_ignored_entirely():
    """A stray offset creates no episode and contributes no plottable point."""
    episodes = episodes_of([off(3, [["A"]], x=[1], y=[2]), off(5, [["B"]], x=[3], y=[4])])

    assert episodes == []
    stats = summarize(episodes, fps=25.0, total_frames=10, limb="RH")
    assert stats.total_touches == 0 and stats.open_touches == 0


def test_open_at_end_is_censored():
    episodes = episodes_of([on(3, [["A"]]), blank(4, [["B"]]), blank(20)])

    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.closed is False
    assert episode.end_frame is None
    assert episode.duration_frames is None
    assert episode.duration_seconds(25.0) is None
    assert episode.last_seen_frame == 20
    assert episode.zones_mid == ("B",)


def test_closed_then_open_keeps_the_closed_one_intact():
    """A censored trailing touch must not corrupt the completed ones."""
    episodes = episodes_of(
        [on(1, [["A"]]), off(4, [["B"]]), on(30, [["C"]]), blank(50)]
    )
    stats = summarize(episodes, fps=25.0, total_frames=51, limb="RH")

    assert [e.closed for e in episodes] == [True, False]
    assert stats.total_touches == 1
    assert stats.open_touches == 1
    assert stats.durations_frames == (3,)
    assert stats.zone_touch_count == {"A": 1, "B": 1}  # no "C" from the open one
    assert transitions(episodes) == {"A": {"B": 1}}


def test_mid_touch_clicks_are_both_counted_and_plottable():
    """The same click must reach the stats AND the trajectory (or neither).

    A zone-only row inside a touch used to be counted by the metrics but dropped
    from the plotted path, which is how hover labels drifted off their points.
    """
    episodes = episodes_of(
        [
            on(1, [["A"]], x=[10], y=[11]),
            blank(2, [["B"]], x=[20], y=[21]),
            off(3, [["C"]], x=[30], y=[31]),
        ]
    )
    episode = episodes[0]

    assert [p.role for p in episode.points] == ["start", "mid", "end"]
    assert [p.frame for p in episode.points] == [1, 2, 3]
    assert [p.zones for p in episode.points] == [("A",), ("B",), ("C",)]
    assert summarize(episodes, total_frames=4).zone_touch_count == {"A": 1, "B": 1, "C": 1}


def test_clicks_outside_any_episode_are_not_plottable():
    """Counterpart of the previous test: neither counted nor drawn."""
    episodes = episodes_of([blank(1, [["A"]], x=[10], y=[11]), on(2, [["B"]], x=[20], y=[21]),
                            off(3, [["C"]], x=[30], y=[31])])

    assert [p.frame for p in episodes[0].points] == [2, 3]
    assert summarize(episodes, total_frames=4).zone_touch_count == {"B": 1, "C": 1}


def test_multi_click_frame_keeps_every_click_with_its_own_zone_bucket():
    episodes = episodes_of(
        [on(1, [["A"]], x=[1], y=[2]), off(3, [["L"], ["I"]], x=[5, 6], y=[7, 8])]
    )
    episode = episodes[0]

    assert episode.zones_end == ("L", "I")
    end_points = [p for p in episode.points if p.role == "end"]
    assert [(p.click_index, p.clicks_in_frame, p.zones) for p in end_points] == [
        (1, 2, ("L",)),
        (2, 2, ("I",)),
    ]
    assert [p.is_last_in_frame for p in end_points] == [False, True]


def test_multi_bucket_zones_in_one_click_are_all_recorded():
    episodes = episodes_of([on(1, [["A", "B"]], x=[1], y=[2]), off(2, [["C", "D"]], x=[3], y=[4])])
    episode = episodes[0]

    assert episode.zones_start == ("A", "B")
    assert episode.zones_end == ("C", "D")
    assert episode.points[0].zones == ("A", "B")


def test_more_clicks_than_zone_buckets_degrades_gracefully():
    episodes = episodes_of([on(1, [["A"]], x=[1, 2, 3], y=[4, 5, 6]), off(2)])
    start_points = [p for p in episodes[0].points if p.role == "start"]

    assert [p.zones for p in start_points] == [("A",), (), ()]


def test_zoneless_episode_uses_nn_for_transitions_only():
    episodes = episodes_of([on(1), off(4)])

    assert episodes[0].zones_start == ()
    assert transitions(episodes) == {NO_ZONE: {NO_ZONE: 1}}
    # ...but the sentinel is NOT invented as an observed zone
    assert summarize(episodes, total_frames=5).zone_touch_count == {}


def test_episodes_are_isolated_per_limb():
    rows = [
        {"frame": 1, "onset": "", "zones": []},
        {"frame": 2, "onset": "", "zones": []},
    ]
    df = make_df(rows)
    df.loc[0, "LH_Onset"] = "ON"
    df.loc[0, "LH_Zones"] = json.dumps([["A"]])
    df.loc[1, "LH_Onset"] = "OFF"
    df.loc[1, "LH_Zones"] = json.dumps([["B"]])
    data = parse_export(df)

    assert len(data.episodes["LH"]) == 1
    assert data.episodes["RH"] == []
    assert data.episodes["LL"] == [] and data.episodes["RL"] == []


# --- summarize math --------------------------------------------------------

def test_summarize_math_matches_hand_computation():
    """Two closed touches of 9 and 2 frames in a 324-frame, 25 fps video."""
    episodes = episodes_of([on(156, [["Z"]]), off(165, [["K"]]),
                            on(261, [["L"]]), off(263, [["L"], ["I"]])]
                           + [blank(323)])
    stats = summarize(episodes, fps=25.0, total_frames=324, limb="RH")

    assert stats.total_touches == 2
    assert stats.durations_frames == (9, 2)
    assert stats.total_duration_frames == 11
    assert stats.percentage_touching == pytest.approx(3.3951, abs=1e-4)
    assert stats.mean_duration_frames == pytest.approx(5.5)
    assert stats.mean_duration_seconds == pytest.approx(0.22)
    assert stats.stdev_duration_frames == pytest.approx(4.9497, abs=1e-4)
    assert stats.touch_rate_per_minute == pytest.approx(9.2593, abs=1e-4)
    assert stats.touch_rate_per_100_frames == pytest.approx(2 / 324 * 100)
    assert stats.video_seconds == pytest.approx(12.96)
    assert stats.durations_seconds == pytest.approx((0.36, 0.08))
    assert stats.total_duration_seconds == pytest.approx(0.44)


def test_summarize_stdev_needs_two_episodes_and_is_none_not_nan():
    one = summarize(episodes_of([on(1), off(3)]), fps=25.0, total_frames=100, limb="RH")
    two = summarize(
        episodes_of([on(1), off(3), on(10), off(20)]), fps=25.0, total_frames=100, limb="RH"
    )

    assert one.stdev_duration_frames is None
    assert one.stdev_duration_seconds is None
    assert two.stdev_duration_frames is not None
    # None, never a NaN that would render as "nan" in the HTML table.
    for value in (one.stdev_duration_frames, one.stdev_duration_seconds):
        assert not (isinstance(value, float) and math.isnan(value))


def test_summarize_mean_is_none_without_any_closed_episode():
    stats = summarize(episodes_of([on(5), blank(9)]), fps=25.0, total_frames=10, limb="RH")

    assert stats.mean_duration_frames is None
    assert stats.mean_duration_seconds is None
    assert stats.open_touches == 1


def test_summarize_onset_histogram_counts_closed_episodes_only():
    episodes = episodes_of(
        [on(1), on(2), off(3),          # 2 onsets
         on(5), off(6),                 # 1 onset
         on(8), on(9), off(10),         # 2 onsets
         on(20), blank(30)]             # open -> excluded
    )
    stats = summarize(episodes, fps=25.0, total_frames=31, limb="RH")

    assert stats.onset_count_distribution == {1: 1, 2: 2}
    assert stats.open_touches == 1


@pytest.mark.parametrize("bad_fps", [None, 0, 0.0, -1.0, float("nan"), "abc"])
def test_unusable_frame_rate_yields_none_seconds_and_never_raises(bad_fps):
    episodes = episodes_of([on(1), off(4), on(10), off(20)])
    stats = summarize(episodes, fps=bad_fps, total_frames=100, limb="RH")

    assert fps_is_usable(bad_fps) is False
    # frame-based results survive intact
    assert stats.total_touches == 2
    assert stats.durations_frames == (3, 10)
    assert stats.total_duration_frames == 13
    assert stats.percentage_touching == pytest.approx(13.0)
    assert stats.mean_duration_frames == pytest.approx(6.5)
    # seconds-based results are explicitly absent, not zero
    assert stats.durations_seconds is None
    assert stats.total_duration_seconds is None
    assert stats.mean_duration_seconds is None
    assert stats.stdev_duration_seconds is None
    assert stats.touch_rate_per_minute is None
    assert stats.video_seconds is None


def test_fps_is_usable_accepts_positive_values():
    assert fps_is_usable(25) and fps_is_usable(29.97) and fps_is_usable("30")


# --- transitions -----------------------------------------------------------

def test_transitions_are_pairwise_cartesian():
    episodes = episodes_of([on(1, [["A"], ["B"]], x=[1, 2], y=[1, 2]),
                            off(5, [["C"], ["D"]], x=[3, 4], y=[3, 4])])

    counts = transitions(episodes)

    assert counts == {"A": {"C": 1, "D": 1}, "B": {"C": 1, "D": 1}}
    # 4 heatmap counts for ONE touch: totals != touch count, by design.
    assert sum(sum(ends.values()) for ends in counts.values()) == 4
    assert summarize(episodes, total_frames=6).total_touches == 1


def test_transitions_accumulate_across_episodes():
    episodes = episodes_of([on(1, [["A"]]), off(2, [["B"]]),
                            on(3, [["A"]]), off(4, [["B"]]),
                            on(5, [["A"]]), off(6, [["C"]])])

    assert transitions(episodes) == {"A": {"B": 2, "C": 1}}


def test_transition_axis_and_matrix():
    episodes = episodes_of([on(1, [["A"]]), off(2, [["ZZZZ"]])])
    counts = transitions(episodes)
    stats = summarize(episodes, total_frames=3)

    zones = transition_zone_axis(counts, stats.zone_touch_count, ["A", "B", NO_ZONE])
    # Sorted: real zones by (length, name), catch-alls (incl. NN) last.
    assert zones == ["A", "B", "ZZZZ", NO_ZONE]

    matrix = transition_matrix(counts, zones)
    assert matrix.at["A", "ZZZZ"] == 1
    assert matrix.values.sum() == 1
    assert list(matrix.index) == zones and list(matrix.columns) == zones


def test_transition_matrix_drops_and_warns_for_zone_off_axis(capsys):
    matrix = transition_matrix({"A": {"B": 3}}, ["A"])

    assert matrix.values.sum() == 0
    assert "WARN" in capsys.readouterr().out


def test_zone_sort_key_puts_catch_alls_last():
    zones = sorted(["NN", "BOX1", "A", "OUTSIDE", "LINE", "ZB", "B"], key=zone_sort_key)

    assert zones[:3] == ["A", "B", "ZB"]
    assert set(zones[3:]) == {"BOX1", "LINE", "NN", "OUTSIDE"}


# --- histogram buckets -----------------------------------------------------

def test_duration_histogram_buckets_ceil_to_whole_seconds():
    episodes = episodes_of([on(0), off(26), on(100), off(101)])
    stats = summarize(episodes, fps=25.0, total_frames=200, limb="RH")

    # 26 frames @25fps = 1.04s -> ceil 2 ; 1 frame = 0.04s -> ceil 1
    assert duration_histogram_buckets(stats, 25.0) == {1: 1, 2: 1}


def test_duration_histogram_buckets_fall_back_to_frames_without_fps():
    episodes = episodes_of([on(0), off(26), on(100), off(101)])
    stats = summarize(episodes, fps=None, total_frames=200, limb="RH")

    assert duration_histogram_buckets(stats, None) == {1: 1, 26: 1}


# --- tolerant value parsing ------------------------------------------------

def test_value_parsers_are_tolerant():
    assert normalize_onset(None) == "" and normalize_onset(" on ") == "ON"
    assert normalize_onset(float("nan")) == ""
    assert parse_xy_list("1, 2.5, ,bad") == [1.0, 2.5]
    assert parse_xy_list(None) == [] and parse_xy_list(float("nan")) == []
    assert flatten_zones([["A", None], "B", None]) == ["A", "B"]


# --- layering guard --------------------------------------------------------

FORBIDDEN_IN_DOMAIN = (
    "plotly",
    "webbrowser",
    "resource_utils",
    "resource_path",
    "asset_path",
    "adapters",
    "service_layer",
    "tkinter",
    "cv2",
)


def test_domain_layer_imports_nothing_from_the_outer_layers():
    """`src/domain/` must stay pure: no plotting, no browser, no asset paths,
    no config reads, no adapters/services, no Tk, no cv2. Pinned mechanically
    because a stray convenience import is the usual way a layer rots.
    """
    import ast
    import pathlib

    domain_dir = pathlib.Path(__file__).resolve().parents[2] / "src" / "domain"
    offenders = []
    for source_file in sorted(domain_dir.glob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
                names.extend(alias.name for alias in node.names)
        for name in names:
            for banned in FORBIDDEN_IN_DOMAIN:
                if banned in name:
                    offenders.append(f"{source_file.name}: {name}")

    assert offenders == []


def test_episode_is_hashable_and_frozen():
    episode = Episode("RH", 1, 2, True, ("A",), (), ("B",), 1)

    assert episode.duration_frames == 1
    assert isinstance(hash(episode), int)
    with pytest.raises(Exception):
        episode.start_frame = 5
