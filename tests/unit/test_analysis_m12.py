"""M12 regression guards for analysis reads and transition metrics.

Re-pointed after `src/analysis.py` was split into
`domain.touch_stats` (pure stats) + `adapters.export_reader` (CSV reading) +
`adapters.plotting` + `service_layer.analysis_service`. The reader tests and the
pairwise multi-zone transition test are unchanged in substance — only the import
paths moved.

ONE expectation was deliberately rewritten:
`test_M12_open_touch_no_self_transition` used to assert that an `ON` with no
matching `OFF` yields `touch_durations == [3]` and `zone_touch_count["FACE"] == 1`
— i.e. it pinned the open touch as a COMPLETED touch of length
`last_row_frame - start_frame`. That was a bug, not a contract: the labeler draws
no timeline interval for an unterminated touch (intervals are only emitted on
OFF), so a video that ends mid-touch showed nothing in the app while a single
stray onset could dominate every statistic. The owner-approved policy is now to
report such episodes SEPARATELY as censored (`Episode.closed=False`), excluded
from durations/means/percentages/histograms but counted under `open_touches`.
See test_touch_stats.py for the full censoring contract.
"""

import pandas as pd
import pytest

from adapters.export_reader import ExportReadError, read_export_df
from adapters.export_writer import export_from_unified
from domain.touch_stats import parse_export, summarize, transitions


def _df(rows, limb="LH"):
    """Minimal but SCHEMA-COMPLETE export DataFrame (validation is strict now)."""
    records = []
    for frame, onset, zones in rows:
        rec = {"Frame": frame, "Time_ms": float(frame)}
        for other in ("LH", "RH", "LL", "RL"):
            rec[f"{other}_X"] = ""
            rec[f"{other}_Y"] = ""
            rec[f"{other}_Onset"] = ""
            rec[f"{other}_Zones"] = "[]"
        rec[f"{limb}_Onset"] = onset
        rec[f"{limb}_Zones"] = zones
        records.append(rec)
    return pd.DataFrame(records)


def _episodes(rows, limb="LH"):
    return parse_export(_df(rows, limb)).episodes[limb]


def test_M12_open_touch_is_censored_not_counted():
    """An unterminated ON is reported separately, never as a completed touch."""
    episodes = _episodes([(2, "ON", '[["FACE"]]'), (5, "", "[]")])

    assert len(episodes) == 1
    open_episode = episodes[0]
    assert open_episode.closed is False
    assert open_episode.end_frame is None
    assert open_episode.duration_frames is None
    assert open_episode.zones_start == ("FACE",)
    assert open_episode.last_seen_frame == 5

    stats = summarize(episodes, fps=30.0, total_frames=6)
    # No completed touch: nothing feeds durations, totals, means or histograms.
    assert stats.total_touches == 0
    assert stats.durations_frames == ()
    assert stats.total_duration_frames == 0
    assert stats.percentage_touching == 0.0
    assert stats.mean_duration_frames is None
    assert stats.stdev_duration_frames is None
    assert stats.onset_count_distribution == {}
    assert stats.zone_touch_count == {}
    # ...but it is NOT dropped silently.
    assert stats.open_touches == 1
    assert stats.open_start_frames == (2,)
    # And it still records no transition (there is no end zone to pair with).
    assert transitions(episodes) == {}


def test_M12_closed_touch_transition_counted():
    episodes = _episodes([(2, "ON", '[["FACE"]]'), (5, "OFF", '[["BELLY"]]')])

    assert transitions(episodes) == {"FACE": {"BELLY": 1}}


def test_M12_reader_missing_file_raises_oserror(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_export_df(str(tmp_path / "missing.csv"))


def test_M12_reader_parse_failure_logged_and_chained(tmp_path, caplog):
    path = tmp_path / "invalid.csv"
    path.write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(ExportReadError) as caught:
        read_export_df(str(path))

    assert caught.value.__cause__ is not None
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert warnings
    assert any(str(path) in record.getMessage() for record in warnings)


def test_M12_reader_accepts_real_and_legacy_export(tmp_path):
    current = tmp_path / "current.csv"
    legacy = tmp_path / "legacy.csv"
    export_from_unified(
        {},
        str(current),
        program_version=7.8,
        video_name="vid",
        labeling_mode="Normal",
        frame_rate=30.0,
        clothes_list=None,
        total_frames=1,
    )
    legacy.write_text(
        "\n".join(f"legacy header {i}" for i in range(6))
        + "\n"
        + current.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert "Frame" in read_export_df(str(current)).columns
    assert "Frame" in read_export_df(str(legacy)).columns


def test_M12_multizone_pairwise_transitions():
    """Multi-zone start/end clicks contribute one count per PAIR (cartesian).

    Kept exactly as strict as before: the heatmap total legitimately exceeds the
    touch count, and the heatmap subtitle says so.
    """
    multi = _episodes([(2, "ON", '[["BELLY", "HIP"]]'), (5, "OFF", '[["FACE"]]')])
    single = _episodes([(2, "ON", '[["BELLY"]]'), (5, "OFF", '[["FACE"]]')])

    assert transitions(multi) == {
        "BELLY": {"FACE": 1},
        "HIP": {"FACE": 1},
    }
    assert transitions(single) == {"BELLY": {"FACE": 1}}
