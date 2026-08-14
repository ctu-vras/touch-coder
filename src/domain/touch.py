"""
domain/touch.py
Pure touch-annotation rules extracted from labeling_app.py. No I/O, no Tk,
no cv2 — masks arrive pre-loaded, frames arrive as plain dicts.
"""


def cycle_param_state(current):
    """Parameter toggle rule (was LabelingApp._param_next_state)."""
    # Cycle: None -> "ON" -> "OFF" -> "ON" ...
    if current is None or current == "":
        return "ON"
    if current == "ON":
        return "OFF"
    if current == "OFF":
        return None
    return None  # current == "ON" or anything else


def find_last_open_onset(frames, limb, current_frame):
    """Return the click points of the last still-open 'ON' for `limb` at or
    before `current_frame`, or [(None, None)] when the touch is closed / absent
    (was the pure scan inside LabelingApp.find_last_green).

    Walks integer frame indices backward from current_frame instead of
    sorting all dict keys — O(distance to last ON/OFF) instead of
    O(N log N) per call. Matters at 300k+ frames.
    """
    for f in range(current_frame, -1, -1):
        b = frames.get(f)
        if not isinstance(b, dict):
            continue
        rec = b.get(limb, {}) if isinstance(b, dict) else {}
        onset = rec.get("Onset")
        if onset == "OFF":
            return [(None, None)]
        if onset == "ON":
            xs = rec.get("X", []) or []
            ys = rec.get("Y", []) or []
            return list(zip(xs, ys)) if xs and ys else [(None, None)]

    return [(None, None)]


def zones_at(masks, x, y):
    """Mask-based zone hit test (was the core of find_image_with_white_pixel).

    NOTE the historically misleading name upstream: the zone masks are BLACK
    shapes on white, so a hit is mask pixel == 0. Only the FIRST matching mask
    (in `masks` list order) is reported, always as a 1-element list; a miss is
    the sentinel ['NN'] — exports contain [["NN"]] buckets, so this exact
    behavior is pinned by the golden/zone-detection tests.
    """
    x = int(x); y = int(y)
    matches = []
    for zone_name, image in masks:
        h, w = image.shape[:2]
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        if image[y, x] == 0:
            matches.append(zone_name)
    if matches:
        return [matches[0]]
    return ['NN']
