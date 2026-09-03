"""Central UI palette, fonts, and reusable ttk styling helpers."""

from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

THEME_NAME = "cosmo"

# Surfaces / text.
SURFACE = "#f8f9fa"
SURFACE_ALT = "#eef0f2"
VIDEO_BG = "#dee2e6"
BORDER = "#ced4da"
TEXT = "#212529"
TEXT_MUTED = "#6c757d"
DISABLED_BG = SURFACE_ALT
DISABLED_FG = TEXT_MUTED

# Semantic widget state.
ACCENT = "#2780e3"
ON_GREEN = "#9fdca4"
OFF_RED = "#E57373"
NEUTRAL = "#ffffff"

# Touch timelines.
TL_EMPTY = "#e9ecef"
TL_OUTLINE = "#d5d9dd"
TL_UNAVAILABLE = "#d7dadd"
TL_UNAVAILABLE_MARK = "#aeb4ba"
TL_ON = ON_GREEN
TL_DURING = "#d9f0db"
TL_OFF = OFF_RED
TL_ONSET_MARK = "#2f8f57"
TL_OFFSET_MARK = "#c0392b"
# Parameter ticks are secondary to onset/offset edges: muted hues, 1 px and
# dashed, so a parameter OFF can never be mistaken for a touch OFF.
TL_PARAM_ON_MARK = "#74b58c"
TL_PARAM_OFF_MARK = "#dc9088"
TL_PARAM_MARK_WIDTH = 1
TL_PARAM_MARK_DASH = (3, 2)
PLAYHEAD = ACCENT

# Status chips use small semantic dots rather than filled backgrounds.
STATUS_OK = TL_ONSET_MARK
STATUS_BAD = TL_OFFSET_MARK
STATUS_WARN = "#d39e00"

# Legacy controller fields retained until the timeline polish phase.
TOUCH_START = ACCENT
TOUCH_END = TL_OFFSET_MARK

# Diagram dots.
DOT_ONSET = "#21a453"
DOT_OFFSET = "#e2483d"
CLOTH_DOT = DOT_OFFSET

_dot_cache = {}

# Fonts.
FONT_BASE = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI", 11)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SUBTITLE = FONT_SMALL
FONT_DIALOG_TITLE = FONT_TITLE


def dot_sprite(color, radius, ring="#ffffff", ring_px=2, hollow=False):
    """Return a cached, antialiased ``PhotoImage`` for a canvas dot."""
    radius = max(1, int(round(float(radius))))
    ring_px = max(0, int(round(float(ring_px))))
    key = (color, radius, ring, ring_px, bool(hollow))
    if key not in _dot_cache:
        supersample = 4
        diameter = (radius + ring_px) * 2
        source_size = diameter * supersample
        image = Image.new("RGBA", (source_size, source_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        inset = ring_px * supersample
        circle_box = [inset, inset, source_size - 1 - inset, source_size - 1 - inset]
        if hollow:
            draw.ellipse(
                circle_box,
                outline=color,
                width=max(2, radius // 2) * supersample,
            )
        else:
            draw.ellipse([0, 0, source_size - 1, source_size - 1], fill=ring)
            draw.ellipse(circle_box, fill=color)
        image = image.resize(
            (diameter, diameter),
            Image.Resampling.LANCZOS,
        )
        _dot_cache[key] = ImageTk.PhotoImage(image)
    return _dot_cache[key]


def _flat_button(style, name, background, hover):
    """Register one flat, light button style with readable hover text."""
    style.configure(
        name,
        background=background,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=background,
        darkcolor=background,
        focusthickness=0,
        font=FONT_BASE,
        padding=(10, 5),
    )
    style.map(
        name,
        background=[("active", hover), ("pressed", hover)],
        foreground=[
            ("active", TEXT),
            ("pressed", TEXT),
            ("disabled", TEXT_MUTED),
        ],
        bordercolor=[("active", BORDER)],
        lightcolor=[("active", hover)],
        darkcolor=[("active", hover)],
    )


def init_style(root):
    """Attach ttkbootstrap's cosmo style to an existing ``tk.Tk`` root."""
    # Keep this import local so importing app modules remains safe in headless
    # tests and does not initialize ttkbootstrap's widget API prematurely.
    import ttkbootstrap

    style = ttkbootstrap.Style(theme=THEME_NAME)
    _flat_button(style, "Tool.TButton", NEUTRAL, TL_EMPTY)
    _flat_button(style, "StateOn.TButton", ON_GREEN, "#8fd096")
    _flat_button(style, "StateOff.TButton", OFF_RED, "#dd6666")
    _flat_button(style, "StateNeutral.TButton", NEUTRAL, TL_EMPTY)
    root.configure(bg=SURFACE)
    return style


def set_button_state(button, state):
    """Apply the semantic ttk style for an ON, OFF, or unset state."""
    normalized = None if state in (None, "None", "") else state
    style_name = {
        "ON": "StateOn.TButton",
        "OFF": "StateOff.TButton",
    }.get(normalized, "StateNeutral.TButton")
    button.configure(style=style_name)


class StatusChip(ttk.Frame):
    """A muted label followed by a colored status dot and plain text."""

    def __init__(self, parent, label, color, text, **kwargs):
        super().__init__(parent, **kwargs)
        ttk.Label(
            self,
            text=label,
            font=FONT_SMALL,
            foreground=TEXT_MUTED,
        ).pack(side="left")
        self._dot_label = ttk.Label(
            self,
            text="●",
            font=FONT_SMALL,
            foreground=color,
        )
        self._dot_label.pack(side="left", padx=(4, 2))
        self._text_label = ttk.Label(self, text=text, font=FONT_SMALL)
        self._text_label.pack(side="left")

    def set(self, color, text):
        """Update the semantic dot color and displayed status text."""
        self._dot_label.configure(foreground=color)
        self._text_label.configure(text=text)
