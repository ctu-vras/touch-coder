"""
save_service Unit-of-Work concurrency invariant.

The save pipeline deep-copies `frames` BEFORE running the export on a worker
thread, then clears `Changed` ONLY for bundles still equal to that snapshot.
A frame the user edits WHILE the export is running must therefore stay dirty
so the next save picks it up — otherwise edits are silently lost.

Run:  uv run pytest tests/ -k save_service
"""
from domain.model import empty_bundle
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
