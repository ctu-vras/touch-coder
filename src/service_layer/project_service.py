"""
service_layer/project_service.py
Non-Tk logic of opening a labeled-video project, extracted from
LabelingApp.load_video, plus the per-video labeling-time accumulator.

The GUI keeps the mode dialog, file picker, progress windows and thread
starting, and calls these functions preserving the audited ordering:

    state DB open (migrating the legacy CSV/JSON state on first open)
    -> timer start -> frame load
    -> frame extraction (an extraction abort rolls back the timer)
    -> position restore -> buffer-thread start -> Changed-flag reset

Progress reporting is injected as a plain callback
(progress_cb(count, total, stage, elapsed_s)).

Persistence note: working state lives in ONE SQLite file per video
(`adapters.sqlite_repo`, opened by `open_state`). The CSV/JSON functions in
this module and in `adapters.unified_repo` are READERS only — kept permanently
as the migration + disaster-recovery path (`service_layer.state_migration`).
Nothing here writes a state CSV or a state JSON sidecar any more.
"""

import json
import os
import re
import shutil
import sys
import time
import traceback
from typing import Dict, Optional

from adapters.frame_extractor import check_items_count, create_frames
from adapters.sqlite_repo import SqliteRepository
from adapters.unified_repo import (
    csv_to_dict,
    import_unified_from_export,
    load_unified_dataset,
)
from domain.model import FrameBundle, empty_bundle
from domain.project import VIDEOS_DIR, ProjectPaths
from service_layer.migration_service import migrate_project_dir

DEFAULT_VIDEOS_DIR = VIDEOS_DIR


# === Labeling-time accumulator ================================================
#
# The accumulator now lives in the state DB (`meta.labeling_time_seconds`).
#
# `load_labeling_time_seconds` below is the MIGRATION reader for the retired
# `state/<name>_metadata.json` sidecar and is kept permanently.
#
# BUG FIX it still carries (silent reset on restart): the old writer stored
# "Total Labeling Time (hours)" while the loader read
# "Total Labeling Time (seconds)", so the accumulator restarted from 0 on every
# app launch. Files written by the fixed version hold SECONDS; loading falls
# back to "Total Labeling Time (hours)" * 3600 for files written by the buggy
# ones. The EXPORT metadata key "Total Labeling Time (hours)" is a frozen
# contract (export_writer) and is unaffected.

def load_labeling_time_seconds(path: str) -> float:
    if not os.path.exists(path):
        return 0.0
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        seconds = payload.get("Total Labeling Time (seconds)")
        if seconds is not None:
            return float(seconds)
        # Migration: older builds only wrote hours (and their loader ignored it).
        hours = payload.get("Total Labeling Time (hours)")
        if hours is not None:
            return float(hours) * 3600.0
        return 0.0
    except Exception as e:
        print(f"WARNING: Failed to load labeling time: {e}")
        return 0.0


class LabelingTimer:
    """Per-video labeling-time accumulator, persisted in the state DB's
    `meta.labeling_time_seconds` (was `state/<name>_metadata.json`)."""

    def __init__(self):
        self.total_s = 0.0
        self.session_start = None

    def start(self, repo: SqliteRepository) -> None:
        self.total_s = repo.load_labeling_time_seconds()
        self.session_start = time.monotonic()

    def current_s(self) -> float:
        total = float(self.total_s or 0.0)
        if self.session_start is None:
            return total
        return total + (time.monotonic() - self.session_start)

    def persist(self, repo: SqliteRepository) -> None:
        """Checkpoint the accumulator and keep the session running."""
        total = self.current_s()
        repo.save_labeling_time_seconds(total)
        self.total_s = total
        self.session_start = time.monotonic()

    def finalize(self, repo: SqliteRepository) -> None:
        """Checkpoint the accumulator and stop the session."""
        total = self.current_s()
        repo.save_labeling_time_seconds(total)
        self.total_s = total
        self.session_start = None

    def cancel_session(self) -> None:
        """Stop without persisting, discarding the current session's elapsed
        time. Used when a load aborts after the timer was already started
        (e.g. frame extraction failed) so a failed load doesn't leak an
        accumulating timer."""
        self.session_start = None


# === Video copy into the project videos folder ================================
def plan_video_copy(source_path: str, videos_dir: str = DEFAULT_VIDEOS_DIR):
    """Decide the copy target for a source video.

    Returns (dest_path, action, size_mismatch):
      action 'in_place'  — source already IS the project copy, nothing to do;
      action 'existing'  — a same-named file already exists, reuse it
                           (size_mismatch=True when sizes differ or are
                           unreadable status is unknown -> GUI warns);
      action 'copy'      — caller should copy source -> dest_path.
    """
    os.makedirs(videos_dir, exist_ok=True)
    dest_path = os.path.join(videos_dir, os.path.basename(source_path))

    if os.path.abspath(source_path) == os.path.abspath(dest_path):
        print(f"INFO: Video already inside project videos folder: {dest_path}")
        return dest_path, "in_place", False

    if os.path.exists(dest_path):
        size_mismatch = False
        try:
            size_mismatch = os.path.getsize(source_path) != os.path.getsize(dest_path)
        except Exception:
            print("WARN: Could not compare video sizes; using existing copy.")
        print(f"INFO: Video already exists in videos folder: {dest_path}")
        return dest_path, "existing", size_mismatch

    return dest_path, "copy", False


def copy_file_with_progress(src_path, dest_path, progress_cb, chunk_size=8 * 1024 * 1024):
    total_bytes = os.path.getsize(src_path)
    copied = 0
    start_time = time.time()

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(src_path, "rb") as src_file, open(dest_path, "wb") as dest_file:
        while True:
            chunk = src_file.read(chunk_size)
            if not chunk:
                break
            dest_file.write(chunk)
            copied += len(chunk)
            if progress_cb:
                progress_cb(copied, total_bytes, "Copying video", time.time() - start_time)
    shutil.copystat(src_path, dest_path, follow_symlinks=True)


# === Project open =============================================================
def prepare_project(raw_video_name: str, labeling_mode: str) -> ProjectPaths:
    """ProjectPaths for a raw video name (reliability-suffix rule applied)
    with all project directories created."""
    paths = ProjectPaths.for_video(
        raw_video_name, reliability=(labeling_mode == "Reliability")
    )
    # Pre-rename folders (<video>/data/) must become <video>/state/ BEFORE the
    # makedirs below would create an empty state/ next to them.
    migrate_project_dir(paths.video_dir)
    for d in (paths.state_dir, paths.frames_dir, paths.plots_dir, paths.export_dir):
        os.makedirs(d, exist_ok=True)
    return paths


def open_state(paths: ProjectPaths, *, fps=None, program_version=None,
               progress_cb=None) -> SqliteRepository:
    """Open this project's working-state DB and return the OPEN repository.

    On the first open of a pre-SQLite project this imports every legacy
    CSV/JSON state file into a fresh `state/<video>.db` and renames the sources
    `*.migrated` (`service_layer.state_migration`); afterwards it just opens the
    file. The caller owns closing it, and then calls `repo.load_frames()` — the
    two steps stay separate so the GUI can keep starting its labeling timer
    between them, exactly where it did before SQLite.
    """
    from service_layer import state_migration  # local: state_migration imports us

    repo, migration = state_migration.migrate_state_to_sqlite(
        paths, fps=fps, program_version=program_version, progress_cb=progress_cb
    )
    print(
        f"INFO: open_state: {paths.state_db} ready "
        f"(first-time migration={not migration.already_migrated})"
    )
    return repo


def load_frames_dataset(paths: ProjectPaths, progress_cb=None) -> Dict[int, FrameBundle]:
    """MIGRATION / RECOVERY path — no longer part of a normal project open.

    3-tier recovery ladder, tiers 1+2: unified CSV first; when that yields
    nothing and an export exists, recover once from the export. Called by
    `state_migration` on the first open of a pre-SQLite project (and available
    for manual disaster recovery)."""
    unified_path = paths.unified_csv
    export_path = paths.export_csv
    print(f"INFO: load_video: unified_path={unified_path}", flush=True)
    print(f"INFO: load_video: export_path={export_path}", flush=True)

    frames: Dict[int, FrameBundle] = {}
    try:
        print("INFO: load_video: loading unified dataset...", flush=True)
        t_unified = time.time()
        frames = load_unified_dataset(unified_path, progress_cb=progress_cb) or {}
        print(f"INFO: load_video: unified load done in {time.time() - t_unified:.1f}s "
              f"({len(frames)} frames)", flush=True)
    except Exception:
        print("ERROR: load_video: exception while loading unified dataset:", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        frames = {}

    # Fallback: if unified is empty but export exists, recover once from export.
    # We deliberately do NOT write the unified CSV here: on huge videos
    # (e.g. 300k+ frames) writing all rows blocks the UI thread for tens of
    # seconds. The next regular Save will materialize the unified file
    # naturally; until then we just keep the recovered dict in memory.
    if (not frames) and os.path.exists(export_path):
        print("INFO: Unified empty; importing from export for recovery…", flush=True)
        try:
            t_recover = time.time()
            frames = import_unified_from_export(export_path, progress_cb=progress_cb) or {}
            print(f"INFO: Recovery import returned in {time.time() - t_recover:.1f}s", flush=True)
        except Exception:
            print("ERROR: load_video: exception during import_unified_from_export:", flush=True)
            traceback.print_exc()
            sys.stdout.flush()
            frames = {}

        print(
            f"INFO: Recovery loaded {len(frames)} frames in memory "
            f"(unified CSV will be written on first Save).",
            flush=True,
        )
    return frames


def migrate_legacy_limb_csvs(frames: Dict[int, FrameBundle], paths: ProjectPaths) -> None:
    """MIGRATION / RECOVERY path — 3-tier recovery ladder, tier 3: merge legacy
    per-limb CSVs into the in-memory store (mutates `frames` in place). Only
    called when tiers 1+2 produced nothing."""
    print("INFO: No unified file found; attempting legacy CSV migration...")
    any_legacy = False
    for suffix in ['RH', 'LH', 'RL', 'LL']:
        csv_path = paths.limb_csv(suffix)
        if os.path.exists(csv_path):
            any_legacy = True
            d = csv_to_dict(csv_path)
            for fr, rec in d.items():
                b = frames.setdefault(fr, empty_bundle())
                b[suffix] = rec
    if any_legacy:
        print("INFO: Legacy limb CSVs merged into unified in-memory store.")
    else:
        print("INFO: Starting with an empty unified store.")


def frames_ready(paths: ProjectPaths, total_frames: int) -> bool:
    """True when the frames folder already holds the expected frame count."""
    return check_items_count(paths.frames_dir, total_frames)


def extract_frames(video_path: str, paths: ProjectPaths, labeling_mode: str,
                   progress_cb=None) -> None:
    """Extract frames for the video (raises FrameExtractionError on failure).
    Reliability mode copies the original project's frames when available."""
    create_frames(
        video_path,
        paths.frames_dir,
        labeling_mode,
        paths.video_name,
        progress_cb=progress_cb,
        original_frames_dir=paths.original.frames_dir,
    )


# === Last position ============================================================
def read_last_position(paths: ProjectPaths, total_frames: int) -> Optional[int]:
    """MIGRATION reader for the retired `state/<name>_last_position.json`.
    Clamped resume frame, or None. The live path is
    `SqliteRepository.read_last_position`."""
    path = paths.last_position_json
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        frame = int(payload.get("frame", 0))
        return max(0, min(total_frames, frame))
    except Exception as e:
        print(f"WARNING: Failed to restore last position: {e}")
        return None


# === Clothes ==================================================================
def rescale_clothes_points(points: list, stored_scale: Optional[float],
                           display_scale: float, default_scale: float) -> list:
    """Rescale dot coordinates from the scale they were STORED at to the
    current display scale.

    Shared by the DB path (`SqliteRepository.load_clothes_rows` +
    `clothes_diagram_scale`) and the legacy TXT reader below, so both keep the
    identical fallback chain: a missing scale means 0.5 (the historical default
    the sidecar was written at), and a non-positive one falls back to the
    display scale.
    """
    if stored_scale is None:
        stored_scale = 0.5
    if stored_scale <= 0:
        stored_scale = display_scale or default_scale
    display_scale = display_scale or default_scale
    scale_ratio = display_scale / stored_scale
    return [(x * scale_ratio, y * scale_ratio) for x, y in points]


def clothes_file_has_data(file_path: Optional[str]) -> bool:
    """MIGRATION reader: True when the legacy clothes sidecar exists and holds
    more than its header. The live check is `SqliteRepository.has_clothes`."""
    if not file_path or not os.path.exists(file_path):
        return False
    with open(file_path, 'r', encoding="utf-8") as f:
        return len(f.readlines()) > 1


def load_clothes_points(file_path: Optional[str], display_scale: float,
                        default_scale: float) -> list:
    """MIGRATION reader: dot coordinates from the legacy clothes sidecar,
    rescaled from the file's DiagramScale to the current display scale."""
    if not file_path or not os.path.exists(file_path):
        return []
    file_scale = None
    points = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.lower().startswith("diagramscale:"):
                try:
                    file_scale = float(line.split(":", 1)[1].strip())
                except ValueError:
                    file_scale = None
                continue
            if "X=" in line and "Y=" in line:
                match = re.search(r"X=([-\d.]+),\s*Y=([-\d.]+)", line)
                if match:
                    x = float(match.group(1))
                    y = float(match.group(2))
                    points.append((x, y))
    return rescale_clothes_points(points, file_scale, display_scale, default_scale)


def load_clothes_points_from_repo(repo, display_scale: float,
                                  default_scale: float) -> list:
    """Live path: clothes dot coordinates from the state DB, rescaled exactly
    as the legacy sidecar reader did."""
    points = [(row[1], row[2]) for row in repo.load_clothes_rows()]
    return rescale_clothes_points(
        points, repo.clothes_diagram_scale(), display_scale, default_scale
    )
