"""Export-schema validation and legacy-format tolerance for Analysis.

Before the split, `_load_limb_rows` used `rec.get(f"{limb}_Onset", "")`, so ANY
CSV with a `Frame` column "parsed" successfully and produced a complete
dashboard of zeros with no warning whatsoever — an easy way to publish numbers
for a file that never contained the limb data. Validation is now strict and up
front, and these tests pin both the strictness and the two legacy shapes that
must keep working (retired `Look` column, 6-line preamble).
"""

import json

import pandas as pd
import pytest

from adapters.export_reader import LEGACY_PREAMBLE_LINES, ExportReadError, read_export_df
from adapters.export_writer import export_from_unified
from domain.model import LIMBS, empty_bundle
from domain.touch_stats import (
    ExportSchemaError,
    optional_columns,
    parse_export,
    required_columns,
    validate_export_columns,
)
from domain.project import ProjectPaths
from service_layer import analysis_service


# --- helpers ---------------------------------------------------------------

def write_real_export(path, frames=None, total_frames=3, frame_rate=25.0):
    """Write an export CSV through the REAL exporter, so these tests break if
    the export schema itself ever drifts away from what analysis requires."""
    export_from_unified(
        frames if frames is not None else {},
        str(path),
        program_version=8.0,
        video_name="vid",
        labeling_mode="Normal",
        frame_rate=frame_rate,
        clothes_list=None,
        total_frames=total_frames,
    )
    return path


def touch_frames():
    """RH ON at frame 1 (zone A), OFF at frame 3 (zone B)."""
    frames = {}
    for frame, onset, zone in ((1, "ON", "A"), (3, "OFF", "B")):
        bundle = empty_bundle()
        bundle["RH"] = {
            "X": [10], "Y": [20], "Onset": onset, "Bodypart": "RH",
            "Zones": [[zone]], "Touch": None,
        }
        frames[frame] = bundle
    return frames


# --- the real exporter satisfies the validator -----------------------------

def test_real_export_passes_validation(tmp_path):
    path = write_real_export(tmp_path / "vid_export.csv", touch_frames())
    df = read_export_df(str(path))

    assert validate_export_columns(df) == []   # nothing optional missing either
    data = parse_export(df)
    assert data.total_frames == 4
    assert len(data.episodes["RH"]) == 1


def test_required_columns_are_exactly_frame_time_and_per_limb_onset_zones():
    expected = {"Frame", "Time_ms"}
    for limb in LIMBS:
        expected |= {f"{limb}_Onset", f"{limb}_Zones"}

    assert set(required_columns()) == expected
    # X/Y are optional: they feed only the trajectory plot.
    assert set(optional_columns()) == {f"{limb}_{ax}" for limb in LIMBS for ax in ("X", "Y")}


# --- missing columns are loud ---------------------------------------------

def test_frame_only_csv_is_rejected_naming_every_missing_column():
    df = pd.DataFrame({"Frame": [0, 1, 2]})

    with pytest.raises(ExportSchemaError) as caught:
        validate_export_columns(df)

    message = str(caught.value)
    assert "Time_ms" in message
    for limb in LIMBS:
        assert f"{limb}_Onset" in message and f"{limb}_Zones" in message


def test_single_missing_limb_column_is_named(tmp_path):
    path = write_real_export(tmp_path / "vid_export.csv", touch_frames())
    df = read_export_df(str(path)).drop(columns=["LL_Zones"])

    with pytest.raises(ExportSchemaError) as caught:
        parse_export(df)

    # The message names the missing column(s) first, then lists what WAS found.
    missing_part = str(caught.value).split("(found:")[0]
    assert "LL_Zones" in missing_part
    assert "RH_Zones" not in missing_part  # only the missing ones are blamed


def test_service_aborts_on_missing_columns_without_writing_anything(tmp_path, capsys):
    paths = ProjectPaths(video_name="vid", base_dir=str(tmp_path / "data"))
    (tmp_path / "data" / "vid" / "export").mkdir(parents=True)
    pd.DataFrame({"Frame": [0, 1]}).to_csv(paths.export_csv, index=False)
    out_dir = tmp_path / "plots"

    with pytest.raises(ExportSchemaError):
        analysis_service.run_analysis(paths, frame_rate=25.0, output_folder=str(out_dir))

    assert "ERROR" in capsys.readouterr().out
    # No half-written dashboard: the output folder is not even created.
    assert not out_dir.exists()


def test_missing_xy_columns_only_warn(tmp_path, capsys):
    path = write_real_export(tmp_path / "vid_export.csv", touch_frames())
    df = read_export_df(str(path)).drop(columns=["RH_X", "RH_Y"])

    missing = validate_export_columns(df)
    assert set(missing) == {"RH_X", "RH_Y"}

    data = parse_export(df)
    assert len(data.episodes["RH"]) == 1        # stats unaffected
    assert data.episodes["RH"][0].points == ()  # nothing to draw


# --- legacy tolerance ------------------------------------------------------

def test_legacy_look_column_is_tolerated_and_ignored(tmp_path):
    path = write_real_export(tmp_path / "vid_export.csv", touch_frames())
    df = read_export_df(str(path))
    for limb in LIMBS:
        df[f"{limb}_Look"] = "No"          # retired per-limb Look field
    df["Look"] = "Yes"                      # and the old global one

    assert validate_export_columns(df) == []
    data = parse_export(df)
    episode = data.episodes["RH"][0]
    assert episode.duration_frames == 2
    assert episode.zones_start == ("A",) and episode.zones_end == ("B",)


def test_legacy_six_line_preamble_is_parsed(tmp_path):
    current = write_real_export(tmp_path / "current.csv", touch_frames())
    legacy = tmp_path / "legacy.csv"
    preamble = "\n".join(
        [
            "Program Version: 7.8",
            "Video Name: vid",
            "Labeling Mode: Normal",
            "Frame Rate: 25.0",
            "Zones Covered With Clothes: []",
            "",
        ]
    )
    legacy.write_text(preamble + "\n" + current.read_text(encoding="utf-8"), encoding="utf-8")

    assert LEGACY_PREAMBLE_LINES == 6
    df = read_export_df(str(legacy))

    assert validate_export_columns(df) == []
    assert len(parse_export(df).episodes["RH"]) == 1


def test_header_only_export_is_handled(tmp_path):
    """An export with no rows analyses to an all-zero dashboard, legitimately."""
    path = tmp_path / "empty_export.csv"
    path.write_text(",".join(required_columns() + optional_columns()) + "\n", encoding="utf-8")

    df = read_export_df(str(path))
    data = parse_export(df)

    assert data.row_count == 0
    assert data.total_frames == 0
    assert all(data.episodes[limb] == [] for limb in LIMBS)


def test_unreadable_bytes_raise_export_read_error(tmp_path):
    path = tmp_path / "junk.csv"
    path.write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(ExportReadError):
        read_export_df(str(path))


# --- frame-rate provenance -------------------------------------------------

def test_frame_rate_prefers_caller_then_metadata_then_none(tmp_path, capsys):
    meta = tmp_path / "vid_metadata.json"
    meta.write_text(json.dumps({"Frame Rate": 25.0}), encoding="utf-8")

    assert analysis_service.resolve_frame_rate(30.0, str(meta)) == (30.0, "caller")
    assert "overrides export metadata" in capsys.readouterr().out

    # A 0.0 probe (some containers report 0 for CAP_PROP_FPS) is not a value.
    assert analysis_service.resolve_frame_rate(0.0, str(meta)) == (25.0, "metadata")
    assert analysis_service.resolve_frame_rate(None, str(meta)) == (25.0, "metadata")
    assert "from export metadata" in capsys.readouterr().out

    assert analysis_service.resolve_frame_rate(0.0, str(tmp_path / "nope.json")) == (
        None, "unavailable",
    )
    assert "WARN" in capsys.readouterr().out


def test_frame_rate_metadata_with_zero_value_is_not_used(tmp_path):
    meta = tmp_path / "vid_metadata.json"
    meta.write_text(json.dumps({"Frame Rate": 0.0}), encoding="utf-8")

    assert analysis_service.resolve_frame_rate(None, str(meta)) == (None, "unavailable")


def test_frame_rate_corrupt_metadata_does_not_raise(tmp_path, capsys):
    meta = tmp_path / "vid_metadata.json"
    meta.write_text("{not json", encoding="utf-8")

    assert analysis_service.resolve_frame_rate(None, str(meta)) == (None, "unavailable")
    assert "WARN" in capsys.readouterr().out
