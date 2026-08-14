"""M12 regression guards for analysis reads and transition metrics."""

import pytest

import analysis
from adapters.export_writer import export_from_unified


def _row(frame, onset, zones):
    return {"Frame": frame, "Onset": onset, "X": [], "Y": [], "Zones": zones}


def _plain_transitions(metrics):
    return {
        start: dict(ends) for start, ends in metrics["transition_counts"].items()
    }


def test_M12_open_touch_no_self_transition():
    metrics = analysis._compute_limb_metrics([_row(2, "ON", [["FACE"]]), _row(5, "", [])])

    assert _plain_transitions(metrics) == {}
    assert metrics["touch_durations"] == [3]
    assert metrics["zone_touch_count"]["FACE"] == 1


def test_M12_closed_touch_transition_counted():
    metrics = analysis._compute_limb_metrics(
        [_row(2, "ON", [["FACE"]]), _row(5, "OFF", [["BELLY"]])]
    )

    assert _plain_transitions(metrics) == {"FACE": {"BELLY": 1}}


def test_M12_reader_missing_file_raises_oserror(tmp_path):
    with pytest.raises(FileNotFoundError):
        analysis._read_export_df(str(tmp_path / "missing.csv"))


def test_M12_reader_parse_failure_logged_and_chained(tmp_path, capsys):
    path = tmp_path / "invalid.csv"
    path.write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(analysis.ExportReadError) as caught:
        analysis._read_export_df(str(path))

    assert caught.value.__cause__ is not None
    output = capsys.readouterr().out
    assert "WARN" in output and str(path) in output


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

    assert "Frame" in analysis._read_export_df(str(current)).columns
    assert "Frame" in analysis._read_export_df(str(legacy)).columns


def test_M12_multizone_pairwise_transitions():
    metrics = analysis._compute_limb_metrics(
        [_row(2, "ON", [["BELLY", "HIP"]]), _row(5, "OFF", [["FACE"]])]
    )
    single = analysis._compute_limb_metrics(
        [_row(2, "ON", [["BELLY"]]), _row(5, "OFF", [["FACE"]])]
    )

    assert _plain_transitions(metrics) == {
        "BELLY": {"FACE": 1},
        "HIP": {"FACE": 1},
    }
    assert _plain_transitions(single) == {"BELLY": {"FACE": 1}}
