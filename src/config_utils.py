"""
config_utils.py
Config loading helpers used by the controller and UI.
"""

import json
from PIL import Image, ImageTk


def load_config_flags():
    with open('config.json', 'r') as file:
        config = json.load(file)
        NEW_TEMPLATE = config.get('new_template', False)
        diagram_size = config.get('diagram_size', 'small')
        minimal_touch_lenght = config.get('minimal_touch_lenght', '280')
        return NEW_TEMPLATE, diagram_size, minimal_touch_lenght


def load_parameter_names_into(video_obj, par_buttons, limb_par_buttons):
    """
    Sets names onto the video object and updates the buttons' labels.
    par_buttons: dict {1: button, 2: button, 3: button}
    limb_par_buttons: dict {1: button, 2: button, 3: button}
    """
    with open('config.json', 'r') as file:
        config = json.load(file)
        p1 = config.get('parameter1', 'Parameter 1')
        p2 = config.get('parameter2', 'Parameter 2')
        p3 = config.get('parameter3', 'Parameter 3')

        video_obj.parameter1_name = p1
        video_obj.parameter2_name = p2
        video_obj.parameter3_name = p3
        par_buttons[1].config(text=f"{p1}", bg='lightgrey')
        par_buttons[2].config(text=f"{p2}", bg='lightgrey')
        par_buttons[3].config(text=f"{p3}", bg='lightgrey')

        video_obj.limb_parameter1_name = config.get('limb_parameter1', 'Limb Parameter 1')
        video_obj.limb_parameter2_name = config.get('limb_parameter2', 'Limb Parameter 2')
        video_obj.limb_parameter3_name = config.get('limb_parameter3', 'Limb Parameter 3')
        limb_par_buttons[1].config(text=f"{video_obj.limb_parameter1_name}", bg='lightgrey')
        limb_par_buttons[2].config(text=f"{video_obj.limb_parameter2_name}", bg='lightgrey')
        limb_par_buttons[3].config(text=f"{video_obj.limb_parameter3_name}", bg='lightgrey')
