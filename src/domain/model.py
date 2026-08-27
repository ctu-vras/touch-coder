"""
domain/model.py
Pure in-memory data model for touch labeling: the FrameRecord / FrameBundle
shapes, their constructors, formatting helpers, and the LimbView wrapper.
Split out of the old data_utils.py / video_model.py. No I/O, no Tk.
"""

import json
from typing import TypedDict, NotRequired, List, Optional, Dict, Iterator

# Canonical limb order used across the app (radio buttons, LimbViews, exports
# use their own frozen per-column order — see adapters.export_writer).
LIMBS = ["LH", "RH", "LL", "RL"]


class FrameRecord(TypedDict):
    X: List[int]                 # 0+ points in X
    Y: List[int]                 # 0+ points in Y (aligned with X)
    Onset: str                   # "ON" | "OFF" | ""
    Bodypart: str                # "LH"|"RH"|"LL"|"RL"|"" (for the owning limb CSV this is redundant, but present)
    Zones: List[str]             # always list, may be []
    Touch: Optional[int]          # Parameter_1..3 states ("ON"/"OFF"/None)
    LimbParams: NotRequired[Dict[str, Optional[str]]]        # same, but limb-specific


class FrameBundle(TypedDict):
    LH: FrameRecord
    RH: FrameRecord
    LL: FrameRecord
    RL: FrameRecord
    Note: Optional[str]
    Params: Dict[str, Optional[str]]
    Changed: NotRequired[bool]   # per-frame (global) params


def empty_record(limb: str) -> FrameRecord:
    return FrameRecord(
        X=[], Y=[], Onset="", Bodypart=limb, Zones=[], Touch=None
    )


def _normalize_param_state(v):
    # Legacy artifact: toggle_limb_parameter used to store the string "None" (M1).
    return None if v in (None, "", "None") else v


def empty_bundle() -> FrameBundle:
    return {
        "LH": empty_record("LH"),
        "RH": empty_record("RH"),
        "LL": empty_record("LL"),
        "RL": empty_record("RL"),
        "Note": None,
        "Params": {},
    }


def bundle_summary_dict(b):
    """
    Return a compact, readable dict of everything we care about in a FrameBundle,
    including Onset/Touch/Zones per limb + top-level Note/Params.
    """
    def limb_view(rec, label):
        if not rec:
            return {"_missing": True}
        return {
            "Onset": rec.get("Onset"),
            "Touch": rec.get("Touch"),
            "Zones": rec.get("Zones") or [],
            "Points": len(rec.get("X") or []),  # quick sanity check of clic
            "LimbParams": rec.get("LimbParams") or {},
        }

    return {
        "Note": b.get("Note"),
        "Params": b.get("Params") or {},
        "LH": limb_view(b.get("LH"), "LH"),
        "RH": limb_view(b.get("RH"), "RH"),
        "LL": limb_view(b.get("LL"), "LL"),
        "RL": limb_view(b.get("RL"), "RL"),
    }


def bundle_summary_str(b, frame_index=None):
    import json
    head = {} if frame_index is None else {"Frame": frame_index}
    data = bundle_summary_dict(b)
    data = {**head, **data}
    return json.dumps(data, indent=2, ensure_ascii=False)


def preview_lines_for_save(frames: Dict[int, FrameBundle],
                           total_frames: int,
                           changed_only: bool = True,
                           limbs_order = ("LH","LL","RH","RL")) -> list[str]:
    """
    Build human-readable preview lines for frames that are about to be saved.
    Format per limb present: 'frame=23 | LH: On ["17L"] | RH: Off [] ...'
    If changed_only=True, only lists frames where any limb has rec['changed'].
    """
    lines: list[str] = []

    def bundle_is_changed(b: FrameBundle) -> bool:
        if b.get("Changed"):
            return True
        return any(isinstance(b.get(l), dict) and b[l].get("changed") for l in limbs_order)

    for f in range(total_frames + 1):
        b = frames.get(f)
        if not isinstance(b, dict):
            continue
        if changed_only and not b.get("Changed"):
            continue

        parts = [f"frame={f:>5}"]
        for limb in limbs_order:
            rec = b.get(limb, {})
            xs = rec.get("X", [])
            ys = rec.get("Y", [])
            limb_params = {
                k: v for k, v in (rec.get("LimbParams") or {}).items() if v is not None
            }
            if (not xs or not ys) and not limb_params:
                # skip empty limb (keeps preview concise)
                continue
            bits = []
            if xs and ys:
                bits.append(f"{rec.get('Onset', '')} {rec.get('Zones', [])}")
            if limb_params:
                bits.append("LP[" + ", ".join(f"{k}:{v}" for k, v in limb_params.items()) + "]")
            parts.append(f"{limb}: " + " ".join(bits))
        # include note/params if present
        note = b.get("Note")
        if note:
            parts.append(f'Note="{note}"')
        params = b.get("Params") or {}
        if any(v is not None for v in params.values()):
            # Show only ON/OFF/None summary
            par_show = ", ".join(f"{k}:{v}" for k, v in params.items())
            parts.append(f"Params[{par_show}]")

        if len(parts) > 1:  # at least one limb had content or note/params
            lines.append(" | ".join(parts))
        elif b.get("Changed"):
            # A dirty frame can have nothing left to show (its last point was
            # deleted); it is still written on save, so keep it in the preview
            # to make the line count match what the repository persists.
            lines.append(f"{parts[0]} | (cleared)")

    return lines


class LimbView:
    """Read/write view onto a single limb ('RH'/'LH'/'RL'/'LL') across the owning
    Video's live `frames` dict. Reads never mutate; writes create the bundle on demand.
    `frames` is resolved lazily from the owner, so reassigning `video.frames` needs no rebind."""
    def __init__(self, video, limb: str):
        self._video = video
        self._limb = limb

    @property
    def _frames(self) -> Dict[int, FrameBundle]:
        return self._video.frames

    # --- reads: never insert ---
    def __getitem__(self, frame: int):
        return self._frames[frame][self._limb]          # KeyError if frame absent; no mutation

    def get(self, frame, default=None):
        b = self._frames.get(frame)
        return (b[self._limb] if b and self._limb in b else default)

    def __contains__(self, frame) -> bool:
        return frame in self._frames

    def __len__(self) -> int:
        return len(self._frames)

    def __iter__(self) -> Iterator[int]:
        return iter(self._frames)

    def keys(self):   return self._frames.keys()
    def values(self): return (b[self._limb] for b in self._frames.values() if self._limb in b)
    def items(self):  return ((f, b[self._limb]) for f, b in self._frames.items() if self._limb in b)

    # --- write: creating the bundle here is intended ---
    def __setitem__(self, frame: int, rec):
        b = self._frames.setdefault(frame, empty_bundle())
        b[self._limb] = rec
