"""
service_layer/annotation_service.py
Pure mutation halves of the annotation handlers, extracted from
LabelingApp (on_diagram_click / on_middle_click / parameter_dic_insert /
toggle_limb_parameter / save_note). No Tk, no I/O: each function takes the
live frames dict plus plain arguments and returns what the GUI needs for
its repaint. Marking bundles dirty (Changed flag + timeline dirty bits)
stays with the GUI's mark_bundle_changed, called AFTER these mutations —
exactly the old handler ordering.
"""

from typing import Dict, Optional

from domain.model import FrameBundle, empty_bundle
from domain.touch import cycle_param_state

LIMB_PARAM_KEYS = ("Par1", "Par2", "Par3")


def _param_key(idx: int) -> str:
    return f"Par{idx}"


def ensure_bundle(frames: Dict[int, FrameBundle], idx: int) -> FrameBundle:
    """The canonical get-or-create for a frame bundle (was _ensure_bundle)."""
    b = frames.get(idx)
    if not isinstance(b, dict):
        b = empty_bundle()
        frames[idx] = b
    return b


def _empty_limb_record(limb: str) -> dict:
    return {"X": [], "Y": [], "Onset": "", "Bodypart": limb, "Zones": [], "Touch": None}


def _normalize_zones_shape(rec: dict) -> list:
    """Legacy zones shape (flat list) -> list-of-lists aligned with X/Y."""
    zones = rec.get('Zones', [])
    if zones and isinstance(zones[0], (int, str)):
        zones = [[z] for z in zones]
    rec['Zones'] = zones
    return zones


def add_click(frames: Dict[int, FrameBundle], idx: int, limb: str,
              x_pos: float, y_pos: float, onset: str, zones: list) -> dict:
    """Touch-branch mutation of on_diagram_click: append the click's X/Y and
    its zone bucket; the record-level Onset/Bodypart are OVERWRITTEN by the
    newest click (whole-record onset rule). Returns the mutated record."""
    bundle = frames.get(idx)
    existing = bundle.get(limb) if isinstance(bundle, dict) else None

    if not isinstance(existing, dict) or (not existing.get('X') and not existing.get('Y')):
        rec = {
            "X": [int(x_pos)],
            "Y": [int(y_pos)],
            "Onset": onset,
            "Bodypart": limb,
            # IMPORTANT: store zones per point (list-of-lists)
            "Zones": [zones],   # <- one entry per point
            "Touch": None,
        }
        b = frames.setdefault(idx, empty_bundle())
        b[limb] = rec
    else:
        rec = existing
        rec.setdefault('X', []).append(int(x_pos))
        rec.setdefault('Y', []).append(int(y_pos))

        # normalize Zones to list-of-lists if older shape is present
        zone_buckets = _normalize_zones_shape(rec)
        zone_buckets.append(zones)  # one zones bucket per point

        rec['Bodypart'] = limb
        rec['Onset'] = onset

    return rec


def remove_nearest_click(frames: Dict[int, FrameBundle], idx: int, limb: str,
                         x_pos: float, y_pos: float, max_dist: float) -> bool:
    """Middle-click deletion: remove the click nearest to (x_pos, y_pos)
    within `max_dist` (data coords) plus its zones bucket. When the last
    click goes, the record is CLEARED (Onset reset to "") so nothing leaks
    into the export. Returns True when a click was removed."""
    bundle = frames.get(idx)
    rec = bundle.get(limb) if isinstance(bundle, dict) else None
    if not isinstance(rec, dict):
        return False  # nothing to delete

    xs = rec.get('X', [])
    ys = rec.get('Y', [])
    zones = _normalize_zones_shape(rec)

    if not xs or not ys:
        return False

    # find closest point (euclidean in data coords)
    closest_idx = None
    closest_d2 = float('inf')
    for i, (x, y) in enumerate(zip(xs, ys)):
        d2 = (x - x_pos) * (x - x_pos) + (y - y_pos) * (y - y_pos)
        if d2 < closest_d2:
            closest_d2 = d2
            closest_idx = i

    if closest_idx is None or closest_d2 > max_dist ** 2:
        return False

    # delete this point and its zones bucket (if present)
    del xs[closest_idx]
    del ys[closest_idx]
    if isinstance(zones, list) and closest_idx < len(zones):
        del zones[closest_idx]

    if not xs:  # no points left -> clear the record to prevent export leakage
        bundle[limb] = _empty_limb_record(limb)  # important: Onset cleared to ""
    else:
        rec['X'] = xs
        rec['Y'] = ys
        rec['Zones'] = zones
        # keep Onset as-is for remaining points

    return True


def toggle_global_param(frames: Dict[int, FrameBundle], idx: int,
                        param_index: int) -> Optional[str]:
    """Cycle global Param_i (1..3) on the frame's bundle:
    None -> "ON" -> "OFF" -> None. Returns the new state."""
    b = ensure_bundle(frames, idx)
    params = b.get("Params")
    if not isinstance(params, dict):
        params = {}
    key = _param_key(param_index)
    new_state = cycle_param_state(params.get(key))
    params[key] = new_state
    b["Params"] = params
    return new_state


def toggle_limb_param(frames: Dict[int, FrameBundle], idx: int, limb: str,
                      param_index: int) -> Optional[str]:
    """Cycle limb-specific Param_i (1..3) on the frame's limb record,
    creating bundle/record/LimbParams as needed. Returns the new state."""
    b = ensure_bundle(frames, idx)
    rec = b.get(limb) or _empty_limb_record(limb)
    b[limb] = rec

    limb_params = rec.get("LimbParams")
    if not isinstance(limb_params, dict):
        limb_params = {}
        rec["LimbParams"] = limb_params
    key = _param_key(param_index)
    new_state = cycle_param_state(limb_params.get(key))
    limb_params[key] = new_state
    return new_state


def set_note(frames: Dict[int, FrameBundle], idx: int, note_text: str) -> bool:
    """Save-note semantics: store the stripped text (None when empty);
    returns True when the stored note actually changed."""
    b = ensure_bundle(frames, idx)
    prev = (b.get("Note") or "").strip()
    new_val = note_text if note_text else None
    if prev != (new_val or ""):
        b["Note"] = new_val
        return True
    return False
