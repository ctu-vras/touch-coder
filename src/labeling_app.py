import os
import sys
import csv
import json
import time
import shutil
from tkinter import ttk
from turtle import right
import cv2
import tkinter as tk
from tkinter import messagebox, filedialog
from threading import Thread
from PIL import Image, ImageTk
import pandas as pd
import keyboard
from data_utils import bundle_summary_str  # import the helper
import analysis
from sort_frames import process_touch_data_strict_transitions

# Split-out modules
from cloth_app import ClothApp
from video_model import Video
from ui_components import build_ui
from data_utils import (
    csv_to_dict, save_dataset, save_parameter_to_csv, load_parameter_from_csv,
    save_limb_parameters, load_limb_parameters, merge_and_flip_export, extract_zones_from_file
)
from frame_utils import check_items_count, create_frames
from config_utils import (
    load_config,
    save_config,
    load_config_flags,
    load_parameter_names_into,
    load_perf_config,
    load_display_limits,
)
from data_utils import (
    csv_to_dict, save_dataset, save_parameter_to_csv, load_parameter_from_csv,
    save_limb_parameters, load_limb_parameters, merge_and_flip_export, extract_zones_from_file,
    FrameRecord
)
from perf_utils import PerfLogger
import tkinter as tk
from tkinter import ttk

def custom_confirm_close(root,saved: bool):
    win = tk.Toplevel(root)
    win.title("Close Application")
    win.geometry("600x300")
    win.resizable(True, True)
    win.grab_set()  # makes it modal

    msg = tk.Label(
        win,
        text="Do you want to close the application?\n\nProgress was saved." if saved else "Do you want to close the application?\n\n",
        font=("Segoe UI", 11),
        justify="center",
        wraplength=350
    )
    msg.pack(expand=True, fill="both", padx=20, pady=20)

    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=10)

    def on_yes():
        
        win.destroy()
        root.destroy()

    ttk.Button(btn_frame, text="OK", command=on_yes).pack(side="left", padx=10)
    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="left", padx=10)

class LabelingApp(tk.Tk):
    def __init__(self):
        super().__init__()

        # Core state (was previously in __init__)
        self.video = None
        self.video_name = None
        self.minimal_touch_lenght = None
        self.NEW_TEMPLATE = False

        # Build UI (creates frames, widgets, binds events; sets many attributes)
        build_ui(self)

        # Load config flags that affect UI sizing & behavior
        self.NEW_TEMPLATE, self.minimal_touch_lenght = load_config_flags()
        print("INFO: Loaded new template:", self.NEW_TEMPLATE)
        print("INFO: Loaded minimal touch length:", self.minimal_touch_lenght)
        perf_enabled, perf_log_every_s, perf_log_top_n = load_perf_config()
        self.perf = PerfLogger(
            enabled=perf_enabled,
            log_every_s=perf_log_every_s,
            top_n=perf_log_top_n,
        )
        print("INFO: Perf logging enabled:", perf_enabled)
        self.max_display_width, self.max_display_height = load_display_limits()
        print("INFO: Max display size:", self.max_display_width, "x", self.max_display_height)

        # Timeline and buffering helpers
        self.background_thread = Thread(target=self.background_update, daemon=True)
        self.background_thread_play = Thread(target=self.background_update_play, daemon=True)

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
    # --- Param helpers ---
    def _limb_param_key_for_index(self, idx: int) -> str:
        return f"Par{idx}"


    def _ensure_limb_params(self, rec: dict) -> dict:
        if not isinstance(rec.get("LimbParams"), dict):
            rec["LimbParams"] = {}
        return rec["LimbParams"]
    
    def _param_next_state(self, current):
        # Cycle: None -> "ON" -> "OFF" -> "ON" ...
        if current is None or current == "":
            return "ON"
        if current == "ON":
            return "OFF"
        if current == "OFF":
            return None
        return None  # current == "ON" or anything else

    def _param_key_for_index(self, idx: int) -> str:
        return f"Par{idx}"
        
    def on_note_changed(self, text: str):
        idx = self.frame_index
        b = self._ensure_bundle(idx)
        if b.get("Note") != text:
            b["Note"] = text
            self.mark_bundle_changed(idx)
    
    def _ensure_bundle(self, idx: int):
        b = self.video.frames.get(idx)
        if not isinstance(b, dict):
            # create an empty bundle (match your empty_bundle() structure)
            b = {
                "Note": None,
                "Params": {},
                "LH": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "RH": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "LL": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "RL": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                # no "Changed" by default
            }
            self.video.frames[idx] = b
        return b

    def _ensure_params(self, b: dict):
        if "Params" not in b or not isinstance(b["Params"], dict):
            b["Params"] = {}
        return b["Params"]
    # -------------------------
    # Callbacks & logic below are the same as before (moved unchanged)
    # -------------------------
    # in labeling_app.py, add helpers on the LabelingApp class
    def mark_bundle_changed(self, index=None):
        if self.video is None:
            return
        idx = self.video.current_frame
        
        b = self.video.frames.get(idx)
        if isinstance(b, dict):
            b["Changed"] = True
            self._timeline_dirty = True
            self._timeline2_dirty = True
            # optional: keep your terminal print
            if hasattr(self, "notify_bundle_changed"):
                self.notify_bundle_changed(idx)

    def notify_bundle_changed(self, index=None):
        if self.video is None:
            return
        idx = self.video.current_frame
        try:
            b = self.video.frames[idx]
            print("\n=== FrameBundle UPDATED ===")
            print(bundle_summary_str(b, frame_index=idx))
        except Exception as e:
            print(f"[notify_bundle_changed] could not print bundle at {idx}: {e}")
    
    def _get_bundle(self, frame):
        from data_utils import empty_bundle
        return self.video.frames.setdefault(frame, empty_bundle())

    def set_param_on_frame(self, frame, name, state):  # state: "ON"/"OFF"/None
        b = self._get_bundle(frame)
        params = b.get("Params", {}) or {}
        params[name] = state
        b["Params"] = params
    
    # --- Small helpers / global clicks ---
    def global_click(self, event):
        try:
            focus = self.focus_get()
        except Exception:
            focus = None
        if getattr(self, "note_entry", None) and focus == self.note_entry and event.widget != self.note_entry:
            self.focus_set()

    def navigate_left(self, event): self.next_frame(-1)
    
    def navigate_right(self, event): self.next_frame(1)

    def disable_arrow_keys(self, event=None):
        self.unbind("<Left>")
        self.unbind("<Right>")
        self.unbind("<Shift-Left>")
        self.unbind("<Shift-Right>")

    def enable_arrow_keys(self, event=None):
        self.bind("<Left>", self.navigate_left)
        self.bind("<Right>", self.navigate_right)
        self.bind("<Shift-Left>", lambda event: self.next_frame(-7))
        self.bind("<Shift-Right>", lambda event: self.next_frame(7))

    def update_last_mouse_position(self, event):
        self.last_mouse_x = event.x
        self.last_mouse_y = event.y

    def on_resize(self, event):
        print("INFO: Resized to {}x{}".format(event.width, event.height))
        self.img_buffer.clear()
        if self.video:
            self.display_first_frame()

    def on_mouse_wheel(self, event):
        if event.delta > 0 or getattr(event, "num", None) == 4:
            self.next_frame(-1)
        elif event.delta < 0 or getattr(event, "num", None) == 5:
            self.next_frame(1)

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

        target_attr = f"data{option}" if hasattr(self.video, f"data{option}") else "data"
        target_data = getattr(self.video, target_attr, {})

        rec = target_data.get(current_frame)
        if not isinstance(rec, dict):
            return  # nothing to delete

        xs = rec.get('X', [])
        ys = rec.get('Y', [])
        zones = rec.get('Zones', [])

        # normalize Zones to list-of-lists (to align with X/Y)
        if zones and isinstance(zones[0], (int, str)):
            zones = [[z] for z in zones]
            rec['Zones'] = zones

        if not xs or not ys:
            return

        # find closest point (euclidean in data coords)
        closest_idx = None
        closest_d2 = float('inf')
        for i, (x, y) in enumerate(zip(xs, ys)):
            d2 = (x - x_pos) * (x - x_pos) + (y - y_pos) * (y - y_pos)
            if d2 < closest_d2:
                closest_d2 = d2
                closest_idx = i

        # threshold in data coords (≈20 px in display); translates to 20/scale
        if closest_idx is not None and closest_d2 <= (20.0 / scale) ** 2:
            # delete this point and its zones bucket (if present)
            del xs[closest_idx]
            del ys[closest_idx]
            if isinstance(zones, list) and closest_idx < len(zones):
                del zones[closest_idx]

            if not xs:  # no points left -> clear the record to prevent export leakage
                target_data[current_frame] = {
                    "X": [],
                    "Y": [],
                    "Onset": "",          # important: clear onset
                    "Bodypart": option,   # keep limb name for consistency if needed
                    "Look": "No",
                    "Zones": [],
                    "Touch": None,
                }
            else:
                rec['X'] = xs
                rec['Y'] = ys
                rec['Zones'] = zones
                rec['Look'] = "No"  # or keep existing
                # keep Onset as-is for remaining points; you can also coerce if you prefer:
                # rec['Onset'] = "ON" if any remaining were added with ON else ""

            self.mark_bundle_changed()

    # --- Diagram init & routine render ---
    def init_diagram(self):
        # set up periodic dots refresh
        self.periodic_print_dot()

    def periodic_print_dot(self):
        self.diagram_canvas.delete("all")
        self.on_radio_click()  # keeps same behavior for image & palette
        dot_size = getattr(self, "dot_size", 10)
        scale = getattr(self, "diagram_scale", 1.0)
        if self.video and hasattr(self.video, 'data'):
            if self.option_var_1.get() == "RH":
                data = self.video.dataRH
            elif self.option_var_1.get() == "LH":
                data = self.video.dataLH
            elif self.option_var_1.get() == "RL":
                data = self.video.dataRL
            elif self.option_var_1.get() == "LL":
                data = self.video.dataLL
            self.find_last_green(data)
            frame_data: FrameRecord | dict = data.get(self.video.current_frame, {})
            xs = frame_data.get('X', []) if frame_data else []
            ys = frame_data.get('Y', []) if frame_data else []
            onset = frame_data.get('Onset', "OFF") if frame_data else "OFF"
            for x, y in zip(xs, ys):
                color = 'green' if onset == "ON" else 'red'
                self.diagram_canvas.create_oval(x*scale - dot_size, y*scale - dot_size,
                                                x*scale + dot_size, y*scale + dot_size, fill=color)
            # after drawing the filled dots from the current frame:
            array_xy = getattr(self.video, "last_green", [(None, None)])
            for (x_last, y_last) in array_xy:
                if x_last is not None:
                    self.diagram_canvas.create_oval(
                        x_last*scale - dot_size, y_last*scale - dot_size,
                        x_last*scale + dot_size, y_last*scale + dot_size,
                        outline='green', fill=''  # outline-only "ghost"
                    )
        self.after(300, self.periodic_print_dot)

    def on_diagram_click(self, event, right):
        onset = "ON" if right else "OFF"
        display_scale = getattr(self, "diagram_scale", 1.0)
        x_pos = event.x * (1.0 / display_scale)
        y_pos = event.y * (1.0 / display_scale)
        zone_results = list(self.find_image_with_white_pixel(x_pos, y_pos))  # list for stability

        current_frame = self.video.current_frame
        option = self.option_var_1.get()

        target_attr = f"data{option}" if hasattr(self.video, f"data{option}") else "data"
        target_data = getattr(self.video, target_attr, {})
        setattr(self.video, f"is_touch{option}", True)

        print(f"CLICK: before  frame={current_frame:>5} limb={option} onset={onset} zones={zone_results}")

        existing = target_data.get(current_frame)
        if not isinstance(existing, dict) or (not existing.get('X') and not existing.get('Y')):
            rec = {
                "X": [int(x_pos)],
                "Y": [int(y_pos)],
                "Onset": onset,
                "Bodypart": option,
                "Look": "No",
                # IMPORTANT: store zones per point (list-of-lists)
                "Zones": [zone_results],   # <- one entry per point
                "Touch": None,
            }
            target_data[current_frame] = rec
        else:
            rec = existing
            rec.setdefault('X', []).append(int(x_pos))
            rec.setdefault('Y', []).append(int(y_pos))

            # normalize Zones to list-of-lists if older shape is present
            zones = rec.get('Zones', [])
            if zones and zones and isinstance(zones[0], (int, str)):
                # legacy shape -> convert to list-of-lists pairing length with X
                zones = [[z] for z in zones]
            rec['Zones'] = zones
            zones.append(zone_results)  # one zones bucket per point

            rec['Bodypart'] = option
            rec['Onset'] = onset
            rec['Look'] = "No"

        self.mark_bundle_changed()

        rec = target_data.get(current_frame, {})
        print(
            f"CLICK:  after  frame={current_frame:>5} limb={option} onset={rec.get('Onset')} "
            f"points={len(rec.get('X', []))} zones_len={len(rec.get('Zones', []))}"
        )
    
    def preview_before_save(self, changed_only: bool = True):
        """
        Print a compact preview of what would be saved right now.
        Shows base/data/export dirs and per-frame summaries.
        """
        if not self.video:
            print("PREVIEW: No video loaded."); return


        base_dir = os.path.dirname(self.video.frames_dir)  # -> Labeled_data/<video>
        data_dir   = os.path.join(base_dir, "data")
        export_dir = os.path.join(base_dir, "export")
        unified_path = os.path.join(data_dir, f"{self.video_name}_unified.csv")
        export_path = os.path.join(export_dir, f"{self.video_name}_export.csv")

        print("\n===== PREVIEW: Save destinations =====")
        print(f"Base:   {base_dir}")
        print(f"Data:   {data_dir}")
        print(f"Export: {export_dir}")
        print(f"Unified CSV (will write changed-only): {unified_path}")
        print(f"Export  CSV (will write all frames):   {export_path}")

        from data_utils import preview_lines_for_save
        lines = preview_lines_for_save(self.video.frames, self.video.total_frames, changed_only=changed_only)

        if not lines:
            print("PREVIEW: No changed frames to save.")
        else:
            print("===== PREVIEW: Frames to be saved =====")
            for line in lines:
                print(line)
            print("===== PREVIEW: End =====\n")
    
    def find_last_green(self, _unused_data=None):
        """
        Set self.video.last_green to the last 'ON' points for the selected limb
        at or before the current frame; clear when an 'OFF' is encountered first.
        """
        if not (self.video and isinstance(self.video.frames, dict)):
            self.video.last_green = [(None, None)]
            return

        limb = self.option_var_1.get()  # "LH"/"RH"/"LL"/"RL"
        start = self.video.current_frame

        # Iterate frames in reverse up to current frame
        for f in sorted(self.video.frames.keys(), reverse=True):
            if f > start:
                continue
            b = self.video.frames.get(f, {}) or {}
            rec = b.get(limb, {}) if isinstance(b, dict) else {}

            onset = rec.get("Onset")
            if onset == "OFF":
                # an explicit Off cancels the ghost
                self.video.last_green = [(None, None)]
                return
            if onset == "ON":
                xs = rec.get("X", []) or []
                ys = rec.get("Y", []) or []
                self.video.last_green = list(zip(xs, ys)) if xs and ys else [(None, None)]
                return

        # nothing found
        self.video.last_green = [(None, None)]

    def on_radio_click(self):
        if self.option_var_1.get() == "RH":
            image_path = "icons/RH_new_template.png" if self.NEW_TEMPLATE else "icons/RH.png"
        elif self.option_var_1.get() == "LH":
            image_path = "icons/LH_new_template.png" if self.NEW_TEMPLATE else "icons/LH.png"
        elif self.option_var_1.get() == "RL":
            image_path = "icons/RL_new_template.png" if self.NEW_TEMPLATE else "icons/RL.png"
        else:  # LL
            image_path = "icons/LL_new_template.png" if self.NEW_TEMPLATE else "icons/LL.png"

        img = Image.open(image_path)
        scale = getattr(self, "diagram_scale", 1.0)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(img)
        self.diagram_canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.draw_timeline()
        self.draw_timeline2()
        self.update_limb_parameter_buttons()

    # --- Diagram helpers used outside ---
    def find_image_with_white_pixel(self, x, y):
        import cv2, os
        with self.perf.time("find_image_with_white_pixel"):
            x = int(x); y = int(y)
            directory = "icons/zones3_new_template" if self.NEW_TEMPLATE else "icons/zones3"
            for filename in os.listdir(directory):
                fp = os.path.join(directory, filename)
                if os.path.isfile(fp) and fp.lower().endswith(('.png', '.jpg', '.jpeg')):
                    image = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
                    if image is None:
                        continue
                    if image[y, x] == 0:
                        return [filename.rsplit('.', 1)[0]]
            return ['NN']

    # --- Timelines ---
    def on_timeline_click(self, event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline_canvas.winfo_width()
            frame_number = int(click_position / canvas_width * self.video.number_frames_in_zone)
            if self.video.total_frames >= frame_number + self.video.number_frames_in_zone * self.video.current_frame_zone:
                self.video.current_frame = frame_number + self.video.number_frames_in_zone * self.video.current_frame_zone
                self.display_first_frame()
            else:
                print("ERROR: Frame Number")

    def on_timeline2_click(self, event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline2_canvas.winfo_width()
            new_frame = int((click_position / canvas_width) * self.video.total_frames)
            self.video.current_frame = new_frame
            self.video.current_frame_zone = new_frame // self.video.number_frames_in_zone
            print("INFO: Jumping to exact frame:", new_frame)
            self.display_first_frame()

    def parameter_color_at_frame(self, frame):
        b = self._get_bundle(frame)
        params = (b.get("Params") or {})
        # If any param ON => green; else if any OFF => red; else None
        if any(v == "ON" for v in params.values()): return "green"
        if any(v == "OFF" for v in params.values()): return "red"
        return None

    def draw_timeline(self):
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

            sector_width = canvas_width / self.video.number_frames_in_zone if self.video.number_frames_in_zone else 1
            offset = self.video.number_frames_in_zone * zone
            top = 0; bottom = canvas_height

            if needs_full:
                self.timeline_canvas.delete("all")
                data_source = {
                    'RH': self.video.dataRH, 'LH': self.video.dataLH, 'RL': self.video.dataRL, 'LL': self.video.dataLL
                }
                data = data_source.get(limb, self.video.data)
                self.is_touch_timeline = False if zone == 0 else self.video.touch_to_next_zone[zone]

                def get_color(frame_idx, data):
                    if frame_idx > self.video.total_frames: return 'black'
                    details = data.get(frame_idx, {})
                    xs = details.get('X', [])
                    if not xs:
                        return self.color_during if self.is_touch_timeline else 'lightgrey'
                    if len(xs) >= 1 and xs[0] is not None:
                        if details.get('Onset') == 'ON':
                            self.is_touch_timeline = True; return 'lightgreen'
                        else:
                            self.is_touch_timeline = False; return '#E57373'
                    return self.color_during if self.is_touch_timeline else 'lightgrey'

                for frame in range(self.video.number_frames_in_zone):
                    left = frame * sector_width
                    right = left + sector_width
                    frame_offset = frame + offset
                    color = get_color(frame_offset, data)
                    self.timeline_canvas.create_rectangle(left, top, right, bottom, fill=color, outline='black')
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

                self._timeline_dirty = False
                self._timeline_last_zone = zone
                self._timeline_last_limb = limb
                self._timeline_canvas_size = (canvas_width, canvas_height)
                self._timeline_playhead_id = None

            # Update playhead only
            frame_in_zone = self.video.current_frame - offset
            if 0 <= frame_in_zone < self.video.number_frames_in_zone:
                left = frame_in_zone * sector_width
                right = left + sector_width
                if self._timeline_playhead_id is None:
                    self._timeline_playhead_id = self.timeline_canvas.create_rectangle(
                        left, top, right, bottom, fill='dodgerblue', outline='black'
                    )
                else:
                    self.timeline_canvas.coords(self._timeline_playhead_id, left, top, right, bottom)

    def draw_timeline2(self):
        with self.perf.time("draw_timeline2"):
            if not (self.video and self.video.total_frames > 0):
                return

            canvas_width  = self.timeline2_canvas.winfo_width()
            canvas_height = self.timeline2_canvas.winfo_height()
            limb = self.option_var_1.get()  # currently selected limb ("LH","RH","LL","RL")
            needs_full = (
                self._timeline2_dirty
                or self._timeline2_last_limb != limb
                or self._timeline2_canvas_size != (canvas_width, canvas_height)
            )

            if needs_full:
                self.timeline2_canvas.delete("all")
                # --- First pass: collect intervals (for yellow fill) and all lines to draw later ---
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

                    x = (frame / self.video.total_frames) * canvas_width

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

                    # --- GLOBAL Parameter markers (non-limb) — always visible ---
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
                # 1) Yellow fills
                for x1, x2 in intervals:
                    self.timeline2_canvas.create_rectangle(x1, 0, x2, canvas_height, fill='yellow', outline='')

                # 2) Global parameter lines
                for x, c in param_lines:
                    self.timeline2_canvas.create_line(x, 0, x, canvas_height, fill=c, width=2)

                #    Limb-specific parameter ticks for the selected limb
                for x, c in limb_param_lines:
                    self.timeline2_canvas.create_line(x, 0, x, canvas_height, fill=c, width=2)

                # 3) On/Off edge markers
                for x in on_lines:
                    self.timeline2_canvas.create_line(x, 0, x, canvas_height, fill='green', width=1)
                for x in off_lines:
                    self.timeline2_canvas.create_line(x, 0, x, canvas_height, fill='red', width=1)

                self._timeline2_dirty = False
                self._timeline2_last_limb = limb
                self._timeline2_canvas_size = (canvas_width, canvas_height)
                self._timeline2_playhead_id = None

            # Current frame indicator only
            current_pos = (self.video.current_frame / self.video.total_frames) * canvas_width
            margin = 2
            current_pos = min(max(current_pos, margin), canvas_width - margin)
            if self._timeline2_playhead_id is None:
                self._timeline2_playhead_id = self.timeline2_canvas.create_line(
                    current_pos, 0, current_pos, canvas_height, fill='dodgerblue', width=2
                )
            else:
                self.timeline2_canvas.coords(self._timeline2_playhead_id, current_pos, 0, current_pos, canvas_height)
    
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

            current_time = self.video.current_frame / self.video.frame_rate * 1000
            total_time = self.video.total_frames / self.video.frame_rate * 1000
            self.time_counter_label.config(text=f"{format_time(int(current_time))} / {format_time(int(total_time))}")

        else:
            self.frame_counter_label.config(text="0 / 0")

        self.video.current_frame_zone = int(self.video.current_frame / self.video.number_frames_in_zone)

    # --- Playback & buffer ---
    def background_update(self, frame_number=None):
        import time
        was_missing = False
        while True:
            time.sleep(0.01)
            if self.video is not None:
                with self.perf.time("background_update"):
                    frame_number = self.video.current_frame
                    if frame_number < 0 or frame_number > self.video.total_frames:
                        return
                    start_frame = max(0, frame_number - 30)
                    end_frame = min(self.video.total_frames, frame_number + 50)

                    buffer_loaded = True
                    current_frame_loaded = frame_number in self.img_buffer

                    if not current_frame_loaded:
                        buffer_loaded = False
                        self.load_frame(frame_number)
                        was_missing = True

                    for i in range(frame_number, end_frame + 1):
                        if i not in self.img_buffer:
                            buffer_loaded = False
                            self.load_frame(i)

                    for i in range(frame_number, start_frame - 1, -1):
                        if i not in self.img_buffer:
                            buffer_loaded = False
                            self.load_frame(i)

                    buffer_range = 200
                    min_keep = max(0, frame_number - buffer_range)
                    max_keep = min(self.video.total_frames, frame_number + buffer_range)
                    frames_to_remove = [k for k in self.img_buffer if k < min_keep or k > max_keep]
                    for k in frames_to_remove: del self.img_buffer[k]

                    if current_frame_loaded:
                        self.loading_label.config(text="Buffer Loaded", bg='lightgreen')
                    else:
                        self.loading_label.config(text="Buffer Loading", bg='#E57373')

                    if was_missing and current_frame_loaded:
                        self.after(0, self.display_first_frame)
                    was_missing = not current_frame_loaded

    def background_update_play(self):
        import time
        while True:
            if self.play and self.video is not None:
                start = time.perf_counter()
                self.next_frame(1, play=True)
                if self.video.current_frame % 10 == 0:
                    self.after(0, self.draw_timeline)
                interval = 1.0 / self.frame_rate if self.frame_rate else 0.04
                elapsed = time.perf_counter() - start
                time.sleep(max(0.0, interval - elapsed))
            else:
                time.sleep(0.05)

    def load_frame(self, frame_number):
        from PIL import Image
        import os
        try:
            with self.perf.time("load_frame_total"):
                frame_path = os.path.join(self.video.frames_dir, f"frame{frame_number}.jpg")
                with self.perf.time("load_frame_open"):
                    img = Image.open(frame_path)
                with self.perf.time("load_frame_resize"):
                    img = self.resize_frame(img)
                with self.perf.time("load_frame_photo"):
                    photo_img = ImageTk.PhotoImage(img)
                self.img_buffer[frame_number] = photo_img
        except Exception as e:
            print(f"ERROR: Opening or processing frame {frame_number}: {str(e)}")

    def resize_frame(self, img):
        with self.perf.time("resize_frame"):
            display_width = self.video_frame.winfo_width()
            display_height = self.video_frame.winfo_height()
            if self.max_display_width:
                display_width = min(display_width, self.max_display_width)
            if self.max_display_height:
                display_height = min(display_height, self.max_display_height)
            if display_width <= 0 or display_height <= 0:
                return img
            original_width, original_height = img.size
            aspect_ratio = original_width / original_height
            if display_width / display_height > aspect_ratio:
                new_width = int(display_height * aspect_ratio); new_height = display_height
            else:
                new_width = display_width; new_height = int(display_width / aspect_ratio)
            self.old_width = new_width; self.old_height = new_height
            return img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    def display_first_frame(self, frame_number=None):
        with self.perf.time("display_first_frame"):
            if frame_number is None:
                frame_number = self.video.current_frame
            else:
                self.video.current_frame = frame_number
            if frame_number < 0 or frame_number > self.video.total_frames:
                print("ERROR: Frame number out of bounds."); return
            if frame_number in self.img_buffer:
                photo_img = self.img_buffer[frame_number]
                if hasattr(self, 'frame_label') and self.frame_label:
                    self.frame_label.configure(image=photo_img)
                else:
                    self.frame_label = tk.Label(self.video_frame, image=photo_img)
                    self.frame_label.pack(expand=True)
                self.loading_label.config(text="Buffer Loaded", bg='lightgreen')
                self.image = photo_img
            else:
                print("INFO: Frame not in buffer.")
                self.loading_label.config(text="Buffer Loading", bg='#E57373')

            self.update_note_entry()
            self.update_frame_counter()
            self.update_limb_parameter_buttons()
            self.update_button_colors()

    # --- Parameter toggles & coloring ---
    def update_button_colors(self):
        if self.video is None:
            return
        idx = self.video.current_frame
        b = self.video.frames.get(idx, {})
        params = (b.get("Params") or {})

        for i, btn in ((1, self.par1_btn), (2, self.par2_btn), (3, self.par3_btn)):
            key = self._param_key_for_index(i)
            state = params.get(key)
            if state == "ON":
                btn.config(bg="lightgreen")
            elif state == "OFF":
                btn.config(bg="#E57373")
            else:
                btn.config(bg="lightgrey")

    def parameter_dic_insert(self, parameter_index: int):
        """Toggle Param_i (1..3) for the CURRENT frame directly on the bundle."""
        if self.video is None:
            return
        idx = self.video.current_frame
        b = self._ensure_bundle(idx)
        params = self._ensure_params(b)

        key = self._param_key_for_index(parameter_index)
        prev = params.get(key)
        new_state = self._param_next_state(prev)
        params[key] = new_state
        b["Params"] = params

        # color the right button immediately
        button = {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn}[parameter_index]
        if new_state == "ON":
            button.config(bg="lightgreen")
        elif new_state == "OFF":
            button.config(bg="#E57373")
        else:
            button.config(bg="lightgrey")

        # mark frame dirty, print, and refresh timeline
        self.mark_bundle_changed(idx)
        self.draw_timeline()

    def toggle_limb_parameter(self, param_number: int):
        limb = self.option_var_1.get()
        frame = self.video.current_frame

        # ensure bundle & this limb's record exist
        b = self._ensure_bundle(frame)
        rec = b.get(limb) or {"X": [], "Y": [], "Onset": "", "Bodypart": limb, "Look": "", "Zones": [], "Touch": None}
        b[limb] = rec

        limb_params = self._ensure_limb_params(rec)
        key = self._limb_param_key_for_index(param_number)
        prev = limb_params.get(key)
        # None -> ON -> OFF -> None
        if prev is None or prev == "":
            new_state = "ON"
        elif prev == "ON":
            new_state = "OFF"
        elif prev == "OFF":
            new_state = "None"
        else:
            new_state = None
        limb_params[key] = new_state

        # reflect on button color
        btn = {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn}[param_number]
        if new_state == "ON":
            btn.config(bg="lightgreen")
        elif new_state == "OFF":
            btn.config(bg="#E57373")
        else:
            btn.config(bg="lightgray")

        # mark & redraw (so timeline updates)
        self.mark_bundle_changed(frame)
        self.draw_timeline()

    def update_limb_parameter_buttons(self):
        if not self.video: return
        limb = self.option_var_1.get()
        frame = self.video.current_frame
        b = self.video.frames.get(frame, {})
        rec = b.get(limb, {}) if isinstance(b, dict) else {}
        limb_params = rec.get("LimbParams", {}) if isinstance(rec, dict) else {}

        for i, btn in ((1, self.limb_par1_btn), (2, self.limb_par2_btn), (3, self.limb_par3_btn)):
            key = self._limb_param_key_for_index(i)
            state = limb_params.get(key)
            if state == "ON":
                btn.config(bg="lightgreen")
            elif state == "OFF":
                btn.config(bg="#E57373")
            else:
                btn.config(bg="lightgray")
    
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
                colors.append("green")
            elif val == "OFF":
                colors.append("#E57373")
            else:
                colors.append(None)
        return colors

    # --- Notes & selection ---
    def select_frame(self):
        frame = self.note_entry.get()
        try:
            frame_int = int(frame)
        except ValueError:
            print("Error selecting frame: The frame number must be a valid integer.")
            self.note_entry.delete(0, 'end'); return
        if self.video is not None:
            if frame_int < 0 or frame_int > self.video.total_frames:
                print("Error selecting frame: Out of range!")
                self.note_entry.delete(0, 'end'); return
            self.video.current_frame = frame_int
            self.update_frame_counter()
            self.display_first_frame()
        else:
            print("Error selecting frame: No video loaded!")
        self.note_entry.delete(0, 'end')

    def save_note(self):
        print("INFO: Saving note...")
        self.enable_arrow_keys()

        idx = self.video.current_frame
        note_text = (self.note_entry.get() or "").strip()

        # ensure bundle exists, then update Note
        b = self.video.frames.get(idx)
        if not isinstance(b, dict):
            # If you have data_utils.empty_bundle(), prefer importing and using that here.
            b = {"Note": None, "Params": {}, "LH": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "RH": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "LL": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "RL": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []}}
            self.video.frames[idx] = b

        prev = (b.get("Note") or "").strip()
        new_val = note_text if note_text else None

        if prev != (new_val or ""):
            b["Note"] = new_val
            # mark bundle dirty + print (uses your existing helpers)
            if hasattr(self, "mark_bundle_changed"):
                self.mark_bundle_changed(idx)
            elif isinstance(b, dict):
                b["Changed"] = True
            if hasattr(self, "notify_bundle_changed"):
                self.notify_bundle_changed(idx)

        
        print(f"INFO: Note saved for frame {idx}: {note_text}")
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
            note_text = (b.get("Note") or "")  # bundle-first

        # Fallback (only if you still have legacy self.video.notes around)
        if not note_text and hasattr(self.video, "notes"):
            note_text = self.video.notes.get(idx, "") or ""

        self.note_entry.delete(0, tk.END)
        self.note_entry.insert(0, note_text)

    # --- Save / Export ---
    def save_data(self):
        self.preview_before_save(changed_only=True)
        print("INFO: Saving (unified & export)...")
        

        base_dir = os.path.dirname(self.video.frames_dir)  # -> Labeled_data/<video>
        data_dir   = os.path.join(base_dir, "data")
        export_dir = os.path.join(base_dir, "export")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(export_dir, exist_ok=True)
        print(f"DEBUG: Base dir:   {base_dir}")
        print(f"DEBUG: Data dir:   {data_dir}")
        print(f"DEBUG: Export dir: {export_dir}")
        print(f"DEBUG: Frames dir: {self.video.frames_dir}")
        unified_path = os.path.join(data_dir, f"{self.video_name}_unified.csv")
        print(f"DEBUG: Writing unified dataset → {unified_path}")

        from data_utils import save_unified_dataset, export_from_unified, extract_zones_from_file
        save_unified_dataset(unified_path, self.video.total_frames, self.video.frames)

        clothes_list = extract_zones_from_file(
            self.video.clothes_file_path or self.video.dataNotes_path_to_csv.replace('_notes.csv', '_clothes.txt')
        )
        export_path = os.path.join(export_dir, f"{self.video_name}_export.csv")
        print(f"DEBUG: Writing export dataset → {export_path}")

        # labeling_app.py (inside save_data, before export_from_unified call)
        param_labels = {
            "Parameter_1": (self.par1_btn.cget("text") or "Par1"),
            "Parameter_2": (self.par2_btn.cget("text") or "Par2"),
            "Parameter_3": (self.par3_btn.cget("text") or "Par3"),
        }
        limb_param_labels = {
            "XX_Parameter_1": (self.limb_par1_btn.cget("text") or "LimbPar1"),
            "XX_Parameter_2": (self.limb_par2_btn.cget("text") or "LimbPar2"),
            "XX_Parameter_3": (self.limb_par3_btn.cget("text") or "LimbPar3"),
        }
        # NEW: write JSON sidecar with metadata (instead of stuffing CSV header)
        from data_utils import write_export_metadata
        meta_path = os.path.join(export_dir, f"{self.video_name}_metadata.json")
        write_export_metadata(
            meta_path=meta_path,
            program_version=self.video.program_version,
            video_name=self.video_name,
            labeling_mode=self.labeling_mode,
            frame_rate=self.frame_rate,
            clothes_list=clothes_list,
            param_labels=param_labels,
            limb_param_labels=limb_param_labels,
        )

        export_from_unified(
            self.video.frames,
            export_path,
            self.video.program_version,
            self.video_name,
            self.labeling_mode,
            self.frame_rate,
            clothes_list,
            total_frames=self.video.total_frames,
            param_labels=param_labels,
            limb_param_labels=limb_param_labels,
        )
        print("INFO: Save completed successfully.")
        for f, b in self.video.frames.items():
            if isinstance(b, dict) and b.get("Changed"):
                b["Changed"] = False
        print("DEBUG: Cleared bundle 'Changed' flags after save.")

    # --- Analysis / Sort / Playback buttons ---
    def analysis(self):
        if self.video:
            self.save_data()
            data_dir = os.path.dirname(self.video.dataRH_path_to_csv)
            base_dir = os.path.dirname(data_dir)
            plots_path = os.path.join(base_dir, "plots")
            analysis.do_analysis(data_dir, plots_path, self.video_name, debug=False, frame_rate=self.frame_rate)

    def play_video(self):
        if self.video is None:
            print("ERROR: First select video")
            return
        if not self.play:
            self.play = True
            if not self.play_thread_on:
                self.play_thread_on = True
                if not self.background_thread_play.is_alive():
                    self.background_thread_play.start()

    def stop_video(self):
        self.play = False

    def sort_frames(self):
        self.save_data()
        base_dir = os.path.dirname(os.path.dirname(self.video.dataRH_path_to_csv))
        csv_path = os.path.join(base_dir, "export", f"{self.video_name}_export.csv")
        images_dir = self.video.frames_dir
        output_dir = os.path.join(base_dir, "sorted_frames")
        os.makedirs(output_dir, exist_ok=True)
        try:
            process_touch_data_strict_transitions(csv_path, images_dir, output_dir)
            print(f"INFO: Sorted frames written to {output_dir}")
        except Exception as e:
            print(f"ERROR in sort_frames: {e}")

    # --- Video load & init ---
    def ask_labeling_mode(self):
        mode_window = tk.Toplevel(self)
        mode_window.title("Select Labeling Mode")
        mode_window.geometry("480x220")
        mode_window.grab_set()
        label = tk.Label(mode_window, text="Choose the labeling mode:", font=("Arial", 12))
        label.pack(pady=10)

        def set_mode(mode):
            self.labeling_mode = mode
            bg = 'yellow' if mode == 'Reliability' else 'lightgreen'
            self.mode_label.config(text=f"Mode: {mode}", bg=bg)
            mode_window.destroy()

        tk.Button(mode_window, text="Normal", command=lambda: set_mode("Normal"), width=15).pack(pady=5)
        tk.Button(mode_window, text="Reliability", command=lambda: set_mode("Reliability"), width=15).pack(pady=5)
        mode_window.wait_window()

    def load_video(self):
        if self.video is not None:
            print("INFO: Saving before loading new video.")
            self.save_data()
            self.save_last_position()

        self.ask_labeling_mode()
        if not hasattr(self, 'labeling_mode'):
            print("INFO: No mode selected, cancelling video load."); return

        video_path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=(
                ("Video files", "*.mp4 *.MP4 *.mov *.MOV *.avi *.AVI *.mkv *.MKV *.flv *.FLV *.wmv *.WMV"),
                ("All files", "*.*"),
            ),
        )
        if not video_path: return

        self.video = Video(video_path)
        self.video.frame_rate = round(cv2.VideoCapture(video_path).get(cv2.CAP_PROP_FPS), 1)
        self.frame_rate = self.video.frame_rate
        self.framerate_label.config(text=f"Frame Rate: {self.frame_rate}")
        min_lenght_in_frames = self.minimal_touch_lenght * self.frame_rate / 1000
        self.min_touch_lenght_label.config(text=f"Minimal Touch Length: {min_lenght_in_frames}")
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        if self.labeling_mode == "Reliability": video_name += "_reliability"
        self.video_name = video_name

        base_dir = os.path.join("Labeled_data", video_name)
        data_dir = os.path.join(base_dir, "data")
        frames_dir = os.path.join(base_dir, "frames")
        plots_dir = os.path.join(base_dir, "plots")
        export_dir = os.path.join(base_dir, "export")
        for d in (data_dir, frames_dir, plots_dir, export_dir): os.makedirs(d, exist_ok=True)
        self.video.frames_dir = frames_dir

        # --- Unified-first load ---
        unified_path = os.path.join(data_dir, f"{video_name}_unified.csv")
        export_path  = os.path.join(export_dir, f"{video_name}_export.csv")

        from data_utils import (
            load_unified_dataset, empty_bundle,
            import_unified_from_export, save_unified_dataset
        )

        # Load unified (robust: handles 0-byte / header-only files)
        self.video.frames = load_unified_dataset(unified_path) or {}

        # Rebind LimbViews to the current dict (CRITICAL)
        self.video.dataRH._frames = self.video.frames
        self.video.dataLH._frames = self.video.frames
        self.video.dataRL._frames = self.video.frames
        self.video.dataLL._frames = self.video.frames

        # Fallback: if unified is empty but export exists, recover once from export
        if not self.video.frames and os.path.exists(export_path):
            print("INFO: Unified empty; importing from export for recovery…")
            self.video.frames = import_unified_from_export(export_path) or {}

            # Rebind again to the recovered dict
            self.video.dataRH._frames = self.video.frames
            self.video.dataLH._frames = self.video.frames
            self.video.dataRL._frames = self.video.frames
            self.video.dataLL._frames = self.video.frames

            # Persist immediately so next launch is fast & does not depend on export
            os.makedirs(data_dir, exist_ok=True)
            save_unified_dataset(unified_path, self.video.total_frames, self.video.frames, changed_only=False)
            print(f"INFO: Recovery wrote unified → {unified_path}")

        


        # Always set these paths (other features derive folders from them)
        for suffix in ['RH', 'LH', 'RL', 'LL']:
            csv_path = os.path.join(data_dir, f"{video_name}{suffix}.csv")
            setattr(self.video, f"data{suffix}_path_to_csv", csv_path)

        # If unified did not exist BUT legacy limb CSVs do, migrate them once into self.video.frames
        if not self.video.frames:
            print("INFO: No unified file found; attempting legacy CSV migration...")
            any_legacy = False
            for suffix in ['RH', 'LH', 'RL', 'LL']:
                csv_path = getattr(self.video, f"data{suffix}_path_to_csv")
                if os.path.exists(csv_path):
                    any_legacy = True
                    d = csv_to_dict(csv_path)
                    for fr, rec in d.items():
                        b = self.video.frames.setdefault(fr, empty_bundle())
                        b[suffix] = rec
            if any_legacy:
                print("INFO: Legacy limb CSVs merged into unified in-memory store.")
            else:
                print("INFO: Starting with an empty unified store.")

        # Parameter CSVs
        for name in ['parameter_1', 'parameter_2', 'parameter_3']:
            csv_path = os.path.join(data_dir, f"{video_name}{name}.csv")
            setattr(self.video, f"data{name}_path_to_csv", csv_path)

        

        # Names for parameters (update button text)
        load_parameter_names_into(
            self.video,
            {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn},
            {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn}
        )

        # Frames generation/check
        if not check_items_count(frames_dir, self.video.total_frames):
            print("ERROR: Number of frames is different, creating new frames")
            create_frames(video_path, frames_dir, self.labeling_mode, self.video_name)
        else:
            print("INFO: Number of frames is correct")

        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._timeline_last_zone = None
        self._timeline_last_limb = None
        self._timeline2_last_limb = None
        self._timeline_canvas_size = (0, 0)
        self._timeline2_canvas_size = (0, 0)
        self._timeline_playhead_id = None
        self._timeline2_playhead_id = None

        # Restore last position (if present) before initial draw
        self.restore_last_position(data_dir, video_name)

        # Initial draw
        self.display_first_frame()
        self.draw_timeline()
        self.draw_timeline2()
        self.name_label.config(
            text=f"Video: {video_name} | FPS: {self.frame_rate} | Version: {self.video.program_version}"
        )

        if not self.background_thread.is_alive():
            self.background_thread.start()
        else:
            self.img_buffer.clear()
            print("INFO: Thread already running.")

        # Notes
        self.video.notes = {}
        self.video.dataNotes_path_to_csv = os.path.join(data_dir, f"{video_name}_notes.csv")
        if os.path.exists(self.video.dataNotes_path_to_csv):
            with open(self.video.dataNotes_path_to_csv, mode='r', newline='') as csv_file:
                reader = csv.reader(csv_file)
                next(reader, None)
                for row in reader:
                    if len(row) == 2:
                        frame = int(row[0]); note = row[1]
                        self.video.notes[frame] = note
            print("INFO: Notes loaded successfully.")
        self.update_note_entry()

        # Limb parameters
        p1, p2, p3 = load_limb_parameters(os.path.join(data_dir, f"{video_name}_limb_parameters.csv"))
        self.video.limb_parameter1, self.video.limb_parameter2, self.video.limb_parameter3 = p1, p2, p3

        # Clothes file presence => colorize button
        self.video.clothes_file_path = os.path.join(data_dir, f"{video_name}_clothes.txt")
        if self.video.clothes_file_path and os.path.exists(self.video.clothes_file_path):
            with open(self.video.clothes_file_path, 'r') as f:
                if len(f.readlines()) > 1:
                    self.cloth_btn.config(bg="lightgreen")

        self.load_video_btn.config(state=tk.DISABLED, bg="gray", fg='lightgray')
        for b in self.video.frames.values():
            if isinstance(b, dict):
                b["Changed"] = False
        print("INFO: Welcome back! I wish you happy labeling session! :)")

    # --- Clothes side window ---
    def open_cloth_app(self):
        if self.video is None:
            print("ERROR: First select video")
        else:
            ClothApp(self, self.update_data_clothes)

    def update_data_clothes(self, dots):
        self.data_clothes = dots
        print("Data clothes updated:", self.data_clothes)
        self.save_clothes_to_text()
        self.cloth_btn.config(bg="lightgreen")

    def save_clothes_to_text(self):
        print("INFO: Saving clothes...")
        if not self.video.dataRH_path_to_csv:
            print("ERROR: Data path is not set"); return
        data_folder = os.path.dirname(self.video.dataRH_path_to_csv)
        os.makedirs(data_folder, exist_ok=True)
        text_file_path = os.path.join(data_folder, f"{self.video_name}_clothes.txt")
        self.video.clothes_file_path = text_file_path
        with open(text_file_path, mode='w') as f:
            f.write("Coordinates and Zones for Clothing Items:\n")
            for dot_id, (x, y) in self.data_clothes.items():
                zones = self.find_image_with_white_pixel(2*x, 2*y)
                zones_str = ','.join(zones)
                f.write(f"Dot ID {dot_id}: X={x}, Y={y}, Zones={zones_str}\n")
        print("INFO: Clothes saved")

    

    def on_close(self):
        saved = False
        if self.video is not None:
            self.save_data()
            self.save_last_position()
            saved = True
        custom_confirm_close(self, saved)

    def _last_position_path(self, data_dir: str, video_name: str) -> str:
        return os.path.join(data_dir, f"{video_name}_last_position.json")

    def save_last_position(self):
        if self.video is None or self.video_name is None:
            return
        data_dir = os.path.join("Labeled_data", self.video_name, "data")
        os.makedirs(data_dir, exist_ok=True)
        path = self._last_position_path(data_dir, self.video_name)
        try:
            payload = {
                "frame": int(self.video.current_frame),
                "total_frames": int(self.video.total_frames),
            }
            with open(path, "w") as f:
                json.dump(payload, f)
            print(f"INFO: Saved last position → {path}")
        except Exception as e:
            print(f"WARNING: Failed to save last position: {e}")

    def restore_last_position(self, data_dir: str, video_name: str):
        path = self._last_position_path(data_dir, video_name)
        if not os.path.exists(path):
            return
        try:
            with open(path, "r") as f:
                payload = json.load(f) or {}
            frame = int(payload.get("frame", 0))
            frame = max(0, min(self.video.total_frames, frame))
            self.video.current_frame = frame
            self.video.current_frame_zone = int(self.video.current_frame / self.video.number_frames_in_zone)
            print(f"INFO: Restored last position: frame {self.video.current_frame}")
        except Exception as e:
            print(f"WARNING: Failed to restore last position: {e}")

    # --- Settings ---
    def open_settings(self):
        if getattr(self, "_settings_win", None) and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return

        cfg = load_config()
        win = tk.Toplevel(self)
        win.title("Settings")
        win.resizable(False, False)
        self._settings_win = win

        def _v(key, default):
            return cfg.get(key, default)

        vars_map = {
            "perf_enabled": tk.BooleanVar(value=bool(_v("perf_enabled", False))),
            "perf_log_every_s": tk.StringVar(value=str(_v("perf_log_every_s", 2.0))),
            "perf_log_top_n": tk.StringVar(value=str(_v("perf_log_top_n", 6))),
            "max_display_width": tk.StringVar(value=str(_v("max_display_width", 0))),
            "max_display_height": tk.StringVar(value=str(_v("max_display_height", 0))),
            "diagram_scale": tk.StringVar(value=str(_v("diagram_scale", 1.0))),
            "dot_size": tk.StringVar(value=str(_v("dot_size", 10))),
        }

        row = 0
        tk.Label(win, text="Performance").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))
        row += 1
        tk.Checkbutton(win, text="Enable perf logging", variable=vars_map["perf_enabled"]).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8
        )
        row += 1
        tk.Label(win, text="Perf log interval (s)").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["perf_log_every_s"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Perf top N").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["perf_log_top_n"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1

        tk.Label(win, text="Display").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(10, 4))
        row += 1
        tk.Label(win, text="Max display width (0 = auto)").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["max_display_width"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Max display height (0 = auto)").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["max_display_height"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Diagram scale").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["diagram_scale"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Dot size").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["dot_size"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1

        def parse_int(value, key):
            if value is None:
                return 0
            s = str(value).strip()
            if s == "":
                return 0
            try:
                return int(float(s))
            except Exception:
                raise ValueError(f"{key} must be a number")

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

        def apply_settings(close=False):
            try:
                new_cfg = dict(cfg)
                new_cfg["perf_enabled"] = bool(vars_map["perf_enabled"].get())
                new_cfg["perf_log_every_s"] = parse_float(vars_map["perf_log_every_s"].get(), "perf_log_every_s")
                new_cfg["perf_log_top_n"] = parse_int(vars_map["perf_log_top_n"].get(), "perf_log_top_n")
                new_cfg["max_display_width"] = parse_int(vars_map["max_display_width"].get(), "max_display_width")
                new_cfg["max_display_height"] = parse_int(vars_map["max_display_height"].get(), "max_display_height")
                new_cfg["diagram_scale"] = parse_float(vars_map["diagram_scale"].get(), "diagram_scale")
                new_cfg["dot_size"] = parse_float(vars_map["dot_size"].get(), "dot_size")
            except ValueError as e:
                messagebox.showerror("Invalid settings", str(e), parent=win)
                return

            save_config(new_cfg)
            self.apply_runtime_settings(new_cfg)
            if close:
                win.destroy()

        btn_frame = tk.Frame(win)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(10, 8))
        tk.Button(btn_frame, text="Apply", command=lambda: apply_settings(close=False)).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Apply & Close", command=lambda: apply_settings(close=True)).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Close", command=win.destroy).pack(side="left", padx=5)

    def apply_runtime_settings(self, cfg: dict):
        self.max_display_width = int(cfg.get("max_display_width") or 0) or None
        self.max_display_height = int(cfg.get("max_display_height") or 0) or None
        self.perf.enabled = bool(cfg.get("perf_enabled", False))
        self.perf.log_every_s = float(cfg.get("perf_log_every_s", 2.0))
        self.perf.top_n = int(cfg.get("perf_log_top_n", 6))

        new_scale = float(cfg.get("diagram_scale", 1.0))
        new_dot = float(cfg.get("dot_size", 10))
        self.diagram_scale = new_scale
        self.dot_size = new_dot

        base_w, base_h = 450, 696
        w, h = int(base_w * new_scale), int(base_h * new_scale)
        try:
            self.diagram_canvas.config(width=w, height=h)
            self.diagram_canvas.delete("all")
            self.on_radio_click()
        except Exception:
            pass

        # Flush buffer so new resolution takes effect immediately.
        if hasattr(self, "img_buffer"):
            self.img_buffer.clear()
        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._timeline_playhead_id = None
        self._timeline2_playhead_id = None
        if getattr(self, "video", None):
            self.display_first_frame()

    # --- Frame stepping ---
    def next_frame(self, number_of_frames, play=False):
        if self.video is None:
            print("ERROR: Video = None"); return
        if number_of_frames > 0:
            self.video.current_frame = min(self.video.total_frames, self.video.current_frame + number_of_frames)
        elif number_of_frames < 0:
            self.video.current_frame = max(0, self.video.current_frame + number_of_frames)
        else:
            print("ERROR: Wrong number of frames."); return

        self.display_first_frame()
        if not play: self.draw_timeline()
        self.draw_timeline2()
