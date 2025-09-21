import os
import sys
import csv
import json
import time
import shutil
import cv2
import tkinter as tk
from tkinter import messagebox, filedialog
from threading import Thread
from PIL import Image, ImageTk
import pandas as pd
import keyboard

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
from config_utils import load_config_flags, load_parameter_names_into
from data_utils import (
    csv_to_dict, save_dataset, save_parameter_to_csv, load_parameter_from_csv,
    save_limb_parameters, load_limb_parameters, merge_and_flip_export, extract_zones_from_file,
    FrameRecord
)

class LabelingApp(tk.Tk):
    def __init__(self):
        super().__init__()

        # Core state (was previously in __init__)
        self.video = None
        self.video_name = None
        self.diagram_size = None
        self.minimal_touch_lenght = None
        self.NEW_TEMPLATE = False

        # Build UI (creates frames, widgets, binds events; sets many attributes)
        build_ui(self)

        # Load config flags that affect UI sizing & behavior
        self.NEW_TEMPLATE, self.diagram_size, self.minimal_touch_lenght = load_config_flags()
        print("INFO: Loaded new template:", self.NEW_TEMPLATE)
        print("INFO: Loaded diagram size:", self.diagram_size)

        # Timeline and buffering helpers
        self.background_thread = Thread(target=self.background_update, daemon=True)
        self.background_thread_play = Thread(target=self.background_update_play, daemon=True)

        # Diagram init
        self.init_diagram()

    # -------------------------
    # Callbacks & logic below are the same as before (moved unchanged)
    # -------------------------

    # --- Small helpers / global clicks ---
    def global_click(self, event):
        if getattr(self, "note_entry", None) and self.focus_get() == self.note_entry and event.widget != self.note_entry:
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
        if event is None or isinstance(event, tk.Event):
            x_pos, y_pos = self.last_mouse_x, self.last_mouse_y
        else:
            x_pos, y_pos = event.x, event.y

        limb_key = f'data{self.option_var_1.get()}'
        limb_data = getattr(self.video, limb_key, {})
        scale = 1 if self.diagram_size == "large" else 0.5
        current_frame = self.video.current_frame

        if current_frame in limb_data:
            rec: FrameRecord = limb_data[current_frame]
            xs = rec.get('X', []); ys = rec.get('Y', []); zones = rec.get('Zones', [])
            closest_distance = float('inf'); closest_index = None
            for index, (x, y) in enumerate(zip(xs, ys)):
                d = ((x*scale - x_pos)**2 + (y*scale - y_pos)**2)**0.5
                if d <= 20 and d < closest_distance:
                    closest_distance = d; closest_index = index
            if closest_index is not None:
                del xs[closest_index]; del ys[closest_index]
                if closest_index < len(zones): del zones[closest_index]
                if not xs:  # no points left -> drop record
                    del limb_data[current_frame]
                else:
                    rec['changed'] = True

    # --- Diagram init & routine render ---
    def init_diagram(self):
        # set up periodic dots refresh
        self.periodic_print_dot()

    def periodic_print_dot(self):
        self.diagram_canvas.delete("all")
        self.on_radio_click()  # keeps same behavior for image & palette
        dot_size = 5
        scale = 1 if self.diagram_size == "large" else 0.5
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
            onset = frame_data.get('Onset', "Off") if frame_data else "Off"
            for x, y in zip(xs, ys):
                color = 'green' if onset == "On" else 'red'
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
        onset = "On" if right else "Off"
        x_pos, y_pos = event.x, event.y
        scale = 1 if self.diagram_size == "large" else 2
        x_pos *= scale; y_pos *= scale
        zone_results = self.find_image_with_white_pixel(x_pos, y_pos)
        current_frame = self.video.current_frame
        option = self.option_var_1.get()

        target_attr = f"data{option}" if hasattr(self.video, f"data{option}") else "data"
        target_data = getattr(self.video, target_attr, {})
        setattr(self.video, f"is_touch{option}", True)

        if current_frame not in target_data:
            rec: FrameRecord = {
                "X": [int(x_pos)],
                "Y": [int(y_pos)],
                "Onset": onset,
                "Bodypart": option,
                "Look": "No",
                "Zones": list(zone_results),
                "Touch": None,
                "changed": True,
            }
            target_data[current_frame] = rec
        else:
            rec: FrameRecord = target_data[current_frame]
            rec.setdefault('X', []).append(int(x_pos))
            rec.setdefault('Y', []).append(int(y_pos))
            if not isinstance(rec.get('Zones', []), list):
                rec['Zones'] = list(zone_results)
            else:
                rec['Zones'].extend(zone_results)
            rec['Bodypart'] = option
            rec['Onset'] = onset
            rec['Look'] = "No"
            rec['changed'] = True

    def find_last_green(self, data):
        keys = sorted(data.keys(), reverse=True)
        start = self.video.current_frame
        for key in keys:
            if key <= start:
                if data[key].get('Onset') == 'Off':
                    self.video.last_green = [(None, None)]
                    return
                elif data[key].get('Onset') == 'On':
                    xs = data[key].get('X', []); ys = data[key].get('Y', [])
                    self.video.last_green = list(zip(xs, ys)) if xs and ys else [(None, None)]
                    return
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
        scale = 1 if self.diagram_size == "large" else 0.5
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(img)
        self.diagram_canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.draw_timeline()
        self.draw_timeline2()
        self.update_limb_parameter_buttons()

    # --- Diagram helpers used outside ---
    def find_image_with_white_pixel(self, x, y):
        import cv2, os
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
        current_limb = self.option_var_1.get()
        result = None
        for d in [self.video.parameter_button1_state_dict,
                  self.video.parameter_button2_state_dict,
                  self.video.parameter_button3_state_dict]:
            if frame in d:
                state = d[frame]
                if state == "ON": return "green"
                elif state == "OFF": result = "red"
        for i in range(1, 4):
            limb_param = getattr(self.video, f"limb_parameter{i}")
            if (current_limb, frame) in limb_param:
                state = limb_param.get((current_limb, frame))
                if state == "ON": return "green"
                elif state == "OFF": result = "red"
        return result

    def draw_timeline(self):
        self.timeline_canvas.delete("all")
        if not (self.video and self.video.total_frames > 0):
            return
        canvas_width = self.timeline_canvas.winfo_width()
        sector_width = canvas_width / self.video.number_frames_in_zone
        offset = self.video.number_frames_in_zone * self.video.current_frame_zone
        data_source = {
            'RH': self.video.dataRH, 'LH': self.video.dataLH, 'RL': self.video.dataRL, 'LL': self.video.dataLL
        }
        data = data_source.get(self.option_var_1.get(), self.video.data)
        top = 0; bottom = 100
        self.is_touch_timeline = False if self.video.current_frame_zone == 0 else self.video.touch_to_next_zone[self.video.current_frame_zone]

        def get_color(frame_idx, data):
            if frame_idx > self.video.total_frames: return 'black'
            details = data.get(frame_idx, {})
            xs = details.get('X', [])
            if not xs:
                return self.color_during if self.is_touch_timeline else 'lightgrey'
            if len(xs) >= 1 and xs[0] is not None:
                if details.get('Onset') == 'On':
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
            if frame_offset == self.video.current_frame:
                self.timeline_canvas.create_rectangle(left, top, right, bottom, fill='dodgerblue', outline='black')
            if frame == self.video.number_frames_in_zone - 1:
                if self.video.current_frame_zone + 1 < len(self.video.touch_to_next_zone):
                    self.video.touch_to_next_zone[self.video.current_frame_zone + 1] = (color == self.color_during)
                elif self.video.current_frame_zone + 1 == len(self.video.touch_to_next_zone):
                    self.video.touch_to_next_zone.append(color == self.color_during)

    def draw_timeline2(self):
        self.timeline2_canvas.delete("all")
        if self.video and self.video.total_frames > 0:
            canvas_width = self.timeline2_canvas.winfo_width()
            canvas_height = self.timeline2_canvas.winfo_height()
            selected_limb_key = f'data{self.option_var_1.get()}'
            limb_data = getattr(self.video, selected_limb_key, {})
            for frame, details in limb_data.items():
                if 'Onset' in details and details['Onset'] == 'On':
                    touch_pos = (frame / self.video.total_frames) * canvas_width
                    self.timeline2_canvas.create_line(touch_pos, 0, touch_pos, canvas_height, fill='green', width=1)
            current_pos = (self.video.current_frame / self.video.total_frames) * canvas_width
            margin = 2
            current_pos = min(max(current_pos, margin), canvas_width - margin)
            self.timeline2_canvas.create_line(current_pos, 0, current_pos, canvas_height, fill='dodgerblue', width=2)

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
        if self.video is not None:
            while True:
                if self.play:
                    self.next_frame(1, play=True)
                    if self.video.current_frame % 10 == 0:
                        self.after(0, self.draw_timeline)
                    self.after(0, self.display_first_frame)
                time.sleep(0.03)

    def load_frame(self, frame_number):
        from PIL import Image
        import os
        try:
            frame_path = os.path.join(self.video.frames_dir, f"frame{frame_number}.jpg")
            img = Image.open(frame_path)
            img = self.resize_frame(img)
            photo_img = ImageTk.PhotoImage(img)
            self.img_buffer[frame_number] = photo_img
        except Exception as e:
            print(f"ERROR: Opening or processing frame {frame_number}: {str(e)}")

    def resize_frame(self, img):
        display_width = self.video_frame.winfo_width()
        display_height = self.video_frame.winfo_height()
        original_width, original_height = img.size
        aspect_ratio = original_width / original_height
        if display_width / display_height > aspect_ratio:
            new_width = int(display_height * aspect_ratio); new_height = display_height
        else:
            new_width = display_width; new_height = int(display_width / aspect_ratio)
        self.old_width = new_width; self.old_height = new_height
        return img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    def display_first_frame(self, frame_number=None):
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
        parameter_dicts = {
            1: self.video.parameter_button1_state_dict,
            2: self.video.parameter_button2_state_dict,
            3: self.video.parameter_button3_state_dict,
        }
        parameter_buttons = {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn}
        key = self.video.current_frame
        for param_num, param_dict in parameter_dicts.items():
            current_state = param_dict.get(key, None)
            if current_state is None: parameter_buttons[param_num].config(bg='lightgrey')
            elif current_state == "ON": parameter_buttons[param_num].config(bg='lightgreen')
            elif current_state == "OFF": parameter_buttons[param_num].config(bg='#E57373')  # noqa: F821
            else: parameter_buttons[param_num].config(bg='lightgrey')

    def parameter_dic_insert(self, parameter):
        parameter_dicts = {
            1: self.video.parameter_button1_state_dict,
            2: self.video.parameter_button2_state_dict,
            3: self.video.parameter_button3_state_dict,
        }
        parameter_buttons = {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn}
        key = self.video.current_frame
        current_dict = parameter_dicts.get(parameter)
        current_state = current_dict.get(key, None)
        if current_state is None:
            new_state = "ON"; parameter_buttons[parameter].config(bg='lightgreen')
        elif current_state == "ON":
            new_state = "OFF"; parameter_buttons[parameter].config(bg='#E57373')
        else:
            new_state = None; parameter_buttons[parameter].config(bg='lightgrey')
        current_dict[key] = new_state
        print("Dictionary", parameter, current_dict)

    def toggle_limb_parameter(self, param_number):
        limb = self.option_var_1.get()
        param_dicts = {1: self.video.limb_parameter1, 2: self.video.limb_parameter2, 3: self.video.limb_parameter3}
        param_buttons = {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn}
        param_dict = param_dicts[param_number]
        frame = self.video.current_frame
        current_state = param_dict.get((limb, frame), None)
        if current_state is None:
            new_state = "ON"; param_buttons[param_number].config(bg='lightgreen')
        elif current_state == "ON":
            new_state = "OFF"; param_buttons[param_number].config(bg='#E57373')
        else:
            new_state = None; param_buttons[param_number].config(bg='lightgray')
        param_dict[(limb, frame)] = new_state
        print(f"INFO: {limb} - Parameter {param_number} at Frame {frame} set to {new_state}")

    def update_limb_parameter_buttons(self):
        limb = self.option_var_1.get()
        if not self.video: return
        frame = self.video.current_frame
        param_dicts = {1: self.video.limb_parameter1, 2: self.video.limb_parameter2, 3: self.video.limb_parameter3}
        param_buttons = {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn}
        for param_num in range(1, 4):
            current_state = param_dicts[param_num].get((limb, frame), None)
            if current_state == "ON":
                param_buttons[param_num].config(bg='lightgreen')
            elif current_state == "OFF":
                param_buttons[param_num].config(bg='#E57373')
            else:
                param_buttons[param_num].config(bg='lightgray')

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
        current_frame = self.video.current_frame
        note_text = self.note_entry.get()
        if note_text.strip():
            self.video.notes[current_frame] = note_text
        elif current_frame in self.video.notes:
            del self.video.notes[current_frame]
        notes_path = self.video.dataNotes_path_to_csv
        if not notes_path:
            print("ERROR: Notes CSV path is not set."); return
        with open(notes_path, mode='w', newline='') as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(['Frame', 'Note'])
            for frame, note in sorted(self.video.notes.items()):
                writer.writerow([frame, note])
        print(f"INFO: Note saved for frame {current_frame}: {note_text}")
        keyboard.press_and_release('tab')

    def update_note_entry(self):
        current_frame = self.video.current_frame
        note_text = self.video.notes.get(current_frame, "")
        self.note_entry.delete(0, tk.END)
        self.note_entry.insert(0, note_text)

    # --- Save / Export ---
    def save_data(self):
        print("INFO: Saving...")
        save_dataset(self.video.dataRH_path_to_csv, self.video.total_frames, self.video.dataRH)
        save_dataset(self.video.dataLH_path_to_csv, self.video.total_frames, self.video.dataLH)
        save_dataset(self.video.dataRL_path_to_csv, self.video.total_frames, self.video.dataRL)
        save_dataset(self.video.dataLL_path_to_csv, self.video.total_frames, self.video.dataLL, with_touch=True)

        save_parameter_to_csv(self.video.dataparameter_1_path_to_csv, self.video.parameter_button1_state_dict)
        save_parameter_to_csv(self.video.dataparameter_2_path_to_csv, self.video.parameter_button2_state_dict)
        save_parameter_to_csv(self.video.dataparameter_3_path_to_csv, self.video.parameter_button3_state_dict)

        data_folder = os.path.dirname(self.video.dataRH_path_to_csv)
        limb_params_path = os.path.join(data_folder, f"{self.video_name}_limb_parameters.csv")
        save_limb_parameters(limb_params_path, {
            "Parameter_1": self.video.limb_parameter1,
            "Parameter_2": self.video.limb_parameter2,
            "Parameter_3": self.video.limb_parameter3
        })

        # Export (merged + flipped)
        base_dir = os.path.dirname(os.path.dirname(self.video.dataRH_path_to_csv))
        export_folder = os.path.join(base_dir, "export")
        clothes_list = extract_zones_from_file(self.video.clothes_file_path or self.video.dataNotes_path_to_csv.replace('_notes.csv', '_clothes.txt'))
        merge_and_flip_export(
            self.video.dataLH_path_to_csv, self.video.dataLL_path_to_csv,
            self.video.dataRH_path_to_csv, self.video.dataRL_path_to_csv,
            [self.video.dataparameter_1_path_to_csv, self.video.dataparameter_2_path_to_csv, self.video.dataparameter_3_path_to_csv],
            self.video.dataNotes_path_to_csv,
            limb_params_path,
            self.video_name, self.frame_rate, self.video.program_version, self.labeling_mode,
            clothes_list, export_folder
        )
        print("INFO: Saved & exported.")

    # --- Analysis / Sort / Playback buttons ---
    def analysis(self):
        if self.video:
            self.save_data()
            directory_path = self.video.dataRH_path_to_csv[:self.video.dataRH_path_to_csv.rfind("data\\") + len("data\\")]
            index = directory_path.rfind("data")
            plots_path = directory_path[:index] + "plots" + directory_path[index + len("data"):] if index != -1 else directory_path
            analysis.do_analysis(directory_path, plots_path, self.video_name, debug=True, frame_rate=self.frame_rate)

    def play_video(self):
        if not self.play:
            self.play = True
            if not self.play_thread_on:
                self.play_thread_on = True
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
        mode_window.geometry("300x150")
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

        self.ask_labeling_mode()
        if not hasattr(self, 'labeling_mode'):
            print("INFO: No mode selected, cancelling video load."); return

        video_path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=(("Video files", "*.mp4;*.mov;*.avi;*.mkv;*.flv;*.wmv"), ("All files", "*.*"))
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

        # Prepare limb CSVs
        for suffix in ['RH', 'LH', 'RL', 'LL']:
            csv_path = os.path.join(data_dir, f"{video_name}{suffix}.csv")
            setattr(self.video, f"data{suffix}_path_to_csv", csv_path)
            if os.path.exists(csv_path):
                print(f"INFO: CSV {suffix} already exists; loading.")
                setattr(self.video, f"data{suffix}", csv_to_dict(csv_path))
            else:
                with open(csv_path, 'w', newline='') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(['Frame', 'X', 'Y', 'Onset', 'Bodypart', 'Look', 'Zones', 'Touch'])

        # Parameter CSVs
        for name in ['parameter_1', 'parameter_2', 'parameter_3']:
            csv_path = os.path.join(data_dir, f"{video_name}{name}.csv")
            setattr(self.video, f"data{name}_path_to_csv", csv_path)

        # Load parameter states
        self.video.parameter_button1_state_dict = load_parameter_from_csv(self.video.dataparameter_1_path_to_csv)
        self.video.parameter_button2_state_dict = load_parameter_from_csv(self.video.dataparameter_2_path_to_csv)
        self.video.parameter_button3_state_dict = load_parameter_from_csv(self.video.dataparameter_3_path_to_csv)

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

        # Initial draw
        self.display_first_frame()
        self.draw_timeline()
        self.draw_timeline2()
        self.name_label.config(text=f"Video Name: {video_name}")

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

    # --- Closing ---
    def on_close(self):
        if messagebox.askokcancel("Close Aplication", "Do you want to close the aplication?"):
            if self.video:
                print("INFO: Closing...")
                self.save_data()
                print("INFO: Get some rest, you deserve it!")
                print("INFO: See you soon!")
            self.destroy()

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
