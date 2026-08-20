"""
ProjectPaths is the single source of truth for the on-disk layout, so these
tests pin the CURRENT scheme (post folder rename):

    data/<video_name>/{state,export,frames,plots}/

The pre-9.0 scheme was `Labeled_data/<video_name>/data/...`. Nothing here may
resolve to one of those legacy names, and no legacy state-sidecar path exists
on ProjectPaths any more (the migration that read them was removed in 9.0).
"""
import os

import pytest

from domain.project import (
    DATA_DIR,
    RELIABILITY_SUFFIX,
    STATE_SUBDIR,
    VIDEOS_DIR,
    ProjectPaths,
)

LEGACY_DATA_DIR = "Labeled_data"   # pre-9.0 root, no longer a domain constant


def _parts(path):
    return path.replace("\\", "/").split("/")


# === Constants ================================================================
def test_layout_constants_are_the_new_names():
    assert DATA_DIR == "data"
    assert STATE_SUBDIR == "state"
    assert VIDEOS_DIR == "videos"
    assert RELIABILITY_SUFFIX == "_reliability"


# === Directories ==============================================================
def test_directories_use_new_layout():
    p = ProjectPaths("cat3")
    assert _parts(p.video_dir) == ["data", "cat3"]
    assert _parts(p.state_dir) == ["data", "cat3", "state"]
    assert _parts(p.export_dir) == ["data", "cat3", "export"]
    assert _parts(p.frames_dir) == ["data", "cat3", "frames"]
    assert _parts(p.plots_dir) == ["data", "cat3", "plots"]


def test_no_path_resolves_under_a_legacy_folder_name():
    """The old scheme would have produced `data/cat3/data/...` — guard the
    double rename explicitly."""
    p = ProjectPaths("cat3")
    paths = [
        p.video_dir, p.state_dir, p.export_dir, p.frames_dir, p.plots_dir,
        p.state_db, p.export_csv, p.export_metadata,
    ]
    for path in paths:
        segments = _parts(path)
        assert LEGACY_DATA_DIR not in segments, path
        # "data" may appear ONLY as the root; never as the per-video working dir.
        assert segments.index(DATA_DIR) == 0, path
        assert segments.count(DATA_DIR) == 1, path


def test_base_dir_is_overridable_for_tests(tmp_path):
    p = ProjectPaths("cat3", base_dir=str(tmp_path))
    assert p.state_dir == os.path.join(str(tmp_path), "cat3", "state")
    assert p.video_dir == os.path.join(str(tmp_path), "cat3")


# === File composition =========================================================
@pytest.mark.parametrize("attr, subdir, filename", [
    ("state_db", "state", "cat3.db"),
    ("export_csv", "export", "cat3_export.csv"),
    ("export_metadata", "export", "cat3_metadata.json"),
])
def test_files_land_in_the_right_subdir(attr, subdir, filename):
    p = ProjectPaths("cat3")
    assert _parts(getattr(p, attr)) == ["data", "cat3", subdir, filename]


def test_no_legacy_state_sidecar_paths_remain():
    """The six retired sidecars must not be reachable through ProjectPaths.
    Their readers are gone; a surviving path property would invite a caller to
    hand-build the format again."""
    p = ProjectPaths("cat3")
    for attr in ("unified_csv", "notes_csv", "limb_params_csv",
                 "last_position_json", "video_time_json", "clothes_txt",
                 "limb_csv"):
        assert not hasattr(p, attr), attr


def test_state_db_and_export_metadata_live_in_different_subdirs():
    p = ProjectPaths("cat3")
    assert _parts(p.state_db)[2] == STATE_SUBDIR
    assert _parts(p.export_metadata)[2] == "export"


# === Reliability suffix rule ==================================================
def test_for_video_appends_reliability_suffix():
    p = ProjectPaths.for_video("cat3", reliability=True)
    assert p.video_name == "cat3_reliability"
    assert p.is_reliability is True
    assert _parts(p.state_dir) == ["data", "cat3_reliability", "state"]
    assert _parts(p.state_db)[-1] == "cat3_reliability.db"


def test_for_video_normal_mode_leaves_name_alone():
    p = ProjectPaths.for_video("cat3", reliability=False)
    assert p.video_name == "cat3"
    assert p.is_reliability is False


def test_for_video_does_not_double_suffix():
    p = ProjectPaths.for_video("cat3_reliability", reliability=True)
    assert p.video_name == "cat3_reliability"


def test_for_video_honours_base_dir(tmp_path):
    p = ProjectPaths.for_video("cat3", reliability=True, base_dir=str(tmp_path))
    assert p.video_dir == os.path.join(str(tmp_path), "cat3_reliability")


def test_original_strips_the_suffix_and_keeps_base_dir(tmp_path):
    p = ProjectPaths.for_video("cat3", reliability=True, base_dir=str(tmp_path))
    original = p.original
    assert original.video_name == "cat3"
    assert original.base_dir == str(tmp_path)
    assert original.frames_dir == os.path.join(str(tmp_path), "cat3", "frames")


def test_original_of_a_normal_project_is_itself():
    p = ProjectPaths("cat3")
    assert p.original is p


def test_paths_are_frozen():
    p = ProjectPaths("cat3")
    with pytest.raises(Exception):
        p.video_name = "other"
