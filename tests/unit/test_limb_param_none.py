"""M1 regression guards for canonical cleared limb-parameter state."""

import csv
import json

from adapters.export_writer import export_from_unified
from adapters.unified_repo import (
    import_unified_from_export,
    load_unified_dataset,
)
from domain.model import empty_bundle


def test_M1_load_unified_normalizes_none_string(tmp_path):
    path = tmp_path / "vid_unified.csv"
    limb = {
        "X": [10],
        "Y": [20],
        "Onset": "ON",
        "Bodypart": "LH",
        "Look": "No",
        "Zones": [["FACE"]],
        "Touch": None,
        "LimbParams": {"Par1": "None", "Par2": "ON", "Par3": None},
    }
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["Frame", "Note", "Params", "LH", "RH", "LL", "RL"]
        )
        writer.writeheader()
        writer.writerow({"Frame": 4, "Params": "{}", "LH": json.dumps(limb)})

    frames = load_unified_dataset(str(path))

    assert frames[4]["LH"]["LimbParams"]["Par1"] is None
    assert frames[4]["LH"]["LimbParams"]["Par2"] == "ON"


def test_M1_export_writes_empty_for_none_string(tmp_path):
    out = tmp_path / "vid_export.csv"
    bundle = empty_bundle()
    bundle["LH"]["LimbParams"] = {"Par1": "None"}

    export_from_unified(
        {0: bundle},
        str(out),
        program_version=7.8,
        video_name="vid",
        labeling_mode="Normal",
        frame_rate=30.0,
        clothes_list=None,
        total_frames=0,
    )

    with out.open(newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["LH_Parameter_1"] == ""


def test_M1_import_export_normalizes(tmp_path):
    path = tmp_path / "vid_export.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Frame", "LH_Parameter_1"])
        writer.writeheader()
        writer.writerow({"Frame": 2, "LH_Parameter_1": "None"})

    frames = import_unified_from_export(str(path))

    assert frames[2]["LH"]["LimbParams"]["Par1"] is None
