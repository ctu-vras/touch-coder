"""
C4 regression guard — `save_pose_dataset` must never silently drop previously
saved rows when the existing 3D unified CSV cannot be parsed.

The incremental (changed-only) pose save re-reads the on-disk unified CSV to
merge prior frames with this session's changed frames. If that read fails for a
genuine reason (e.g. a truncated file from an interrupted write), the writer must
NOT overwrite the live file with only the current session's rows — that would
destroy all history. Instead it logs loudly and raises `PoseUnifiedReadError`,
leaving the on-disk file byte-for-byte untouched so a retry can re-persist the
still-dirty frames.
"""
import pytest

import pose_mismatch_data
from pose_mismatch_data import (
    PoseUnifiedReadError,
    empty_pose_bundle,
    save_pose_dataset,
)


def _changed_bundle():
    b = empty_pose_bundle()
    b["Changed"] = True
    return b


def test_C4_pose_save_preserves_prior_rows_on_read_error(tmp_path, monkeypatch, capsys):
    csv_path = str(tmp_path / "vid_3d_unified.csv")

    # (1) Seed a valid existing unified pose CSV. On a fresh path the file does
    # not exist yet, so this first save never calls read_csv.
    seed_frames = {i: _changed_bundle() for i in (0, 1, 2)}
    save_pose_dataset(csv_path, total_frames=2, frames=seed_frames)

    import pandas as pd  # local import so the monkeypatch below cannot affect it

    assert len(pd.read_csv(csv_path)) == 3, "seed save should have written 3 data rows"

    # (2) Snapshot the exact bytes on disk.
    before = (tmp_path / "vid_3d_unified.csv").read_bytes()

    # (3) Force a genuine parse failure deterministically.
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pose_mismatch_data.pd, "read_csv", _boom)

    # A new session with a single, different changed frame.
    new_frames = {5: _changed_bundle()}

    # (4) The read failure must be signalled (not swallowed) AND the prior file
    # must be left byte-for-byte untouched (not overwritten with the 1-row set).
    with pytest.raises(PoseUnifiedReadError):
        save_pose_dataset(csv_path, total_frames=5, frames=new_frames)

    after = (tmp_path / "vid_3d_unified.csv").read_bytes()
    assert after == before, "prior rows must not be overwritten on a read error"

    # (5) Rule-0 observability: the failure is logged with the path.
    out = capsys.readouterr().out
    assert "ERROR" in out and csv_path in out
