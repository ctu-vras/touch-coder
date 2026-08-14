"""
service_layer/migration_service.py
One-way, idempotent on-disk layout migration from the pre-rename folder scheme
to the current one (see domain.project for the canonical layout):

    Labeled_data/            ->  data/
    Labeled_data/<v>/data/   ->  data/<v>/state/
    Videos/                  ->  videos/

Rules that make this safe to run unattended on every startup:

  * Nothing is ever overwritten or merged. A destination that already exists is
    reported as a collision (WARN) and left completely alone, old copy included.
  * Only directory renames (`os.rename`) are used — same volume, atomic, and no
    byte of a frames tree is ever copied.
  * Running it when there is nothing to migrate is a no-op that logs nothing
    but the summary line, so a second run is always clean.

Called from `main` before the Tk app is constructed (whole-tree pass) and from
`project_service.prepare_project` for the video about to be opened (in case its
folder appeared after startup). Never call this from widget code.
"""

import os
from typing import List, Optional

from domain.project import (
    DATA_DIR,
    EXPORT_SUBDIR,
    FRAMES_SUBDIR,
    LEGACY_DATA_DIR,
    LEGACY_STATE_SUBDIR,
    LEGACY_VIDEOS_DIR,
    PLOTS_SUBDIR,
    STATE_SUBDIR,
    VIDEOS_DIR,
)

# A directory is treated as a labeled-video project when it holds at least one
# of these. Guards against sweeping up unrelated folders a user dropped in.
_PROJECT_MARKERS = (
    LEGACY_STATE_SUBDIR, STATE_SUBDIR, EXPORT_SUBDIR, FRAMES_SUBDIR, PLOTS_SUBDIR,
)


def _listdir(path: str) -> List[str]:
    try:
        return os.listdir(path)
    except OSError as exc:
        print(f"WARN: migration: could not list {path}: {exc}")
        return []


def _exists_case_sensitive(parent: str, name: str) -> bool:
    """True when `name` exists in `parent` with EXACTLY this spelling.

    `os.path.exists` is useless for the Videos/videos rename on Windows and
    macOS: the filesystem is case-insensitive, so it answers True for `videos`
    while only `Videos` is on disk. Comparing against the parent listing is the
    only reliable test.
    """
    return name in _listdir(parent)


def _rename(src: str, dst: str, what: str) -> bool:
    """Rename src -> dst, logging the outcome. Returns True when it happened."""
    try:
        os.rename(src, dst)
    except OSError as exc:
        print(f"ERROR: migration: failed to move {what} {src!r} -> {dst!r}: {exc}")
        return False
    print(f"INFO: migration: moved {what} {src!r} -> {dst!r}")
    return True


def _is_project_dir(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    entries = set(_listdir(path))
    return any(marker in entries for marker in _PROJECT_MARKERS)


# === Inner rename: <video>/data/ -> <video>/state/ ============================
def migrate_video_state_dir(video_dir: str) -> bool:
    """Rename the per-video working dir `data/` to `state/`.

    Returns True when a rename happened. No-op (with a WARN on collision) when
    the folder is already migrated or both spellings exist.
    """
    if not os.path.isdir(video_dir):
        return False

    entries = set(_listdir(video_dir))
    legacy = os.path.join(video_dir, LEGACY_STATE_SUBDIR)
    target = os.path.join(video_dir, STATE_SUBDIR)

    if LEGACY_STATE_SUBDIR not in entries:
        return False  # already migrated, or never had a working dir
    if STATE_SUBDIR in entries:
        print(
            f"WARN: migration: {video_dir!r} has BOTH {LEGACY_STATE_SUBDIR!r} and "
            f"{STATE_SUBDIR!r}; leaving both untouched — merge them by hand "
            f"(the app will read {STATE_SUBDIR!r})."
        )
        return False
    return _rename(legacy, target, "per-video working dir")


def migrate_project_dir(video_dir: str) -> bool:
    """Migrate a single labeled-video folder in place (currently only the
    working-dir rename). Safe and cheap to call on every video load."""
    return migrate_video_state_dir(video_dir)


# === Root rename: Labeled_data/ -> data/ ======================================
def _sweep_inner_state_dirs(target_root: str) -> None:
    """Rename `<video>/data/` -> `<video>/state/` for every project under the
    new root. Runs even when the root itself needed no rename, so a tree that
    was half-migrated by an interrupted run still converges."""
    if not os.path.isdir(target_root):
        return
    for name in sorted(_listdir(target_root)):
        migrate_project_dir(os.path.join(target_root, name))


def _migrate_data_root(root: str) -> None:
    legacy_root = os.path.join(root, LEGACY_DATA_DIR)
    target_root = os.path.join(root, DATA_DIR)

    if not _exists_case_sensitive(root, LEGACY_DATA_DIR):
        _sweep_inner_state_dirs(target_root)
        return

    if not _exists_case_sensitive(root, DATA_DIR):
        # Simple case: move the whole tree in one atomic rename, then fix the
        # inner working dirs.
        if not _rename(legacy_root, target_root, "output root"):
            return
        _sweep_inner_state_dirs(target_root)
        return

    # Both roots exist: move per-video folders that only live in the old root.
    print(
        f"INFO: migration: both {LEGACY_DATA_DIR!r} and {DATA_DIR!r} exist; "
        f"migrating per-video folders individually."
    )
    existing = set(_listdir(target_root))
    moved = 0
    for name in sorted(_listdir(legacy_root)):
        legacy_video = os.path.join(legacy_root, name)
        target_video = os.path.join(target_root, name)
        if not os.path.isdir(legacy_video):
            # Loose files (.gitkeep and friends) — never clobber, never move.
            if name not in existing:
                if _rename(legacy_video, target_video, "output-root file"):
                    moved += 1
            continue
        if name in existing:
            print(
                f"WARN: migration: COLLISION — {target_video!r} already exists, so "
                f"{legacy_video!r} was NOT migrated. Nothing was overwritten; "
                f"merge the two folders by hand."
            )
            continue
        if not _is_project_dir(legacy_video):
            print(
                f"WARN: migration: skipping {legacy_video!r} — no "
                f"{'/'.join(_PROJECT_MARKERS)} subfolder, so it does not look "
                f"like a labeled-video project."
            )
            continue
        if _rename(legacy_video, target_video, "video project"):
            moved += 1
            migrate_project_dir(target_video)

    leftovers = _listdir(legacy_root)
    if leftovers:
        print(
            f"INFO: migration: {legacy_root!r} kept ({len(leftovers)} entries left "
            f"behind); {moved} entries migrated into {target_root!r}."
        )
    else:
        try:
            os.rmdir(legacy_root)
            print(f"INFO: migration: removed now-empty {legacy_root!r}")
        except OSError as exc:
            print(f"INFO: migration: left empty {legacy_root!r} in place: {exc}")

    # Folders already living under the new root may still use the old inner name.
    _sweep_inner_state_dirs(target_root)


# === Root rename: Videos/ -> videos/ ==========================================
def _migrate_videos_root(root: str) -> None:
    if not _exists_case_sensitive(root, LEGACY_VIDEOS_DIR):
        return
    if _exists_case_sensitive(root, VIDEOS_DIR):
        print(
            f"WARN: migration: both {LEGACY_VIDEOS_DIR!r} and {VIDEOS_DIR!r} exist "
            f"under {root!r}; leaving both untouched — move the videos by hand."
        )
        return
    # Case-only rename on a case-insensitive filesystem: os.rename would be a
    # no-op (or refuse), so bounce through a temporary name.
    legacy = os.path.join(root, LEGACY_VIDEOS_DIR)
    target = os.path.join(root, VIDEOS_DIR)
    if LEGACY_VIDEOS_DIR.lower() == VIDEOS_DIR.lower():
        staging = os.path.join(root, f"{VIDEOS_DIR}__migrating")
        if _exists_case_sensitive(root, os.path.basename(staging)):
            print(f"WARN: migration: staging path {staging!r} already exists; skipping.")
            return
        if not _rename(legacy, staging, "videos root (step 1/2)"):
            return
        _rename(staging, target, "videos root (step 2/2)")
    else:
        _rename(legacy, target, "videos root")


# === Entry point ==============================================================
def migrate_layout(root: Optional[str] = None) -> None:
    """Bring the on-disk layout under `root` (default: cwd) up to date.

    Idempotent: safe to call on every startup. Logs every move and every skip
    reason; never raises — a failed migration must not stop the app from
    starting, it just leaves the old tree where it is.
    """
    root = os.path.abspath(root or os.getcwd())
    print(f"INFO: migration: checking on-disk layout under {root!r}")
    try:
        _migrate_data_root(root)
        _migrate_videos_root(root)
    except Exception as exc:  # never let a migration hiccup block startup
        print(f"ERROR: migration: aborted unexpectedly ({exc!r}); layout left as-is.")
        return
    print("INFO: migration: layout check complete.")
