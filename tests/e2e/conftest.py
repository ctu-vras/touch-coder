"""
tests/e2e/conftest.py
Machinery for the in-process Tk end-to-end suite (`pytest -m gui`).

WHY THIS SUITE EXISTS
---------------------
Every layer of the refactor is covered by the unit/integration suites, but
nothing exercised the real widget tree, the real `bind()` table or a genuine
annotate -> save -> export cycle through the GUI. These three tests construct
the actual `LabelingApp`, pump Tk's own event loop and drive behavior through
widgets and synthesized events (see `gui_driver`):

  1. test_tk_smoke.py             does the app still BUILD, wire and CLOSE?
  2. test_annotate_save_export.py load -> annotate -> Save -> DB + export CSV
  3. test_upgrade_path.py         the owner's real pre-refactor tree, migrated

Anything that only re-asserts logic already covered by the 287 unit /
integration tests is deliberately NOT here: a Tk root is expensive, flaky and
intrusive, so this suite buys only what nothing else can.

WINDOW DISCIPLINE
-----------------
The first version of this suite popped a dozen real windows onto the owner's
desktop while they were working. Two rules now prevent that:

  * ONE Tk root per test, three for the whole suite. Each test shares a single
    app instance across all of its assertions (`app` / `loaded_app`); the
    upgrade test is the only one that needs a second root and it reuses the
    same `app_factory`.
  * Every window is made INVISIBLE and moved OFF-SCREEN the moment it exists —
    the root in `app_factory`, and every `Toplevel` the app itself opens (mode
    picker, progress modals, close confirmation) via a patched
    `tkinter.Toplevel.__init__` in the `workspace` fixture.

    Technique: `attributes("-alpha", 0.0)` + `geometry("+6000+6000")`.
    NOT `withdraw()`: an unmapped window resolves no geometry, and the code
    under test caches `video_frame.winfo_width()/winfo_height()` into
    `_display_w/_display_h` for the buffering thread (H1) — a withdrawn root
    would feed it 1x1 and silently change the frame-scaling path being tested.
    An alpha-0, off-screen root is still MAPPED, so `winfo_*` returns the real
    955x814 video panel and 225x348 diagram canvas (verified) while nothing is
    drawn on the owner's screen.

    `Toplevel.geometry("520x180")` calls made later by the app only set the
    SIZE, so the off-screen position installed at construction survives.

Teardown runs in a fixture `finally` and is best-effort at every step (stop
playback, drop the video so the daemon threads let go of Tk, shut the frame
buffer down, close the state DB, drain the `after` queue, destroy), so a
FAILING test can never leave an orphan window or a live daemon thread behind.

ISOLATION (non-negotiable)
--------------------------
The app resolves its writable state from exactly two places:

  * `domain.project.ProjectPaths` builds every path from the RELATIVE roots
    ``data/`` and ``videos/``, i.e. relative to the process CWD.
  * `adapters.config.get_config_path()` returns ``<get_app_dir()>/config.json``,
    and `get_app_dir()` is the REPO ROOT when running from source.

So isolation needs both: `monkeypatch.chdir(tmp_path)` for the data roots, and
a monkeypatched config path for `config.json` (the app rewrites it whenever the
mode dialog is confirmed or Settings are applied). Both are asserted as
preconditions in the `workspace` fixture, and a session-scoped guard
(`repo_state_untouched`) snapshots the repo's real ``data/``,
``Labeled_data/``, ``videos/`` and ``config.json`` before the first e2e test
and re-checks them after the last one.

`gui.resource_utils.asset_path` still resolves to the repo's ``src/resources``
tree — that is READ-ONLY asset loading (diagrams, zone masks) and is exactly
what the shipped app does.

TEST VIDEO
----------
A synthetic 10-frame 64x48 mp4 written once per session with `cv2.VideoWriter`.
The real ffmpeg-first extractor handles it in about a second, so the tests run
the genuine extraction path instead of pre-seeded frames, and the whole suite
still finishes in seconds. `videos/cat3.mp4` (let alone the 533 MB sample) is
never touched.
"""

import gc
import hashlib
import json
import os
import sys
import time
import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(__file__))  # make `gui_driver` importable

from gui_driver import pump  # noqa: E402

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# The owner's real, gitignored research data. Must be byte-identical afterwards.
_GUARDED_DIRS = ("data", "Labeled_data", "videos")
_GUARDED_FILES = ("config.json",)

# Far outside any plausible desktop, on every monitor arrangement.
OFFSCREEN_X, OFFSCREEN_Y = 6000, 6000

# Every interpreter handle this suite creates, parked for the whole session.
#
# Tk state is process-wide, and TEARING AN INTERPRETER DOWN AT AN ARBITRARY
# MOMENT breaks the NEXT one. `Tkapp_Dealloc` calls `Tcl_DeleteInterp`, which
# releases Tcl's process-wide library-path / encoding objects; when the
# generational GC happened to run that finalizer while `_tkinter.create()` was
# building the next root, construction failed with
# `Can't find a usable init.tcl in the following directories: ...` (a minimal
# two-test file with nothing but `tk.Tk()` reproduces it, so it is not this
# app's doing). Two cheap, complementary guards make it deterministic:
#
#   * park `application.tk` here so the interpreter is never deallocated at all
#     (the widget tree IS destroyed, so only a small Tkapp is retained), and
#   * `gc.collect()` immediately BEFORE constructing each root, so any finalizer
#     that is going to run does so at a harmless point.
#
# The keepalive alone was enough for plain `tk.Tk()`; the app also needs the
# explicit collect (it leaves ImageTk/PIL and ttk objects behind whose
# finalizers touch Tk), so both stay.
_INTERP_KEEPALIVE = []


# === window discipline ========================================================
def hide_window(window):
    """Make `window` invisible and park it off-screen (see module docstring).

    Best-effort: a window destroyed between construction and this call must not
    turn into a test error.
    """
    try:
        window.attributes("-alpha", 0.0)
    except tk.TclError as exc:  # pragma: no cover - WM without alpha support
        print(f"TEST: could not set alpha on {window}: {exc!r}")
    try:
        window.geometry(f"+{OFFSCREEN_X}+{OFFSCREEN_Y}")
    except tk.TclError as exc:  # pragma: no cover
        print(f"TEST: could not move {window} off-screen: {exc!r}")


# === isolation guard ==========================================================
def _repo_state_manifest():
    """Cheap fingerprint of the owner's real data roots.

    `frames/` folders hold hundreds of thousands of JPEGs, so they are recorded
    as (name, file count) instead of walked file by file — the app can only add
    frames to one, never silently rewrite them, and a stray project folder shows
    up as a new key regardless.
    """
    manifest = {}
    for name in _GUARDED_FILES:
        path = os.path.join(_REPO_ROOT, name)
        if os.path.exists(path):
            with open(path, "rb") as handle:
                manifest[name] = hashlib.sha256(handle.read()).hexdigest()
        else:
            manifest[name] = None
    for root in _GUARDED_DIRS:
        base = os.path.join(_REPO_ROOT, root)
        if not os.path.isdir(base):
            manifest[root] = None
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            rel = os.path.relpath(dirpath, _REPO_ROOT)
            if os.path.basename(dirpath) == "frames":
                dirnames[:] = []
                manifest[rel] = ("frames-dir", len(filenames))
                continue
            manifest[rel + "/"] = tuple(sorted(dirnames))
            for filename in sorted(filenames):
                path = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(path)
                    manifest[os.path.join(rel, filename)] = (stat.st_size, stat.st_mtime_ns)
                except OSError as exc:  # pragma: no cover - diagnostic only
                    manifest[os.path.join(rel, filename)] = ("stat-failed", repr(exc))
    return manifest


@pytest.fixture(scope="session", autouse=True)
def repo_state_untouched():
    """Prove the suite never touched the owner's real data or config."""
    before = _repo_state_manifest()
    yield
    after = _repo_state_manifest()
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    assert not (added or removed or changed), (
        "the GUI suite modified the repo's real data!\n"
        f"  added:   {added}\n  removed: {removed}\n  changed: {changed}"
    )


# === synthetic test video =====================================================
@pytest.fixture(scope="session")
def tiny_video(tmp_path_factory):
    """A 10-frame 64x48 25fps mp4, written once for the whole session."""
    import cv2
    import numpy as np

    path = tmp_path_factory.mktemp("tiny_video") / "tiny.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (64, 48))
    try:
        for index in range(10):
            frame = np.full((48, 64, 3), index * 20, dtype=np.uint8)
            cv2.putText(frame, str(index), (5, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            writer.write(frame)
    finally:
        writer.release()
    assert path.exists() and path.stat().st_size > 0, "could not write the test video"
    return str(path)


# The app is driven with the researcher's real diagram scale (0.5), so the
# click tests also cover the display -> data coordinate conversion.
DIAGRAM_SCALE = 0.5
TEST_CONFIG = {
    "diagram_scale": DIAGRAM_SCALE,
    "dot_size": 10.0,
    "new_template": False,
    "minimal_touch_length": 280,
    "parameter1": "Looking1",
    "parameter2": "P2",
    "parameter3": "P3",
    "limb_parameter1": "LP1",
    "limb_parameter2": "LP2",
    "limb_parameter3": "LP3",
    "video_downscale": 1.0,
    "jump_seconds": 0.28,
    # OFF so an arrow KeyPress is a deterministic single step instead of
    # starting the hold-to-play watchdog (that path has its own unit tests).
    "realtime_arrow_hold": False,
    "perf_enabled": False,
    "perf_log_every_s": 2.0,
    "perf_log_top_n": 10,
    "last_labeling_mode": "Normal",
}

# A data-space coordinate inside zone mask "I" (torso), verified against
# adapters.zone_masks.load_zone_masks + domain.touch.zones_at: a real click
# there must be recorded with Zones == [["I"]]. The canvas coordinate is the
# data coordinate times the diagram scale, which is exactly the conversion
# `LabelingApp.on_diagram_click` inverts.
ZONE_I_DATA_XY = (192, 336)
ZONE_I_NAME = "I"
ZONE_I_CANVAS_XY = (int(192 * DIAGRAM_SCALE), int(336 * DIAGRAM_SCALE))


class Workspace:
    """Per-test sandbox + the stubs for dialogs that cannot be synthesized."""

    def __init__(self, root, video):
        self.root = root                # tmp_path: CWD, data/, videos/, config.json
        self.video = video              # the synthetic source video (outside root)
        self.chosen_video = None        # what askopenfilename() will return
        self.mode = "Normal"
        self.messages = []              # (kind, title, text) for every messagebox
        self.answers = {}               # kind -> canned answer for ask* boxes
        self.opened_urls = []           # webbrowser.open() calls

    # --- convenience paths ---------------------------------------------------
    def project(self, name="tiny"):
        from domain.project import ProjectPaths

        return ProjectPaths(name)

    def errors(self):
        return [m for m in self.messages if m[0] == "showerror"]


@pytest.fixture
def workspace(tmp_path, tiny_video, monkeypatch):
    """Sandbox the app: CWD, config.json, dialogs, browser and global hotkeys."""
    from adapters import config

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(TEST_CONFIG, indent=2), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(config, "get_config_path", lambda: str(config_path))

    space = Workspace(tmp_path, tiny_video)

    # --- preconditions: if either of these is wrong the suite would write into
    # --- the owner's repo, so fail here rather than discover it in teardown.
    assert os.path.realpath(os.getcwd()) == os.path.realpath(str(tmp_path))
    assert os.path.realpath(config.get_config_path()) == os.path.realpath(str(config_path))

    # --- window discipline for the app's OWN modals --------------------------
    # The mode picker, the three progress windows and the close confirmation are
    # real Toplevels created by production code with a size-only geometry. Hide
    # each one at construction so nothing flashes onto the owner's desktop; the
    # dialogs still map, still grab, and are still driven for real.
    original_toplevel_init = tk.Toplevel.__init__

    def hidden_toplevel_init(self, *args, **kwargs):
        original_toplevel_init(self, *args, **kwargs)
        hide_window(self)

    monkeypatch.setattr(tk.Toplevel, "__init__", hidden_toplevel_init)

    # --- the OS file picker: no way to synthesize, so it is stubbed ----------
    from tkinter import filedialog, messagebox

    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kw: space.chosen_video or "")

    def _record(kind, default=None):
        def handler(title="", message="", **kwargs):
            space.messages.append((kind, title, message))
            print(f"TEST: messagebox.{kind}({title!r}): {message!r}")
            return space.answers.get(kind, default)
        return handler

    for kind, default in (("showerror", None), ("showwarning", None), ("showinfo", None)):
        monkeypatch.setattr(messagebox, kind, _record(kind, default))
    # askyesno defaults to "no" so a silent save failure can never be waved
    # through unnoticed: the close path would abort and the test would fail.
    monkeypatch.setattr(messagebox, "askyesno", _record("askyesno", False))

    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: space.opened_urls.append(url))

    # `save_note` fires a global Tab keystroke to leave the note field; that
    # would type into whatever window the developer has focused.
    import keyboard

    monkeypatch.setattr(keyboard, "press_and_release", lambda *a, **k: None)

    return space


# === the application ==========================================================
def _cancel_pending_after(application):
    """Cancel every scheduled `after()` callback still in Tcl's timer queue.

    A safety net for teardown only — the app cancels its OWN repeating timers in
    `_cancel_pending_timers()` (see test_tk_smoke). A test that fails halfway
    through never reaches `on_close`, and a leftover timer firing into a
    destroyed interpreter intermittently poisons creation of the NEXT one
    (`TclError: invalid command name "tcl_findLibrary"`).
    """
    try:
        for after_id in application.tk.eval("after info").split():
            try:
                application.after_cancel(after_id)
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover - already torn down
        print(f"TEST: could not drain the after queue: {exc!r}")


def _reset_ttkbootstrap():
    """Detach ttkbootstrap's process-wide state from the dead interpreter.

    `Style` is a per-process singleton and `Publisher` keeps a global registry
    of widgets to notify on a theme change. Left in place, the next root's
    `Style()` broadcasts `<<ThemeChanged>>` to widgets of the destroyed one.
    """
    try:
        from ttkbootstrap.style import Publisher, Style

        Publisher.clear_subscribers()
        Style.instance = None
    except Exception as exc:  # pragma: no cover
        print(f"TEST: could not reset ttkbootstrap state: {exc!r}")


def _teardown_app(application):
    """Stop the daemon threads' access to the app, then destroy the root.

    Every step is independently guarded: this runs from a fixture `finally`, so
    it must complete even when the test failed in the middle of a load.
    """
    try:
        application.play = False
        # The buffer/playback context providers return None once video is None,
        # so the two daemon threads stop touching Tk before the root goes away.
        application.video = None
        application.frame_buffer.shutdown(wait=False, cancel_futures=True)
    except Exception as exc:  # pragma: no cover - teardown best effort
        print(f"TEST: buffer shutdown during teardown failed: {exc!r}")
    try:
        if application.state_repo is not None:
            application.state_repo.close()
            application.state_repo = None
    except Exception as exc:  # pragma: no cover
        print(f"TEST: state repo close during teardown failed: {exc!r}")
    _cancel_pending_after(application)
    try:
        application.destroy()
    except Exception as exc:  # pragma: no cover - already destroyed by on_close
        print(f"TEST: destroy during teardown: {exc!r}")
    # Keep the interpreter handle alive for the rest of the session — see
    # _INTERP_KEEPALIVE. The widget tree is gone; only the Tkapp is retained.
    _INTERP_KEEPALIVE.append(application.tk)
    _reset_ttkbootstrap()


@pytest.fixture
def app_factory(workspace):
    """Build real `LabelingApp` roots; every one is torn down afterwards.

    A factory (not just a single fixture) because a test may need to destroy
    one root and construct a second one in the same sandbox. Keep the count
    minimal — one root per test unless the scenario genuinely needs two.
    """
    from gui import theme
    from labeling_app import LabelingApp

    built = []
    callback_errors = []

    def make():
        # `theme.dot_sprite` memoizes `ImageTk.PhotoImage` objects, which belong
        # to the Tk interpreter that created them. Production has exactly one
        # root for the process lifetime; a test that builds a second one must
        # drop the cache or the stale image commands raise TclError.
        theme._dot_cache.clear()
        # Force any pending Tk-related finalizer to run NOW, at a point where a
        # half-torn-down interpreter cannot matter. See _INTERP_KEEPALIVE: with
        # the collection left to the generational GC it landed in the middle of
        # `_tkinter.create()` below and the new root died with
        # `Can't find a usable init.tcl`. Deterministic either way now.
        gc.collect()
        application = LabelingApp()
        # Hide FIRST, resize second: build_ui already called geometry('1200x1000')
        # on-screen, and we must not let it appear even for one frame.
        hide_window(application)
        application.geometry(f"1200x1000+{OFFSCREEN_X}+{OFFSCREEN_Y}")

        def record_callback_exception(exc_type, exc_value, traceback_obj):
            import traceback as tb

            callback_errors.append(f"{exc_type.__name__}: {exc_value}")
            tb.print_exception(exc_type, exc_value, traceback_obj)

        # A Tk callback that raises is normally only PRINTED. Record them so a
        # broken binding or a timer firing into a half-torn-down widget fails
        # the test instead of scrolling past in the captured output.
        application.report_callback_exception = record_callback_exception
        pump(application, 0.15)  # realize geometry so <Configure> seeds the buffer
        built.append(application)
        return application

    make.callback_errors = callback_errors

    try:
        yield make
    finally:
        for application in reversed(built):
            _teardown_app(application)
        theme._dot_cache.clear()
    assert callback_errors == [], f"Tk callbacks raised during the test: {callback_errors}"


@pytest.fixture
def app(app_factory):
    """One real, running `LabelingApp` with no video loaded."""
    return app_factory()


@pytest.fixture
def loaded_app(app, workspace):
    """A real app with the synthetic video loaded through the Load Video button."""
    from gui_driver import load_video

    started = time.perf_counter()
    load_video(app, workspace, workspace.video)
    print(f"TEST: video load took {time.perf_counter() - started:.1f}s")
    return app
