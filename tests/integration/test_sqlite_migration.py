"""
Legacy working state -> `state/<video>.db`: the one-way door.

This is the highest-stakes code path in the SQLite move: it runs exactly once
per project, on real researcher data, and there is no second chance. So it is
asserted from the strongest possible angle —

    export produced FROM THE MIGRATED DB
        must be BYTE-IDENTICAL to
    export produced from what the OLD READERS returned for the same files

— because `export/<video>_export.csv` is a frozen contract that downstream
research pipelines parse with their own readers. Semantic equality of the store
is asserted too, but the byte comparison is what actually protects published
datasets.

Also pinned here:
  * every legacy source is CONSUMED and RENAMED `*.migrated`, never deleted;
  * the migration is idempotent — a second call is a no-op that touches nothing;
  * the recovery ladder is preserved in order (unified CSV -> export CSV ->
    per-limb CSVs), including duplicate journal rows resolving last-writer-wins
    and a crash-torn final journal row being tolerated;
  * the notes and limb-parameter sidecars are QUARANTINED, not promoted into
    the bundles — promoting them would silently change existing exports;
  * a failed import leaves NO database and renames NOTHING.

Everything runs under tmp_path. The owner's real `Labeled_data/` tree is never
read or written by this suite.

Run:  uv run pytest tests/integration/test_sqlite_migration.py -v
"""
import json
import os

import pytest

from adapters.export_writer import export_from_unified
from adapters.sqlite_repo import SqliteRepository
from adapters.unified_repo import (
    csv_to_dict,
    extract_zones_from_file,
    load_limb_parameters,
    load_notes_csv,
    load_unified_dataset,
)
from domain.model import empty_bundle
from domain.project import ProjectPaths
from service_layer import project_service, state_migration

VIDEO = "vid"
TOTAL_FRAMES = 12
FPS = 25.0


# === fixture builders =========================================================
def _paths(tmp_path):
    p = ProjectPaths(VIDEO, base_dir=str(tmp_path / "data"))
    os.makedirs(p.state_dir, exist_ok=True)
    os.makedirs(p.export_dir, exist_ok=True)
    return p


def _journal_row(frame, bundle):
    cells = [
        str(frame),
        "" if bundle.get("Note") is None else bundle["Note"],
        json.dumps(bundle.get("Params", {})),
        json.dumps(bundle["LH"]), json.dumps(bundle["RH"]),
        json.dumps(bundle["LL"]), json.dumps(bundle["RL"]),
    ]
    return ",".join('"' + c.replace('"', '""') + '"' for c in cells) + "\n"


def _write_journal(paths, rows):
    """`rows` is an ordered list of (frame, bundle) — duplicates ALLOWED and
    expected: that is exactly how the append-only journal recorded a re-edit."""
    with open(paths.unified_csv, "w", encoding="utf-8", newline="") as fh:
        fh.write("Frame,Note,Params,LH,RH,LL,RL\n")
        for frame, bundle in rows:
            fh.write(_journal_row(frame, bundle))


def _touch(limb, xs, ys, onset, zones, *, limb_params=None):
    rec = {"X": list(xs), "Y": list(ys), "Onset": onset, "Bodypart": limb,
           "Zones": zones, "Touch": None}
    if limb_params is not None:
        rec["LimbParams"] = limb_params
    return rec


def _session_rows():
    """A realistic journal: frame 3 is written twice (the researcher fixed the
    onset), plus params-only and note-only frames and a UTF-8 note."""
    first = empty_bundle()
    first["LH"] = _touch("LH", [10, 20], [30, 40], "ON",
                         [["FACE"], ["17L", "17R"]],
                         limb_params={"Par1": "ON", "Par2": None, "Par3": None})
    first["Params"] = {"Par1": "ON"}
    first["Note"] = "začátek ěšč, s čárkou"

    corrected = empty_bundle()
    corrected["LH"] = _touch("LH", [11], [31], "OFF", [["FACE"]])
    corrected["Note"] = "opraveno"

    offset = empty_bundle()
    offset["RH"] = _touch("RH", [300], [210], "OFF", [["BELLY"]],
                          limb_params={"Par1": "None", "Par2": "OFF", "Par3": None})

    params_only = empty_bundle()
    params_only["Params"] = {"Par1": "OFF", "Par2": "ON", "Par3": None}

    return [
        (3, first),          # first write of frame 3 ...
        (6, offset),
        (3, corrected),      # ... superseded by this one (last-writer-wins)
        (9, params_only),
    ]


def _write_notes(paths):
    with open(paths.notes_csv, "w", encoding="utf-8", newline="") as fh:
        fh.write("Frame,Note\r\n5,Dívá se\r\n8,another note\r\n")


def _write_limb_params(paths):
    with open(paths.limb_params_csv, "w", encoding="utf-8", newline="") as fh:
        fh.write("Limb,Frame,Parameter,State\r\n")
        fh.write("LH,4,Parameter_1,ON\r\nRL,4,Parameter_2,OFF\r\n")


def _write_clothes(paths, scale=1.0):
    with open(paths.clothes_txt, "w", encoding="utf-8") as fh:
        fh.write("Coordinates and Zones for Clothing Items:\n")
        fh.write(f"DiagramScale: {scale}\n")
        fh.write("Dot ID 2: X=145, Y=287, Zones=L\n")
        fh.write("Dot ID 3: X=206, Y=295, Zones=K\n")
        fh.write("Dot ID 4: X=144, Y=367, Zones=L\n")
        fh.write("Dot ID 5: X=212, Y=387, Zones=A,B\n")


def _write_last_position(paths, frame=7, total_frames=TOTAL_FRAMES):
    with open(paths.last_position_json, "w", encoding="utf-8") as fh:
        json.dump({"frame": frame, "total_frames": total_frames}, fh)


def _write_labeling_time(paths, hours=None, seconds=None):
    payload = {}
    if hours is not None:
        payload["Total Labeling Time (hours)"] = hours
    if seconds is not None:
        payload["Total Labeling Time (seconds)"] = seconds
    with open(paths.video_time_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _write_legacy_limb_csv(paths, limb):
    with open(paths.limb_csv(limb), "w", encoding="utf-8", newline="") as fh:
        fh.write("Frame,X,Y,Onset,Bodypart,Zones,Touch\r\n")
        fh.write(f'2,"55","66",ON,{limb},"[""FACE""]",\r\n')


def _full_legacy_project(tmp_path):
    """Every legacy state file at once, the worst case a real folder can be."""
    paths = _paths(tmp_path)
    _write_journal(paths, _session_rows())
    _write_notes(paths)
    _write_limb_params(paths)
    _write_clothes(paths)
    _write_last_position(paths)
    _write_labeling_time(paths, hours=0.0057)
    return paths


def _export_to(frames, out_csv):
    export_from_unified(
        frames, str(out_csv), program_version="7.8.0 (Windows)", video_name=VIDEO,
        labeling_mode="Normal", frame_rate=FPS, clothes_list=None,
        total_frames=TOTAL_FRAMES,
    )
    return out_csv


def _migrate(paths, **kwargs):
    repo, result = state_migration.migrate_state_to_sqlite(paths, **kwargs)
    return repo, result


# === THE guarantee ============================================================
def test_export_from_migrated_db_is_byte_identical_to_export_from_the_csvs(tmp_path):
    """The strongest available proof that the migration is lossless where it
    counts: both stores must serialize to the SAME export bytes.

    `export/<video>_export.csv` is the frozen contract downstream pipelines
    parse, so byte equality here means the move to SQLite is invisible to every
    consumer of published data.
    """
    paths = _full_legacy_project(tmp_path)

    # Baseline: what the OLD readers produce, exported.
    from_csvs = load_unified_dataset(paths.unified_csv)
    baseline = _export_to(from_csvs, tmp_path / "baseline_export.csv")

    repo, _ = _migrate(paths, fps=FPS)
    try:
        migrated = _export_to(repo.load_frames(), tmp_path / "migrated_export.csv")
    finally:
        repo.close()

    assert migrated.read_bytes() == baseline.read_bytes()


def test_migrated_store_is_semantically_equal_to_the_old_readers(tmp_path):
    """Field-for-field, not just export-equal: the in-memory store the app gets
    from the DB must equal the store it used to get from the journal."""
    paths = _full_legacy_project(tmp_path)
    from_csvs = load_unified_dataset(paths.unified_csv)

    repo, _ = _migrate(paths, fps=FPS)
    try:
        from_db = repo.load_frames()
    finally:
        repo.close()

    assert from_db == from_csvs


def test_only_look_is_dropped_and_bodypart_normalized(tmp_path):
    """The two — and only two — deltas between the journal store and the DB
    store, both deliberate:

      * the retired per-limb `Look` (see test_look_vestigial) is DROPPED. Old
        journal blobs still carry it verbatim because the loader replays raw
        JSON; it has no writer, no reader and no export column.
      * `Bodypart` is RECONSTRUCTED from the limb key, so records whose blob
        omitted it (real archived data does) gain the canonical value.

    Nothing else may differ: no key present in both may have a different value.
    """
    paths = _paths(tmp_path)
    with_look = empty_bundle()
    with_look["RH"] = {"X": [146], "Y": [90], "Onset": "ON", "Bodypart": "RH",
                       "Look": "No", "Zones": [["Z"]], "Touch": None}
    no_bodypart = empty_bundle()
    no_bodypart["LH"] = {"Onset": None, "Touch": None, "Zones": [], "X": [], "Y": []}
    no_bodypart["Note"] = "hi"
    _write_journal(paths, [(156, with_look), (244, no_bodypart)])
    from_csvs = load_unified_dataset(paths.unified_csv)
    assert from_csvs[156]["RH"]["Look"] == "No"        # the reader keeps it

    repo, _ = _migrate(paths)
    try:
        from_db = repo.load_frames()
    finally:
        repo.close()

    assert set(from_db) == set(from_csvs)
    for frame in from_csvs:
        for limb in ("LH", "RH", "LL", "RL"):
            source, stored = from_csvs[frame][limb], from_db[frame][limb]
            assert set(source) - set(stored) <= {"Look"}, (frame, limb)
            assert set(stored) - set(source) <= {"Bodypart"}, (frame, limb)
            shared = set(source) & set(stored) - {"Bodypart"}
            assert {k: source[k] for k in shared} == {k: stored[k] for k in shared}
            assert stored["Bodypart"] == limb
            assert "Look" not in stored
    # Onset=None survived as None, not as "".
    assert from_db[244]["LH"]["Onset"] is None


# === the recovery ladder ======================================================
def test_duplicate_journal_rows_resolve_last_writer_wins(tmp_path):
    """Frame 3 was written twice; the DB must hold the CORRECTED version only."""
    paths = _full_legacy_project(tmp_path)

    repo, result = _migrate(paths)
    try:
        loaded = repo.load_frames()
    finally:
        repo.close()

    assert loaded[3]["Note"] == "opraveno"
    assert loaded[3]["LH"]["Onset"] == "OFF"
    assert loaded[3]["LH"]["X"] == [11]
    assert set(loaded) == {3, 6, 9}
    assert result.frames_imported == 3


def test_crash_torn_final_journal_row_is_tolerated(tmp_path):
    """An older build killed mid-append left a partial last line. The migration
    reader still repairs that (SQLite cannot produce it, but archived journals
    already have it), and the torn fragment must not become a frame."""
    paths = _paths(tmp_path)
    _write_journal(paths, _session_rows())
    with open(paths.unified_csv, "ab") as fh:
        fh.write(b'4,"unterminated')

    repo, _ = _migrate(paths)
    try:
        loaded = repo.load_frames()
    finally:
        repo.close()

    assert set(loaded) == {3, 6, 9}
    assert 4 not in loaded


def test_recovery_from_the_export_csv_when_the_journal_is_missing(tmp_path):
    """Ladder tier 2, unchanged: no journal but an export on disk means the
    export is re-imported instead of starting empty."""
    paths = _paths(tmp_path)
    source = {}
    b = empty_bundle()
    b["RH"] = _touch("RH", [7], [8], "ON", [["FACE"]])
    source[4] = b
    _export_to(source, paths.export_csv)
    _write_last_position(paths)

    repo, _ = _migrate(paths)
    try:
        loaded = repo.load_frames()
    finally:
        repo.close()

    assert loaded[4]["RH"]["X"] == [7]
    assert loaded[4]["RH"]["Onset"] == "ON"
    assert loaded[4]["RH"]["Zones"] == [["FACE"]]


def test_recovery_from_legacy_per_limb_csvs(tmp_path):
    """Ladder tier 3, unchanged: with no journal and no export, the per-limb
    CSVs are merged, and the DB must hold what `csv_to_dict` produced."""
    paths = _paths(tmp_path)
    for limb in ("RH", "LH", "RL", "LL"):
        _write_legacy_limb_csv(paths, limb)

    expected = {limb: csv_to_dict(paths.limb_csv(limb)) for limb in
                ("RH", "LH", "RL", "LL")}

    repo, result = _migrate(paths)
    try:
        loaded = repo.load_frames()
    finally:
        repo.close()

    assert set(loaded) == {2}
    for limb in ("RH", "LH", "RL", "LL"):
        source = expected[limb][2]
        rec = loaded[2][limb]
        assert rec["X"] == source["X"]
        assert rec["Y"] == source["Y"]
        assert rec["Onset"] == source["Onset"]
        # Flat legacy zone shape (list[str], not list-of-lists) survives as-is.
        assert rec["Zones"] == source["Zones"]
    assert result.frames_imported == 1


# === sidecars =================================================================
def test_clothes_dots_and_scale_match_the_sidecar_readers(tmp_path):
    """Clothes feed the export metadata's frozen "Zones Covered With Clothes"
    list AND the dialog's initial dot positions — both must be unchanged."""
    paths = _full_legacy_project(tmp_path)
    expected_zones = extract_zones_from_file(paths.clothes_txt)
    expected_points = project_service.load_clothes_points(paths.clothes_txt, 1.0, 0.5)
    # The legacy "does the Clothes button light up?" check, for comparison.
    assert project_service.clothes_file_has_data(paths.clothes_txt) is True

    repo, result = _migrate(paths)
    try:
        assert result.clothes_dots == 4
        assert repo.has_clothes() is True
        assert repo.clothes_diagram_scale() == 1.0
        # Same zone SET (unsorted by contract on both sides).
        assert sorted(repo.clothes_zone_list()) == sorted(expected_zones)
        # A multi-zone dot stays ONE comma-joined entry (frozen tokenization).
        assert "A,B" in repo.clothes_zone_list()
        # Same rescaled dialog points.
        assert project_service.load_clothes_points_from_repo(repo, 1.0, 0.5) == \
            expected_points
    finally:
        repo.close()


def test_last_position_and_labeling_time_move_into_meta(tmp_path):
    paths = _full_legacy_project(tmp_path)
    expected_frame = project_service.read_last_position(paths, TOTAL_FRAMES)
    expected_seconds = project_service.load_labeling_time_seconds(paths.video_time_json)

    repo, _ = _migrate(paths)
    try:
        assert repo.read_last_position(TOTAL_FRAMES) == expected_frame == 7
        # The hours-only sidecar is converted on the way in (0.0057 h).
        assert repo.load_labeling_time_seconds() == pytest.approx(expected_seconds)
        assert repo.get_meta("total_frames") == str(TOTAL_FRAMES)
        assert repo.get_meta("video_name") == VIDEO
    finally:
        repo.close()


def test_notes_and_limb_param_sidecars_are_quarantined_not_promoted(tmp_path):
    """Neither sidecar has ever reached the export: the notes CSV was a display
    fallback (`video.notes`) and the limb-parameters CSV had no reader at all.
    They must land in the DB WITHOUT changing any bundle, or every existing
    project's export would shift on first open."""
    paths = _full_legacy_project(tmp_path)
    expected_notes = load_notes_csv(paths.notes_csv)
    expected_states = load_limb_parameters(paths.limb_params_csv)

    repo, result = _migrate(paths)
    try:
        # Preserved, byte-for-byte, in their own table.
        assert repo.load_legacy_notes() == expected_notes
        assert result.notes_applied == len(expected_notes)
        stored = repo.load_legacy_limb_params()
        assert ("Par1", "ON") in [(k, s) for _, _, k, s in stored]
        assert result.limb_params_applied == sum(len(d) for d in expected_states)

        loaded = repo.load_frames()
        # NOT promoted: no bundle was created, no LimbParams was invented.
        assert set(loaded) == {3, 6, 9}
        assert 5 not in loaded and 8 not in loaded and 4 not in loaded
        assert loaded[3]["Note"] == "opraveno"
    finally:
        repo.close()


# === file handling ============================================================
def test_every_consumed_source_is_renamed_never_deleted(tmp_path):
    paths = _full_legacy_project(tmp_path)
    sources = state_migration.legacy_state_sources(paths)
    assert len(sources) == 6

    repo, result = _migrate(paths)
    repo.close()

    assert sorted(result.sources_migrated) == sorted(sources)
    assert result.sources_skipped == []
    for source in sources:
        assert not os.path.exists(source), f"{source} still at its old name"
        assert os.path.exists(source + ".migrated"), f"{source}.migrated missing"
    assert os.path.exists(paths.state_db)


def test_the_export_csv_is_never_renamed(tmp_path):
    """The export is a published artifact and a recovery input, not state."""
    paths = _paths(tmp_path)
    _write_journal(paths, _session_rows())
    _export_to({}, paths.export_csv)

    repo, _ = _migrate(paths)
    repo.close()

    assert os.path.exists(paths.export_csv)
    assert not os.path.exists(paths.export_csv + ".migrated")


def test_migration_is_idempotent(tmp_path):
    paths = _full_legacy_project(tmp_path)

    first_repo, first = _migrate(paths, fps=FPS)
    try:
        before = first_repo.load_frames()
    finally:
        first_repo.close()
    listing_before = sorted(os.listdir(paths.state_dir))

    second_repo, second = _migrate(paths, fps=FPS)
    try:
        after = second_repo.load_frames()
    finally:
        second_repo.close()

    assert first.already_migrated is False
    assert second.already_migrated is True
    assert second.sources_migrated == []
    assert second.frames_imported == 0
    assert after == before
    assert sorted(os.listdir(paths.state_dir)) == listing_before


def test_a_project_with_no_legacy_state_gets_an_empty_db(tmp_path):
    paths = _paths(tmp_path)

    repo, result = _migrate(paths)
    try:
        assert result.created_empty is True
        assert result.sources_migrated == []
        assert repo.load_frames() == {}
        assert repo.frame_count() == 0
    finally:
        repo.close()

    assert os.path.exists(paths.state_db)


def test_a_failed_import_leaves_no_db_and_renames_nothing(tmp_path, monkeypatch):
    """The whole import is one transaction: if anything raises, the partial DB
    file is discarded and every source stays exactly where it was, so the next
    attempt starts from the same inputs."""
    paths = _full_legacy_project(tmp_path)
    sources = state_migration.legacy_state_sources(paths)

    def boom(self, rows):
        raise RuntimeError("simulated failure mid-import")

    monkeypatch.setattr(SqliteRepository, "import_clothes", boom)

    with pytest.raises(RuntimeError):
        state_migration.migrate_state_to_sqlite(paths)

    assert not os.path.exists(paths.state_db)
    for source in sources:
        assert os.path.exists(source), f"{source} was renamed despite the failure"
        assert not os.path.exists(source + ".migrated")


def test_a_rename_collision_leaves_both_copies(tmp_path):
    """Mirrors `migration_service`'s directory-collision policy: warn and keep
    BOTH files rather than overwrite or delete anything."""
    paths = _full_legacy_project(tmp_path)
    with open(paths.unified_csv + ".migrated", "w", encoding="utf-8") as fh:
        fh.write("an earlier migration's copy\n")

    repo, result = _migrate(paths)
    repo.close()

    assert os.path.exists(paths.unified_csv)                 # left in place
    assert os.path.exists(paths.unified_csv + ".migrated")   # not overwritten
    assert [p for p, _ in result.sources_skipped] == [paths.unified_csv]
    with open(paths.unified_csv + ".migrated", encoding="utf-8") as fh:
        assert fh.read() == "an earlier migration's copy\n"


def test_second_open_reads_the_db_and_ignores_stale_sources(tmp_path):
    """Once the DB exists it WINS. A legacy file that reappears (a restored
    backup, a sync client) must not silently re-import over newer work."""
    paths = _full_legacy_project(tmp_path)
    repo, _ = _migrate(paths)
    try:
        newer = empty_bundle()
        newer["Note"] = "typed after the migration"
        newer["Changed"] = True
        repo.save_frames({11: newer}, TOTAL_FRAMES)
    finally:
        repo.close()

    # A stale journal turns up again under its original name.
    _write_journal(paths, _session_rows())

    repo, result = _migrate(paths)
    try:
        loaded = repo.load_frames()
    finally:
        repo.close()

    assert result.already_migrated is True
    assert loaded[11]["Note"] == "typed after the migration"
    assert os.path.exists(paths.unified_csv)  # untouched, not consumed
