"""
The working-state repository: `state/<video>.db` round-trip fidelity.

`adapters.export_writer` consumes in-memory `FrameBundle` dicts, never files,
so the frozen export contract reduces to ONE property: whatever
`SqliteRepository.save_frames` writes, `load_frames` must give back unchanged.
These tests hammer that property from both sides — a field-by-field walk over
every distinction the exporter or the GUI can observe, plus a whole-store
equality check (`save -> load == the in-memory original`).

Also pinned here: the SQLite properties that REPLACED the journal's mechanisms
(idempotent re-save, dirty-only writes, `user_version`, FK cascade), the
Tk-thread ownership guard, and the byte-compatibility of the clothes zone list
with the retired `extract_zones_from_file`.

Black-box, tmp_path only — never touches the real data/ tree.

Run:  uv run pytest tests/unit/test_sqlite_repo.py -v
"""
import copy
import json
import os
import sqlite3
import threading

import pytest

from adapters.sqlite_repo import SCHEMA_VERSION, SchemaVersionError, SqliteRepository
from adapters.unified_repo import extract_zones_from_file
from domain.model import empty_bundle, empty_record


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepository(str(tmp_path / "state" / "vid.db"))
    yield r
    r.close()


def _dirty(bundle):
    bundle["Changed"] = True
    return bundle


def _expected(frames):
    """What `load_frames` must return: the same store minus the `Changed`
    dirty flag, which no backend has ever serialized."""
    expected = copy.deepcopy(frames)
    for bundle in expected.values():
        bundle.pop("Changed", None)
    return expected


def _rich_frames():
    """One store exercising every representable distinction at once."""
    frames = {}

    # Multi-click frame, multi-zone bucket, limb params with an explicit None.
    b = empty_bundle()
    b["LH"] = {"X": [120, 140], "Y": [80, 95], "Onset": "ON", "Bodypart": "LH",
               "Zones": [["FACE"], ["17L", "17R"]], "Touch": None,
               "LimbParams": {"Par1": "ON", "Par2": None, "Par3": None}}
    b["RH"] = {"X": [300], "Y": [210], "Onset": "OFF", "Bodypart": "RH",
               "Zones": [["BELLY"]], "Touch": None}
    b["Params"] = {"Par1": "ON"}
    b["Note"] = "začátek ěšč, s čárkou"
    frames[2] = _dirty(b)

    # Params-only frame; Par3 is present-but-None; a zone bucket with NO click.
    b = empty_bundle()
    b["Params"] = {"Par1": "OFF", "Par2": "ON", "Par3": None}
    b["Note"] = "params only, with a comma"
    b["LL"]["Zones"] = [["GHOST"]]
    frames[7] = _dirty(b)

    # Completely empty bundle: its EXISTENCE is the only thing to preserve.
    frames[9] = _dirty(empty_bundle())

    # Onset None (real archived data has it) and an empty-string note.
    b = empty_bundle()
    b["RL"] = {"X": [], "Y": [], "Onset": None, "Bodypart": "RL",
               "Zones": [], "Touch": None}
    b["Note"] = ""
    frames[40] = _dirty(b)

    return frames


# === the core property =========================================================
def test_save_load_round_trips_the_whole_store(repo):
    """`save -> load` equals the in-memory original, field for field. This is
    the single assertion the frozen export contract rests on."""
    frames = _rich_frames()
    expected = _expected(frames)

    repo.save_frames(frames, total_frames=50)

    assert repo.load_frames() == expected


def test_round_trip_survives_a_reopen(tmp_path):
    """Same property across a process boundary: the bytes on disk, not a warm
    in-memory cache, carry the state."""
    db = str(tmp_path / "state" / "vid.db")
    frames = _rich_frames()
    expected = _expected(frames)

    first = SqliteRepository(db)
    first.save_frames(frames, total_frames=50)
    first.close()

    second = SqliteRepository(db)
    try:
        assert second.load_frames() == expected
    finally:
        second.close()


@pytest.mark.parametrize("note", [None, "", "plain", "a, comma", "ěšč UTF-8", "0"])
def test_note_none_empty_and_text_stay_distinct(repo, note):
    """None / '' / text are three different values. The exporter writes
    `b.get("Note", "")` straight into the CSV cell, so collapsing any pair
    would silently change published notes."""
    b = empty_bundle()
    b["Note"] = note
    repo.save_frames({3: _dirty(b)}, total_frames=5)

    loaded = repo.load_frames()[3]
    assert loaded["Note"] == note
    assert (loaded["Note"] is None) == (note is None)


def test_absent_param_key_and_present_none_are_different(repo):
    """`{}` vs `{"Par3": None}`: an ABSENT row means the key is not in the
    dict, a row with `state IS NULL` means the key is present with value
    None."""
    empty_params = empty_bundle()
    empty_params["Params"] = {}
    explicit_none = empty_bundle()
    explicit_none["Params"] = {"Par1": "ON", "Par3": None}

    repo.save_frames(
        {1: _dirty(empty_params), 2: _dirty(explicit_none)}, total_frames=5
    )

    loaded = repo.load_frames()
    assert loaded[1]["Params"] == {}
    assert loaded[2]["Params"] == {"Par1": "ON", "Par3": None}


def test_absent_limbparams_key_and_empty_dict_are_different(repo):
    """The `has_limb_params` flag exists exactly for this: no `LimbParams` key
    at all vs an empty `LimbParams` dict."""
    without = empty_bundle()
    with_empty = empty_bundle()
    with_empty["LH"]["LimbParams"] = {}
    with_none = empty_bundle()
    with_none["RH"]["LimbParams"] = {"Par2": None}

    repo.save_frames(
        {1: _dirty(without), 2: _dirty(with_empty), 3: _dirty(with_none)},
        total_frames=5,
    )

    loaded = repo.load_frames()
    assert "LimbParams" not in loaded[1]["LH"]
    assert loaded[2]["LH"]["LimbParams"] == {}
    assert loaded[3]["RH"]["LimbParams"] == {"Par2": None}


def test_click_index_is_the_xy_zones_alignment(repo):
    """One `clicks` row per index; `click_index` IS the alignment that the
    `{limb}_X` / `{limb}_Y` / `{limb}_Zones` export cells depend on. Order and
    pairing must survive verbatim."""
    b = empty_bundle()
    b["RL"] = {"X": [11, 22, 33], "Y": [44, 55, 66], "Onset": "ON",
               "Bodypart": "RL", "Zones": [["A"], ["B", "C"], []], "Touch": None}
    repo.save_frames({4: _dirty(b)}, total_frames=5)

    rec = repo.load_frames()[4]["RL"]
    assert rec["X"] == [11, 22, 33]
    assert rec["Y"] == [44, 55, 66]
    assert rec["Zones"] == [["A"], ["B", "C"], []]


def test_zone_bucket_without_a_click_survives(repo):
    """Legacy data holds zone buckets with no matching coordinate. The journal
    preserved them (tests/integration/test_lifecycle.py pins that), so the DB
    must too — hence nullable `x`/`y` rather than a click-only table."""
    b = empty_bundle()
    b["LL"]["Zones"] = [["GHOST"], ["ALSO_GHOST"]]
    repo.save_frames({6: _dirty(b)}, total_frames=8)

    rec = repo.load_frames()[6]["LL"]
    assert rec["X"] == []
    assert rec["Y"] == []
    assert rec["Zones"] == [["GHOST"], ["ALSO_GHOST"]]


def test_empty_bucket_is_distinct_from_no_bucket(repo):
    """`Zones=[[]]` (one EMPTY bucket) vs `Zones=[]` (no buckets): `zones='[]'`
    vs a NULL `zones` column. `json.dumps` puts these in different export
    cells."""
    one_empty = empty_bundle()
    one_empty["LH"]["Zones"] = [[]]
    none_at_all = empty_bundle()
    none_at_all["LH"] = {"X": [5], "Y": [6], "Onset": "ON", "Bodypart": "LH",
                         "Zones": [], "Touch": None}

    repo.save_frames({1: _dirty(one_empty), 2: _dirty(none_at_all)}, total_frames=5)

    loaded = repo.load_frames()
    assert loaded[1]["LH"]["Zones"] == [[]]
    assert loaded[2]["LH"]["Zones"] == []
    assert loaded[2]["LH"]["X"] == [5]


def test_flat_legacy_zone_shape_round_trips(repo):
    """`csv_to_dict` (legacy per-limb CSVs) yields FLAT `list[str]` zones, not
    list-of-lists. A bucket is therefore sometimes a bare string, and
    `json.dumps`/`loads` must return the same type."""
    b = empty_bundle()
    b["RH"] = {"X": [1, 2], "Y": [3, 4], "Onset": "ON", "Bodypart": "RH",
               "Zones": ["FACE", "BELLY"], "Touch": None}
    repo.save_frames({1: _dirty(b)}, total_frames=2)

    assert repo.load_frames()[1]["RH"]["Zones"] == ["FACE", "BELLY"]


def test_empty_frames_and_gaps_are_preserved_exactly(repo):
    """Frame EXISTENCE is data: the store holds only labeled frames (the
    exporter synthesizes rows for the gaps), so the key set must round-trip."""
    frames = {0: _dirty(empty_bundle()), 5: _dirty(empty_bundle()),
              123: _dirty(empty_bundle())}

    repo.save_frames(frames, total_frames=200)

    loaded = repo.load_frames()
    assert set(loaded) == {0, 5, 123}
    for frame in loaded.values():
        assert frame == empty_bundle()


def test_missing_limb_row_loads_as_the_canonical_empty_record(repo):
    """A limb with nothing to store gets no row at all; `load_frames` rebuilds
    `empty_record(limb)`, which is what every writer would have produced."""
    repo.save_frames({1: _dirty(empty_bundle())}, total_frames=2)

    bundle = repo.load_frames()[1]
    for limb in ("LH", "RH", "LL", "RL"):
        assert bundle[limb] == empty_record(limb)


def test_onset_none_and_empty_string_stay_distinct(repo):
    """Archived unified journals contain `"Onset": null` next to `""`. Both
    export as an empty cell, but the store must not invent a value."""
    none_onset = empty_bundle()
    none_onset["LH"] = {"X": [], "Y": [], "Onset": None, "Bodypart": "LH",
                        "Zones": [], "Touch": None}
    repo.save_frames({1: _dirty(none_onset)}, total_frames=2)

    assert repo.load_frames()[1]["LH"]["Onset"] is None


def test_bodypart_is_reconstructed_and_touch_is_always_none(repo):
    """Documented, deliberate non-persistence: `Bodypart` is rebuilt from the
    limb key (every writer sets it to exactly that; nothing reads it) and
    `Touch` has no writer, no reader and no export column."""
    b = empty_bundle()
    b["LH"] = {"X": [1], "Y": [2], "Onset": "ON", "Bodypart": "WRONG",
               "Zones": [["A"]], "Touch": 99}
    repo.save_frames({1: _dirty(b)}, total_frames=2)

    rec = repo.load_frames()[1]["LH"]
    assert rec["Bodypart"] == "LH"
    assert rec["Touch"] is None


def test_changed_flag_is_never_persisted(repo):
    repo.save_frames({1: _dirty(empty_bundle())}, total_frames=2)

    assert "Changed" not in repo.load_frames()[1]


# === save semantics (what replaced the journal's mechanisms) ===================
def test_only_dirty_frames_are_written(repo):
    """`Changed` is still the dirty tracker. A clean frame must not be touched,
    so a stale in-memory bundle can never overwrite newer stored rows."""
    dirty = empty_bundle()
    dirty["Note"] = "written"
    clean = empty_bundle()
    clean["Note"] = "not written"

    written = repo.save_frames({1: _dirty(dirty), 2: clean}, total_frames=5)

    assert written == 1
    assert set(repo.load_frames()) == {1}


def test_second_identical_save_is_a_no_op(repo):
    """SUPERSEDES the journal's "grows then compacts" invariant.

    The append-only journal wrote a duplicate row per re-save and needed
    load-time compaction to bound the file. An UPSERT has no such growth: the
    property to protect is now that re-saving identical data leaves row counts
    AND contents unchanged.
    """
    frames = _rich_frames()
    repo.save_frames(frames, total_frames=50)
    first_counts = _row_counts(repo)
    first_load = repo.load_frames()

    for bundle in frames.values():           # the app clears these after a save
        bundle["Changed"] = True             # re-dirty to force a second write
    repo.save_frames(frames, total_frames=50)

    assert _row_counts(repo) == first_counts
    assert repo.load_frames() == first_load


def test_resaving_a_frame_replaces_rather_than_appends(repo):
    """SUPERSEDES "duplicate Frame rows resolve last-writer-wins". There are no
    duplicates to resolve: the new state replaces the old one in place."""
    b = empty_bundle()
    b["LH"] = {"X": [1, 2, 3], "Y": [4, 5, 6], "Onset": "ON", "Bodypart": "LH",
               "Zones": [["A"], ["B"], ["C"]], "Touch": None}
    repo.save_frames({2: _dirty(b)}, total_frames=5)

    shrunk = empty_bundle()
    shrunk["LH"] = {"X": [9], "Y": [9], "Onset": "OFF", "Bodypart": "LH",
                    "Zones": [["Z"]], "Touch": None}
    repo.save_frames({2: _dirty(shrunk)}, total_frames=5)

    rec = repo.load_frames()[2]["LH"]
    assert rec["X"] == [9]                   # the 3 old clicks are GONE
    assert rec["Onset"] == "OFF"
    assert _row_counts(repo)["clicks"] == 1


def test_clearing_all_clicks_leaves_no_orphan_rows(repo):
    """Deleting the last click clears the record (annotation_service). Nothing
    may leak into the export afterwards — the FK cascade guarantees it."""
    b = empty_bundle()
    b["LH"] = {"X": [1], "Y": [2], "Onset": "ON", "Bodypart": "LH",
               "Zones": [["A"]], "Touch": None}
    repo.save_frames({2: _dirty(b)}, total_frames=5)

    repo.save_frames({2: _dirty(empty_bundle())}, total_frames=5)

    counts = _row_counts(repo)
    assert counts["clicks"] == 0
    assert counts["limb_records"] == 0
    assert counts["frames"] == 1             # the frame itself still exists


class _Unserializable:
    """Stands in for a mid-save failure: `json.dumps` refuses it, so the write
    of frame 3 below blows up AFTER frame 2's rows are already in the open
    transaction."""


def test_save_is_atomic_across_the_whole_dirty_set(tmp_path):
    """SUPERSEDES the journal's torn-tail repair.

    A failure mid-append used to leave a partial final row that the loader had
    to detect and repair. Now the ENTIRE save is one `BEGIN IMMEDIATE`, so a
    failure on any frame rolls back every frame in that save: a reader sees
    exactly the previously committed state, and there is no torn state to
    repair in the first place.
    """
    db = str(tmp_path / "state" / "vid.db")
    repo = SqliteRepository(db)
    try:
        good = empty_bundle()
        good["Note"] = "committed"
        repo.save_frames({1: _dirty(good)}, total_frames=5)
        before = repo.load_frames()

        fine = empty_bundle()
        fine["Note"] = "would have been fine"
        doomed = empty_bundle()
        doomed["LH"] = {"X": [1], "Y": [2], "Onset": "ON", "Bodypart": "LH",
                        "Zones": [[_Unserializable()]], "Touch": None}

        with pytest.raises(TypeError):
            repo.save_frames({2: _dirty(fine), 3: _dirty(doomed)}, total_frames=5)

        # Frame 2 was written BEFORE the failure and must still be rolled back.
        assert repo.load_frames() == before
    finally:
        repo.close()

    # And the rolled-back state is what a fresh connection sees on disk.
    reopened = SqliteRepository(db)
    try:
        assert set(reopened.load_frames()) == {1}
    finally:
        reopened.close()


def test_empty_save_still_records_total_frames(repo):
    """A save with nothing dirty must not fail and must keep the frame-count
    metadata current (Analysis reads it back)."""
    assert repo.save_frames({}, total_frames=321) == 0

    assert repo.get_meta("total_frames") == "321"


def test_frames_outside_total_frames_are_still_stored(repo):
    """The journal writer iterated `range(total_frames + 1)` and silently DROPPED
    any higher key. Dropping annotations is never the right default; the export
    ignores out-of-range frames either way, so storing them is strictly safer."""
    repo.save_frames({999: _dirty(empty_bundle())}, total_frames=10)

    assert set(repo.load_frames()) == {999}


# === schema / integrity =======================================================
def test_user_version_is_stamped(repo):
    assert repo._user_version() == SCHEMA_VERSION


def test_a_newer_schema_is_refused_not_downgraded(tmp_path):
    """Opening a DB from a FUTURE TinyTouch would drop its unknown columns on
    the next save. Fail loudly instead."""
    db = str(tmp_path / "state" / "vid.db")
    SqliteRepository(db).close()
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(SchemaVersionError):
        SqliteRepository(db)


def test_foreign_keys_cascade_on_frame_delete(repo):
    """`PRAGMA foreign_keys=ON` plus `ON DELETE CASCADE` is what makes a
    one-frame re-save a two-statement operation with no stale children."""
    b = empty_bundle()
    b["Params"] = {"Par1": "ON"}
    b["LH"] = {"X": [1], "Y": [2], "Onset": "ON", "Bodypart": "LH",
               "Zones": [["A"]], "Touch": None, "LimbParams": {"Par1": "ON"}}
    repo.save_frames({1: _dirty(b)}, total_frames=5)
    assert _row_counts(repo)["clicks"] == 1

    repo._conn.execute("DELETE FROM frames WHERE frame = 1")

    counts = _row_counts(repo)
    assert counts["frames"] == 0
    assert counts["frame_params"] == 0
    assert counts["limb_records"] == 0
    assert counts["clicks"] == 0
    assert counts["limb_params"] == 0


def test_journal_mode_is_delete_not_wal(repo):
    """WAL's `-wal` / `-shm` sidecars confuse OneDrive / Dropbox, and these
    project folders routinely live inside synced trees."""
    mode = repo._conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode.lower() == "delete"


def test_no_wal_sidecars_appear_on_disk(tmp_path):
    db = tmp_path / "state" / "vid.db"
    repo = SqliteRepository(str(db))
    try:
        repo.save_frames({1: _dirty(empty_bundle())}, total_frames=2)
    finally:
        repo.close()

    leftovers = sorted(p.name for p in db.parent.iterdir())
    assert leftovers == ["vid.db"]


def test_repository_refuses_off_thread_access(repo):
    """The connection is Tk-thread-bound by contract; the export worker only
    ever sees the deepcopy snapshot. An off-thread call must fail loudly."""
    out = {}

    def worker():
        try:
            repo.load_frames()
        except Exception as exc:  # noqa: BLE001 — we assert on it
            out["exc"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert isinstance(out.get("exc"), RuntimeError)
    assert "Tk thread" in str(out["exc"])


# === meta-backed sidecars =====================================================
def test_last_position_round_trips_and_clamps(repo):
    assert repo.read_last_position(total_frames=100) is None

    repo.save_last_position(frame=42, total_frames=100)

    assert repo.read_last_position(total_frames=100) == 42
    assert repo.read_last_position(total_frames=10) == 10   # clamped down
    assert repo.get_meta("total_frames") == "100"


def test_labeling_time_round_trips(repo):
    assert repo.load_labeling_time_seconds() == 0.0

    repo.save_labeling_time_seconds(4321.5)

    assert repo.load_labeling_time_seconds() == 4321.5


def test_labeling_time_survives_a_corrupt_meta_value(repo):
    repo.set_meta("labeling_time_seconds", "not a number")

    assert repo.load_labeling_time_seconds() == 0.0


def test_clothes_round_trip_and_full_replace(repo):
    repo.save_clothes([(2, 145.0, 287.0, "L"), (3, 206.0, 295.0, "K")], 1.0)
    assert repo.has_clothes() is True
    assert repo.load_clothes_rows() == [(2, 145.0, 287.0, "L"), (3, 206.0, 295.0, "K")]
    assert repo.clothes_diagram_scale() == 1.0

    # A second dialog save REPLACES the set (dots can be deleted).
    repo.save_clothes([(1, 10.0, 20.0, "A,B")], 0.5)

    assert repo.load_clothes_rows() == [(1, 10.0, 20.0, "A,B")]
    assert repo.clothes_diagram_scale() == 0.5


def test_clothes_zone_list_matches_the_retired_sidecar_reader(repo, tmp_path):
    """`export/<video>_metadata.json` is a frozen contract, so the DB's zone
    list must equal what `extract_zones_from_file` produced from the same dots:
    set-deduplicated, UNSORTED, and a multi-zone dot contributing its whole
    comma-joined string as ONE entry."""
    dots = [(2, 145.0, 287.0, "L"), (3, 206.0, 295.0, "K"),
            (4, 144.0, 367.0, "L"), (5, 212.0, 387.0, "A,B")]
    sidecar = tmp_path / "vid_clothes.txt"
    sidecar.write_text(
        "Coordinates and Zones for Clothing Items:\nDiagramScale: 1.0\n"
        + "".join(f"Dot ID {d}: X={x}, Y={y}, Zones={z}\n" for d, x, y, z in dots),
        encoding="utf-8",
    )
    repo.save_clothes(dots, 1.0)

    assert sorted(repo.clothes_zone_list()) == sorted(
        extract_zones_from_file(str(sidecar))
    )
    # "A,B" is ONE entry, not two zones (the frozen tokenization).
    assert "A,B" in repo.clothes_zone_list()


def test_clothes_zone_list_is_none_without_dots(repo):
    assert repo.clothes_zone_list() is None


def test_legacy_notes_are_quarantined_not_promoted(repo):
    """The notes sidecar was only ever a DISPLAY fallback (`video.notes`); its
    text has never reached `bundle["Note"]` nor the export. Promoting it now
    would silently add notes to published datasets."""
    with repo.transaction():
        repo.import_legacy_notes({7: "Dívá se", 9: "second"})

    assert repo.load_legacy_notes() == {7: "Dívá se", 9: "second"}
    assert repo.load_frames() == {}          # NOT turned into bundles


def test_legacy_limb_params_are_quarantined(repo):
    """The limb-parameters sidecar has had no app reader for versions; its rows
    are stored so nothing is lost, but they are not folded into LimbParams."""
    with repo.transaction():
        repo.import_legacy_limb_params([(4, "LH", "Par1", "ON"),
                                        (4, "LH", "Par2", None)])

    assert repo.load_legacy_limb_params() == [(4, "LH", "Par1", "ON"),
                                              (4, "LH", "Par2", None)]
    assert repo.load_frames() == {}


# === helpers ==================================================================
def _row_counts(repo):
    tables = ("frames", "frame_params", "limb_records", "clicks", "limb_params",
              "clothes_dots", "legacy_notes", "legacy_limb_params")
    return {
        t: repo._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in tables
    }
