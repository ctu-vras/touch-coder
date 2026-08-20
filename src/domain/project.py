"""
domain/project.py
Canonical on-disk layout of one labeled-video project. Every path under
`data/<video_name>/` is derived here — no other module may hand-build these
strings (ARCHITECTURE.md "Data Layout on Disk" is the reference).

Layout:

    data/<video_name>/
    ├── state/      working state: <video_name>.db (SQLite, source of truth)
    ├── export/     publication-ready CSV + metadata JSON
    ├── frames/     frame0.jpg … frameN.jpg
    └── plots/      Plotly HTML from Analysis

This is the only layout the app knows. The pre-9.0 names (`Labeled_data/`,
`<name>/data/`, `Videos/`) and the retired CSV/JSON state sidecars are not
recognized any more — the migration that converted them was removed in 9.0.
"""

import os
from dataclasses import dataclass

DATA_DIR = "data"
STATE_SUBDIR = "state"
EXPORT_SUBDIR = "export"
FRAMES_SUBDIR = "frames"
PLOTS_SUBDIR = "plots"
VIDEOS_DIR = "videos"
RELIABILITY_SUFFIX = "_reliability"


@dataclass(frozen=True)
class ProjectPaths:
    """All paths for one labeled video under `<base_dir>/<video_name>/`.

    `video_name` is the FULL project folder name, including any
    `_reliability` suffix. Use `for_video()` to apply the suffix rule from a
    raw video name + labeling mode.
    """

    video_name: str
    base_dir: str = DATA_DIR

    # --- construction -------------------------------------------------------
    @classmethod
    def for_video(cls, video_name: str, reliability: bool = False,
                  base_dir: str = DATA_DIR) -> "ProjectPaths":
        """Build paths from a raw video name, applying the reliability rule:
        Reliability mode appends `_reliability` to the project folder name."""
        if reliability and not video_name.endswith(RELIABILITY_SUFFIX):
            video_name = video_name + RELIABILITY_SUFFIX
        return cls(video_name=video_name, base_dir=base_dir)

    @property
    def is_reliability(self) -> bool:
        return self.video_name.endswith(RELIABILITY_SUFFIX)

    @property
    def original(self) -> "ProjectPaths":
        """The non-reliability twin of this project (Reliability mode copies
        its frames from the original instead of re-extracting)."""
        if not self.is_reliability:
            return self
        return ProjectPaths(self.video_name[: -len(RELIABILITY_SUFFIX)], self.base_dir)

    # --- directories ---------------------------------------------------------
    @property
    def video_dir(self) -> str:
        return os.path.join(self.base_dir, self.video_name)

    @property
    def state_dir(self) -> str:
        """Working state (was `<video>/data/` before the layout rename)."""
        return os.path.join(self.video_dir, STATE_SUBDIR)

    @property
    def export_dir(self) -> str:
        return os.path.join(self.video_dir, EXPORT_SUBDIR)

    @property
    def frames_dir(self) -> str:
        return os.path.join(self.video_dir, FRAMES_SUBDIR)

    @property
    def plots_dir(self) -> str:
        return os.path.join(self.video_dir, PLOTS_SUBDIR)

    # --- files ----------------------------------------------------------------
    @property
    def state_db(self) -> str:
        """The working-state SQLite database — the source of truth.

        Everything the app once spread over `<name>_unified.csv`,
        `<name>_notes.csv`, `<name>_limb_parameters.csv`,
        `<name>_last_position.json`, `<name>_metadata.json` and
        `<name>_clothes.txt` lives in this one file
        (`adapters.sqlite_repo.SqliteRepository`). Those names have no
        properties here any more: nothing reads or writes them.
        """
        return os.path.join(self.state_dir, f"{self.video_name}.db")

    @property
    def export_csv(self) -> str:
        return os.path.join(self.export_dir, f"{self.video_name}_export.csv")

    @property
    def export_metadata(self) -> str:
        return os.path.join(self.export_dir, f"{self.video_name}_metadata.json")
