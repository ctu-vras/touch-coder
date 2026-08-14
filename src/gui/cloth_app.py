import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

from gui.resource_utils import resource_path
from gui import theme

DEFAULT_CLOTH_DIAGRAM_SCALE = 1.0
DEFAULT_CLOTH_DOT_RADIUS = 7


class ClothApp:
    def __init__(
        self,
        master,
        on_save_callback,
        on_close_callback,
        initial_points=None,
        diagram_scale=DEFAULT_CLOTH_DIAGRAM_SCALE,
        dot_radius=DEFAULT_CLOTH_DOT_RADIUS,
    ):
        self.top_level = tk.Toplevel(master)
        self.top_level.title("Clothes App")
        self.top_level.configure(bg=theme.SURFACE)
        self.on_save_callback = on_save_callback
        self.on_close_callback = on_close_callback
        self.diagram_scale = float(diagram_scale)
        self.dot_radius = int(dot_radius)

        self.content = ttk.Frame(self.top_level, padding=16)
        self.content.grid(row=0, column=0, sticky="nsew")
        self.top_level.columnconfigure(0, weight=1)
        self.top_level.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)

        self.controls = ttk.Frame(self.content)
        self.controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.controls.columnconfigure(0, weight=1)

        save_btn = ttk.Button(
            self.controls,
            text="Save",
            command=self.on_save,
            style="Tool.TButton",
            takefocus=0,
        )
        save_btn.pack(side="left", padx=5)

        save_close_btn = ttk.Button(
            self.controls,
            text="Save & Close",
            command=self.on_close,
            style="Tool.TButton",
            takefocus=0,
        )
        save_close_btn.pack(side="left", padx=5)

        self.f = ttk.Frame(self.content)
        self.f.grid(row=1, column=0, sticky="nsew")

        self.dots = {}
        self.img = Image.open(resource_path("icons/diagram.png"))
        self.img = self.img.resize(
            (int(self.img.width * self.diagram_scale), int(self.img.height * self.diagram_scale)),
            Image.LANCZOS,
        )
        self.photo2 = ImageTk.PhotoImage(self.img)
        self.canvas2 = tk.Canvas(
            self.f,
            width=self.img.width,
            height=self.img.height,
            bg=theme.SURFACE,
            highlightthickness=0,
        )

        self.canvas2.pack(padx=10, pady=10)
        self.canvas2.create_image(0, 0, anchor="nw", image=self.photo2)
        self.canvas2.bind("<Button-1>", self.add_dot)    # Left click to add a dot
        self.canvas2.bind("<Button-2>", self.remove_dot) # Middle click to remove a dot
        self.top_level.protocol("WM_DELETE_WINDOW", self.on_close)

        if initial_points:
            for x, y in initial_points:
                self._create_dot(x, y)

        self.top_level.update_idletasks()
        win_w = self.content.winfo_reqwidth()
        win_h = self.content.winfo_reqheight()
        self.top_level.geometry(f"{win_w}x{win_h}")

    def on_save(self):
        if self.on_save_callback:
            self.on_save_callback(self.dots, self.diagram_scale)

    def on_close(self):
        # Callback with dots data on close
        if self.on_close_callback:
            self.on_close_callback(self.dots, self.diagram_scale)
        self.top_level.destroy()

    def _create_dot(self, x, y):
        dot_id = self.canvas2.create_image(
            x,
            y,
            image=theme.dot_sprite(theme.CLOTH_DOT, self.dot_radius),
        )
        self.dots[dot_id] = (x, y)
        return dot_id

    def add_dot(self, event):
        self._create_dot(event.x, event.y)
        print("INFO: Clothes dots: ", self.dots)

    def remove_dot(self, event):
        closest_dot = self.canvas2.find_closest(event.x, event.y)[0]
        if closest_dot in self.dots:
            del self.dots[closest_dot]
            self.canvas2.delete(closest_dot)
            print("Dots:", self.dots)
