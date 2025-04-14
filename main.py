import cv2
import os
import csv
import sys
import json
import time
import shutil 
import pandas as pd
import tkinter as tk
from tkinter import messagebox
from tkinter import filedialog
from threading import Thread
from PIL import Image, ImageTk
import keyboard
import analysis

with open('config.json', 'r') as file:
            config = json.load(file)
            NEW_TEMPLATE = config.get('new_template', False)  # Default to 'medium' if not specified
            print("INFO: Loaded new template:", NEW_TEMPLATE)

#new tasks---------------------------------------------------------------------------------------
#TODOO
# Change looking to Limb Specific Parameter

#code review (after Json agrees)


#Minor things---------------------
# change colot of limb parameter buttons when not selected
#in export zone [] and """" problem
#bigger entry for notes?
#typo in loading parameter names
#loading button fix
#update readme with looking btn change
#delete looking from export
#guide na zacatku
#deleting confidential data
#indication of saving
#frame loading indication

#Bugs
#when 

#DONE---------------------------

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
        self.img = Image.open("icons/diagram.png")  # Upravte cestu k obrázku
        self.img = self.img.resize((int(self.img.width*0.5), int(self.img.height*0.5)), Image.LANCZOS)  # Resize the image to double the original dimensions
        self.photo2 = ImageTk.PhotoImage(self.img)
        self.canvas2 = tk.Canvas(self.f, width=self.img.width, height=self.img.height, bg='red')  # Adjust canvas size
        
        self.canvas2.pack()
        self.canvas2.create_image(0, 0, anchor="nw", image=self.photo2)
        self.canvas2.bind("<Button-1>", self.add_dot)  # Pravé tlačítko pro přidání tečky
        self.canvas2.bind("<Button-2>", self.remove_dot)  # Prostřední tlačítko pro odstranění tečky
        self.top_level.protocol("WM_DELETE_WINDOW", self.on_close)
        
    def on_close(self):
        # Zavolání callback funkce s daty teček při uzavření okna
        self.on_close_callback(self.dots)
        self.top_level.destroy()  # Uzavření okna    
        
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

class Video:
    
    def __init__(self, video_path):
        self.video_path = video_path
        self.current_frame = 0  # Starting at frame 0
        self.current_frame_zone = 0
        self.number_frames_in_zone = 100
        self.video_name = None
        self.total_frames = self.get_total_frames()
        self.number_zones = int(self.total_frames/self.number_frames_in_zone) + 1
        self.frames_dir = None
        self.data = {}
        self.data_path_to_csv = None
        self.dots = []
        self.dataRH = {}
        self.dataRH_path_to_csv = None
        self.dataLH = {}
        self.dataLH_path_to_csv = None
        self.dataRL = {}
        self.dataRL_path_to_csv = None
        self.dataLL = {}
        self.dataLL_path_to_csv = None
        
        self.is_touchRH = False
        self.is_touchLH = False
        self.is_touchRL = False
        self.is_touchLL = False
        self.touch_to_next_zone = [False for _ in range(self.number_zones)]
        self.last_green = [(10, 10),(5, 5),(50, 50)]
        self.play = False
        self.frame_rate = None
        self.parameter_button1_state_dict = {}
        self.parameter_button2_state_dict = {}
        self.parameter_button3_state_dict = {}
        self.dataNotes_path_to_csv = None
        self.program_version = 5.5
        print("INFO: Program version:", self.program_version)
        self.parameter1_name = None
        self.parameter2_name = None
        self.parameter3_name = None
        self.clothes_file_path = None
        self.notes = {}
        self.limb_parameter1 = {}  # RH, LH, RL, LL each has its own entry
        self.limb_parameter2 = {}
        self.limb_parameter3 = {}
    
    def get_total_frames(self):
        cap = cv2.VideoCapture(self.video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        #print("total_frame -1:",total_frames -1)
        return total_frames -1
    
    def get_frame(self, frame_number):
        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        success, frame = cap.read()
        cap.release()
        if success:
            return frame
        else:
            return None


class LabelingApp(tk.Tk):
    
    def __init__(self):
        super().__init__()
        self.title('Labeling Application')
        self.geometry('1200x1000')
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.video = None
        self.photo = None
        self.pil_frame = None
        self.current_dot_item_id = [None]
        self.data_clothes = {}
        self.touch = False
        self.is_touch_timeline = False
        self.color_start = "blue"
        self.color_during = "yellow"
        self.color_end = "red"
        self.frame_cache = {}
        self.image = None
        self.diagram_size, self.minimal_touch_lenght = self.load_config()
        
        self.img_buffer = {}
        self.play = False
        self.play_thread_on = False
        # Create frames for video, timeline, and diagram
        self.old_width = None
        self.old_height = None
        self.video_frame = tk.Frame(self, bg='gray')
        self.timeline_frame = tk.Frame(self, bg='grey',height=50)
        self.notes_file_path = None
        self.progress = {}
        self.frame_rate = None
        #self.looking_dic = {}
        #self.cat3_mp4looking= None
        
        self.timeline2_canvas = tk.Canvas(self.timeline_frame, bg='lightgrey', height=30)
        self.timeline2_canvas.pack(fill=tk.X, expand=True, pady=(0, 5))  # Add padding below the first timeline
        self.timeline2_canvas.bind("<Button-1>", self.on_timeline2_click)  # Bind click event

        self.timeline_canvas = tk.Canvas(self.timeline_frame, bg='grey', height=50)
        self.timeline_canvas.pack(fill=tk.X, expand=True, pady=(10, 0))  # Add padding above the second timeline
        self.timeline_canvas.bind("<Button-1>", self.on_timeline_click)  # Bind click event
        self.bind_all("<Button-1>", self.global_click, add="+")
        
        
        
        self.control_frame = tk.Frame(self, bg='lightgrey',height=100)
        #self.control_frame_2 = tk.Frame(self, bg='red',height=100)
        
        self.diagram_frame = tk.Frame(self, bg='lightgrey')
        
        
          # Span across both columns
        self.video_frame.grid(row=1, column=0, sticky="nsew")
        self.timeline_frame.grid(row=2, column=0, sticky="ew")
        self.control_frame.grid(row=0, column=0, columnspan=1, sticky="ew")
        #self.control_frame_2.grid(row=0, column=0, columnspan=1, sticky="ew")
        self.diagram_frame.grid(row=0, column=1, rowspan=3, sticky="ns")
        
        # Configure the main window to make the video and timeline frames resizable
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)  # Video frame
        self.rowconfigure(2, weight=0)  # Timeline frame, adjust weight as needed
        #self.timeline_canvas.bind("<Configure>", lambda event: self.draw_timeline())
        #self.timeline2_canvas.bind("<Configure>", lambda event: self.draw_timeline2())
        # Initialize GUI components for each section
        
        self.init_controls()
        
        self.init_video()
        self.init_timeline()
        image_path = "icons/diagram0.png"
        img = Image.open(image_path)
        self.photo = ImageTk.PhotoImage(img)
        if self.diagram_size == "large":
            scale = 1
        else:
            scale = 0.5
        self.diagram_canvas = tk.Canvas(self.diagram_frame, bg='lightgrey', width=int(450*scale), height=int(696*scale))
        self.diagram_canvas.pack(padx=10, pady=10)
        self.diagram_canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.last_mouse_x = 0
        self.last_mouse_y = 0

        # Bind mouse motion inside the diagram
        self.diagram_canvas.bind("<Motion>", self.update_last_mouse_position)
        self.diagram_canvas.bind("<Button-3>",lambda event: self.on_diagram_click(event,right = False))
        self.diagram_canvas.bind("<Button-1>",lambda event: self.on_diagram_click(event,right = True))
        self.diagram_canvas.bind("<Button-2>", self.on_middle_click)
        self.bind("<KeyPress-d>", self.on_middle_click)  # Bind only within canvas
        self.bind("<Left>", self.navigate_left)
        self.bind("<Right>", self.navigate_right)
        self.bind("<Shift-Left>", lambda event: self.next_frame(-7))
        self.bind("<Shift-Right>", lambda event: self.next_frame(7))

        # Detect focus on note entry to disable arrow navigation
        
        self.bind("<Shift-Right>", lambda event: self.next_frame(7))
        self.bind("<Shift-Left>", lambda event: self.next_frame(-7))
        self.bind("<MouseWheel>", self.on_mouse_wheel)  # Windows
        self.video_frame.bind('<Configure>', self.on_resize)
        self.init_diagram()
        
        
        
        self.background_thread = Thread(target=self.background_update)
        self.background_thread.daemon = True
    
    def global_click(self, event):
        # If the current focus is on the note_entry and the click is outside of it
        if self.focus_get() == self.note_entry and event.widget != self.note_entry:
            # Transfer focus away (e.g., to the root window)
            self.focus_set()
    def navigate_left(self, event):
        #print(f"Current focus: {self.focus_get()}, Note Entry: {self.note_entry}")

        """ Moves to the previous frame only if note entry is not focused. """
        
        self.next_frame(-1)

    def navigate_right(self, event):
        #print(f"Current focus: {self.focus_get()}, Note Entry: {self.note_entry}")

        """ Moves to the next frame only if note entry is not focused. """
        
        self.next_frame(1)   
    
    def disable_arrow_keys(self, event=None):
        """ Unbinds arrow keys when note entry is focused. """
        self.unbind("<Left>")
        self.unbind("<Right>")
        self.unbind("<Shift-Left>")
        self.unbind("<Shift-Right>")

    def enable_arrow_keys(self, event=None):
        """ Rebinds arrow keys when note entry loses focus. """
        self.bind("<Left>", self.navigate_left)
        self.bind("<Right>", self.navigate_right)
        self.bind("<Shift-Left>", lambda event: self.next_frame(-7))
        self.bind("<Shift-Right>", lambda event: self.next_frame(7))    
        
        #self.periodic_call()
    
    def update_last_mouse_position(self, event):
        """ Stores the last mouse position for use when pressing 'D' """
        self.last_mouse_x = event.x
        self.last_mouse_y = event.y
    
    def on_resize(self, event):
        print("INFO: Resized to {}x{}".format(event.width, event.height))
        self.img_buffer.clear()  # Clear buffer to remove old images
        if self.video:
            self.display_first_frame()  # Reload the first frame after clearing buffer
    
    def load_frame(self, frame_number):
        try:
            frame_path = os.path.join(self.video.frames_dir, f"frame{frame_number}.jpg")
            img = Image.open(frame_path)
            img = self.resize_frame(img)
            photo_img = ImageTk.PhotoImage(img)
            self.img_buffer[frame_number] = photo_img
        except Exception as e:
            print(f"ERROR: Opening or processing frame {frame_number}: {str(e)}")
    
    def background_update(self, frame_number=None):
        was_missing = False  # Track if the current frame was previously missing

        while True:
            time.sleep(0.01)

            if self.video is not None:
                frame_number = self.video.current_frame

                if frame_number < 0 or frame_number > self.video.total_frames:
                    return

                start_frame = max(0, frame_number - 30)
                end_frame = min(self.video.total_frames, frame_number + 50)

                buffer_loaded = True  # Flag to track overall buffer status
                current_frame_loaded = frame_number in self.img_buffer  # Is current frame in buffer?

                # Check if we were missing this frame previously
                if not current_frame_loaded:
                    buffer_loaded = False
                    self.load_frame(frame_number)
                    was_missing = True  # Mark that the frame was missing before

                # Preload future frames
                for i in range(frame_number, end_frame + 1):
                    if i not in self.img_buffer:
                        buffer_loaded = False
                        self.load_frame(i)

                # Preload past frames
                for i in range(frame_number, start_frame - 1, -1):
                    if i not in self.img_buffer:
                        buffer_loaded = False
                        self.load_frame(i)

                # Remove frames outside the buffer range
                buffer_range = 200
                min_keep = max(0, frame_number - buffer_range)
                max_keep = min(self.video.total_frames, frame_number + buffer_range)

                frames_to_remove = [k for k in self.img_buffer if k < min_keep or k > max_keep]

                for k in frames_to_remove:
                    del self.img_buffer[k]

                # **Update buffer label:**
                if current_frame_loaded:
                    self.loading_label.config(text="Buffer Loaded", bg='lightgreen')
                else:
                    self.loading_label.config(text="Buffer Loading", bg='#E57373')  # **Only red if current frame is missing**

                # *** Force display update only if the frame was missing before and is now loaded ***
                if was_missing and current_frame_loaded:
                    self.after(0, self.display_first_frame)
                
                # Reset the flag if the frame is loaded
                was_missing = not current_frame_loaded

    def load_parameter_name(self):
        with open('config.json', 'r') as file:
            config = json.load(file)
            parameter1 = config.get('parameter1', 'Parameter 1')  # Default to 'medium' if not specified
            parameter2 = config.get('parameter2', 'Parameter 2')  # Default to 'medium' if not specified
            parameter3 = config.get('parameter3', 'Parameter 3')  # Default to 'medium' if not specified
            self.video.parameter1_name = parameter1
            self.video.parameter2_name = parameter2
            self.video.parameter3_name = parameter3
            

            self.par1_btn.config(text=f"{parameter1}",bg='lightgrey')
            self.par2_btn.config(text=f"{parameter2}",bg='lightgrey')
            self.par3_btn.config(text=f"{parameter3}",bg='lightgrey')

                # Load limb-specific parameters
            self.video.limb_parameter1_name = config.get('limb_parameter1', 'Limb Parameter 1')
            self.video.limb_parameter2_name = config.get('limb_parameter2', 'Limb Parameter 2')
            self.video.limb_parameter3_name = config.get('limb_parameter3', 'Limb Parameter 3')

            
            self.limb_par1_btn.config(text=f"{self.video.limb_parameter1_name}",bg='lightgrey')
            self.limb_par2_btn.config(text=f"{self.video.limb_parameter2_name}",bg='lightgrey')
            self.limb_par3_btn.config(text=f"{self.video.limb_parameter3_name}",bg='lightgrey')

    def load_config(self):
        with open('config.json', 'r') as file:
            config = json.load(file)
            diagram_size = config.get('diagram_size', 'small')  # Default to 'medium' if not specified
            minimal_touch_lenght = config.get('minimal_touch_lenght', '280')
            
            print("INFO: Loaded diagram size:", diagram_size)
            return diagram_size, minimal_touch_lenght
    
    def on_mouse_wheel(self, event):
            if event.delta > 0 or event.num == 4:  # Scrolling up
                self.next_frame(-1)
            elif event.delta < 0 or event.num == 5:  # Scrolling down
                self.next_frame(1)
        
    def on_middle_click(self, event=None):
        """ Deletes the closest dot using last mouse position if triggered by 'D' """
        
        # Use stored position if triggered by 'D' (event is None)
        if event is None or isinstance(event, tk.Event):  
            x_pos, y_pos = self.last_mouse_x, self.last_mouse_y  
        else:  
            x_pos, y_pos = event.x, event.y  

        # Get the currently selected limb data dictionary
        limb_key = f'data{self.option_var_1.get()}'
        limb_data = getattr(self.video, limb_key, {})

        scale = 1 if self.diagram_size == "large" else 0.5

        # Current frame to check
        current_frame = self.video.current_frame

        if current_frame in limb_data:
            details = limb_data[current_frame]
            closest_distance = float('inf')
            closest_index = None

            # Find closest point
            for index, (x, y) in enumerate(details['xy']):
                distance = ((x*scale - x_pos)**2 + (y*scale - y_pos)**2)**0.5
                if distance <= 20 and distance < closest_distance:
                    closest_distance = distance
                    closest_index = index

            if closest_index is not None:
                del details['xy'][closest_index]  # Remove the coordinate
                print(f"DEBUG: Deleting zone: {details['Zone'][closest_index]}")
                del details['Zone'][closest_index]
                
                if not details['xy']:  
                    del limb_data[current_frame]  # Remove frame entry if empty

                print(f"INFO: Deleted point {closest_index} at ({x_pos}, {y_pos}) in frame {current_frame}")

    def on_diagram_click(self, event,right):
        if right:
            onset = "On"
        else:
            onset = "Off"
            #self.video.last_green = [None, None]
        x_pos, y_pos = event.x, event.y
        scale = 1 if self.diagram_size == "large" else 2
        x_pos *= scale
        y_pos *= scale
        zone_results = self.find_image_with_white_pixel(x_pos, y_pos)
        print("INFO: Zone:",zone_results)
        #zones = ', '.join([result[0] for result in zone_results if result])
        print(f"INFO: Click on diagram at: x={x_pos}, y={y_pos}")
        current_frame = self.video.current_frame
        option = self.option_var_1.get()
        target_attr = f"data{option}" if hasattr(self.video, f"data{option}") else "data"
        target_data = getattr(self.video, target_attr, {})
        
        print(f"INFO: Writing to {option} ...")
        setattr(self.video, f"is_touch{option}", True)
        
        if current_frame not in target_data:
            # If the frame doesn't exist in target_data, initialize it
            target_data[current_frame] = {
                'xy': [(x_pos, y_pos)],
                'Onset': onset,
                'Bodypart': option,
                'Look': "No",
                'Zone': list(zone_results)  # Ensure this is always a list
            }
            print(f"INFO: Created new entry for frame {current_frame} with Zone: {zone_results}")
        else:
            # If frame exists, ensure 'xy' is a list before appending
            if 'xy' not in target_data[current_frame]:
                target_data[current_frame]['xy'] = []
            target_data[current_frame]['xy'].append((x_pos, y_pos))

            # Ensure 'Zone' is a list before appending
            if 'Zone' not in target_data[current_frame] or not isinstance(target_data[current_frame]['Zone'], list):
                target_data[current_frame]['Zone'] = list(zone_results)
            else:
                target_data[current_frame]['Zone'].extend(zone_results)  # Append all detected zones

            print(f"INFO: Updated frame {current_frame}: (x, y)={x_pos, y_pos}, Zone={target_data[current_frame]['Zone']}")
            target_data[current_frame]['Bodypart'] = option
            target_data[current_frame]['Onset'] = onset
            target_data[current_frame]['Look'] = "No"

        print("DEBUG:", target_data[current_frame])
    
    def find_last_green(self,data):
        #print("data to find last green dot:",data)
        keys = sorted(data.keys(), reverse=True)  # Sort keys in descending order
        start = self.video.current_frame
        for key in keys:
            if key <= start:  # Only consider keys <= start
                if data[key]['Onset'] == 'Off':
                    #print('last onset is Off')
                    self.video.last_green = [(None, None)]
                    return None
                elif data[key]['Onset'] == 'On':
                    #print('last onset is On')
                    xy = data[key]['xy'][-1]
                    #print("INFO: xy last data: ",data[key]['xy'])
                    x = xy[0]
                    y = xy[1]
                    self.video.last_green = data[key]['xy']
                    return None
        self.video.last_green = [(None, None)]
        return None  # Return None if no match is found
    
    def on_radio_click(self):
        #print("changing higlight")
        
        
        touch = False

        if self.option_var_1.get() == "RH":
            if NEW_TEMPLATE:
                image_path = "icons/RH_new_template.png"
            else:
                image_path = "icons/RH.png"
            if self.video:
                touch = self.video.is_touchRH
        elif self.option_var_1.get() == "LH":
            if self.video:
                touch = self.video.is_touchLH
            if NEW_TEMPLATE:
                image_path = "icons/LH_new_template.png"
            else:    
                image_path = "icons/LH.png"
            
        elif self.option_var_1.get() == "RL":
            if self.video:
                touch = self.video.is_touchRL
            if NEW_TEMPLATE:
                image_path = "icons/RL_new_template.png"
            else:
                image_path = "icons/RL.png"
        elif self.option_var_1.get() == "LL":
            if self.video:
                touch = self.video.is_touchLL
            if NEW_TEMPLATE:
                image_path = "icons/LL_new_template.png"
            else:
                image_path = "icons/LL.png"
        #self.bool_var = touch
        #self.display_text_var.set("Touch" if self.bool_var else "No Touch")
        img = Image.open(image_path)
        if self.diagram_size == "large":
            scale = 1
        else:
            scale = 0.5
        img = img.resize((int(img.width * scale),int(img.height * scale)), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(img)
        self.diagram_canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.draw_timeline()
        self.draw_timeline2()
        self.update_limb_parameter_buttons()

    def periodic_print_dot(self):
        # Nejprve odstranit všechny předchozí body z plátna
        self.diagram_canvas.delete("all")  # Odstraní vše z plátna, můžete chtít odstranit jen specifické body
        self.on_radio_click()
        #self.color_looking()
        dot_size = 5
        #self.draw_eyes()
        if self.diagram_size == "large":
            scale = 1
        else:
            scale = 0.5
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
            frame_data = data.get(self.video.current_frame, {})
            if 'xy' in frame_data:
                for x, y in frame_data['xy']:
                    
                    onset = frame_data.get('Onset', "Off")
                    if onset == "On":
                        color = 'green'
                    else:
                        color = 'red'
                    # Vytvoření bodu pro každou dvojici souřadnic
                    self.diagram_canvas.create_oval(x*scale - dot_size, y*scale - dot_size, x*scale + dot_size, y*scale + dot_size, fill=color)
                    #dot_size = 15
                    #self.diagram_canvas.create_oval(x - dot_size, y - dot_size, x + dot_size, y + dot_size, outline=color, fill='', width=5)
            #last dot
            #print("Last_green:",self.video.last_green)
            array_xy = self.video.last_green
            #print("INFO: Array_xy:",array_xy)
            for i in range(len(array_xy)):
                x_last, y_last = array_xy[i]
                if x_last != None:
                    self.diagram_canvas.create_oval(x_last*scale - dot_size, y_last*scale - dot_size, x_last*scale + dot_size, y_last*scale + dot_size, outline='green', fill='')

        # Periodicky volat tuto funkci
        self.after(300, self.periodic_print_dot)  
    
    def periodic_print_dot_thread(self):
        # Nejprve odstranit všechny předchozí body z plátna
        while True:
            self.diagram_canvas.delete("all")  # Odstraní vše z plátna, můžete chtít odstranit jen specifické body
            self.on_radio_click()
            #self.color_looking()
            dot_size = 5
            #self.draw_eyes()
            if self.diagram_size == "large":
                scale = 1
            else:
                scale = 0.5
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
                frame_data = data.get(self.video.current_frame, {})
                if 'xy' in frame_data:
                    for x, y in frame_data['xy']:
                        
                        onset = frame_data.get('Onset', "Off")
                        if onset == "On":
                            color = 'green'
                        else:
                            color = 'red'
                        # Vytvoření bodu pro každou dvojici souřadnic
                        self.diagram_canvas.create_oval(x*scale - dot_size, y*scale - dot_size, x*scale + dot_size, y*scale + dot_size, fill=color)
                        #dot_size = 15
                        #self.diagram_canvas.create_oval(x - dot_size, y - dot_size, x + dot_size, y + dot_size, outline=color, fill='', width=5)
                #last dot
                #print("Last_green:",self.video.last_green)
                x_last, y_last = self.video.last_green
                if x_last != None:
                    self.diagram_canvas.create_oval(x_last*scale - dot_size, y_last*scale - dot_size, x_last*scale + dot_size, y_last*scale + dot_size, outline='green', fill='')

            # Periodicky volat tuto funkci
            time.sleep(0.5)
    
    def find_image_with_white_pixel(self, x, y):
        # List to hold the names of the images where the pixel is white
        x = int(x)
        y = int(y)
        if NEW_TEMPLATE:
                directory = "icons/zones3_new_template"
        else:
            directory = "icons/zones3"
        images_with_white_pixel = []
        
        # Iterate over all files in the given directory
        for filename in os.listdir(directory):
            # Construct full file path
            file_path = os.path.join(directory, filename)
            # Ensure file is an image
            if os.path.isfile(file_path) and file_path.endswith(('.png', '.jpg', '.jpeg')):
                # Read image in grayscale
                image = cv2.imread(file_path, cv2.IMREAD_GRAYSCALE)
                # Check if the image was loaded correctly
                if image is None:
                    continue
                # Check if the pixel at (x, y) is white
                if image[y, x] == 0:
                    images_with_white_pixel.append(filename.rsplit('.', 1)[0])
                    #print("zone:", images_with_white_pixel)
                    return images_with_white_pixel
        return ['NN']    
    
    def on_timeline_click(self, event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline_canvas.winfo_width()
            frame_number = int(click_position / canvas_width * self.video.number_frames_in_zone)
            if self.video.total_frames >= frame_number+self.video.number_frames_in_zone*self.video.current_frame_zone:
                self.video.current_frame = frame_number+self.video.number_frames_in_zone*self.video.current_frame_zone
                self.display_first_frame()
            else:
                print("ERROR: Frame Number")
    
    def on_timeline2_click(self, event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline2_canvas.winfo_width()
            
            # Determine the exact clicked frame as a proportion of the total video length.
            new_frame = int((click_position / canvas_width) * self.video.total_frames)

            # Update current frame and its zone dynamically
            self.video.current_frame = new_frame
            self.video.current_frame_zone = new_frame // self.video.number_frames_in_zone

            print("INFO: Jumping to exact frame:", new_frame)
            self.display_first_frame()
    
    def draw_timeline2(self):
        self.timeline2_canvas.delete("all")  # Clear existing drawings
        if self.video and self.video.total_frames > 0:
            canvas_width = self.timeline2_canvas.winfo_width()
            canvas_height = self.timeline2_canvas.winfo_height()

            # Get the currently selected limb from the radio button
            selected_limb_key = f'data{self.option_var_1.get()}'
            limb_data = getattr(self.video, selected_limb_key, {})

            # Draw yellow lines for touch frames
            for frame, details in limb_data.items():
                if 'Onset' in details and details['Onset'] == 'On':  # Check if there was a touch
                    touch_pos = (frame / self.video.total_frames) * canvas_width
                    self.timeline2_canvas.create_line(touch_pos, 0, touch_pos, canvas_height, fill='green', width=1)

            # Draw red line for current frame position
            current_pos = (self.video.current_frame / self.video.total_frames) * canvas_width
            margin = 2
            if current_pos < margin:
                current_pos = margin
            elif current_pos > canvas_width - margin:
                current_pos = canvas_width - margin
            self.timeline2_canvas.create_line(current_pos, 0, current_pos, canvas_height, fill='dodgerblue', width=2)

    def check_zone_for_touch(self, zone_index):
        # Dynamically calculate the number of frames per zone if not predefined
        frames_per_zone = 100

        # Calculate the frame range for the given zone
        start_frame = zone_index * frames_per_zone
        end_frame = (zone_index + 1) * frames_per_zone

        # Retrieve the limb data based on the currently selected limb
        selected_limb_key = f'data{self.option_var_1.get()}'
        limb_data = getattr(self.video, selected_limb_key, {})

        # Check for 'On' touch data within this range
        return any(details.get('Onset') in {'On', 'Off'} for frame_idx, details in limb_data.items()
           if start_frame <= frame_idx < end_frame)

    def parameter_color_at_frame(self, frame):
        """
        Return "green" if any parameter for the current limb is ON at the given frame,
        or "red" if at least one parameter is OFF (and none are ON).
        """
        current_limb = self.option_var_1.get()
        result = None

        # Check general parameter dictionaries.
        for d in [self.video.parameter_button1_state_dict,
                self.video.parameter_button2_state_dict,
                self.video.parameter_button3_state_dict]:
            if frame in d:
                state = d[frame]
                if state == "ON":
                    return "green"
                elif state == "OFF":
                    result = "red"

        # Check limb-specific parameters (keys are tuples: (limb, frame)).
        for i in range(1, 4):
            limb_param = getattr(self.video, f"limb_parameter{i}")
            if (current_limb, frame) in limb_param:
                state = limb_param.get((current_limb, frame))
                if state == "ON":
                    return "green"
                elif state == "OFF":
                    result = "red"

        
        return result

    def draw_timeline(self):
        self.timeline_canvas.delete("all")  # Clear existing drawings
        if not (self.video and self.video.total_frames > 0):
            return

        canvas_width = self.timeline_canvas.winfo_width()
        sector_width = canvas_width / self.video.number_frames_in_zone
        offset = self.video.number_frames_in_zone * self.video.current_frame_zone

        # Determine the data source based on option.
        data_source = {
            'RH': self.video.dataRH,
            'LH': self.video.dataLH,
            'RL': self.video.dataRL,
            'LL': self.video.dataLL
        }
        data = data_source.get(self.option_var_1.get(), self.video.data)

        top = 0
        bottom = 100
        self.is_touch_timeline = False if self.video.current_frame_zone == 0 else self.video.touch_to_next_zone[self.video.current_frame_zone]

        def get_color(frame_idx, data):
            if frame_idx > self.video.total_frames:
                return 'black'
            details = data.get(frame_idx, {})
            array = details.get('xy', (None, None))
            if array is None or not array:
                return self.color_during if self.is_touch_timeline else 'lightgrey'
            if len(array) >= 1 and array[0] and array[0][0] is not None:
                if details.get('Onset') == 'On':
                    self.is_touch_timeline = True
                    return 'lightgreen'
                else:
                    self.is_touch_timeline = False
                    return '#E57373'
            else:
                return self.color_during if self.is_touch_timeline else 'lightgrey'

        # Draw each frame in the timeline zone.
        for frame in range(self.video.number_frames_in_zone):
            left = frame * sector_width
            right = left + sector_width
            frame_offset = frame + offset

            color = get_color(frame_offset, data)
            self.timeline_canvas.create_rectangle(left, top, right, bottom, fill=color, outline='black')

            # Overlay parameter indicator line.
            # If any parameter is set, display a vertical line at the center of the sector:
            param_color = self.parameter_color_at_frame(frame_offset)
            if param_color is not None:
                mid_x = (left + right) / 2
                self.timeline_canvas.create_line(mid_x, top, mid_x, bottom, fill=param_color, width=2)

            # Special case for the current frame.
            if frame_offset == self.video.current_frame:
                self.timeline_canvas.create_rectangle(left, top, right, bottom, fill='dodgerblue', outline='black')

            # Update touch-to-next-zone for the last frame of the zone.
            if frame == self.video.number_frames_in_zone - 1:
                if self.video.current_frame_zone + 1 < len(self.video.touch_to_next_zone):
                    self.video.touch_to_next_zone[self.video.current_frame_zone + 1] = (color == self.color_during)
                elif self.video.current_frame_zone + 1 == len(self.video.touch_to_next_zone):
                    self.video.touch_to_next_zone.append(color == self.color_during)
    
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

            current_time_text = f"{format_time(int(current_time))} / {format_time(int(total_time))}"
            self.time_counter_label.config(text=current_time_text)
        else:
            self.frame_counter_label.config(text="0 / 0")
        
        self.video.current_frame_zone = int(self.video.current_frame/self.video.number_frames_in_zone)
        #self.draw_timeline2()
        #self.update_diagram()
    
    def display_first_frame(self, frame_number=None):
        #print("INFO:self.play:",self.play)
        #print("INFO: Displaying")
        #self.background_thread.run()
        if frame_number is None:
            frame_number = self.video.current_frame
        else:
            self.video.current_frame = frame_number

        if frame_number < 0:
            print("ERROR: Frame number cannot be negative.")
            return
        if frame_number > self.video.total_frames:
            print("ERROR: Frame number cannot be bigger than the maximum number of frames.")
            return

        # Check if the frame is already in the buffer
        if frame_number in self.img_buffer:
            photo_img = self.img_buffer[frame_number]
            if hasattr(self, 'frame_label') and self.frame_label:
                self.frame_label.configure(image=photo_img)
            else:
                self.frame_label = tk.Label(self.video_frame, image=photo_img)
                self.frame_label.pack(expand=True)
            self.loading_label.config(text="Buffer Loaded", bg='lightgreen')
            self.image = photo_img  # Keep a reference to prevent garbage collection
        else:
            print("INFO: Frame not in buffer. You may need to wait or trigger a buffer update.")
            self.loading_label.config(text="Buffer Loading", bg='#E57373')
           
        self.update_note_entry()
        self.update_frame_counter()
        self.update_limb_parameter_buttons()
        self.update_button_colors()
        #self.draw_timeline()
        #self.draw_timeline2()
    
    def resize_frame(self, img):
        # Get current dimensions of the video frame for resizing
        display_width = self.video_frame.winfo_width()
        display_height = self.video_frame.winfo_height()
        
        # Calculate the new size maintaining the aspect ratio
        original_width, original_height = img.size
        aspect_ratio = original_width / original_height
        if display_width / display_height > aspect_ratio:
            new_width = int(display_height * aspect_ratio)
            new_height = display_height
        else:
            new_width = display_width
            new_height = int(display_width / aspect_ratio)

        # Resize the image using high-quality downsampling
        self.old_width = new_width
        self.old_height = new_height
        resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        #if img == resized_img:
            #print("no differenc in resizing")
        #else:
            #print("resizing")
        return resized_img
    
    def next_frame(self,number_of_frames,play=False):
        #self.option_var_2.set("DNK")
        #number of frames is: -10,-1,1,10
        
        
        if self.video != None:
            if number_of_frames > 0:
                # Implement what happens when 'Next Frame' is clicked
                if self.video.current_frame+number_of_frames > self.video.total_frames:
                    self.video.current_frame = self.video.total_frames
                else:
                    self.video.current_frame = self.video.current_frame+number_of_frames
                #self.update_diagram()
                #print("Go next for:",number_of_frames)
                self.display_first_frame()
                if play:
                    pass
                    #self.draw_timeline()
                else:
                    self.draw_timeline()
                self.draw_timeline2()
            elif number_of_frames < 0:
                if self.video.current_frame+number_of_frames < 0:
                    self.video.current_frame = 0
                else:
                    self.video.current_frame = self.video.current_frame+number_of_frames
                #print("Go back for:",number_of_frames)
                self.display_first_frame()
                if play:
                    pass
                    #self.draw_timeline()
                else:
                    self.draw_timeline()
                self.draw_timeline2()
            else:
                print("ERROR: Wrong number of frames.")
            #looking_data = self.get_looking_data_for_frame(self.video.current_frame)
            #self.option_var_2.set(looking_data)
        else:
            print("ERROR: Video = None")
            
            
        #if self.option_var_2.get() == "L" or self.option_var_2.get() == "NL":
            #self.save_looking()
        
    def update_button_colors(self):
        """Periodically update button colors based on the current state in the dictionaries."""

        # Dictionary mapping parameter numbers to their corresponding state dictionaries
        parameter_dicts = {
            1: self.video.parameter_button1_state_dict,
            2: self.video.parameter_button2_state_dict,
            3: self.video.parameter_button3_state_dict,

        }

        # Dictionary mapping parameter numbers to their corresponding buttons
        parameter_buttons = {
            1: self.par1_btn,
            2: self.par2_btn,
            3: self.par3_btn,

        }

        # Get the current frame (key)
        key = self.video.current_frame

        # Iterate over each parameter number and corresponding button
        for param_num, param_dict in parameter_dicts.items():
            # Get the current state for this parameter
            current_state = param_dict.get(key, None)

            # Update the corresponding button's color based on its state
            if current_state is None:
                parameter_buttons[param_num].config(bg='lightgrey')  # Default color
            elif current_state == "ON":
                parameter_buttons[param_num].config(bg='lightgreen')  # Button is ON
            elif current_state == "OFF":
                parameter_buttons[param_num].config(bg='#E57373')  # Button is OFF
            else:
                parameter_buttons[param_num].config(bg='lightgrey')

        # Optionally, you can schedule this function to be called periodically
        # Example: self.root.after(1000, self.update_button_colors)
    
    def parameter_dic_insert(self, parameter):
        """Toggle the state of the button between 'ON', 'OFF', and None dynamically for any parameter."""

        # Dictionary mapping parameter numbers to their corresponding state dictionaries
        parameter_dicts = {
            1: self.video.parameter_button1_state_dict,
            2: self.video.parameter_button2_state_dict,
            3: self.video.parameter_button3_state_dict,

        }

        # Dictionary mapping parameter numbers to their corresponding buttons (if needed)
        parameter_buttons = {
            1: self.par1_btn,
            2: self.par2_btn,
            3: self.par3_btn,

        }

        # Retrieve the current frame (key)
        key = self.video.current_frame

        # Get the state dictionary for the given parameter
        current_dict = parameter_dicts.get(parameter)

        # Get the current state for the given key (default is None)
        current_state = current_dict.get(key, None)

        # Cycle through the states: None -> ON -> OFF -> None
        if current_state is None:
            new_state = "ON"
            parameter_buttons[parameter].config(bg='lightgreen')  # Change button color to green
        elif current_state == "ON":
            new_state = "OFF"
            parameter_buttons[parameter].config(bg='#E57373')  # Change button color to red
        elif current_state == "OFF":
            new_state = None
            parameter_buttons[parameter].config(bg='lightgrey')  # Change button color to grey

        # Update the dictionary with the new state
        current_dict[key] = new_state

        # For debugging or tracking, you can print the updated dictionary
        print("Dictionary",parameter,current_dict)
    
    def init_controls(self):
        # Initialize control buttons here, using grid layout
        self.load_video_btn = tk.Button(self.control_frame, text="Load Video", command=self.load_video)
        self.load_video_btn.grid(row=0, column=0, padx=5, pady=5)

        self.cloth_btn = tk.Button(self.control_frame, text="Clothes", command=self.open_cloth_app)
        self.cloth_btn.grid(row=0, column=1, padx=5, pady=5)

        save_btn = tk.Button(self.control_frame, text="Save", command=self.save_data)
        save_btn.grid(row=0, column=2, padx=5, pady=5)

        analysis_btn = tk.Button(self.control_frame, text="Analysis", command=self.analysis)
        analysis_btn.grid(row=0, column=3, padx=5, pady=5)

        #export_btn = tk.Button(self.control_frame, text="Export", command=self.export)
        #export_btn.grid(row=0, column=4, padx=5, pady=5)

        back_10_frame_btn = tk.Button(self.control_frame, text="<<", command=lambda: self.next_frame(-7))
        back_10_frame_btn.grid(row=0, column=5, padx=5, pady=5)

        back_frame_btn = tk.Button(self.control_frame, text="<", command=lambda: self.next_frame(-1))
        back_frame_btn.grid(row=0, column=6, padx=5, pady=5)

        self.frame_counter_label = tk.Label(self.control_frame, text="0 / 0")
        self.frame_counter_label.grid(row=1, column=7, padx=5)

        self.time_counter_label = tk.Label(self.control_frame, text="0 / 0")
        self.time_counter_label.grid(row=0, column=7, padx=5)

        next_frame_btn = tk.Button(self.control_frame, text=">", command=lambda: self.next_frame(1))
        next_frame_btn.grid(row=0, column=8, padx=5, pady=5)

        next_10_frame_btn = tk.Button(self.control_frame, text=">>", command=lambda: self.next_frame(7))
        next_10_frame_btn.grid(row=0, column=9, padx=5, pady=5)

        play_btn = tk.Button(self.control_frame, text="Play", command=self.play_video)
        play_btn.grid(row=0, column=10, padx=5, pady=5)

        stop_btn = tk.Button(self.control_frame, text="Stop", command=self.stop_video)
        stop_btn.grid(row=0, column=11, padx=5, pady=5)






        self.framerate_label = tk.Label(self.control_frame, text=f"Frame Rate: -----",bg='lightgrey')
        self.framerate_label.grid(row=0, column=12, padx=5, pady=5)

        self.min_touch_lenght_label = tk.Label(self.control_frame, text=f"Minimal Touch Length: -----",bg='lightgrey')
        self.min_touch_lenght_label.grid(row=0, column=13, padx=5, pady=5)

        self.loading_label = tk.Label(self.control_frame, text="Buffer Loaded",bg='lightgrey')
        self.loading_label.grid(row=1, column=12, padx=5, pady=5)
        
        # Now place the labels in a new row
        self.name_label = tk.Label(self.control_frame, text="Video Name: -----",bg='lightgrey')
        self.name_label.grid(row=1, column=13, columnspan=3, padx=5, pady=5, sticky="w")

        # Now place the labels in a new row
        self.mode_label = tk.Label(self.control_frame, text="Mode: -----",bg='lightgrey')
        self.mode_label.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="w")
        
        
        self.background_thread_play = Thread(target=self.background_update_play)
        self.background_thread_play.daemon = True

    def extract_zones_from_file(self, file_path):
        """
        Extract zones covered by clothes from the given .txt file.

        Args:
            file_path (str): Path to the .txt file.

        Returns:
            List[str] or None: A list of unique zones covered by clothes, or None if the file doesn't exist.
        """
        # Check if the file exists
        if not os.path.exists(file_path):
            return None

        zones = set()  # Using a set to avoid duplicates

        # Open the file and read it line by line
        with open(file_path, 'r') as file:
            for line in file:
                # Check if the line contains the word 'Zones'
                if 'Zones=' in line:
                    # Extract the part after 'Zones='
                    zone_data = line.split('Zones=')[-1].strip()
                    # Add the zone to the set
                    zones.add(zone_data)

        return list(zones)  # Return the list of unique zones
     
    def export(self):
        
        #self.save_data()
        """
        Merges limb data, looking data, general parameters, limb parameters, and notes into a single export file.
        Ensures all parameter-limb combinations are present, even if missing data.
        """
        
        # Load the limb data
        lh_df = pd.read_csv(self.video.dataLH_path_to_csv)
        ll_df = pd.read_csv(self.video.dataLL_path_to_csv)
        rh_df = pd.read_csv(self.video.dataRH_path_to_csv)
        rl_df = pd.read_csv(self.video.dataRL_path_to_csv)
        
        # Rename columns to identify each limb clearly
        for df, limb in zip([lh_df, ll_df, rh_df, rl_df], ['LH', 'LL', 'RH', 'RL']):
            df.columns = [f"{limb}_{col}" if col != "Frame" else "Frame" for col in df.columns]
        
        # Merge all limb data
        merged_df = lh_df.merge(ll_df, on="Frame", how="outer") \
                        .merge(rh_df, on="Frame", how="outer") \
                        .merge(rl_df, on="Frame", how="outer")
        
        
        for i in range(1, 4):
            param_path = getattr(self.video, f'dataparameter_{i}_path_to_csv')

            if os.path.exists(param_path):  # Check if the file exists before reading
                param_df = pd.read_csv(param_path)
                param_df.columns = ['Frame', f'Parameter_{i}']
                merged_df = merged_df.merge(param_df, on='Frame', how='outer')
            else:
                print(f"WARNING: {param_path} not found. Filling Parameter_{i} with None.")
                if f'Parameter_{i}' not in merged_df.columns:
                    merged_df[f'Parameter_{i}'] = None  # Ensure column exists

        # Delete all _Look_x columns and rename _Look_y to _Look_x for each limb (does not modify original data)
        for limb in ['LH', 'LL', 'RH', 'RL']:
            # Remove _Look_x columns
            merged_df = merged_df.drop([col for col in merged_df.columns if col == f'{limb}_Look_x'], axis=1)

            # Rename _Look_y columns to _Look_x
            merged_df = merged_df.rename(columns={f'{limb}_Look_y': f'{limb}_Look_x'})

        # Remove duplicate rows based on 'Frame'
        merged_df = merged_df.drop_duplicates(subset=['Frame'])

        # Remove columns that end with _y
        merged_df = merged_df.drop([col for col in merged_df.columns if col.endswith('_y')], axis=1)

        # Rename columns by removing _x from the names
        merged_df.columns = [col.replace('_x', '') for col in merged_df.columns]


        merged_df = merged_df.drop([col for col in merged_df.columns if col.endswith('_Touch')], axis=1)

        notes_path = self.video.dataNotes_path_to_csv

        if os.path.exists(notes_path):  # Check if the file exists before reading
            notes_df = pd.read_csv(notes_path)
            merged_df = merged_df.merge(notes_df, on='Frame', how='outer')
        else:
            print(f"WARNING: {notes_path} not found. Filling 'Note' column with None.")
            if 'Note' not in merged_df.columns:
                merged_df['Note'] = None  # Ensure column exists

        # Load limb parameters
        limb_param_path = os.path.join(os.path.dirname(self.video.dataRH_path_to_csv), f"{self.video_name}_limb_parameters.csv")
        if os.path.exists(limb_param_path):
            limb_params_df = pd.read_csv(limb_param_path)
            if not limb_params_df.empty:
                limb_params_df = limb_params_df.pivot(index='Frame', columns=['Limb', 'Parameter'], values='State')
                limb_params_df.columns = [f"{limb}_{param}" for limb, param in limb_params_df.columns]
                limb_params_df.reset_index(inplace=True)
            merged_df = pd.merge(merged_df, limb_params_df, on='Frame', how='outer')

        # Ensure all parameter-limb combinations exist
        expected_columns = ['Frame']  # Start with 'Frame' column
        for limb in ['LH', 'LL', 'RH', 'RL']:
            for param in ['Parameter_1', 'Parameter_2', 'Parameter_3']:
                col_name = f"{limb}_{param}"
                if col_name not in merged_df.columns:
                    merged_df[col_name] = None  # Ensure column exists
                expected_columns.append(col_name)  # Maintain expected order

        # Ensure unique rows per frame
        merged_df = merged_df.drop_duplicates(subset=['Frame'])

        # Reorder columns to match the expected structure
        existing_columns = [col for col in expected_columns if col in merged_df.columns]
        remaining_columns = [col for col in merged_df.columns if col not in existing_columns]
        merged_df = merged_df[existing_columns + remaining_columns]

        # Move 'Note' column to the end

                # Identify parameter columns for all limbs
        limb_params = [f"{limb}_Parameter_{i}" for limb in ['LH', 'LL', 'RH', 'RL'] for i in range(1, 4)]

        # Identify columns that should remain in the original order
        other_columns = [col for col in merged_df.columns if col not in limb_params and col != 'Note']

        # Reorder columns: Keep original order → Move limb parameters to end → Move Note to last
        final_column_order = other_columns + limb_params + (['Note'] if 'Note' in merged_df.columns else [])

        # Apply the new column order
        merged_df = merged_df[final_column_order]
        # Define output directory
        export_folder = os.path.join(os.path.dirname(os.path.dirname(self.video.dataRH_path_to_csv)), "export")
        os.makedirs(export_folder, exist_ok=True)
        output_csv = os.path.join(export_folder, f"{self.video_name}_export.csv")

        # Save merged data to CSV
        merged_df.to_csv(output_csv, index=False)

        # Add metadata
        with open(output_csv, 'r') as file:
            data = file.read()
        with open(output_csv, 'w') as file:
            file.write(f"Program Version: {self.video.program_version}\n")
            file.write(f"Video Name: {self.video_name}\n")
            file.write(f"Labeling Mode: {self.labeling_mode}\n")
            file.write(f"Frame Rate: {self.frame_rate}\n")
            clothes_list = self.extract_zones_from_file(self.video.clothes_file_path or self.video.dataNotes_path_to_csv.replace('_notes.csv', '_clothes.txt'))
            file.write(f"Zones Covered With Clothes: {clothes_list}\n")
            file.write(f"Limb Parameter 1: {self.video.limb_parameter1_name}\n")
            file.write(f"Limb Parameter 2: {self.video.limb_parameter2_name}\n")
            file.write(f"Limb Parameter 3: {self.video.limb_parameter3_name}\n")
            file.write(f"Parameter 1: {self.video.parameter1_name}\n")
            file.write(f"Parameter 2: {self.video.parameter2_name}\n")
            file.write(f"Parameter 3: {self.video.parameter3_name}\n")
            file.write("\n")  # Separate metadata from data
            file.write(data)

        print(f"INFO: Combined export saved to {output_csv}")

    def analysis(self):
        if self.video:
            self.save_data()
            print("data_path:",self.video.dataRH_path_to_csv)
            directory_path = self.video.dataRH_path_to_csv[:self.video.dataRH_path_to_csv.rfind("data\\") + len("data\\")]

            print("dir path",directory_path)
            data_path = directory_path
            index = data_path.rfind("data")
            if index != -1:
                plots_path = data_path[:index] + "plots" + data_path[index + len("data"):]
            else:
                plots_path = data_path  # If "data" is not found, keep the original string
            print("plots path",plots_path)
            print("name:",self.video_name)
            data_path ="C:/Users/lukan/Desktop/Projects/Projects_git/Labeling App/Labeled_data/cat3_mp4/data/"
            output_folder = "C:/Users/lukan/Desktop/Projects/Projects_git/Labeling App/Labeled_data/cat3_mp4/plots/"
            name = "cat3_mp4"
            analysis.do_analysis(directory_path,plots_path,self.video_name,debug=True,frame_rate=self.frame_rate)
    
    def play_video(self):
        if self.play == False:
            self.play = True
            if self.play_thread_on == True:
                pass
            else:
                self.play_thread_on = True
                self.background_thread_play.start()
                 
    def stop_video(self):
        
        self.play = False
    
    def background_update_play(self):
        if self.video is not None:
            while True:
                if self.play:
                    self.next_frame(1,play=True)

                    # **Only update the timeline every 5 frames to reduce flickering**
                    if self.video.current_frame % 10 == 0:
                        self.after(0, self.draw_timeline)

                    self.after(0, self.display_first_frame)  # Ensure smooth playback
                time.sleep(0.03)  # Controls play speed
    
    def open_cloth_app(self):
        if self.video == None:
            print("ERROR: First select video")
        else:
            ClothApp(self,self.update_data_clothes)
        
    def update_data_clothes(self, dots):
        # Aktualizace 'self.data_clothes' novými daty
        self.data_clothes = dots
        print("Data clothes updated:", self.data_clothes) 
        self.save_clothes_to_text()
        self.cloth_btn.config(bg="lightgreen")
    
    def save_clothes_to_text(self):
        # Ensure the path is initialized
        print("INFO: Saving clothes...")
        if not self.video.dataRH_path_to_csv:
            print("ERROR: Data path is not set")
            # Optionally set a default path or return to avoid proceeding with an invalid state
            return

        data_folder = os.path.dirname(self.video.dataRH_path_to_csv)
        if not os.path.exists(data_folder):
            print(f"Creating directory {data_folder} because it does not exist")
            os.makedirs(data_folder, exist_ok=True)
        video_name = os.path.splitext(os.path.basename(self.video.video_path))[0]
        video_name = self.video_name
        text_file_path = os.path.join(data_folder, f"{video_name}_clothes.txt")
        self.video.clothes_file_path = text_file_path
        with open(text_file_path, mode='w') as text_file:
            # Write header or introductory information (optional)
            text_file.write("Coordinates and Zones for Clothing Items:\n")

            for dot_id, (x, y) in self.data_clothes.items():
                zones = self.find_image_with_white_pixel(2*x,2*y)
                zones_str = ','.join(zones)  # Convert list of zones to a string
                text_file.write(f"Dot ID {dot_id}: X={x}, Y={y}, Zones={zones_str}\n")
        print("INFO: Clothes saved")
    
    def init_video(self):
        # Initialize video frame components here
        pass

    def init_timeline(self):
        # Initialize timeline components here
        pass

    def init_diagram(self):
        # Skupina pro první toggle s 4 možnostmi
        self.display_text_var = tk.StringVar()
        if self.video:
            self.bool_var = self.video.is_touch  # Příklad boolovské proměnné, kterou budete používat
        else:
            self.bool_var = False
        # Nastavení výchozího textu na základě boolovské proměnné
        #self.display_text_var.set("Touch" if self.bool_var else "No Touch")

        # Vytvoření a umístění Label widgetu
        #display_label = tk.Label(self.diagram_frame, textvariable=self.display_text_var)
        #display_label.pack(anchor="n")
        button_width = 15  # Set a fixed width for all buttons
        button_height = 1  # Set a fixed height
        
        self.option_var_1 = tk.StringVar()
        self.option_var_1.set("RH")  # Výchozí možnost
        
        label_after_separator1 = tk.Label(self.diagram_frame, text="Limb Selector", font=("Arial", 10, "bold"),bg='lightgrey')
        label_after_separator1.pack(anchor="n", pady=(5, 2))
        rb = tk.Radiobutton(self.diagram_frame, text=f"Right Hand", variable=self.option_var_1, value="RH",bg='lightgrey')
        rb.pack(anchor="n")
        rb = tk.Radiobutton(self.diagram_frame, text=f"Left Hand", variable=self.option_var_1, value="LH",bg='lightgrey')
        rb.pack(anchor="n")
        rb = tk.Radiobutton(self.diagram_frame, text=f"Right Leg", variable=self.option_var_1, value="RL",bg='lightgrey')
        rb.pack(anchor="n")
        rb = tk.Radiobutton(self.diagram_frame, text=f"Left Leg", variable=self.option_var_1, value="LL",bg='lightgrey')
        rb.pack(anchor="n")
        # Oddělovací prvek pro vizuální rozdělení dvou skupin
        separator = tk.Frame(self.diagram_frame, height=2, bd=1, relief="sunken")
        separator.pack(fill="x", padx=5, pady=5)
        
        
        
        
        separator = tk.Frame(self.diagram_frame, height=2, bd=1, relief="sunken")
        separator.pack(fill="x", padx=5, pady=5)
        #parametr
        label_after_separator2 = tk.Label(self.diagram_frame, text="Parameters (Limb-Specific)", font=("Arial", 10, "bold"),bg='lightgrey')
        label_after_separator2.pack(anchor="n", pady=(5, 2))
        # New buttons for limb-specific parameters
        self.limb_par1_btn = tk.Button(self.diagram_frame, text="Limb Parameter 1",
                                    command=lambda: self.toggle_limb_parameter(1), width=button_width, height=button_height)
        self.limb_par1_btn.pack(anchor="n")

        self.limb_par2_btn = tk.Button(self.diagram_frame, text="Limb Parameter 2",
                                    command=lambda: self.toggle_limb_parameter(2), width=button_width, height=button_height)
        self.limb_par2_btn.pack(anchor="n")

        self.limb_par3_btn = tk.Button(self.diagram_frame, text="Limb Parameter 3",
                                    command=lambda: self.toggle_limb_parameter(3), width=button_width, height=button_height)
        self.limb_par3_btn.pack(anchor="n")
        label_after_separator3 = tk.Label(self.diagram_frame, text="Parameters", font=("Arial", 10, "bold"),bg='lightgrey')
        label_after_separator3.pack(anchor="n", pady=(5, 2))
        self.par1_btn = tk.Button(self.diagram_frame, text="Parametr 1",
                                  command=lambda: self.parameter_dic_insert(1), width=button_width, height=button_height)
        self.par1_btn.pack(anchor="n")

        self.par2_btn = tk.Button(self.diagram_frame, text="Parametr 2",
                                  command=lambda: self.parameter_dic_insert(2), width=button_width, height=button_height)
        self.par2_btn.pack(anchor="n")

        self.par3_btn = tk.Button(self.diagram_frame, text="Parametr 3",
                                  command=lambda: self.parameter_dic_insert(3), width=button_width, height=button_height)
        self.par3_btn.pack(anchor="n")
        separator = tk.Frame(self.diagram_frame, height=2, bd=1, relief="sunken")
        separator.pack(fill="x", padx=5, pady=5)
        
        # Oddělovací prvek pro vizuální rozdělení dvou skupin
        


        self.note_entry = tk.Entry(self,width=40)
        self.note_entry.grid(row=2, column=1, padx=5, pady=(5, 60))
        self.note_entry.bind("<FocusIn>", self.disable_arrow_keys)
        self.note_entry.bind("<FocusOut>", self.enable_arrow_keys)
        # Create a button to save the note
        self.save_note_button = tk.Button(self, text="Save Note", command=self.save_note)
        
        self.save_note_button.grid(row=2, column=1, padx=(185, 5), pady=5)
        self.select_frame_button = tk.Button(self, text="Select Frame", command=self.select_frame)
        self.select_frame_button.grid(row=2, column=1, padx=(35, 5), pady=5)
        self.periodic_print_dot()
        #self.dot_thread = Thread(target=self.periodic_print_dot_thread)
        #self.dot_thread.daemon = True
        #self.dot_thread.start()
    
    def select_frame(self):
        frame = self.note_entry.get()
        #print("goto frame:", frame)
        #print("frame type:", type(frame))

        try:
            frame_int = int(frame)
        except ValueError:
            print("Error selecting frame: The frame number must be a valid integer.")
            self.note_entry.delete(0, 'end')
            return None

        if self.video is not None:
            if frame_int < 0:
                print("Error selecting frame: The frame number cannot be smaller than zero!")
                self.note_entry.delete(0, 'end')
                return None
            if frame_int > self.video.total_frames:
                print("Error selecting frame: The frame number cannot be larger than total frames!")
                self.note_entry.delete(0, 'end')
                return None
            self.video.current_frame = frame_int
            self.update_frame_counter()
            self.display_first_frame()
        else:
            print("Error selecting frame: The frame number cannot be selected when a video is not loaded!")
        self.note_entry.delete(0, 'end')
    
    def toggle_limb_parameter(self, param_number):
        """Toggle between ON, OFF, and None for the currently selected limb's parameter."""
        # Get the currently selected limb
        limb = self.option_var_1.get()  # RH, LH, RL, LL

        # Select the correct dictionary based on parameter number
        param_dicts = {
            1: self.video.limb_parameter1,
            2: self.video.limb_parameter2,
            3: self.video.limb_parameter3,
        }
        param_buttons = {
            1: self.limb_par1_btn,
            2: self.limb_par2_btn,
            3: self.limb_par3_btn,
        }

        param_dict = param_dicts[param_number]
        param_button = param_buttons[param_number]

        # Get the current state
        frame = self.video.current_frame
        current_state = param_dict.get((limb, frame), None)

        # Cycle through states: None -> ON -> OFF -> None
        if current_state is None:
            new_state = "ON"
            param_button.config(bg='lightgreen')  # Green for ON
        elif current_state == "ON":
            new_state = "OFF"
            param_button.config(bg='#E57373')  # Red for OFF
        else:  # "OFF"
            new_state = None
            param_button.config(bg='lightgray')  # Default gray

        # Save the new state
        param_dict[(limb, frame)] = new_state
        print(f"INFO: {limb} - Parameter {param_number} at Frame {frame} set to {new_state}")
    
    def update_limb_parameter_buttons(self):
        """Update the colors of limb parameter buttons based on current state."""
        limb = self.option_var_1.get()  # RH, LH, RL, LL
        if not self.video:
            return
        frame = self.video.current_frame

        param_dicts = {
            1: self.video.limb_parameter1,
            2: self.video.limb_parameter2,
            3: self.video.limb_parameter3,
        }
        param_buttons = {
            1: self.limb_par1_btn,
            2: self.limb_par2_btn,
            3: self.limb_par3_btn,
        }

        for param_num in range(1, 4):
            param_dict = param_dicts[param_num]
            param_button = param_buttons[param_num]

            current_state = param_dict.get((limb, frame), None)

            if current_state == "ON":
                param_button.config(bg='lightgreen')
            elif current_state == "OFF":
                param_button.config(bg='#E57373')
            else:
                param_button.config(bg='lightgray')
    
    def save_parameter_to_csv(self, parameter_number):
        """Save the data from the parameter dictionary to a CSV file."""

        # Dictionary mapping parameter numbers to their corresponding state dictionaries
        parameter_dicts = {
            1: self.video.parameter_button1_state_dict,
            2: self.video.parameter_button2_state_dict,
            3: self.video.parameter_button3_state_dict,

        }

        # Dictionary mapping parameter numbers to the CSV paths
        csv_paths = {
            1: self.video.dataparameter_1_path_to_csv,
            2: self.video.dataparameter_2_path_to_csv,
            3: self.video.dataparameter_3_path_to_csv,

        }

        # Get the dictionary and path based on the parameter_number
        parameter_dict = parameter_dicts.get(parameter_number)
        csv_path = csv_paths.get(parameter_number)

        if parameter_dict and csv_path:
            # Write the dictionary to the CSV file
            with open(csv_path, mode='w', newline='') as csv_file:
                writer = csv.writer(csv_file)

                # Write headers (Optional: Customize if you need more columns)
                writer.writerow(['Frame', 'State'])

                # Write the key-value pairs to the CSV
                for key, value in parameter_dict.items():
                    writer.writerow([key, value])

            print(f"Data for parameter {parameter_number} saved to {csv_path}")
        else:
            print(f"Parameter {parameter_number} data or CSV path not found.")

    def save_limb_parameters(self):
        """Save limb parameters to a CSV file in the data folder."""
        if not self.video:
            return

        # Use the correct path for the data folder
        data_folder = os.path.dirname(self.video.dataRH_path_to_csv)
        csv_path = os.path.join(data_folder, f"{self.video_name}_limb_parameters.csv")

        limb_params = {
            "Parameter_1": self.video.limb_parameter1,
            "Parameter_2": self.video.limb_parameter2,
            "Parameter_3": self.video.limb_parameter3,
        }

        with open(csv_path, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Limb", "Frame", "Parameter", "State"])  # Header

            for param_name, param_dict in limb_params.items():
                for (limb, frame), state in param_dict.items():
                    writer.writerow([limb, frame, param_name, state])

        print(f"INFO: Limb parameters saved to {csv_path}")
        
    def save_note(self):
        """Save or update the note for the current frame in the notes CSV."""
        print("INFO: Saving note...")
        self.enable_arrow_keys()
        current_frame = self.video.current_frame  # Get the current frame number
        note_text = self.note_entry.get()  # Get text from the entry field

        # Update or delete the note in memory
        if note_text.strip():  # If note is not empty, update it
            self.video.notes[current_frame] = note_text
        elif current_frame in self.video.notes:  # If empty, remove it from memory
            del self.video.notes[current_frame]

        # Define the notes file path
        notes_path = self.video.dataNotes_path_to_csv
        if not notes_path:
            print("ERROR: Notes CSV path is not set.")
            return

        # Overwrite the CSV with updated notes
        with open(notes_path, mode='w', newline='') as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(['Frame', 'Note'])  # Write header

            # Write all notes from memory (sorted by frame)
            for frame, note in sorted(self.video.notes.items()):
                writer.writerow([frame, note])

        print(f"INFO: Note saved for frame {current_frame}: {note_text}")
        keyboard.press_and_release('tab')
    
    def ask_labeling_mode(self):
        """Popup with buttons for selecting labeling mode."""
        mode_window = tk.Toplevel(self)
        mode_window.title("Select Labeling Mode")
        mode_window.geometry("300x150")
        mode_window.grab_set()  # Make this window modal

        label = tk.Label(mode_window, text="Choose the labeling mode:", font=("Arial", 12))
        label.pack(pady=10)

        def set_mode(mode):
            """Store the mode, update the existing label, and close the window."""
            self.labeling_mode = mode
            if mode == 'Reliability':
                bg = 'yellow'
            else:
                bg = 'lightgreen'
            self.mode_label.config(text=f"Mode: {mode}",bg=bg)  # Update the existing label
            mode_window.destroy()

        # Create buttons for selection
        normal_button = tk.Button(mode_window, text="Normal", command=lambda: set_mode("Normal"), width=15)
        normal_button.pack(pady=5)

        reliability_button = tk.Button(mode_window, text="Reliability", command=lambda: set_mode("Reliability"), width=15)
        reliability_button.pack(pady=5)

        mode_window.wait_window()  # Wait for the user to make a selection
    
    def load_video(self):
        if self.video is not None:
            print("INFO: Saving before loading new video.")
            self.save_data() 

        if self.video is not None:
            print("INFO: Saving before loading new video.")
            self.save_data()

        self.ask_labeling_mode()

        if not hasattr(self, 'labeling_mode'):
            print("INFO: No mode selected, cancelling video load.")
            return  # User closed the selection window without choosinge




        video_path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=(
                ("Video files", "*.mp4;*.mov;*.avi;*.mkv;*.flv;*.wmv"),
                ("All files", "*.*")
            )
        )
        if not video_path:
            return  # User cancelled the dialog

        self.video = Video(video_path)
        self.video.frame_rate = cv2.VideoCapture(video_path).get(cv2.CAP_PROP_FPS)
        self.video.frame_rate = round(self.video.frame_rate, 1)

        self.frame_rate = self.video.frame_rate
        self.framerate_label.config(text=f"Frame Rate: {self.frame_rate}")
        min_lenght_in_frames = self.minimal_touch_lenght*self.frame_rate/1000
        
        #self.min_touch_lenght = round(self.min_touch_lenght, 1)
        self.min_touch_lenght_label.config(text=f"Minimal Touch Length: {min_lenght_in_frames}")
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        if self.labeling_mode == "Reliability":
            video_name = video_name+"_reliability"
        self.video_name = video_name
        base_dir = os.path.join("Labeled_data", video_name)
        os.makedirs(base_dir, exist_ok=True)

        data_dir = os.path.join(base_dir, "data")
        frames_dir = os.path.join(base_dir, "frames")
        plots_dir = os.path.join(base_dir, "plots")
        export_dir = os.path.join(base_dir, "export")

        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(frames_dir, exist_ok=True)
        os.makedirs(plots_dir, exist_ok=True)
        os.makedirs(export_dir, exist_ok=True)

        self.video.frames_dir = frames_dir

        # Create or load CSV files for each type of data
        data_types = ['RH', 'LH', 'RL', 'LL']
        for type in data_types:
            suffix = type
            csv_path = os.path.join(data_dir, f"{video_name}{suffix}.csv")
            setattr(self.video, f"data{suffix}_path_to_csv", csv_path)
            if os.path.exists(csv_path):
                print(f"INFO: CSV {suffix} already exists")
                print("INFO: Loading dictionary from csv in path:",csv_path)
                setattr(self.video, f"data{suffix}", self.csv_to_dict(csv_path))
            else:
                with open(csv_path, 'w', newline='') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(['Frame', 'X', 'Y', 'Onset', 'Bodypart', 'Look', 'Zones', 'Touch'])

        data_types = ['parameter_1', 'parameter_2', 'parameter_3']
        for type in data_types:
            suffix = type
            csv_path = os.path.join(data_dir, f"{video_name}{suffix}.csv")
            setattr(self.video, f"data{suffix}_path_to_csv", csv_path)
            if os.path.exists(csv_path):
                print(f"INFO: CSV {suffix} already exists")
                print("INFO: Loading dictionary from csv in path:", csv_path)

        for i in range(3):
            self.load_parameter_from_csv(i+1)
        # Check and handle frame discrepancies
        self.load_parameter_name()
        if not self.check_items_count(frames_dir, self.video.total_frames):
            print("ERROR: Number of frames is different, creating new frames")
            self.create_frames(video_path, frames_dir)
        else:
            print("INFO: Number of frames is correct")
            
        # Initialize video display and timeline
        self.display_first_frame()
        self.draw_timeline()
        self.draw_timeline2()
        self.name_label.config(text=f"Video Name: {video_name}")
        if not self.background_thread.is_alive():
            self.background_thread.start()
        else:
            self.img_buffer.clear()
            print("INFO: Thread is already running.")
        self.save_note()
        print("INFO: Welcome back! I wish you happy labeling session! :)")
         # Initialize empty notes dictionary
        self.video.notes = {}

        # Load notes from CSV
        # Ensure notes_path is set correctly
        self.video.dataNotes_path_to_csv = os.path.join(data_dir, f"{video_name}_notes.csv")

        notes_path = self.video.dataNotes_path_to_csv
        if os.path.exists(notes_path):
            with open(notes_path, mode='r', newline='') as csv_file:
                reader = csv.reader(csv_file)
                next(reader)  # Skip header row
                for row in reader:
                    if len(row) == 2:  # Ensure valid row format
                        frame = int(row[0])
                        note = row[1]
                        self.video.notes[frame] = note  # Store notes by frame
            print("INFO: Notes loaded successfully.")

        # Ensure UI updates when video loads
        self.update_note_entry()
        #print("notes: ",self.video.notes)
        self.load_limb_parameters()
        # Check if clothes file exists and has data
        self.video.clothes_file_path = os.path.join(data_dir, f"{video_name}_clothes.txt")
        if self.video.clothes_file_path and os.path.exists(self.video.clothes_file_path):
            with open(self.video.clothes_file_path, 'r') as file:
                clothes_data = file.readlines()
            
            if len(clothes_data) > 1:  # More than just the header
                self.cloth_btn.config(bg="lightgreen")  # Change color to indicate data is present
            else:
                pass
                #self.cloth_btn.config(bg="red")  # Change color if file is empty
        else:
            pass
            #self.cloth_btn.config(bg="yellow")  # Default color if file does not exist
        self.load_video_btn.config(state=tk.DISABLED, bg="gray",fg='lightgray')

    def update_note_entry(self):
        """Update the note entry field based on the current frame."""
        current_frame = self.video.current_frame

        # Retrieve the note for the current frame
        note_text = self.video.notes.get(current_frame, "")  # Default to empty if no note

        # Update the text entry in the GUI
        self.note_entry.delete(0, tk.END)  # Clear existing text
        self.note_entry.insert(0, note_text)  # Insert the new note

        #print(f"INFO: Displaying note for frame {current_frame}: {note_text}")
    
    def create_frames(self, video_path, frames_dir):
        print("INFO: Checking if frames need to be created...")

        # If in "Reliability" mode, check for an existing normal frames directory
        if self.labeling_mode == "Reliability":
            original_video_name = self.video_name.replace("_reliability", "")  # Remove "_reliability"
            original_frames_dir = os.path.join("Labeled_data", original_video_name, "frames")

            if os.path.exists(original_frames_dir):  # Check if original frames exist
                print(f"INFO: Found existing frames at {original_frames_dir}. Copying instead of generating...")

                # Ensure the target frames directory exists
                os.makedirs(frames_dir, exist_ok=True)

                frame_files = os.listdir(original_frames_dir)  # Get list of frame files
                total_files = len(frame_files)  # Get total frame count

                for index, filename in enumerate(frame_files):
                    src_path = os.path.join(original_frames_dir, filename)
                    dst_path = os.path.join(frames_dir, filename)
                    shutil.copy2(src_path, dst_path)  # Preserve metadata while copying

                    # Calculate and display progress
                    progress = ((index + 1) / total_files) * 100
                    sys.stdout.write(f"\rCopying frames... {progress:.2f}%")
                    sys.stdout.flush()

                print("\nINFO: Frames copied successfully.")
                return  # Exit function early since frames are copied

        # If not in "Reliability" mode or no existing frames found, generate new frames
        print("INFO: Creating frames from video...")
        vidcap = cv2.VideoCapture(video_path)

        total_frames = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
        success, image = vidcap.read()
        count = 0

        while success:
            frame_path = os.path.join(frames_dir, f"frame{count}.jpg")
            cv2.imwrite(frame_path, image)  # Save frame as JPEG file
            success, image = vidcap.read()
            count += 1

            # Display progress
            progress = (count / total_frames) * 100
            sys.stdout.write(f"\rGenerating frames... {progress:.2f}%")
            sys.stdout.flush()

        print("\nINFO: Frames have been created successfully.")
        
    def check_items_count(self,folder_path, expected_count):
        
        # Získání seznamu všech položek (souborů a složek) ve složce
        items = os.listdir(folder_path)

        # Porovnání počtu položek s očekávaným počtem
        print("INFO: Number of files in frames folder: ",len(items)-1)
        print("INFO: Number of expected frames in the folder",expected_count)
        if len(items)-1 == expected_count:
            return True
        else:
            return False
     
    def csv_to_dict(self, csv_path):
        print("INFO: Loading old CSV")
        data = {}
        with open(csv_path, mode='r', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                frame = int(row['Frame'])
                xs = [int(x) for x in row['X'].split(',')] if row['X'] else []
                ys = [int(y) for y in row['Y'].split(',')] if row['Y'] else []
                onset = row.get('Onset', '')
                bodypart = row.get('Bodypart', '')
                look = row.get('Look', '')

                # Ensure 'Zone' is always stored as a proper list
                try:
                    zones = json.loads(row['Zones']) if row['Zones'] else []
                except json.JSONDecodeError:
                    zones = [row['Zones']] if row['Zones'] else []  # Fallback if not JSON formatted

                xy = list(zip(xs, ys))
                data[frame] = {
                    'xy': xy,
                    'Onset': onset,
                    'Bodypart': bodypart,
                    'Look': look,
                    'Zone': zones  # This is now guaranteed to be a list
                }
        return data
    
    def save_data(self):
        print("INFO: Saving...")
        def save_dataset(csv_path, data, touch_data=None):
            if not csv_path:
                print("ERROR: CSV path is None")
                return  

            with open(csv_path, mode='w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(['Frame', 'X', 'Y', 'Onset', 'Bodypart', 'Look', 'Zones', 'Touch'])

                for frame in range(self.video.total_frames + 1):
                    details = data.get(frame, {})
                    xy_data = details.get('xy', [])

                    # Ensure xy_data is a list of tuples before unpacking
                    if isinstance(xy_data, list) and all(isinstance(coord, tuple) and len(coord) == 2 for coord in xy_data) and xy_data:
                        xs, ys = zip(*xy_data)
                    else:
                        xs, ys = [], []  # Default to empty lists if no valid data

                    x_str = ','.join(map(str, xs))
                    y_str = ','.join(map(str, ys))
                    onset = details.get('Onset', '')
                    bodypart = details.get('Bodypart', '')
                    look = details.get('Look', '')
                    
                    # Ensure 'Zone' is stored properly as JSON string
                    zones = json.dumps(details.get('Zone', []))  # Convert list to a JSON string
                    
                    touch = ''
                    if touch_data and onset:
                        touch = 1 if onset == "On" else 0

                    writer.writerow([frame, x_str, y_str, onset, bodypart, look, zones, touch])
        # Process and save data for each data set
        save_dataset(self.video.dataRH_path_to_csv, self.video.dataRH)
        save_dataset(self.video.dataLH_path_to_csv, self.video.dataLH)
        save_dataset(self.video.dataRL_path_to_csv, self.video.dataRL)
        save_dataset(self.video.dataLL_path_to_csv, self.video.dataLL, touch_data=True)  # Example of specifying touch data
        
        for i in range(3):
            self.save_parameter_to_csv(i+1)
        print("INFO: Saved")
        self.save_limb_parameters()
        self.export()

    def on_close(self):
        if messagebox.askokcancel("Close Aplication", "Do you want to close the aplication?"):
            if self.video:
                print("INFO: Closing...")
                self.save_data()
                print("INFO: Get some rest, you deserve it!")
                print("INFO: See you soon!")
            self.destroy()

    def load_parameter_from_csv(self, parameter_number):
        """Load the data from the CSV file into the corresponding parameter dictionary."""

        # Dictionary mapping parameter numbers to their corresponding state dictionaries
        parameter_dicts = {
            1: self.video.parameter_button1_state_dict,
            2: self.video.parameter_button2_state_dict,
            3: self.video.parameter_button3_state_dict,

        }

        # Dictionary mapping parameter numbers to the CSV paths
        csv_paths = {
            1: self.video.dataparameter_1_path_to_csv,
            2: self.video.dataparameter_2_path_to_csv,
            3: self.video.dataparameter_3_path_to_csv,

        }

        # Get the dictionary and path based on the parameter_number
        parameter_dict = parameter_dicts.get(parameter_number)
        csv_path = csv_paths.get(parameter_number)

        if parameter_dict is not None and csv_path is not None:
            # Clear the current dictionary before loading new data
            parameter_dict.clear()

            try:
                # Open the CSV file and load the data into the dictionary
                with open(csv_path, mode='r') as csv_file:
                    reader = csv.reader(csv_file)
                    next(reader)  # Skip the header row

                    for row in reader:
                        if len(row) == 2:  # Expecting two columns: 'Frame' and 'State'
                            key = int(row[0])  # Convert the frame number to an integer (key)
                            value = row[1]  # The state can be 'ON', 'OFF', or 'None'
                            parameter_dict[key] = value

                print(f"Data for parameter {parameter_number} loaded from {csv_path}")
            except FileNotFoundError:
                print(f"CSV file for parameter {parameter_number} not found: {csv_path}")
        else:
            print(f"Parameter {parameter_number} data or CSV path not found.")

    def load_limb_parameters(self):
        """Load limb parameters from a CSV file in the data folder."""
        if not self.video:
            return

        data_folder = os.path.dirname(self.video.dataRH_path_to_csv)
        csv_path = os.path.join(data_folder, f"{self.video_name}_limb_parameters.csv")

        if not os.path.exists(csv_path):
            print("INFO: No saved limb parameters found in data folder.")
            return

        with open(csv_path, 'r', newline='') as file:
            reader = csv.reader(file)
            next(reader)  # Skip header

            for row in reader:
                limb, frame, param_name, state = row
                frame = int(frame)

                if param_name == "Parameter_1":
                    self.video.limb_parameter1[(limb, frame)] = state
                elif param_name == "Parameter_2":
                    self.video.limb_parameter2[(limb, frame)] = state
                elif param_name == "Parameter_3":
                    self.video.limb_parameter3[(limb, frame)] = state

        print("INFO: Limb parameters loaded successfully from data folder.")

if __name__ == "__main__":
    print("Labeling App starting...")
    app = LabelingApp()
    app.mainloop()



