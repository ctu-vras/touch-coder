"""
ui_preview.py — throwaway visual appetizer for the ttkbootstrap "cosmo" restyle.

v2 — calmer palette after feedback: too many colors / button styles in v1.
Rules now:
  * ALL buttons share ONE neutral style (white, thin grey border).
  * ONE accent color (cosmo blue): playhead, selected radio, focused entry.
  * Green/red appear ONLY where they carry annotation meaning
    (param button state, status dots, timeline cells/marks).
  * No yellow: "during touch" is a pale green, so a touch reads as one
    green block (onset dark tick -> pale green run -> red offset tick).

NO app logic: every button is a dummy.
Run:  uv run python ui_preview.py
"""

import os
import tkinter as tk
from tkinter import ttk

import ttkbootstrap as tb
from PIL import Image, ImageDraw, ImageTk

# =====================================================================
# Palette v2
# =====================================================================
THEME_NAME = "cosmo"

SURFACE = "#f8f9fa"
SURFACE_ALT = "#eef0f2"
VIDEO_BG = "#dee2e6"
BORDER = "#ced4da"
TEXT = "#212529"
TEXT_MUTED = "#6c757d"

ACCENT = "#2780e3"          # the ONE accent (playhead, selection)

ON_GREEN = "#9fdca4"        # param button ON / status ok
OFF_RED = "#e57373"         # param button OFF / status bad (kept from old app)
NEUTRAL = "#ffffff"         # param button unset == normal button face

TL_EMPTY = "#e9ecef"
TL_OUTLINE = "#d5d9dd"
TL_ON = ON_GREEN            # onset cell
TL_DURING = "#d9f0db"       # pale green — same family as ON, replaces yellow
TL_OFF = OFF_RED            # offset cell
TL_ONSET_MARK = "#2f8f57"
TL_OFFSET_MARK = "#c0392b"
PLAYHEAD = ACCENT

POSE_BODY_SCALE_COLOR = "#446a8a"

DOT_ONSET = "#21a453"       # refined green (was pure 'green')
DOT_OFFSET = "#e2483d"      # refined red (was pure 'red')

FONT_BASE = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 10, "bold")

DIAGRAM_SCALE = 0.55


def noop():
    pass


# =====================================================================
# Antialiased dot sprites (Tk canvas ovals have NO antialiasing —
# render 4x oversized with PIL, downscale LANCZOS, cache per look)
# =====================================================================
_dot_cache = {}


def dot_sprite(color, radius, ring="#ffffff", ring_px=2, hollow=False):
    """Return a cached PhotoImage of a smooth dot for canvas.create_image."""
    key = (color, radius, ring, ring_px, hollow)
    if key not in _dot_cache:
        ss = 4  # supersampling factor
        d_out = (radius + ring_px) * 2
        size = d_out * ss
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if hollow:
            draw.ellipse([ring_px * ss, ring_px * ss, size - 1 - ring_px * ss,
                          size - 1 - ring_px * ss],
                         outline=color, width=max(2, radius // 2) * ss)
        else:
            draw.ellipse([0, 0, size - 1, size - 1], fill=ring)       # white halo ring
            draw.ellipse([ring_px * ss, ring_px * ss, size - 1 - ring_px * ss,
                          size - 1 - ring_px * ss], fill=color)
        img = img.resize((d_out, d_out), Image.LANCZOS)
        _dot_cache[key] = ImageTk.PhotoImage(img)
    return _dot_cache[key]


# =====================================================================
# Root + style
# =====================================================================
root = tk.Tk()
root.title("TinyTouch — UI preview v2 (cosmo, no functionality)")
root.geometry("1200x860")
style = tb.Style(theme=THEME_NAME)


def _flat_button(name, bg, hover):
    """One flat button recipe used for EVERY button in the app."""
    style.configure(name, background=bg, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=bg, darkcolor=bg,
                    focusthickness=0, font=FONT_BASE, padding=(10, 5))
    style.map(name,
              background=[("active", hover), ("pressed", hover)],
              foreground=[("active", TEXT), ("pressed", TEXT),
                          ("disabled", TEXT_MUTED)],  # cosmo defaults hover text to WHITE
              bordercolor=[("active", BORDER)],
              lightcolor=[("active", hover)],
              darkcolor=[("active", hover)])


_flat_button("Tool.TButton", NEUTRAL, "#e9ecef")          # every normal button
_flat_button("StateOn.TButton", ON_GREEN, "#8fd096")       # param ON
_flat_button("StateOff.TButton", OFF_RED, "#dd6666")       # param OFF
# param unset == identical to a normal button (no third look)
_flat_button("StateNeutral.TButton", NEUTRAL, "#e9ecef")

root.configure(bg=SURFACE)

# =====================================================================
# Layout skeleton (same grid as the real app)
# =====================================================================
video_frame = tk.Frame(root, bg=VIDEO_BG)
timeline_frame = tk.Frame(root, bg=SURFACE)
control_frame = ttk.Frame(root)
diagram_frame = ttk.Frame(root)

video_frame.grid(row=1, column=0, sticky="nsew")
timeline_frame.grid(row=2, column=0, sticky="ew")
control_frame.grid(row=0, column=0, sticky="ew")
diagram_frame.grid(row=0, column=1, rowspan=3, sticky="ns")
root.columnconfigure(0, weight=1)
root.rowconfigure(1, weight=1)

# =====================================================================
# Top toolbar — every button identical
# =====================================================================
top_row = ttk.Frame(control_frame)
top_row.pack(fill="x", padx=8, pady=(8, 4))

for name in ("Load Video", "Save", "Settings", "Analysis", "Clothes"):
    ttk.Button(top_row, text=name, style="Tool.TButton", command=noop,
               takefocus=0).pack(side="left", padx=3)

counter = ttk.Label(top_row, text="Frame 1524 / 45210    00:50.8", font=FONT_BASE)
counter.pack(side="right", padx=(8, 0))
for name in ("Stop", "Play", ">>", ">", "<", "<<"):
    ttk.Button(top_row, text=name, style="Tool.TButton", command=noop,
               width=5 if len(name) <= 2 else 6, takefocus=0).pack(side="right", padx=2)


def status_chip(parent, label, dot_color, text):
    """'Buffer: ● Loaded' — colored DOT only, no filled background."""
    ttk.Label(parent, text=label, font=FONT_SMALL, foreground=TEXT_MUTED).pack(side="left")
    ttk.Label(parent, text="●", font=FONT_SMALL, foreground=dot_color).pack(side="left", padx=(4, 2))
    ttk.Label(parent, text=text, font=FONT_SMALL).pack(side="left", padx=(0, 14))


bottom_row = ttk.Frame(control_frame)
bottom_row.pack(fill="x", padx=8, pady=(0, 8))
status_chip(bottom_row, "Mode:", TL_ONSET_MARK, "Normal")
status_chip(bottom_row, "Buffer:", TL_ONSET_MARK, "Loaded")
status_chip(bottom_row, "", TL_OFFSET_MARK, "Loading…")
ttk.Label(bottom_row, text="Frame rate: 29.97    Min touch: 3    Jump: 5 s    Video: infant_042.mp4",
          font=FONT_SMALL, foreground=TEXT_MUTED).pack(side="left")

# =====================================================================
# Video area placeholder
# =====================================================================
tk.Label(video_frame, text="( video frame renders here )",
         bg=VIDEO_BG, fg=TEXT_MUTED, font=("Segoe UI", 14)).place(relx=0.5, rely=0.5, anchor="center")

# =====================================================================
# Timelines with mock data
# =====================================================================
tl2 = tk.Canvas(timeline_frame, bg=SURFACE, height=30, highlightthickness=0)
tl2.pack(fill=tk.X, expand=True, padx=8, pady=(6, 4))
tl = tk.Canvas(timeline_frame, bg=SURFACE, height=56, highlightthickness=0)
tl.pack(fill=tk.X, expand=True, padx=8, pady=(4, 8))


def draw_mock_timelines(_event=None):
    w = max(tl.winfo_width(), 100)
    right = w - 2  # inset so the right border isn't clipped off-canvas

    # --- scrub bar: full-video overview ---
    tl2.delete("all")
    tl2.create_rectangle(1, 8, right, 22, fill=TL_EMPTY, outline=TL_OUTLINE)
    for a, b in ((0.30, 0.36), (0.62, 0.66)):
        x0, x1 = int(w * a), int(w * b)
        # fill sits BETWEEN the tick lines (3px gap) — nothing overlaps
        tl2.create_rectangle(x0 + 3, 9, x1 - 3, 21, fill=TL_DURING, outline="")
        tl2.create_line(x0, 5, x0, 25, fill=TL_ONSET_MARK, width=2)
        tl2.create_line(x1, 5, x1, 25, fill=TL_OFFSET_MARK, width=2)
    px = int(w * 0.45)
    tl2.create_line(px, 6, px, 27, fill=PLAYHEAD, width=2)
    tl2.create_polygon(px - 4, 1, px + 4, 1, px, 8, fill=PLAYHEAD, outline="")

    # --- zoomed timeline: per-frame cells, single-line grid ---
    tl.delete("all")
    n = 41
    cell = (w - 3) / n
    states = ([TL_EMPTY] * 8 + [TL_ON] + [TL_DURING] * 9 + [TL_OFF]
              + [TL_EMPTY] * 6 + [TL_ON] + [TL_DURING] * 4 + [TL_OFF] + [TL_EMPTY] * 11)
    for i, col in enumerate(states[:n]):
        if col != TL_EMPTY:
            tl.create_rectangle(1 + i * cell, 10, 1 + (i + 1) * cell, 38,
                                fill=col, outline="")
    # grid drawn once on top: outer box + one line per boundary (no doubled edges)
    tl.create_rectangle(1, 10, 1 + n * cell, 38, fill="", outline=TL_OUTLINE)
    for i in range(1, n):
        x = 1 + i * cell
        tl.create_line(x, 10, x, 38, fill=TL_OUTLINE)
    for i, col in ((9, TL_ONSET_MARK), (10, TL_ONSET_MARK), (11, TL_OFFSET_MARK), (25, TL_ONSET_MARK)):
        tl.create_rectangle(1 + i * cell + 2, 42, 1 + (i + 1) * cell - 2, 50, fill=col, outline="")
    px = 1 + (n // 2) * cell + cell / 2
    tl.create_line(px, 8, px, 52, fill=PLAYHEAD, width=2)
    tl.create_polygon(px - 4, 2, px + 4, 2, px, 9, fill=PLAYHEAD, outline="")


tl.bind("<Configure>", draw_mock_timelines)

# =====================================================================
# Right panel
# =====================================================================
pad = {"padx": 10}

diagram_holder = ttk.Frame(diagram_frame)
diagram_holder.pack(side="top", pady=(10, 4), **pad)
_diagram_img = None
try:
    img = Image.open(os.path.join(os.path.dirname(__file__), "icons", "diagram.png"))
    w, h = int(450 * DIAGRAM_SCALE), int(696 * DIAGRAM_SCALE)
    img = img.resize((w, h), Image.LANCZOS)
    _diagram_img = ImageTk.PhotoImage(img)
    canvas = tk.Canvas(diagram_holder, bg=SURFACE, width=w, height=h, highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=_diagram_img)
    # NEW smooth sprite dots (onset / offset / hollow "last onset" marker)
    canvas.create_image(w * 0.42, h * 0.35, image=dot_sprite(DOT_ONSET, 6))
    canvas.create_image(w * 0.58, h * 0.47, image=dot_sprite(DOT_OFFSET, 6))
    canvas.create_image(w * 0.35, h * 0.55, image=dot_sprite(DOT_ONSET, 6, hollow=True))
except Exception:
    ttk.Label(diagram_holder, text="(diagram.png not found)", font=FONT_SMALL).pack()


limb_row = ttk.Frame(diagram_frame)
limb_row.pack(pady=(2, 4))
limb_var = tk.StringVar(value="RH")
for limb in ("RH", "LH", "RF", "LF"):
    ttk.Radiobutton(limb_row, text=limb, value=limb, variable=limb_var,
                    takefocus=0).pack(side="left", padx=6)

ttk.Separator(diagram_frame, orient="horizontal").pack(fill="x", pady=6, **pad)

ttk.Label(diagram_frame, text="Parameters", font=FONT_BOLD).pack()
ttk.Label(diagram_frame, text="(Limb-Specific)", font=FONT_SMALL,
          foreground=TEXT_MUTED).pack(pady=(0, 4))
ttk.Button(diagram_frame, text="Reaching  (ON)", style="StateOn.TButton",
           width=18, command=noop, takefocus=0).pack(pady=2)
ttk.Button(diagram_frame, text="Grasping  (OFF)", style="StateOff.TButton",
           width=18, command=noop, takefocus=0).pack(pady=2)
ttk.Button(diagram_frame, text="Holding", style="StateNeutral.TButton",
           width=18, command=noop, takefocus=0).pack(pady=2)

ttk.Separator(diagram_frame, orient="horizontal").pack(fill="x", pady=6, **pad)

ttk.Label(diagram_frame, text="Parameters", font=FONT_BOLD).pack(pady=(0, 4))
ttk.Button(diagram_frame, text="Looking  (ON)", style="StateOn.TButton",
           width=18, command=noop, takefocus=0).pack(pady=2)
ttk.Button(diagram_frame, text="Mouthing", style="StateNeutral.TButton",
           width=18, command=noop, takefocus=0).pack(pady=2)

ttk.Separator(diagram_frame, orient="horizontal").pack(fill="x", pady=6, **pad)

ttk.Label(diagram_frame, text="Body Scale (pose mode)", font=FONT_SMALL,
          foreground=TEXT_MUTED).pack()
scale_var = tk.DoubleVar(value=1.0)
tk.Scale(diagram_frame, from_=0.7, to=1.3, resolution=0.01, orient="horizontal",
         variable=scale_var, troughcolor=SURFACE_ALT, bg=SURFACE,
         highlightthickness=0, length=180, takefocus=0).pack(pady=(0, 4))

note_entry = ttk.Entry(diagram_frame, width=32)
note_entry.pack(side="bottom", fill="x", pady=(4, 10), **pad)
note_entry.insert(0, "note text…")
ttk.Button(diagram_frame, text="Save Note", style="Tool.TButton",
           width=16, command=noop, takefocus=0).pack(side="bottom", pady=2)
ttk.Button(diagram_frame, text="Select Frame", style="Tool.TButton",
           width=16, command=noop, takefocus=0).pack(side="bottom", pady=2)

root.mainloop()
