"""
config_utils.py
Config loading helpers used by the controller and UI.
"""

import json
import os
import shutil
from PIL import Image, ImageTk

from atomic_io import atomic_write
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


CONFIG_DEFAULTS = {
    'new_template': False,
    'minimal_touch_length': '280',
    'perf_enabled': False, 'perf_log_every_s': 2.0, 'perf_log_top_n': 6,
    'max_display_width': 0, 'max_display_height': 0,
    'video_downscale': 1.0,
    'jump_seconds': 1.0,
    'realtime_arrow_hold': True,
    'parameter1': 'Parameter 1', 'parameter2': 'Parameter 2', 'parameter3': 'Parameter 3',
    'limb_parameter1': 'Limb Parameter 1', 'limb_parameter2': 'Limb Parameter 2',
    'limb_parameter3': 'Limb Parameter 3',
}


def load_config():
    try:
        config_path = _ensure_config_file()
        with open(config_path, 'r') as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"WARNING: config.json unreadable ({e}); using defaults")
        return {}


def save_config(config: dict) -> None:
    config_path = _ensure_config_file()
    atomic_write(config_path, lambda file: json.dump(config, file, indent=2, sort_keys=False))


def load_config_flags():
    config = load_config()   # never raises; {} on corrupt file
    NEW_TEMPLATE = config.get('new_template', CONFIG_DEFAULTS['new_template'])
    minimal_touch_length = config.get('minimal_touch_length', CONFIG_DEFAULTS['minimal_touch_length'])
    return NEW_TEMPLATE, minimal_touch_length


def load_perf_config():
    config = load_config()
    enabled = bool(config.get('perf_enabled', CONFIG_DEFAULTS['perf_enabled']))
    log_every_s = float(config.get('perf_log_every_s', CONFIG_DEFAULTS['perf_log_every_s']))
    top_n = int(config.get('perf_log_top_n', CONFIG_DEFAULTS['perf_log_top_n']))
    return enabled, log_every_s, top_n


def load_display_limits():
    config = load_config()
    max_w = config.get('max_display_width', CONFIG_DEFAULTS['max_display_width'])
    max_h = config.get('max_display_height', CONFIG_DEFAULTS['max_display_height'])
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


def load_video_downscale():
    config = load_config()
    raw = config.get('video_downscale', CONFIG_DEFAULTS['video_downscale'])
    try:
        scale = float(raw)
    except Exception:
        scale = 1.0
    if scale <= 0:
        scale = 1.0
    return scale


def load_jump_seconds():
    config = load_config()
    raw = config.get('jump_seconds', CONFIG_DEFAULTS['jump_seconds'])
    try:
        seconds = float(raw)
    except Exception:
        seconds = 1.0
    if seconds <= 0:
        seconds = 1.0
    return seconds


def load_realtime_arrow_hold():
    config = load_config()
    return bool(config.get('realtime_arrow_hold', CONFIG_DEFAULTS['realtime_arrow_hold']))


def load_parameter_names_into(video_obj, par_buttons, limb_par_buttons):
    """
    Sets names onto the video object and updates the buttons' labels.
    par_buttons: dict {1: button, 2: button, 3: button}
    limb_par_buttons: dict {1: button, 2: button, 3: button}
    """
    config = load_config()
    p1 = config.get('parameter1', CONFIG_DEFAULTS['parameter1'])
    p2 = config.get('parameter2', CONFIG_DEFAULTS['parameter2'])
    p3 = config.get('parameter3', CONFIG_DEFAULTS['parameter3'])

    video_obj.parameter1_name = p1
    video_obj.parameter2_name = p2
    video_obj.parameter3_name = p3
    if par_buttons.get(1):
        par_buttons[1].config(text=f"{p1}", bg='lightgrey')
    if par_buttons.get(2):
        par_buttons[2].config(text=f"{p2}", bg='lightgrey')
    if par_buttons.get(3):
        par_buttons[3].config(text=f"{p3}", bg='lightgrey')

    video_obj.limb_parameter1_name = config.get('limb_parameter1', CONFIG_DEFAULTS['limb_parameter1'])
    video_obj.limb_parameter2_name = config.get('limb_parameter2', CONFIG_DEFAULTS['limb_parameter2'])
    video_obj.limb_parameter3_name = config.get('limb_parameter3', CONFIG_DEFAULTS['limb_parameter3'])
    if limb_par_buttons.get(1):
        limb_par_buttons[1].config(text=f"{video_obj.limb_parameter1_name}", bg='lightgrey')
    if limb_par_buttons.get(2):
        limb_par_buttons[2].config(text=f"{video_obj.limb_parameter2_name}", bg='lightgrey')
    if limb_par_buttons.get(3):
        limb_par_buttons[3].config(text=f"{video_obj.limb_parameter3_name}", bg='lightgrey')
