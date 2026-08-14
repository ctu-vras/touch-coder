"""
Golden lock on `export/<video>_metadata.json` (`write_export_metadata`).

The metadata sidecar replaced the old 5-line CSV preamble; downstream tooling
reads it by key NAME, and humans (and diff-based review) rely on its stable key
ORDER, 2-space indentation, and literal UTF-8 (ensure_ascii=False). Until this
file existed there were ZERO tests on it — pin every observable byte-level
property before the refactor.

json.load preserves the file's key order in the returned dict (Python dicts are
insertion-ordered), so `list(d.keys())` asserts the on-disk order exactly.
"""
import json

from data_utils import write_export_metadata


BASE_KEYS = [
    "Program Version",
    "Video Name",
    "Labeling Mode",
    "Frame Rate",
    "Zones Covered With Clothes",
    "Param Labels",
    "Limb Param Labels",
]


def _write(tmp_path, **overrides):
    kwargs = dict(
        program_version=8.0,
        video_name="vid",
        labeling_mode="Normal",
        frame_rate=25.0,
        clothes_list=["BELLY", "17L"],
        param_labels={"Par1": "Looking1"},
        limb_param_labels={"Par1": "Grasp"},
    )
    kwargs.update(overrides)
    path = tmp_path / "vid_metadata.json"
    write_export_metadata(str(path), **kwargs)
    return path


def test_metadata_key_set_and_order_without_labeling_time(tmp_path):
    path = _write(tmp_path)
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    assert list(meta.keys()) == BASE_KEYS


def test_metadata_labeling_time_appends_as_last_key(tmp_path):
    path = _write(tmp_path, labeling_time_seconds=3600.0)
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    assert list(meta.keys()) == BASE_KEYS + ["Total Labeling Time (hours)"]
    assert meta["Total Labeling Time (hours)"] == 1.0


def test_metadata_exact_values_roundtrip(tmp_path):
    path = _write(tmp_path, labeling_time_seconds=7200.0)
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    assert meta == {
        "Program Version": 8.0,
        "Video Name": "vid",
        "Labeling Mode": "Normal",
        "Frame Rate": 25.0,
        "Zones Covered With Clothes": ["BELLY", "17L"],
        "Param Labels": {"Par1": "Looking1"},
        "Limb Param Labels": {"Par1": "Grasp"},
        "Total Labeling Time (hours)": 2.0,
    }


def test_metadata_labeling_time_rounds_to_4dp(tmp_path):
    # 3661 s = 1.01694444... h -> 1.0169 (round-half-even at 4 decimals).
    path = _write(tmp_path, labeling_time_seconds=3661.0)
    meta = json.loads(path.read_text(encoding="utf-8"))
    assert meta["Total Labeling Time (hours)"] == 1.0169


def test_metadata_clothes_none_stays_null_and_labels_default_to_empty(tmp_path):
    """No clothes dialog run -> clothes_list=None is serialized as JSON null
    (NOT an empty list — downstream distinguishes 'never marked' from 'marked
    nothing'). Absent label dicts collapse to {}."""
    path = _write(tmp_path, clothes_list=None, param_labels=None,
                  limb_param_labels=None)
    meta = json.loads(path.read_text(encoding="utf-8"))
    assert meta["Zones Covered With Clothes"] is None
    assert meta["Param Labels"] == {}
    assert meta["Limb Param Labels"] == {}
    assert '"Zones Covered With Clothes": null' in path.read_text(encoding="utf-8")


def test_metadata_is_indent2_json(tmp_path):
    """The sidecar is pretty-printed with indent=2 and LF newlines (atomic_write
    opens with newline='' so json's own '\\n' is never translated, even on
    Windows) — diffs and hand-inspection depend on this layout."""
    raw = _write(tmp_path).read_bytes()
    text = raw.decode("utf-8")
    assert text.startswith('{\n  "Program Version": '), text[:40]
    assert '\n  "Video Name": "vid",\n' in text
    assert b"\r\n" not in raw


def test_metadata_non_ascii_survives_as_utf8_not_escapes(tmp_path):
    """ensure_ascii=False is part of the contract: a Czech zone/param label must
    land in the file as literal UTF-8 bytes, never \\uXXXX escapes."""
    path = _write(tmp_path, clothes_list=["Bříško"],
                  param_labels={"Par1": "Pohled ěšč"})
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "metadata grew a UTF-8 BOM"
    assert "Bříško".encode("utf-8") in raw
    assert "Pohled ěšč".encode("utf-8") in raw
    assert b"\\u" not in raw
    meta = json.loads(path.read_text(encoding="utf-8"))
    assert meta["Zones Covered With Clothes"] == ["Bříško"]
    assert meta["Param Labels"]["Par1"] == "Pohled ěšč"
