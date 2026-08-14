"""
Per-video labeling-time accumulator: the writer/reader key mismatch bug.

Before the fix, `_write_video_time` stored "Total Labeling Time (hours)" while
`_load_video_time` read "Total Labeling Time (seconds)", so the accumulator
silently reset to 0 on every app restart. The state file now stores SECONDS,
and loading falls back to hours*3600 to migrate files written by the buggy
builds. The EXPORT metadata key stays "Total Labeling Time (hours)" (frozen by
the golden metadata test) — that contract is untouched.

Run:  uv run pytest tests/ -k labeling_time
"""
import json

from service_layer.project_service import (
    load_labeling_time_seconds,
    write_labeling_time_seconds,
)


def _state_path(tmp_path):
    return str(tmp_path / "data" / "vid_metadata.json")


def test_roundtrip_survives_a_restart(tmp_path):
    path = _state_path(tmp_path)

    write_labeling_time_seconds(path, 4321.5)

    # The regression: this read used to return 0.0 and wipe the accumulator.
    assert load_labeling_time_seconds(path) == 4321.5


def test_state_file_stores_seconds(tmp_path):
    path = _state_path(tmp_path)
    write_labeling_time_seconds(path, 7200.0)

    payload = json.loads(open(path, encoding="utf-8").read())

    assert payload["Total Labeling Time (seconds)"] == 7200.0
    # Hours are export-only; the internal state file no longer carries them.
    assert "Total Labeling Time (hours)" not in payload


def test_legacy_hours_only_file_is_migrated(tmp_path):
    """Files written by the buggy version hold ONLY hours — read them as
    seconds instead of silently starting over at zero."""
    path = _state_path(tmp_path)
    write_labeling_time_seconds(path, 0.0)  # creates the directory
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"Total Labeling Time (hours)": 2.5}, f)

    assert load_labeling_time_seconds(path) == 9000.0  # 2.5 h


def test_seconds_win_when_both_keys_are_present(tmp_path):
    path = _state_path(tmp_path)
    write_labeling_time_seconds(path, 0.0)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "Total Labeling Time (seconds)": 100.0,
                "Total Labeling Time (hours)": 99.0,
            },
            f,
        )

    assert load_labeling_time_seconds(path) == 100.0


def test_missing_and_corrupt_files_start_at_zero(tmp_path):
    missing = _state_path(tmp_path)
    assert load_labeling_time_seconds(missing) == 0.0

    write_labeling_time_seconds(missing, 0.0)
    with open(missing, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert load_labeling_time_seconds(missing) == 0.0
