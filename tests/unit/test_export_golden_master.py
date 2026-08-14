"""
Golden-master lock on the VALUE encoding of `export/<video>_export.csv`.

`test_export_schema` freezes the column ORDER; this file freezes the cell
BYTES. Downstream research pipelines parse the export with their own readers
(comma-split X/Y lists, `json.loads` on Zones, exact "ON"/"OFF" tokens), so any
drift in how a value is serialized — a changed float format, JSON separators
without the space after commas, quoting changes, params leaking "None" strings —
silently corrupts published datasets even though the schema test stays green.

The checked-in fixture `fixtures/golden_export.csv` was generated from the
CURRENT exporter (current behavior IS the contract) and covers every encoding
case: multi-click cells, multi-zone buckets, empty frame gaps, params-only
frames, the legacy "None" limb-param string, and a note with a comma + UTF-8.

Newlines are the ONE platform-dependent byte: pandas' `to_csv` writes
`os.linesep` line terminators (CRLF on Windows, LF elsewhere), so comparisons
normalize CRLF -> LF on both sides instead of hardcoding either.

If this test ever goes red, the export encoding changed: either revert the
change, or coordinate with the downstream pipeline and regenerate the fixture
deliberately in the same commit.
"""
import os

import pandas as pd

from data_utils import export_from_unified, empty_bundle

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "golden_export.csv")

GOLDEN_FPS = 25.0          # frame f -> Time_ms = f * 40.0
GOLDEN_TOTAL_FRAMES = 6    # rows for frames 0..6 inclusive (7 rows)


def build_golden_frames():
    """One frames dict exercising every value-encoding path of the exporter.

    Frames 0 and 3 are deliberately ABSENT: the exporter must synthesize empty
    rows for them (gap handling).
    """
    frames = {}

    # Frame 1 — multi-click LH (2 clicks, single-zone buckets) + a second limb.
    # The LH_Zones cell must serialize as the exact bytes "[[""L""], [""I""]]"
    # (json.dumps default separators put a space after the comma BETWEEN
    # buckets; csv QUOTE_MINIMAL doubles the inner quotes).
    b = empty_bundle()
    b["LH"] = {"X": [10, 20], "Y": [30, 40], "Onset": "ON", "Bodypart": "LH",
               "Zones": [["L"], ["I"]], "Touch": None}
    b["RL"] = {"X": [5], "Y": [6], "Onset": "OFF", "Bodypart": "RL",
               "Zones": [["17L"]], "Touch": None}
    frames[1] = b

    # Frame 2 — single click, Onset OFF (unquoted single-token X/Y cells).
    b = empty_bundle()
    b["RH"] = {"X": [100], "Y": [200], "Onset": "OFF", "Bodypart": "RH",
               "Zones": [["FACE"]], "Touch": None}
    frames[2] = b

    # Frame 4 — params only, no clicks anywhere.
    # Global: Par2 is None -> empty cell. Limb: the legacy string "None"
    # (old toggle_limb_parameter artifact) must be normalized to an empty cell.
    b = empty_bundle()
    b["Params"] = {"Par1": "ON", "Par2": None, "Par3": "OFF"}
    b["LL"]["LimbParams"] = {"Par1": "None", "Par2": "ON"}
    b["RH"]["LimbParams"] = {"Par3": "OFF"}
    frames[4] = b

    # Frame 5 — note with a comma AND non-ASCII (forces quoting + UTF-8),
    # plus a click with a 2-element zone bucket and an EMPTY Onset.
    b = empty_bundle()
    b["Note"] = "chlapec ěšč, poznámka"
    b["RL"] = {"X": [7], "Y": [8], "Onset": "", "Bodypart": "RL",
               "Zones": [["L", "I"]], "Touch": None}
    frames[5] = b

    # Frame 6 — last frame is inclusive (row count = total_frames + 1).
    b = empty_bundle()
    b["LH"] = {"X": [50], "Y": [60], "Onset": "OFF", "Bodypart": "LH",
               "Zones": [["B"]], "Touch": None}
    frames[6] = b

    return frames


def _export_golden(tmp_path, frame_rate=GOLDEN_FPS):
    out = tmp_path / "vid_export.csv"
    export_from_unified(
        build_golden_frames(), str(out), program_version=8.0, video_name="vid",
        labeling_mode="Normal", frame_rate=frame_rate, clothes_list=None,
        total_frames=GOLDEN_TOTAL_FRAMES,
    )
    return out


def _normalized(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")


def test_export_matches_golden_master_byte_for_byte(tmp_path):
    """Full-file lock: every cell of the export must equal the checked-in
    fixture (after newline normalization — the only platform-dependent byte)."""
    out = _export_golden(tmp_path)

    assert os.path.exists(FIXTURE), (
        "Golden fixture missing — it must be checked in at "
        "tests/unit/fixtures/golden_export.csv (see module docstring)."
    )
    with open(FIXTURE, "rb") as fh:
        expected = _normalized(fh.read())
    actual = _normalized(out.read_bytes())

    assert actual == expected, (
        "Export VALUE encoding drifted from the golden master — downstream "
        "pipelines parse these exact bytes. Diff the exported file against "
        "tests/unit/fixtures/golden_export.csv before changing anything."
    )


def test_export_zone_cells_keep_json_dumps_quoting(tmp_path):
    """Raw-byte tripwire for the Zones cells specifically: json.dumps default
    separators emit a space after the comma between buckets, and pandas
    QUOTE_MINIMAL doubles the embedded quotes. Downstream json.loads-based
    readers depend on this exact shape surviving pandas upgrades."""
    raw = _export_golden(tmp_path).read_bytes()

    # Two single-zone buckets (frame 1, LH):
    assert b'"[[""L""], [""I""]]"' in raw
    # One 2-element bucket — space after the comma INSIDE the bucket too (frame 5, RL):
    assert b'"[[""L"", ""I""]]"' in raw
    # Empty zones are an UNQUOTED two-byte [] cell:
    assert b",[]," in raw


def test_export_is_utf8_without_bom(tmp_path):
    """The export is written UTF-8 with NO BOM; a BOM would corrupt the
    'Frame' header for naive readers, and \\u-escaping the note would change
    its bytes for everyone else."""
    raw = _export_golden(tmp_path).read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf"), "export grew a UTF-8 BOM"
    # The comma forces quoting; the diacritics must appear as literal UTF-8
    # bytes (never \u escapes — csv has no escape mechanism, so any change
    # here means an encoding regression).
    assert '"chlapec ěšč, poznámka"'.encode("utf-8") in raw
    assert b"\\u" not in raw


def test_export_line_terminator_is_os_linesep(tmp_path):
    """pandas to_csv on a text handle uses os.linesep (CRLF on Windows, LF on
    POSIX). Pin that platform-relative behavior — hardcoding either would make
    this suite lie on the other OS."""
    raw = _export_golden(tmp_path).read_bytes()

    header_end = raw.index(b"\n")
    if os.linesep == "\r\n":
        assert raw[header_end - 1:header_end + 1] == b"\r\n"
    else:
        assert raw[header_end - 1:header_end] != b"\r"


def test_export_zero_frame_rate_zeroes_time_only(tmp_path):
    """A 0-FPS probe (some containers) must not abort the export: Time_ms
    falls back to 0.0 for EVERY row while all other cells stay identical to
    the golden encoding."""
    out_fps = _export_golden(tmp_path)
    out_zero = tmp_path / "vid_zero_export.csv"
    export_from_unified(
        build_golden_frames(), str(out_zero), program_version=8.0,
        video_name="vid", labeling_mode="Normal", frame_rate=0.0,
        clothes_list=None, total_frames=GOLDEN_TOTAL_FRAMES,
    )

    df_fps = pd.read_csv(out_fps, dtype=str, keep_default_na=False)
    df_zero = pd.read_csv(out_zero, dtype=str, keep_default_na=False)

    assert (df_zero["Time_ms"] == "0.0").all()
    others = [c for c in df_fps.columns if c != "Time_ms"]
    assert df_zero[others].equals(df_fps[others])
