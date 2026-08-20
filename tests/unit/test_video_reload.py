"""Regression tests for loading another video in the same session.

Two independent guarantees:

  1. `LabelingApp._unload_current_video` runs its steps in the one order that
     cannot mix two projects' data: every writer against the OLD repo first,
     then the video identity drops (idling the worker threads), then the frame
     buffer is emptied (generation bump), and the state DB closes last.
     Tested in the `test_close_m9.py` style — the unbound method driven over a
     `SimpleNamespace` that records events.

  2. `FrameBuffer.background_update` survives a transiently out-of-range
     current frame (possible around a reload when an old position meets a new
     total). It used to `return`, killing the daemon thread permanently — a
     Thread cannot be restarted, so every later load would break.
"""

import time
from threading import Thread
from types import SimpleNamespace

from PIL import Image

import labeling_app
from adapters.frame_buffer import BufferContext, FrameBuffer
from labeling_app import LabelingApp


# === 1. _unload_current_video ordering ========================================
class _FrameBufferStub:
    def __init__(self, events):
        self.events = events
        self.buffer_ready = True


def _app(events, save_result=True, cloth_app=None):
    app = SimpleNamespace(
        video=object(),
        video_name="old_video",
        _cloth_app=cloth_app,
        _last_step_sign=1,
        stop_video=lambda: events.append("stop_play"),
        _cancel_arrow_hold_state=lambda: events.append("cancel_hold"),
        save_data=lambda: events.append("save") or save_result,
        save_last_position=lambda: events.append("last_position"),
        _finalize_video_time=lambda: events.append("finalize_time"),
        _reset_zone_cache=lambda: events.append("reset_zone_cache"),
        _set_note_entry_text=lambda text: events.append(("note_entry", text)),
        _refresh_jump_label=lambda: None,
        _set_mode_button_states=lambda: events.append("mode_buttons"),
        cloth_btn=object(),
        name_label=SimpleNamespace(config=lambda **kw: None),
        framerate_label=SimpleNamespace(config=lambda **kw: None),
    )
    app.frame_buffer = _FrameBufferStub(events)
    # These two record whether the video was ALREADY detached when they ran —
    # the property that makes stale-frame / wrong-repo mixups impossible.
    app._buffer_reset = lambda: events.append(("buffer_reset", app.video is None))
    app._close_state_repo = lambda: events.append(("close_repo", app.video is None))
    return app


def test_unload_writes_first_then_detaches_then_clears(monkeypatch):
    events = []
    app = _app(events)
    monkeypatch.setattr(labeling_app.theme, "set_button_state", lambda *a: None)

    assert LabelingApp._unload_current_video(app) is True

    assert events == [
        "stop_play",
        "cancel_hold",
        "save",
        "last_position",
        "finalize_time",
        ("buffer_reset", True),   # video already None: workers idle by now
        ("close_repo", True),     # DB closes last, after every writer
        "reset_zone_cache",
        ("note_entry", ""),
        "mode_buttons",
    ]
    assert app.video is None and app.video_name is None
    assert app._last_step_sign == 0
    assert app.frame_buffer.buffer_ready is False


def test_unload_closes_the_clothes_window_before_saving(monkeypatch):
    events = []
    cloth = SimpleNamespace(
        top_level=SimpleNamespace(winfo_exists=lambda: True),
        on_close=lambda: events.append("cloth_close"),
    )
    app = _app(events, cloth_app=cloth)
    monkeypatch.setattr(labeling_app.theme, "set_button_state", lambda *a: None)

    assert LabelingApp._unload_current_video(app) is True

    # The clothes dots must be written through the OLD repo, i.e. before the
    # final save and long before the repo closes.
    assert events.index("cloth_close") < events.index("save")
    assert app._cloth_app is None


def test_unload_aborts_on_save_failure_and_keeps_the_session(monkeypatch):
    events = []
    app = _app(events, save_result=False)
    monkeypatch.setattr(labeling_app.theme, "set_button_state", lambda *a: None)
    original_video = app.video

    assert LabelingApp._unload_current_video(app) is False

    # Nothing was torn down: no detach, no buffer reset, no repo close.
    assert events == ["stop_play", "cancel_hold", "save"]
    assert app.video is original_video
    assert app.video_name == "old_video"


def test_unload_without_a_video_is_a_noop():
    events = []
    app = _app(events)
    app.video = None

    assert LabelingApp._unload_current_video(app) is True
    assert events == []


# === 2. the buffering daemon survives an out-of-range frame ===================
def test_buffering_loop_survives_out_of_range_current_frame(tmp_path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    Image.new("RGB", (8, 6), "red").save(frames_dir / "frame0.jpg")

    ticks = {"count": 0}

    def get_buffer_context():
        ticks["count"] += 1
        if ticks["count"] <= 3:
            # An old position against a new, shorter video — the exact
            # transient a reload can produce.
            return BufferContext(
                frames_dir=str(frames_dir), current_frame=99, total_frames=3,
                display_w=100, display_h=100, downscale=1.0,
                jump_frame_count=1, last_step_sign=0,
            )
        return BufferContext(
            frames_dir=str(frames_dir), current_frame=0, total_frames=0,
            display_w=100, display_h=100, downscale=1.0,
            jump_frame_count=1, last_step_sign=0,
        )

    fb = FrameBuffer(
        schedule_on_ui=lambda fn: fn(),
        on_status_change=lambda loaded: None,
        get_buffer_context=get_buffer_context,
        get_playback_context=lambda: None,
        apply_play_advance=lambda *a: None,
        on_playback_boundary=lambda *a: None,
        on_playback_schedule_error=lambda exc: None,
        on_priority_frame_loaded=lambda: None,
    )
    thread = Thread(target=fb.background_update, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and fb.get(0) is None:
            time.sleep(0.01)
    finally:
        fb.shutdown(wait=False, cancel_futures=True)
        thread.join(timeout=5.0)

    assert not thread.is_alive(), "the buffering thread did not shut down"
    assert fb.get(0) is not None, (
        "the buffering thread died on the out-of-range tick instead of "
        "skipping it — frame 0 was never loaded"
    )
