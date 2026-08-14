"""
On-disk layout migration (service_layer.migration_service).

Every test builds a synthetic OLD-layout tree under `tmp_path` and runs the real
`migrate_layout` against it. The real `Labeled_data/` tree is never touched —
`migrate_layout(root=...)` takes an explicit root precisely so this is testable.

Covered:
  * Labeled_data/ -> data/ when only the old root exists (incl. inner data/ -> state/)
  * both roots exist -> per-video folders migrated one by one
  * collision -> nothing moved, nothing overwritten, WARN logged
  * already-migrated tree -> no-op
  * idempotence -> a second run changes nothing
  * Videos/ -> videos/ using a case-insensitive-safe rename
"""
import os

from domain.project import DATA_DIR, LEGACY_DATA_DIR, STATE_SUBDIR
from service_layer.migration_service import migrate_layout, migrate_project_dir


# === Helpers ==================================================================
def _make_old_project(root, video="cat3", state_subdir="data", marker="unified"):
    """Old-layout video folder: <root>/<video>/{data,export,frames,plots}/."""
    video_dir = os.path.join(root, video)
    state = os.path.join(video_dir, state_subdir)
    os.makedirs(state)
    os.makedirs(os.path.join(video_dir, "export"))
    os.makedirs(os.path.join(video_dir, "frames"))
    os.makedirs(os.path.join(video_dir, "plots"))
    with open(os.path.join(state, f"{video}_{marker}.csv"), "w", encoding="utf-8") as fh:
        fh.write("Frame\n0\n")
    with open(os.path.join(video_dir, "export", f"{video}_export.csv"), "w",
              encoding="utf-8") as fh:
        fh.write("Frame,Time_ms\n0,0\n")
    for i in range(3):
        with open(os.path.join(video_dir, "frames", f"frame{i}.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8\xff\xd9")
    return video_dir


def _tree(root):
    """Relative paths of every file under root, for exact before/after compare."""
    out = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.add(rel.replace("\\", "/"))
    return out


def _names(path):
    return sorted(os.listdir(path))


# === Labeled_data/ only =======================================================
def test_old_root_only_is_renamed_and_inner_state_migrated(tmp_path, capsys):
    root = str(tmp_path)
    _make_old_project(os.path.join(root, LEGACY_DATA_DIR), "cat3")

    migrate_layout(root)

    assert not os.path.exists(os.path.join(root, LEGACY_DATA_DIR))
    video_dir = os.path.join(root, DATA_DIR, "cat3")
    assert _names(video_dir) == ["export", "frames", "plots", "state"]
    assert os.path.isfile(os.path.join(video_dir, "state", "cat3_unified.csv"))
    assert len(os.listdir(os.path.join(video_dir, "frames"))) == 3

    out = capsys.readouterr().out
    assert "output root" in out
    assert "per-video working dir" in out


def test_multiple_videos_all_get_inner_rename(tmp_path):
    root = str(tmp_path)
    legacy = os.path.join(root, LEGACY_DATA_DIR)
    for name in ("cat3", "cat3_reliability", "other_vid"):
        _make_old_project(legacy, name)

    migrate_layout(root)

    for name in ("cat3", "cat3_reliability", "other_vid"):
        assert os.path.isdir(os.path.join(root, DATA_DIR, name, STATE_SUBDIR))
        assert not os.path.exists(os.path.join(root, DATA_DIR, name, "data"))


def test_no_file_is_lost_by_the_root_rename(tmp_path):
    root = str(tmp_path)
    legacy = os.path.join(root, LEGACY_DATA_DIR)
    _make_old_project(legacy, "cat3")
    before = {p.replace(f"{LEGACY_DATA_DIR}/", f"{DATA_DIR}/").replace("/data/", "/state/")
              for p in _tree(root)}

    migrate_layout(root)

    assert _tree(root) == before


# === Both roots exist =========================================================
def test_both_roots_migrates_only_the_old_only_videos(tmp_path, capsys):
    root = str(tmp_path)
    legacy = os.path.join(root, LEGACY_DATA_DIR)
    new = os.path.join(root, DATA_DIR)
    _make_old_project(legacy, "old_only")
    os.makedirs(new)
    with open(os.path.join(new, ".gitkeep"), "w", encoding="utf-8") as fh:
        fh.write("")
    _make_old_project(new, "already_new", state_subdir=STATE_SUBDIR)

    migrate_layout(root)

    assert sorted(os.listdir(new)) == [".gitkeep", "already_new", "old_only"]
    assert os.path.isdir(os.path.join(new, "old_only", STATE_SUBDIR))
    assert os.path.isfile(os.path.join(new, "old_only", "state", "old_only_unified.csv"))
    # The emptied legacy root is cleaned up.
    assert not os.path.exists(legacy)
    out = capsys.readouterr().out
    assert "both" in out and "individually" in out


def test_collision_never_overwrites_and_warns(tmp_path, capsys):
    root = str(tmp_path)
    legacy = os.path.join(root, LEGACY_DATA_DIR)
    new = os.path.join(root, DATA_DIR)
    _make_old_project(legacy, "cat3", marker="OLDCOPY")
    _make_old_project(new, "cat3", state_subdir=STATE_SUBDIR, marker="NEWCOPY")
    legacy_before = _tree(legacy)
    new_before = _tree(new)

    migrate_layout(root)

    out = capsys.readouterr().out
    assert "WARN" in out and "COLLISION" in out
    # Both trees are byte-for-byte untouched.
    assert _tree(legacy) == legacy_before
    assert _tree(new) == new_before
    assert os.path.isfile(os.path.join(new, "cat3", "state", "cat3_NEWCOPY.csv"))
    assert os.path.isfile(os.path.join(legacy, "cat3", "data", "cat3_OLDCOPY.csv"))
    # The old root survives because it still holds the un-migrated folder.
    assert os.path.isdir(legacy)


def test_collision_does_not_block_the_other_videos(tmp_path):
    root = str(tmp_path)
    legacy = os.path.join(root, LEGACY_DATA_DIR)
    new = os.path.join(root, DATA_DIR)
    _make_old_project(legacy, "clash")
    _make_old_project(legacy, "movable")
    _make_old_project(new, "clash", state_subdir=STATE_SUBDIR)

    migrate_layout(root)

    assert os.path.isdir(os.path.join(new, "movable", STATE_SUBDIR))
    assert os.path.isdir(os.path.join(legacy, "clash", "data"))
    assert not os.path.exists(os.path.join(legacy, "movable"))


def test_inner_state_collision_leaves_both_alone(tmp_path, capsys):
    root = str(tmp_path)
    video_dir = _make_old_project(os.path.join(root, DATA_DIR), "cat3")
    os.makedirs(os.path.join(video_dir, STATE_SUBDIR))
    with open(os.path.join(video_dir, STATE_SUBDIR, "cat3_unified.csv"), "w",
              encoding="utf-8") as fh:
        fh.write("Frame\n1\n")

    migrate_layout(root)

    out = capsys.readouterr().out
    assert "WARN" in out and "BOTH" in out
    assert os.path.isfile(os.path.join(video_dir, "data", "cat3_unified.csv"))
    assert os.path.isfile(os.path.join(video_dir, STATE_SUBDIR, "cat3_unified.csv"))


# === Already migrated / idempotence ===========================================
def test_already_migrated_tree_is_untouched(tmp_path):
    root = str(tmp_path)
    _make_old_project(os.path.join(root, DATA_DIR), "cat3", state_subdir=STATE_SUBDIR)
    before = _tree(root)

    migrate_layout(root)

    assert _tree(root) == before
    assert not os.path.exists(os.path.join(root, LEGACY_DATA_DIR))


def test_migration_is_idempotent(tmp_path):
    root = str(tmp_path)
    _make_old_project(os.path.join(root, LEGACY_DATA_DIR), "cat3")

    migrate_layout(root)
    after_first = _tree(root)
    migrate_layout(root)
    migrate_layout(root)

    assert _tree(root) == after_first


def test_empty_root_is_a_safe_no_op(tmp_path, capsys):
    root = str(tmp_path)

    migrate_layout(root)

    assert _tree(root) == set()
    assert os.listdir(root) == []
    assert "layout check complete" in capsys.readouterr().out


# === Videos/ -> videos/ =======================================================
def test_videos_root_is_lowercased(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "Videos"))
    with open(os.path.join(root, "Videos", "cat3.mp4"), "wb") as fh:
        fh.write(b"movie")

    migrate_layout(root)

    # Listing the parent is the only case-sensitive test on Windows/macOS.
    assert "videos" in os.listdir(root)
    assert "Videos" not in os.listdir(root)
    assert os.path.isfile(os.path.join(root, "videos", "cat3.mp4"))


def test_videos_migration_is_idempotent(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "Videos"))
    with open(os.path.join(root, "Videos", "cat3.mp4"), "wb") as fh:
        fh.write(b"movie")

    migrate_layout(root)
    migrate_layout(root)

    assert os.listdir(root) == ["videos"]
    assert os.listdir(os.path.join(root, "videos")) == ["cat3.mp4"]


def test_lowercase_videos_only_is_left_alone(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "videos"))
    with open(os.path.join(root, "videos", "cat3.mp4"), "wb") as fh:
        fh.write(b"movie")

    migrate_layout(root)

    assert os.listdir(root) == ["videos"]
    assert os.path.isfile(os.path.join(root, "videos", "cat3.mp4"))


# === Per-video hook (called from project_service.prepare_project) =============
def test_migrate_project_dir_renames_only_the_working_dir(tmp_path):
    video_dir = _make_old_project(str(tmp_path), "cat3")

    assert migrate_project_dir(video_dir) is True

    assert _names(video_dir) == ["export", "frames", "plots", "state"]
    assert migrate_project_dir(video_dir) is False


def test_migrate_project_dir_on_missing_folder_is_false(tmp_path):
    assert migrate_project_dir(str(tmp_path / "does_not_exist")) is False
