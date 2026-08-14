"""
H4 — corrupt `config.json` must not crash the config loaders.

Every loader in `config_utils` funnels through the hardened `load_config()`, so a
truncated/garbage file yields `{}` and each loader returns its documented default
instead of raising `json.JSONDecodeError`. Inputs live under `tmp_path`; the real
`config.json` is never touched (we monkeypatch `get_config_path`).
"""
import types

import pytest

import config_utils
from config_utils import (
    load_config_flags,
    load_perf_config,
    load_display_limits,
    load_video_downscale,
    load_jump_seconds,
    load_realtime_arrow_hold,
    load_parameter_names_into,
)


@pytest.fixture
def corrupt_config(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text('{"new_template": true, "jump_sec')  # truncated -> JSONDecodeError
    monkeypatch.setattr(config_utils, "get_config_path", lambda: str(p))
    return p


def test_H4_flags_fall_back(corrupt_config):
    assert load_config_flags() == (False, '280')


def test_H4_perf_falls_back(corrupt_config):
    assert load_perf_config() == (False, 2.0, 6)


def test_H4_downscale_falls_back(corrupt_config):
    assert load_video_downscale() == 1.0


def test_H4_jump_seconds_falls_back(corrupt_config):
    assert load_jump_seconds() == 1.0


def test_H4_arrow_hold_falls_back(corrupt_config):
    assert load_realtime_arrow_hold() is True


def test_H4_display_limits_falls_back(corrupt_config):
    assert load_display_limits() == (None, None)


def test_H4_parameter_names_fall_back(corrupt_config):
    vid = types.SimpleNamespace()
    load_parameter_names_into(vid, {}, {})  # empty button dicts -> .get(n) is None -> skipped
    assert vid.parameter1_name == 'Parameter 1'
    assert vid.parameter2_name == 'Parameter 2'
    assert vid.parameter3_name == 'Parameter 3'
    assert vid.limb_parameter1_name == 'Limb Parameter 1'
    assert vid.limb_parameter2_name == 'Limb Parameter 2'
    assert vid.limb_parameter3_name == 'Limb Parameter 3'
