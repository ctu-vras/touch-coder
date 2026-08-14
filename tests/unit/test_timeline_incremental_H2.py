"""H2 regression tests for incremental pose timeline state updates."""

from types import SimpleNamespace

from labeling_app import LabelingApp
from pose_mismatch_data import empty_pose_bundle
from pose_timeline import build_pose_timeline_state, update_pose_timeline_state


def _event_bundle(joint, event, *, scale=None):
    bundle = empty_pose_bundle()
    bundle["Joints"][joint]["Event"] = event
    if scale is not None:
        bundle["ScaleRaw"] = scale
        bundle["ScaleFactor"] = scale
        bundle["ScaleSet"] = True
    return bundle


def _frames():
    return {
        2: _event_bundle("L_WRIST", "ON"),
        8: _event_bundle("R_ANKLE", "ON", scale=1.1),
        17: _event_bundle("L_WRIST", "OFF"),
        31: _event_bundle("NECK", "ON", scale=0.9),
        46: _event_bundle("R_ANKLE", "OFF"),
    }


def test_H2_incremental_equals_full_rebuild():
    for from_frame in (1, 25, 49):
        frames = _frames()
        prior_state = build_pose_timeline_state(frames, 50)

        frames[from_frame] = _event_bundle("R_ELBOW", "ON", scale=1.2)
        incremental = update_pose_timeline_state(
            prior_state, frames, 50, from_frame
        )

        assert incremental == build_pose_timeline_state(frames, 50)


def test_H2_suffix_touches_only_from_frame_onward():
    class TrackingFrames(dict):
        def __init__(self, values):
            super().__init__(values)
            self.reads = []

        def get(self, key, default=None):
            self.reads.append(key)
            return super().get(key, default)

    original = _frames()
    prior_state = build_pose_timeline_state(original, 50)
    tracked = TrackingFrames(original)
    tracked[37] = _event_bundle("R_SHOULDER", "ON")

    update_pose_timeline_state(prior_state, tracked, 50, 37)

    assert tracked.reads
    assert min(tracked.reads) >= 37


def test_H2_pose_edit_keeps_cache_and_marks_earliest_dirty_suffix():
    cache = object()
    frames = {12: {}, 30: {}}
    stub = SimpleNamespace(
        annotation_mode="pose_3d",
        video=SimpleNamespace(current_frame=30, frames=frames),
        _timeline_dirty=False,
        _timeline2_dirty=False,
        _pose_timeline_state_cache=cache,
        _pose_state_dirty_from=20,
    )

    LabelingApp.mark_bundle_changed(stub, 12)

    assert frames[12]["Changed"] is True
    assert stub._pose_timeline_state_cache is cache
    assert stub._pose_state_dirty_from == 12
