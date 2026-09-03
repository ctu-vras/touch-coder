"""
Cheapest possible regression net: does the real application still BUILD, WIRE
and CLOSE?

`LabelingApp.__init__` runs `theme.init_style` (ttkbootstrap), `build_ui`
(the whole widget tree + every binding) and `init_diagram` (zone-mask load +
diagram render). An import error, a missing asset, a renamed callback, a
ttkbootstrap style regression or a broken PyInstaller-visible resource path
fails here in about a second — long before the workflow tests get a chance to.

HOW IT DRIVES THE APP
---------------------
Construction plus the REAL event loop, no stubs beyond the sandbox. Buttons are
inspected through their own `command` option and located by their real `text`,
so a control that lost its callback is a failure. The close path goes through
`app.on_close()` — the exact callable `build_ui` registers as
`WM_DELETE_WINDOW` — and answers the app's own modal confirmation by invoking
its real OK button.

Everything lives in ONE test function on purpose: each `LabelingApp` is a real
Tk root, and this suite is not allowed to open one per assertion (see the window
discipline section of conftest.py). The per-section helpers keep the failure
messages specific.
"""

import tkinter as tk

import pytest

from gui_driver import close, find_button, pump, walk

pytestmark = pytest.mark.gui

# Panels and widgets the controller talks to by attribute; build_ui must
# produce every one of them or the app is broken in a way no unit test sees.
_REQUIRED_ATTRIBUTES = (
    "video_frame", "timeline_frame", "control_frame", "diagram_frame",
    "diagram_canvas", "timeline_canvas", "timeline2_canvas",
    "note_entry", "load_video_btn", "analysis_btn", "cloth_btn",
    "par1_btn", "par2_btn", "par3_btn",
    "limb_par1_btn", "limb_par2_btn", "limb_par3_btn",
    "frame_counter_label", "time_counter_label", "name_label",
    "mode_label", "loading_label", "jump_label",
)

_EXPECTED_BUTTON_LABELS = (
    "Load Video", "Settings", "Clothes", "Analysis", "Save",
    "<<", "<", ">", ">>", "Play", "Stop",
    "Save Note", "Select Frame",
)

_EXPECTED_TOP_BUTTON_ORDER = (
    "Load Video", "Settings", "Clothes", "Analysis", "Save",
)


def _assert_panels_exist(app):
    for attribute in _REQUIRED_ATTRIBUTES:
        assert getattr(app, attribute, None) is not None, f"build_ui lost {attribute}"


def _assert_every_button_is_wired(app):
    """A ttk.Button with an empty `command` is a silent dead control."""
    unwired = []
    for widget in walk(app):
        if widget.winfo_class() != "TButton":
            continue
        if not str(widget.cget("command")).strip():
            unwired.append(widget.cget("text"))
    assert unwired == [], f"buttons with no command: {unwired}"


def _assert_expected_buttons_exist(app):
    for label in _EXPECTED_BUTTON_LABELS:
        assert find_button(app, label) is not None


def _assert_top_buttons_are_ordered(app):
    labels = tuple(
        child.cget("text")
        for child in app.load_video_btn.master.winfo_children()
        if child.winfo_class() == "TButton"
    )
    assert labels == _EXPECTED_TOP_BUTTON_ORDER


def _assert_zone_masks_loaded(app):
    """The diagram's hit-test data must actually be on disk and readable."""
    assert app._zone_masks, "no zone masks were loaded"
    names = [name for name, _ in app._zone_masks]
    assert "I" in names and "OUTSIDE" in names


def test_app_builds_wires_and_closes_cleanly(app, capfd):
    assert app.video is None
    assert app.title() == "TinyTouch"

    _assert_panels_exist(app)
    _assert_every_button_is_wired(app)
    _assert_expected_buttons_exist(app)
    _assert_top_buttons_are_ordered(app)
    _assert_zone_masks_loaded(app)

    # Analysis / Clothes stay disabled until a video is loaded.
    assert str(app.analysis_btn.cget("state")) == "disabled"
    assert str(app.cloth_btn.cget("state")) == "disabled"

    # The periodic diagram repaint (after(300, ...)) must survive a few ticks
    # with no video loaded — that is the state the app starts in.
    pump(app, 0.7)
    assert isinstance(app.diagram_canvas, tk.Canvas)

    # --- the close path, and the timer bug it used to leak -------------------
    # `periodic_print_dot` reschedules itself forever. Before the fix it was
    # never cancelled, so the timer outlived the interpreter and Tcl reported
    # `invalid command name "...periodic_print_dot"` on every exit. `close()`
    # samples Tcl's `after` queue at the last instant before destroy: with no
    # video loaded nothing else schedules anything, so it must be EMPTY.
    still_scheduled = close(app)
    assert still_scheduled == [], (
        "the app destroyed its root with timers still in Tcl's queue: "
        f"{still_scheduled}"
    )
    # Nothing Tcl-level may be printed by the close sequence.
    captured = capfd.readouterr()
    noise = [
        line for line in (captured.out + captured.err).splitlines()
        if "invalid command name" in line or "bgerror" in line
    ]
    assert noise == [], f"Tcl errors printed while closing: {noise}"
