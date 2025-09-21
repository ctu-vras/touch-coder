import tkinter as tk
from PIL import Image, ImageTk


class ClothApp:
    def __init__(self, master, on_close_callback):
        # Vytvoření nového okna pomocí Toplevel
        self.top_level = tk.Toplevel(master)
        self.top_level.title("Clothes App")
        self.top_level.geometry("225x348")  # Double the original dimensions for the window size
        self.on_close_callback = on_close_callback

        self.f = tk.Frame(self.top_level, bg='red')
        self.f.grid(row=1, column=0, sticky="nsew")

        self.dots = {}
        self.img = Image.open("icons/diagram.png")
        self.img = self.img.resize((int(self.img.width*0.5), int(self.img.height*0.5)), Image.LANCZOS)
        self.photo2 = ImageTk.PhotoImage(self.img)
        self.canvas2 = tk.Canvas(self.f, width=self.img.width, height=self.img.height, bg='red')

        self.canvas2.pack()
        self.canvas2.create_image(0, 0, anchor="nw", image=self.photo2)
        self.canvas2.bind("<Button-1>", self.add_dot)    # Left click to add a dot
        self.canvas2.bind("<Button-2>", self.remove_dot) # Middle click to remove a dot
        self.top_level.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        # Callback with dots data on close
        self.on_close_callback(self.dots)
        self.top_level.destroy()

    def add_dot(self, event):
        dot_id = self.canvas2.create_oval(event.x-5, event.y-5, event.x+5, event.y+5, fill="red")
        self.dots[dot_id] = (event.x, event.y)
        print("INFO: Clothes dots: ", self.dots)

    def remove_dot(self, event):
        closest_dot = self.canvas2.find_closest(event.x, event.y)[0]
        if closest_dot in self.dots:
            del self.dots[closest_dot]
            self.canvas2.delete(closest_dot)
            print("Dots:", self.dots)
