"""
adapters/zone_masks.py
Zone-mask PNG loading (cv2 imread), pulled out of LabelingApp._load_zone_masks.
The pure hit-test rule that consumes these masks lives in domain.touch.zones_at.
"""

import os

import cv2


def load_zone_masks(directory):
    """Load every readable grayscale mask image in `directory`.

    Returns an ordered list of (zone_name, image) tuples — the zone name is
    the filename minus its last extension. Filenames are sorted so mask
    precedence (first match wins on overlapping masks, see zones_at) is
    deterministic across filesystems instead of depending on os.listdir order.
    Unreadable / non-image files are skipped silently.
    """
    masks = []
    if not os.path.isdir(directory):
        print(f"WARNING: Zones directory not found: {directory}")
        return masks
    for filename in sorted(os.listdir(directory)):
        fp = os.path.join(directory, filename)
        if os.path.isfile(fp) and fp.lower().endswith(('.png', '.jpg', '.jpeg')):
            image = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            zone_name = filename.rsplit('.', 1)[0]
            masks.append((zone_name, image))
    print(f"INFO: Loaded touch zone masks from {directory}: {len(masks)} masks")
    return masks
