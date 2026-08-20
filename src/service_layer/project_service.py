"""
service_layer/project_service.py
Non-Tk logic of opening a labeled-video project, extracted from
LabelingApp.load_video, plus the per-video labeling-time accumulator.

The GUI keeps the mode dialog, file picker, progress windows and thread
starting, and calls these functions preserving the audited ordering:

    state DB open -> timer start -> frame load
    -> frame extraction (an extraction abort rolls back the timer)
    -> position restore -> buffer-thread start -> Changed-flag reset

Progress reporting is injected as a plain callback
(progress_cb(count, total, stage, elapsed_s)).

Persistence note: working state lives in ONE SQLite file per video
(`adapters.sqlite_repo`, opened by `open_state`) and that is the ONLY state
format this build reads or writes. The legacy CSV/JSON readers and the
one-time import that fed them into SQLite were removed in 9.0; a project
created by 8.0.x or earlier is NOT converted (see ARCHITECTURE.md).
"""

import os
import shutil
import time
from typing import Optional

from adapters.frame_extractor import check_items_count, create_frames
from adapters.sqlite_repo import (
    META_CREATED_BY_VERSION,
    META_FPS,
    META_VIDEO_NAME,
    SqliteRepository,
)
from domain.project import VIDEOS_DIR, ProjectPaths

DEFAULT_VIDEOS_DIR = VIDEOS_DIR


# === Labeling-time accumulator ================================================
#
# The accumulator lives in the state DB (`meta.labeling_time_seconds`), stored
# in SECONDS. The EXPORT metadata key "Total Labeling Time (hours)" is a frozen
# contract (export_writer) and is written from these seconds at export time.

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
    for d in (paths.state_dir, paths.frames_dir, paths.plots_dir, paths.export_dir):
        os.makedirs(d, exist_ok=True)
    return paths


def open_state(paths: ProjectPaths, *, fps=None,
               program_version=None) -> SqliteRepository:
    """Open (creating when absent) this project's working-state DB and return
    the OPEN repository.

    `SqliteRepository.__init__` builds the schema when the file is new, so this
    is the create path for a brand-new project as much as the open path for an
    existing one. The provenance meta is re-stamped on every open: it is cheap
    and it keeps an orphaned DB self-describing.

    The caller owns closing it, and then calls `repo.load_frames()` — the two
    steps stay separate so the GUI can start its labeling timer between them.
    """
    repo = SqliteRepository(paths.state_db)
    meta = {META_VIDEO_NAME: paths.video_name}
    if fps is not None:
        meta[META_FPS] = fps
    if program_version is not None:
        meta[META_CREATED_BY_VERSION] = program_version
    repo.set_meta_many(meta)
    print(f"INFO: open_state: {paths.state_db} ready")
    return repo


def frames_ready(paths: ProjectPaths, total_frames: int) -> bool:
    """True when the frames folder already holds the expected frame count."""
    return check_items_count(paths.frames_dir, total_frames)


def extract_frames(video_path: str, paths: ProjectPaths, labeling_mode: str,
                   progress_cb=None, cancel_event=None) -> None:
    """Extract frames for the video (raises FrameExtractionError on failure).
    Reliability mode copies the original project's frames when available."""
    create_frames(
        video_path,
        paths.frames_dir,
        labeling_mode,
        paths.video_name,
        progress_cb=progress_cb,
        original_frames_dir=paths.original.frames_dir,
        cancel_event=cancel_event,
    )


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


def load_clothes_points_from_repo(repo, display_scale: float,
                                  default_scale: float) -> list:
    """Live path: clothes dot coordinates from the state DB, rescaled exactly
    as the legacy sidecar reader did."""
    points = [(row[1], row[2]) for row in repo.load_clothes_rows()]
    return rescale_clothes_points(
        points, repo.clothes_diagram_scale(), display_scale, default_scale
    )
