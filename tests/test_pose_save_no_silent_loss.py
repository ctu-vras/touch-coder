"""C4 guard: incremental pose saves must preserve every prior row.

H3 removed the risky read/merge/rewrite path. The writer now appends dirty
rows, so an unavailable reader cannot cause saved history to be replaced.
"""

import pose_mismatch_data
from pose_mismatch_data import empty_pose_bundle, load_pose_dataset, save_pose_dataset


def _changed_bundle():
    bundle = empty_pose_bundle()
    bundle["Changed"] = True
    return bundle


def test_C4_pose_save_preserves_prior_rows_without_reread(tmp_path, monkeypatch):
    csv_path = str(tmp_path / "vid_3d_unified.csv")

    seed_frames = {i: _changed_bundle() for i in (0, 1, 2)}
    save_pose_dataset(csv_path, total_frames=2, frames=seed_frames)

    import pandas as pd

    assert len(pd.read_csv(csv_path)) == 3
    before = (tmp_path / "vid_3d_unified.csv").read_bytes()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pose_mismatch_data.pd, "read_csv", _boom)
    save_pose_dataset(csv_path, total_frames=5, frames={5: _changed_bundle()})

    after = (tmp_path / "vid_3d_unified.csv").read_bytes()
    assert after.startswith(before), "prior rows must not be overwritten"
    assert len(after) > len(before), "the changed frame should be appended"

    monkeypatch.undo()
    assert set(load_pose_dataset(csv_path)) == {0, 1, 2, 5}
