"""
ui_components.py
All Tkinter widget construction, layout, and event bindings live here.
It takes a controller object (your LabelingApp instance) that already
implements the callback methods (e.g., load_video, on_timeline_click, etc.).
"""

import tkinter as tk
from PIL import Image, ImageTk
import sys

from config_utils import load_config
from resource_utils import resource_path

def _load_diagram_scale():
    """Read a numeric diagram_scale from config.json; default to 1.0 if missing."""
    try:
        cfg = load_config()
        return float(cfg.get("diagram_scale", 1.0))
    except Exception:
        return 1.0

def _load_dot_size():
    """Read a numeric dot_size from config.json; default to 10 if missing."""
    try:
        cfg = load_config()
        return float(cfg.get("dot_size", 10))
    except Exception:
        return 10.0

def build_ui(app):
    """
    Build all containers (frames), widgets, and bindings on the given app.
    The app is expected to be a tk.Tk (or Toplevel) and to provide all
    callback methods that are referenced here.
    """
    # === Root window basics ===
    app.title('TinyTouch')
    app.geometry('1200x1000')
    app.protocol("WM_DELETE_WINDOW", app.on_close)

    # === Top-level state used by the GUI ===
    app.photo = None
    app.pil_frame = None
    app.current_dot_item_id = [None]
    app.touch = False
    app.is_touch_timeline = False
    app.color_start = "blue"
    app.color_during = "yellow"
    app.color_end = "red"
    app.frame_cache = {}
    app.image = None
    app.img_buffer = {}
    app.img_buffer_bytes = {}
    app.img_buffer_total = 0
    app.play = False
    app.play_thread_on = False
    app.old_width = None
    app.old_height = None
    app.notes_file_path = None
    app.progress = {}
    app.frame_rate = None
    app.last_mouse_x = 0
    app.last_mouse_y = 0

    # Also store diagram_size to mirror original (controller later overwrites with config_utils)
    scale = _load_diagram_scale()
    app.diagram_scale = scale
    dot_size = _load_dot_size()
    app.dot_size = dot_size

    # === Containers ===
    app.video_frame = tk.Frame(app, bg='#bcbcbc')
    app.timeline_frame = tk.Frame(app, bg='grey', height=50)
    app.control_frame = tk.Frame(app, bg='lightgrey', height=100)
    app.diagram_frame = tk.Frame(app, bg='lightgrey')

    # Match original grid positions exactly
    app.video_frame.grid(row=1, column=0, sticky="nsew")
    app.timeline_frame.grid(row=2, column=0, sticky="ew")
    app.control_frame.grid(row=0, column=0, columnspan=1, sticky="ew")
    app.diagram_frame.grid(row=0, column=1, rowspan=3, sticky="ns")

    app.columnconfigure(0, weight=1)
    app.rowconfigure(1, weight=1)
    app.rowconfigure(2, weight=0)

    # === Timeline canvases ===
    app.timeline2_canvas = tk.Canvas(app.timeline_frame, bg='lightgrey', height=30)
    app.timeline2_canvas.pack(fill=tk.X, expand=True, pady=(0, 5))
    app.timeline2_canvas.bind("<Button-1>", app.on_timeline2_click)

    app.timeline_canvas = tk.Canvas(app.timeline_frame, bg='grey', height=50)
    app.timeline_canvas.pack(fill=tk.X, expand=True, pady=(10, 0))
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
    top_row = tk.Frame(app.control_frame, bg='lightgrey')
    top_row.pack(fill="x", padx=5, pady=5)

    bottom_row = tk.Frame(app.control_frame, bg='lightgrey')
    bottom_row.pack(fill="x", padx=5, pady=(0, 5))

    left_top = tk.Frame(top_row, bg='lightgrey')
    left_top.pack(side="left")

    right_top = tk.Frame(top_row, bg='lightgrey')
    right_top.pack(side="right")

    right_top_buttons = tk.Frame(right_top, bg='lightgrey')
    right_top_buttons.pack(side="top", anchor="e")

    right_top_status = tk.Frame(right_top, bg='lightgrey')
    right_top_status.pack(side="top", anchor="e")

    app.load_video_btn = tk.Button(left_top, text="Load Video", command=app.load_video)
    app.load_video_btn.pack(side="left", padx=5)

    save_btn = tk.Button(left_top, text="Save", command=app.save_data)
    save_btn.pack(side="left", padx=5)

    settings_btn = tk.Button(left_top, text="Settings", command=app.open_settings)
    settings_btn.pack(side="left", padx=5)

    analysis_btn = tk.Button(left_top, text="Analysis", command=app.analysis)
    analysis_btn.pack(side="left", padx=5)

    app.cloth_btn = tk.Button(left_top, text="Clothes", command=app.open_cloth_app)
    app.cloth_btn.pack(side="left", padx=5)

    sort_btn = tk.Button(left_top, text="Sort Frames", command=app.sort_frames, state='disabled')
    sort_btn.pack(side="left", padx=5)

    back_10_frame_btn = tk.Button(right_top_buttons, text="<<", command=lambda: app.next_frame(-7))
    back_10_frame_btn.pack(side="left", padx=5)

    back_frame_btn = tk.Button(right_top_buttons, text="<", command=lambda: app.next_frame(-1))
    back_frame_btn.pack(side="left", padx=5)

    next_frame_btn = tk.Button(right_top_buttons, text=">", command=lambda: app.next_frame(1))
    next_frame_btn.pack(side="left", padx=5)

    next_10_frame_btn = tk.Button(right_top_buttons, text=">>", command=lambda: app.next_frame(7))
    next_10_frame_btn.pack(side="left", padx=5)

    play_btn = tk.Button(right_top_buttons, text="Play", command=app.play_video)
    play_btn.pack(side="left", padx=5)

    stop_btn = tk.Button(right_top_buttons, text="Stop", command=app.stop_video)
    stop_btn.pack(side="left", padx=5)

    app.frame_counter_label = tk.Label(right_top_status, text="0 / 0", bg='lightgrey')
    app.frame_counter_label.pack(side="left", padx=5)

    app.time_counter_label = tk.Label(right_top_status, text="0 / 0", bg='lightgrey')
    app.time_counter_label.pack(side="left", padx=10)

    left_bottom = tk.Frame(bottom_row, bg='lightgrey')
    left_bottom.pack(side="left")

    right_bottom = tk.Frame(bottom_row, bg='lightgrey')
    right_bottom.pack(side="right")

    app.mode_label = tk.Label(left_bottom, text="Mode: -----", bg='lightgrey')
    app.mode_label.pack(side="left", padx=5)

    app.loading_label = tk.Label(left_bottom, text="Buffer Loaded", bg='lightgrey')
    app.loading_label.pack(side="left", padx=10)

    # Keep the label for updates, but do not show it in the UI.
    app.framerate_label = tk.Label(left_bottom, text="Frame Rate: -----", bg='lightgrey')

    app.min_touch_lenght_label = tk.Label(left_bottom, text="Minimal Touch Length: -----", bg='lightgrey')
    app.min_touch_lenght_label.pack(side="left", padx=10)

    app.name_label = tk.Label(right_bottom, text="Video Name: -----", bg='lightgrey')
    app.name_label.pack(side="left", padx=10)


def _build_diagram_panel(app, scale):
    # radio group for limb selector
    app.option_var_1 = tk.StringVar()
    app.option_var_1.set("RH")
    # === Diagram canvas EXACTLY like original ===
    # original used icons/diagram0.png, scaled size, and packed inside diagram_frame
    base_w, base_h = 450, 696
    w, h = int(base_w * scale), int(base_h * scale)

    app.diagram_canvas = tk.Canvas(app.diagram_frame, bg='lightgrey', width=w, height=h)
    app.diagram_canvas.pack(padx=10, pady=10, side="top", anchor="n")

    try:
        img = Image.open(resource_path("icons/diagram0.png"))
        img = img.resize((w, h), Image.LANCZOS)
        app.photo = ImageTk.PhotoImage(img)
        app.diagram_canvas.create_image(0, 0, anchor="nw", image=app.photo)
    except Exception:
        # If the image is missing, keep empty canvas — controller will redraw on first radio click.
        pass

    # Bind clicks on the diagram (these call back into the controller)
    app.diagram_canvas.bind("<Motion>", app.update_last_mouse_position)
    app.diagram_canvas.bind("<Button-3>", lambda event: app.on_diagram_click(event, right=False))
    app.diagram_canvas.bind("<Button-1>", lambda event: app.on_diagram_click(event, right=True))
    app.diagram_canvas.bind("<Button-2>", app.on_middle_click)

    label_after_separator1 = tk.Label(app.diagram_frame, text="Limb Selector", font=("Arial", 10, "bold"), bg='lightgrey')
    label_after_separator1.pack(anchor="n", pady=(5, 2))

    # NOTE: The original text/values asymmetry is preserved intentionally.
    rb = tk.Radiobutton(app.diagram_frame, text="Right Hand", variable=app.option_var_1, value="RH", bg='lightgrey')
    rb.pack(anchor="n")
    rb = tk.Radiobutton(app.diagram_frame, text="Left Hand", variable=app.option_var_1, value="LH", bg='lightgrey')
    rb.pack(anchor="n")
    rb = tk.Radiobutton(app.diagram_frame, text="Right Leg", variable=app.option_var_1, value="RL", bg='lightgrey')
    rb.pack(anchor="n")
    rb = tk.Radiobutton(app.diagram_frame, text="Left Leg", variable=app.option_var_1, value="LL", bg='lightgrey')
    rb.pack(anchor="n")

    separator = tk.Frame(app.diagram_frame, height=2, bd=1, relief="sunken")
    separator.pack(fill="x", padx=5, pady=5)

    label_after_separator2 = tk.Label(app.diagram_frame, text="Parameters", font=("Arial", 10, "bold"), bg='lightgrey')
    label_after_separator2.pack(anchor="n", pady=(5, 0))
    label_after_separator21 = tk.Label(app.diagram_frame, text="(Limb-Specific)", font=("Arial", 8), bg='lightgrey')
    label_after_separator21.pack(anchor="n", pady=(0, 5))

    app.limb_par1_btn = tk.Button(app.diagram_frame, text="Limb Parameter 1",
                                  command=lambda: app.toggle_limb_parameter(1), width=15, height=1)
    app.limb_par1_btn.pack(anchor="n")
    app.limb_par2_btn = tk.Button(app.diagram_frame, text="Limb Parameter 2",
                                  command=lambda: app.toggle_limb_parameter(2), width=15, height=1)
    app.limb_par2_btn.pack(anchor="n")
    app.limb_par3_btn = tk.Button(app.diagram_frame, text="Limb Parameter 3",
                                  command=lambda: app.toggle_limb_parameter(3), width=15, height=1)
    app.limb_par3_btn.pack(anchor="n")

    separator = tk.Frame(app.diagram_frame, height=2, bd=1, relief="sunken")
    separator.pack(fill="x", padx=5, pady=5)

    label_after_separator3 = tk.Label(app.diagram_frame, text="Parameters", font=("Arial", 10, "bold"), bg='lightgrey')
    label_after_separator3.pack(anchor="n", pady=(5, 2))
    app.par1_btn = tk.Button(app.diagram_frame, text="Parametr 1",
                             command=lambda: app.parameter_dic_insert(1), width=15, height=1)
    app.par1_btn.pack(anchor="n")
    app.par2_btn = tk.Button(app.diagram_frame, text="Parametr 2",
                             command=lambda: app.parameter_dic_insert(2), width=15, height=1)
    app.par2_btn.pack(anchor="n")
    app.par3_btn = tk.Button(app.diagram_frame, text="Parametr 3",
                             command=lambda: app.parameter_dic_insert(3), width=15, height=1)
    app.par3_btn.pack(anchor="n")

    separator = tk.Frame(app.diagram_frame, height=2, bd=1, relief="sunken")
    separator.pack(fill="x", padx=5, pady=5)

    

   

    app.select_frame_button = tk.Button(
        app.diagram_frame, text="Select Frame", command=app.select_frame, width=15, height=1
    )
    app.select_frame_button.pack(side="bottom",padx=5, pady=5)
     # Buttons
    app.save_note_button = tk.Button(
        app.diagram_frame, text="Save Note", command=app.save_note, width=15, height=1
    )
    app.save_note_button.pack(side="bottom",padx=5, pady=5)

    # Note entry & helpers (kept on root to mirror original placement)
    app.note_entry = tk.Entry(app.diagram_frame, width=40)
    app.note_entry.pack(side="bottom", fill="x", padx=5, pady=5)



def _bind_navigation(app):
    app.bind("<KeyPress-d>", app.on_middle_click)
    app.bind("<Left>", app.navigate_left)
    app.bind("<Right>", app.navigate_right)
    app.bind("<Shift-Left>", lambda event: app.next_frame(-7))
    app.bind("<Shift-Right>", lambda event: app.next_frame(7))

    # Wheel bindings: Windows/Mac vs Linux
    if sys.platform.startswith("linux"):
        # Wheel events on X11/Wayland commonly report as Button-4/5
        app.bind_all("<Button-4>", app.on_mouse_wheel)
        app.bind_all("<Button-5>", app.on_mouse_wheel)
    else:
        app.bind_all("<MouseWheel>", app.on_mouse_wheel)
