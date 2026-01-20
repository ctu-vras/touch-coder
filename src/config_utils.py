"""
config_utils.py
Config loading helpers used by the controller and UI.
"""

import json
import os
import shutil
from PIL import Image, ImageTk

from resource_utils import get_app_dir, resource_path


def get_config_path() -> str:
    return os.path.join(get_app_dir(), "config.json")


def _ensure_config_file() -> str:
    config_path = get_config_path()
    if os.path.exists(config_path):
        return config_path

    bundled_config = resource_path("config.json")
    if os.path.exists(bundled_config):
        try:
            shutil.copyfile(bundled_config, config_path)
            return config_path
        except Exception:
            return bundled_config

    return config_path


def load_config():
    try:
        config_path = _ensure_config_file()
        with open(config_path, 'r') as file:
            return json.load(file)
    except Exception:
        return {}


def save_config(config: dict) -> None:
    config_path = _ensure_config_file()
    with open(config_path, 'w') as file:
        json.dump(config, file, indent=2, sort_keys=False)


def load_config_flags():
    config_path = _ensure_config_file()
    with open(config_path, 'r') as file:
        config = json.load(file)
        NEW_TEMPLATE = config.get('new_template', False)
        minimal_touch_length = config.get('minimal_touch_length', '280')
        return NEW_TEMPLATE, minimal_touch_length


def load_perf_config():
    config_path = _ensure_config_file()
    with open(config_path, 'r') as file:
        config = json.load(file)
        enabled = bool(config.get('perf_enabled', False))
        log_every_s = float(config.get('perf_log_every_s', 2.0))
        top_n = int(config.get('perf_log_top_n', 6))
        return enabled, log_every_s, top_n


def load_display_limits():
    config_path = _ensure_config_file()
    with open(config_path, 'r') as file:
        config = json.load(file)
        max_w = config.get('max_display_width', 0)
        max_h = config.get('max_display_height', 0)
        try:
            max_w = int(max_w)
        except Exception:
            max_w = 0
        try:
            max_h = int(max_h)
        except Exception:
            max_h = 0
        max_w = max_w if max_w > 0 else None
        max_h = max_h if max_h > 0 else None
        return max_w, max_h


def load_parameter_names_into(video_obj, par_buttons, limb_par_buttons):
    """
    Sets names onto the video object and updates the buttons' labels.
    par_buttons: dict {1: button, 2: button, 3: button}
    limb_par_buttons: dict {1: button, 2: button, 3: button}
    """
    config_path = _ensure_config_file()
    with open(config_path, 'r') as file:
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
