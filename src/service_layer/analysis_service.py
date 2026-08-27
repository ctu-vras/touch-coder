"""
service_layer/analysis_service.py
The Analysis use case, extracted from the old monolithic `src/analysis.py`.

Orchestration only — four ordered steps, no rules and no figures of its own:

    1. READ      adapters.export_reader.read_export_df(paths.export_csv)
    2. VALIDATE  + PARSE via domain.touch_stats.parse_export (raises
                 ExportSchemaError naming any missing column)
    3. COMPUTE   domain.touch_stats.summarize / transitions / transition_matrix
                 for all four limbs — ALL of it, before anything is written
    4. WRITE     adapters.plotting.* , returning the master HTML path

Step 3 finishing before step 4 starts is deliberate: the old code interleaved
computation and `write_html`, so a mid-run exception (fps 0 -> ZeroDivisionError)
left a half-populated `plots/` directory that looked like a successful run.

The browser is NOT opened here. `run_analysis` returns the master HTML path and
the GUI decides; `open_browser` exists only so a caller can inject
`webbrowser.open` (or a no-op in tests) if it wants the service to do it.

FRAME-RATE PROVENANCE (`resolve_frame_rate`): an explicit, usable `frame_rate`
argument wins as an override; otherwise the `"Frame Rate"` key of
`export/<name>_metadata.json` (written by adapters.export_writer) is used;
otherwise the run continues with NO frame rate and emits frame-based numbers
only. Whichever source won is logged.
"""

import json
import os
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from adapters import plotting
from adapters.export_reader import read_export_df
from adapters.zone_masks import list_zone_names
from domain.model import LIMBS
from domain.project import ProjectPaths
from domain.touch_stats import (
    Episode,
    ExportSchemaError,
    LimbStats,
    fps_is_usable,
    parse_export,
    summarize,
    transition_matrix,
    transition_zone_axis,
    transitions,
)

METADATA_FPS_KEY = "Frame Rate"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisResult:
    """What one Analysis run produced. `master_html` is the page to open."""

    master_html: str
    output_folder: str
    frame_rate: Optional[float]
    frame_rate_source: str
    total_frames: int
    stats: List[LimbStats]
    episodes: Dict[str, List[Episode]]
    written_files: List[str]
    warnings: List[str]


def _read_metadata_frame_rate(metadata_path: str):
    """`"Frame Rate"` from the export metadata sidecar, or None if unavailable.

    Every failure mode (absent file, unreadable JSON, missing key) is a WARN and
    returns None: analysis must never be blocked by its optional sidecar.
    """
    if not os.path.exists(metadata_path):
        logger.debug("no export metadata at %s; cannot recover frame rate", metadata_path)
        return None
    try:
        with open(metadata_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("could not read export metadata %s: %r", metadata_path, exc)
        return None
    if not isinstance(meta, dict) or METADATA_FPS_KEY not in meta:
        logger.warning("export metadata %s has no %r key", metadata_path, METADATA_FPS_KEY)
        return None
    return meta.get(METADATA_FPS_KEY)


def resolve_frame_rate(caller_frame_rate, metadata_path: str):
    """Pick the frame rate to analyse with; returns `(fps_or_None, source)`.

    Precedence: an explicitly passed usable value overrides the sidecar (the
    caller has the live probe); otherwise the sidecar value; otherwise None,
    which puts the whole run in frame-only mode instead of crashing. The GUI can
    legitimately pass 0.0 — `cv2.CAP_PROP_FPS` reports 0 for some containers —
    so `0`/negative/None all count as "not provided".
    """
    meta_value = _read_metadata_frame_rate(metadata_path)

    if fps_is_usable(caller_frame_rate):
        fps = float(caller_frame_rate)
        if fps_is_usable(meta_value) and float(meta_value) != fps:
            logger.info("frame rate %s from caller overrides export metadata %s", fps, meta_value)
        else:
            logger.info("frame rate %s from caller", fps)
        return fps, "caller"

    if fps_is_usable(meta_value):
        fps = float(meta_value)
        logger.info(
            "frame rate %s from export metadata (caller passed unusable %r)",
            fps,
            caller_frame_rate,
        )
        return fps, "metadata"

    logger.warning(
        "no usable frame rate (caller=%r, metadata=%r); seconds-based results will be omitted",
        caller_frame_rate,
        meta_value,
    )
    return None, "unavailable"


def run_analysis(paths: ProjectPaths,
                 frame_rate=None,
                 new_template: bool = False,
                 output_folder: Optional[str] = None,
                 limbs: Sequence[str] = LIMBS,
                 open_browser: Optional[Callable[[str], object]] = None) -> AnalysisResult:
    """Run the full analysis for one project and return an `AnalysisResult`.

    `paths` supplies the export CSV, the metadata sidecar and the default output
    directory (`plots/`); pass `output_folder` to write elsewhere (tests do).
    `new_template` and the derived zone list are INPUTS — this service never
    reads config.json, the GUI passes its config snapshot down.

    Raises `adapters.export_reader.ExportReadError` when the CSV is unparseable
    and `domain.touch_stats.ExportSchemaError` when required columns are absent.
    Both are loud on purpose: the GUI shows the message in a dialog. Nothing is
    written before validation succeeds.
    """
    name = paths.video_name
    export_path = paths.export_csv
    output_folder = output_folder or paths.plots_dir
    warnings: List[str] = []

    logger.info("analysis starting for %r: export=%s output=%s", name, export_path, output_folder)

    # 1) READ + 2) VALIDATE/PARSE — before creating the output directory, so a
    #    rejected export leaves no empty plots/ behind.
    df = read_export_df(export_path)
    data = parse_export_data(df, limbs)

    fps, fps_source = resolve_frame_rate(frame_rate, paths.export_metadata)
    if fps is None:
        warnings.append(
            "Frame rate unavailable — seconds-based results are omitted and the "
            "duration histogram is shown in frames."
        )
    if data.missing_optional_columns:
        message = (
            "Export has no "
            + ", ".join(data.missing_optional_columns)
            + " column(s); the touch-trajectory plot will have no points."
        )
        logger.warning("%s", message)
        warnings.append(message)

    # 3) COMPUTE everything first (see module docstring).
    stats: List[LimbStats] = []
    matrices = []
    zones_default = list_zone_names(new_template)
    for limb in limbs:
        episodes = data.episodes.get(limb, [])
        limb_stats = summarize(episodes, fps=fps, total_frames=data.total_frames, limb=limb)
        stats.append(limb_stats)
        counts = transitions(episodes)
        zones = transition_zone_axis(counts, limb_stats.zone_touch_count, zones_default)
        matrices.append((transition_matrix(counts, zones), zones))
        _log_limb(limb, limb_stats, data.total_frames)

    open_total = sum(s.open_touches for s in stats)
    if open_total:
        detail = ", ".join(
            f"{s.limb}@{list(s.open_start_frames)}" for s in stats if s.open_touches
        )
        message = (
            f"{open_total} open/unterminated touch(es) found ({detail}); reported "
            "separately as censored and excluded from durations, means, "
            "percentages, rates, transitions and histograms."
        )
        logger.warning("%s", message)
        warnings.append(message)

    # 4) WRITE
    os.makedirs(output_folder, exist_ok=True)
    written: List[str] = []
    for limb, (matrix, zones) in zip(limbs, matrices):
        written.append(plotting.write_transition_heatmap(matrix, zones, limb, output_folder))
    written.append(
        plotting.write_trajectory_plot(
            data.episodes, plotting.limb_image_paths(new_template), output_folder
        )
    )
    plotting.write_analysis_tables(stats, data.total_frames, fps, output_folder)
    written.extend(
        [
            os.path.join(output_folder, plotting.FRAMES_TABLE_CSV),
            os.path.join(output_folder, plotting.SECONDS_TABLE_CSV),
        ]
    )
    written.append(plotting.write_summary_table(stats, data.total_frames, fps, output_folder))
    written.append(plotting.write_onset_histogram(stats, output_folder))
    written.append(plotting.write_duration_histogram(stats, fps, output_folder))
    master_html = plotting.write_master_html(name, output_folder, limbs, notes=warnings)
    written.append(master_html)

    logger.info(
        "analysis complete for %r: %s file(s) in %s (frames=%s, fps=%r via %s)",
        name, len(written), output_folder, data.total_frames, fps, fps_source,
    )

    if open_browser is not None:
        open_browser(master_html)

    return AnalysisResult(
        master_html=master_html,
        output_folder=output_folder,
        frame_rate=fps,
        frame_rate_source=fps_source,
        total_frames=data.total_frames,
        stats=stats,
        episodes=data.episodes,
        written_files=written,
        warnings=warnings,
    )


def parse_export_data(df, limbs: Sequence[str] = LIMBS):
    """Thin wrapper over `domain.touch_stats.parse_export` that logs the schema
    verdict (the domain layer stays free of orchestration concerns)."""
    try:
        data = parse_export(df, limbs)
    except ExportSchemaError as exc:
        logger.error("export schema rejected: %s", exc)
        raise
    episode_counts = {limb: len(eps) for limb, eps in data.episodes.items()}
    logger.debug(
        "export schema OK: %s row(s), total_frames=%s, episodes=%s",
        data.row_count,
        data.total_frames,
        episode_counts,
    )
    return data


def _log_limb(limb: str, stats: LimbStats, total_frames: int) -> None:
    logger.debug(
        "%s: closed=%s open=%s total=%sf (%.4f%% of %sf) mean=%s stdev=%s rate/min=%s",
        limb, stats.total_touches, stats.open_touches,
        stats.total_duration_frames, stats.percentage_touching, total_frames,
        stats.mean_duration_frames, stats.stdev_duration_frames,
        stats.touch_rate_per_minute,
    )
