"""M8 regression guards for the unified 3D-pose loader."""

import pandas as pd

from pose_mismatch_data import (
    empty_pose_bundle,
    empty_pose_joint_map,
    load_pose_dataset,
    save_pose_dataset,
)


def test_M8_load_pose_dataset_roundtrip(tmp_path):
    csv_path = str(tmp_path / "vid_3d_unified.csv")

    set_bundle = empty_pose_bundle()
    set_bundle["Note"] = "check left ankle"
    set_bundle["Params"] = {"Par1": "ON", "Par2": "OFF", "Par3": None}
    set_bundle["ScaleRaw"] = 1.17
    set_bundle["ScaleFactor"] = 1.17
    set_bundle["ScaleSet"] = True
    set_bundle["HeadScaleRaw"] = 0.88
    set_bundle["HeadScaleFactor"] = 0.88
    set_bundle["HeadScaleSet"] = True
    set_bundle["Joints"]["L_ANKLE"] = {
        "Event": "ON",
        "X": 120,
        "Y": 340,
        "Opacity": 0.35,
    }
    set_bundle["Joints"]["NECK"] = {
        "Event": "OFF",
        "X": 210,
        "Y": 80,
        "Opacity": 0.8,
    }

    unset_bundle = empty_pose_bundle()
    unset_bundle["Note"] = "defaults"
    unset_bundle["Params"] = {"Par1": None, "Par2": None, "Par3": "ON"}

    expected = {0: set_bundle, 1: unset_bundle}
    save_pose_dataset(csv_path, total_frames=1, frames=expected, changed_only=False)

    assert load_pose_dataset(csv_path) == expected


def test_M8_old_schema_without_headscale_columns(tmp_path):
    csv_path = tmp_path / "old_vid_3d_unified.csv"
    pd.DataFrame(
        [
            {
                "Frame": 4,
                "Note": "old schema",
                "Params": "{}",
                "ScaleRaw": 1.1,
                "ScaleFactor": 1.1,
                "ScaleSet": True,
                "Joints": "{}",
            }
        ]
    ).to_csv(csv_path, index=False)

    bundle = load_pose_dataset(str(csv_path))[4]

    assert bundle["HeadScaleRaw"] == 1.0
    assert bundle["HeadScaleFactor"] == 1.0
    assert bundle["HeadScaleSet"] is False


def test_M8_skips_bad_frame_and_bad_json(tmp_path):
    csv_path = tmp_path / "bad_vid_3d_unified.csv"
    pd.DataFrame(
        [
            {"Frame": "x", "Params": "{}", "Joints": "{}"},
            {"Frame": "7", "Params": "not-json", "Joints": "not-json"},
        ]
    ).to_csv(csv_path, index=False)

    frames = load_pose_dataset(str(csv_path))

    assert list(frames) == [7]
    assert frames[7]["Params"] == {}
    assert frames[7]["Joints"] == empty_pose_joint_map()
