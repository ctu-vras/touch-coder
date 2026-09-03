"""
Shared pytest setup for TinyTouch.

The app's `src/` modules import each other with `src/` as the import root
(`from domain.model import ...`, `from service_layer import ...`), so tests must
have `src/` on sys.path. We add it here rather than requiring an editable install.

Tests are black-box against the pure-function core (I/O, parsing, data model).
They must NEVER read or write the real `data/` tree — build inputs under
the `tmp_path` fixture instead.
"""
import os
import sys

import pytest

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _write_frame_jpgs(frames_dir, count):
    """Create `count` placeholder frame{n}.jpg files (content irrelevant —
    sort/copy code only cares about names)."""
    os.makedirs(frames_dir, exist_ok=True)
    for i in range(count):
        with open(os.path.join(frames_dir, f"frame{i}.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8\xff\xd9")  # minimal JPEG SOI+EOI marker
    return frames_dir


@pytest.fixture
def make_frame_jpgs():
    """Factory: make_frame_jpgs(dir, count) -> dir of placeholder frame JPGs."""
    return _write_frame_jpgs


@pytest.fixture
def one_touch_frames():
    """A touch-mode `frames` dict with a single LH touch: ON at frame 2
    (zone FACE), OFF at frame 5. Zones are stored list-of-lists, one bucket
    per click — the exact shape the exporter and Sort Frames must handle."""
    from domain.model import empty_bundle

    frames = {}

    on = empty_bundle()
    on["LH"] = {
        "X": [100], "Y": [100], "Onset": "ON", "Bodypart": "LH",
        "Look": "No", "Zones": [["FACE"]], "Touch": None,
    }
    on["Changed"] = True
    frames[2] = on

    off = empty_bundle()
    off["LH"] = {
        "X": [100], "Y": [100], "Onset": "OFF", "Bodypart": "LH",
        "Look": "No", "Zones": [["FACE"]], "Touch": None,
    }
    off["Changed"] = True
    frames[5] = off

    return frames
