"""
domain/touch.py
Pure touch-annotation rules extracted from labeling_app.py. No I/O, no Tk,
no cv2 — masks arrive pre-loaded, frames arrive as plain dicts.

This module also owns the ZONE VOCABULARY: which zone names are real anatomy
and which are catch-alls. Two rules depend on that classification — the click
hit test here (`zones_at`) and the analysis axis ordering
(`touch_stats.zone_sort_key`) — so it is defined ONCE, here, and imported by
`touch_stats` rather than duplicated (they used to drift).
"""

# --- zone vocabulary --------------------------------------------------------
# Zone names come from the mask FILENAMES (adapters.zone_masks), so these are
# the only names with a meaning fixed by the app itself.

#: Sentinel written when a click hits no mask at all. Never a mask file.
NO_ZONE = "NN"

#: The diagram's drawn boundary lines. Semantically "the click sits exactly
#: between two zones, so we cannot tell which" — an AMBIGUITY MARKER, not a
#: place on the body. See `zones_at` for why that makes it lowest precedence.
LINE_ZONE = "LINE"

#: The region masked as off-body.
OUTSIDE_ZONE = "OUTSIDE"

#: BOX1..BOX6 — the catch-all targets drawn beside the body (ground, prop,
#: caregiver, ...). Not anatomy, but still a deliberate annotation.
BOX_ZONE_PREFIX = "BOX"

# Hit-test precedence tiers: LOWER WINS. Exposed as constants so a caller can
# reason about the tiers without re-deriving the string rules.
ZONE_TIER_REAL = 0       # any anatomical zone (A, B, ..., WB, QB, ...)
ZONE_TIER_BOX = 1        # BOX1..BOX6
ZONE_TIER_OUTSIDE = 2    # OUTSIDE
ZONE_TIER_LINE = 3       # LINE — last resort among actual mask matches
ZONE_TIER_NONE = 4       # the NN sentinel; defensive, NN is not a mask name


def is_box_zone(zone) -> bool:
    """True for the catch-all boxes drawn beside the body (`BOX1`..`BOX6`)."""
    return str(zone).startswith(BOX_ZONE_PREFIX)


def is_catch_all_zone(zone) -> bool:
    """True for every zone name that is NOT real anatomy.

    The catch-alls are the boxes, `OUTSIDE`, `LINE` and the `NN` sentinel.
    Single source of truth for that classification: the hit test uses it to
    build its precedence tiers and `touch_stats.zone_sort_key` uses it to push
    these names to the end of every heatmap axis.
    """
    z = str(zone)
    return is_box_zone(z) or z in {OUTSIDE_ZONE, LINE_ZONE, NO_ZONE}


def zone_precedence(zone) -> int:
    """Hit-test tier of `zone` — LOWER WINS (see the ZONE_TIER_* constants).

    Real anatomy beats the boxes, which beat `OUTSIDE`, which beats `LINE`.
    Pure string classification, so it works for any mask set.
    """
    z = str(zone)
    if z == NO_ZONE:
        return ZONE_TIER_NONE
    if z == LINE_ZONE:
        return ZONE_TIER_LINE
    if z == OUTSIDE_ZONE:
        return ZONE_TIER_OUTSIDE
    if is_box_zone(z):
        return ZONE_TIER_BOX
    return ZONE_TIER_REAL


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
    shapes on white, so a hit is mask pixel == 0.

    Masks OVERLAP: ~1.4% (default set) / ~2.2% (alternate set) of the diagram
    is claimed by two or more of the shipped masks, and nearly all of that is
    the `LINE` mask — the diagram's drawn boundary lines — lying on top of the
    real zones it separates. So the tie-break is not cosmetic, it decides what
    lands in the dataset.

    PRECEDENCE (highest first), `zone_precedence` above:

      1. real anatomical zones  (anything not BOX*/OUTSIDE/LINE/NN)
      2. `BOX1`..`BOX6`         (catch-all targets — still real annotations)
      3. `OUTSIDE`
      4. `LINE`
      5. `['NN']`               only when NO mask matched at all

    WHY `LINE` IS LAST: `LINE` means "the click sits exactly between two zones,
    so we cannot tell which one" — an ambiguity marker. It is only true
    information when nothing else claims the pixel. The previous rule was
    "first mask in the (alphabetically sorted) list wins", which made the
    marker depend on the zone's NAME: a click on the edge of `Q` was recorded
    as `LINE` while the identical click on `F` was recorded as `F`. Same for
    `OUTSIDE`: a real zone beats it, because a pixel claimed by anatomy is not
    off-body.

    Within a tier the FIRST match in `masks` order wins, which
    `adapters.zone_masks.load_zone_masks` makes alphabetical — it only ever
    matters for the ~150px borders two boxes share.

    Always returns a 1-ELEMENT list: the export's `{limb}_Zones` buckets and
    everything downstream assume exactly one zone per click. A miss is the
    sentinel ['NN'] (exports contain [["NN"]] buckets), never an empty list.
    """
    x = int(x); y = int(y)
    matches = []
    for zone_name, image in masks:
        h, w = image.shape[:2]
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        if image[y, x] == 0:
            matches.append(zone_name)
    if not matches:
        return [NO_ZONE]
    # min() keeps the FIRST minimum, so equal tiers fall back to mask order.
    return [min(matches, key=zone_precedence)]
