"""
Full persistence lifecycle: the cross-module safety net for the refactor.

    frames dict A
      -> save_frames (dirty-only, one transaction)
      -> load_frames                     == A (minus Changed)
      -> mutate one frame, save again    (REPLACE in place, no duplicate rows)
      -> reopen the DB, load             == the mutated state
      -> export_from_unified             (frozen legacy schema)

Each stage runs the REAL production functions against tmp_path files, so any
refactor that breaks the seam between the repository and the export writer
turns this red even when every per-function unit test still passes.

Two legs were removed in 9.0 with the legacy readers they depended on:

  * export -> `import_unified_from_export` (state recovery from an export CSV);
  * unified-CSV journal -> `load_unified_dataset` compared against the DB.

Consequence worth knowing: nothing now asserts that the export CSV can be read
back into an equivalent store, so an export-format change that silently drops a
field will not be caught here. The export BYTES stay pinned by
tests/unit/test_export_golden_master.py, test_export_schema.py and
test_export_metadata.py — a schema regression is still caught, a round-trip
asymmetry is not.

What the state DB preserves that the export never did (asserted in stage 1):
zone buckets with NO matching click. `Changed` flags and `Touch` are not
serialized by any backend.
"""
import copy
import csv

from adapters.export_writer import export_from_unified
from adapters.sqlite_repo import SqliteRepository
from domain.model import empty_bundle

TOTAL_FRAMES = 10


def _build_frames_a():
    """Realistic multi-limb session: touches on 3 limbs across 3 frames,
    global + limb params (incl. the legacy 'None' string), UTF-8 notes."""
    frames = {}

    b = empty_bundle()
    b["LH"] = {"X": [120, 140], "Y": [80, 95], "Onset": "ON", "Bodypart": "LH",
               "Zones": [["FACE"], ["17L", "17R"]], "Touch": None,
               "LimbParams": {"Par1": "ON", "Par2": None, "Par3": None}}
    b["RH"] = {"X": [300], "Y": [210], "Onset": "ON", "Bodypart": "RH",
               "Zones": [["BELLY"]], "Touch": None}
    b["Params"] = {"Par1": "ON"}
    b["Note"] = "začátek ěšč"
    b["Changed"] = True
    frames[2] = b

    b = empty_bundle()
    b["LH"] = {"X": [125], "Y": [82], "Onset": "OFF", "Bodypart": "LH",
               "Zones": [["FACE"]], "Touch": None}
    b["RL"] = {"X": [50], "Y": [400], "Onset": "ON", "Bodypart": "RL",
               "Zones": [["NN"]], "Touch": None,
               # Legacy artifact: the string "None" (pre-M1 saves).
               "LimbParams": {"Par1": "None", "Par2": "OFF", "Par3": None}}
    b["Changed"] = True
    frames[5] = b

    b = empty_bundle()
    b["Params"] = {"Par1": "OFF", "Par2": "ON", "Par3": None}
    b["Note"] = "params only, with a comma"
    # Zones WITHOUT a click: survives the state-DB roundtrip but is LOST on
    # export->import (realigned to the empty click list) — see final stage.
    b["LL"]["Zones"] = [["GHOST"]]
    b["Changed"] = True
    frames[7] = b

    return frames


def _expected_after_state_roundtrip(frames):
    """What `load_frames` must return for `frames`:
    - the top-level Changed flag is NOT persisted (documented loss);
    - legacy 'None' LimbParams strings are normalized to real None on load."""
    expected = copy.deepcopy(frames)
    for bundle in expected.values():
        bundle.pop("Changed", None)
        for limb in ("LH", "RH", "LL", "RL"):
            lp = bundle[limb].get("LimbParams")
            if isinstance(lp, dict):
                for k, v in lp.items():
                    lp[k] = None if v in (None, "", "None") else v
    return expected


def _clear_changed(frames):
    for bundle in frames.values():
        bundle.pop("Changed", None)


def test_full_lifecycle_state_db_roundtrip_and_export(tmp_path):
    db = str(tmp_path / "state" / "vid.db")
    export = str(tmp_path / "export" / "vid_export.csv")

    # --- Stage 1: save (dirty-only) -> load -> semantic identity -------------
    frames_a = _build_frames_a()
    repo = SqliteRepository(db)
    repo.save_frames(frames_a, TOTAL_FRAMES)

    loaded_a = repo.load_frames()

    assert loaded_a == _expected_after_state_roundtrip(frames_a)
    # Untouched frames were never written: the store holds only labeled frames.
    assert set(loaded_a) == {2, 5, 7}
    # PRESERVED by the state DB (unlike the export): a zone bucket with no click.
    assert loaded_a[7]["LL"]["Zones"] == [["GHOST"]]

    # --- Stage 2: mutate one frame, save again, replace-in-place -------------
    _clear_changed(frames_a)  # the app clears Changed after a save
    mutated = copy.deepcopy(frames_a[5])
    mutated["LH"] = {"X": [1, 2], "Y": [3, 4], "Onset": "ON", "Bodypart": "LH",
                     "Zones": [["B"], ["I"]], "Touch": None}
    mutated["Note"] = "opraveno"
    mutated["Changed"] = True
    frames_a[5] = mutated

    repo.save_frames(frames_a, TOTAL_FRAMES)
    repo.close()

    # Reopen: the bytes on disk carry the state, not a warm cache.
    repo = SqliteRepository(db)
    loaded_b = repo.load_frames()

    # Frame 5 reflects ONLY the newest save; 2 and 7 are untouched.
    assert loaded_b[5] == _expected_after_state_roundtrip({5: mutated})[5]
    assert loaded_b[2] == loaded_a[2]
    assert loaded_b[7] == loaded_a[7]
    assert set(loaded_b) == {2, 5, 7}

    # --- Stage 3: export the loaded store ------------------------------------
    # The repo -> exporter seam, which is the one this file exists to guard.
    # (The export BYTES are pinned by tests/unit/test_export_golden_master.py;
    # here we only assert the seam produces a complete, well-formed table.)
    export_from_unified(
        loaded_b, export, program_version=8.0, video_name="vid",
        labeling_mode="Normal", frame_rate=25.0, clothes_list=None,
        total_frames=TOTAL_FRAMES,
    )
    repo.close()

    with open(export, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # One row per frame 0..TOTAL_FRAMES, labeled or not.
    assert [int(r["Frame"]) for r in rows] == list(range(TOTAL_FRAMES + 1))
    # The labeled frames carried their clicks and notes into the table.
    assert rows[2]["LH_X"] and rows[2]["LH_Y"]
    assert rows[2]["Note"] == "začátek ěšč"
    assert rows[5]["Note"] == "opraveno"
    assert rows[3]["Note"] == ""
