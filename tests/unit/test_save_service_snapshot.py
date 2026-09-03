"""
save_service Unit-of-Work: the concurrency invariant and the step wiring.

The save pipeline is:

    1. persist_state(repo, ...)   dirty frames -> state DB, one transaction
    2. snapshot = build_save_snapshot(frames)      (deep copy)
    3. run_export(snapshot, ...)  metadata sidecar + export CSV, WORKER thread
    4. clear_clean_flags(frames, snapshot)

Concurrency invariant: the snapshot is deep-copied BEFORE the worker-thread
export, and `Changed` is then cleared ONLY for bundles still equal to that
snapshot. A frame the user edits WHILE the export is running must therefore
stay dirty so the next save picks it up — otherwise edits are silently lost.

Run:  uv run pytest tests/ -k save_service
"""
import json
import os

from adapters.sqlite_repo import SqliteRepository
from domain.model import empty_bundle
from domain.project import ProjectPaths
from service_layer import save_service


def _dirty_bundle(note):
    b = empty_bundle()
    b["Note"] = note
    b["Changed"] = True
    return b


def test_snapshot_is_a_deep_copy():
    frames = {1: _dirty_bundle("original")}

    snapshot = save_service.build_save_snapshot(frames)

    assert snapshot == frames
    assert snapshot[1] is not frames[1]
    frames[1]["LH"]["X"].append(5)
    assert snapshot[1]["LH"]["X"] == []  # nested structures copied too


def test_unedited_frames_are_marked_clean():
    frames = {1: _dirty_bundle("saved"), 2: _dirty_bundle("also saved")}
    snapshot = save_service.build_save_snapshot(frames)

    save_service.clear_clean_flags(frames, snapshot)

    assert frames[1]["Changed"] is False
    assert frames[2]["Changed"] is False


def test_frame_edited_during_export_stays_dirty():
    frames = {1: _dirty_bundle("saved"), 2: _dirty_bundle("saved")}
    snapshot = save_service.build_save_snapshot(frames)

    # ... the export runs on the worker thread while the user edits frame 2.
    frames[2]["Note"] = "typed while exporting"

    save_service.clear_clean_flags(frames, snapshot)

    assert frames[1]["Changed"] is False   # exported unchanged -> clean
    assert frames[2]["Changed"] is True    # NOT in the export -> still dirty


def test_frame_created_during_export_stays_dirty():
    frames = {1: _dirty_bundle("saved")}
    snapshot = save_service.build_save_snapshot(frames)

    frames[9] = _dirty_bundle("brand new")  # absent from the snapshot

    save_service.clear_clean_flags(frames, snapshot)

    assert frames[1]["Changed"] is False
    assert frames[9]["Changed"] is True


# === the full four-step pipeline ==============================================
def test_full_save_pipeline_writes_state_and_export(tmp_path):
    """Runs all four steps in order against real files, so a broken seam
    between them (a missing import, a renamed argument) fails here instead of
    only in the GUI. Step 3 runs on the caller's thread here; in the app the
    GUI hands it to a worker, which is safe precisely because it touches the
    snapshot and the export writer only — never the repository."""
    paths = ProjectPaths("vid", base_dir=str(tmp_path / "data"))
    os.makedirs(paths.state_dir, exist_ok=True)
    frames = {2: _dirty_bundle("note on two")}
    metadata = save_service.MetadataInputs(
        program_version="8.0.0 (test)",
        video_name="vid",
        labeling_mode="Normal",
        clothes_list=None,
        param_labels={"Parameter_1": "Looking1"},
        limb_param_labels=None,
        labeling_time_seconds=3600.0,
    )

    repo = SqliteRepository(paths.state_db)
    try:
        assert save_service.persist_state(repo, 5, frames) == 1
        snapshot = save_service.build_save_snapshot(frames)
        save_service.run_export(snapshot, paths, 25.0, metadata, total_frames=5)
        save_service.clear_clean_flags(frames, snapshot)

        assert repo.load_frames()[2]["Note"] == "note on two"
    finally:
        repo.close()

    assert frames[2]["Changed"] is False
    assert os.path.exists(paths.export_csv)
    meta = json.load(open(paths.export_metadata, encoding="utf-8"))
    assert meta["Video Name"] == "vid"
    assert meta["Total Labeling Time (hours)"] == 1.0


def test_clothes_zones_for_metadata_come_from_the_repo(tmp_path):
    """The export metadata's "Zones Covered With Clothes" now reads the state
    DB instead of the retired clothes sidecar."""
    paths = ProjectPaths("vid", base_dir=str(tmp_path / "data"))
    repo = SqliteRepository(paths.state_db)
    try:
        assert save_service.load_clothes_zones(None) is None
        assert save_service.load_clothes_zones(repo) is None

        repo.save_clothes([(1, 10.0, 20.0, "L"), (2, 30.0, 40.0, "L")], 1.0)

        assert save_service.load_clothes_zones(repo) == ["L"]
    finally:
        repo.close()
