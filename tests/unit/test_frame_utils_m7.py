"""M7 regression guards for frame extraction cleanup and scalability."""

import io
import os

import pytest

from adapters import frame_extractor as frame_utils
from domain.project import DATA_DIR, FRAMES_SUBDIR


def _reliability_source(tmp_path, video_name="vid"):
    # Mirrors ProjectPaths(...).original.frames_dir, which create_frames derives
    # relative to the cwd the tests chdir into.
    source = tmp_path / DATA_DIR / video_name / FRAMES_SUBDIR
    source.mkdir(parents=True)
    return source


def test_M7_reliability_copy_filters_non_frames(tmp_path, monkeypatch, make_frame_jpgs, capsys):
    source = _reliability_source(tmp_path)
    make_frame_jpgs(str(source), 5)
    (source / "Thumbs.db").write_bytes(b"metadata")
    (source / "notes.txt").write_text("not a frame", encoding="utf-8")
    destination = tmp_path / "copied"
    monkeypatch.chdir(tmp_path)

    count = frame_utils.create_frames(
        "unused.mp4", str(destination), "Reliability", "vid_reliability"
    )

    assert count == 5
    assert sorted(os.listdir(destination)) == [f"frame{i}.jpg" for i in range(5)]
    output = capsys.readouterr().out
    assert "WARN" in output and "skipped" in output.lower()


def test_M7_reliability_nonframes_only_raises(tmp_path, monkeypatch):
    source = _reliability_source(tmp_path)
    (source / "Thumbs.db").write_bytes(b"metadata")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(frame_utils.FrameExtractionError):
        frame_utils.create_frames(
            "unused.mp4", str(tmp_path / "copied"), "Reliability", "vid_reliability"
        )


def test_M7_reliability_progress_throttled(tmp_path, monkeypatch, make_frame_jpgs):
    source = _reliability_source(tmp_path)
    make_frame_jpgs(str(source), 100)
    monkeypatch.chdir(tmp_path)
    clock = iter([0.0] + [0.1] * 99 + [1.1, 1.2])
    monkeypatch.setattr(frame_utils.time, "time", lambda: next(clock, 1.2))
    calls = []

    frame_utils.create_frames(
        "unused.mp4",
        str(tmp_path / "copied"),
        "Reliability",
        "vid_reliability",
        progress_cb=lambda *args: calls.append(args),
        progress_interval_s=1.0,
    )

    assert len(calls) < 10
    assert calls[-1][:2] == (100, 100)


def test_M7_sequential_probe(tmp_path, make_frame_jpgs):
    make_frame_jpgs(str(tmp_path), 5)
    assert frame_utils._advance_sequential_count(str(tmp_path), 0) == 5
    make_frame_jpgs(str(tmp_path), 10)
    assert frame_utils._advance_sequential_count(str(tmp_path), 5) == 10
    (tmp_path / "frame11.jpg").write_bytes(b"gap")
    assert frame_utils._advance_sequential_count(str(tmp_path), 10) == 10


class _ErrorCapture:
    def __init__(self, *_args, **_kwargs):
        self.released = False
        self.read_count = 0

    def isOpened(self):
        return True

    def get(self, _prop):
        return 2

    def read(self):
        self.read_count += 1
        return (True, object()) if self.read_count == 1 else (False, None)

    def release(self):
        self.released = True


def test_M7_opencv_capture_released_on_error(tmp_path, monkeypatch):
    capture = _ErrorCapture()
    monkeypatch.setattr(frame_utils.cv2, "VideoCapture", lambda *_args: capture)
    monkeypatch.setattr(
        frame_utils.cv2, "imwrite", lambda *_args: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        frame_utils._extract_frames_opencv("vid.mp4", str(tmp_path), None, 1.0)

    assert capture.released is True


def test_M7_opencv_imwrite_false_raises(tmp_path, monkeypatch, capsys):
    capture = _ErrorCapture()
    monkeypatch.setattr(frame_utils.cv2, "VideoCapture", lambda *_args: capture)
    monkeypatch.setattr(frame_utils.cv2, "imwrite", lambda *_args: False)

    with pytest.raises(frame_utils.FrameExtractionError):
        frame_utils._extract_frames_opencv("vid.mp4", str(tmp_path), None, 1.0)

    assert capture.released is True
    assert "ERROR" in capsys.readouterr().out


class _ProbeCapture:
    def __init__(self, *_args, **_kwargs):
        self.released = False

    def get(self, prop):
        return 10 if prop == frame_utils.cv2.CAP_PROP_FRAME_COUNT else 1

    def release(self):
        self.released = True


class _FakeProcess:
    def __init__(self):
        self.pid = 1234
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_M7_ffmpeg_process_killed_on_progress_error(tmp_path, monkeypatch):
    capture = _ProbeCapture()
    process = _FakeProcess()
    monkeypatch.setattr(frame_utils, "_get_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(frame_utils.cv2, "VideoCapture", lambda *_args: capture)
    monkeypatch.setattr(frame_utils.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(frame_utils.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="progress failed"):
        frame_utils._extract_frames_ffmpeg(
            "vid.mp4",
            str(tmp_path),
            lambda *_args: (_ for _ in ()).throw(RuntimeError("progress failed")),
            1.0,
        )

    assert capture.released is True
    assert process.killed is True


def test_M7_unexpected_error_funneled(tmp_path, monkeypatch):
    failure = OSError("count failed")
    monkeypatch.setattr(
        frame_utils, "_extract_frames_ffmpeg", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)
    )

    with pytest.raises(frame_utils.FrameExtractionError) as caught:
        frame_utils.create_frames(
            "vid.mp4", str(tmp_path / "frames"), "Normal", "vid"
        )

    assert caught.value.__cause__ is failure
