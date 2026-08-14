"""
service_layer/project_service.py
Non-Tk logic of opening a labeled-video project, extracted from
LabelingApp.load_video, plus the per-video labeling-time accumulator.

The GUI keeps the mode dialog, file picker, progress windows and thread
starting, and calls these functions preserving the audited ordering:

    timer start -> unified load -> export recovery -> legacy migration
    -> frame extraction (an extraction abort rolls back the timer)
    -> position restore -> buffer-thread start -> Changed-flag reset

Progress reporting is injected as a plain callback
(progress_cb(count, total, stage, elapsed_s)).
"""

import json
import os
import re
import shutil
import sys
import time
import traceback
from typing import Dict, Optional

from adapters.atomic_io import atomic_write
from adapters.frame_extractor import check_items_count, create_frames
from adapters.unified_repo import (
    csv_to_dict,
    import_unified_from_export,
    load_notes_csv,
    load_unified_dataset,
)
from domain.model import FrameBundle, empty_bundle
from domain.project import VIDEOS_DIR, ProjectPaths
from service_layer.migration_service import migrate_project_dir

DEFAULT_VIDEOS_DIR = VIDEOS_DIR


# === Labeling-time accumulator (state/<name>_metadata.json) ===================
#
# BUG FIX (silent reset on restart): the writer used to store
# "Total Labeling Time (hours)" while the loader read
# "Total Labeling Time (seconds)", so the accumulator restarted from 0 on
# every app launch. The state file now stores SECONDS under
# "Total Labeling Time (seconds)"; loading falls back to
# "Total Labeling Time (hours)" * 3600 to migrate files written by the buggy
# versions. The EXPORT metadata key "Total Labeling Time (hours)" is a frozen
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


def write_labeling_time_seconds(path: str, total_seconds: float) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "Total Labeling Time (seconds)": round(float(total_seconds), 3),
    }
    try:
        atomic_write(path, lambda f: json.dump(payload, f, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"WARNING: Failed to save labeling time: {e}")


class LabelingTimer:
    """Per-video labeling-time accumulator (state lived on LabelingApp as
    _video_time_total_s / _video_session_start)."""

    def __init__(self):
        self.total_s = 0.0
        self.session_start = None

    def start(self, state_path: str) -> None:
        self.total_s = load_labeling_time_seconds(state_path)
        self.session_start = time.monotonic()

    def current_s(self) -> float:
        total = float(self.total_s or 0.0)
        if self.session_start is None:
            return total
        return total + (time.monotonic() - self.session_start)

    def persist(self, state_path: str) -> None:
        """Checkpoint the accumulator and keep the session running."""
        total = self.current_s()
        write_labeling_time_seconds(state_path, total)
        self.total_s = total
        self.session_start = time.monotonic()

    def finalize(self, state_path: str) -> None:
        """Checkpoint the accumulator and stop the session."""
        total = self.current_s()
        write_labeling_time_seconds(state_path, total)
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


def load_frames_dataset(paths: ProjectPaths, progress_cb=None) -> Dict[int, FrameBundle]:
    """3-tier recovery ladder, tiers 1+2: unified CSV first; when that yields
    nothing and an export exists, recover once from the export (in memory
    only — the next Save materializes the unified file)."""
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
    """3-tier recovery ladder, tier 3: merge legacy per-limb CSVs into the
    in-memory unified store (mutates `frames` in place). Only called when
    tiers 1+2 produced nothing."""
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


def load_notes(notes_path: str) -> dict:
    if os.path.exists(notes_path):
        notes = load_notes_csv(notes_path)
        print("INFO: Notes loaded successfully.")
        return notes
    return {}


# === Last position ============================================================
def write_last_position(paths: ProjectPaths, frame: int, total_frames: int) -> None:
    os.makedirs(paths.state_dir, exist_ok=True)
    path = paths.last_position_json
    try:
        payload = {
            "frame": int(frame),
            "total_frames": int(total_frames),
        }
        atomic_write(path, lambda f: json.dump(payload, f))
        print(f"INFO: Saved last position at {path}")
    except Exception as e:
        print(f"WARNING: Failed to save last position: {e}")


def read_last_position(paths: ProjectPaths, total_frames: int) -> Optional[int]:
    """Clamped resume frame from the last-position sidecar, or None."""
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


# === Clothes sidecar (read side) ==============================================
def clothes_file_has_data(file_path: Optional[str]) -> bool:
    """True when the clothes sidecar exists and holds more than its header."""
    if not file_path or not os.path.exists(file_path):
        return False
    with open(file_path, 'r', encoding="utf-8") as f:
        return len(f.readlines()) > 1


def load_clothes_points(file_path: Optional[str], display_scale: float,
                        default_scale: float) -> list:
    """Parse dot coordinates from the clothes sidecar, rescaled from the
    file's DiagramScale to the current display scale."""
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
    if file_scale is None:
        file_scale = 0.5
    if file_scale <= 0:
        file_scale = display_scale or default_scale
    display_scale = display_scale or default_scale
    scale_ratio = display_scale / file_scale
    return [(x * scale_ratio, y * scale_ratio) for x, y in points]
