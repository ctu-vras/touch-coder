"""
C1 regression guard — a crash mid-save must NOT corrupt the file it is saving.

Before C1 every writer serialized straight onto the live destination file
(`open(path, "w")`, then stream), so a kill / disk-full / OS error mid-write
left the file truncated. The fix routes file writes through
`atomic_io.atomic_write`: stream into a sibling `<path>.tmp`, fsync, then
`os.replace` it onto the destination. A reader therefore only ever sees the
previous complete file or the new complete file.

The invariant is unchanged; the files it protects moved. The unified-CSV journal
this test used to exercise is gone — working state now lives in
`state/<video>.db` — so the same guarantee is asserted on both survivors:

  * `export/<video>_export.csv`, the last big `atomic_write` consumer and the
    published research artifact (`test_C1_export_...`);
  * the state DB, where a failed save rolls its transaction back instead
    (`test_C1_state_db_...`) — the SQLite property that supersedes
    write-temp-then-rename for working state.

Black-box, tmp_path only — never touches the real data/ tree.

Run:  uv run pytest tests/ -k C1 -v
"""
import os

import pandas as pd
import pytest

from adapters.export_writer import export_from_unified
from adapters.sqlite_repo import SqliteRepository
from domain.model import empty_bundle


def _one_changed(frame, note="v1"):
    b = empty_bundle()
    b["Note"] = note
    b["Changed"] = True
    return {frame: b}


def _export(out_csv, frames):
    export_from_unified(
        frames, out_csv, program_version=8.0, video_name="vid",
        labeling_mode="Normal", frame_rate=25.0, clothes_list=None,
        total_frames=3,
    )


def test_C1_export_preserves_prior_file_on_write_crash(tmp_path, monkeypatch):
    """The export CSV is rewritten from scratch on every save, so a mid-write
    crash is exactly when a published dataset would be destroyed."""
    out_csv = str(tmp_path / "export" / "vid_export.csv")

    # 1) baseline good save (real to_csv)
    _export(out_csv, _one_changed(1))
    good = open(out_csv, "rb").read()

    # 2) inject a write that corrupts its target then crashes
    def boom(self, path_or_buf=None, *a, **k):
        target = path_or_buf
        if hasattr(target, "write"):
            target.write("CORRUPT")             # -> lands in .tmp under atomic code
        else:
            open(target, "w").write("CORRUPT")  # -> corrupts live file under old code
        raise IOError("simulated disk-full mid-write")

    monkeypatch.setattr(pd.DataFrame, "to_csv", boom)

    with pytest.raises(IOError):
        _export(out_csv, _one_changed(2))

    # RED (before): live file truncated to "CORRUPT".
    # GREEN (after): corruption confined to .tmp; original bytes intact.
    assert open(out_csv, "rb").read() == good
    assert not os.path.exists(out_csv + ".tmp")


def test_C1_state_db_preserves_prior_state_on_save_crash(tmp_path):
    """The working-state equivalent. SQLite does not need write-temp-then-rename:
    a save is ONE `BEGIN IMMEDIATE`, so a failure rolls back and the last
    committed state is what the next reader (and the next process) sees."""
    db = str(tmp_path / "state" / "vid.db")
    repo = SqliteRepository(db)
    try:
        repo.save_frames(_one_changed(1, "committed"), total_frames=3)
        good = repo.load_frames()

        doomed = _one_changed(2, "must not land")
        # json.dumps refuses this bucket, failing the save AFTER the frame row
        # was already inserted into the open transaction.
        doomed[2]["LH"] = {"X": [1], "Y": [2], "Onset": "ON", "Bodypart": "LH",
                           "Zones": [[object()]], "Touch": None}

        with pytest.raises(TypeError):
            repo.save_frames(doomed, total_frames=3)

        assert repo.load_frames() == good
    finally:
        repo.close()

    reopened = SqliteRepository(db)
    try:
        assert reopened.load_frames() == good
    finally:
        reopened.close()
    # No stray rollback journal left behind.
    assert not os.path.exists(db + "-journal")


def test_C1_atomic_write_rolls_back_on_exception(tmp_path):
    from adapters.atomic_io import atomic_write

    path = str(tmp_path / "sub" / "file.txt")

    # pre-existing good content
    atomic_write(path, lambda f: f.write("ORIGINAL"))
    assert open(path, "r", encoding="utf-8").read() == "ORIGINAL"

    # a raising write_fn must leave the original untouched and drop the temp
    def boom(f):
        f.write("HALF")
        raise ValueError("boom mid-write")

    with pytest.raises(ValueError):
        atomic_write(path, boom)

    assert open(path, "r", encoding="utf-8").read() == "ORIGINAL"
    assert not os.path.exists(path + ".tmp")
