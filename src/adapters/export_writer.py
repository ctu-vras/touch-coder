"""
adapters/export_writer.py
The frozen legacy export contract: `export/<video>_export.csv` plus its JSON
metadata sidecar. Moved VERBATIM from data_utils.py — the byte-level golden
master tests (tests/unit/test_export_golden_master.py) pin this file's output;
any encoding drift silently corrupts published research datasets.
"""

import json
import logging
import pandas as pd
from typing import Dict

from adapters.atomic_io import atomic_write
from domain.model import FrameBundle, _normalize_param_state, empty_bundle


logger = logging.getLogger(__name__)


def write_export_metadata(meta_path: str,
                          program_version,
                          video_name,
                          labeling_mode,
                          frame_rate,
                          clothes_list,
                          param_labels: dict | None = None,
                          limb_param_labels: dict | None = None,
                          labeling_time_seconds: float | None = None) -> None:
    """
    Writes a JSON sidecar with all non-tabular export metadata that used to be
    stuffed into the first 5 lines of *_export.csv.
    """
    meta = {
        "Program Version": program_version,
        "Video Name": video_name,
        "Labeling Mode": labeling_mode,
        "Frame Rate": frame_rate,
        "Zones Covered With Clothes": clothes_list,
        "Param Labels": param_labels or {},
        "Limb Param Labels": limb_param_labels or {},
    }
    if labeling_time_seconds is not None:
        meta["Total Labeling Time (hours)"] = round(float(labeling_time_seconds) / 3600.0, 4)
    atomic_write(meta_path, lambda f: json.dump(meta, f, indent=2, ensure_ascii=False))


def export_from_unified(frames: Dict[int, FrameBundle],
                        out_csv: str,
                        program_version: float,
                        video_name: str,
                        labeling_mode: str,
                        frame_rate: float,
                        clothes_list,
                        total_frames: int,
                        param_labels: Dict[str, str] | None = None,
                        limb_param_labels: Dict[str, str] | None = None) -> None:
    """
    Emit the legacy *_export.csv with EXACT schema/order and a row for EVERY frame 0..total_frames.
    - Global Params: Par1..Par3 → Parameter_1..3
    - Limb Params:  Par1..Par3 → {LH,LL,RH,RL}_Parameter_1..3
    """
    rows = []

    def _xy_str(lst):
        return ",".join(map(str, lst)) if lst else ""

    for f in range(total_frames + 1):
        b = frames.get(f, empty_bundle())
        # 0-FPS probe (some containers) must not abort the export.
        row = {"Frame": f, "Time_ms": (f / frame_rate) * 1000.0 if frame_rate else 0.0}

        # Limb blocks in order: LH, LL, RH, RL
        for limb in ["LH", "LL", "RH", "RL"]:
            rec = b.get(limb, {}) if isinstance(b, dict) else {}
            row[f"{limb}_X"] = _xy_str(rec.get("X", []))
            row[f"{limb}_Y"] = _xy_str(rec.get("Y", []))
            row[f"{limb}_Onset"] = rec.get("Onset", "")
            row[f"{limb}_Zones"] = json.dumps(rec.get("Zones", []) or [])

        # Global params (canonical keys → fixed columns)
        params = (b.get("Params") or {}) if isinstance(b, dict) else {}
        for i in (1, 2, 3):
            val = params.get(f"Par{i}")
            row[f"Parameter_{i}"] = "" if (val is None or val == "") else val

        # Limb-specific params (canonical keys → fixed columns)
        for limb in ["LH", "LL", "RH", "RL"]:
            rec = b.get(limb, {}) if isinstance(b, dict) else {}
            lp = rec.get("LimbParams", {}) if isinstance(rec, dict) else {}
            for i in (1, 2, 3):
                val = _normalize_param_state(lp.get(f"Par{i}"))
                row[f"{limb}_Parameter_{i}"] = "" if (val is None or val == "") else val

        row["Note"] = b.get("Note", "") if isinstance(b, dict) else ""
        rows.append(row)

    # Exact legacy column order
    cols = ["Frame", "Time_ms"]
    for limb in ["LH", "LL", "RH", "RL"]:
        cols += [f"{limb}_X", f"{limb}_Y", f"{limb}_Onset", f"{limb}_Zones"]
    cols += ["Parameter_1", "Parameter_2", "Parameter_3"]
    for limb in ["LH", "LL", "RH", "RL"]:
        cols += [f"{limb}_Parameter_1", f"{limb}_Parameter_2", f"{limb}_Parameter_3"]
    cols += ["Note"]

    df = pd.DataFrame(rows, columns=cols)
    atomic_write(out_csv, lambda f: df.to_csv(f, index=False))

    # CSV remains clean (no preamble). Metadata is written separately by caller.
    logger.debug("Export -> %s (rows=%d)", out_csv, len(rows))
