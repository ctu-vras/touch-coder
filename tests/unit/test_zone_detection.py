"""
Characterization tests for the mask-based zone hit-testing
(`LabelingApp.find_image_with_white_pixel` / `_load_zone_masks`).

Every click on the diagram is translated to zone names through these two
methods, and the zone names flow straight into the frozen export contract
(`{limb}_Zones`), so their EXACT current semantics must survive the refactor:

  * Despite the method's name, a pixel is a hit when the mask value is 0
    (BLACK), not white — the zone masks are black shapes on white.
  * Only the FIRST matching mask (in `_zone_masks` list order) is returned,
    even when several masks overlap — the result is always a 1-element list.
  * A miss (no mask matched, or the click is outside every mask's bounds) is
    the sentinel ['NN'], NOT an empty list. Exports contain [["NN"]] buckets,
    so changing this would silently change research data.

These pin behavior as-is; they are not an endorsement of the naming.
"""
import os
from contextlib import nullcontext
from types import SimpleNamespace

import cv2
import numpy as np

import labeling_app
from labeling_app import LabelingApp


def _mask(black_box=None, size=20):
    """A size x size white (255) mask with an optional black (0) zone square
    given as (row_start, row_stop, col_start, col_stop)."""
    img = np.full((size, size), 255, dtype=np.uint8)
    if black_box:
        r0, r1, c0, c1 = black_box
        img[r0:r1, c0:c1] = 0
    return img


def _stub_app(masks):
    return SimpleNamespace(
        _zone_masks=masks,
        perf=SimpleNamespace(time=lambda _name: nullcontext()),
    )


def _two_zone_app():
    # A: rows/cols 5..9 black; B: rows/cols 8..14 black; they overlap at 8..9.
    return _stub_app([
        ("A", _mask((5, 10, 5, 10))),
        ("B", _mask((8, 15, 8, 15))),
    ])


def test_click_inside_single_zone_returns_that_zone():
    app = _two_zone_app()
    assert LabelingApp.find_image_with_white_pixel(app, 6, 6) == ["A"]
    assert LabelingApp.find_image_with_white_pixel(app, 12, 12) == ["B"]


def test_hit_means_mask_pixel_is_black_zero_not_white():
    """The zone masks are black-on-white: value 0 is a hit, 255 (and any other
    non-zero value) is a miss — the opposite of what the method name suggests."""
    app = _stub_app([("GRAY", np.full((20, 20), 128, dtype=np.uint8))])
    assert LabelingApp.find_image_with_white_pixel(app, 10, 10) == ["NN"]

    app = _stub_app([("BLACK", np.zeros((20, 20), dtype=np.uint8))])
    assert LabelingApp.find_image_with_white_pixel(app, 10, 10) == ["BLACK"]


def test_overlapping_masks_first_in_list_wins_single_result():
    """(9, 9) is black in BOTH masks; only the first-listed zone is reported,
    and the result is a 1-element list (never all matches)."""
    app = _two_zone_app()
    assert LabelingApp.find_image_with_white_pixel(app, 9, 9) == ["A"]

    reversed_app = _stub_app(list(reversed(app._zone_masks)))
    assert LabelingApp.find_image_with_white_pixel(reversed_app, 9, 9) == ["B"]


def test_miss_returns_NN_sentinel_not_empty_list():
    app = _two_zone_app()
    assert LabelingApp.find_image_with_white_pixel(app, 0, 0) == ["NN"]


def test_out_of_bounds_click_is_a_safe_NN_miss():
    """Clicks past the mask edges (or negative after int-truncation) must not
    raise — they skip every mask and fall through to the ['NN'] sentinel."""
    app = _two_zone_app()
    for x, y in [(25, 6), (6, 25), (-1, 6), (6, -1), (20, 20), (-5, -5)]:
        assert LabelingApp.find_image_with_white_pixel(app, x, y) == ["NN"]


def test_float_coordinates_are_truncated_toward_zero():
    """Diagram clicks arrive as scaled floats (x / diagram_scale); the method
    int()-truncates them, so 9.99 tests pixel 9 (inside A), not 10."""
    app = _two_zone_app()
    assert LabelingApp.find_image_with_white_pixel(app, 9.99, 9.99) == ["A"]
    assert LabelingApp.find_image_with_white_pixel(app, 10.01, 10.01) == ["B"]


def test_empty_mask_list_triggers_lazy_load():
    """With no masks cached, the method calls self._load_zone_masks() before
    matching (masks load lazily on the first click after startup)."""
    app = _stub_app([])
    app._load_zone_masks = lambda: app.__setattr__(
        "_zone_masks", [("LAZY", _mask((0, 20, 0, 20)))]
    )
    assert LabelingApp.find_image_with_white_pixel(app, 3, 3) == ["LAZY"]


# --- _load_zone_masks -------------------------------------------------------

def _loader_stub():
    return SimpleNamespace(NEW_TEMPLATE=False, _zone_dir=None, _zone_masks=[])


def _write_png(directory, name, value=0):
    cv2.imwrite(os.path.join(directory, name),
                np.full((4, 4), value, dtype=np.uint8))


def test_load_zone_masks_reads_images_and_names_from_filenames(tmp_path, monkeypatch):
    """Masks come from every readable image file in the zones directory; the
    zone NAME is the filename minus its last extension. Non-image and
    unreadable files are skipped silently (no crash)."""
    zone_dir = tmp_path / "zones3"
    zone_dir.mkdir()
    _write_png(str(zone_dir), "FACE.png")
    _write_png(str(zone_dir), "17L.jpg")
    (zone_dir / "readme.txt").write_text("not a mask")
    (zone_dir / "corrupt.png").write_bytes(b"this is not a png")

    monkeypatch.setattr(labeling_app, "resource_path", lambda rel: str(zone_dir))
    app = _loader_stub()

    LabelingApp._load_zone_masks(app)

    assert sorted(name for name, _ in app._zone_masks) == ["17L", "FACE"]
    for _, img in app._zone_masks:
        assert img.ndim == 2  # loaded grayscale — hit test indexes [y, x]
    assert app._zone_dir == str(zone_dir)


def test_load_zone_masks_caches_per_directory(tmp_path, monkeypatch):
    """A second call for the same directory with masks already loaded is a
    no-op (the cache key is the resolved directory), so per-click lazy loads
    stay cheap."""
    zone_dir = tmp_path / "zones3"
    zone_dir.mkdir()
    _write_png(str(zone_dir), "FACE.png")
    monkeypatch.setattr(labeling_app, "resource_path", lambda rel: str(zone_dir))
    app = _loader_stub()

    LabelingApp._load_zone_masks(app)
    first = app._zone_masks
    _write_png(str(zone_dir), "LATER.png")  # would be picked up by a reload

    LabelingApp._load_zone_masks(app)

    assert app._zone_masks is first
    assert [name for name, _ in app._zone_masks] == ["FACE"]


def test_load_zone_masks_missing_directory_is_a_warned_noop(tmp_path, monkeypatch, capsys):
    missing = str(tmp_path / "nope")
    monkeypatch.setattr(labeling_app, "resource_path", lambda rel: missing)
    app = _loader_stub()

    LabelingApp._load_zone_masks(app)

    assert app._zone_masks == []
    assert "Zones directory not found" in capsys.readouterr().out
