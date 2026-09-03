"""Unit tests for the pure layout rules in `adapters.plotting`: heatmap sizing
(keeps a ~38-zone transition matrix readable and a 3-zone one from being a
postage stamp) and the mirrored on-screen limb order."""
import pytest

from adapters.plotting import (
    HEATMAP_CELL_PX,
    HEATMAP_CHROME_PX,
    HEATMAP_MAX_HEIGHT_PX,
    HEATMAP_MIN_HEIGHT_PX,
    display_limb_order,
    heatmap_height,
)


def test_linear_in_the_working_range():
    # 38 zones (the real zones3 axis) sits inside the clamp band.
    assert heatmap_height(38) == 38 * HEATMAP_CELL_PX + HEATMAP_CHROME_PX


def test_clamps_small_and_large_matrices():
    assert heatmap_height(0) == HEATMAP_MIN_HEIGHT_PX
    assert heatmap_height(3) == HEATMAP_MIN_HEIGHT_PX
    assert heatmap_height(1000) == HEATMAP_MAX_HEIGHT_PX


def test_monotonic_non_decreasing():
    heights = [heatmap_height(n) for n in range(0, 120)]
    assert heights == sorted(heights)


def test_display_limb_order_mirrors_pairs():
    # The diagrams face the viewer: subject's right limbs on the viewer's left.
    assert display_limb_order(("LH", "RH", "LL", "RL")) == ["RH", "LH", "RL", "LL"]


def test_display_limb_order_keeps_unknown_limbs():
    assert display_limb_order(("LH", "XX", "RH")) == ["RH", "LH", "XX"]


@pytest.mark.parametrize("n", [1, 10, 38, 60])
def test_rows_never_thinner_than_before(n):
    """Guard the point of the change: inside the clamp band each zone row gets
    its full cell pitch (the old fixed 1000px gave ~21px rows at 38 zones)."""
    height = heatmap_height(n)
    if HEATMAP_MIN_HEIGHT_PX < height < HEATMAP_MAX_HEIGHT_PX:
        assert (height - HEATMAP_CHROME_PX) / n == HEATMAP_CELL_PX
