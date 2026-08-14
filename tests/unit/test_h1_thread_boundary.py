"""
H1 — thread → UI boundary for the playback/buffer daemon threads.

H1's fix stops the playback thread from calling Tk widget code directly:
the worker now only advances plain state (`current_frame`, `_last_step_sign`)
and schedules ONE main-thread redraw via `self.after(0, _render_current_frame)`.
The end-to-end `after(0, ...)` marshalling needs a live Tk mainloop and is
covered by the manual checklist in the fix plan — not reproducible headless.

What IS testable pure logic (per the plan's "automatable slice"):

1. `_compute_play_step` — the side-effect-free play-step decision extracted
   from `background_update_play` (boundary stop + clamped advance).
2. `_is_ui_thread` / `_assert_ui_thread` — the thread-identity guard that
   catches future off-thread widget access. Exercised via unbound methods on
   a lightweight stub `self`, without a running Tk root (same pattern as C2).

Run:  uv run pytest tests/ -k h1
"""
import threading
from types import SimpleNamespace

import pytest

import labeling_app
from labeling_app import LabelingApp


# --- 1) _compute_play_step: pure play-step decision -------------------------

def test_advances_forward():
    assert LabelingApp._compute_play_step(10, 100, 1) == (11, False)


def test_advances_backward():
    assert LabelingApp._compute_play_step(10, 100, -1) == (9, False)


def test_stops_at_end_going_forward():
    assert LabelingApp._compute_play_step(100, 100, 1) == (100, True)


def test_stops_at_start_going_backward():
    assert LabelingApp._compute_play_step(0, 100, -1) == (0, True)


def test_last_step_into_the_end_is_not_a_stop():
    # One frame before the edge still advances; the NEXT tick stops.
    assert LabelingApp._compute_play_step(99, 100, 1) == (100, False)
    assert LabelingApp._compute_play_step(1, 100, -1) == (0, False)


def test_stops_when_beyond_bounds():
    # Defensive: current_frame past the edge must also stop, not oscillate.
    assert LabelingApp._compute_play_step(150, 100, 1) == (150, True)
    assert LabelingApp._compute_play_step(-5, 100, -1) == (-5, True)


# --- 2) thread-identity guard ------------------------------------------------

def _stub(ident=None):
    return SimpleNamespace(
        _ui_thread_ident=threading.get_ident() if ident is None else ident,
        _is_ui_thread=lambda: None,  # replaced below where needed
    )


def _run_in_worker(fn):
    """Run fn() on a fresh thread; return (result, exception)."""
    out = {}

    def target():
        try:
            out["result"] = fn()
        except Exception as e:  # noqa: BLE001 — we assert on it
            out["exc"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    return out.get("result"), out.get("exc")


def test_is_ui_thread_true_on_recording_thread():
    stub = _stub()
    assert LabelingApp._is_ui_thread(stub) is True


def test_is_ui_thread_false_on_worker_thread():
    stub = _stub()  # ident recorded on THIS (the "UI") thread
    result, exc = _run_in_worker(lambda: LabelingApp._is_ui_thread(stub))
    assert exc is None
    assert result is False


def test_assert_ui_thread_noop_when_guard_disabled(monkeypatch):
    monkeypatch.setattr(labeling_app, "DEBUG_ASSERT_UI_THREAD", False)
    stub = _stub()
    stub._is_ui_thread = lambda: LabelingApp._is_ui_thread(stub)
    # Off-thread call must NOT raise while the dev flag is off.
    _, exc = _run_in_worker(lambda: LabelingApp._assert_ui_thread(stub))
    assert exc is None


def test_assert_ui_thread_raises_off_thread_when_enabled(monkeypatch):
    monkeypatch.setattr(labeling_app, "DEBUG_ASSERT_UI_THREAD", True)
    stub = _stub()
    stub._is_ui_thread = lambda: LabelingApp._is_ui_thread(stub)
    _, exc = _run_in_worker(lambda: LabelingApp._assert_ui_thread(stub))
    assert isinstance(exc, RuntimeError)
    assert "non-UI thread" in str(exc)


def test_assert_ui_thread_passes_on_ui_thread_when_enabled(monkeypatch):
    monkeypatch.setattr(labeling_app, "DEBUG_ASSERT_UI_THREAD", True)
    stub = _stub()
    stub._is_ui_thread = lambda: LabelingApp._is_ui_thread(stub)
    LabelingApp._assert_ui_thread(stub)  # must not raise
