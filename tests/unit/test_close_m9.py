"""M9 regression tests for close ordering and save-failure decisions."""

from types import SimpleNamespace

import labeling_app
from labeling_app import LabelingApp


class _Buffer:
    """Stands in for adapters.frame_buffer.FrameBuffer, which now owns the
    loader pool that on_close tears down."""

    def __init__(self, events):
        self.events = events

    def shutdown(self, *, wait, cancel_futures):
        self.events.append(("shutdown", wait, cancel_futures))


def _app(events, save_result=True):
    return SimpleNamespace(
        video=object(),
        save_data=lambda: events.append("save") or save_result,
        save_last_position=lambda: events.append("last_position"),
        _finalize_video_time=lambda: events.append("finalize_time"),
        # The working-state SQLite connection must close AFTER every writer
        # (save, last position, labeling-time checkpoint) and BEFORE teardown.
        _close_state_repo=lambda: events.append("close_state_repo"),
        frame_buffer=_Buffer(events),
        destroy=lambda: events.append("destroy"),
    )


def test_M9_cancel_has_zero_save_or_teardown_side_effects(monkeypatch):
    events = []
    app = _app(events)
    monkeypatch.setattr(
        labeling_app,
        "custom_confirm_close",
        lambda root: events.append("confirm") or False,
    )

    LabelingApp.on_close(app)

    assert events == ["confirm"]


def test_M9_confirm_saves_before_teardown(monkeypatch):
    events = []
    app = _app(events)
    monkeypatch.setattr(
        labeling_app,
        "custom_confirm_close",
        lambda root: events.append("confirm") or True,
    )

    LabelingApp.on_close(app)

    assert events == [
        "confirm",
        "save",
        "last_position",
        "finalize_time",
        "close_state_repo",
        ("shutdown", False, True),
        "destroy",
    ]


def test_M9_rejected_close_anyway_keeps_session_alive(monkeypatch):
    events = []
    app = _app(events, save_result=False)
    monkeypatch.setattr(labeling_app, "custom_confirm_close", lambda root: True)
    monkeypatch.setattr(
        labeling_app.messagebox,
        "askyesno",
        lambda *args, **kwargs: events.append("close_anyway") or False,
    )

    LabelingApp.on_close(app)

    assert events == ["save", "close_anyway"]
