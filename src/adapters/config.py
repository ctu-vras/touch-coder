"""
adapters/config.py
config.json read/write (moved from config_utils.py). The on-disk JSON format
is pinned by tests (roundtrip, key order, \\uXXXX escaping) — do not change
how load_config/save_config serialize.

The old `load_parameter_names_into` was split: the config-reading half is
`load_parameter_labels()` here; the Video-entity mutation + Tk button wiring
live in labeling_app.load_parameter_names_into.
"""

import json
import os
import shutil
from dataclasses import dataclass, field

from adapters.atomic_io import atomic_write
from gui.resource_utils import get_app_dir, resource_path


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
        with open(config_path, 'r', encoding="utf-8") as file:
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


def load_parameter_labels() -> dict:
    """Config-reading half of the old load_parameter_names_into: the six
    user-facing parameter label strings (global + limb-specific)."""
    config = load_config()
    return {
        key: config.get(key, CONFIG_DEFAULTS[key])
        for key in (
            'parameter1', 'parameter2', 'parameter3',
            'limb_parameter1', 'limb_parameter2', 'limb_parameter3',
        )
    }


@dataclass
class AppConfig:
    """Typed snapshot of config.json, loaded ONCE at startup via
    load_app_config(). Holds the same values (and the same fallback rules)
    as the individual load_* helpers above; `raw` keeps the full parsed dict
    so unknown keys survive a Settings save round-trip."""

    new_template: bool = CONFIG_DEFAULTS['new_template']
    minimal_touch_length: object = CONFIG_DEFAULTS['minimal_touch_length']
    diagram_scale: float = 1.0
    dot_size: float = 10.0
    video_downscale: float = 1.0
    jump_seconds: float = 1.0
    realtime_arrow_hold: bool = CONFIG_DEFAULTS['realtime_arrow_hold']
    perf_enabled: bool = CONFIG_DEFAULTS['perf_enabled']
    perf_log_every_s: float = CONFIG_DEFAULTS['perf_log_every_s']
    perf_log_top_n: int = CONFIG_DEFAULTS['perf_log_top_n']
    parameter1: str = CONFIG_DEFAULTS['parameter1']
    parameter2: str = CONFIG_DEFAULTS['parameter2']
    parameter3: str = CONFIG_DEFAULTS['parameter3']
    limb_parameter1: str = CONFIG_DEFAULTS['limb_parameter1']
    limb_parameter2: str = CONFIG_DEFAULTS['limb_parameter2']
    limb_parameter3: str = CONFIG_DEFAULTS['limb_parameter3']
    raw: dict = field(default_factory=dict)


def load_app_config() -> AppConfig:
    """Read config.json once and coerce it into an AppConfig, mirroring the
    exact fallback behavior of the individual load_* helpers."""
    config = load_config()

    def _float_or(raw_value, fallback, positive_only=False):
        try:
            value = float(raw_value)
        except Exception:
            value = fallback
        if positive_only and value <= 0:
            value = fallback
        return value

    return AppConfig(
        new_template=config.get('new_template', CONFIG_DEFAULTS['new_template']),
        minimal_touch_length=config.get('minimal_touch_length', CONFIG_DEFAULTS['minimal_touch_length']),
        diagram_scale=_float_or(config.get('diagram_scale', 1.0), 1.0),
        dot_size=_float_or(config.get('dot_size', 10), 10.0),
        video_downscale=_float_or(
            config.get('video_downscale', CONFIG_DEFAULTS['video_downscale']), 1.0, positive_only=True
        ),
        jump_seconds=_float_or(
            config.get('jump_seconds', CONFIG_DEFAULTS['jump_seconds']), 1.0, positive_only=True
        ),
        realtime_arrow_hold=bool(config.get('realtime_arrow_hold', CONFIG_DEFAULTS['realtime_arrow_hold'])),
        perf_enabled=bool(config.get('perf_enabled', CONFIG_DEFAULTS['perf_enabled'])),
        perf_log_every_s=float(config.get('perf_log_every_s', CONFIG_DEFAULTS['perf_log_every_s'])),
        perf_log_top_n=int(config.get('perf_log_top_n', CONFIG_DEFAULTS['perf_log_top_n'])),
        parameter1=config.get('parameter1', CONFIG_DEFAULTS['parameter1']),
        parameter2=config.get('parameter2', CONFIG_DEFAULTS['parameter2']),
        parameter3=config.get('parameter3', CONFIG_DEFAULTS['parameter3']),
        limb_parameter1=config.get('limb_parameter1', CONFIG_DEFAULTS['limb_parameter1']),
        limb_parameter2=config.get('limb_parameter2', CONFIG_DEFAULTS['limb_parameter2']),
        limb_parameter3=config.get('limb_parameter3', CONFIG_DEFAULTS['limb_parameter3']),
        raw=config,
    )
