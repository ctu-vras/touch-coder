"""Pure builders for the cumulative 3D pose timeline state."""

from pose_mismatch_data import POSE_JOINTS, ensure_pose_bundle, scale_raw_to_factor


def build_pose_timeline_state(frames: dict, total_frames: int) -> dict:
    """Build cumulative pose state for every frame from 0 through total_frames."""
    return update_pose_timeline_state({}, frames, total_frames, 0)


def update_pose_timeline_state(
    state: dict,
    frames: dict,
    total_frames: int,
    from_frame: int,
) -> dict:
    """Recompute a cached state's suffix in place and return the same dict."""
    total_frames = int(total_frames)
    for frame in tuple(state):
        if frame > total_frames:
            del state[frame]
    if total_frames < 0:
        state.clear()
        return state

    start = max(0, int(from_frame))
    if start > total_frames:
        return state

    if start > 0:
        previous = state.get(start - 1)
        if not isinstance(previous, dict):
            rebuilt = build_pose_timeline_state(frames, total_frames)
            state.clear()
            state.update(rebuilt)
            return state
        active_joints = set(previous.get("active") or ())
        active_scale_raw = float(previous.get("scale_raw", 1.0) or 1.0)
        active_scale_factor = float(previous.get("scale_factor", 1.0) or 1.0)
        active_head_scale_raw = float(previous.get("head_scale_raw", 1.0) or 1.0)
        active_head_scale_factor = float(previous.get("head_scale_factor", 1.0) or 1.0)
    else:
        active_joints = set()
        active_scale_raw = 1.0
        active_scale_factor = 1.0
        active_head_scale_raw = 1.0
        active_head_scale_factor = 1.0

    for frame in range(start, total_frames + 1):
        bundle = ensure_pose_bundle(frames.get(frame))
        joints = bundle.get("Joints") or {}
        events = {}
        for joint in POSE_JOINTS:
            rec = joints.get(joint, {})
            event = rec.get("Event") if isinstance(rec, dict) else None
            if event == "ON":
                active_joints.add(joint)
                events[joint] = "ON"
            elif event == "OFF":
                active_joints.discard(joint)
                events[joint] = "OFF"

        if bundle.get("ScaleSet"):
            active_scale_raw = float(bundle.get("ScaleRaw", 1.0) or 1.0)
            active_scale_factor = float(
                bundle.get("ScaleFactor", scale_raw_to_factor(active_scale_raw)) or 1.0
            )
        else:
            active_scale_raw = 1.0
            active_scale_factor = 1.0

        if bundle.get("HeadScaleSet"):
            active_head_scale_raw = float(bundle.get("HeadScaleRaw", 1.0) or 1.0)
            active_head_scale_factor = float(
                bundle.get(
                    "HeadScaleFactor", scale_raw_to_factor(active_head_scale_raw)
                )
                or 1.0
            )
        else:
            active_head_scale_raw = 1.0
            active_head_scale_factor = 1.0

        state[frame] = {
            "events": events,
            "active": set(active_joints),
            "active_count": len(active_joints),
            "scale_raw": active_scale_raw,
            "scale_factor": active_scale_factor,
            "head_scale_raw": active_head_scale_raw,
            "head_scale_factor": active_head_scale_factor,
        }

    return state
