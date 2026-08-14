"""
service_layer/save_service.py
The save Unit of Work, extracted from LabelingApp.save_data. No Tk here:
the GUI orchestrates the modal progress dialog + worker thread and calls
these steps in order:

    1. persist_unified(...)        — changed-only journal append (UI thread)
    2. snapshot = build_save_snapshot(frames)
    3. run_export(snapshot, ...)   — metadata sidecar + full export CSV
                                     (the GUI runs THIS on its worker thread)
    4. clear_clean_flags(frames, snapshot)

Concurrency invariant (pinned by tests): the snapshot is a deep copy taken
BEFORE the worker-thread export, and afterwards `Changed` is cleared ONLY
for bundles still equal to their snapshot (`b == snapshot.get(f)`), so a
frame edited DURING the export stays dirty and reaches the next save.
"""

import copy
import os
from dataclasses import dataclass
from typing import Dict, Optional

from adapters.export_writer import export_from_unified, write_export_metadata
from adapters.unified_repo import extract_zones_from_file, save_unified_dataset
from domain.model import FrameBundle
from domain.project import ProjectPaths


@dataclass(frozen=True)
class MetadataInputs:
    """Non-tabular export inputs, gathered on the UI thread (button labels are
    Tk reads; labeling time is clocked before the export starts)."""
    program_version: object
    video_name: str
    labeling_mode: str
    clothes_list: Optional[list]
    param_labels: Optional[dict]
    limb_param_labels: Optional[dict]
    labeling_time_seconds: Optional[float]


def load_clothes_zones(clothes_path: Optional[str]):
    """Clothes-zone list for the export metadata, or None when no file path."""
    return extract_zones_from_file(clothes_path) if clothes_path else None


def build_save_snapshot(frames: Dict[int, FrameBundle]) -> Dict[int, FrameBundle]:
    """Deep-copy the live frames dict BEFORE the worker-thread export so
    concurrent edits can't tear the export nor be wrongly marked clean."""
    return copy.deepcopy(frames)


def persist_unified(unified_path: str, total_frames: int, frames: Dict[int, FrameBundle]) -> None:
    """Changed-only journal append to the unified CSV (source of truth)."""
    os.makedirs(os.path.dirname(unified_path), exist_ok=True)
    save_unified_dataset(unified_path, total_frames, frames)


def run_export(snapshot: Dict[int, FrameBundle],
               paths: ProjectPaths,
               fps,
               metadata: MetadataInputs,
               total_frames: int) -> None:
    """Full export: JSON metadata sidecar + legacy export CSV, both written
    from the immutable snapshot. Runs on the GUI's worker thread."""
    os.makedirs(paths.export_dir, exist_ok=True)
    write_export_metadata(
        meta_path=paths.export_metadata,
        program_version=metadata.program_version,
        video_name=metadata.video_name,
        labeling_mode=metadata.labeling_mode,
        frame_rate=fps,
        clothes_list=metadata.clothes_list,
        param_labels=metadata.param_labels,
        limb_param_labels=metadata.limb_param_labels,
        labeling_time_seconds=metadata.labeling_time_seconds,
    )
    export_from_unified(
        snapshot,
        paths.export_csv,
        metadata.program_version,
        metadata.video_name,
        metadata.labeling_mode,
        fps,
        metadata.clothes_list,
        total_frames=total_frames,
        param_labels=metadata.param_labels,
        limb_param_labels=metadata.limb_param_labels,
    )


def clear_clean_flags(frames: Dict[int, FrameBundle],
                      snapshot: Dict[int, FrameBundle]) -> None:
    """Clear `Changed` ONLY for bundles still identical to their exported
    snapshot — a frame edited during the export stays dirty."""
    for f, b in frames.items():
        if (
            isinstance(b, dict)
            and b.get("Changed")
            and b == snapshot.get(f)
        ):
            b["Changed"] = False
    print("DEBUG: Cleared bundle 'Changed' flags after save.")
