from threading import Thread
from collections import deque
import json
import time
from tkinter import messagebox
from tkinter import Label, ttk
from tkinter import filedialog
import cv2
import os
import tkinter as tk
from PIL import Image, ImageTk
import analysis
import csv
import sys
import pandas as pd

#-----------------------------to do main
#indication of clothes
#dlouhy touch nezbarvi timeline
#timeline2 doesnt show yellow if touch starts in one zone and ends in let say 5 zones after it will higlight only two zones

#------------------------------to do features
#second display
#guide na zacatku
#analysis
#deleting confidential data
#indication of saving
#frame loading indication
#play button

#-----------------------------to do bugs
#loading second video doesnt work
#loading frames not showing


#-----------------------------to do optimizition
#pri drzeni sipky to prestane stihat
#saving doesnt have to happen for all the touches every time

#-----------------------------done
#move to specific frame
#optimize
#2x diagram size
#scroling
#7framu shift a button
#note different path
#2x zelena rozbije timeline
#novej diagram s hlavou
#pri skoku mimo buffer se nenacte nova fotka
#looking
#independent new looking
#ghost touch

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
        self.datalooking_path_to_csv = None
        self.datalooking = {}
        self.datalookingRH = {}
        self.datalookingLH = {}
        self.datalookingRL = {}
        self.datalookingLL = {}
        self.datalookingRH_path_to_csv = None
        self.datalookingLH_path_to_csv = None
        self.datalookingRL_path_to_csv = None
        self.datalookingLL_path_to_csv = None
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
        self.program_version = '4.0 new template'
        self.parameter1_name = None
        self.parameter2_name = None
        self.parameter3_name = None
        self.clothes_file_path = None
    
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

        self.timeline_canvas = tk.Canvas(self.timeline_frame, bg='lightgrey', height=50)
        self.timeline_canvas.pack(fill=tk.X, expand=True, pady=(10, 0))  # Add padding above the second timeline
        self.timeline_canvas.bind("<Button-1>", self.on_timeline_click)  # Bind click event
        
        
        
        
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
        
        self.diagram_canvas.bind("<Button-3>",lambda event: self.on_diagram_click(event,right = False))
        self.diagram_canvas.bind("<Button-1>",lambda event: self.on_diagram_click(event,right = True))
        self.diagram_canvas.bind("<Button-2>", self.on_middle_click)
        self.bind("<Right>", lambda event: self.next_frame(1))
        self.bind("<Left>", lambda event: self.next_frame(-1))
        self.bind("<Shift-Right>", lambda event: self.next_frame(7))
        self.bind("<Shift-Left>", lambda event: self.next_frame(-7))
        self.bind("<MouseWheel>", self.on_mouse_wheel)  # Windows
        self.video_frame.bind('<Configure>', self.on_resize)
        self.init_diagram()
        
        
        
        self.background_thread = Thread(target=self.background_update)
        self.background_thread.daemon = True
        
        
        
        #self.periodic_call()
    
    def on_resize(self, event):
    # Function to handle resize events
        print("INFO: Resized to {}x{}".format(event.width, event.height))
        self.img_buffer.clear()
        if self.video:
            self.display_first_frame()
    # Add your code here to clear and reload the image buffer
    def background_update(self, frame_number=None):
        while True:
            time.sleep(0.02)
            #print("buffer lenght = ",len(self.img_buffer))
            if self.video is not None:
                #print("video")
                
                frame_number = self.video.current_frame
                
                    
                if frame_number < 0:
                    print("ERROR: Frame number cannot be negative")
                    return
                if frame_number > self.video.total_frames:
                    print("ERROR: Frame number cannot be bigger than max number of frames")
                    return

                # Load and buffer frames from 10 frames before to 10 frames after the current frame
                start_frame = max(0, frame_number - 50)  # Ensure we do not go below frame 0
                end_frame = min(self.video.total_frames, frame_number + 100)  # Ensure we do not go beyond the last frame
                #current frame:
                if frame_number not in self.img_buffer:
                        frame_path = os.path.join(self.video.frames_dir, f"frame{frame_number}.jpg")
                        try:
                            img = Image.open(frame_path)
                            img = self.resize_frame(img)
                            photo_img = ImageTk.PhotoImage(img)
                            self.img_buffer[frame_number] = photo_img  # Store the image in the buffer
                            print(f"INFO: Loading current frame {frame_number}")
                            
                            self.display_first_frame()
                        except Exception as e:
                            print(f"ERROR: Opening or processing frame {i}: {str(e)}")
                            continue
                        
                for i in range(frame_number, end_frame + 1):
                    
                    if i not in self.img_buffer:  # Check if frame is already buffered
                        frame_path = os.path.join(self.video.frames_dir, f"frame{i}.jpg")
                        try:
                            img = Image.open(frame_path)
                            img = self.resize_frame(img)
                            photo_img = ImageTk.PhotoImage(img)
                            self.img_buffer[i] = photo_img  # Store the image in the buffer
                            #print(f"Loading frame {i}")
                            
                        except Exception as e:
                            print(f"ERROR: Opening or processing frame {i}: {str(e)}")
                            continue
                for i in range(frame_number, start_frame-1, -1):
                    
                    if i not in self.img_buffer:  # Check if frame is already buffered
                        frame_path = os.path.join(self.video.frames_dir, f"frame{i}.jpg")
                        try:
                            img = Image.open(frame_path)
                            img = self.resize_frame(img)
                            photo_img = ImageTk.PhotoImage(img)
                            self.img_buffer[i] = photo_img  # Store the image in the buffer
                            #print(f"Loading frame {i}")
                            
                        except Exception as e:
                            print(f"ERROR: Opening or processing frame {i}: {str(e)}")
                            continue                    
                # Clean up the buffer to hold only the 20 relevant frames
                current_frame = self.video.current_frame
                buffer_range = 200

                # Define the range of frames you want to keep in the buffer
                start_frame = current_frame - buffer_range
                end_frame = current_frame + buffer_range

                # Identify keys (frames) that are outside the buffer range
                keys_to_remove = [k for k in self.img_buffer if k < start_frame or k > end_frame]

                # Remove the identified frames from the buffer
                for k in keys_to_remove:
                    
                    del self.img_buffer[k]
                    #print("INFO: Deleting frame number: ",k)
    def load_paramter_name(self):
        with open('config.json', 'r') as file:
            config = json.load(file)
            parameter1 = config.get('parameter1', 'Parameter 1')  # Default to 'medium' if not specified
            parameter2 = config.get('parameter2', 'Parameter 2')  # Default to 'medium' if not specified
            parameter3 = config.get('parameter3', 'Parameter 3')  # Default to 'medium' if not specified
            self.video.parameter1_name = parameter1
            self.video.parameter2_name = parameter2
            self.video.parameter3_name = parameter3
            self.par1_btn.config(text=parameter1)
            self.par2_btn.config(text=parameter2)
            self.par3_btn.config(text=parameter3)

            self.par1_btn.config(text=f"{parameter1}")
            self.par2_btn.config(text=f"{parameter2}")
            self.par3_btn.config(text=f"{parameter3}")


    def load_config(self):
        with open('config.json', 'r') as file:
            config = json.load(file)
            diagram_size = config.get('diagram_size', 'small')  # Default to 'medium' if not specified
            minimal_touch_lenght = config.get('minimal_touch_lenght', '280')
            print("INFO: Loaded diagram size:", diagram_size)
            return diagram_size, minimal_touch_lenght
    #diagram 
    def on_mouse_wheel(self, event):
            if event.delta > 0 or event.num == 4:  # Scrolling up
                self.next_frame(-1)
            elif event.delta < 0 or event.num == 5:  # Scrolling down
                self.next_frame(1)
        
    def on_middle_click(self, event):
        # Get the currently selected limb data dictionary based on the option selected
        limb_key = f'data{self.option_var_1.get()}'
        limb_data = getattr(self.video, limb_key, {})
        scale = 1 if self.diagram_size == "large" else 0.5
        
        # Current frame to check
        current_frame = self.video.current_frame

        # Check if there are coordinates for the current frame
        if current_frame in limb_data:
            details = limb_data[current_frame]
            closest_distance = float('inf')
            closest_index = None

            # Iterate through coordinates in the current frame to find the closest point
            for index, (x, y) in enumerate(details['xy']):
                distance = ((x*scale - event.x)**2 + (y*scale - event.y)**2)**0.5
                if distance <= 20 and distance < closest_distance:
                    closest_distance = distance
                    closest_index = index

            # Delete the closest point if a suitable one was found
            if closest_index is not None:
                del details['xy'][closest_index]  # Remove the coordinate from the list

                # If there are no coordinates left for the frame, remove the frame entry
                if not details['xy']:
                    del limb_data[current_frame]

                # Optional: Update the canvas or UI to reflect the change3
                # This might involve re-drawing the frame or adjusting UI elements
                print(f"INFO: Deleted point {closest_index} from frame {current_frame}")
    
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
        
        if current_frame in target_data and 'xy' in target_data[current_frame]:
            target_data[current_frame]['xy'].append((x_pos, y_pos))
            target_data[current_frame]['Zone'].append(zone_results[0])
        else:
            target_data[current_frame] = {
                'xy': [(x_pos, y_pos)],
                'Onset': onset,
                'Bodypart': option,
                'Look': "No",
                'Zone':zone_results
            }
    
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
            image_path = "icons/RH_new_template.png"
            if self.video:
                touch = self.video.is_touchRH
        elif self.option_var_1.get() == "LH":
            if self.video:
                touch = self.video.is_touchLH
            image_path = "icons/LH_new_template.png"
            
        elif self.option_var_1.get() == "RL":
            if self.video:
                touch = self.video.is_touchRL
            
            image_path = "icons/RL_new_template.png"
        elif self.option_var_1.get() == "LL":
            if self.video:
                touch = self.video.is_touchLL
            image_path = "icons/LL_new_template.png"
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
       
    def periodic_print_dot(self):
        # Nejprve odstranit všechny předchozí body z plátna
        self.diagram_canvas.delete("all")  # Odstraní vše z plátna, můžete chtít odstranit jen specifické body
        self.on_radio_click()
        self.color_looking()
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
            self.color_looking()
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
    
    def draw_eyes(self):
        #print("drawing eyes")
        x1 = 152/2
        y1 = 129/2
        x2 = 215/2
        y2 = 129/2
        dot_size = 5  # Velikost bodu můžete upravit podle potřeby
    # Vytvořit "dot" jako malý kruh (oval) na souřadnicích x, y
        if self.option_var_2.get() == 'L':
            color = self.color_during
        elif self.option_var_2.get() == 'NL':
            color = 'black'
        elif self.option_var_2.get() == 'DNK':
            color = 'grey'
        self.diagram_canvas.create_oval(x1 - dot_size, y1 - dot_size, x1 + dot_size, y1 + dot_size, fill=color)
        self.diagram_canvas.create_oval(x2 - dot_size, y2 - dot_size, x2 + dot_size, y2 + dot_size, fill=color)
    
    def find_image_with_white_pixel(self, x, y):
        # List to hold the names of the images where the pixel is white
        x = int(x)
        y = int(y)
        directory = "icons/zones3_new_template"
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
        
    #timeline
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
    
    def on_timeline2_click(self,event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline2_canvas.winfo_width()
            frame_number = int(click_position / canvas_width * self.video.number_zones)
            if frame_number < self.video.current_frame_zone:
                self.touch = False
            self.video.current_frame_zone = frame_number
            print("INFO: Current zone: ",self.video.current_frame_zone)
            self.video.current_frame = self.video.number_frames_in_zone*self.video.current_frame_zone
            self.display_first_frame()
            #self.display_first_frame(frame_number)
    
    def draw_timeline2(self):
        self.timeline2_canvas.delete("all")  # Clear existing drawings
        if self.video and self.video.total_frames > 0:
            canvas_width = self.timeline2_canvas.winfo_width()
            sector_width = canvas_width / self.video.number_zones

            # Assume `self.video.zone_touches` is a list where each index represents a zone and the value is a boolean indicating if a touch occurred.
            # This list needs to be calculated or updated elsewhere in your code based on actual touch data.
            
            for frame in range(self.video.number_zones):
                left = frame * sector_width
                right = left + sector_width
                top = 0
                bottom = 100
                
                # Check if there is at least one touch in the current frame zone
                has_touch = self.check_zone_for_touch(frame)  # This function needs to be defined or replace this with your logic
                
                if frame == self.video.current_frame_zone:
                    fill_color = 'blue'
                elif has_touch:
                    fill_color = self.color_during
                else:
                    fill_color = 'gray'
                
                self.timeline2_canvas.create_rectangle(left, top, right, bottom, fill=fill_color, outline='black')

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

    def draw_timeline(self):
        self.timeline_canvas.delete("all")  # Clear existing drawings
        if not (self.video and self.video.total_frames > 0):
            return

        canvas_width = self.timeline_canvas.winfo_width()
        sector_width = canvas_width / self.video.number_frames_in_zone
        offset = self.video.number_frames_in_zone * self.video.current_frame_zone

        # Determine the data source based on option
        data_source = {
            'RH': self.video.dataRH,
            'LH': self.video.dataLH,
            'RL': self.video.dataRL,
            'LL': self.video.dataLL
        }
        data = data_source.get(self.option_var_1.get(), self.video.data)

        # Initialize variables
        top = 0
        bottom = 100
        self.is_touch_timeline = False if self.video.current_frame_zone == 0 else self.video.touch_to_next_zone[self.video.current_frame_zone]

        # Function to determine color based on data presence and type
        def get_color(frame_idx, data):
            if frame_idx > self.video.total_frames:
                return 'black'
            details = data.get(frame_idx, {})
            array = details.get('xy', (None, None))
            
            # Check if array is None or if it doesn't contain at least one element
            if array is None or not array:
                return self.color_during if self.is_touch_timeline else 'grey'
            
            # Ensure the first element of array is not None and is subscriptable
            if len(array) >= 1 and array[0] and array[0][0] is not None:
                if details.get('Onset') == 'On':
                    self.is_touch_timeline = True
                    return 'green'
                else:
                    self.is_touch_timeline = False
                    return 'red'
            else:
                return self.color_during if self.is_touch_timeline else 'grey'

        # Draw each frame in the timeline
        for frame in range(self.video.number_frames_in_zone):
            left = frame * sector_width
            right = left + sector_width
            frame_offset = frame + offset

            color = get_color(frame_offset, data)
            self.timeline_canvas.create_rectangle(left, top, right, bottom, fill=color, outline='black')

            # Special case for the current frame
            if frame_offset == self.video.current_frame:
                self.timeline_canvas.create_rectangle(left, top, right, bottom, fill='blue', outline='black')

            # Check if the zone ends with a touch and safely update the list
            if frame == self.video.number_frames_in_zone - 1:
                if self.video.current_frame_zone + 1 < len(self.video.touch_to_next_zone):
                    self.video.touch_to_next_zone[self.video.current_frame_zone + 1] = (color == self.color_during)
                elif self.video.current_frame_zone + 1 == len(self.video.touch_to_next_zone):  # Safely extend the list if at the end
                    self.video.touch_to_next_zone.append(color == self.color_during)

    def update_frame_counter(self):
        if self.video:
            current_frame_text = f"{self.video.current_frame} / {self.video.total_frames}"
            self.frame_counter_label.config(text=current_frame_text)
        else:
            self.frame_counter_label.config(text="0 / 0")
        
        self.video.current_frame_zone = int(self.video.current_frame/self.video.number_frames_in_zone)
        #self.draw_timeline2()
        #self.update_diagram()
    
    def display_first_frame(self, frame_number=None):
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
            self.loading_label.config(text="Buffer Loaded", bg='green')
            self.image = photo_img  # Keep a reference to prevent garbage collection
        else:
            print("INFO: Frame not in buffer. You may need to wait or trigger a buffer update.")
            self.loading_label.config(text="Buffer Loading", bg='red')
            #self.background_thread.run()
            #self.display_first_frame()
        #rame = cv2.imread(first_frame_path)
        #if frame is not None:
            # Convert the frame to PIL format for resizing
            #pil_frame = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            #self.pil_frame = pil_frame
            # Resize the frame to fit the display area
            #display_width = self.video_frame.winfo_width()
            #display_height = self.video_frame.winfo_height()
            #print("display_width:",display_width)
            #print("display_height:",display_height)
            #resized_frame = self.resize_frame()

            # Convert back to ImageTk format
            #frame_image = ImageTk.PhotoImage(image=resized_frame)
            #if hasattr(self, 'frame_label'):
                #self.frame_label.configure(image=frame_image)
            #else:
                #self.frame_label = tk.Label(self.video_frame, image=frame_image)
                #self.frame_label.pack(expand=True)

            #self.frame_label.image = frame_image  # Keep a reference!
            
        #else:
           # print(f"Error loading frame {frame_number}.")
        
        self.update_frame_counter()
        
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
    
    def show_frame(self):
        pass
    
    def next_frame(self,number_of_frames):
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
                self.draw_timeline()
                self.draw_timeline2()
            elif number_of_frames < 0:
                if self.video.current_frame+number_of_frames < 0:
                    self.video.current_frame = 0
                else:
                    self.video.current_frame = self.video.current_frame+number_of_frames
                #print("Go back for:",number_of_frames)
                self.display_first_frame()
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
        
    def color_looking(self):
        if self.video is not None:
            # Reset button colors
            self.do_not_know_btn.config(bg='lightgray')
            self.not_looking_btn.config(bg='lightgray')
            self.looking_btn.config(bg='lightgray')

            # Get the dictionary based on self.option_var_1.get()
            option_var = self.option_var_1.get()  # This gets "RH", "LH", "RL", or "LL"
            attribute_name = f"datalooking{option_var}"  # Create the attribute name

            # Check if the attribute exists in self.video
            if hasattr(self.video, attribute_name):
                data_dict = getattr(self.video, attribute_name)

                # Now, use this dictionary for timeline coloring
                if self.video.current_frame in data_dict:
                    details = data_dict.get(self.video.current_frame, {})
                    
                    look = details.get('Look', '')
                    if look == "DNK":
                        self.do_not_know_btn.config(bg='green')
                    elif look == "L":
                        self.looking_btn.config(bg='green')
                    elif look == "NL":
                        self.not_looking_btn.config(bg='green')


            self.update_button_colors()

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
                parameter_buttons[param_num].config(bg='green')  # Button is ON
            elif current_state == "OFF":
                parameter_buttons[param_num].config(bg='red')  # Button is OFF

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
            parameter_buttons[parameter].config(bg='green')  # Change button color to green
        elif current_state == "ON":
            new_state = "OFF"
            parameter_buttons[parameter].config(bg='red')  # Change button color to red
        elif current_state == "OFF":
            new_state = None
            parameter_buttons[parameter].config(bg='grey')  # Change button color to grey

        # Update the dictionary with the new state
        current_dict[key] = new_state

        # For debugging or tracking, you can print the updated dictionary
        print("Dictionary",parameter,current_dict)
    def looking_dic_insert(self,parametr):
        
        if parametr == 1:
            looking_data = "DNK"
        if parametr == 2:
            looking_data ="L"
        if parametr == 3:
            looking_data ="NL"
        # Current frame as the key
        if self.video is not None:
            
            self.video.datalooking[self.video.current_frame] = {
                'xy': [],
                'Onset': '',
                'Bodypart': '',
                'Look': looking_data,
                'Zone':''
            }
        
        #new version of looking-------------
        if self.video is not None:
            # Construct the attribute name dynamically based on self.option_var_1.get()
            option_var = self.option_var_1.get()  # This gets "RH", "LH", "RL", or "LL"
            attribute_name = f"datalooking{option_var}"  # Create the attribute name

            # Check if the attribute exists in self.video
            if hasattr(self.video, attribute_name):
                data_dict = getattr(self.video, attribute_name)

                # Insert the data into the correct dictionary
                data_dict[self.video.current_frame] = {
                    'xy': [],
                    'Onset': '',
                    'Bodypart': '',
                    'Look': looking_data,
                    'Zone': ''
                }
            else:
                print(f"Attribute {attribute_name} does not exist in self.video.")
            
    def get_looking_data_for_frame(self, frame_number):
        """
        Retrieves the looking data for a specific frame from a text file.
        
        :param frame_number: The frame number to retrieve data for.
        :param file_path: Path to the text file containing the data.
        :return: The looking data for the specified frame or None if not found.
        """
        file_path = self.notes_file_path
        if file_path != None:
                
            try:
                with open(file_path, 'r') as file:
                    for line in file:
                        if line.startswith(f"Frame {frame_number}:"):
                            # Extract the looking data after the frame identifier
                            return line.strip().split("Frame {frame_number}:")
            except FileNotFoundError:
                print("ERROR: File not found")
                return None
            except Exception as e:
                print(f"ERROR: An error occurred: {e}")
                return None

            # If no matching frame is found
            return None
    
    def init_controls(self):
        # Initialize control buttons here, using grid layout
        load_video_btn = tk.Button(self.control_frame, text="Load Video", command=self.load_video)
        load_video_btn.grid(row=0, column=0, padx=5, pady=5)

        self.cloth_btn = tk.Button(self.control_frame, text="Clothes", command=self.open_cloth_app)
        self.cloth_btn.grid(row=0, column=1, padx=5, pady=5)

        save_btn = tk.Button(self.control_frame, text="Save", command=self.save_data)
        save_btn.grid(row=0, column=2, padx=5, pady=5)

        analysis_btn = tk.Button(self.control_frame, text="Analysis", command=self.analysis)
        analysis_btn.grid(row=0, column=3, padx=5, pady=5)

        export_btn = tk.Button(self.control_frame, text="Export", command=self.export)
        export_btn.grid(row=0, column=4, padx=5, pady=5)

        back_10_frame_btn = tk.Button(self.control_frame, text="<<", command=lambda: self.next_frame(-7))
        back_10_frame_btn.grid(row=0, column=5, padx=5, pady=5)

        back_frame_btn = tk.Button(self.control_frame, text="<", command=lambda: self.next_frame(-1))
        back_frame_btn.grid(row=0, column=6, padx=5, pady=5)

        self.frame_counter_label = tk.Label(self.control_frame, text="0 / 0")
        self.frame_counter_label.grid(row=0, column=7, padx=5)

        next_frame_btn = tk.Button(self.control_frame, text=">", command=lambda: self.next_frame(1))
        next_frame_btn.grid(row=0, column=8, padx=5, pady=5)

        next_10_frame_btn = tk.Button(self.control_frame, text=">>", command=lambda: self.next_frame(7))
        next_10_frame_btn.grid(row=0, column=9, padx=5, pady=5)

        play_btn = tk.Button(self.control_frame, text="Play", command=self.play_video)
        play_btn.grid(row=0, column=10, padx=5, pady=5)

        stop_btn = tk.Button(self.control_frame, text="Stop", command=self.stop_video)
        stop_btn.grid(row=0, column=11, padx=5, pady=5)






        self.framerate_label = tk.Label(self.control_frame, text=f"Frame Rate: -----")
        self.framerate_label.grid(row=0, column=12, padx=5, pady=5)

        self.min_touch_lenght_label = tk.Label(self.control_frame, text=f"Minimal Touch Length: -----")
        self.min_touch_lenght_label.grid(row=0, column=13, padx=5, pady=5)

        self.loading_label = tk.Label(self.control_frame, text="Buffer loaded")
        self.loading_label.grid(row=1, column=7, padx=5, pady=5)
        
        # Now place the labels in a new row
        self.name_label = tk.Label(self.control_frame, text="Video Name: -----")
        self.name_label.grid(row=1, column=12, columnspan=3, padx=5, pady=5, sticky="w")

        
        
        
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
        self.save_data()
        """
        Merge the limb data, looking data, notes, and parameters from the provided CSV files into one CSV file.
        """
        lh_path = self.video.dataLH_path_to_csv
        ll_path = self.video.dataLL_path_to_csv
        rh_path = self.video.dataRH_path_to_csv
        rl_path = self.video.dataRL_path_to_csv

        # Load the limb data from the CSV files (read-only)
        lh_df = pd.read_csv(lh_path)
        ll_df = pd.read_csv(ll_path)
        rh_df = pd.read_csv(rh_path)
        rl_df = pd.read_csv(rl_path)

        # Rename the columns to identify each limb's data clearly (temporary renaming)
        lh_df.columns = [f'LH_{col}' if col != 'Frame' else 'Frame' for col in lh_df.columns]
        ll_df.columns = [f'LL_{col}' if col != 'Frame' else 'Frame' for col in ll_df.columns]
        rh_df.columns = [f'RH_{col}' if col != 'Frame' else 'Frame' for col in rh_df.columns]
        rl_df.columns = [f'RL_{col}' if col != 'Frame' else 'Frame' for col in rl_df.columns]

        # Merge the dataframes based on the 'Frame' column (creates a new dataframe)
        merged_df = pd.merge(lh_df, ll_df, on='Frame', how='outer')
        merged_df = pd.merge(merged_df, rh_df, on='Frame', how='outer')
        merged_df = pd.merge(merged_df, rl_df, on='Frame', how='outer')

        # Load the looking data (read-only)
        looking_lh_path = self.video.datalookingLH_path_to_csv
        looking_ll_path = self.video.datalookingLL_path_to_csv
        looking_rh_path = self.video.datalookingRH_path_to_csv
        looking_rl_path = self.video.datalookingRL_path_to_csv

        looking_lh_df = pd.read_csv(looking_lh_path)
        looking_ll_df = pd.read_csv(looking_ll_path)
        looking_rh_df = pd.read_csv(looking_rh_path)
        looking_rl_df = pd.read_csv(looking_rl_path)

        # Rename the columns to identify each limb's looking data clearly (temporary renaming)
        looking_lh_df.columns = [f'LH_{col}' if col != 'Frame' else 'Frame' for col in looking_lh_df.columns]
        looking_ll_df.columns = [f'LL_{col}' if col != 'Frame' else 'Frame' for col in looking_ll_df.columns]
        looking_rh_df.columns = [f'RH_{col}' if col != 'Frame' else 'Frame' for col in looking_rh_df.columns]
        looking_rl_df.columns = [f'RL_{col}' if col != 'Frame' else 'Frame' for col in looking_rl_df.columns]

        # Merge looking data into the merged dataframe (creates a new dataframe)
        merged_df = pd.merge(merged_df, looking_lh_df, on='Frame', how='outer')
        merged_df = pd.merge(merged_df, looking_ll_df, on='Frame', how='outer')
        merged_df = pd.merge(merged_df, looking_rh_df, on='Frame', how='outer')
        merged_df = pd.merge(merged_df, looking_rl_df, on='Frame', how='outer')

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

        # Load the notes data (read-only)
        notes_path = self.video.dataNotes_path_to_csv
        parameter_1_path = self.video.dataparameter_1_path_to_csv
        parameter_2_path = self.video.dataparameter_2_path_to_csv
        parameter_3_path = self.video.dataparameter_3_path_to_csv

        notes_df = pd.read_csv(notes_path)
        parameter_1_df = pd.read_csv(parameter_1_path)
        parameter_2_df = pd.read_csv(parameter_2_path)
        parameter_3_df = pd.read_csv(parameter_3_path)

        # Merge parameter data into the dataframe (on 'Frame')
        notes_df.columns = ['Frame', 'Note']
        parameter_1_df.columns = ['Frame', 'Parameter_1']
        parameter_2_df.columns = ['Frame', 'Parameter_2']
        parameter_3_df.columns = ['Frame', 'Parameter_3']

        merged_df = pd.merge(merged_df, notes_df, on='Frame', how='outer')
        merged_df = pd.merge(merged_df, parameter_1_df, on='Frame', how='outer')
        merged_df = pd.merge(merged_df, parameter_2_df, on='Frame', how='outer')
        merged_df = pd.merge(merged_df, parameter_3_df, on='Frame', how='outer')

        # Reorder the columns so that columns for each limb are next to each other
        columns = ['Frame']
        for limb in ['RH', 'LH', 'LL', 'RL']:
            limb_columns = [col for col in merged_df.columns if col.startswith(limb)]
            columns.extend(limb_columns)



        # Add parameter columns to the end
        columns.extend(['Parameter_1', 'Parameter_2', 'Parameter_3','Note'])

        # Reorder the dataframe based on the new column order
        merged_df = merged_df[columns]

        # Extract the directory and video name
        directory = os.path.dirname(rh_path)
        video_name = self.video_name

        # Construct the output path
        output_csv = os.path.join(directory, f"{video_name}_export.csv")

        # Save the final merged data to a new CSV file
        merged_df.to_csv(output_csv, index=False)
        print(f"INFO: Merged CSV with notes and parameters saved to {output_csv}")



        # Construct the output path
        output_csv = os.path.join(directory, f"{video_name}_export.csv")

        # Save the final merged data to a temporary CSV file
        temp_csv = os.path.join(directory, f"{video_name}_temp.csv")
        # Remove duplicate rows based on 'Frame'
        merged_df = merged_df.drop_duplicates(subset='Frame', keep='first')

        merged_df.to_csv(temp_csv, index=False)
        if self.video.clothes_file_path is not None:
            clothes_list = self.extract_zones_from_file(self.video.clothes_file_path)
        else:
            # Use the path to notes_csv and replace '_notes.csv' with '_clothes.txt'
            notes_path = self.video.dataNotes_path_to_csv
            clothes_path = notes_path.replace('_notes.csv', '_clothes.txt')

            # Now extract the zones from this clothes file
            clothes_list = self.extract_zones_from_file(clothes_path)

        # Add metadata as the first few rows, followed by the actual data
        with open(output_csv, 'w') as f_out:
            # Write the metadata
            f_out.write(f"Program Version: {self.video.program_version}\n")
            f_out.write(f"Video Name: {self.video_name}\n")
            f_out.write(f"Frame Rate: {self.frame_rate}\n")
            f_out.write(f"Zones Covered With Clothes: {clothes_list}\n")
            f_out.write(f"Parameter 1: {self.video.parameter1_name}\n")
            f_out.write(f"Parameter 2: {self.video.parameter2_name}\n")
            f_out.write(f"Parameter 3: {self.video.parameter3_name}\n")
            f_out.write("\n")  # Blank line to separate metadata from data

            # Append the actual CSV data
            with open(temp_csv, 'r') as f_in:
                f_out.write(f_in.read())

        # Remove the temporary CSV
        os.remove(temp_csv)

        print(f"INFO: Merged CSV with metadata saved to {output_csv}")

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
                    print("INFO: Moving to next frame")
                    
                    self.next_frame(1)
                    self.display_first_frame()
                time.sleep(0.03)
        print("INFO: Ending of thread")
    
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
        
        
        self.option_var_1 = tk.StringVar()
        self.option_var_1.set("RH")  # Výchozí možnost
        
        
        rb = tk.Radiobutton(self.diagram_frame, text=f"Right Hand", variable=self.option_var_1, value="RH")
        rb.pack(anchor="n")
        rb = tk.Radiobutton(self.diagram_frame, text=f"Left Hand", variable=self.option_var_1, value="LH")
        rb.pack(anchor="n")
        rb = tk.Radiobutton(self.diagram_frame, text=f"Right Leg", variable=self.option_var_1, value="RL")
        rb.pack(anchor="n")
        rb = tk.Radiobutton(self.diagram_frame, text=f"Left Leg", variable=self.option_var_1, value="LL")
        rb.pack(anchor="n")
        # Oddělovací prvek pro vizuální rozdělení dvou skupin
        separator = tk.Frame(self.diagram_frame, height=2, bd=1, relief="sunken")
        separator.pack(fill="x", padx=5, pady=5)
        
        
        
        #self.option_var_2 = tk.StringVar()
        #self.option_var_2.set("DNK")  # Výchozí možnost
        
        #rb_looking = tk.Radiobutton(self.diagram_frame, text=f"Do Not Know", variable=self.option_var_2, value="DNK")
        #rb_looking.pack(anchor="n")
        #rb_looking = tk.Radiobutton(self.diagram_frame, text=f"Looking", variable=self.option_var_2, value="L")
        #rb_looking.pack(anchor="n")
        #rb_looking = tk.Radiobutton(self.diagram_frame, text=f"Not Looking", variable=self.option_var_2, value="NL")
        #rb_looking.pack(anchor="n")
        self.do_not_know_btn = tk.Button(self.diagram_frame, text="Do Not Know",command=lambda: self.looking_dic_insert(1))
        self.do_not_know_btn.pack(anchor="n")
        self.looking_btn = tk.Button(self.diagram_frame, text="Looking", command=lambda: self.looking_dic_insert(2))
        self.looking_btn.pack(anchor="n")
        self.not_looking_btn = tk.Button(self.diagram_frame, text="Not Looking", command=lambda: self.looking_dic_insert(3))
        self.not_looking_btn.pack(anchor="n")
        
        # Oddělovací prvek pro vizuální rozdělení dvou skupin
        separator = tk.Frame(self.diagram_frame, height=2, bd=1, relief="sunken")
        separator.pack(fill="x", padx=5, pady=5)
        #parametr
        self.par1_btn = tk.Button(self.diagram_frame, text="Parametr 1",
                                  command=lambda: self.parameter_dic_insert(1))
        self.par1_btn.pack(anchor="n")

        self.par2_btn = tk.Button(self.diagram_frame, text="Parametr 2",
                                  command=lambda: self.parameter_dic_insert(2))
        self.par2_btn.pack(anchor="n")

        self.par3_btn = tk.Button(self.diagram_frame, text="Parametr 3",
                                  command=lambda: self.parameter_dic_insert(3))
        self.par3_btn.pack(anchor="n")


        self.note_entry = tk.Entry(self,width=40)
        self.note_entry.grid(row=2, column=1, padx=5, pady=(5, 60))

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

    import csv
    import os

    import csv
    import os

    def save_note(self):
        """Save the current frame and note to a CSV file in the same directory as the previous logic."""

        # Current frame as the key
        print("INFO: Saving note...")
        current_frame = self.video.current_frame

        # Retrieve the note from the entry widget
        note_text = self.note_entry.get()

        # Save the text entry content to the notes dictionary
        if not hasattr(self.video, 'notes'):
            self.video.notes = {}  # Initialize the notes dictionary if it doesn't exist
        self.video.notes[current_frame] = note_text

        # Optionally clear the entry after saving
        self.note_entry.delete(0, 'end')

        # Determine the directory from dataRH (assuming it contains a path, adjust if it's stored differently)
        if hasattr(self.video, 'dataRH_path_to_csv'):  # Assume this is where the path is stored
            notes_dir = os.path.dirname(self.video.dataRH_path_to_csv)
        else:
            notes_dir = os.getcwd()  # Default to current working directory if no path is specified

        # Ensure the directory exists (optional, depends on setup)
        if not os.path.exists(notes_dir):
            os.makedirs(notes_dir)

        # Get the video file name without the extension
        video_name = os.path.splitext(os.path.basename(self.video.video_path))[0]

        # Construct the full path for the notes.csv file, including the video name
        notes_file_path = os.path.join(notes_dir, f"{video_name}_notes.csv")

        self.video.dataNotes_path_to_csv =  notes_file_path
        print("Notes path:",self.video.dataNotes_path_to_csv)
        file_exists = os.path.isfile(notes_file_path)

        # Write or append the note to the CSV file
        with open(notes_file_path, mode='a', newline='') as csv_file:
            writer = csv.writer(csv_file)

            # If the file is new, write the header
            if not file_exists:
                writer.writerow(['Frame', 'Note'])

            # Write the current frame and note
            writer.writerow([current_frame, note_text])

        print(f"INFO: Note saved to {notes_file_path}")

    #loading and saving
    def load_video(self):
        if self.video is not None:
            print("INFO: Saving before loading new video.")
            self.save_data() 
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
        self.video_name = video_name
        base_dir = os.path.join("Labeled_data", video_name)
        os.makedirs(base_dir, exist_ok=True)

        data_dir = os.path.join(base_dir, "data")
        frames_dir = os.path.join(base_dir, "frames")
        plots_dir = os.path.join(base_dir, "plots")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(frames_dir, exist_ok=True)
        os.makedirs(plots_dir, exist_ok=True)

        self.video.frames_dir = frames_dir

        # Create or load CSV files for each type of data
        data_types = ['RH', 'LH', 'RL', 'LL','looking','lookingRH','lookingLH','lookingRL','lookingLL']
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
        self.load_paramter_name()
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

    def create_frames(self, video_path, frames_dir):
        print("INFO: Creating frames...")

        # Open the video capture
        vidcap = cv2.VideoCapture(video_path)

        # Get total frame count (optional but helpful for progress calculation)
        total_frames = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))

        success, image = vidcap.read()
        count = 0

        # Loop through video frames
        while success:
            frame_path = os.path.join(frames_dir, f"frame{count}.jpg")
            cv2.imwrite(frame_path, image)  # Save frame as JPEG file
            success, image = vidcap.read()
            count += 1

            # Calculate progress percentage
            progress = (count / total_frames) * 100

            # Print progress on the same line using carriage return
            sys.stdout.write(f"\rProgress: {progress:.2f}%")
            sys.stdout.flush()

        print("\nINFO: Frames have been created successfully")
    
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
                if bodypart == None:
                    pass
                else:
                    zones = row.get('Zones', '')
                    look = row.get('Look', '')
                    xy = list(zip(xs, ys))
                    data[frame] = {
                        'xy': xy,
                        'Onset': onset,
                        'Bodypart': bodypart,
                        'Look': look,
                        'Zone': zones
                    }
        #print("Loaded from CSV:",data)
        return data
    
    def save_looking_new(self):
        
        csv_path = self.video.datalooking_path_to_csv
        #print("csvRH,",self.video.dataRH_path_to_csv)
        
        if not csv_path:
            print("ERROR: CSV path is None.")
            return  # Optionally, handle the error more robustly here

        with open(csv_path, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['Frame', 'X', 'Y', 'Onset', 'Bodypart', 'Look', 'Zones', 'Touch'])

            for frame in range(self.video.total_frames + 1):
                if frame in self.video.datalooking:
                    
                    details = self.video.datalooking.get(frame, {})
                    look = details.get("Look", '')
                    xs = ''
                    ys = ''
                    x_str = ''
                    y_str = ''
                    onset = ''
                    bodypart = ''
                    
                    zones = ''
                
                    if xs or ys or onset or bodypart or look:
                        writer.writerow([frame, x_str, y_str, onset, bodypart, look, zones])
                    else:
                        writer.writerow([frame])  # Write frame number if no data is available.
                #zone_results = [self.find_image_with_white_pixel(x, y) for x, y in zip(xs, ys)]
                #zones = ', '.join([result[0] for result in zone_results if result])
        #new saving
        csv_path = self.video.datalooking_path_to_csv
    
        if not csv_path:
            print("ERROR: CSV path is None.")
            return

        # Define the suffixes you want to save
        suffixes = ["RH", "LH", "RL", "LL"]

        for suffix in suffixes:
            # Create the dynamic attribute name
            attribute_name = f"datalooking{suffix}"
            
            # Check if the attribute exists in self.video
            if hasattr(self.video, attribute_name):
                data_dict = getattr(self.video, attribute_name)
                
                # Define the specific CSV path for each suffix
                csv_path_suffix = csv_path.replace('.csv', f'{suffix}.csv')
                
                # Open the CSV file for writing
                with open(csv_path_suffix, mode='w', newline='') as file:
                    writer = csv.writer(file)
                    writer.writerow(['Frame', 'X', 'Y', 'Onset', 'Bodypart', 'Look', 'Zones', 'Touch'])

                    for frame in range(self.video.total_frames + 1):
                        if frame in data_dict:
                            details = data_dict.get(frame, {})
                            look = details.get("Look", '')
                            xs = details.get("xy", [])
                            x_str = xs[0] if len(xs) > 0 else ''
                            y_str = xs[1] if len(xs) > 1 else ''
                            onset = details.get("Onset", '')
                            bodypart = details.get("Bodypart", '')
                            zones = details.get("Zone", '')
                            
                            writer.writerow([frame, x_str, y_str, onset, bodypart, look, zones])
                        else:
                            writer.writerow([frame])

        print("INFO: Saving new looking completed successfully.")
    # Process and save data for each data set
    def save_data(self):
        print("INFO: Saving...")
        def save_dataset(csv_path, data, touch_data=None):
            if not csv_path:
                print("ERROR: CSV path is None")
                return  # Optionally, handle the error more robustly here

            with open(csv_path, mode='w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(['Frame', 'X', 'Y', 'Onset', 'Bodypart', 'Look', 'Zones', 'Touch'])

                for frame in range(self.video.total_frames + 1):
                    details = data.get(frame, {})
                    xs, ys = zip(*details.get('xy', [])) if 'xy' in details else ([], [])
                    x_str = ','.join(map(str, xs))
                    y_str = ','.join(map(str, ys))
                    onset = details.get('Onset', '')
                    bodypart = details.get('Bodypart', '')
                    look = details.get('Look', '')
                    zones = details.get('Zone','')
                    
                    
                    #zone_results = [self.find_image_with_white_pixel(x, y) for x, y in zip(xs, ys)]
                    #zones = ', '.join([result[0] for result in zone_results if result])

                    touch = ''
                    if touch_data and onset:
                        touch = 1 if onset == "On" else 0

                    if xs or ys or onset or bodypart or look:
                        writer.writerow([frame, x_str, y_str, onset, bodypart, look, zones, touch])
                    else:
                        writer.writerow([frame])  # Write frame number if no data is available.

        # Process and save data for each data set
        save_dataset(self.video.dataRH_path_to_csv, self.video.dataRH)
        save_dataset(self.video.dataLH_path_to_csv, self.video.dataLH)
        save_dataset(self.video.dataRL_path_to_csv, self.video.dataRL)
        save_dataset(self.video.dataLL_path_to_csv, self.video.dataLL, touch_data=True)  # Example of specifying touch data
        self.save_looking_new()
        for i in range(3):
            self.save_parameter_to_csv(i+1)
        print("INFO: Saved")


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
if __name__ == "__main__":
    app = LabelingApp()
    app.mainloop()



