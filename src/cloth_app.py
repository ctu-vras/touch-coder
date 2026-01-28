import tkinter as tk
from PIL import Image, ImageTk

from resource_utils import resource_path

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
        # Vytvoření nového okna pomocí Toplevel
        self.top_level = tk.Toplevel(master)
        self.top_level.title("Clothes App")
        self.on_save_callback = on_save_callback
        self.on_close_callback = on_close_callback
        self.diagram_scale = float(diagram_scale)
        self.dot_radius = int(dot_radius)

        self.controls = tk.Frame(self.top_level, bg='lightgrey')
        self.controls.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.controls.columnconfigure(0, weight=1)

        save_btn = tk.Button(self.controls, text="Save", command=self.on_save)
        save_btn.pack(side="left", padx=5)

        save_close_btn = tk.Button(self.controls, text="Save & Close", command=self.on_close)
        save_close_btn.pack(side="left", padx=5)

        self.f = tk.Frame(self.top_level, bg='lightgrey')
        self.f.grid(row=1, column=0, sticky="nsew")
        self.top_level.columnconfigure(0, weight=1)
        self.top_level.rowconfigure(1, weight=1)

        self.dots = {}
        self.img = Image.open(resource_path("icons/diagram.png"))
        self.img = self.img.resize(
            (int(self.img.width * self.diagram_scale), int(self.img.height * self.diagram_scale)),
            Image.LANCZOS,
        )
        self.photo2 = ImageTk.PhotoImage(self.img)
        self.canvas2 = tk.Canvas(self.f, width=self.img.width, height=self.img.height, bg='lightgrey')

        self.canvas2.pack(padx=10, pady=10)
        self.canvas2.create_image(0, 0, anchor="nw", image=self.photo2)
        self.canvas2.bind("<Button-1>", self.add_dot)    # Left click to add a dot
        self.canvas2.bind("<Button-2>", self.remove_dot) # Middle click to remove a dot
        self.top_level.protocol("WM_DELETE_WINDOW", self.on_close)

        if initial_points:
            for x, y in initial_points:
                self._create_dot(x, y)

        self.top_level.update_idletasks()
        controls_h = self.controls.winfo_reqheight()
        win_w = self.img.width + 20
        win_h = self.img.height + 20 + controls_h + 10
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
        dot_id = self.canvas2.create_oval(
            x - self.dot_radius,
            y - self.dot_radius,
            x + self.dot_radius,
            y + self.dot_radius,
            fill="red",
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
