"""M10 regression guards for aligned X/Y/Zones export recovery."""

import csv

from data_utils import import_unified_from_export


def _write_export(path, rows):
    fieldnames = ["Frame", "LH_X", "LH_Y", "LH_Zones"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_M10_bad_token_drops_pair_and_zone(tmp_path, capsys):
    path = tmp_path / "bad_token.csv"
    _write_export(
        path,
        [{
            "Frame": 5,
            "LH_X": "100,oops,300",
            "LH_Y": "10,20,30",
            "LH_Zones": '[["a"], ["b"], ["c"]]',
        }],
    )

    rec = import_unified_from_export(str(path))[5]["LH"]

    assert rec["X"] == [100, 300]
    assert rec["Y"] == [10, 30]
    assert rec["Zones"] == [["a"], ["c"]]
    assert "dropping click 1" in capsys.readouterr().out


def test_M10_single_click_numeric_column_recovers(tmp_path):
    path = tmp_path / "numeric.csv"
    _write_export(
        path,
        [
            {"Frame": 0, "LH_X": 12, "LH_Y": 34, "LH_Zones": '[["a"]]'},
            {"Frame": 1, "LH_X": 56, "LH_Y": 78, "LH_Zones": '[["b"]]'},
        ],
    )

    frames = import_unified_from_export(str(path))

    assert frames[0]["LH"]["X"] == [12]
    assert frames[0]["LH"]["Y"] == [34]
    assert frames[1]["LH"]["X"] == [56]
    assert frames[1]["LH"]["Y"] == [78]


def test_M10_floats_and_negatives_kept(tmp_path):
    path = tmp_path / "float_negative.csv"
    _write_export(
        path,
        [{"Frame": 7, "LH_X": "12.5,-5", "LH_Y": "1,2", "LH_Zones": "[[], []]"}],
    )

    rec = import_unified_from_export(str(path))[7]["LH"]

    assert rec["X"] == [12, -5]
    assert rec["Y"] == [1, 2]
    assert rec["Zones"] == [[], []]


def test_M10_token_count_mismatch_truncates(tmp_path, capsys):
    path = tmp_path / "mismatch.csv"
    _write_export(
        path,
        [{"Frame": 9, "LH_X": "1,2,3", "LH_Y": "7,8", "LH_Zones": "[[], [], []]"}],
    )

    rec = import_unified_from_export(str(path))[9]["LH"]
    output = capsys.readouterr().out

    assert rec["X"] == [1, 2]
    assert rec["Y"] == [7, 8]
    assert rec["Zones"] == [[], []]
    assert "3 X vs 2 Y tokens" in output
    assert "total coordinate pairs dropped=1" in output
