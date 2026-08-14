"""
adapters/frame_buffer.py
The frame read-ahead buffer + playback pacing engine, extracted from
LabelingApp (background_update / background_update_play and the buffer
store/evict helpers). NO Tk imports here: every UI interaction is marshaled
through the injected `schedule_on_ui` callable, and all app state the worker
threads need arrives through the injected context providers.

Threading invariants preserved from the original implementation:
  (a) one RLock (`_lock`) guards the four buffer structures
      (frame dict, byte-size dict, byte total, in-flight set);
  (b) `reset()` bumps the generation counter so in-flight decodes started
      under the old video/settings discard their result;
  (c) worker threads never touch the GUI — UI work goes through the injected
      `schedule_on_ui` (the app supplies its main-loop "run this on the UI
      thread soon" scheduler);
  (d) `compute_play_step` stays a pure, side-effect-free function
      (re-exported as `LabelingApp._compute_play_step` for the H1 tests).

Single-writer rule (two-writer bug fix): the playback worker no longer
assigns `video.current_frame` itself. It REQUESTS the advance via
`schedule_on_ui`, so every write to `current_frame` happens on the Tk main
thread (`apply_play_advance` callback), alongside the UI-thread writers
(next_frame, timeline clicks, select_frame). The `_advance_pending` flag
keeps the worker from double-stepping while a requested advance is still
queued on the UI thread.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event, RLock
from typing import Callable, NamedTuple, Optional

from PIL import Image

PLAYBACK_BUFFER_PAUSE_S = 1.0
PLAYBACK_BUFFER_AHEAD = 3
BUFFER_MAX_BYTES = 1_000_000_000


def compute_play_step(current_frame, total_frames, direction):
    """Pure play-step decision: (next_frame, stop). No Tk, no side effects.

    stop=True when playback sits at the edge it is moving toward (frame 0
    going backward, total_frames going forward) so the play loop doesn't
    busy-spin against a boundary.
    """
    if (direction > 0 and current_frame >= total_frames) or \
       (direction < 0 and current_frame <= 0):
        return current_frame, True
    return max(0, min(total_frames, current_frame + direction)), False


def resize_for_buffer(img, display_width, display_height, downscale):
    """Tk-free resize used by worker threads. Pure CPU work — no widget calls."""
    if display_width <= 0 or display_height <= 0:
        return img
    original_width, original_height = img.size
    aspect_ratio = original_width / original_height

    if downscale <= 0:
        downscale = 1.0

    target_width = max(1, int(original_width / downscale))
    target_height = max(1, int(original_height / downscale))

    max_width = min(display_width, target_width)
    max_height = min(display_height, target_height)

    if max_width / max_height > aspect_ratio:
        new_width = int(max_height * aspect_ratio); new_height = max_height
    else:
        new_width = max_width; new_height = int(max_width / aspect_ratio)
    return img.resize((new_width, new_height), Image.Resampling.LANCZOS)


class BufferContext(NamedTuple):
    """Per-tick snapshot of app state the buffering thread needs.
    Built ON DEMAND by the injected provider so the engine never reaches
    into the GUI object graph."""
    frames_dir: str
    current_frame: int
    total_frames: int
    display_w: int
    display_h: int
    downscale: float
    jump_frame_count: int
    last_step_sign: int


class PlaybackContext(NamedTuple):
    """Per-tick snapshot of playback state for the play-advance thread."""
    playing: bool
    direction: int
    current_frame: int
    total_frames: int
    frame_rate: Optional[float]


class _NullPerf:
    """No-op stand-in for PerfLogger when none is injected."""

    def time(self, name):
        return nullcontext()


class FrameBuffer:
    """Sliding-window JPEG frame buffer + playback pacing engine.

    Constructed with UI marshaling + status callbacks and config values;
    the frames directory and all per-video numbers arrive via the context
    providers because they change on every video load.
    """

    def __init__(
        self,
        *,
        schedule_on_ui: Callable,
        on_status_change: Callable,
        get_buffer_context: Callable,
        get_playback_context: Callable,
        apply_play_advance: Callable,
        on_playback_boundary: Callable,
        on_playback_schedule_error: Callable,
        on_priority_frame_loaded: Callable,
        max_bytes: int = BUFFER_MAX_BYTES,
        playback_buffer_ahead: int = PLAYBACK_BUFFER_AHEAD,
        playback_buffer_pause_s: float = PLAYBACK_BUFFER_PAUSE_S,
        perf=None,
    ):
        # Injected boundary — the ONLY paths back into the GUI.
        self._schedule_on_ui = schedule_on_ui
        self._on_status_change = on_status_change            # on_status_change(loaded: bool)
        self._get_buffer_context = get_buffer_context        # () -> BufferContext | None
        self._get_playback_context = get_playback_context    # () -> PlaybackContext | None
        self._apply_play_advance = apply_play_advance        # (next_frame, direction) on UI thread
        self._on_playback_boundary = on_playback_boundary    # (current_frame, direction)
        self._on_playback_schedule_error = on_playback_schedule_error  # (exc)
        self._on_priority_frame_loaded = on_priority_frame_loaded      # () on UI thread

        # Config values.
        self._max_bytes = max_bytes
        self._playback_ahead = playback_buffer_ahead
        self._playback_pause_s = playback_buffer_pause_s
        self._perf = perf if perf is not None else _NullPerf()

        # Buffer structures — ALL guarded by the one RLock.
        self._lock = RLock()
        self._buffer = {}            # frame index -> decoded/resized PIL image
        self._buffer_bytes = {}      # frame index -> estimated byte size
        self._buffer_total = 0       # running byte total
        self._inflight = set()       # frame indices currently being decoded
        self._gen = 0                # bumps on reset() to discard stale decodes

        # Priority-load + parallel-prefetch infrastructure.
        self._priority_frame = None
        self._priority_event = Event()
        self._loader_pool = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="frame-loader"
        )

        # Playback gating.
        self.buffer_ready = False
        self._advance_pending = False   # a requested advance awaits the UI thread

    # === Buffer access (UI thread) ============================================
    def __contains__(self, frame_number) -> bool:
        return frame_number in self._buffer

    def get(self, frame_number):
        """The decoded PIL image for `frame_number`, or None if not buffered."""
        return self._buffer.get(frame_number)

    def request_priority(self, frame_number):
        """Hint the buffering thread to load `frame_number` ASAP (jump target)."""
        self._priority_frame = frame_number
        self._priority_event.set()

    def poke(self):
        """Wake the buffering thread early (current frame missing from cache)."""
        self._priority_event.set()

    def reset(self):
        # Lock so concurrent workers can't store into a half-cleared buffer.
        # Bumps the generation so any in-flight worker decoded under the OLD
        # video/settings discards its result instead of polluting the new buffer.
        with self._lock:
            self._buffer.clear()
            self._buffer_bytes.clear()
            self._buffer_total = 0
            self._inflight.clear()
            self._gen += 1

    def shutdown(self, *, wait=False, cancel_futures=True):
        self._loader_pool.shutdown(wait=wait, cancel_futures=cancel_futures)

    # === Buffering thread =====================================================
    def background_update(self):
        while True:
            # Sleep up to 10 ms unless a jump pokes us awake earlier.
            self._priority_event.wait(timeout=0.01)
            self._priority_event.clear()
            ctx = self._get_buffer_context()
            if ctx is None:
                continue
            with self._perf.time("background_update"):
                current_frame = ctx.current_frame
                if current_frame < 0 or current_frame > ctx.total_frames:
                    return

                # Per-tick context captured once (the provider reads the
                # geometry cache maintained on the main thread — H1).
                frames_dir = ctx.frames_dir
                display_w = ctx.display_w
                display_h = ctx.display_h
                downscale = ctx.downscale
                gen = self._gen

                # 1) Load the currently-visible frame first (synchronously) so the
                #    user sees their jump destination ASAP, then fire a paint.
                current_frame_loaded = current_frame in self._buffer
                if not current_frame_loaded:
                    with self._perf.time("priority_load"):
                        self._load_frame_to_buffer(
                            current_frame, frames_dir, display_w, display_h, downscale, gen
                        )
                    if current_frame in self._buffer:
                        self._schedule_on_ui(self._on_priority_frame_loaded)
                        current_frame_loaded = True

                # 2) Honour an explicit priority hint (e.g. _request_buffered_step
                #    polling pre-loads the jump target before next_frame() is called).
                priority = self._priority_frame
                self._priority_frame = None
                if (priority is not None
                        and priority != current_frame
                        and 0 <= priority <= ctx.total_frames
                        and priority not in self._buffer):
                    with self._perf.time("priority_load"):
                        self._load_frame_to_buffer(
                            priority, frames_dir, display_w, display_h, downscale, gen
                        )

                # 3) Asymmetric, velocity-aware prefetch window. Same direction as
                #    the user's last navigation step gets a wider lookahead so a
                #    second jump in that direction lands in cache.
                base_ahead, base_behind = 50, 30
                jump = max(1, ctx.jump_frame_count)
                sign = ctx.last_step_sign
                if sign > 0:
                    ahead = max(base_ahead, jump * 2)
                    behind = max(10, base_behind // 2)
                elif sign < 0:
                    ahead = max(10, base_ahead // 2)
                    behind = max(base_behind, jump * 2)
                else:
                    ahead, behind = base_ahead, base_behind
                start_frame = max(0, current_frame - behind)
                end_frame = min(ctx.total_frames, current_frame + ahead)

                # 4) Submit prefetch loads to the worker pool. Forward window first
                #    (most likely direction of travel), then backward.
                for i in range(current_frame + 1, end_frame + 1):
                    self._maybe_submit_load(
                        i, ctx.total_frames, frames_dir, display_w, display_h, downscale, gen
                    )
                for i in range(current_frame - 1, start_frame - 1, -1):
                    self._maybe_submit_load(
                        i, ctx.total_frames, frames_dir, display_w, display_h, downscale, gen
                    )

                # 5) Trim out-of-range frames + enforce the byte budget. Hard cap
                #    scales with the asymmetric window so a wide forward prefetch
                #    isn't immediately undone.
                buffer_range_behind = max(200, behind * 4)
                buffer_range_ahead = max(200, ahead * 4)
                min_keep = max(0, current_frame - buffer_range_behind)
                max_keep = min(ctx.total_frames, current_frame + buffer_range_ahead)
                with self._lock:
                    frames_to_remove = [k for k in self._buffer if k < min_keep or k > max_keep]
                    for k in frames_to_remove:
                        self._remove_frame(k)
                    self._evict_to_budget(current_frame)

                # 6) Update the status pill (the app dedupes + marshals to Tk).
                self._on_status_change(current_frame_loaded)

                # 7) buffer_ready gates the playback thread.
                buffer_ready = current_frame_loaded
                if buffer_ready:
                    max_check = min(ctx.total_frames, current_frame + self._playback_ahead)
                    for i in range(current_frame, max_check + 1):
                        if i not in self._buffer:
                            buffer_ready = False
                            break
                self.buffer_ready = buffer_ready

    # === Playback thread ======================================================
    def background_update_play(self):
        while True:
            ctx = self._get_playback_context()
            if ctx is not None and ctx.playing:
                direction = 1 if ctx.direction >= 0 else -1
                current_frame = ctx.current_frame
                next_frame, stop = compute_play_step(
                    current_frame, ctx.total_frames, direction
                )
                if stop:
                    self._on_playback_boundary(current_frame, direction)
                    continue
                if current_frame not in self._buffer:
                    self.buffer_ready = False
                    time.sleep(self._playback_pause_s)
                    continue
                if next_frame not in self._buffer:
                    self.buffer_ready = False
                    time.sleep(self._playback_pause_s)
                    continue
                if not self.buffer_ready:
                    time.sleep(self._playback_pause_s)
                    continue
                if self._advance_pending:
                    # The previous requested advance hasn't run on the UI thread
                    # yet — wait for it instead of double-stepping the same frame.
                    time.sleep(0.001)
                    continue
                start = time.perf_counter()
                # Single-writer rule (two-writer bug fix): REQUEST the advance;
                # the UI thread applies current_frame + redraw in one callback.
                if not self._request_advance(next_frame, direction):
                    continue
                if next_frame not in self._buffer:
                    # Evicted between the check above and now — reload with priority.
                    self._priority_event.set()
                interval = 1.0 / ctx.frame_rate if ctx.frame_rate else 0.04
                elapsed = time.perf_counter() - start
                time.sleep(max(0.0, interval - elapsed))
            else:
                time.sleep(0.05)

    def _request_advance(self, next_frame, direction):
        """Marshal one playback advance to the UI thread. Returns False when
        scheduling failed (Tk mainloop gone — close mid-playback)."""
        self._advance_pending = True

        def _apply():
            try:
                self._apply_play_advance(next_frame, direction)
            finally:
                self._advance_pending = False

        try:
            self._schedule_on_ui(_apply)
            return True
        except Exception as e:
            # Mirrors the old TclError/RuntimeError guard around the UI-marshal
            # call, without this adapter knowing anything about the GUI toolkit.
            self._advance_pending = False
            self._on_playback_schedule_error(e)
            return False

    # === Decode / store / evict ==============================================
    def _load_frame_to_buffer(self, frame_number, frames_dir, display_w, display_h, downscale, gen):
        """Disk read + JPEG decode + resize + buffer store. Safe to run on any thread.

        Stores the result only if the buffer generation still matches (gen == self._gen),
        i.e. no reset() happened mid-decode. Always discards from the in-flight set.
        """
        try:
            with self._perf.time("load_frame_total"):
                frame_path = os.path.join(frames_dir, f"frame{frame_number}.jpg")
                with self._perf.time("load_frame_open"):
                    with Image.open(frame_path) as opened:
                        img = opened.copy()
                with self._perf.time("load_frame_resize"):
                    img = resize_for_buffer(img, display_w, display_h, downscale)
                try:
                    bytes_per_pixel = max(1, len(img.getbands()))
                except Exception:
                    bytes_per_pixel = 4
                est_bytes = int(img.width * img.height * bytes_per_pixel)
                with self._lock:
                    if gen == self._gen:
                        self._store_frame(frame_number, img, est_bytes)
                    self._inflight.discard(frame_number)
        except Exception as e:
            with self._lock:
                self._inflight.discard(frame_number)
            print(f"ERROR: Opening or processing frame {frame_number}: {str(e)}")

    def _maybe_submit_load(self, frame_number, total_frames, frames_dir, display_w, display_h, downscale, gen):
        """Submit a prefetch load to the worker pool, deduping against in-flight + cached frames."""
        if frame_number < 0 or frame_number > total_frames:
            return
        with self._lock:
            if frame_number in self._buffer or frame_number in self._inflight:
                return
            self._inflight.add(frame_number)
        try:
            self._loader_pool.submit(
                self._load_frame_to_buffer,
                frame_number, frames_dir, display_w, display_h, downscale, gen,
            )
        except RuntimeError:
            # Pool was shut down (e.g. on app close); back out the inflight reservation.
            with self._lock:
                self._inflight.discard(frame_number)

    def _remove_frame(self, frame_number):
        with self._lock:
            if frame_number in self._buffer:
                del self._buffer[frame_number]
            removed = self._buffer_bytes.pop(frame_number, 0)
            self._buffer_total = max(0, self._buffer_total - removed)

    def _store_frame(self, frame_number, img, est_bytes):
        with self._lock:
            if frame_number in self._buffer_bytes:
                self._buffer_total = max(0, self._buffer_total - self._buffer_bytes.get(frame_number, 0))
            self._buffer[frame_number] = img
            self._buffer_bytes[frame_number] = est_bytes
            self._buffer_total = self._buffer_total + est_bytes

    def _evict_to_budget(self, current_frame):
        limit = self._max_bytes
        if limit is None or limit <= 0:
            return
        with self._lock:
            if self._buffer_total <= limit:
                return
            candidates = sorted(self._buffer.keys(), key=lambda k: abs(k - current_frame), reverse=True)
            for k in candidates:
                if k == current_frame:
                    continue
                self._remove_frame(k)
                if self._buffer_total <= limit:
                    break
