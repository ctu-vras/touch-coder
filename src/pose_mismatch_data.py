import json
import io
import os
import time
from typing import Dict, Optional

import pandas as pd

from atomic_io import atomic_write, durable_append


POSE_JOINTS = [
    "L_ANKLE",
    "R_ANKLE",
    "L_KNEE",
    "R_KNEE",
    "L_HIP",
    "R_HIP",
    "L_WRIST",
    "R_WRIST",
    "L_ELBOW",
    "R_ELBOW",
    "L_SHOULDER",
    "R_SHOULDER",
    "NECK",
]

POSE_UNIFIED_COLUMNS = [
    "Frame",
    "Note",
    "Params",
    "ScaleRaw",
    "ScaleFactor",
    "ScaleSet",
    "HeadScaleRaw",
    "HeadScaleFactor",
    "HeadScaleSet",
    "Joints",
]
UNIFIED_COMPACT_FACTOR = 2


def empty_pose_joint_map() -> Dict[str, dict]:
    return {joint: {"Event": None, "X": None, "Y": None, "Opacity": 1.0} for joint in POSE_JOINTS}


def empty_pose_bundle() -> dict:
    return {
        "Note": None,
        "Params": {},
        "ScaleRaw": 1.0,
        "ScaleFactor": 1.0,
        "ScaleSet": False,
        "ScaleAutoCarry": False,
        "HeadScaleRaw": 1.0,
        "HeadScaleFactor": 1.0,
        "HeadScaleSet": False,
        "HeadScaleAutoCarry": False,
        "Joints": empty_pose_joint_map(),
    }


def scale_raw_to_factor(scale_raw: float) -> float:
    try:
        value = float(scale_raw)
        if value != value:  # NaN guard: min/max would leak the upper bound.
            value = 1.0
    except Exception:
        value = 1.0
    return max(0.7, min(1.3, value))


def ensure_pose_bundle(bundle: Optional[dict]) -> dict:
    if not isinstance(bundle, dict):
        bundle = empty_pose_bundle()
    if not isinstance(bundle.get("Params"), dict):
        bundle["Params"] = {}
    joints = bundle.get("Joints")
    if not isinstance(joints, dict):
        joints = empty_pose_joint_map()
        bundle["Joints"] = joints
    for joint in POSE_JOINTS:
        rec = joints.get(joint)
        if not isinstance(rec, dict):
            joints[joint] = {"Event": None, "X": None, "Y": None, "Opacity": 1.0}
        else:
            if "Event" not in rec:
                rec["Event"] = None
            if "X" not in rec:
                rec["X"] = None
            if "Y" not in rec:
                rec["Y"] = None
            try:
                op = float(rec.get("Opacity", 1.0))
                if op != op:  # NaN guard
                    op = 1.0
                rec["Opacity"] = max(0.0, min(1.0, op))
            except (TypeError, ValueError):
                rec["Opacity"] = 1.0
    if "ScaleRaw" not in bundle:
        bundle["ScaleRaw"] = 1.0
    if "ScaleFactor" not in bundle:
        bundle["ScaleFactor"] = scale_raw_to_factor(bundle["ScaleRaw"])
    for raw_key, factor_key in (
        ("ScaleRaw", "ScaleFactor"),
        ("HeadScaleRaw", "HeadScaleFactor"),
    ):
        if raw_key not in bundle:
            bundle[raw_key] = 1.0
        old_raw = bundle[raw_key]
        raw_invalid = False
        try:
            raw_value = float(old_raw)
            if raw_value != raw_value:
                raw_invalid = True
                raw_value = 1.0
        except Exception:
            raw_invalid = True
            raw_value = 1.0
        sanitized_raw = scale_raw_to_factor(raw_value)
        if raw_invalid or sanitized_raw != raw_value:
            print(
                "WARNING: pose scale out of range/NaN on load: "
                f"{raw_key}={old_raw!r} -> {sanitized_raw}"
            )
        bundle[raw_key] = sanitized_raw

        factor_was_present = factor_key in bundle
        if not factor_was_present:
            bundle[factor_key] = scale_raw_to_factor(sanitized_raw)
        old_factor = bundle[factor_key]
        factor_invalid = False
        try:
            factor_value = float(old_factor)
            if factor_value != factor_value:
                factor_invalid = True
                factor_value = scale_raw_to_factor(sanitized_raw)
        except Exception:
            factor_invalid = True
            factor_value = scale_raw_to_factor(sanitized_raw)
        sanitized_factor = scale_raw_to_factor(factor_value)
        if factor_was_present and (factor_invalid or sanitized_factor != factor_value):
            print(
                "WARNING: pose scale out of range/NaN on load: "
                f"{factor_key}={old_factor!r} -> {sanitized_factor}"
            )
        bundle[factor_key] = sanitized_factor
    if "ScaleSet" not in bundle:
        bundle["ScaleSet"] = bundle.get("ScaleRaw", 1.0) != 1.0
    if "ScaleAutoCarry" not in bundle:
        bundle["ScaleAutoCarry"] = False
    if "HeadScaleSet" not in bundle:
        bundle["HeadScaleSet"] = bundle.get("HeadScaleRaw", 1.0) != 1.0
    if "HeadScaleAutoCarry" not in bundle:
        bundle["HeadScaleAutoCarry"] = False
    if "Note" not in bundle:
        bundle["Note"] = None
    return bundle


def _pose_unified_row(frame: int, bundle: dict) -> dict:
    bundle = ensure_pose_bundle(bundle)
    return {
        "Frame": frame,
        "Note": bundle.get("Note"),
        "Params": json.dumps(bundle.get("Params") or {}),
        "ScaleRaw": bundle.get("ScaleRaw", 0.0),
        "ScaleFactor": bundle.get(
            "ScaleFactor", scale_raw_to_factor(bundle.get("ScaleRaw", 0.0))
        ),
        "ScaleSet": bool(bundle.get("ScaleSet", False)),
        "HeadScaleRaw": bundle.get("HeadScaleRaw", 1.0),
        "HeadScaleFactor": bundle.get(
            "HeadScaleFactor", scale_raw_to_factor(bundle.get("HeadScaleRaw", 1.0))
        ),
        "HeadScaleSet": bool(bundle.get("HeadScaleSet", False)),
        "Joints": json.dumps(bundle.get("Joints") or {}),
    }


def _read_pose_journal(csv_path: str):
    try:
        return pd.read_csv(csv_path), False
    except pd.errors.ParserError:
        with open(csv_path, "rb") as f:
            data = f.read()
        if data.endswith(b"\n"):
            raise
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            raise
        print(
            f"WARNING: Ignoring crash-torn final 3D unified row and repairing journal → {csv_path}",
            flush=True,
        )
        return pd.read_csv(io.BytesIO(data[: last_newline + 1])), True


def _compact_pose_dataset(
    csv_path: str,
    frames: Dict[int, dict],
    rows_on_disk: int,
    *,
    force: bool = False,
) -> None:
    distinct_frames = len(frames)
    if not distinct_frames or (
        not force and rows_on_disk <= distinct_frames * UNIFIED_COMPACT_FACTOR
    ):
        return
    rows = [_pose_unified_row(frame, frames[frame]) for frame in sorted(frames)]
    df = pd.DataFrame(rows, columns=POSE_UNIFIED_COLUMNS)
    atomic_write(csv_path, lambda f: df.to_csv(f, index=False), keep_backup=True)
    print(
        f"INFO: 3D unified compacted {rows_on_disk} journal rows to "
        f"{distinct_frames} distinct frames → {csv_path}"
    )


def load_pose_dataset(csv_path: str) -> Dict[int, dict]:
    frames: Dict[int, dict] = {}
    if not (csv_path and os.path.exists(csv_path)):
        return frames
    try:
        if os.path.getsize(csv_path) == 0:
            return frames
        df, recovered_torn_tail = _read_pose_journal(csv_path)
    except Exception as e:
        print(f"ERROR: Failed to read 3D unified CSV: {e}")
        return frames

    col_idx = {c: i for i, c in enumerate(df.columns)}
    if "Frame" not in col_idx:
        print("ERROR: load_pose_dataset: 'Frame' column missing — aborting", flush=True)
        return frames

    iter_start = time.perf_counter()
    for row in df.itertuples(index=False, name=None):
        def _get(name, default=None):
            i = col_idx.get(name, -1)
            return default if i < 0 else row[i]

        try:
            frame = int(_get("Frame"))
        except Exception:
            continue
        bundle = empty_pose_bundle()
        note = _get("Note")
        bundle["Note"] = None if pd.isna(note) else str(note)
        try:
            bundle["Params"] = json.loads(_get("Params") or "{}")
        except Exception:
            bundle["Params"] = {}
        try:
            bundle["ScaleRaw"] = float(_get("ScaleRaw", 1.0) or 1.0)
        except Exception:
            bundle["ScaleRaw"] = 1.0
        try:
            bundle["ScaleFactor"] = float(_get("ScaleFactor", scale_raw_to_factor(bundle["ScaleRaw"])) or 1.0)
        except Exception:
            bundle["ScaleFactor"] = scale_raw_to_factor(bundle["ScaleRaw"])
        scale_set = _get("ScaleSet", None)
        if scale_set is None or (isinstance(scale_set, float) and pd.isna(scale_set)):
            bundle["ScaleSet"] = bundle["ScaleRaw"] != 1.0
        else:
            bundle["ScaleSet"] = str(scale_set).strip().lower() in ("1", "true", "yes")
        try:
            bundle["HeadScaleRaw"] = float(_get("HeadScaleRaw", 1.0) or 1.0)
        except Exception:
            bundle["HeadScaleRaw"] = 1.0
        try:
            bundle["HeadScaleFactor"] = float(
                _get("HeadScaleFactor", scale_raw_to_factor(bundle["HeadScaleRaw"])) or 1.0
            )
        except Exception:
            bundle["HeadScaleFactor"] = scale_raw_to_factor(bundle["HeadScaleRaw"])
        head_scale_set = _get("HeadScaleSet", None)
        if head_scale_set is None or (isinstance(head_scale_set, float) and pd.isna(head_scale_set)):
            bundle["HeadScaleSet"] = bundle["HeadScaleRaw"] != 1.0
        else:
            bundle["HeadScaleSet"] = str(head_scale_set).strip().lower() in ("1", "true", "yes")
        try:
            joints = json.loads(_get("Joints") or "{}")
        except Exception:
            joints = {}
        bundle["Joints"] = joints
        frames[frame] = ensure_pose_bundle(bundle)
    print(
        f"DEBUG: load_pose_dataset: parsed {len(df)} rows into {len(frames)} frames "
        f"via itertuples in {time.perf_counter() - iter_start:.2f}s",
        flush=True,
    )
    _compact_pose_dataset(csv_path, frames, len(df), force=recovered_torn_tail)
    return frames


def save_pose_dataset(csv_path: str, total_frames: int, frames: Dict[int, dict], changed_only: bool = True) -> None:
    if not csv_path:
        return

    changed_rows = []

    for frame in range(total_frames + 1):
        bundle = frames.get(frame)
        if changed_only and (not isinstance(bundle, dict) or not bundle.get("Changed")):
            continue
        if not isinstance(bundle, dict):
            bundle = empty_pose_bundle()
        changed_rows.append(_pose_unified_row(frame, bundle))

    if changed_only and not changed_rows:
        return

    df = pd.DataFrame(changed_rows, columns=POSE_UNIFIED_COLUMNS)
    durable_append(
        csv_path,
        lambda f, is_new_file: df.to_csv(f, index=False, header=is_new_file),
    )
    print(
        f"DEBUG: 3D unified → {csv_path}; changed_only={changed_only}, "
        f"rows_appended={len(changed_rows)}"
    )


def export_pose_dataset(
    frames: Dict[int, dict],
    out_csv: str,
    total_frames: int,
    frame_rate: float,
) -> None:
    rows = []
    for frame in range(total_frames + 1):
        bundle = ensure_pose_bundle(frames.get(frame))
        scale_factor = 1.0
        if bundle.get("ScaleSet"):
            scale_raw = float(bundle.get("ScaleRaw", 1.0) or 1.0)
            scale_factor = float(bundle.get("ScaleFactor", scale_raw_to_factor(scale_raw)) or 1.0)
        head_scale_factor = 1.0
        if bundle.get("HeadScaleSet"):
            head_raw = float(bundle.get("HeadScaleRaw", 1.0) or 1.0)
            head_scale_factor = float(
                bundle.get("HeadScaleFactor", scale_raw_to_factor(head_raw)) or 1.0
            )
        row = {
            "Frame": frame,
            "Time_ms": (frame / frame_rate) * 1000.0 if frame_rate else 0.0,
            "ScaleFactor": scale_factor,
            "HeadScaleFactor": head_scale_factor,
        }
        params = bundle.get("Params") or {}
        for i in (1, 2, 3):
            val = params.get(f"Par{i}")
            row[f"Parameter_{i}"] = "" if val in (None, "") else val
        joints = bundle.get("Joints") or {}
        for joint in POSE_JOINTS:
            event = None
            opacity = None
            if isinstance(joints.get(joint), dict):
                event = joints[joint].get("Event")
                opacity = joints[joint].get("Opacity")
            row[f"{joint}_Event"] = "" if event in (None, "") else event
            if event in (None, ""):
                row[f"{joint}_Opacity"] = ""
            else:
                try:
                    op = float(opacity if opacity is not None else 1.0)
                    op = max(0.0, min(1.0, op))
                    row[f"{joint}_Opacity"] = op
                except (TypeError, ValueError):
                    row[f"{joint}_Opacity"] = 1.0
        row["Note"] = bundle.get("Note") or ""
        rows.append(row)

    cols = [
        "Frame",
        "Time_ms",
        "ScaleFactor",
        "HeadScaleFactor",
        "Parameter_1",
        "Parameter_2",
        "Parameter_3",
    ]
    cols.extend(c for joint in POSE_JOINTS for c in (f"{joint}_Event", f"{joint}_Opacity"))
    cols.append("Note")
    df = pd.DataFrame(rows, columns=cols)
    atomic_write(out_csv, lambda f: df.to_csv(f, index=False))
