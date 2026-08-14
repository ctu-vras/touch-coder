"""
Per-video labeling-time accumulator: the writer/reader key mismatch bug, and
its migration into the state DB.

Before the fix, `_write_video_time` stored "Total Labeling Time (hours)" while
`_load_video_time` read "Total Labeling Time (seconds)", so the accumulator
silently reset to 0 on every app restart. The fixed sidecar stored SECONDS, and
the reader falls back to hours*3600 to migrate files written by the buggy
builds. The EXPORT metadata key stays "Total Labeling Time (hours)" (frozen by
the golden metadata test) — that contract is untouched.

The accumulator now lives in the state DB (`meta.labeling_time_seconds`), so
`state/<name>_metadata.json` has no writer any more. Every reader invariant
above still matters — that JSON is exactly what the migration has to consume —
so the tests below build the sidecar bytes directly instead of round-tripping
through the retired writer, and the round-trip invariant ("survives a restart")
is re-asserted on the DB, which is what performs it now.

Run:  uv run pytest tests/ -k labeling_time
"""
import json

from adapters.sqlite_repo import SqliteRepository
from service_layer.project_service import LabelingTimer, load_labeling_time_seconds


def _write_sidecar(tmp_path, payload):
    """Materialize a legacy `state/<name>_metadata.json` byte-for-byte."""
    path = tmp_path / "state" / "vid_metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# === the retired sidecar: reader invariants (the migration input) =============
def test_sidecar_seconds_are_read_back(tmp_path):
    """The regression: this read used to return 0.0 and wipe the accumulator."""
    path = _write_sidecar(tmp_path, {"Total Labeling Time (seconds)": 4321.5})

    assert load_labeling_time_seconds(path) == 4321.5


def test_legacy_hours_only_file_is_migrated(tmp_path):
    """Files written by the buggy version hold ONLY hours — read them as
    seconds instead of silently starting over at zero."""
    path = _write_sidecar(tmp_path, {"Total Labeling Time (hours)": 2.5})

    assert load_labeling_time_seconds(path) == 9000.0  # 2.5 h


def test_seconds_win_when_both_keys_are_present(tmp_path):
    path = _write_sidecar(tmp_path, {
        "Total Labeling Time (seconds)": 100.0,
        "Total Labeling Time (hours)": 99.0,
    })

    assert load_labeling_time_seconds(path) == 100.0


def test_missing_and_corrupt_files_start_at_zero(tmp_path):
    missing = str(tmp_path / "state" / "vid_metadata.json")
    assert load_labeling_time_seconds(missing) == 0.0

    corrupt = _write_sidecar(tmp_path, {})
    with open(corrupt, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert load_labeling_time_seconds(corrupt) == 0.0


# === the live store: the same round-trip, in the state DB =====================
def test_accumulator_survives_a_restart_in_the_state_db(tmp_path):
    """REPLACES the sidecar round-trip test. Same invariant — the total must
    still be there after the app is closed and reopened — on the store that
    holds it now."""
    db = str(tmp_path / "state" / "vid.db")

    first = SqliteRepository(db)
    try:
        first.save_labeling_time_seconds(4321.5)
    finally:
        first.close()

    second = SqliteRepository(db)
    try:
        assert second.load_labeling_time_seconds() == 4321.5
    finally:
        second.close()


def test_hours_are_export_only_and_never_stored(tmp_path):
    """Hours belong to `export/<name>_metadata.json` alone; the working store
    keeps seconds, so no rounding creeps into the accumulator."""
    repo = SqliteRepository(str(tmp_path / "state" / "vid.db"))
    try:
        repo.save_labeling_time_seconds(7200.0)

        assert repo.get_meta("labeling_time_seconds") == "7200.0"
        assert repo.get_meta("Total Labeling Time (hours)") is None
    finally:
        repo.close()


def test_timer_accumulates_across_sessions(tmp_path):
    """The accumulator adds each session to the stored total rather than
    replacing it — the bug's user-visible symptom was losing prior sessions."""
    repo = SqliteRepository(str(tmp_path / "state" / "vid.db"))
    try:
        repo.save_labeling_time_seconds(1000.0)

        timer = LabelingTimer()
        timer.start(repo)
        assert timer.total_s == 1000.0
        timer.finalize(repo)

        assert repo.load_labeling_time_seconds() >= 1000.0
        assert timer.session_start is None
    finally:
        repo.close()
