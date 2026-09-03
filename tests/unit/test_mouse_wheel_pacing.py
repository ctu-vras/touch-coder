"""Headless checks for frame-rate-paced mouse-wheel navigation.

The wheel queues notches and consumes one frame per video frame interval, so a
fast flick moves at playback speed (like holding an arrow key) and the queue is
capped so motion stops shortly after the wheel does.
"""

from types import MethodType, SimpleNamespace

import pytest

import labeling_app
from labeling_app import LabelingApp


class FakeBuffer:
    def __init__(self, frames=range(0, 1000)):
        self.frames = set(frames)
        self.priority_requests = []

    def __contains__(self, frame):
        return frame in self.frames

    def request_priority(self, frame):
        self.priority_requests.append(frame)


class FakeClock:
    """Deterministic stand-in for time.monotonic; the test advances it."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(labeling_app, "time", fake)
    return fake


def _wheel_app(*, fps=25.0, total_frames=999, current_frame=100, buffer=None,
               clock=None, redraw_s=0.0):
    app = SimpleNamespace(
        video=SimpleNamespace(current_frame=current_frame, total_frames=total_frames),
        frame_rate=fps,
        frame_buffer=buffer if buffer is not None else FakeBuffer(),
        _wheel_backlog=0,
        _wheel_tick_id=None,
        _wheel_delta_accum=0,
        _wheel_next_due=None,
        scheduled=[],   # (delay_ms, callback)
        steps=[],
        cancelled=[],
    )

    def after(self, delay, callback):
        self.scheduled.append((delay, callback))
        return len(self.scheduled)

    def after_cancel(self, ident):
        self.cancelled.append(ident)

    def next_frame(self, delta, play=False):
        self.steps.append(delta)
        self.video.current_frame = max(0, min(self.video.total_frames, self.video.current_frame + delta))
        if clock is not None:
            clock.now += redraw_s  # simulate the synchronous redraw cost

    app.after = MethodType(after, app)
    app.after_cancel = MethodType(after_cancel, app)
    app.next_frame = MethodType(next_frame, app)
    for name in (
        "on_mouse_wheel", "_wheel_notches", "_wheel_backlog_cap",
        "_frame_interval_ms", "_wheel_tick", "_cancel_wheel_scroll",
    ):
        setattr(app, name, MethodType(getattr(LabelingApp, name), app))
    return app


def _drain(app, clock=None):
    """Run scheduled after() callbacks until the queue is empty."""
    while app.scheduled:
        delay, callback = app.scheduled.pop(0)
        if clock is not None:
            clock.now += delay / 1000.0  # the timer fires when its delay elapses
        callback()


def _win_event(delta):
    return SimpleNamespace(delta=delta, num=None)


@pytest.fixture
def windows_platform(monkeypatch):
    monkeypatch.setattr(labeling_app.sys, "platform", "win32")


def test_redraw_cost_is_absorbed_into_the_frame_interval(windows_platform, clock):
    # 25 fps -> 40 ms period. Each redraw takes 21 ms, so the timer must wait
    # only the remaining 19 ms; otherwise a 12 s video scrolls in ~18 s.
    app = _wheel_app(clock=clock, redraw_s=0.021)
    for _ in range(4):
        app.on_mouse_wheel(_win_event(-120))
    assert [d for d, _ in app.scheduled] == [19]

    start = clock.now - 0.021  # the first step happened at t=start
    _drain(app, clock)
    assert len(app.steps) == 4
    # Three paced steps after the first one: exactly 3 periods elapsed at
    # the moment the last step fired (+ its own redraw, + the idle tick).
    assert clock.now == pytest.approx(start + 4 * 0.040, abs=1e-9)


def test_redraw_slower_than_the_interval_never_waits_more_than_a_millisecond(windows_platform, clock):
    app = _wheel_app(clock=clock, redraw_s=0.060)
    app.on_mouse_wheel(_win_event(-120))
    app.on_mouse_wheel(_win_event(-120))
    assert [d for d, _ in app.scheduled] == [1]


def test_single_notch_steps_one_frame_immediately(windows_platform, clock):
    app = _wheel_app(clock=clock)
    app.on_mouse_wheel(_win_event(-120))
    assert app.steps == [1]
    assert app.video.current_frame == 101
    # The stepper stays armed for one frame interval, then idles.
    assert [d for d, _ in app.scheduled] == [40]
    _drain(app)
    assert app.steps == [1]
    assert app._wheel_tick_id is None


def test_wheel_up_moves_backwards(windows_platform):
    app = _wheel_app()
    app.on_mouse_wheel(_win_event(120))
    assert app.steps == [-1]


def test_linux_button_events_map_to_one_notch_each():
    app = _wheel_app()
    app.on_mouse_wheel(SimpleNamespace(delta=0, num=5))
    app.on_mouse_wheel(SimpleNamespace(delta=0, num=4))  # queued: stepper still armed
    _drain(app)
    assert app.steps == [1, -1]


def test_burst_is_paced_at_frame_interval_and_capped(windows_platform, clock):
    app = _wheel_app(fps=25.0, clock=clock)
    for _ in range(30):  # a fast flick: 30 notches in one event-loop pass
        app.on_mouse_wheel(_win_event(-120))

    assert app.steps == [1]                      # first notch painted immediately
    assert [d for d, _ in app.scheduled] == [40]  # one paced tick at 1000/25 ms
    _drain(app)

    # The immediate step plus at most WHEEL_BACKLOG_S worth of queued frames;
    # the remaining notches are dropped so motion stops soon after the wheel.
    cap = round(25.0 * labeling_app.WHEEL_BACKLOG_S)
    assert len(app.steps) == 1 + cap
    assert app.video.current_frame == 100 + 1 + cap
    assert app._wheel_backlog == 0
    assert app._wheel_tick_id is None


def test_reversal_drops_stale_backlog(windows_platform):
    app = _wheel_app()
    for _ in range(4):
        app.on_mouse_wheel(_win_event(-120))     # forward burst, 3 still queued
    app.on_mouse_wheel(_win_event(120))          # one notch back
    _drain(app)
    assert app.steps == [1, -1]


def test_high_resolution_deltas_accumulate_to_whole_notches(windows_platform):
    app = _wheel_app()
    for _ in range(3):
        app.on_mouse_wheel(_win_event(-40))
    assert app.steps == [1]
    assert app._wheel_delta_accum == 0


def test_missing_frame_requests_priority_and_retries_without_consuming(windows_platform):
    buffer = FakeBuffer(frames={100})
    app = _wheel_app(buffer=buffer)
    app.on_mouse_wheel(_win_event(-120))

    assert app.steps == []
    assert buffer.priority_requests == [101]
    assert [d for d, _ in app.scheduled] == [labeling_app.WHEEL_BUFFER_POLL_MS]
    assert app._wheel_backlog == 1

    buffer.frames.add(101)
    _drain(app)
    assert app.steps == [1]


def test_wheel_stops_at_last_frame(windows_platform):
    app = _wheel_app(total_frames=200, current_frame=200)
    app.on_mouse_wheel(_win_event(-120))
    assert app.steps == []
    assert app._wheel_backlog == 0


def test_cancel_drops_queue_and_pending_tick(windows_platform):
    app = _wheel_app()
    for _ in range(3):
        app.on_mouse_wheel(_win_event(-120))
    assert app._wheel_tick_id is not None
    app._cancel_wheel_scroll()
    assert app._wheel_backlog == 0
    assert app._wheel_tick_id is None
    assert app.cancelled == [1]


def test_frame_interval_falls_back_without_frame_rate(windows_platform):
    app = _wheel_app(fps=None)
    assert app._frame_interval_ms() == labeling_app.WHEEL_DEFAULT_INTERVAL_MS
    assert app._wheel_backlog_cap() >= 1
