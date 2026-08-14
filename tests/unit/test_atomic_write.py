"""
C1 regression guard — atomic writes.

A crash mid-save must NOT corrupt the source-of-truth file. Every live writer
routes its bytes through `atomic_io.atomic_write`, which streams into a sibling
`<path>.tmp`, fsyncs, then `os.replace`s it onto the destination. If the write
raises before the replace, the previous complete file is left untouched and the
corruption is confined to the discarded `.tmp`.

Black-box, tmp_path only — never touches the real Labeled_data/.

Run:  uv run pytest tests/ -k C1 -v
"""
import os

import pandas as pd
import pytest

from adapters.unified_repo import save_unified_dataset
from domain.model import empty_bundle


def _one_changed(frame):
    b = empty_bundle()
    b["Changed"] = True
    return {frame: b}


def test_C1_unified_save_preserves_prior_file_on_write_crash(tmp_path, monkeypatch):
    csv_path = str(tmp_path / "data" / "vid_unified.csv")

    # 1) baseline good save (real to_csv)
    save_unified_dataset(csv_path, total_frames=3, frames=_one_changed(1))
    good = open(csv_path, "rb").read()

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
        save_unified_dataset(csv_path, total_frames=3, frames=_one_changed(2))

    # RED (before): live file truncated to "CORRUPT".
    # GREEN (after): corruption confined to .tmp; original bytes intact.
    assert open(csv_path, "rb").read() == good
    assert not os.path.exists(csv_path + ".tmp")


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
