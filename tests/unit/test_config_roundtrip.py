"""
`config_utils.save_config` <-> `load_config` roundtrip lock.

Settings -> Apply funnels the WHOLE config dict through save_config, so any
key the writer drops, retypes, or reorders is a silent loss of user settings.
Pinned here before the refactor:

  * a full config dict (all documented keys) roundtrips value- AND type-exact;
  * UNKNOWN keys roundtrip untouched — load_config returns the raw dict, so a
    config written by a newer app version survives an open/save in this one;
  * key order is preserved on disk (json.dump(sort_keys=False) + insertion-
    ordered dicts);
  * non-ASCII parameter labels survive AND are stored as literal UTF-8:
    save_config passes ensure_ascii=False, so the file bytes are the real
    characters, matching what the export-metadata writer has always done.
    The VALUE roundtrip is exact either way (json.load decodes \\uXXXX
    escapes); the on-disk BYTES are what is pinned here.

The config path is resolved via `get_config_path()`; we monkeypatch it (same
pattern as test_config_corrupt) so the developer's real config.json is never
touched. `_ensure_config_file` may seed the tmp file from the bundled
config.json first — save_config atomically overwrites it, which these tests
implicitly verify.
"""
import json

import pytest

from adapters import config as config_utils
from adapters.config import load_config, save_config, load_config_flags, load_jump_seconds


FULL_CONFIG = {
    "diagram_scale": 1.5,
    "dot_size": 4,
    "new_template": True,
    "minimal_touch_length": "280",
    "parameter1": "Looking1",
    "parameter2": "Pohled ěščřž",       # UTF-8 label must survive
    "parameter3": "Parameter 3",
    "limb_parameter1": "Grasp",
    "limb_parameter2": "Limb Parameter 2",
    "limb_parameter3": "Limb Parameter 3",
    "video_downscale": 2.0,
    "jump_seconds": 2.5,
    "perf_enabled": False,
    "perf_log_every_s": 2.0,
    "perf_log_top_n": 6,
    "last_labeling_mode": "Reliability",
}


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(config_utils, "get_config_path", lambda: str(p))
    return p


def test_full_config_roundtrips_identically(config_path):
    save_config(dict(FULL_CONFIG))

    loaded = load_config()

    assert loaded == FULL_CONFIG
    # Type fidelity, not just equality-after-coercion:
    assert isinstance(loaded["new_template"], bool)
    assert isinstance(loaded["jump_seconds"], float)
    assert isinstance(loaded["perf_log_top_n"], int)
    assert isinstance(loaded["minimal_touch_length"], str)


def test_unknown_keys_are_preserved(config_path):
    """load_config returns the raw parsed dict — no whitelist — so settings
    written by a newer app version must survive a save/load cycle here."""
    cfg = dict(FULL_CONFIG)
    cfg["future_feature"] = {"nested": [1, 2, 3], "on": True}
    cfg["another_unknown"] = None

    save_config(cfg)
    loaded = load_config()

    assert loaded["future_feature"] == {"nested": [1, 2, 3], "on": True}
    assert loaded["another_unknown"] is None
    assert loaded == cfg


def test_key_order_preserved_on_disk(config_path):
    save_config(dict(FULL_CONFIG))

    on_disk = json.loads(config_path.read_text(encoding="utf-8"))

    assert list(on_disk.keys()) == list(FULL_CONFIG.keys())


def test_utf8_labels_are_stored_as_literal_utf8(config_path):
    """save_config passes ensure_ascii=False -> the file holds the real UTF-8
    bytes, not ASCII \\uXXXX escapes.

    This flipped deliberately: config.json was the only file in the app that
    escaped non-ASCII (`adapters.export_writer` has always written the metadata
    sidecar as literal UTF-8), and clients do open config.json to read the
    parameter labels. Only the BYTES changed -- json.load decodes escapes, so
    the round-tripped VALUE was exact before and is still exact (last assert).
    """
    save_config(dict(FULL_CONFIG))

    raw = config_path.read_bytes()
    assert "Pohled ěščřž".encode("utf-8") in raw
    assert b"\\u011b" not in raw  # no escape for the ě any more

    assert load_config()["parameter2"] == "Pohled ěščřž"


def test_file_is_indent2_utf8_no_bom(config_path):
    save_config(dict(FULL_CONFIG))
    raw = config_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8").startswith('{\n  "diagram_scale": 1.5,')


def test_loaders_see_saved_values(config_path):
    """The typed loaders sit on top of load_config; a saved config must be
    what they report back to the app."""
    save_config(dict(FULL_CONFIG))

    new_template, minimal = load_config_flags()
    assert new_template is True
    assert minimal == "280"
    assert load_jump_seconds() == 2.5
