import json
import os
from typing import Dict, Optional

import pandas as pd


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
    if "ScaleSet" not in bundle:
        bundle["ScaleSet"] = bundle.get("ScaleRaw", 1.0) != 1.0
    if "ScaleAutoCarry" not in bundle:
        bundle["ScaleAutoCarry"] = False
    if "HeadScaleRaw" not in bundle:
        bundle["HeadScaleRaw"] = 1.0
    if "HeadScaleFactor" not in bundle:
        bundle["HeadScaleFactor"] = scale_raw_to_factor(bundle["HeadScaleRaw"])
    if "HeadScaleSet" not in bundle:
        bundle["HeadScaleSet"] = bundle.get("HeadScaleRaw", 1.0) != 1.0
    if "HeadScaleAutoCarry" not in bundle:
        bundle["HeadScaleAutoCarry"] = False
    if "Note" not in bundle:
        bundle["Note"] = None
    return bundle


def load_pose_dataset(csv_path: str) -> Dict[int, dict]:
    frames: Dict[int, dict] = {}
    if not (csv_path and os.path.exists(csv_path)):
        return frames
    try:
        if os.path.getsize(csv_path) == 0:
            return frames
        df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"ERROR: Failed to read 3D unified CSV: {e}")
        return frames

    for _, row in df.iterrows():
        try:
            frame = int(row["Frame"])
        except Exception:
            continue
        bundle = empty_pose_bundle()
        note = row.get("Note")
        bundle["Note"] = None if pd.isna(note) else str(note)
        try:
            bundle["Params"] = json.loads(row.get("Params") or "{}")
        except Exception:
            bundle["Params"] = {}
        try:
            bundle["ScaleRaw"] = float(row.get("ScaleRaw", 1.0) or 1.0)
        except Exception:
            bundle["ScaleRaw"] = 1.0
        try:
            bundle["ScaleFactor"] = float(row.get("ScaleFactor", scale_raw_to_factor(bundle["ScaleRaw"])) or 1.0)
        except Exception:
            bundle["ScaleFactor"] = scale_raw_to_factor(bundle["ScaleRaw"])
        scale_set = row.get("ScaleSet", None)
        if scale_set is None or (isinstance(scale_set, float) and pd.isna(scale_set)):
            bundle["ScaleSet"] = bundle["ScaleRaw"] != 1.0
        else:
            bundle["ScaleSet"] = str(scale_set).strip().lower() in ("1", "true", "yes")
        try:
            bundle["HeadScaleRaw"] = float(row.get("HeadScaleRaw", 1.0) or 1.0)
        except Exception:
            bundle["HeadScaleRaw"] = 1.0
        try:
            bundle["HeadScaleFactor"] = float(
                row.get("HeadScaleFactor", scale_raw_to_factor(bundle["HeadScaleRaw"])) or 1.0
            )
        except Exception:
            bundle["HeadScaleFactor"] = scale_raw_to_factor(bundle["HeadScaleRaw"])
        head_scale_set = row.get("HeadScaleSet", None)
        if head_scale_set is None or (isinstance(head_scale_set, float) and pd.isna(head_scale_set)):
            bundle["HeadScaleSet"] = bundle["HeadScaleRaw"] != 1.0
        else:
            bundle["HeadScaleSet"] = str(head_scale_set).strip().lower() in ("1", "true", "yes")
        try:
            joints = json.loads(row.get("Joints") or "{}")
        except Exception:
            joints = {}
        bundle["Joints"] = joints
        frames[frame] = ensure_pose_bundle(bundle)
    return frames


def save_pose_dataset(csv_path: str, total_frames: int, frames: Dict[int, dict], changed_only: bool = True) -> None:
    if not csv_path:
        return

    changed_rows = []
    existing_map: Dict[int, dict] = {}

    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        try:
            existing_df = pd.read_csv(csv_path)
            for _, row in existing_df.iterrows():
                try:
                    frame = int(row["Frame"])
                except Exception:
                    continue
                existing_map[frame] = {
                    "Frame": frame,
                    "Note": None if pd.isna(row.get("Note")) else row.get("Note"),
                    "Params": row.get("Params"),
                    "ScaleRaw": row.get("ScaleRaw"),
                    "ScaleFactor": row.get("ScaleFactor"),
                    "ScaleSet": row.get("ScaleSet"),
                    "HeadScaleRaw": row.get("HeadScaleRaw") if "HeadScaleRaw" in row else 1.0,
                    "HeadScaleFactor": row.get("HeadScaleFactor") if "HeadScaleFactor" in row else 1.0,
                    "HeadScaleSet": row.get("HeadScaleSet") if "HeadScaleSet" in row else False,
                    "Joints": row.get("Joints"),
                }
        except Exception:
            existing_map = {}

    for frame in range(total_frames + 1):
        bundle = frames.get(frame)
        if changed_only and (not isinstance(bundle, dict) or not bundle.get("Changed")):
            continue
        if not isinstance(bundle, dict):
            bundle = empty_pose_bundle()
        bundle = ensure_pose_bundle(bundle)
        changed_rows.append(
            {
                "Frame": frame,
                "Note": bundle.get("Note"),
                "Params": json.dumps(bundle.get("Params") or {}),
                "ScaleRaw": bundle.get("ScaleRaw", 0.0),
                "ScaleFactor": bundle.get("ScaleFactor", scale_raw_to_factor(bundle.get("ScaleRaw", 0.0))),
                "ScaleSet": bool(bundle.get("ScaleSet", False)),
                "HeadScaleRaw": bundle.get("HeadScaleRaw", 1.0),
                "HeadScaleFactor": bundle.get(
                    "HeadScaleFactor", scale_raw_to_factor(bundle.get("HeadScaleRaw", 1.0))
                ),
                "HeadScaleSet": bool(bundle.get("HeadScaleSet", False)),
                "Joints": json.dumps(bundle.get("Joints") or {}),
            }
        )

    if changed_only and not changed_rows:
        return

    for row in changed_rows:
        existing_map[row["Frame"]] = row

    cols = [
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
    out_rows = [existing_map[k] for k in sorted(existing_map.keys())]
    df = pd.DataFrame(out_rows, columns=cols)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    df.to_csv(csv_path, index=False)


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
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    df.to_csv(out_csv, index=False)
