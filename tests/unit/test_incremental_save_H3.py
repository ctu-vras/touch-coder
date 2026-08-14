"""H3 regression tests for append-only unified dataset saves."""

import data_utils
from data_utils import empty_bundle, load_unified_dataset, save_unified_dataset


def _touch_bundle(onset, *, note=None):
    bundle = empty_bundle()
    bundle["LH"].update(
        {
            "X": [101],
            "Y": [202],
            "Onset": onset,
            "Zones": [["FACE"]],
        }
    )
    bundle["Params"] = {"Par1": "ON"}
    bundle["Note"] = note
    bundle["Changed"] = True
    return bundle


def test_H3_touch_roundtrip_fidelity(tmp_path):
    path = str(tmp_path / "touch_unified.csv")
    save_unified_dataset(path, 5, {2: _touch_bundle("ON", note="roundtrip")})

    loaded = load_unified_dataset(path)

    assert loaded[2]["LH"]["Onset"] == "ON"
    assert loaded[2]["LH"]["X"] == [101]
    assert loaded[2]["LH"]["Y"] == [202]
    assert loaded[2]["LH"]["Zones"] == [["FACE"]]
    assert loaded[2]["Params"] == {"Par1": "ON"}
    assert loaded[2]["Note"] == "roundtrip"


def test_H3_touch_no_full_reread(tmp_path, monkeypatch):
    path = str(tmp_path / "touch_unified.csv")
    save_unified_dataset(path, 5, {1: _touch_bundle("ON")})

    with monkeypatch.context() as patch:
        patch.setattr(
            data_utils.pd,
            "read_csv",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("full reread")),
        )
        save_unified_dataset(path, 5, {4: _touch_bundle("OFF")})

    loaded = load_unified_dataset(path)
    assert set(loaded) == {1, 4}


def test_H3_touch_last_writer_wins(tmp_path):
    path = str(tmp_path / "touch_unified.csv")
    save_unified_dataset(path, 5, {2: _touch_bundle("ON")})
    save_unified_dataset(path, 5, {2: _touch_bundle("OFF")})

    assert load_unified_dataset(path)[2]["LH"]["Onset"] == "OFF"


def test_H3_touch_compacts_duplicate_journal_rows(tmp_path):
    path = str(tmp_path / "touch_unified.csv")
    save_unified_dataset(path, 5, {2: _touch_bundle("ON")})
    save_unified_dataset(path, 5, {2: _touch_bundle("OFF")})
    save_unified_dataset(path, 5, {2: _touch_bundle("ON")})

    assert len(data_utils.pd.read_csv(path)) == 3
    assert load_unified_dataset(path)[2]["LH"]["Onset"] == "ON"
    assert len(data_utils.pd.read_csv(path)) == 1


def test_H3_touch_recovers_crash_torn_final_append(tmp_path):
    path = tmp_path / "touch_unified.csv"
    save_unified_dataset(str(path), 5, {1: _touch_bundle("ON")})
    with path.open("ab") as f:
        f.write(b'4,"unterminated')

    loaded = load_unified_dataset(str(path))

    assert set(loaded) == {1}
    assert len(data_utils.pd.read_csv(path)) == 1
