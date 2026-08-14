"""
H3 — incremental saves must not scale with the amount of already-persisted data.

H3 originally fixed a full-rewrite save: every Save re-serialized the WHOLE
store, so a long video got slower and slower to annotate. The fix made saves
append-only (changed frames only), which brought three mechanisms with it —
duplicate `Frame` rows resolved last-writer-wins, load-time compaction to bound
journal growth, and torn-final-row repair after a crash mid-append.

The store is now `state/<video>.db` (`adapters.sqlite_repo`), so the MECHANISMS
changed while every invariant they protected stayed. Each test below names the
old invariant it inherits and the SQLite property that now carries it:

  round-trip fidelity            -> unchanged (asserted here and, exhaustively,
                                    in test_sqlite_repo.py)
  "no full re-read on save"      -> unchanged: the save reads nothing and writes
                                    only the dirty frames' rows
  "duplicate rows, last wins"    -> replace-in-place: there are no duplicates
  "journal grows, then compacts"  -> idempotence: a second identical save leaves
                                    row counts AND contents unchanged, so there
                                    is no growth to compact
  "repair a torn final row"      -> atomicity: one BEGIN IMMEDIATE per save, so
                                    a crash can never leave a torn row

Run:  uv run pytest tests/ -k H3 -v
"""
import pytest

from adapters.sqlite_repo import SqliteRepository
from domain.model import empty_bundle


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepository(str(tmp_path / "state" / "touch.db"))
    yield r
    r.close()


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


def _row_counts(repo):
    return {
        table: repo._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("frames", "frame_params", "limb_records", "clicks")
    }


class _StatementLog:
    """Records every SQL statement the connection executes, so a test can prove
    what a save did and did NOT touch."""

    def __init__(self, repo):
        self.statements = []
        repo._conn.set_trace_callback(self.statements.append)
        self._repo = repo

    def stop(self):
        self._repo._conn.set_trace_callback(None)

    def count(self, prefix):
        return sum(1 for s in self.statements if s.strip().upper().startswith(prefix))


def test_H3_touch_roundtrip_fidelity(repo):
    """Inherited unchanged: a saved touch comes back exactly as annotated."""
    repo.save_frames({2: _touch_bundle("ON", note="roundtrip")}, total_frames=5)

    loaded = repo.load_frames()

    assert loaded[2]["LH"]["Onset"] == "ON"
    assert loaded[2]["LH"]["X"] == [101]
    assert loaded[2]["LH"]["Y"] == [202]
    assert loaded[2]["LH"]["Zones"] == [["FACE"]]
    assert loaded[2]["Params"] == {"Par1": "ON"}
    assert loaded[2]["Note"] == "roundtrip"


def test_H3_touch_save_does_not_read_or_rewrite_stored_frames(repo):
    """REPLACES "no full re-read on save" (which was pinned by making
    `pd.read_csv` throw).

    The invariant is identical — a save's cost must not depend on how much is
    already stored — but the proof is now direct: saving ONE dirty frame issues
    no SELECT at all, and touches no row belonging to another frame.
    """
    for frame in range(0, 40):
        repo.save_frames({frame: _touch_bundle("ON")}, total_frames=50)
    before = repo.load_frames()

    log = _StatementLog(repo)
    try:
        repo.save_frames({44: _touch_bundle("OFF")}, total_frames=50)
    finally:
        log.stop()

    assert log.count("SELECT") == 0, log.statements
    # Exactly one save's worth of work: BEGIN, the frame's DELETE + INSERTs,
    # the meta UPSERT, COMMIT — a constant, independent of the 40 stored frames.
    assert log.count("DELETE") == 1
    assert log.count("INSERT") == 5  # frames, frame_params, limb_records, clicks, meta
    # Nothing already stored changed.
    after = repo.load_frames()
    assert {f: after[f] for f in before} == before


def test_H3_touch_last_write_replaces_in_place(repo):
    """REPLACES "duplicate Frame rows resolve last-writer-wins".

    Same observable outcome (the newest save is what loads back), but there is
    no duplicate to resolve: the row is updated, not appended.
    """
    repo.save_frames({2: _touch_bundle("ON")}, total_frames=5)
    repo.save_frames({2: _touch_bundle("OFF")}, total_frames=5)

    assert repo.load_frames()[2]["LH"]["Onset"] == "OFF"
    assert _row_counts(repo)["frames"] == 1


def test_H3_touch_repeated_saves_do_not_grow_the_store(repo):
    """REPLACES "the journal grows, then compacts when rows > 2x frames".

    That invariant existed only to bound append-only growth. With an UPSERT
    there is no growth: three saves of one frame leave the same row counts as
    one save, so the property that supersedes it is stability, and a second
    IDENTICAL save is a pure no-op.
    """
    repo.save_frames({2: _touch_bundle("ON")}, total_frames=5)
    counts_after_one = _row_counts(repo)

    repo.save_frames({2: _touch_bundle("OFF")}, total_frames=5)
    repo.save_frames({2: _touch_bundle("ON")}, total_frames=5)

    assert _row_counts(repo) == counts_after_one
    assert repo.load_frames()[2]["LH"]["Onset"] == "ON"

    # ... and re-saving the SAME state again changes nothing at all.
    snapshot = repo.load_frames()
    repo.save_frames({2: _touch_bundle("ON")}, total_frames=5)
    assert _row_counts(repo) == counts_after_one
    assert repo.load_frames() == snapshot


def test_H3_touch_crash_mid_save_cannot_leave_a_torn_frame(tmp_path):
    """REPLACES "recover a crash-torn final append".

    The journal could be cut off mid-row, so the loader had to detect the
    fragment and repair the file. A save is now a single transaction, so the
    torn state is unrepresentable: the failed save leaves the previous commit
    untouched and needs no repair pass.
    """
    db = str(tmp_path / "state" / "touch.db")
    repo = SqliteRepository(db)
    try:
        repo.save_frames({1: _touch_bundle("ON")}, total_frames=5)

        doomed = _touch_bundle("OFF")
        doomed["LH"]["Zones"] = [[object()]]  # json.dumps refuses -> mid-save failure
        with pytest.raises(TypeError):
            repo.save_frames({4: doomed}, total_frames=5)
    finally:
        repo.close()

    reopened = SqliteRepository(db)
    try:
        loaded = reopened.load_frames()
        assert set(loaded) == {1}
        assert loaded[1]["LH"]["Onset"] == "ON"
    finally:
        reopened.close()
