"""Unit tests for the frame-buffer decode pipeline performance work.

Pins the NEW behavior:

  1. `compute_target_size` — the pure fit-box sizing rule shared by
     `resize_for_buffer` and the draft-decode target in `_load_frame_to_buffer`.
     Unlike the old rule it UPSCALES (a 480p video on a 4K monitor must fill
     the canvas), still preserves aspect, and keeps the old degenerate-input
     guards (display dims <= 0 return the original, downscale <= 0 acts as 1).
  2. `resize_for_buffer` — fit-box semantics + no-op when already at target.
  3. `_load_frame_to_buffer` — the draft + BILINEAR path stores frames at the
     exact `compute_target_size` dimensions (both up- and downscale).
  4. Jump cancellation — queued-but-not-started prefetch decodes outside the
     new window are cancelled and leave `_inflight` (no leaked reservations).
"""

import time
from threading import Event, Thread

import pytest
from PIL import Image

from adapters.frame_buffer import FrameBuffer, compute_target_size, resize_for_buffer


# === 1. compute_target_size ====================================================
def test_upscale_low_res_source_fills_the_display_box():
    # 854x480 source on a 4K canvas: scale = min(3840/854, 2160/480) ≈ 4.4965.
    w, h = compute_target_size(854, 480, 3840, 2160, 1.0)
    assert w == 3840
    assert h == int(480 * (3840 / 854))
    assert w > 854 and h > 480  # actually upscaled — the old rule never did


def test_downscale_large_source_fits_the_display_box():
    # 4K source into a 1280x720 canvas (same aspect): exact fit.
    assert compute_target_size(3840, 2160, 1280, 720, 1.0) == (1280, 720)


def test_downscale_factor_shrinks_the_box():
    # downscale=2 halves the box: 1280x720 -> 640x360.
    assert compute_target_size(1920, 1080, 1280, 720, 2.0) == (640, 360)


def test_degenerate_display_dims_return_the_original_size():
    assert compute_target_size(1920, 1080, 0, 720, 1.0) == (1920, 1080)
    assert compute_target_size(1920, 1080, 1280, 0, 1.0) == (1920, 1080)
    assert compute_target_size(1920, 1080, -5, -5, 1.0) == (1920, 1080)


def test_nonpositive_downscale_is_treated_as_one():
    assert compute_target_size(1920, 1080, 1280, 720, 0) == \
        compute_target_size(1920, 1080, 1280, 720, 1.0)
    assert compute_target_size(1920, 1080, 1280, 720, -3) == (1280, 720)


@pytest.mark.parametrize("orig_w,orig_h,disp_w,disp_h,downscale", [
    (854, 480, 3840, 2160, 1.0),    # upscale, wide source
    (480, 854, 3840, 2160, 1.0),    # upscale, tall source
    (3840, 2160, 1280, 720, 1.0),   # downscale
    (1920, 1080, 1000, 1000, 1.5),  # square box, fractional downscale
])
def test_aspect_ratio_is_preserved(orig_w, orig_h, disp_w, disp_h, downscale):
    w, h = compute_target_size(orig_w, orig_h, disp_w, disp_h, downscale)
    # int() truncation moves each dim by < 1 px, so allow that much drift.
    assert abs(w / h - orig_w / orig_h) < 2 / min(w, h)
    # And the result never exceeds the box.
    assert w <= disp_w / downscale and h <= disp_h / downscale


def test_result_dims_are_at_least_one_pixel():
    assert compute_target_size(4000, 2, 100, 100, 1.0)[1] >= 1


# === 2. resize_for_buffer ======================================================
def test_resize_for_buffer_upscales_to_the_fit_box():
    img = Image.new("RGB", (100, 50))
    out = resize_for_buffer(img, 400, 400, 1.0)
    assert out.size == (400, 200)


def test_resize_for_buffer_is_a_noop_at_target_size():
    img = Image.new("RGB", (400, 200))
    assert resize_for_buffer(img, 400, 400, 1.0) is img


def test_resize_for_buffer_keeps_the_degenerate_guard():
    img = Image.new("RGB", (100, 50))
    assert resize_for_buffer(img, 0, 0, 1.0) is img


# === 3. the decode path stores frames at final display size ===================
def _make_buffer(get_buffer_context=lambda: None):
    return FrameBuffer(
        schedule_on_ui=lambda fn: fn(),
        on_status_change=lambda loaded: None,
        get_buffer_context=get_buffer_context,
        get_playback_context=lambda: None,
        apply_play_advance=lambda *a: None,
        on_playback_boundary=lambda *a: None,
        on_playback_schedule_error=lambda exc: None,
        on_priority_frame_loaded=lambda: None,
    )


@pytest.mark.parametrize("src_size,display,expected", [
    ((100, 50), (400, 400), (400, 200)),    # upscale path (no draft effect)
    ((1600, 800), (400, 400), (400, 200)),  # downscale path (draft engages)
])
def test_load_frame_stores_compute_target_size_dims(tmp_path, src_size, display, expected):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    Image.new("RGB", src_size, "blue").save(frames_dir / "frame0.jpg")

    fb = _make_buffer()
    try:
        fb._load_frame_to_buffer(0, str(frames_dir), *display, 1.0, fb._gen)
        img = fb.get(0)
        assert img is not None, "the frame never reached the buffer"
        assert img.size == expected
        assert 0 not in fb._inflight  # the sync path cleaned up after itself
    finally:
        fb.shutdown(wait=False, cancel_futures=True)


# === 4. jumps cancel stale queued prefetches ===================================
def test_jump_cancels_queued_decodes_outside_the_new_window(tmp_path):
    from adapters.frame_buffer import BufferContext

    total = 200
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for i in range(total + 1):
        Image.new("RGB", (8, 6), "red").save(frames_dir / f"frame{i}.jpg")

    position = {"frame": 0}

    def get_buffer_context():
        return BufferContext(
            frames_dir=str(frames_dir), current_frame=position["frame"],
            total_frames=total, display_w=100, display_h=100, downscale=1.0,
            jump_frame_count=1, last_step_sign=0,
        )

    fb = _make_buffer(get_buffer_context)
    # Block every pool worker so prefetch submissions stay QUEUED (only
    # queued-not-started futures are cancellable — exactly what a jump must
    # flush). The priority load of the current frame runs synchronously on
    # the buffering thread, so it is unaffected by the blocked pool.
    release = Event()
    for _ in range(fb._loader_pool._max_workers):
        fb._loader_pool.submit(release.wait)

    thread = Thread(target=fb.background_update, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with fb._lock:
                queued = [f for f in fb._inflight if f <= 50]
            if len(queued) >= 40:
                break
            time.sleep(0.01)
        assert len(queued) >= 40, "the old window never filled the pool queue"

        # Jump. The next tick recomputes the window around 150 and must cancel
        # the queued decodes for the old window (all outside [120, 200]).
        position["frame"] = 150
        fb.poke()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with fb._lock:
                stale = [f for f in fb._inflight if f < 120]
            if not stale:
                break
            time.sleep(0.01)
        assert not stale, (
            f"stale queued decodes survived the jump: {sorted(stale)[:10]}..."
        )
    finally:
        release.set()
        fb.shutdown(wait=False, cancel_futures=True)
        thread.join(timeout=5.0)
    assert not thread.is_alive(), "the buffering thread did not shut down"
