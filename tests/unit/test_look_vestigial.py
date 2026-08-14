"""M3 regression guards for retiring the vestigial per-limb Look field."""

import csv
import json

from adapters.unified_repo import import_unified_from_export, load_unified_dataset


def test_M3_recovery_records_have_no_look(tmp_path):
    path = tmp_path / "vid_export.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["Frame", "LH_X", "LH_Y", "LH_Onset", "LH_Zones"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Frame": 1,
                "LH_X": "10",
                "LH_Y": "20",
                "LH_Onset": "ON",
                "LH_Zones": '[["FACE"]]',
            }
        )

    frames = import_unified_from_export(str(path))

    assert all("Look" not in frames[1][limb] for limb in ("LH", "LL", "RH", "RL"))


def test_M3_old_unified_blob_with_look_still_loads(tmp_path):
    path = tmp_path / "vid_unified.csv"
    limb = {
        "X": [10],
        "Y": [20],
        "Onset": "ON",
        "Bodypart": "LH",
        "Look": "No",
        "Zones": [["FACE"]],
        "Touch": None,
    }
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["Frame", "Note", "Params", "LH", "RH", "LL", "RL"]
        )
        writer.writeheader()
        writer.writerow({"Frame": 3, "Params": "{}", "LH": json.dumps(limb)})

    frames = load_unified_dataset(str(path))

    assert frames[3]["LH"]["X"] == [10]
    assert frames[3]["LH"]["Y"] == [20]
    assert frames[3]["LH"]["Onset"] == "ON"
    assert frames[3]["LH"]["Zones"] == [["FACE"]]
