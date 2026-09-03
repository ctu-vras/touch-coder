"""
Tests for the mask-based zone hit-testing
(`LabelingApp.find_image_with_white_pixel` -> `domain.touch.zones_at`, plus
`LabelingApp._load_zone_masks`).

Every click on the diagram is translated to zone names through these two
methods, and the zone names flow straight into the frozen export contract
(`{limb}_Zones`), so their EXACT semantics are pinned here:

  * Despite the method's name, a pixel is a hit when the mask value is 0
    (BLACK), not white — the zone masks are black shapes on white.
  * The result is ALWAYS a 1-element list, even when several masks overlap.
  * A miss (no mask matched, or the click is outside every mask's bounds) is
    the sentinel ['NN'], NOT an empty list. Exports contain [["NN"]] buckets,
    so changing this would silently change research data.
  * Overlapping masks are resolved by PRECEDENCE, not by list order:
    real anatomical zone > BOX1..BOX6 > OUTSIDE > LINE. List order (which the
    loader makes alphabetical) only breaks ties WITHIN a tier.

WHY THE PRECEDENCE, and why it changed (this file used to pin plain
first-match-wins): the shipped masks overlap on 1.4% / 2.2% of the diagram and
almost all of that is the `LINE` mask — the diagram's drawn boundary lines —
sitting on top of the real zones it separates. `LINE` MEANS "the click is
exactly between two zones, so we cannot tell which": an ambiguity marker that
is only true information when nothing else claims the pixel. Under
first-match-wins the marker depended on the zone's NAME instead: a click on the
edge of `Q` came out as `LINE` (Q sorts after LINE) while the identical click
on `F` came out as `F`. Same argument for `OUTSIDE` — a pixel claimed by
anatomy is not off-body. So `LINE` is the last resort among real matches and
`NN` stays the last resort overall.
"""
import os
from contextlib import nullcontext
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import labeling_app
from adapters.zone_masks import load_zone_masks, zones_dir
from domain.touch import (
    LINE_ZONE,
    NO_ZONE,
    ZONE_TIER_REAL,
    is_catch_all_zone,
    zone_precedence,
    zones_at,
)
from domain.touch_stats import zone_sort_key
from labeling_app import LabelingApp


def _mask(black_box=None, size=20):
    """A size x size white (255) mask with an optional black (0) zone square
    given as (row_start, row_stop, col_start, col_stop)."""
    img = np.full((size, size), 255, dtype=np.uint8)
    if black_box:
        r0, r1, c0, c1 = black_box
        img[r0:r1, c0:c1] = 0
    return img


def _full():
    """A mask that is black everywhere — claims every pixel."""
    return _mask((0, 20, 0, 20))


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


def _hit(masks, x, y):
    """Resolve (x, y) through the GUI wrapper for `masks` in that order."""
    return LabelingApp.find_image_with_white_pixel(_stub_app(list(masks)), x, y)


def _both_orders(first, second, x=10, y=10):
    """The resolved zone with `first` listed first, then with it listed last.

    Precedence must not depend on mask order, so both calls must agree.
    """
    a = _hit([(first, _full()), (second, _full())], x, y)
    b = _hit([(second, _full()), (first, _full())], x, y)
    assert a == b, f"order-dependent result: {a} vs {b}"
    return a


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


# --- precedence between tiers ----------------------------------------------

def test_real_zone_beats_LINE_regardless_of_mask_order():
    """The change this file exists to pin. `Q` sorts AFTER `LINE` and used to
    lose to it; `F` sorts before and used to win. Now both win, because a
    pixel a real zone claims is not "between two zones"."""
    assert _both_orders("Q", LINE_ZONE) == ["Q"]      # was ["LINE"]
    assert _both_orders("F", LINE_ZONE) == ["F"]      # unchanged
    assert _both_orders("WB", LINE_ZONE) == ["WB"]    # was ["LINE"]


def test_box_beats_LINE():
    """The boxes are catch-alls, but a click in one is still a deliberate
    annotation — more informative than "ambiguous boundary"."""
    assert _both_orders("BOX5", LINE_ZONE) == ["BOX5"]   # was ["BOX5"] (B < L)
    assert _both_orders("BOX1", LINE_ZONE) == ["BOX1"]


def test_outside_beats_LINE():
    """`OUTSIDE` sorts after `LINE`, so this pixel used to report `LINE`. It is
    the bulk of the real-mask change (~2000 px per template)."""
    assert _both_orders("OUTSIDE", LINE_ZONE) == ["OUTSIDE"]  # was ["LINE"]


def test_real_zone_beats_OUTSIDE():
    """Hairline slivers where a real zone's mask laps over the off-body mask
    belong to the body. `Q`/`R` sort after `OUTSIDE` and used to lose."""
    assert _both_orders("Q", "OUTSIDE") == ["Q"]        # was ["OUTSIDE"]
    assert _both_orders("C", "OUTSIDE") == ["C"]        # unchanged (C < O)


def test_real_zone_beats_box():
    """A real zone outranks a catch-all box even when the box sorts first."""
    assert _both_orders("A", "BOX1") == ["A"]           # unchanged (A < B)
    assert _both_orders("D", "BOX1") == ["D"]           # was ["BOX1"]


def test_box_beats_OUTSIDE():
    assert _both_orders("BOX3", "OUTSIDE") == ["BOX3"]


def test_full_tier_ladder_resolves_to_the_real_zone():
    """All four tiers claiming one pixel: the real zone wins outright."""
    masks = [(name, _full()) for name in
             (LINE_ZONE, "OUTSIDE", "BOX2", "R")]
    assert _hit(masks, 10, 10) == ["R"]
    assert _hit(list(reversed(masks)), 10, 10) == ["R"]


def test_LINE_is_reported_when_it_is_the_only_match():
    """`LINE` is demoted, never suppressed: with nothing else claiming the
    pixel it is the correct answer (and must not degrade to `NN`)."""
    assert _hit([(LINE_ZONE, _full())], 10, 10) == [LINE_ZONE]
    # ... and when the other masks miss this pixel.
    assert _hit([("A", _mask((0, 5, 0, 5))), (LINE_ZONE, _full())], 10, 10) == [LINE_ZONE]


def test_OUTSIDE_is_reported_when_only_LINE_competes():
    assert _hit([(LINE_ZONE, _full()), ("OUTSIDE", _full())], 10, 10) == ["OUTSIDE"]


# --- ties WITHIN a tier: mask order (alphabetical from the loader) ----------

def test_overlapping_real_zones_resolve_by_mask_list_order():
    """(9, 9) is black in BOTH masks and both are real zones, so the tier
    cannot decide: the FIRST-listed mask wins and the result stays a 1-element
    list (never all matches). The loader sorts filenames, so "first" means
    alphabetical."""
    app = _two_zone_app()
    assert LabelingApp.find_image_with_white_pixel(app, 9, 9) == ["A"]

    reversed_app = _stub_app(list(reversed(app._zone_masks)))
    assert LabelingApp.find_image_with_white_pixel(reversed_app, 9, 9) == ["B"]


def test_box_vs_box_border_resolves_by_mask_list_order():
    """The only same-tier overlap left in the shipped masks: the ~150 px
    borders neighbouring boxes share. Same tier, so the FIRST-listed mask wins
    — and `load_zone_masks` sorts filenames, so with the real masks that is the
    alphabetically first box (`BOX1` over `BOX3`)."""
    assert _hit([("BOX1", _full()), ("BOX3", _full())], 10, 10) == ["BOX1"]
    assert _hit([("BOX5", _full()), ("BOX6", _full())], 10, 10) == ["BOX5"]
    # Order-dependence WITHIN a tier is explicit, not accidental: the rule is
    # mask order, and the alphabetical guarantee comes from the loader.
    assert _hit([("BOX3", _full()), ("BOX1", _full())], 10, 10) == ["BOX3"]


@pytest.mark.parametrize("template", ("zones3", "zones3_new_template"))
def test_real_masks_box_borders_go_to_the_alphabetically_first_box(template):
    """End-to-end version of the above through the real loader: on a pixel two
    boxes share, the reported box is the lower-numbered one."""
    masks = _real_masks(template == "zones3_new_template")
    names = [n for n, _ in masks]
    box = [i for i, n in enumerate(names) if n.startswith("BOX")]
    height = min(img.shape[0] for _, img in masks)
    width = min(img.shape[1] for _, img in masks)
    hits = np.stack([img[:height, :width] == 0 for _, img in masks])

    real = [i for i, n in enumerate(names) if not is_catch_all_zone(n)]
    ys, xs = np.where((hits[box].sum(axis=0) > 1) & ~hits[real].any(axis=0))
    assert len(ys), f"{template}: no box-vs-box overlap — invariant untested"
    for i in np.linspace(0, len(ys) - 1, min(len(ys), 200)).astype(int):
        x, y = int(xs[i]), int(ys[i])
        overlapping = sorted(names[j] for j in box if hits[j, y, x])
        assert zones_at(masks, x, y) == [overlapping[0]], (template, x, y)


# --- misses -----------------------------------------------------------------

def test_miss_returns_NN_sentinel_not_empty_list():
    app = _two_zone_app()
    assert LabelingApp.find_image_with_white_pixel(app, 0, 0) == ["NN"]


def test_out_of_bounds_click_is_a_safe_NN_miss():
    """Clicks past the mask edges (or negative after int-truncation) must not
    raise — they skip every mask and fall through to the ['NN'] sentinel."""
    app = _two_zone_app()
    for x, y in [(25, 6), (6, 25), (-1, 6), (6, -1), (20, 20), (-5, -5)]:
        assert LabelingApp.find_image_with_white_pixel(app, x, y) == ["NN"]


def test_no_masks_at_all_is_NN_not_a_crash():
    assert zones_at([], 5, 5) == [NO_ZONE]


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


# --- the shared catch-all classification -----------------------------------

def test_catch_all_classification_is_shared_with_the_analysis_axis_order():
    """`domain.touch.is_catch_all_zone` is the ONE definition of "not real
    anatomy": the hit-test tiers and `touch_stats.zone_sort_key` both use it,
    so they cannot drift apart."""
    for name in ("BOX1", "BOX6", "OUTSIDE", LINE_ZONE, NO_ZONE):
        assert is_catch_all_zone(name), name
        assert zone_precedence(name) > ZONE_TIER_REAL, name
        assert zone_sort_key(name)[0] == 1, name

    for name in ("A", "Z", "WB", "QB", "Q", "R"):
        assert not is_catch_all_zone(name), name
        assert zone_precedence(name) == ZONE_TIER_REAL, name
        assert zone_sort_key(name)[0] == 0, name


# --- the REAL shipped masks -------------------------------------------------

_TEMPLATES = ("zones3", "zones3_new_template")


def _real_masks(new_template):
    directory = zones_dir(new_template)
    if not os.path.isdir(directory):
        pytest.skip(f"zone masks not present: {directory}")
    masks = load_zone_masks(directory)
    if not masks:
        pytest.skip(f"no readable zone masks in {directory}")
    return masks


def _overlap_samples(masks, limit=400):
    """Deterministic sample of pixels where `LINE` AND some other mask match.

    Vectorized with numpy to FIND the pixels (450x696x38 booleans is cheap);
    only the sample is pushed back through `zones_at`, so the test stays fast.
    """
    names = [n for n, _ in masks]
    height = min(img.shape[0] for _, img in masks)
    width = min(img.shape[1] for _, img in masks)
    hits = np.stack([img[:height, :width] == 0 for _, img in masks])
    line_idx = names.index(LINE_ZONE)
    others = hits[[i for i in range(len(names)) if i != line_idx]].any(axis=0)
    ys, xs = np.where(hits[line_idx] & others)
    if len(ys) <= limit:
        picks = range(len(ys))
    else:
        picks = np.linspace(0, len(ys) - 1, limit).astype(int)  # even, stable
    return [(int(xs[i]), int(ys[i])) for i in picks], hits, names, width, height


@pytest.mark.parametrize("template", _TEMPLATES)
def test_real_masks_never_report_LINE_where_another_mask_matches(template):
    """The invariant the precedence exists for, checked against the shipped
    masks rather than synthetic ones: wherever the `LINE` mask overlaps any
    other mask, the click must resolve to that other mask.

    Also asserts the overlap is non-empty, so the test cannot pass vacuously
    if someone redraws the masks.
    """
    masks = _real_masks(template == "zones3_new_template")
    samples, hits, names, _, _ = _overlap_samples(masks)

    assert samples, f"{template}: LINE overlaps nothing — invariant untested"

    for x, y in samples:
        result = zones_at(masks, x, y)
        assert len(result) == 1, (template, x, y, result)
        assert result != [LINE_ZONE], (
            f"{template}: ({x}, {y}) reported LINE although "
            f"{[names[i] for i in np.where(hits[:, y, x])[0]]} match"
        )
        # The winner really does claim this pixel (no phantom answers).
        assert hits[names.index(result[0]), y, x]


@pytest.mark.parametrize("template", _TEMPLATES)
def test_real_masks_never_report_a_catch_all_over_a_real_zone(template):
    """Stronger form of the same rule: at any pixel some anatomical mask
    claims, the reported zone is an anatomical one — never BOX*, OUTSIDE,
    LINE or NN. (The shipped masks have zero real-vs-real overlap, so the
    reported zone is in fact the only real match.)"""
    masks = _real_masks(template == "zones3_new_template")
    names = [n for n, _ in masks]
    height = min(img.shape[0] for _, img in masks)
    width = min(img.shape[1] for _, img in masks)
    hits = np.stack([img[:height, :width] == 0 for _, img in masks])
    real = [i for i, n in enumerate(names) if not is_catch_all_zone(n)]
    catch_all = [i for i, n in enumerate(names) if is_catch_all_zone(n)]

    ys, xs = np.where(hits[real].any(axis=0) & hits[catch_all].any(axis=0))
    assert len(ys), f"{template}: no real/catch-all overlap — invariant untested"

    picks = np.linspace(0, len(ys) - 1, min(len(ys), 400)).astype(int)
    for i in picks:
        x, y = int(xs[i]), int(ys[i])
        result = zones_at(masks, x, y)
        assert not is_catch_all_zone(result[0]), (
            f"{template}: ({x}, {y}) reported the catch-all {result[0]} although "
            f"{[names[j] for j in real if hits[j, y, x]]} match"
        )


@pytest.mark.parametrize("template", _TEMPLATES)
def test_real_masks_unclaimed_pixels_are_NN(template):
    """The other end: the ~0.1% of the canvas no mask covers still yields the
    sentinel, and out-of-bounds clicks do too (this is what the GUI's WARN
    about an `NN` click reports on)."""
    masks = _real_masks(template == "zones3_new_template")
    height = min(img.shape[0] for _, img in masks)
    width = min(img.shape[1] for _, img in masks)
    hits = np.stack([img[:height, :width] == 0 for _, img in masks])

    ys, xs = np.where(~hits.any(axis=0))
    assert len(ys), f"{template}: every pixel is claimed — NN path untested"
    picks = np.linspace(0, len(ys) - 1, min(len(ys), 200)).astype(int)
    for i in picks:
        assert zones_at(masks, int(xs[i]), int(ys[i])) == [NO_ZONE]

    assert zones_at(masks, width + 10, height + 10) == [NO_ZONE]
    assert zones_at(masks, -1, -1) == [NO_ZONE]


# --- observability: an NN click must not be silent ------------------------

def _click_app(masks):
    """Minimum LabelingApp surface `on_diagram_click` touches, so the handler
    can be driven without a Tk root."""
    app = _stub_app(masks)
    app.video = SimpleNamespace(current_frame=41, frames={})
    app.diagram_scale = 1.0
    app.option_var_1 = SimpleNamespace(get=lambda: "RH")
    app.mark_bundle_changed = lambda: None
    app._render_diagram_dots = lambda: None
    # Unbound method on a stub: bind the real hit-test wrapper explicitly.
    app.find_image_with_white_pixel = (
        lambda x, y: LabelingApp.find_image_with_white_pixel(app, x, y)
    )
    return app


def _do_click(app, x, y):
    LabelingApp.on_diagram_click(app, SimpleNamespace(x=x, y=y), True)


def test_click_that_resolves_to_NN_is_logged_with_frame_limb_and_coords(caplog):
    """A click matching no mask still enters the dataset (as `NN`), so the GUI
    must SAY so — silently recording a defect is the failure mode this guards
    (no silent failures). The pure rule stays log-free; the warning
    lives at the call site, which is the only place that knows frame + limb."""
    app = _click_app([("A", _mask((0, 5, 0, 5)))])

    _do_click(app, 15, 15)  # outside A's black square -> NN

    records = [record for record in caplog.records if record.name == "labeling_app"]
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    message = records[0].getMessage()
    assert "NN" in message
    assert "frame=41" in message
    assert "limb=RH" in message
    assert "x=15.0" in message and "y=15.0" in message
    # The click was still recorded, warning or not.
    assert app.video.frames[41]["RH"]["Zones"] == [[NO_ZONE]]


def test_out_of_bounds_click_is_warned_too(caplog):
    app = _click_app([("A", _mask((0, 5, 0, 5)))])

    _do_click(app, 500, 500)

    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_click_that_resolves_to_a_zone_logs_no_NN_warning(caplog):
    app = _click_app([("A", _mask((0, 20, 0, 20)))])

    _do_click(app, 10, 10)

    assert not any(record.levelname == "WARNING" for record in caplog.records)
    assert app.video.frames[41]["RH"]["Zones"] == [["A"]]


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

    monkeypatch.setattr(labeling_app, "asset_path", lambda rel: str(zone_dir))
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
    monkeypatch.setattr(labeling_app, "asset_path", lambda rel: str(zone_dir))
    app = _loader_stub()

    LabelingApp._load_zone_masks(app)
    first = app._zone_masks
    _write_png(str(zone_dir), "LATER.png")  # would be picked up by a reload

    LabelingApp._load_zone_masks(app)

    assert app._zone_masks is first
    assert [name for name, _ in app._zone_masks] == ["FACE"]


def test_load_zone_masks_missing_directory_is_a_warned_noop(tmp_path, monkeypatch, caplog):
    missing = str(tmp_path / "nope")
    monkeypatch.setattr(labeling_app, "asset_path", lambda rel: missing)
    app = _loader_stub()

    LabelingApp._load_zone_masks(app)

    assert app._zone_masks == []
    assert any(
        record.levelname == "WARNING" and "Zones directory not found" in record.getMessage()
        for record in caplog.records
    )
