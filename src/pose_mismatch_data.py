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
    return {joint: {"Event": None, "X": None, "Y": None} for joint in POSE_JOINTS}


def empty_pose_bundle() -> dict:
    return {
        "Note": None,
        "Params": {},
        "ScaleRaw": 1.0,
        "ScaleFactor": 1.0,
        "ScaleSet": False,
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
            joints[joint] = {"Event": None, "X": None, "Y": None}
        else:
            if "Event" not in rec:
                rec["Event"] = None
            if "X" not in rec:
                rec["X"] = None
            if "Y" not in rec:
                rec["Y"] = None
    if "ScaleRaw" not in bundle:
        bundle["ScaleRaw"] = 1.0
    if "ScaleFactor" not in bundle:
        bundle["ScaleFactor"] = scale_raw_to_factor(bundle["ScaleRaw"])
    if "ScaleSet" not in bundle:
        bundle["ScaleSet"] = bundle.get("ScaleRaw", 1.0) != 1.0
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
                "Joints": json.dumps(bundle.get("Joints") or {}),
            }
        )

    if changed_only and not changed_rows:
        return

    for row in changed_rows:
        existing_map[row["Frame"]] = row

    cols = ["Frame", "Note", "Params", "ScaleRaw", "ScaleFactor", "ScaleSet", "Joints"]
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
    active_scale_raw = 0.0
    active_scale_factor = 1.0
    for frame in range(total_frames + 1):
        bundle = ensure_pose_bundle(frames.get(frame))
        if bundle.get("ScaleSet"):
            active_scale_raw = float(bundle.get("ScaleRaw", 0.0) or 0.0)
            active_scale_factor = float(bundle.get("ScaleFactor", scale_raw_to_factor(active_scale_raw)) or 1.0)
        row = {
            "Frame": frame,
            "Time_ms": (frame / frame_rate) * 1000.0 if frame_rate else 0.0,
            "ScaleFactor": active_scale_factor,
        }
        params = bundle.get("Params") or {}
        for i in (1, 2, 3):
            val = params.get(f"Par{i}")
            row[f"Parameter_{i}"] = "" if val in (None, "") else val
        joints = bundle.get("Joints") or {}
        for joint in POSE_JOINTS:
            event = None
            if isinstance(joints.get(joint), dict):
                event = joints[joint].get("Event")
            row[f"{joint}_Event"] = "" if event in (None, "") else event
        row["Note"] = bundle.get("Note") or ""
        rows.append(row)

    cols = ["Frame", "Time_ms", "ScaleFactor", "Parameter_1", "Parameter_2", "Parameter_3"]
    cols.extend(f"{joint}_Event" for joint in POSE_JOINTS)
    cols.append("Note")
    df = pd.DataFrame(rows, columns=cols)
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    df.to_csv(out_csv, index=False)
