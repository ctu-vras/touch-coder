"""
Per-video labeling-time accumulator, stored in the state DB as
`meta.labeling_time_seconds`.

History this still guards: `_write_video_time` once stored "Total Labeling Time
(hours)" while `_load_video_time` read "Total Labeling Time (seconds)", so the
accumulator silently reset to 0 on every app restart. The store keeps SECONDS;
the EXPORT metadata key stays "Total Labeling Time (hours)" (frozen by the
golden metadata test) and is derived at export time.

The tests that read the retired `state/<name>_metadata.json` sidecar went with
the migration in 9.0. The invariants that survive it — survives a restart,
hours never enter the store, sessions accumulate — are asserted below on the
store that holds the value now.

Run:  uv run pytest tests/ -k labeling_time
"""
from adapters.sqlite_repo import SqliteRepository
from service_layer.project_service import LabelingTimer


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
