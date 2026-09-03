"""
tests/e2e/gui_driver.py
Small vocabulary for driving the REAL Tk application from pytest.

Everything here goes through Tk's own mechanisms, because that is the only way
to prove the WIRING rather than the handlers:

  * `pump` / `wait_until` run the real event loop (`update_idletasks` +
    `update`), so `after()` callbacks, the buffering thread's
    `after(0, ...)` marshaling and the modal windows' nested loops all behave
    exactly as they do in production.
  * `click` invokes the widget's OWN command (`Button.invoke()`), so a test
    fails if a button is wired to the wrong callback or not wired at all.
  * `press` / `click_canvas` synthesize real X/Win events with
    `event_generate`, so a test fails if a `bind()` is missing or bound to the
    wrong sequence.
  * `dismiss_dialog` polls for a modal Toplevel by TITLE and invokes its real
    button, so the app's own dialogs (mode picker, close confirmation) stay in
    the loop instead of being stubbed out.

No helper here reaches into a private attribute of the app; the few places a
test has to do that are documented at the call site.
"""

import time
import tkinter as tk
from tkinter import ttk


# === event loop ===============================================================
def pump(app, seconds=0.0):
    """Run the real Tk event loop for at least `seconds` (0 = one full pass)."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            app.update_idletasks()
            app.update()
        except tk.TclError:  # app destroyed mid-pump (e.g. after on_close)
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.005)


def wait_until(app, predicate, timeout=20.0, what="condition"):
    """Pump the event loop until `predicate()` is truthy. Raises on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        pump(app)
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(0.01)


# === widget lookup ============================================================
def walk(widget):
    """Depth-first iterator over `widget` and all its descendants."""
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def find_widget(root, *, cls=None, text=None):
    """First widget under `root` matching an exact class and/or `text` option."""
    for widget in walk(root):
        if cls is not None and not isinstance(widget, cls):
            continue
        if text is not None:
            try:
                if widget.cget("text") != text:
                    continue
            except tk.TclError:
                continue
        return widget
    return None


def find_button(root, text):
    button = find_widget(root, cls=(ttk.Button, tk.Button), text=text)
    assert button is not None, f"no button labeled {text!r} in {root}"
    return button


def click(root, text):
    """Press the button labeled `text` through its OWN command callback."""
    find_button(root, text).invoke()


def select_radio(root, text):
    """Select the radiobutton labeled `text` (sets its var + runs its command)."""
    radio = find_widget(root, cls=(ttk.Radiobutton, tk.Radiobutton), text=text)
    assert radio is not None, f"no radiobutton labeled {text!r}"
    radio.invoke()


def find_toplevel(app, title):
    for child in app.winfo_children():
        if isinstance(child, tk.Toplevel):
            try:
                if child.title() == title:
                    return child
            except tk.TclError:
                continue
    return None


# === real input events ========================================================
def press(widget, sequence, **kwargs):
    """Synthesize a real key/button event on `widget` (exercises `bind`)."""
    widget.event_generate(sequence, when="now", **kwargs)


def click_canvas(canvas, sequence, x, y):
    """Synthesize a real mouse click at canvas coordinates (x, y)."""
    canvas.event_generate("<Motion>", when="now", x=x, y=y)
    canvas.event_generate(sequence, when="now", x=x, y=y)


def type_text(entry, text):
    """Type `text` into a Text/Entry one real KeyPress at a time."""
    entry.focus_set()
    for char in text:
        keysym = {" ": "space"}.get(char, char)
        entry.event_generate("<KeyPress>", when="now", keysym=keysym)


# === modal dialogs the app owns itself ========================================
def dismiss_dialog(app, title, button_text, attempts=400, interval_ms=15):
    """Arm a poller that clicks `button_text` on the app's own modal `title`.

    The app's mode picker and close confirmation are real `Toplevel`s that block
    on `wait_window()`. `after()` callbacks still run inside that nested loop,
    so this drives the REAL dialog (including the config write behind the mode
    picker) instead of stubbing the method out.

    Returns a state dict whose ``clicked`` flag a test can assert on.
    """
    state = {"clicked": False, "left": attempts}

    def tick():
        if state["clicked"]:
            return
        window = find_toplevel(app, title)
        if window is not None:
            button = find_widget(window, cls=(ttk.Button, tk.Button), text=button_text)
            if button is not None:
                state["clicked"] = True
                button.invoke()
                return
        state["left"] -= 1
        if state["left"] > 0:
            app.after(interval_ms, tick)

    app.after(interval_ms, tick)
    return state


# === composed workflows =======================================================
def load_video(app, workspace, video_path, mode="Normal"):
    """Click "Load Video" and answer both dialogs the way a user would.

    The mode picker is the app's own Toplevel (driven for real); the OS file
    picker is `tkinter.filedialog.askopenfilename`, which cannot be synthesized
    and is stubbed by the `workspace` fixture.
    """
    workspace.chosen_video = str(video_path)
    workspace.mode = mode
    mode_dialog = dismiss_dialog(app, "Select Mode", "Continue")
    app.load_video_btn.invoke()
    assert mode_dialog["clicked"], "the mode dialog never appeared"
    assert app.video is not None, "Load Video did not produce a video"
    # The buffering thread decodes frame 0 asynchronously; wait for the first
    # real paint so later steps see a consistent display.
    wait_until(app, lambda: 0 in app.frame_buffer, what="frame 0 in the buffer")


def save(app):
    """Click the toolbar Save button and let the export modal finish.

    `invoke()` blocks until the export modal's `wait_window()` returns, so the
    state DB and the export CSV are both on disk when this comes back.
    """
    click(app, "Save")
    pump(app, 0.05)


def pending_timers(app):
    """Tcl's live `after` queue as [(id, "<script> timer"), ...].

    tkinter names the Tcl command it registers for `after` after the Python
    callback (`Misc.after` sets `callit.__name__ = func.__name__`), so a
    still-scheduled repeating timer is identifiable BY NAME here — and that is
    the same name Tcl puts in the `invalid command name "...periodic_print_dot"`
    error the app used to print on every exit.
    """
    entries = []
    for after_id in app.tk.eval("after info").split():
        try:
            entries.append((after_id, app.tk.eval(f"after info {after_id}")))
        except tk.TclError:  # fired between the listing and the query
            continue
    return entries


def close(app):
    """Close the app the way the WM does, confirming the app's own dialog.

    Returns the `after` queue as it was at the LAST instant the interpreter was
    alive: `app.destroy` is shadowed on the instance so the sample is taken
    inside `on_close()`, immediately after `_cancel_pending_timers()` ran. That
    is the only place the app's timer hygiene can be observed — one line later
    the interpreter is gone.
    """
    at_destroy = []
    real_destroy = app.destroy

    def capturing_destroy():
        at_destroy.extend(pending_timers(app))
        real_destroy()

    app.destroy = capturing_destroy
    confirm = dismiss_dialog(app, "Close Application", "OK")
    try:
        app.on_close()
    finally:
        try:
            del app.destroy  # drop the shadow; the class method is live again
        except AttributeError:  # pragma: no cover
            pass
    assert confirm["clicked"], "the close confirmation never appeared"
    return at_destroy


def goto_frame(app, target):
    """Step to `target` with the real `>` / `<` toolbar buttons."""
    guard = 0
    while app.video.current_frame != target:
        click(app, ">" if app.video.current_frame < target else "<")
        pump(app)
        guard += 1
        assert guard < 500, f"could not reach frame {target}"
