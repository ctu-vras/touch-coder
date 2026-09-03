"""
H5 — frame extraction must SIGNAL failure instead of silently yielding an
empty frames/ folder.

Black-box against `frame_utils`, deterministic (no real codec/ffmpeg): the
ffmpeg path is forced off and OpenCV's VideoCapture is faked so both extractors
produce zero frames. `create_frames` must raise `FrameExtractionError` (and, on
success, return the produced frame count) rather than returning `None`.
"""
import os

import pytest

from adapters import frame_extractor as frame_utils


class _FakeCapture:
    """Stand-in for cv2.VideoCapture that fails to open the container, so the
    OpenCV fallback writes zero frames (the H5 silent-failure scenario)."""

    def __init__(self, *_args, **_kwargs):
        pass

    def isOpened(self):
        return False

    def get(self, _prop):
        return 0

    def read(self):
        return False, None

    def release(self):
        pass


def test_H5_zero_frames_raises(tmp_path, monkeypatch):
    frames_dir = os.path.join(str(tmp_path), "frames")

    # Force the OpenCV fallback, then make the capture fail to open so it
    # writes nothing — the empty-folder case that used to pass silently.
    monkeypatch.setattr(frame_utils, "_extract_frames_ffmpeg", lambda *a, **k: False)
    monkeypatch.setattr(frame_utils.cv2, "VideoCapture", _FakeCapture)

    with pytest.raises(frame_utils.FrameExtractionError):
        frame_utils.create_frames(
            os.path.join(str(tmp_path), "bogus.mp4"),
            frames_dir,
            "Normal",
            "vid",
        )

    produced = [f for f in os.listdir(frames_dir) if f.endswith(".jpg")] if os.path.exists(frames_dir) else []
    assert produced == []


def test_H5_returns_count_on_success(tmp_path, monkeypatch, make_frame_jpgs):
    frames_dir = os.path.join(str(tmp_path), "frames")

    def _fake_ffmpeg(video_path, out_dir, progress_cb, progress_interval_s):
        make_frame_jpgs(out_dir, 3)  # frame0.jpg, frame1.jpg, frame2.jpg
        return True

    monkeypatch.setattr(frame_utils, "_extract_frames_ffmpeg", _fake_ffmpeg)

    count = frame_utils.create_frames(
        os.path.join(str(tmp_path), "ok.mp4"),
        frames_dir,
        "Normal",
        "vid",
    )
    assert count == 3
