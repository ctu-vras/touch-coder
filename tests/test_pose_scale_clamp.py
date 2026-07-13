"""M11 regression guards for the documented pose-scale range."""

import pandas as pd

from pose_mismatch_data import export_pose_dataset, load_pose_dataset, scale_raw_to_factor


def _write_pose_row(path, **overrides):
    row = {
        "Frame": 0,
        "Note": "",
        "Params": "{}",
        "ScaleRaw": 1.0,
        "ScaleFactor": 1.0,
        "ScaleSet": True,
        "HeadScaleRaw": 1.0,
        "HeadScaleFactor": 1.0,
        "HeadScaleSet": True,
        "Joints": "{}",
    }
    row.update(overrides)
    pd.DataFrame([row]).to_csv(path, index=False)


def test_M11_out_of_range_factor_clamped_on_load(tmp_path):
    path = tmp_path / "pose.csv"
    _write_pose_row(path, ScaleRaw=5.0, ScaleFactor=5.0)

    bundle = load_pose_dataset(str(path))[0]

    assert bundle["ScaleRaw"] == 1.3
    assert bundle["ScaleFactor"] == 1.3


def test_M11_nan_scale_becomes_neutral(tmp_path):
    path = tmp_path / "pose.csv"
    _write_pose_row(path, ScaleRaw="", ScaleFactor="")

    bundle = load_pose_dataset(str(path))[0]

    assert bundle["ScaleRaw"] == 1.0
    assert bundle["ScaleFactor"] == 1.0


def test_M11_scale_raw_to_factor_nan():
    assert scale_raw_to_factor(float("nan")) == 1.0


def test_M11_headscale_clamped(tmp_path):
    path = tmp_path / "pose.csv"
    _write_pose_row(path, HeadScaleRaw=0.1, HeadScaleFactor=0.1)

    bundle = load_pose_dataset(str(path))[0]

    assert bundle["HeadScaleRaw"] == 0.7
    assert bundle["HeadScaleFactor"] == 0.7


def test_M11_export_stays_in_range(tmp_path):
    source = tmp_path / "pose.csv"
    output = tmp_path / "pose_export.csv"
    _write_pose_row(
        source,
        ScaleRaw=5.0,
        ScaleFactor=5.0,
        HeadScaleRaw=0.1,
        HeadScaleFactor=0.1,
    )
    frames = load_pose_dataset(str(source))

    export_pose_dataset(frames, str(output), total_frames=0, frame_rate=30.0)
    exported = pd.read_csv(output)

    for column in ("ScaleFactor", "HeadScaleFactor"):
        assert exported[column].notna().all()
        assert exported[column].between(0.7, 1.3).all()
