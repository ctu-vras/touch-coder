"""
ui_components.py
All Tkinter widget construction, layout, and event bindings live here.
It takes a controller object (your LabelingApp instance) that already
implements the callback methods (e.g., load_video, on_timeline_click, etc.).
"""

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import sys

from gui.resource_utils import asset_path
from gui import theme

def build_ui(app):
    """
    Build all containers (frames), widgets, and bindings on the given app.
    The app is expected to be a tk.Tk (or Toplevel) and to provide all
    callback methods that are referenced here.
    """
    # === Root window basics ===
    app.title('TinyTouch')
    app.geometry('1200x1000')
    app.minsize(1100, 800)
    app.protocol("WM_DELETE_WINDOW", app.on_close)

    # === Top-level state used by the GUI ===
    app.photo = None
    app.pil_frame = None
    app.current_dot_item_id = [None]
    app.touch = False
    app.is_touch_timeline = False
    app.color_start = theme.TOUCH_START
    app.color_during = theme.TL_DURING
    app.color_end = theme.TOUCH_END
    app.frame_cache = {}
    app.image = None
    # The frame cache itself lives in adapters.frame_buffer.FrameBuffer
    # (constructed in LabelingApp.__init__), not on the widget tree.
    app.play = False
    app.play_thread_on = False
    app.notes_file_path = None
    app.progress = {}
    app.frame_rate = None
    app.last_mouse_x = 0
    app.last_mouse_y = 0

    # Diagram scale / dot size come from the AppConfig snapshot the app holds
    # (loaded once in LabelingApp.__init__) — the GUI never re-reads config.json.
    scale = app.config.diagram_scale
    app.diagram_scale = scale
    dot_size = app.config.dot_size
    app.dot_size = dot_size

    # === Containers ===
    app.video_frame = tk.Frame(app, bg=theme.VIDEO_BG, bd=0)
    app.timeline_frame = tk.Frame(app, bg=theme.SURFACE_ALT, height=50)
    app.control_frame = tk.Frame(app, bg=theme.SURFACE, height=100)
    app.diagram_frame = ttk.Frame(app)

    # Match original grid positions exactly
    app.video_frame.grid(row=1, column=0, sticky="nsew")
    app.timeline_frame.grid(row=2, column=0, sticky="ew")
    app.control_frame.grid(row=0, column=0, columnspan=1, sticky="ew")
    app.diagram_frame.grid(row=0, column=1, rowspan=3, sticky="ns")

    app.columnconfigure(0, weight=1)
    app.rowconfigure(1, weight=1)
    app.rowconfigure(2, weight=0)

    # === Timeline canvases ===
    app.timeline2_canvas = tk.Canvas(
        app.timeline_frame,
        bg=theme.SURFACE,
        height=30,
        highlightthickness=0,
    )
    app.timeline2_canvas.pack(fill=tk.X, expand=True, padx=8, pady=(6, 4))
    app.timeline2_canvas.bind("<Button-1>", app.on_timeline2_click)

    app.timeline_canvas = tk.Canvas(
        app.timeline_frame,
        bg=theme.SURFACE_ALT,
        height=50,
        highlightthickness=0,
    )
    app.timeline_canvas.pack(fill=tk.X, expand=True, padx=8, pady=(4, 8))
    app.timeline_canvas.bind("<Button-1>", app.on_timeline_click)
    if sys.platform.startswith("linux"):
        app.bind("<Button-1>", app.global_click, add="+")   # safer on Linux
    else:
        app.bind_all("<Button-1>", app.global_click, add="+")  # keep Windows behavio

    # === Controls (top bar) ===
    _build_controls(app)

    # === Diagram panel (right column) ===
    _build_diagram_panel(app, scale)

    # === Bindings ===
    _bind_navigation(app)

    # === Resize behavior for video panel ===
    app.video_frame.bind('<Configure>', app.on_resize)


def _build_controls(app):
    # Use pack-based rows to keep layout stable as buttons change
    top_row = ttk.Frame(app.control_frame)
    top_row.pack(fill="x", padx=5, pady=5)

    bottom_row = ttk.Frame(app.control_frame)
    bottom_row.pack(fill="x", padx=5, pady=(0, 5))

    left_top = ttk.Frame(top_row)
    left_top.pack(side="left")

    right_top = ttk.Frame(top_row)
    right_top.pack(side="right")

    right_top_buttons = ttk.Frame(right_top)
    right_top_buttons.pack(side="top", anchor="e")

    right_top_status = ttk.Frame(right_top)
    right_top_status.pack(side="top", anchor="e")

    app.load_video_btn = ttk.Button(
        left_top,
        text="Load Video",
        command=app.load_video,
        style="Tool.TButton",
        takefocus=0,
    )
    app.load_video_btn.pack(side="left", padx=5)

    settings_btn = ttk.Button(
        left_top,
        text="Settings",
        command=app.open_settings,
        style="Tool.TButton",
        takefocus=0,
    )
    settings_btn.pack(side="left", padx=5)

    app.cloth_btn = ttk.Button(
        left_top,
        text="Clothes",
        command=app.open_cloth_app,
        style="Tool.TButton",
        takefocus=0,
    )
    app.cloth_btn.pack(side="left", padx=5)

    app.analysis_btn = ttk.Button(
        left_top,
        text="Analysis",
        command=app.analysis,
        style="Tool.TButton",
        takefocus=0,
    )
    app.analysis_btn.pack(side="left", padx=5)

    save_btn = ttk.Button(
        left_top,
        text="Save",
        command=app.save_data,
        style="Tool.TButton",
        takefocus=0,
    )
    save_btn.pack(side="left", padx=5)

    back_10_frame_btn = ttk.Button(
        right_top_buttons,
        text="<<",
        command=lambda: app.next_frame(-app.jump_frame_count),
        style="Tool.TButton",
        takefocus=0,
    )
    back_10_frame_btn.pack(side="left", padx=5)

    back_frame_btn = ttk.Button(
        right_top_buttons,
        text="<",
        command=lambda: app.next_frame(-1),
        style="Tool.TButton",
        takefocus=0,
    )
    back_frame_btn.pack(side="left", padx=5)

    next_frame_btn = ttk.Button(
        right_top_buttons,
        text=">",
        command=lambda: app.next_frame(1),
        style="Tool.TButton",
        takefocus=0,
    )
    next_frame_btn.pack(side="left", padx=5)

    next_10_frame_btn = ttk.Button(
        right_top_buttons,
        text=">>",
        command=lambda: app.next_frame(app.jump_frame_count),
        style="Tool.TButton",
        takefocus=0,
    )
    next_10_frame_btn.pack(side="left", padx=5)

    play_btn = ttk.Button(
        right_top_buttons,
        text="Play",
        command=app.play_video,
        style="Tool.TButton",
        takefocus=0,
    )
    play_btn.pack(side="left", padx=5)

    stop_btn = ttk.Button(
        right_top_buttons,
        text="Stop",
        command=app.stop_video,
        style="Tool.TButton",
        takefocus=0,
    )
    stop_btn.pack(side="left", padx=5)

    app.frame_counter_label = ttk.Label(right_top_status, text="0 / 0")
    app.frame_counter_label.pack(side="left", padx=5)

    app.time_counter_label = ttk.Label(right_top_status, text="0 / 0")
    app.time_counter_label.pack(side="left", padx=10)

    left_bottom = ttk.Frame(bottom_row)
    left_bottom.pack(side="left")

    right_bottom = ttk.Frame(bottom_row)
    right_bottom.pack(side="right")

    app.mode_label = theme.StatusChip(
        left_bottom,
        label="Mode:",
        color=theme.STATUS_OK,
        text="-----",
    )
    app.mode_label.pack(side="left", padx=5)

    app.loading_label = theme.StatusChip(
        left_bottom,
        label="Buffer:",
        color=theme.STATUS_OK,
        text="Loaded",
    )
    app.loading_label.pack(side="left", padx=10)

    # Keep the label for updates, but do not show it in the UI.
    app.framerate_label = ttk.Label(left_bottom, text="Frame Rate: -----")

    app.min_touch_length_label = ttk.Label(
        left_bottom,
        text="Minimal Touch Length: -----",
    )
    app.min_touch_length_label.pack(side="left", padx=10)

    app.jump_label = ttk.Label(left_bottom, text="Jump: -----")
    app.jump_label.pack(side="left", padx=10)

    app.name_label = ttk.Label(right_bottom, text="Video Name: -----")
    app.name_label.pack(side="left", padx=10)


def _build_diagram_panel(app, scale):
    app.option_var_1 = tk.StringVar()
    app.option_var_1.set("RH")
    # === Diagram canvas EXACTLY like original ===
    # original used icons/diagram0.png, scaled size, and packed inside diagram_frame
    base_w, base_h = 450, 696
    w, h = int(base_w * scale), int(base_h * scale)

    app.diagram_canvas = tk.Canvas(
        app.diagram_frame,
        bg=theme.SURFACE,
        width=w,
        height=h,
        highlightthickness=0,
    )
    app.diagram_canvas.pack(padx=10, pady=10, side="top", anchor="n")

    try:
        img = Image.open(asset_path("icons/diagram0.png"))
        img = img.resize((w, h), Image.LANCZOS)
        app.photo = ImageTk.PhotoImage(img)
        app.diagram_canvas.create_image(0, 0, anchor="nw", image=app.photo)
    except Exception:
        # If the image is missing, keep empty canvas — controller will redraw on first radio click.
        pass

    # Bind clicks on the diagram (these call back into the controller)
    app.diagram_canvas.bind("<Motion>", app.update_last_mouse_position)
    app.diagram_canvas.bind("<Button-3>", lambda event: app.on_diagram_click(event, is_onset=False))
    app.diagram_canvas.bind("<Button-1>", lambda event: app.on_diagram_click(event, is_onset=True))
    app.diagram_canvas.bind("<Button-2>", app.on_middle_click)

    app.mode_controls_frame = ttk.Frame(app.diagram_frame)
    app.mode_controls_frame.pack(fill="x", anchor="n")

    separator = ttk.Separator(app.diagram_frame, orient="horizontal")
    separator.pack(fill="x", padx=10, pady=5)

    app.mode_param_label = ttk.Label(
        app.diagram_frame,
        text="Parameters",
        font=theme.FONT_BOLD,
    )
    app.mode_param_label.pack(anchor="n", pady=(5, 0))
    app.mode_param_subtitle = ttk.Label(
        app.diagram_frame,
        text="(Limb-Specific)",
        font=theme.FONT_SUBTITLE,
    )
    app.mode_param_subtitle.pack(anchor="n", pady=(0, 5))

    app.limb_parameter_frame = ttk.Frame(app.diagram_frame)
    app.limb_parameter_frame.pack(fill="x", anchor="n")
    app.limb_par1_btn = None
    app.limb_par2_btn = None
    app.limb_par3_btn = None

    separator = ttk.Separator(app.diagram_frame, orient="horizontal")
    separator.pack(fill="x", padx=10, pady=5)

    label_after_separator3 = ttk.Label(
        app.diagram_frame,
        text="Parameters",
        font=theme.FONT_BOLD,
    )
    label_after_separator3.pack(anchor="n", pady=(5, 2))
    app.par1_btn = ttk.Button(
        app.diagram_frame,
        text="Parameter 1",
        command=lambda: app.parameter_dic_insert(1),
        width=15,
        style="StateNeutral.TButton",
        takefocus=0,
    )
    app.par1_btn.pack(anchor="n", pady=4)
    app.par2_btn = ttk.Button(
        app.diagram_frame,
        text="Parameter 2",
        command=lambda: app.parameter_dic_insert(2),
        width=15,
        style="StateNeutral.TButton",
        takefocus=0,
    )
    app.par2_btn.pack(anchor="n", pady=4)
    app.par3_btn = ttk.Button(
        app.diagram_frame,
        text="Parameter 3",
        command=lambda: app.parameter_dic_insert(3),
        width=15,
        style="StateNeutral.TButton",
        takefocus=0,
    )
    app.par3_btn.pack(anchor="n", pady=4)

    separator = ttk.Separator(app.diagram_frame, orient="horizontal")
    separator.pack(fill="x", padx=10, pady=5)

    note_controls = ttk.Frame(app.diagram_frame)
    note_controls.pack(side="bottom", anchor="center", pady=(6, 10))

    app.note_entry = tk.Text(
        note_controls,
        width=30,
        height=2,
        wrap="word",
        font=theme.FONT_BASE,
        bg=theme.NEUTRAL,
        fg=theme.TEXT,
        insertbackground=theme.TEXT,
        bd=0,
        padx=6,
        pady=4,
        highlightbackground=theme.BORDER,
        highlightcolor=theme.ACCENT,
        highlightthickness=1,
    )
    app.note_entry.pack(anchor="center")

    note_button_row = ttk.Frame(note_controls)
    note_button_row.pack(fill="x", pady=(6, 0))
    note_button_row.columnconfigure(0, weight=1, uniform="note_actions")
    note_button_row.columnconfigure(1, weight=1, uniform="note_actions")

    app.save_note_button = ttk.Button(
        note_button_row,
        text="Save Note",
        command=app.save_note,
        style="Tool.TButton",
        takefocus=0,
    )
    app.save_note_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))

    app.select_frame_button = ttk.Button(
        note_button_row,
        text="Select Frame",
        command=app.select_frame,
        style="Tool.TButton",
        takefocus=0,
    )
    app.select_frame_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    if hasattr(app, "rebuild_annotation_controls"):
        app.rebuild_annotation_controls()



def _guard_key(app, cb):
    """Skip a global nav key action while the Note entry holds focus.

    The keystroke still reaches the Entry (its class binding ran first in
    bindtag order); we only suppress the nav side effect. Returns None so no
    other binding is broken. The guard sits at the binding site — not inside
    the handler — because on_middle_click is also bound to <Button-2> on the
    canvas, which must keep working regardless of Note-entry focus.
    """
    def wrapped(event):
        if app._entry_has_focus():
            return
        return cb(event)
    return wrapped


def _bind_navigation(app):
    app.bind("<KeyPress-d>", _guard_key(app, app.on_middle_click))
    app.bind("<Left>", _guard_key(app, app.navigate_left))
    app.bind("<Right>", _guard_key(app, app.navigate_right))
    app.bind("<Shift-Left>", _guard_key(app, lambda event: app.next_frame(-app.jump_frame_count)))
    app.bind("<Shift-Right>", _guard_key(app, lambda event: app.next_frame(app.jump_frame_count)))
    app.bind("<space>", _guard_key(app, app.toggle_play))

    # Wheel bindings: Windows/Mac vs Linux
    if sys.platform.startswith("linux"):
        # Wheel events on X11/Wayland commonly report as Button-4/5
        app.bind_all("<Button-4>", app.on_mouse_wheel)
        app.bind_all("<Button-5>", app.on_mouse_wheel)
    else:
        app.bind_all("<MouseWheel>", app.on_mouse_wheel)
