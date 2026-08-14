"""M2 regression tests for frame-addressed dirty marking."""

from types import SimpleNamespace

from labeling_app import LabelingApp


def _stub(current_frame, frames):
    return SimpleNamespace(
        video=SimpleNamespace(current_frame=current_frame, frames=frames),
        _timeline_dirty=False,
        _timeline2_dirty=False,
    )


def test_M2_honors_explicit_index():
    frames = {10: {}, 5: {}}
    stub = _stub(current_frame=10, frames=frames)

    LabelingApp.mark_bundle_changed(stub, 5)

    assert frames[5]["Changed"] is True
    assert "Changed" not in frames[10]
    assert stub._timeline_dirty is True
    assert stub._timeline2_dirty is True


def test_M2_index_zero_is_honored():
    frames = {10: {}, 0: {}}
    stub = _stub(current_frame=10, frames=frames)

    LabelingApp.mark_bundle_changed(stub, 0)

    assert frames[0]["Changed"] is True
    assert "Changed" not in frames[10]
    assert stub._timeline_dirty is True
    assert stub._timeline2_dirty is True


def test_M2_default_marks_current_frame():
    frames = {10: {}, 5: {}}
    stub = _stub(current_frame=10, frames=frames)

    LabelingApp.mark_bundle_changed(stub)

    assert frames[10]["Changed"] is True
    assert "Changed" not in frames[5]
    assert stub._timeline_dirty is True
    assert stub._timeline2_dirty is True
