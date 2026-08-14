"""
Full persistence lifecycle: the cross-module safety net for the refactor.

    frames dict A
      -> save_frames (dirty-only, one transaction)
      -> load_frames                     == A (minus Changed)
      -> mutate one frame, save again    (REPLACE in place, no duplicate rows)
      -> reopen the DB, load             == the mutated state
      -> export_from_unified             (frozen legacy schema)
      -> delete the state DB             (simulates the recovery path)
      -> import_unified_from_export      == everything the export preserves

Each stage runs the REAL production functions against tmp_path files, so any
refactor that breaks a seam between the repository and the export writer turns
this red even when every per-function unit test still passes.

The second test keeps the LEGACY leg alive: a pre-SQLite project's unified-CSV
journal, loaded by the reader that still ships, must produce the same store the
repository does — that equality is what makes the migration safe.

What the export -> import leg provably preserves (asserted below):
X/Y click lists, Onset, Zones aligned with clicks, global params, limb params,
and Notes (incl. UTF-8). What it LOSES — asserted explicitly so the loss stays
a documented decision, not an accident:

  * `Changed` flags (never serialized by any backend);
  * `Touch` (not an export column; import always leaves it None);
  * Zone buckets with NO matching click (import realigns zones to the click
    list, so a zones-without-coordinates record comes back empty — note the
    state DB itself KEEPS them, asserted in stage 2);
  * dict SHAPE: import materializes a bundle for EVERY exported row (no gaps),
    Params/LimbParams come back as full Par1..Par3 dicts padded with None (or
    are dropped entirely when all-None), and Bodypart is reset to the limb key.
"""
import copy
import os

from adapters.export_writer import export_from_unified
from adapters.sqlite_repo import SqliteRepository
from adapters.unified_repo import import_unified_from_export, load_unified_dataset
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


def test_full_lifecycle_state_db_roundtrip_and_export_recovery(tmp_path):
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

    # --- Stage 3: export, delete the DB, recover via import -----------------
    export_from_unified(
        loaded_b, export, program_version=8.0, video_name="vid",
        labeling_mode="Normal", frame_rate=25.0, clothes_list=None,
        total_frames=TOTAL_FRAMES,
    )
    repo.close()
    os.remove(db)  # the recovery scenario: the state DB is gone
    assert not os.path.exists(db)

    recovered = import_unified_from_export(export)

    # Import materializes EVERY exported row — no gaps (shape difference vs
    # the state store, which only held labeled frames).
    assert set(recovered) == set(range(TOTAL_FRAMES + 1))

    # PRESERVED: clicks, onsets, zones-aligned-to-clicks, per limb.
    for f in (2, 5, 7):
        for limb in ("LH", "RH", "LL", "RL"):
            src, rec = loaded_b[f][limb], recovered[f][limb]
            assert rec["X"] == src["X"], (f, limb)
            assert rec["Y"] == src["Y"], (f, limb)
            assert rec["Onset"] == (src.get("Onset") or ""), (f, limb)
            if src["X"]:  # zones survive only where clicks exist (see below)
                assert rec["Zones"] == src["Zones"], (f, limb)

    # PRESERVED: notes, incl. UTF-8 and commas.
    assert recovered[2]["Note"] == "začátek ěšč"
    assert recovered[5]["Note"] == "opraveno"
    assert recovered[7]["Note"] == "params only, with a comma"
    assert recovered[3]["Note"] is None

    # PRESERVED: global params — but padded to the full Par1..3 shape.
    assert recovered[2]["Params"] == {"Par1": "ON", "Par2": None, "Par3": None}
    assert recovered[7]["Params"] == {"Par1": "OFF", "Par2": "ON", "Par3": None}
    # An all-empty params frame comes back as {} (not a None-padded dict).
    assert recovered[5]["Params"] == {}

    # PRESERVED: limb params (padded), with legacy 'None' already scrubbed by
    # the earlier state-DB load.
    assert recovered[2]["LH"]["LimbParams"] == {"Par1": "ON", "Par2": None, "Par3": None}
    assert recovered[5]["RL"]["LimbParams"] == {"Par1": None, "Par2": "OFF", "Par3": None}
    # A limb whose params were all None gets NO LimbParams key at all.
    assert "LimbParams" not in recovered[2]["RH"]

    # LOST (documented, intentional): zone buckets with no matching click —
    # import realigns Zones to the X/Y pair list, so frame 7's clickless
    # LL bucket [['GHOST']] is dropped.
    assert loaded_b[7]["LL"]["Zones"] == [["GHOST"]]
    assert recovered[7]["LL"]["Zones"] == []

    # LOST (documented): Touch is not an export column — always None after
    # import — and Changed flags never round-trip through any backend.
    for f in recovered:
        assert "Changed" not in recovered[f]
        for limb in ("LH", "RH", "LL", "RL"):
            assert recovered[f][limb]["Touch"] is None
            # Bodypart is reconstructed from the column prefix, not stored.
            assert recovered[f][limb]["Bodypart"] == limb


def test_state_db_and_legacy_journal_agree_on_the_same_session(tmp_path):
    """The seam the migration rests on: the retired journal's reader and the
    state DB must produce the SAME in-memory store for the same session, and
    the same export bytes from it.

    `load_unified_dataset` still ships (it is the migration input), so this can
    be asserted directly — build a journal with the legacy row format, load it,
    push it through the repository, and compare both sides.
    """
    import json

    journal = tmp_path / "state" / "vid_unified.csv"
    journal.parent.mkdir(parents=True, exist_ok=True)
    frames_a = _build_frames_a()
    rows = ["Frame,Note,Params,LH,RH,LL,RL\n"]
    for frame in sorted(frames_a):
        b = frames_a[frame]
        cells = [
            str(frame),
            "" if b.get("Note") is None else b["Note"],
            json.dumps(b.get("Params", {})),
            json.dumps(b["LH"]), json.dumps(b["RH"]),
            json.dumps(b["LL"]), json.dumps(b["RL"]),
        ]
        rows.append(",".join('"' + c.replace('"', '""') + '"' for c in cells) + "\n")
    journal.write_text("".join(rows), encoding="utf-8")

    from_journal = load_unified_dataset(str(journal))

    repo = SqliteRepository(str(tmp_path / "state" / "vid.db"))
    try:
        for bundle in from_journal.values():
            bundle["Changed"] = True
        repo.save_frames(from_journal, TOTAL_FRAMES)
        for bundle in from_journal.values():
            bundle.pop("Changed")
        from_db = repo.load_frames()
    finally:
        repo.close()

    # The journal blobs carry Bodypart verbatim; the DB reconstructs it. Every
    # other field must match exactly.
    assert from_db == from_journal

    journal_export = tmp_path / "from_journal.csv"
    db_export = tmp_path / "from_db.csv"
    for store, out in ((from_journal, journal_export), (from_db, db_export)):
        export_from_unified(
            store, str(out), program_version=8.0, video_name="vid",
            labeling_mode="Normal", frame_rate=25.0, clothes_list=None,
            total_frames=TOTAL_FRAMES,
        )

    assert db_export.read_bytes() == journal_export.read_bytes()
