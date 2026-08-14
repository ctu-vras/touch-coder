"""
adapters/zone_masks.py
Zone-mask PNG loading (cv2 imread), pulled out of LabelingApp._load_zone_masks.
The pure hit-test rule that consumes these masks lives in domain.touch.zones_at.

`zones_dir` / `list_zone_names` also make this module the single place that
knows the zone-set directory layout (`icons/zones3` vs
`icons/zones3_new_template`): the labeler loads the masks, analysis only needs
the NAMES for its heatmap axes, and neither hard-codes the paths.
"""

import os

import cv2

from domain.touch_stats import NO_ZONE, zone_sort_key
from gui.resource_utils import asset_path

ZONES_DIR = "icons/zones3"
ZONES_DIR_NEW_TEMPLATE = "icons/zones3_new_template"
_IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg')


def zones_dir(new_template: bool = False) -> str:
    """Absolute path of the zone-mask directory for the active template."""
    return asset_path(ZONES_DIR_NEW_TEMPLATE if new_template else ZONES_DIR)


def list_zone_names(new_template: bool = False) -> list:
    """Zone NAMES for the active template, sorted for display.

    Directory scan (adding a PNG adds a zone — see PROJECT.md), so this is I/O
    and belongs in an adapter. The `NN` sentinel is always included because a
    click that hits no mask is recorded as `NN` and must have a heatmap row.
    An unreadable directory yields just `[NN]` with a WARN rather than raising:
    the heatmap axis then still covers whatever zones the data itself contains.
    """
    directory = zones_dir(new_template)
    names = []
    try:
        for filename in os.listdir(directory):
            if filename.lower().endswith(_IMAGE_SUFFIXES):
                names.append(os.path.splitext(filename)[0])
    except OSError as exc:
        print(f"WARN: could not list zone names in {directory!r}: {exc!r}")
        names = []
    if NO_ZONE not in names:
        names.append(NO_ZONE)
    print(f"INFO: zone list for {'new_template' if new_template else 'zones3'}: {len(names)} zones")
    return sorted(names, key=zone_sort_key)


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
