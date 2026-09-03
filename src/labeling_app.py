import os
import time
import webbrowser
import logging
from contextlib import contextmanager
from threading import Thread, Event, get_ident, current_thread

import keyboard
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk

from adapters import config
from adapters import video_probe
from adapters.frame_buffer import (
    BufferContext,
    FrameBuffer,
    PlaybackContext,
    compute_play_step,
)
from adapters.frame_extractor import FrameExtractionCancelled, FrameExtractionError
from adapters.zone_masks import load_zone_masks
from domain.model import (
    FrameRecord,
    bundle_summary_str,
    preview_lines_for_save,
)
from domain.project import ProjectPaths
from domain.touch import NO_ZONE, find_last_open_onset, zones_at
from gui import theme
from gui.cloth_app import ClothApp, DEFAULT_CLOTH_DIAGRAM_SCALE
from gui.resource_utils import asset_path
from gui.ui_components import build_ui
from log_setup import open_logs_folder
from perf_utils import PerfLogger
from service_layer import analysis_service, annotation_service, project_service, save_service
from service_layer.project_service import LabelingTimer
from video_model import Video


logger = logging.getLogger(__name__)
annotation_logger = logging.getLogger("annot")


# =============================================================================
# Constants
# =============================================================================
# Realtime arrow-hold tuning.
# HOLD_START_DELAY_MS: how long the key must be held before realtime playback
# kicks in. The OS keyboard auto-repeat delay (typically ~500ms) is shorter,
# so we explicitly gate on elapsed time since the first KeyPress.
# HOLD_RELEASE_TIMEOUT_MS: gap with no auto-repeat KeyPress before we treat
# the key as released. OS auto-repeat fires ~every 30ms while held, so 100ms
# is a comfortable margin that responds quickly when the user lets go.
# HOLD_WATCHDOG_INTERVAL_MS: how often the watchdog polls. Set <= timeout/2
# so the actual stop-latency stays close to HOLD_RELEASE_TIMEOUT_MS.
HOLD_START_DELAY_MS = 500
HOLD_RELEASE_TIMEOUT_MS = 100
HOLD_WATCHDOG_INTERVAL_MS = 50
# Dev guard (H1): when True, the main render/timeline methods raise if called
# off the Tk main thread, so any future thread-boundary regression fails loudly
# at the offending call site instead of crashing Tcl intermittently.
DEBUG_ASSERT_UI_THREAD = False


# =============================================================================
# Standalone helpers
# =============================================================================
def center_over_parent(window, parent) -> None:
    """Place a realized dialog in the center of its parent window."""
    window.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - window.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - window.winfo_height()) // 2
    window.geometry(f"+{max(0, x)}+{max(0, y)}")


@contextmanager
def center_native_file_dialog(parent):
    """Center the Windows common file dialog that belongs to *parent*.

    Tk exposes no geometry option for its native Windows file picker.  A
    temporary WinEvent hook lets us position only the picker owned by this app;
    on non-Windows platforms the native Tk behavior remains untouched.
    """
    if os.name != "nt":
        yield
        return

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        event_object_show = 0x8002
        ga_rootowner = 3
        winevent_outofcontext = 0
        swp_nosize = 0x0001
        swp_nozorder = 0x0004
        swp_noactivate = 0x0010
        parent_handle = parent.winfo_id()
        process_id = os.getpid()

        callback_type = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            callback_type,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user32.UnhookWinEvent.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]

        def on_window_shown(_hook, _event, window, object_id, child_id, *_args):
            if object_id != 0 or child_id != 0:
                return
            if user32.GetAncestor(window, ga_rootowner) != parent_handle:
                return
            class_name = ctypes.create_unicode_buffer(256)
            if user32.GetClassNameW(window, class_name, len(class_name)) == 0:
                return
            if class_name.value != "#32770":
                return
            rect = wintypes.RECT()
            if not user32.GetWindowRect(window, ctypes.byref(rect)):
                return
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
            user32.SetWindowPos(
                window,
                None,
                max(0, x),
                max(0, y),
                0,
                0,
                swp_nosize | swp_nozorder | swp_noactivate,
            )

        callback = callback_type(on_window_shown)
        hook = user32.SetWinEventHook(
            event_object_show,
            event_object_show,
            None,
            callback,
            process_id,
            0,
            winevent_outofcontext,
        )
    except (AttributeError, OSError):
        yield
        return

    try:
        yield
    finally:
        user32.UnhookWinEvent(hook)


def custom_confirm_close(root) -> bool:
    win = tk.Toplevel(root)
    win.withdraw()
    win.title("Close Application")
    win.geometry("420x180")
    win.resizable(False, False)
    win.transient(root)
    win.configure(bg=theme.SURFACE)
    win.grab_set()  # makes it modal

    confirmed = False

    content = ttk.Frame(win, padding=16)
    content.pack(fill="both", expand=True)

    msg = ttk.Label(
        content,
        text="Do you want to close the application?\n\nYour progress will be saved.",
        font=theme.FONT_TITLE,
        justify="center",
        anchor="center",
        wraplength=350
    )
    msg.pack(expand=True, fill="both")

    btn_frame = ttk.Frame(content)
    btn_frame.pack(pady=(12, 0))

    def on_yes():
        nonlocal confirmed
        confirmed = True
        win.destroy()

    ttk.Button(
        btn_frame,
        text="OK",
        command=on_yes,
        style="Tool.TButton",
        takefocus=0,
    ).pack(side="left", padx=5)
    ttk.Button(
        btn_frame,
        text="Cancel",
        command=win.destroy,
        style="Tool.TButton",
        takefocus=0,
    ).pack(side="left", padx=5)
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    center_over_parent(win, root)
    win.deiconify()
    win.wait_window()
    return confirmed


def load_parameter_names_into(video_obj, par_buttons, limb_par_buttons):
    """
    Sets names onto the video object and updates the buttons' labels.
    par_buttons: dict {1: button, 2: button, 3: button}
    limb_par_buttons: dict {1: button, 2: button, 3: button}

    The config-reading half lives in adapters.config.load_parameter_labels();
    this keeps the Video-entity mutation + Tk button wiring (GUI side, to be
    absorbed by the service layer in the next refactor step).
    """
    labels = config.load_parameter_labels()
    p1 = labels['parameter1']
    p2 = labels['parameter2']
    p3 = labels['parameter3']

    video_obj.parameter1_name = p1
    video_obj.parameter2_name = p2
    video_obj.parameter3_name = p3
    if par_buttons.get(1):
        par_buttons[1].config(text=f"{p1}")
        theme.set_button_state(par_buttons[1], None)
    if par_buttons.get(2):
        par_buttons[2].config(text=f"{p2}")
        theme.set_button_state(par_buttons[2], None)
    if par_buttons.get(3):
        par_buttons[3].config(text=f"{p3}")
        theme.set_button_state(par_buttons[3], None)

    video_obj.limb_parameter1_name = labels['limb_parameter1']
    video_obj.limb_parameter2_name = labels['limb_parameter2']
    video_obj.limb_parameter3_name = labels['limb_parameter3']
    if limb_par_buttons.get(1):
        limb_par_buttons[1].config(text=f"{video_obj.limb_parameter1_name}")
        theme.set_button_state(limb_par_buttons[1], None)
    if limb_par_buttons.get(2):
        limb_par_buttons[2].config(text=f"{video_obj.limb_parameter2_name}")
        theme.set_button_state(limb_par_buttons[2], None)
    if limb_par_buttons.get(3):
        limb_par_buttons[3].config(text=f"{video_obj.limb_parameter3_name}")
        theme.set_button_state(limb_par_buttons[3], None)


# =============================================================================
# Main Application
# =============================================================================
class LabelingApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.style = theme.init_style(self)

        # Core state (was previously in __init__)
        self.video = None
        self.video_name = None
        self.minimal_touch_length = None
        self.NEW_TEMPLATE = False
        self.clothes_diagram_scale = DEFAULT_CLOTH_DIAGRAM_SCALE
        self._cloth_app = None
        # Working-state repository (state/<video>.db). Opened by load_video,
        # held for as long as that project is open, closed by _close_state_repo.
        # Tk-thread-bound by contract — never hand it to a worker thread.
        self.state_repo = None
        self.labeling_timer = LabelingTimer()
        self._zone_masks = []
        self._zone_dir = None
        self._closing = False
        self._frame_extraction_cancel = None

        # Config snapshot, loaded ONCE. build_ui and everything below read
        # from this AppConfig instead of re-reading config.json.
        self.config = config.load_app_config()

        # Build UI (creates frames, widgets, binds events; sets many attributes)
        build_ui(self)
        self._logged_limb = self.option_var_1.get()

        # Config flags that affect UI sizing & behavior
        self.NEW_TEMPLATE = self.config.new_template
        self.minimal_touch_length = self.config.minimal_touch_length
        logger.debug("new template: %s", self.NEW_TEMPLATE)
        logger.debug("minimal touch length: %s", self.minimal_touch_length)
        self.perf = PerfLogger(
            enabled=self.config.perf_enabled,
            log_every_s=self.config.perf_log_every_s,
            top_n=self.config.perf_log_top_n,
        )
        logger.debug("performance logging enabled: %s", self.config.perf_enabled)
        self.video_downscale = self.config.video_downscale
        logger.debug("video downscale: %s", self.video_downscale)
        self.jump_seconds = self.config.jump_seconds
        self.jump_frame_count = 7  # fallback until a video loads & framerate is known
        logger.debug("fast jump configured: %ss", self.jump_seconds)
        self._refresh_jump_label()

        # Realtime arrow-hold playback state (no KeyRelease bindings â€” OS keyboard
        # auto-repeat KeyPress events act as a heartbeat, polled by a watchdog).
        self.realtime_arrow_hold = self.config.realtime_arrow_hold
        logger.debug("realtime arrow hold: %s", self.realtime_arrow_hold)
        self.play_dir = 1                     # 1 = forward, -1 = backward (set by arrow-hold)
        self._arrow_held_dir = None           # currently-held arrow direction (1 / -1 / None)
        self._first_arrow_press_ms = 0.0      # time of the initial KeyPress (gates 1s hold delay)
        self._last_arrow_press_ms = 0.0       # heartbeat: time of most recent KeyPress
        self._hold_watchdog_id = None         # after() id for the release-detection watchdog
        self._hold_play_active = False        # True while arrow-hold-driven playback is running

        # Thread → UI boundary (H1). Workers never touch Tk widgets directly:
        # they advance plain state and schedule redraws via self.after(0, ...).
        self._ui_thread_ident = get_ident()   # Tk main thread (this __init__)
        self._display_w = 0                   # video_frame geometry, cached on the main
        self._display_h = 0                   # thread (workers must not call winfo_*)
        self._last_step_sign = 0              # +1 forward / -1 backward / 0 none

        # While True, _buffer_context/_playback_context return None so the two
        # worker threads idle. Held for the whole load_video swap: the workers
        # must not observe (or repaint from) a half-published video, and their
        # first tick for a new video only happens after load_video returned —
        # the same ordering the very first load has by construction.
        self._suspend_frame_workers = False

        # Frame buffer + playback engine (adapters.frame_buffer). The engine
        # owns the buffer lock/generation and the loader pool; every UI touch
        # is marshaled through the injected schedule_on_ui, and ALL writes to
        # video.current_frame happen on the Tk thread via _apply_play_advance.
        self.frame_buffer = FrameBuffer(
            schedule_on_ui=lambda fn: self.after(0, fn),
            on_status_change=self._on_buffer_status_change,
            get_buffer_context=self._buffer_context,
            get_playback_context=self._playback_context,
            apply_play_advance=self._apply_play_advance,
            on_playback_boundary=self._on_playback_boundary,
            on_playback_schedule_error=self._on_playback_schedule_error,
            on_priority_frame_loaded=self.display_first_frame,
            perf=self.perf,
        )
        self.background_thread = Thread(target=self.frame_buffer.background_update, daemon=True)
        self.background_thread_play = Thread(target=self.frame_buffer.background_update_play, daemon=True)

        # Diagram init
        self.init_diagram()
        # Timeline draw cache
        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._timeline_last_zone = None
        self._timeline_last_limb = None
        self._timeline2_last_limb = None
        self._timeline2_last_limb = None
        self._timeline_canvas_size = (0, 0)
        self._timeline2_canvas_size = (0, 0)
        self._timeline_playhead_id = None
        self._timeline2_playhead_id = None

    def _limb_param_key_for_index(self, idx: int) -> str:
        return f"Par{idx}"

    def _set_mode_button_states(self):
        has_video = self.video is not None
        state = tk.NORMAL if has_video else tk.DISABLED
        if getattr(self, "analysis_btn", None):
            self.analysis_btn.config(state=state)
        if getattr(self, "cloth_btn", None):
            self.cloth_btn.config(state=state)

    # === UI Rebuild & Annotation Controls =====================================
    def _reset_zone_cache(self):
        self._zone_masks = []
        self._zone_dir = None

    def _clear_frame_children(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def rebuild_annotation_controls(self):
        if not hasattr(self, "mode_controls_frame"):
            return

        self._clear_frame_children(self.mode_controls_frame)
        self._clear_frame_children(self.limb_parameter_frame)
        self.limb_par1_btn = None
        self.limb_par2_btn = None
        self.limb_par3_btn = None

        if getattr(self, "mode_param_label", None):
            self.mode_param_label.config(text="Parameters")
        if getattr(self, "mode_param_subtitle", None):
            self.mode_param_subtitle.config(text="(Limb-Specific)")
        ttk.Label(
            self.mode_controls_frame,
            text="Limb Selector",
            font=theme.FONT_BOLD,
        ).pack(anchor="n", pady=(5, 2))

        # Center the selector as one group while keeping labels easy to scan.
        limb_selector_frame = ttk.Frame(self.mode_controls_frame)
        limb_selector_frame.pack(anchor="n")
        for text, value in (
            ("Right Hand", "RH"),
            ("Left Hand", "LH"),
            ("Right Leg", "RL"),
            ("Left Leg", "LL"),
        ):
            ttk.Radiobutton(
                limb_selector_frame,
                text=text,
                variable=self.option_var_1,
                value=value,
                command=self._on_limb_selected,
                takefocus=0,
            ).pack(anchor="w")

        self.limb_par1_btn = ttk.Button(
            self.limb_parameter_frame,
            text="Limb Parameter 1",
            command=lambda: self.toggle_limb_parameter(1),
            width=15,
            style="StateNeutral.TButton",
            takefocus=0,
        )
        self.limb_par1_btn.pack(anchor="n", pady=4)
        self.limb_par2_btn = ttk.Button(
            self.limb_parameter_frame,
            text="Limb Parameter 2",
            command=lambda: self.toggle_limb_parameter(2),
            width=15,
            style="StateNeutral.TButton",
            takefocus=0,
        )
        self.limb_par2_btn.pack(anchor="n", pady=4)
        self.limb_par3_btn = ttk.Button(
            self.limb_parameter_frame,
            text="Limb Parameter 3",
            command=lambda: self.toggle_limb_parameter(3),
            width=15,
            style="StateNeutral.TButton",
            takefocus=0,
        )
        self.limb_par3_btn.pack(anchor="n", pady=4)

        self._set_mode_button_states()

    # === Data Bundle Management ================================================
    def _param_key_for_index(self, idx: int) -> str:
        return f"Par{idx}"

    def mark_bundle_changed(self, index=None):
        if self.video is None:
            return
        idx = self.video.current_frame if index is None else index

        b = self.video.frames.get(idx)
        if isinstance(b, dict):
            b["Changed"] = True
            self._timeline_dirty = True
            self._timeline2_dirty = True
            if hasattr(self, "notify_bundle_changed"):
                self.notify_bundle_changed(idx)

    def notify_bundle_changed(self, index=None):
        if self.video is None:
            return
        idx = self.video.current_frame if index is None else index
        try:
            b = self.video.frames[idx]
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("FrameBundle updated:\n%s", bundle_summary_str(b, frame_index=idx))
        except Exception:
            logger.warning("could not summarize bundle at frame %s", idx, exc_info=True)

    # === Navigation & Input Events =============================================
    def global_click(self, event):
        try:
            focus = self.focus_get()
        except Exception:
            focus = None
        if getattr(self, "note_entry", None) and focus == self.note_entry and event.widget != self.note_entry:
            self.focus_set()

    def _entry_has_focus(self):
        """True when the Note entry currently holds keyboard focus."""
        try:
            return getattr(self, "note_entry", None) is not None \
                and self.focus_get() is self.note_entry
        except Exception:
            return False

    def _set_loading_label_async(self, text: str, color: str):
        current = getattr(self, "_loading_label_state", None)
        new_state = (text, color)
        if current == new_state:
            return
        self._loading_label_state = new_state

        def _apply():
            if getattr(self, "loading_label", None):
                self.loading_label.set(color, text)

        try:
            self.after(0, _apply)
        except (RuntimeError, tk.TclError) as exc:
            # Called from the buffering thread, which can still be mid-tick when
            # on_close() destroys the root; `after` then raises "main thread is
            # not in main loop" and would kill the daemon with a traceback.
            logger.debug("buffer status update dropped during teardown: %s", exc)

    def _is_ui_thread(self) -> bool:
        """True when called on the Tk main thread (recorded in __init__)."""
        return get_ident() == self._ui_thread_ident

    def _assert_ui_thread(self):
        """Dev guard (H1): fail loudly on off-thread widget access.

        No-op unless DEBUG_ASSERT_UI_THREAD is enabled — zero cost in
        production, a hard RuntimeError at the offending call site in dev.
        """
        if DEBUG_ASSERT_UI_THREAD and not self._is_ui_thread():
            raise RuntimeError(
                f"Tk widget access from non-UI thread '{current_thread().name}' "
                "— marshal via self.after(0, ...)"
            )

    def navigate_left(self, event):  self._on_arrow_press(-1)
    def navigate_right(self, event): self._on_arrow_press(1)

    # --- Realtime arrow-hold playback ---------------------------------------
    # Strategy: bind ONLY KeyPress (no KeyRelease â€” those proved unreliable).
    # The OS keyboard auto-repeat fires KeyPress events ~every 30ms while a
    # key is physically held. We treat each KeyPress as a heartbeat:
    #
    #   - 1st KeyPress while idle: do a one-frame step (tap behavior).
    #   - Auto-repeat KeyPresses for the same direction: keep updating the
    #     heartbeat. Once the key has been held for HOLD_START_DELAY_MS (1s),
    #     start framerate-paced playback (matches the Play button's engine).
    #   - A watchdog timer polls every HOLD_WATCHDOG_INTERVAL_MS. If no
    #     KeyPress has arrived in HOLD_RELEASE_TIMEOUT_MS (100ms), the key
    #     has been released and we stop playback.
    def _on_arrow_press(self, direction):
        """KeyPress on Left (-1) or Right (+1)."""
        if self.video is None:
            return
        if not self.realtime_arrow_hold:
            self._request_buffered_step(direction)
            return

        now_ms = time.monotonic() * 1000.0

        if self._arrow_held_dir == direction:
            # Auto-repeat KeyPress â†’ user is holding. Update heartbeat and
            # gate playback on elapsed-since-first-press exceeding the 1s
            # hold threshold (OS auto-repeat starts much sooner, ~500ms).
            self._last_arrow_press_ms = now_ms
            if (not self._hold_play_active
                    and (now_ms - self._first_arrow_press_ms) >= HOLD_START_DELAY_MS):
                self._begin_hold_playback(direction)
            return

        # Fresh press. If the user was already holding the OPPOSITE direction
        # and we have an active hold-playback, kill it before switching.
        if self._hold_play_active:
            self.play = False
            self._hold_play_active = False
            self.play_dir = 1

        self._arrow_held_dir = direction
        self._first_arrow_press_ms = now_ms
        self._last_arrow_press_ms = now_ms
        self._request_buffered_step(direction)  # immediate one-frame step (tap behavior)
        self._ensure_hold_watchdog()

    def _ensure_hold_watchdog(self):
        if self._hold_watchdog_id is not None:
            return
        self._hold_watchdog_id = self.after(HOLD_WATCHDOG_INTERVAL_MS, self._hold_watchdog_tick)

    def _hold_watchdog_tick(self):
        self._hold_watchdog_id = None
        if self._arrow_held_dir is None:
            return
        now_ms = time.monotonic() * 1000.0
        if now_ms - self._last_arrow_press_ms > HOLD_RELEASE_TIMEOUT_MS:
            # No KeyPress events recently â†’ key has been released.
            direction = self._arrow_held_dir
            self._arrow_held_dir = None
            if self._hold_play_active:
                self.play = False
                self._hold_play_active = False
                self.play_dir = 1
                logger.debug("realtime hold released: direction=%s", direction)
            return
        # Still held â€” keep polling.
        self._hold_watchdog_id = self.after(HOLD_WATCHDOG_INTERVAL_MS, self._hold_watchdog_tick)

    def _begin_hold_playback(self, direction):
        if self._arrow_held_dir != direction or self.video is None:
            return
        self.play_dir = direction
        self.play = True
        self._hold_play_active = True
        if not self.play_thread_on:
            self.play_thread_on = True
            if not self.background_thread_play.is_alive():
                self.background_thread_play.start()
        logger.debug("realtime hold started: direction=%s fps=%s", direction, self.frame_rate)

    def _cancel_arrow_hold_state(self):
        """Force-stop any in-flight hold state. Used when arrow keys are unbound."""
        if self._hold_watchdog_id is not None:
            try: self.after_cancel(self._hold_watchdog_id)
            except Exception: pass
            self._hold_watchdog_id = None
        self._arrow_held_dir = None
        if self._hold_play_active:
            self.play = False
            self._hold_play_active = False
            self.play_dir = 1

    def _refresh_jump_label(self):
        label = getattr(self, "jump_label", None)
        if label is None:
            return
        if self.video is not None and getattr(self, "frame_rate", None):
            text = f"Jump: {self.jump_frame_count} frames ({self.jump_seconds}s)"
        else:
            text = f"Jump: {self.jump_seconds}s (load video to see frames)"
        label.config(text=text)

    def update_last_mouse_position(self, event):
        self.last_mouse_x = event.x
        self.last_mouse_y = event.y

    def on_resize(self, event):
        logger.debug("window resized to %sx%s", event.width, event.height)
        # Refresh the geometry cache the buffer thread reads instead of winfo_* (H1).
        self._display_w = event.width
        self._display_h = event.height
        self._buffer_reset()
        if self.video:
            self.display_first_frame()

    def on_mouse_wheel(self, event):
        if event.delta > 0 or getattr(event, "num", None) == 4:
            self._request_buffered_step(-1)
        elif event.delta < 0 or getattr(event, "num", None) == 5:
            self._request_buffered_step(1)

    def _request_buffered_step(self, delta):
        if self.video is None:
            return
        self._pending_buffer_step = delta
        # Hint the buffering thread to prioritize the jump target so the polling
        # loop in _buffered_step_tick picks it up on the next 50ms tick.
        target = max(0, min(self.video.total_frames, self.video.current_frame + delta))
        if target not in self.frame_buffer:
            self.frame_buffer.request_priority(target)
        self._buffered_step_tick()

    def _buffered_step_tick(self):
        if self.video is None:
            self._pending_buffer_step = None
            return
        delta = getattr(self, "_pending_buffer_step", None)
        if delta is None:
            return
        current_frame = self.video.current_frame
        next_frame = max(0, min(self.video.total_frames, current_frame + delta))
        if current_frame not in self.frame_buffer or next_frame not in self.frame_buffer:
            self.frame_buffer.buffer_ready = False
            self.after(50, self._buffered_step_tick)
            return
        self._pending_buffer_step = None
        self.next_frame(delta)

    def on_middle_click(self, event=None):
        # mouse position in display coords; convert to data coords using diagram_scale
        if event is None or isinstance(event, tk.Event):
            x_disp, y_disp = self.last_mouse_x, self.last_mouse_y
        else:
            x_disp, y_disp = event.x, event.y

        scale = getattr(self, "diagram_scale", 1.0)
        x_pos = x_disp * (1.0 / scale)
        y_pos = y_disp * (1.0 / scale)

        current_frame = self.video.current_frame
        option = self.option_var_1.get()

        # threshold in data coords (≈20 px in display); translates to 20/scale
        removed = annotation_service.remove_nearest_click(
            self.video.frames, current_frame, option, x_pos, y_pos,
            max_dist=20.0 / scale,
        )
        if removed:
            self.mark_bundle_changed()
            # Repaint immediately so the erased dot disappears without waiting
            # for the 300ms periodic_print_dot tick.
            self._render_diagram_dots()
            rec = self.video.frames[current_frame][option]
            annotation_logger.info(
                "f=%s %s delete points=%s",
                current_frame,
                option,
                len(rec.get("X", [])),
            )

    # === Diagram Init & Click Handling =========================================
    def init_diagram(self):
        # set up periodic dots refresh
        self._dot_refresh_after_id = None
        self._reset_zone_cache()
        self._load_zone_masks()
        self.periodic_print_dot()

    def _render_diagram_dots(self):
        """Repaint the diagram canvas with the current frame's dots + ghost.
        Single render pass â€” no scheduling. Safe to call from click handlers
        for instant visual feedback, and from the periodic poller as a fallback.
        """
        self.diagram_canvas.delete("all")
        self.on_radio_click()  # keeps same behavior for image & palette
        dot_size = getattr(self, "dot_size", 10)
        scale = getattr(self, "diagram_scale", 1.0)
        if self.video:
            sel = self.option_var_1.get()
            if sel == "RH":
                data = self.video.dataRH
            elif sel == "LH":
                data = self.video.dataLH
            elif sel == "RL":
                data = self.video.dataRL
            elif sel == "LL":
                data = self.video.dataLL
            else:
                data = {}
            self.find_last_green(data)
            frame_data: FrameRecord | dict = data.get(self.video.current_frame, {})
            xs = frame_data.get('X', []) if frame_data else []
            ys = frame_data.get('Y', []) if frame_data else []
            onset = frame_data.get('Onset', "OFF") if frame_data else "OFF"
            for x, y in zip(xs, ys):
                color = theme.DOT_ONSET if onset == "ON" else theme.DOT_OFFSET
                self.diagram_canvas.create_image(
                    x * scale,
                    y * scale,
                    image=theme.dot_sprite(color, dot_size),
                )
            array_xy = getattr(self.video, "last_green", [(None, None)])
            for (x_last, y_last) in array_xy:
                if x_last is not None:
                    self.diagram_canvas.create_image(
                        x_last * scale,
                        y_last * scale,
                        image=theme.dot_sprite(theme.DOT_ONSET, dot_size, hollow=True),
                    )

    def periodic_print_dot(self):
        self._render_diagram_dots()
        # Keep the id so the timer can be cancelled on close — otherwise the
        # reschedule below fires into an already-destroyed interpreter and Tcl
        # reports `invalid command name "...periodic_print_dot"` on every exit.
        self._dot_refresh_after_id = self.after(300, self.periodic_print_dot)

    def on_diagram_click(self, event, is_onset):
        if self.video is None:
            return
        onset = "ON" if is_onset else "OFF"
        display_scale = getattr(self, "diagram_scale", 1.0)
        x_pos = event.x * (1.0 / display_scale)
        y_pos = event.y * (1.0 / display_scale)
        zone_results = list(self.find_image_with_white_pixel(x_pos, y_pos))  # list for stability

        current_frame = self.video.current_frame
        option = self.option_var_1.get()

        # OBSERVABILITY: an unmatched click is not an error the app can fix, but
        # it silently enters the dataset as the NN sentinel (~0.1% of the canvas
        # matches no mask at all, plus anything outside the mask bounds). Say so
        # here, where the frame and limb are known, so the annotator can go back
        # and re-place the dot instead of finding NN rows after the study.
        if zone_results == [NO_ZONE]:
            logger.warning(
                "click hit no zone mask - recorded as '%s' frame=%s limb=%s "
                "onset=%s x=%.1f y=%.1f (diagram pixels); delete the dot and "
                "click further inside a zone",
                NO_ZONE,
                current_frame,
                option,
                onset,
                x_pos,
                y_pos,
            )

        setattr(self.video, f"is_touch{option}", True)

        rec = annotation_service.add_click(
            self.video.frames, current_frame, option, x_pos, y_pos, onset, zone_results
        )

        self.mark_bundle_changed()
        # Repaint immediately so the dot appears without waiting for the
        # 300ms periodic_print_dot tick.
        self._render_diagram_dots()

        annotation_logger.info(
            "f=%s %s click %s zones=%s points=%s",
            current_frame,
            option,
            rec.get("Onset"),
            zone_results,
            len(rec.get("X", [])),
        )

    def preview_before_save(self, changed_only: bool = True):
        """
        Print a compact preview of what would be saved right now.
        Shows base/state/export dirs and per-frame summaries.
        """
        if not self.video:
            logger.debug("save preview skipped: no video loaded")
            return

        paths = ProjectPaths(self.video_name)
        lines = preview_lines_for_save(self.video.frames, self.video.total_frames, changed_only=changed_only)
        logger.debug(
            "save preview: video_dir=%s state_db=%s export_csv=%s changed=%s%s",
            paths.video_dir,
            paths.state_db,
            paths.export_csv,
            len(lines),
            "\n" + "\n".join(lines) if lines else "",
        )
    
    def find_last_green(self, _unused_data=None):
        """
        Set self.video.last_green to the last 'ON' points for the selected limb
        at or before the current frame; clear when an 'OFF' is encountered first.
        The pure backward scan lives in domain.touch.find_last_open_onset.
        """
        if not (self.video and isinstance(self.video.frames, dict)):
            self.video.last_green = [(None, None)]
            return

        limb = self.option_var_1.get()  # "LH"/"RH"/"LL"/"RL"
        self.video.last_green = find_last_open_onset(
            self.video.frames, limb, self.video.current_frame
        )

    def _on_limb_selected(self):
        selected = self.option_var_1.get()
        previous = getattr(self, "_logged_limb", None)
        if previous is not None and previous != selected:
            annotation_logger.info("limb %s -> %s", previous, selected)
        self._logged_limb = selected
        self.on_radio_click()

    def on_radio_click(self):
        expected_dir = asset_path("icons/zones3_new_template" if self.NEW_TEMPLATE else "icons/zones3")
        if getattr(self, "_zone_dir", None) != expected_dir:
            self._reset_zone_cache()
            self._load_zone_masks()
        if self.option_var_1.get() == "RH":
            image_path = asset_path("icons/RH_new_template.png" if self.NEW_TEMPLATE else "icons/RH.png")
        elif self.option_var_1.get() == "LH":
            image_path = asset_path("icons/LH_new_template.png" if self.NEW_TEMPLATE else "icons/LH.png")
        elif self.option_var_1.get() == "RL":
            image_path = asset_path("icons/RL_new_template.png" if self.NEW_TEMPLATE else "icons/RL.png")
        else:  # LL
            image_path = asset_path("icons/LL_new_template.png" if self.NEW_TEMPLATE else "icons/LL.png")

        scale = getattr(self, "diagram_scale", 1.0)
        # The periodic dot refresh repaints through here every 300ms; cache the
        # decoded+resized PhotoImage per (path, scale) so the repaint does not
        # re-read the PNG from disk on every tick.
        cache = getattr(self, "_diagram_photo_cache", None)
        if cache is None:
            cache = self._diagram_photo_cache = {}
        photo = cache.get((image_path, scale))
        if photo is None:
            with Image.open(image_path) as img:
                resized = img.resize(
                    (int(img.width * scale), int(img.height * scale)), Image.LANCZOS
                )
            photo = cache[(image_path, scale)] = ImageTk.PhotoImage(resized)
        self.photo = photo
        self.diagram_canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.draw_timeline()
        self.draw_timeline2()
        self.update_limb_parameter_buttons()

    # === Zone Masks & Lookups ==================================================
    def _load_zone_masks(self):
        directory = asset_path("icons/zones3_new_template" if self.NEW_TEMPLATE else "icons/zones3")
        if getattr(self, "_zone_dir", None) == directory and getattr(self, "_zone_masks", None):
            return
        self._zone_dir = directory
        self._zone_masks = load_zone_masks(directory)

    def find_image_with_white_pixel(self, x, y):
        # NOTE: historically misleading name — the masks are BLACK shapes on
        # white, so a hit is pixel == 0. domain.touch.zones_at owns the rule:
        # overlapping masks resolve by precedence (real zone > BOX* > OUTSIDE >
        # LINE) and a miss is the ['NN'] sentinel the exports rely on.
        with self.perf.time("find_image_with_white_pixel"):
            if not getattr(self, "_zone_masks", None):
                self._load_zone_masks()
            return zones_at(self._zone_masks, x, y)

    # === Timelines =============================================================
    def on_timeline_click(self, event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline_canvas.winfo_width()
            frame_number = int(click_position / canvas_width * self.video.number_frames_in_zone)
            if self.video.total_frames >= frame_number + self.video.number_frames_in_zone * self.video.current_frame_zone:
                self.video.current_frame = frame_number + self.video.number_frames_in_zone * self.video.current_frame_zone
                self.display_first_frame()
            else:
                logger.error("invalid frame number")

    def on_timeline2_click(self, event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline2_canvas.winfo_width()
            new_frame = int((click_position / canvas_width) * self.video.total_frames)
            self.video.current_frame = new_frame
            self.video.current_frame_zone = new_frame // self.video.number_frames_in_zone
            logger.debug("jumping to exact frame: %s", new_frame)
            self.display_first_frame()

    def parameter_color_at_frame(self, frame):
        b = self.video.frames.get(frame, {}) if self.video else {}
        params = (b.get("Params") or {})
        # If any param ON => green; else if any OFF => red; else None
        if any(v == "ON" for v in params.values()): return theme.TL_ONSET_MARK
        if any(v == "OFF" for v in params.values()): return theme.TL_OFFSET_MARK
        return None

    @staticmethod
    def _update_timeline_playhead(canvas, item_ids, x, top, bottom):
        """Create or move the shared 2 px playhead stem and triangle cap."""
        cap_height = 7
        stem_top = min(bottom, top + cap_height)
        line_coords = (x, stem_top, x, bottom)
        cap_coords = (x - 4, top, x + 4, top, x, stem_top)
        if item_ids is None:
            line_id = canvas.create_line(
                *line_coords,
                fill=theme.PLAYHEAD,
                width=2,
            )
            cap_id = canvas.create_polygon(
                *cap_coords,
                fill=theme.PLAYHEAD,
                outline="",
            )
            return line_id, cap_id

        line_id, cap_id = item_ids
        canvas.coords(line_id, *line_coords)
        canvas.coords(cap_id, *cap_coords)
        canvas.tag_raise(line_id)
        canvas.tag_raise(cap_id)
        return item_ids

    def draw_timeline(self):
        self._assert_ui_thread()
        with self.perf.time("draw_timeline"):
            if not (self.video and self.video.total_frames > 0):
                return
            canvas_width = self.timeline_canvas.winfo_width()
            canvas_height = self.timeline_canvas.winfo_height()
            limb = self.option_var_1.get()
            zone = self.video.current_frame_zone
            needs_full = (
                self._timeline_dirty
                or self._timeline_last_zone != zone
                or self._timeline_last_limb != limb
                or self._timeline_canvas_size != (canvas_width, canvas_height)
            )

            left_edge = 1
            right_edge = max(left_edge + 1, canvas_width - 2)
            top = 1
            bottom = max(top + 1, canvas_height - 2)
            drawable_width = right_edge - left_edge
            sector_width = drawable_width / self.video.number_frames_in_zone if self.video.number_frames_in_zone else 1
            offset = self.video.number_frames_in_zone * zone

            if needs_full:
                self.timeline_canvas.delete("all")
                data_source = {
                    'RH': self.video.dataRH, 'LH': self.video.dataLH, 'RL': self.video.dataRL, 'LL': self.video.dataLL
                }
                data = data_source.get(limb, {})
                self.is_touch_timeline = False if zone == 0 else self.video.touch_to_next_zone[zone]

                def get_color(frame_idx, data):
                    if frame_idx > self.video.total_frames: return theme.TL_UNAVAILABLE
                    details = data.get(frame_idx, {})
                    xs = details.get('X', [])
                    if not xs:
                        return self.color_during if self.is_touch_timeline else theme.TL_EMPTY
                    if len(xs) >= 1 and xs[0] is not None:
                        if details.get('Onset') == 'ON':
                            self.is_touch_timeline = True; return theme.TL_ON
                        else:
                            self.is_touch_timeline = False; return theme.TL_OFF
                    return self.color_during if self.is_touch_timeline else theme.TL_EMPTY

                for frame in range(self.video.number_frames_in_zone):
                    left = left_edge + frame * sector_width
                    right = left + sector_width
                    frame_offset = frame + offset
                    color = get_color(frame_offset, data)
                    self.timeline_canvas.create_rectangle(
                        left, top, right, bottom, fill=color, outline=""
                    )
                    if color == theme.TL_UNAVAILABLE:
                        self.timeline_canvas.create_line(
                            left + 2,
                            bottom - 2,
                            right - 2,
                            top + 2,
                            fill=theme.TL_UNAVAILABLE_MARK,
                            width=1,
                        )
                    param_color = self.parameter_color_at_frame(frame_offset)
                    if param_color is not None:
                        mid_x = (left + right) / 2
                        self.timeline_canvas.create_line(mid_x, top, mid_x, bottom, fill=param_color, width=2)

                    # NEW: per-limb ticks for Param1..3 on this frame
                    colors = self.limb_parameter_colors_at_frame(frame_offset)
                    mid_x = (left + right) / 2
                    offsets = (-2, 0, 2)
                    for col, dx in zip(colors, offsets):
                        if col:
                            self.timeline_canvas.create_line(mid_x + dx, top, mid_x + dx, bottom, fill=col, width=2)

                    if frame == self.video.number_frames_in_zone - 1:
                        if self.video.current_frame_zone + 1 < len(self.video.touch_to_next_zone):
                            self.video.touch_to_next_zone[self.video.current_frame_zone + 1] = (color == self.color_during)
                        elif self.video.current_frame_zone + 1 == len(self.video.touch_to_next_zone):
                            self.video.touch_to_next_zone.append(color == self.color_during)

                # keep the original extra ticks behavior
                colors = self.limb_parameter_colors_at_frame(frame_offset)
                mid_x = (left + right) / 2
                offsets = (-2, 0, 2)  # horizontal pixel offsets for Param1..3
                for col, dx in zip(colors, offsets):
                    if col:
                        self.timeline_canvas.create_line(mid_x + dx, top, mid_x + dx, bottom, fill=col, width=2)

                # Draw one shared grid over borderless fills; shared edges stay 1 px.
                self.timeline_canvas.create_rectangle(
                    left_edge,
                    top,
                    right_edge,
                    bottom,
                    fill="",
                    outline=theme.TL_OUTLINE,
                )
                for frame in range(1, self.video.number_frames_in_zone):
                    x = left_edge + frame * sector_width
                    self.timeline_canvas.create_line(
                        x,
                        top,
                        x,
                        bottom,
                        fill=theme.TL_OUTLINE,
                        width=1,
                    )

                self._timeline_dirty = False
                self._timeline_last_zone = zone
                self._timeline_last_limb = limb
                self._timeline_canvas_size = (canvas_width, canvas_height)
                self._timeline_playhead_id = None

            # Update playhead only
            frame_in_zone = self.video.current_frame - offset
            if 0 <= frame_in_zone < self.video.number_frames_in_zone:
                current_pos = left_edge + (frame_in_zone + 0.5) * sector_width
                current_pos = min(max(current_pos, left_edge + 4), right_edge - 4)
                self._timeline_playhead_id = self._update_timeline_playhead(
                    self.timeline_canvas,
                    self._timeline_playhead_id,
                    current_pos,
                    top,
                    bottom,
                )

    def draw_timeline2(self):
        self._assert_ui_thread()
        with self.perf.time("draw_timeline2"):
            if not (self.video and self.video.total_frames > 0):
                return

            canvas_width  = self.timeline2_canvas.winfo_width()
            canvas_height = self.timeline2_canvas.winfo_height()
            left_edge = 1
            right_edge = max(left_edge + 1, canvas_width - 2)
            top = 1
            bottom = max(top + 1, canvas_height - 2)
            drawable_width = right_edge - left_edge
            limb = self.option_var_1.get()  # currently selected limb ("LH","RH","LL","RL")
            needs_full = (
                self._timeline2_dirty
                or self._timeline2_last_limb != limb
                or self._timeline2_canvas_size != (canvas_width, canvas_height)
            )

            if needs_full:
                self.timeline2_canvas.delete("all")
                self.timeline2_canvas.create_rectangle(
                    left_edge,
                    top,
                    right_edge,
                    bottom,
                    fill=theme.TL_EMPTY,
                    outline=theme.TL_OUTLINE,
                )
                # --- First pass: collect touch intervals and all lines to draw later ---
                on_lines   = []      # x positions of On (green)
                off_lines  = []      # x positions of Off (red)
                intervals  = []      # [(x_on, x_off), ...]
                param_lines = []     # [(x, color)] from GLOBAL parameter_color_at_frame
                limb_param_lines = []  # [(x+dx, color)] from limb_parameter_colors_at_frame
                active_on_x = None

                # Deterministic left->right scan across frames that exist in memory
                for frame in sorted(self.video.frames.keys()):
                    bundle = self.video.frames.get(frame, {})
                    details = bundle.get(limb, {}) if isinstance(bundle, dict) else {}

                    x = left_edge + (frame / self.video.total_frames) * drawable_width

                    # --- Onset/Offset collection for intervals + edge markers (SELECTED LIMB ONLY) ---
                    onset_val = details.get('Onset')
                    if onset_val == 'ON':
                        on_lines.append(x)
                        if active_on_x is None:
                            active_on_x = x
                    elif onset_val == 'OFF':
                        off_lines.append(x)
                        if active_on_x is not None:
                            x1, x2 = (active_on_x, x) if active_on_x <= x else (x, active_on_x)
                            if abs(x2 - x1) >= 1:
                                intervals.append((x1, x2))
                            active_on_x = None

                    # --- GLOBAL Parameter markers (non-limb) â€” always visible ---
                    col = self.parameter_color_at_frame(frame)
                    if col is not None:
                        param_lines.append((x, col))

                    # --- Limb-specific parameter ticks (only when a limb is selected) ---
                    #      small +/-2px offsets so the three limb-params can be distinguished
                    if limb in ("LH", "RH", "LL", "RL") and hasattr(self, "limb_parameter_colors_at_frame"):
                        colors = self.limb_parameter_colors_at_frame(frame)  # returns up to 3 colors or None
                        for dx, c in zip((-2, 0, 2), colors):
                            if c:
                                limb_param_lines.append((x + dx, c))

                # --- Draw order: 1) fills, 2) global & limb param lines, 3) On/Off edges, 4) playhead ---
                # 1) Pale-green touch interval fills
                for x1, x2 in intervals:
                    # Leave a visible gap between the fill and semantic edge ticks.
                    if x2 - x1 > 6:
                        self.timeline2_canvas.create_rectangle(
                            x1 + 3,
                            top + 1,
                            x2 - 3,
                            bottom - 1,
                            fill=theme.TL_DURING,
                            outline="",
                        )

                # 2) Global parameter lines
                for x, c in param_lines:
                    self.timeline2_canvas.create_line(x, top, x, bottom, fill=c, width=2)

                #    Limb-specific parameter ticks for the selected limb
                for x, c in limb_param_lines:
                    self.timeline2_canvas.create_line(x, top, x, bottom, fill=c, width=2)

                # 3) On/Off edge markers
                for x in on_lines:
                    self.timeline2_canvas.create_line(
                        x, top, x, bottom, fill=theme.TL_ONSET_MARK, width=2
                    )
                for x in off_lines:
                    self.timeline2_canvas.create_line(
                        x, top, x, bottom, fill=theme.TL_OFFSET_MARK, width=2
                    )

                self._timeline2_dirty = False
                self._timeline2_last_limb = limb
                self._timeline2_canvas_size = (canvas_width, canvas_height)
                self._timeline2_playhead_id = None

            # Current frame indicator only
            current_pos = left_edge + (
                self.video.current_frame / self.video.total_frames
            ) * drawable_width
            current_pos = min(max(current_pos, left_edge + 4), right_edge - 4)
            self._timeline2_playhead_id = self._update_timeline_playhead(
                self.timeline2_canvas,
                self._timeline2_playhead_id,
                current_pos,
                top,
                bottom,
            )
    
    def update_frame_counter(self):
        if self.video:
            current_frame_text = f"{self.video.current_frame} / {self.video.total_frames}"
            self.frame_counter_label.config(text=current_frame_text)

            def format_time(ms):
                hours, remainder = divmod(ms, 3600000)
                minutes, remainder = divmod(remainder, 60000)
                seconds, milliseconds = divmod(remainder, 1000)
                if hours > 0:
                    return f"{hours}:{minutes:02}:{seconds:02}.{milliseconds:03}"
                else:
                    return f"{minutes}:{seconds:02}.{milliseconds:03}"

            if self.video.frame_rate:
                current_time = self.video.current_frame / self.video.frame_rate * 1000
                total_time = self.video.total_frames / self.video.frame_rate * 1000
                self.time_counter_label.config(text=f"{format_time(int(current_time))} / {format_time(int(total_time))}")
            else:
                # FPS probe returned 0/None for this container; time is unknowable.
                self.time_counter_label.config(text="--:-- / --:--")

        else:
            self.frame_counter_label.config(text="0 / 0")

        self.video.current_frame_zone = int(self.video.current_frame / self.video.number_frames_in_zone)

    def _format_duration(self, seconds):
        if seconds is None:
            return "--:--"
        seconds = int(max(0, seconds))
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02}:{secs:02}"
        return f"{minutes}:{secs:02}"

    def _open_frame_progress_window(self):
        return self._open_progress_window("Preparing Frames", "Preparing frames...")

    def _open_video_copy_progress_window(self):
        return self._open_progress_window("Copying Video", "Copying video to project...")

    def _open_data_progress_window(self):
        return self._open_progress_window("Loading Data", "Loading labeled data...")

    def _open_progress_window(self, title, heading):
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("520x180")
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=theme.SURFACE)

        content = ttk.Frame(win, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text=heading, font=theme.FONT_TITLE).pack(pady=(0, 6))
        status = ttk.Label(content, text="Starting...", font=theme.FONT_BASE)
        status.pack()
        bar = ttk.Progressbar(
            content,
            mode="determinate",
            length=460,
            bootstyle="success-striped",
        )
        bar.pack(pady=8)
        time_label = ttk.Label(
            content,
            text="Elapsed: 0:00 | ETA: --:--",
            font=theme.FONT_SMALL,
        )
        time_label.pack()
        win.update_idletasks()

        def update(count, total, stage, elapsed_s):
            try:
                if not win.winfo_exists():
                    return
                total = max(1, int(total))
                count = min(int(count), total)
                bar["maximum"] = total
                bar["value"] = count
                pct = (count / total) * 100 if total else 0
                status.config(text=f"{stage}: {count} / {total} ({pct:.1f}%)")
                eta_s = None
                if count > 0:
                    rate = elapsed_s / count
                    eta_s = max(0.0, (total - count) * rate)
                time_label.config(
                    text=f"Elapsed: {self._format_duration(elapsed_s)} | ETA: {self._format_duration(eta_s)}"
                )
                win.update_idletasks()
                win.update()
            except tk.TclError:
                pass

        def close():
            try:
                if win.winfo_exists():
                    win.destroy()
            except tk.TclError:
                # The root may have been destroyed while update() was pumping
                # events for a long-running operation.
                pass

        return update, close

    def _run_export_with_progress(self, export_fn):
        """Run a full export off the Tk thread while a modal stays responsive."""
        self._assert_ui_thread()
        win = tk.Toplevel(self)
        win.title("Saving Data")
        win.geometry("520x160")
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=theme.SURFACE)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        content = ttk.Frame(win, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Building full export...", font=theme.FONT_TITLE).pack(
            pady=(2, 8)
        )
        status = ttk.Label(content, text="Writing export snapshot", font=theme.FONT_BASE)
        status.pack()
        bar = ttk.Progressbar(
            content,
            mode="indeterminate",
            length=460,
            bootstyle="success-striped",
        )
        bar.pack(pady=10)
        bar.start(12)
        win.update_idletasks()

        started = time.perf_counter()
        result = {}
        done = Event()

        def worker():
            try:
                export_fn()
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        Thread(target=worker, name="data-export", daemon=True).start()

        def poll_worker():
            if done.is_set():
                bar.stop()
                win.destroy()
                return
            elapsed = time.perf_counter() - started
            status.config(text=f"Writing export snapshot - elapsed {self._format_duration(elapsed)}")
            win.after(100, poll_worker)

        win.after(100, poll_worker)
        win.wait_window()
        if "error" in result:
            raise result["error"]

    def _prepare_video_copy(self, source_path):
        """Ensure the video lives inside the project videos folder; returns the
        path to use, or None when the copy failed. The decision is the
        service's; the progress window and warnings are ours."""
        dest_path, action, size_mismatch = project_service.plan_video_copy(source_path)

        if action == "in_place":
            return dest_path

        if action == "existing":
            if size_mismatch:
                messagebox.showwarning(
                    "Video Copy Skipped",
                    "A video with the same name already exists in the videos folder.\n"
                    "Using the existing copy to avoid overwriting."
                )
            return dest_path

        progress_update, progress_close = self._open_video_copy_progress_window()
        try:
            project_service.copy_file_with_progress(source_path, dest_path, progress_update)
        except Exception as exc:
            logger.exception("failed to copy video to %s", dest_path)
            messagebox.showerror("Video Copy Failed", f"Failed to copy video:\n{exc}")
            return None
        finally:
            progress_close()

        logger.info("copied video to %s", dest_path)
        return dest_path

    # === Frame Buffer Boundary (GUI side of adapters.frame_buffer) =============
    def _buffer_context(self):
        """Per-tick state snapshot for the buffering thread, built on demand.

        Workers must NOT touch Tk widgets (H1), so instead of winfo_width/height
        this hands over the geometry cache maintained on the main thread
        (on_resize + load_video).
        """
        if self._suspend_frame_workers or self.video is None or self.video.frames_dir is None:
            return None
        return BufferContext(
            frames_dir=self.video.frames_dir,
            current_frame=self.video.current_frame,
            total_frames=self.video.total_frames,
            display_w=self._display_w,
            display_h=self._display_h,
            downscale=float(getattr(self, "video_downscale", 1.0) or 1.0),
            jump_frame_count=max(1, getattr(self, "jump_frame_count", 1)),
            last_step_sign=self._last_step_sign,
        )

    def _playback_context(self):
        """Per-tick state snapshot for the playback thread."""
        if self._suspend_frame_workers or self.video is None:
            return None
        return PlaybackContext(
            playing=bool(self.play),
            direction=1 if getattr(self, "play_dir", 1) >= 0 else -1,
            current_frame=self.video.current_frame,
            total_frames=self.video.total_frames,
            frame_rate=self.frame_rate,
        )

    def _on_buffer_status_change(self, loaded: bool):
        """Buffer status pill (the setter dedupes and marshals to the Tk thread)."""
        if loaded:
            self._set_loading_label_async("Loaded", theme.STATUS_OK)
        else:
            self._set_loading_label_async("Loading", theme.STATUS_BAD)

    def _apply_play_advance(self, next_frame, direction):
        """Apply one playback advance ON THE TK THREAD (single-writer rule).

        The playback worker only REQUESTS the advance (via schedule_on_ui), so
        every write to video.current_frame happens here or in the other
        UI-thread writers (next_frame, timeline clicks, select_frame) — never
        concurrently from two threads.
        """
        if self.video is None:
            logger.debug("play advance skipped: no video (shutdown/reload)")
            return
        self.video.current_frame = next_frame
        self._last_step_sign = direction
        try:
            self.display_first_frame()
            self.draw_timeline2()
            if next_frame % 10 == 0:
                self.draw_timeline()
        except tk.TclError as exc:
            # Expected only when the app is being torn down mid-playback.
            logger.debug("play advance redraw aborted during teardown: %s", exc)

    def _on_playback_boundary(self, current_frame, direction):
        """Playback hit the edge it was moving toward — stop cleanly."""
        self.play = False
        self._hold_play_active = False
        self.play_dir = 1
        logger.debug("playback stopped at boundary: frame=%s direction=%s", current_frame, direction)

    def _on_playback_schedule_error(self, exc):
        """UI marshaling failed — the Tk mainloop is gone (close mid-playback)."""
        self.play = False
        logger.debug("playback redraw scheduling stopped: Tk shutting down: %s", exc)

    @staticmethod
    def _compute_play_step(current_frame, total_frames, direction):
        """Pure play-step decision: (next_frame, stop). No Tk, no side effects.
        Lives in adapters.frame_buffer.compute_play_step; kept here as the
        stable entry point the H1 tests pin."""
        return compute_play_step(current_frame, total_frames, direction)

    def _buffer_reset(self):
        """Drop the whole buffer and bump its generation (see FrameBuffer.reset)."""
        self.frame_buffer.reset()

    # === Frame Display =========================================================
    def display_first_frame(self, frame_number=None):
        self._assert_ui_thread()
        with self.perf.time("display_first_frame"):
            if frame_number is None:
                frame_number = self.video.current_frame
            else:
                self.video.current_frame = frame_number
            if frame_number < 0 or frame_number > self.video.total_frames:
                logger.error("frame number out of bounds: %s", frame_number)
                return
            pil_img = self.frame_buffer.get(frame_number)
            if pil_img is not None:
                with self.perf.time("display_frame_photo"):
                    photo_img = ImageTk.PhotoImage(pil_img)
                if hasattr(self, 'frame_label') and self.frame_label:
                    self.frame_label.configure(image=photo_img)
                else:
                    self.frame_label = tk.Label(
                        self.video_frame,
                        image=photo_img,
                        bg=theme.VIDEO_BG,
                        bd=0,
                        highlightbackground=theme.BORDER,
                        highlightcolor=theme.BORDER,
                        highlightthickness=2,
                    )
                    self.frame_label.pack(expand=True)
                self.loading_label.set(theme.STATUS_OK, "Loaded")
                self.image = photo_img
            else:
                logger.debug("frame not in buffer: %s", frame_number)
                self.loading_label.set(theme.STATUS_BAD, "Loading")

            self.update_note_entry()
            self.update_frame_counter()
            self.update_limb_parameter_buttons()
            self.update_button_colors()

    # === Labeling-time accumulator (service_layer.project_service) =============
    def _current_video_time_s(self) -> float:
        return self.labeling_timer.current_s()

    def _persist_video_time(self):
        """Checkpoint the accumulator, session keeps running."""
        if self.video is None or self.state_repo is None:
            return
        self.labeling_timer.persist(self.state_repo)

    def _finalize_video_time(self):
        """Checkpoint the accumulator and end the session."""
        if self.video is None or self.state_repo is None:
            return
        self.labeling_timer.finalize(self.state_repo)

    def _stop_video_timer_if_any(self):
        """Stop the labeling-time timer without persisting, discarding the
        current session's elapsed time. Used when a load aborts after the
        timer was already started (e.g. frame extraction failed)."""
        self.labeling_timer.cancel_session()

    # === Parameter Toggles & Coloring ==========================================
    def update_button_colors(self):
        if self.video is None:
            return
        idx = self.video.current_frame
        b = self.video.frames.get(idx, {})
        params = (b.get("Params") or {})

        for i, btn in ((1, self.par1_btn), (2, self.par2_btn), (3, self.par3_btn)):
            key = self._param_key_for_index(i)
            state = params.get(key)
            theme.set_button_state(btn, state)

    def parameter_dic_insert(self, parameter_index: int):
        """Toggle Param_i (1..3) for the CURRENT frame directly on the bundle."""
        if self.video is None:
            return
        idx = self.video.current_frame
        new_state = annotation_service.toggle_global_param(
            self.video.frames, idx, parameter_index
        )

        # color the right button immediately
        button = {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn}[parameter_index]
        theme.set_button_state(button, new_state)

        # mark frame dirty, print, and refresh timeline
        self.mark_bundle_changed(idx)
        self.draw_timeline()
        annotation_logger.info("f=%s param P%s -> %s", idx, parameter_index, new_state)

    def toggle_limb_parameter(self, param_number: int):
        limb = self.option_var_1.get()
        frame = self.video.current_frame
        new_state = annotation_service.toggle_limb_param(
            self.video.frames, frame, limb, param_number
        )

        # reflect on button color
        btn = {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn}[param_number]
        theme.set_button_state(btn, new_state)

        # mark & redraw (so timeline updates)
        self.mark_bundle_changed(frame)
        self.draw_timeline()
        annotation_logger.info(
            "f=%s %s limbparam LP%s -> %s", frame, limb, param_number, new_state
        )

    def update_limb_parameter_buttons(self):
        if not self.video:
            return
        limb = self.option_var_1.get()
        frame = self.video.current_frame
        b = self.video.frames.get(frame, {})
        rec = b.get(limb, {}) if isinstance(b, dict) else {}
        limb_params = rec.get("LimbParams", {}) if isinstance(rec, dict) else {}

        for i, btn in ((1, self.limb_par1_btn), (2, self.limb_par2_btn), (3, self.limb_par3_btn)):
            key = self._limb_param_key_for_index(i)
            state = limb_params.get(key)
            theme.set_button_state(btn, state)
    
    def limb_parameter_colors_at_frame(self, frame):
        """Return Param1..3 colors for the SELECTED limb at a given frame."""
        limb = self.option_var_1.get()
        b = self.video.frames.get(frame, {})
        rec = b.get(limb, {}) if isinstance(b, dict) else {}
        limb_params = rec.get("LimbParams", {}) if isinstance(rec, dict) else {}

        colors = []
        for i in (1, 2, 3):
            key = self._limb_param_key_for_index(i)
            val = limb_params.get(key)
            if val == "ON":
                colors.append(theme.TL_ONSET_MARK)
            elif val == "OFF":
                colors.append(theme.TL_OFF)
            else:
                colors.append(None)
        return colors

    # === Notes & Frame Selection ===============================================
    def _get_note_entry_text(self):
        return self.note_entry.get("1.0", "end-1c")

    def _clear_note_entry(self):
        self.note_entry.delete("1.0", tk.END)

    def _set_note_entry_text(self, text):
        self._clear_note_entry()
        self.note_entry.insert("1.0", text)

    def select_frame(self):
        frame = self._get_note_entry_text().strip()
        try:
            frame_int = int(frame)
        except ValueError:
            logger.warning("cannot select frame: value is not a valid integer: %r", frame)
            self._clear_note_entry(); return
        if self.video is not None:
            if frame_int < 0 or frame_int > self.video.total_frames:
                logger.warning("cannot select frame: %s is out of range", frame_int)
                self._clear_note_entry(); return
            self.video.current_frame = frame_int
            self.update_frame_counter()
            self.display_first_frame()
        else:
            logger.warning("cannot select frame: no video loaded")
        self._clear_note_entry()

    def save_note(self):
        idx = self.video.current_frame
        note_text = self._get_note_entry_text().strip()

        changed = annotation_service.set_note(self.video.frames, idx, note_text)
        if changed:
            # mark_bundle_changed already emits the notify_bundle_changed summary.
            self.mark_bundle_changed(idx)
            annotation_logger.info("f=%s note -> %r", idx, note_text)
        try:
            import keyboard
            keyboard.press_and_release('tab')
        except Exception:
            pass

    def update_note_entry(self):
        if self.video is None:
            return
        idx = self.video.current_frame
        note_text = ""

        b = self.video.frames.get(idx)
        if isinstance(b, dict):
            note_text = (b.get("Note") or "")

        self._set_note_entry_text(note_text)

    # === Save / Export =========================================================
    def save_data(self):
        if not self.video or not self.video.frames_dir:
            logger.info("save skipped: no video loaded")
            return True
        if self.state_repo is None:
            logger.error("save skipped: no state database is open")
            return False
        started = time.perf_counter()
        dirty_count = sum(
            1 for bundle in self.video.frames.values()
            if isinstance(bundle, dict) and bundle.get("Changed")
        )
        self._persist_video_time()
        self.preview_before_save(changed_only=True)
        logger.info("saving %s changed frames", dirty_count)

        paths = ProjectPaths(self.video_name)
        os.makedirs(paths.state_dir, exist_ok=True)
        os.makedirs(paths.export_dir, exist_ok=True)
        logger.debug(
            "save paths: video=%s state=%s export=%s frames=%s",
            paths.video_dir,
            paths.state_db,
            paths.export_csv,
            self.video.frames_dir,
        )

        # 1) Dirty frames -> state DB, one transaction. UI thread (the repo
        #    enforces that itself); this is the source of truth.
        save_service.persist_state(
            self.state_repo, self.video.total_frames, self.video.frames
        )

        logger.debug("writing export dataset: %s", paths.export_csv)

        # Non-tabular inputs gathered here: the button labels are Tk reads and
        # the labeling clock must be sampled on the UI thread.
        metadata = save_service.MetadataInputs(
            program_version=self.video.program_version,
            video_name=self.video_name,
            labeling_mode=self.labeling_mode,
            clothes_list=save_service.load_clothes_zones(self.state_repo),
            param_labels={
                "Parameter_1": (self.par1_btn.cget("text") or "Par1"),
                "Parameter_2": (self.par2_btn.cget("text") or "Par2"),
                "Parameter_3": (self.par3_btn.cget("text") or "Par3"),
            },
            limb_param_labels=self._limb_param_labels_for_export(),
            labeling_time_seconds=self._current_video_time_s(),
        )

        # 2) Snapshot BEFORE the worker-thread export so concurrent edits can
        #    neither tear the export nor be wrongly marked clean afterwards.
        snapshot = save_service.build_save_snapshot(self.video.frames)
        total_frames = self.video.total_frames
        frame_rate = self.frame_rate

        # 3) Export (metadata sidecar + full CSV) on a worker thread while a
        #    modal progress dialog keeps the UI responsive.
        self._run_export_with_progress(
            lambda: save_service.run_export(
                snapshot, paths, frame_rate, metadata, total_frames
            )
        )
        logger.info(
            "save complete: changed_frames=%s export_rows=%s duration=%.2fs",
            dirty_count,
            self.video.total_frames + 1,
            time.perf_counter() - started,
        )

        # 4) Clear Changed ONLY where the live bundle still equals its snapshot:
        #    a frame edited DURING the export stays dirty for the next save.
        save_service.clear_clean_flags(self.video.frames, snapshot)
        return True

    def _limb_param_labels_for_export(self):
        """Limb-parameter button labels for the export metadata, or None when
        the limb controls have not been built yet."""
        if self.limb_par1_btn and self.limb_par2_btn and self.limb_par3_btn:
            return {
                "XX_Parameter_1": (self.limb_par1_btn.cget("text") or "LimbPar1"),
                "XX_Parameter_2": (self.limb_par2_btn.cget("text") or "LimbPar2"),
                "XX_Parameter_3": (self.limb_par3_btn.cget("text") or "LimbPar3"),
            }
        return None

    # === Analysis / Sort / Playback ============================================
    def analysis(self):
        """Save, then run the Analysis use case and open its master HTML.

        The service does all reading/computing/writing and hands back the master
        page path; opening the browser stays here (a GUI concern) so the service
        remains headless and testable. `new_template` is passed down from this
        app's config snapshot — the service never reads config.json.
        """
        if not self.video:
            return
        self.save_data()
        paths = ProjectPaths(self.video_name)
        try:
            result = analysis_service.run_analysis(
                paths,
                frame_rate=self.frame_rate,
                new_template=self.NEW_TEMPLATE,
            )
        except Exception as exc:
            logger.exception("analysis failed for %s", self.video_name)
            messagebox.showerror("Analysis failed", f"Could not complete analysis:\n{exc}")
            return

        if result.warnings:
            messagebox.showwarning(
                "Analysis finished with warnings", "\n\n".join(result.warnings)
            )
        logger.info("opening analysis dashboard: %s", result.master_html)
        webbrowser.open(result.master_html)

    def play_video(self):
        if self.video is None:
            logger.error("cannot play: select a video first")
            return
        # Always play forward when the user clicks Play, regardless of any
        # prior arrow-hold state that may have left play_dir = -1.
        self.play_dir = 1
        self._hold_play_active = False
        if not self.play:
            self.play = True
            if not self.play_thread_on:
                self.play_thread_on = True
                if not self.background_thread_play.is_alive():
                    self.background_thread_play.start()

    def stop_video(self):
        self.play = False

    def toggle_play(self, event=None):
        if self.play:
            self.stop_video()
        else:
            self.play_video()

    # === Video Load & Init =====================================================
    def ask_labeling_mode(self):
        """Modal mode picker. Returns "Normal" / "Reliability", or None when
        the window is closed without confirming.

        Deliberately side-effect free on the app: the caller commits the mode
        (self.labeling_mode + the mode chip) only once the load is actually
        going ahead, so cancelling a load can never leave the OPEN video
        tagged with a half-switched mode (its export metadata records
        self.labeling_mode on every save).
        """
        mode_window = tk.Toplevel(self)
        mode_window.withdraw()
        mode_window.title("Select Mode")
        mode_window.geometry("420x220")
        mode_window.resizable(False, False)
        mode_window.transient(self)
        mode_window.configure(bg=theme.SURFACE)
        mode_window.grab_set()

        content = ttk.Frame(mode_window, padding=16)
        content.pack(fill="both", expand=True)
        label = ttk.Label(
            content,
            text="Choose labeling mode:",
            font=theme.FONT_DIALOG_TITLE,
        )
        label.pack(pady=(0, 10))
        cfg = config.load_config()
        labeling_var = tk.StringVar(value=getattr(self, "labeling_mode", cfg.get("last_labeling_mode", "Normal")))

        ttk.Radiobutton(
            content,
            text="Normal",
            variable=labeling_var,
            value="Normal",
            takefocus=0,
        ).pack()
        ttk.Radiobutton(
            content,
            text="Reliability",
            variable=labeling_var,
            value="Reliability",
            takefocus=0,
        ).pack()

        chosen = {"mode": None}

        def set_mode():
            chosen["mode"] = labeling_var.get()
            # The preference default is fine to persist right away; it does
            # not touch the open video's state.
            cfg["last_labeling_mode"] = chosen["mode"]
            config.save_config(cfg)
            mode_window.destroy()

        ttk.Button(
            content,
            text="Continue",
            command=set_mode,
            width=18,
            style="Tool.TButton",
            takefocus=0,
        ).pack(pady=(16, 0))
        center_over_parent(mode_window, self)
        mode_window.deiconify()
        mode_window.wait_window()
        return chosen["mode"]

    def _unload_current_video(self) -> bool:
        """Persist and fully detach the open project — the same writes
        `on_close` performs, without tearing the Tk app down. Returns False
        when the final save failed; the project is then left open untouched
        so nothing can be lost.

        ORDERING (the data-mixing guard): every writer runs against the OLD
        repo first; then `self.video` drops to None, which idles the
        buffer/playback threads (their context providers return None); then
        the buffer generation is bumped so in-flight decodes of old frames
        discard their result; the state DB closes last.
        """
        if self.video is None:
            return True
        logger.info("unloading video %r (save and close state DB)", self.video_name)

        # Stop playback / arrow-hold before the video identity changes.
        self.stop_video()
        self._cancel_arrow_hold_state()

        # A leftover Clothes window writes its dots through the CURRENT repo
        # on close, so close it now, while that repo is still the right one.
        if self._cloth_app and self._cloth_app.top_level.winfo_exists():
            logger.debug("closing Clothes window before unloading")
            self._cloth_app.on_close()
        self._cloth_app = None

        try:
            ok = self.save_data()
        except Exception:
            logger.exception("final save failed while unloading %r", self.video_name)
            ok = False
        if not ok:
            logger.error(
                "unload aborted: final save failed; keeping current video open"
            )
            return False
        self.save_last_position()
        self._finalize_video_time()

        # Drop the video FIRST: from here on the worker threads see a None
        # context and go idle instead of touching a half-swapped state.
        self.video = None
        self.video_name = None
        self._last_step_sign = 0
        self._buffer_reset()
        self.frame_buffer.buffer_ready = False

        self._close_state_repo()
        self._reset_zone_cache()

        # UI bits tied to the old project.
        self._set_note_entry_text("")
        theme.set_button_state(self.cloth_btn, None)
        self.name_label.config(text="Video Name: -----")
        self.framerate_label.config(text="Frame Rate: -----")
        self._refresh_jump_label()
        self._set_mode_button_states()
        return True

    def _abort_load_to_clean_state(self):
        """A load failed AFTER the previous project was already detached.
        Return to the well-defined startup state (no video, no repo, no
        timer, empty buffer) instead of leaving a half-loaded session."""
        logger.info("video load aborted; returning to clean no-video state")
        self._stop_video_timer_if_any()
        self._close_state_repo()
        self._reset_zone_cache()
        self.video = None
        self.video_name = None
        self._buffer_reset()
        self._set_mode_button_states()

    def load_video(self):
        """Load Video button. Suspends the buffer/playback workers for the
        whole swap so they can never observe a half-published video, then
        wakes them once the load fully finished (successfully or not — an
        aborted load ends with video=None, which keeps them idle anyway)."""
        self._suspend_frame_workers = True
        try:
            self._load_video_flow()
        finally:
            self._suspend_frame_workers = False
            self.frame_buffer.poke()

    def _load_video_flow(self):
        # 1) Gather the user's choices first. Nothing is saved or torn down
        #    yet, so cancelling either dialog leaves the current session
        #    completely untouched (mode chip included).
        mode = self.ask_labeling_mode()
        if mode is None:
            logger.info("video load cancelled: no mode selected")
            return

        with center_native_file_dialog(self):
            video_path = filedialog.askopenfilename(
                parent=self,
                title="Select Video File",
                filetypes=(
                    ("Video files", "*.mp4 *.MP4 *.mov *.MOV *.avi *.AVI *.mkv *.MKV *.flv *.FLV *.wmv *.WMV"),
                    ("All files", "*.*"),
                ),
            )
        if not video_path: return

        # 2) Read-only preparation of the NEW video (copy + probe) while the
        #    current project, if any, is still fully alive — a failure here
        #    cancels the load without disturbing it.
        copied_path = self._prepare_video_copy(video_path)
        if not copied_path:
            logger.info("video load cancelled: copy failed")
            return
        video_path = copied_path

        # Probe once (adapter); the Video model itself does no I/O anymore.
        raw_frame_count, fps = video_probe.probe(video_path)

        # 3) Persist and detach the current project (no-op on first load).
        #    From here on the app is in the clean "no video" state; any later
        #    failure aborts back to it instead of leaving a half-loaded mix.
        if not self._unload_current_video():
            messagebox.showerror(
                "Save Failed",
                "Saving the current video failed (see the console).\n"
                "The new video was NOT loaded, so nothing is lost.",
            )
            return

        # Commit the mode only now that the load is actually going ahead.
        self.labeling_mode = mode
        annotation_logger.info("mode %s selected for video %s", mode, os.path.basename(video_path))
        self.mode_label.set(
            theme.STATUS_WARN if mode == "Reliability" else theme.STATUS_OK, mode
        )

        # 4) Build the new session against a LOCAL Video object. self.video
        #    is assigned only at the commit point below, once the state DB is
        #    open and the frames exist — until then the buffer/playback
        #    threads idle on a None context, so frames of two videos can
        #    never mix in the buffer.
        video = Video(video_path, total_frames=raw_frame_count - 1)
        video.frame_rate = round(fps, 1)
        self.frame_rate = video.frame_rate
        self.framerate_label.config(text=f"Frame Rate: {self.frame_rate}")
        self.jump_frame_count = max(1, round(self.frame_rate * self.jump_seconds))
        logger.debug(
            "fast jump set to %s frames (%ss at %s fps)",
            self.jump_frame_count,
            self.jump_seconds,
            self.frame_rate,
        )
        min_length_in_frames = self.minimal_touch_length * self.frame_rate / 1000
        self.min_touch_length_label.config(text=f"Minimal Touch Length: {min_length_in_frames}")
        raw_video_name = os.path.splitext(os.path.basename(video_path))[0]
        # The "_reliability" suffix rule and directory creation live in the
        # service (ProjectPaths.for_video underneath).
        paths = project_service.prepare_project(raw_video_name, self.labeling_mode)
        video_name = paths.video_name

        video.frames_dir = paths.frames_dir

        # --- Working state: open (or create) state/<video>.db. The progress
        # window covers the whole phase because `load_frames` below reads every
        # row of a long project.
        data_progress_update, data_progress_close = self._open_data_progress_window()
        try:
            self.state_repo = project_service.open_state(
                paths,
                fps=self.frame_rate,
                program_version=video.program_version,
            )
            # ORDERING (unchanged): the labeling timer starts BEFORE the frame
            # load so the session is already accumulating; an extraction abort
            # below rolls it back via _stop_video_timer_if_any().
            self.labeling_timer.start(self.state_repo)
            video.frames = self.state_repo.load_frames(
                progress_cb=data_progress_update
            )
        except Exception as exc:
            logger.exception("could not open working state for %s", video_name)
            messagebox.showerror(
                "Load Failed",
                f"Could not open the working state for this video:\n\n{exc}\n\n"
                "The video was not loaded.",
            )
            self._abort_load_to_clean_state()
            return
        finally:
            data_progress_close()

        # Names for parameters (update button text)
        load_parameter_names_into(
            video,
            {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn},
            {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn},
        )

        # Frames generation/check
        logger.debug("checking frames folder: %s", paths.frames_dir)
        if not project_service.frames_ready(paths, video.total_frames):
            logger.info("frame set incomplete; extracting frames for %s", video_name)
            progress_update, progress_close = self._open_frame_progress_window()
            extraction_cancel = Event()
            self._frame_extraction_cancel = extraction_cancel
            try:
                project_service.extract_frames(
                    video_path,
                    paths,
                    self.labeling_mode,
                    progress_cb=progress_update,
                    cancel_event=extraction_cancel,
                )
            except FrameExtractionCancelled:
                # The app is closing: on_close set the cancel event and has
                # already saved/closed everything itself, so only the timer
                # needs rolling back — no widget may be touched from here.
                logger.info("frame extraction cancelled during shutdown")
                self._stop_video_timer_if_any()
                return
            except FrameExtractionError as exc:
                logger.error("frame extraction failed for %s: %s", video_name, exc)
                messagebox.showerror(
                    "Frame Extraction Failed",
                    f"Could not extract frames from this video:\n\n{exc}\n\n"
                    "The file may be unreadable or use an unsupported codec. "
                    "The video was not loaded.",
                )
                # Roll back the labeling timer and the state DB opened above,
                # ending in the clean "no video loaded" state.
                self._abort_load_to_clean_state()
                return
            finally:
                if self._frame_extraction_cancel is extraction_cancel:
                    self._frame_extraction_cancel = None
                progress_close()
        else:
            logger.debug("frame set is complete for %s", video_name)

        # 5) COMMIT POINT: the state DB is open, the frames exist on disk.
        #    Publish the new session to the rest of the app — the worker
        #    threads start seeing this video from this line on.
        self.video = video
        self.video_name = video_name
        self._refresh_jump_label()

        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._timeline_last_zone = None
        self._timeline_last_limb = None
        self._timeline2_last_limb = None
        self._timeline_canvas_size = (0, 0)
        self._timeline2_canvas_size = (0, 0)
        self._timeline_playhead_id = None
        self._timeline2_playhead_id = None

        self.restore_last_position()

        t_draw = time.time()
        self.display_first_frame()
        self.draw_timeline()
        self.draw_timeline2()
        logger.debug("initial video draw completed in %.1fs", time.time() - t_draw)
        self.name_label.config(
            text=f"Video: {video_name} | FPS: {self.frame_rate} | Version: {self.video.program_version}"
        )

        # Seed the geometry cache on the main thread before the buffer thread
        # runs — workers read _display_w/_display_h instead of winfo_* (H1).
        self._display_w = self.video_frame.winfo_width()
        self._display_h = self.video_frame.winfo_height()

        # Lazily started once; on a reload it is already alive and simply
        # picks the new video up from its context provider. The buffer itself
        # was emptied (generation-bumped) in _unload_current_video, BEFORE the
        # new video was published, so it cannot hold stale frames here.
        if not self.background_thread.is_alive():
            self.background_thread.start()

        self.update_note_entry()

        # Clothes presence => colorize button
        theme.set_button_state(self.cloth_btn, None)
        if self.state_repo.has_clothes():
            theme.set_button_state(self.cloth_btn, "ON")
        # NOTE: self.clothes_diagram_scale stays the DISPLAY scale. The scale the
        # dots were stored at lives in meta.clothes_diagram_scale and is only
        # used as the rescale source (project_service.rescale_clothes_points),
        # exactly as the sidecar's DiagramScale line was.

        for b in self.video.frames.values():
            if isinstance(b, dict):
                b["Changed"] = False
        self.rebuild_annotation_controls()
        self._set_mode_button_states()
        logger.info(
            "video %s loaded: %s frames at %s fps, resuming at frame %s",
            video_name,
            video.total_frames + 1,
            self.frame_rate,
            video.current_frame,
        )

    # === Clothes Side Window ===================================================
    def open_cloth_app(self):
        if self.video is None:
            logger.error("cannot open Clothes: select a video first")
        else:
            if self._cloth_app and self._cloth_app.top_level.winfo_exists():
                self._cloth_app.top_level.lift()
                self._cloth_app.top_level.focus_force()
                return
            scale = self.clothes_diagram_scale or DEFAULT_CLOTH_DIAGRAM_SCALE
            initial_points = project_service.load_clothes_points_from_repo(
                self.state_repo, scale, DEFAULT_CLOTH_DIAGRAM_SCALE
            )
            self.cloth_btn.config(state=tk.DISABLED)

            def on_save(dots, diagram_scale=None):
                self.update_data_clothes(dots, diagram_scale)

            def on_close(dots, diagram_scale=None):
                self.update_data_clothes(dots, diagram_scale)
                self.cloth_btn.config(state=tk.NORMAL)
                self._cloth_app = None

            try:
                self._cloth_app = ClothApp(
                    self,
                    on_save,
                    on_close,
                    initial_points=initial_points,
                    diagram_scale=scale,
                )
            except Exception:
                self.cloth_btn.config(state=tk.NORMAL)
                self._cloth_app = None
                logger.exception("failed to open Clothes window")

    def update_data_clothes(self, dots, diagram_scale=None):
        self.data_clothes = dots
        if diagram_scale:
            self.clothes_diagram_scale = float(diagram_scale)
        annotation_logger.info("clothes updated dots=%s", len(self.data_clothes))
        self.save_clothes()
        theme.set_button_state(self.cloth_btn, "ON")

    def save_clothes(self):
        """Full replace of the clothes dots in the state DB (was the
        `state/<video>_clothes.txt` sidecar).

        The per-dot `zones` value stays the COMMA-JOINED string the sidecar
        held, because the export metadata's "Zones Covered With Clothes" list is
        a frozen contract built on that tokenization (see
        `SqliteRepository.clothes_zone_list`).
        """
        if self.state_repo is None:
            logger.error("cannot save clothes: no state database is open")
            return
        scale = self.clothes_diagram_scale or DEFAULT_CLOTH_DIAGRAM_SCALE

        rows = []
        for dot_id, (x, y) in self.data_clothes.items():
            if scale == 0:
                scale = DEFAULT_CLOTH_DIAGRAM_SCALE
            zones = self.find_image_with_white_pixel(x / scale, y / scale)
            rows.append((dot_id, x, y, ','.join(zones)))

        self.state_repo.save_clothes(rows, scale)
        logger.info("clothes saved: dots=%s", len(rows))

    

    # === App Lifecycle (close, position) =======================================
    def on_close(self):
        if getattr(self, "_closing", False):
            return
        if not custom_confirm_close(self):
            return
        self._closing = True
        if self.video is not None:
            try:
                ok = self.save_data()
            except Exception:
                logger.exception("final save failed while closing")
                ok = False
            if not ok and not messagebox.askyesno(
                "Save failed",
                "Saving failed - your latest changes are NOT on disk (see the console).\n"
                "Close anyway and lose them?",
            ):
                self._closing = False
                return
            self.save_last_position()
            self._finalize_video_time()
        extraction_cancel = getattr(self, "_frame_extraction_cancel", None)
        if extraction_cancel is not None:
            extraction_cancel.set()
        # ORDERING: the state DB closes only AFTER the final save, the last
        # position and the labeling-time checkpoint — all three write to it.
        self._close_state_repo()
        try:
            self.frame_buffer.shutdown(wait=False, cancel_futures=True)
        except Exception:
            logger.warning("loader pool shutdown failed", exc_info=True)
        self._cancel_pending_timers()
        self.destroy()

    def _cancel_pending_timers(self):
        """Cancel our own repeating `after()` timers before the root goes away.

        Without this the diagram-refresh timer (and any live arrow-hold
        watchdog) fires into a destroyed interpreter and Tcl prints
        `invalid command name "...periodic_print_dot"` on every close.
        """
        self._cancel_arrow_hold_state()
        after_id = getattr(self, "_dot_refresh_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                logger.warning("could not cancel diagram refresh timer", exc_info=True)
            self._dot_refresh_after_id = None

    def _close_state_repo(self):
        """Release the state DB connection (called before opening another
        project and on app close). Safe to call when nothing is open."""
        if self.state_repo is None:
            return
        self.state_repo.close()
        self.state_repo = None

    def save_last_position(self):
        if self.video is None or self.state_repo is None:
            return
        self.state_repo.save_last_position(
            self.video.current_frame, self.video.total_frames
        )

    def restore_last_position(self):
        """Resume where the researcher left off (state DB `meta.last_frame`)."""
        if self.state_repo is None:
            return
        frame = self.state_repo.read_last_position(self.video.total_frames)
        if frame is None:
            return
        self.video.current_frame = frame
        self.video.current_frame_zone = int(self.video.current_frame / self.video.number_frames_in_zone)
        logger.debug("restored last position: frame=%s", self.video.current_frame)

    # === Settings ==============================================================
    def open_settings(self):
        if getattr(self, "_settings_win", None) and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return

        cfg = config.load_config()
        win = tk.Toplevel(self)
        win.title("Settings")
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=theme.SURFACE)
        self._settings_win = win

        content = ttk.Frame(win, padding=16)
        content.pack(fill="both", expand=True)
        content.columnconfigure(1, weight=1)

        def _v(key, default):
            return cfg.get(key, default)

        vars_map = {
            "video_downscale": tk.StringVar(value=str(_v("video_downscale", 1.0))),
            "diagram_scale": tk.StringVar(value=str(_v("diagram_scale", 1.0))),
            "dot_size": tk.StringVar(value=str(_v("dot_size", 10))),
            "jump_seconds": tk.StringVar(value=str(_v("jump_seconds", 1.0))),
            "parameter1": tk.StringVar(value=str(_v("parameter1", "Parameter 1"))),
            "parameter2": tk.StringVar(value=str(_v("parameter2", "Parameter 2"))),
            "parameter3": tk.StringVar(value=str(_v("parameter3", "Parameter 3"))),
            "limb_parameter1": tk.StringVar(value=str(_v("limb_parameter1", "Limb Parameter 1"))),
            "limb_parameter2": tk.StringVar(value=str(_v("limb_parameter2", "Limb Parameter 2"))),
            "limb_parameter3": tk.StringVar(value=str(_v("limb_parameter3", "Limb Parameter 3"))),
            "realtime_arrow_hold": tk.BooleanVar(value=bool(_v("realtime_arrow_hold", True))),
        }

        row = 0
        ttk.Label(content, text="Display", font=theme.FONT_BOLD).grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="w",
            padx=8,
            pady=(0, 6),
        )
        row += 1
        ttk.Label(content, text="Video downscale (1 = full, 2 = half)").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["video_downscale"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Diagram scale").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["diagram_scale"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Dot size").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["dot_size"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Fast-jump seconds (>> / Shift+Arrow)").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["jump_seconds"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Realtime hold (arrow keys play at video framerate)").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Checkbutton(
            content,
            variable=vars_map["realtime_arrow_hold"],
            bootstyle="round-toggle",
            takefocus=0,
        ).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1

        def parse_float(value, key):
            if value is None:
                return 0.0
            s = str(value).strip()
            if s == "":
                return 0.0
            try:
                return float(s)
            except Exception:
                raise ValueError(f"{key} must be a number")

        ttk.Separator(content, orient="horizontal").grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=8,
            pady=(10, 8),
        )
        row += 1
        ttk.Label(content, text="Parameter Labels", font=theme.FONT_BOLD).grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="w",
            padx=8,
            pady=(0, 6),
        )
        row += 1
        ttk.Label(content, text="Limb Parameter 1").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["limb_parameter1"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Limb Parameter 2").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["limb_parameter2"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Limb Parameter 3").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["limb_parameter3"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Parameter 1").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["parameter1"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Parameter 2").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["parameter2"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1
        ttk.Label(content, text="Parameter 3").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(content, textvariable=vars_map["parameter3"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )
        row += 1

        def apply_settings(close=False):
            try:
                new_cfg = dict(cfg)
                downscale = parse_float(vars_map["video_downscale"].get(), "video_downscale")
                if downscale <= 0:
                    downscale = 1.0
                new_cfg["video_downscale"] = downscale
                new_cfg["diagram_scale"] = parse_float(vars_map["diagram_scale"].get(), "diagram_scale")
                new_cfg["dot_size"] = parse_float(vars_map["dot_size"].get(), "dot_size")
                jump_seconds = parse_float(vars_map["jump_seconds"].get(), "jump_seconds")
                if jump_seconds <= 0:
                    jump_seconds = 1.0
                new_cfg["jump_seconds"] = jump_seconds

                def _label_from(key, default):
                    raw = vars_map[key].get()
                    val = str(raw).strip() if raw is not None else ""
                    return val if val else default

                new_cfg["parameter1"] = _label_from("parameter1", new_cfg.get("parameter1", "Parameter 1"))
                new_cfg["parameter2"] = _label_from("parameter2", new_cfg.get("parameter2", "Parameter 2"))
                new_cfg["parameter3"] = _label_from("parameter3", new_cfg.get("parameter3", "Parameter 3"))
                new_cfg["limb_parameter1"] = _label_from("limb_parameter1", new_cfg.get("limb_parameter1", "Limb Parameter 1"))
                new_cfg["limb_parameter2"] = _label_from("limb_parameter2", new_cfg.get("limb_parameter2", "Limb Parameter 2"))
                new_cfg["limb_parameter3"] = _label_from("limb_parameter3", new_cfg.get("limb_parameter3", "Limb Parameter 3"))
                new_cfg["realtime_arrow_hold"] = bool(vars_map["realtime_arrow_hold"].get())
            except ValueError as e:
                messagebox.showerror("Invalid settings", str(e), parent=win)
                return

            config.save_config(new_cfg)
            self.apply_runtime_settings(new_cfg)
            for key, new_value in new_cfg.items():
                old_value = cfg.get(key)
                if old_value != new_value:
                    annotation_logger.info(
                        "setting %s %r -> %r", key, old_value, new_value
                    )
            cfg.clear()
            cfg.update(new_cfg)
            if close:
                win.destroy()

        btn_frame = ttk.Frame(content)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(10, 8))
        ttk.Button(
            btn_frame,
            text="Apply",
            command=lambda: apply_settings(close=False),
            style="Tool.TButton",
            takefocus=0,
        ).pack(side="left", padx=5)
        ttk.Button(
            btn_frame,
            text="Apply & Close",
            command=lambda: apply_settings(close=True),
            style="Tool.TButton",
            takefocus=0,
        ).pack(side="left", padx=5)
        ttk.Button(
            btn_frame,
            text="Open Logs Folder",
            command=self._open_logs_folder,
            style="Tool.TButton",
            takefocus=0,
        ).pack(side="left", padx=5)
        ttk.Button(
            btn_frame,
            text="Close",
            command=win.destroy,
            style="Tool.TButton",
            takefocus=0,
        ).pack(side="left", padx=5)

    def _open_logs_folder(self):
        try:
            open_logs_folder()
        except Exception as exc:
            logger.exception("could not open logs folder")
            messagebox.showerror(
                "Logs unavailable",
                f"Could not open the logs folder:\n{exc}",
                parent=getattr(self, "_settings_win", self),
            )

    def apply_runtime_settings(self, cfg: dict):
        # Refresh the one AppConfig snapshot the app holds (build_ui and other
        # readers consume self.config instead of re-reading config.json).
        self.config = config.load_app_config()
        self.perf.enabled = bool(cfg.get("perf_enabled", False))
        self.perf.log_every_s = float(cfg.get("perf_log_every_s", 2.0))
        self.perf.top_n = int(cfg.get("perf_log_top_n", 6))

        new_downscale = float(cfg.get("video_downscale", 1.0))
        if new_downscale <= 0:
            new_downscale = 1.0
        self.video_downscale = new_downscale

        new_jump_seconds = float(cfg.get("jump_seconds", 1.0))
        if new_jump_seconds <= 0:
            new_jump_seconds = 1.0
        self.jump_seconds = new_jump_seconds
        if self.video is not None and getattr(self, "frame_rate", None):
            self.jump_frame_count = max(1, round(self.frame_rate * self.jump_seconds))
            logger.debug(
                "fast jump updated to %s frames (%ss at %s fps)",
                self.jump_frame_count,
                self.jump_seconds,
                self.frame_rate,
            )
        else:
            logger.debug("fast jump updated to %ss (no video loaded)", self.jump_seconds)
        self._refresh_jump_label()

        # Realtime arrow-hold toggle. If user disables it mid-session while a
        # hold-driven playback is active, tear that playback down cleanly.
        new_realtime = bool(cfg.get("realtime_arrow_hold", True))
        if not new_realtime:
            self._cancel_arrow_hold_state()
        self.realtime_arrow_hold = new_realtime
        logger.debug("realtime arrow hold: %s", self.realtime_arrow_hold)

        new_scale = float(cfg.get("diagram_scale", 1.0))
        new_dot = float(cfg.get("dot_size", 10))
        self.diagram_scale = new_scale
        self.dot_size = new_dot

        # Refresh parameter labels on buttons (and on the active video if present).
        try:
            target = self.video if self.video is not None else self
            load_parameter_names_into(
                target,
                {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn},
                {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn},
            )
        except Exception:
            pass

        base_w, base_h = 450, 696
        w, h = int(base_w * new_scale), int(base_h * new_scale)
        try:
            self.diagram_canvas.config(width=w, height=h)
            self.diagram_canvas.delete("all")
            self.on_radio_click()
        except Exception:
            pass

        # Flush buffer so new resolution takes effect immediately.
        self._buffer_reset()
        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._timeline_playhead_id = None
        self._timeline2_playhead_id = None
        if getattr(self, "video", None):
            self.display_first_frame()

    # === Frame Stepping ========================================================
    def next_frame(self, number_of_frames, play=False):
        if self.video is None:
            logger.debug("frame movement skipped: no video loaded")
            return
        if number_of_frames > 0:
            self.video.current_frame = min(self.video.total_frames, self.video.current_frame + number_of_frames)
            self._last_step_sign = 1
        elif number_of_frames < 0:
            self.video.current_frame = max(0, self.video.current_frame + number_of_frames)
            self._last_step_sign = -1
        else:
            logger.warning("frame movement skipped: delta is zero")
            return

        # Wake the buffering thread if the destination isn't cached so it gets
        # loaded with priority before any prefetch fills.
        if self.video.current_frame not in self.frame_buffer:
            self.frame_buffer.poke()

        self.display_first_frame()
        if not play: self.draw_timeline()
        self.draw_timeline2()
