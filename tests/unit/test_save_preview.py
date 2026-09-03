"""Every dirty frame must appear in the save preview, so the preview's line
count matches the dirty-frame count the repository reports after the save."""

from domain.model import empty_bundle, preview_lines_for_save


def _dirty_bundle():
    b = empty_bundle()
    b["Changed"] = True
    return b


def test_click_note_and_param_frames_render_their_content():
    frames = {}

    frames[3] = _dirty_bundle()
    frames[3]["RH"].update(X=[10], Y=[20], Onset="ON", Zones=[["Z"]])

    frames[5] = _dirty_bundle()
    frames[5]["Note"] = "kicked the toy"

    frames[7] = _dirty_bundle()
    frames[7]["Params"] = {"Par1": "ON"}

    lines = preview_lines_for_save(frames, total_frames=9)

    assert len(lines) == 3
    assert "RH: ON [['Z']]" in lines[0]
    assert 'Note="kicked the toy"' in lines[1]
    assert "Params[Par1:ON]" in lines[2]


def test_limb_param_only_frame_is_listed():
    frames = {4: _dirty_bundle()}
    frames[4]["LH"]["LimbParams"] = {"LP1": "ON", "LP2": None}

    lines = preview_lines_for_save(frames, total_frames=9)

    assert lines == ["frame=    4 | LH: LP[LP1:ON]"]


def test_frame_emptied_by_delete_is_listed_as_cleared():
    # A click followed by a delete leaves a dirty bundle with no content;
    # it is still persisted, so the preview must count it.
    frames = {6: _dirty_bundle()}

    lines = preview_lines_for_save(frames, total_frames=9)

    assert lines == ["frame=    6 | (cleared)"]


def test_clean_frames_stay_out_of_the_preview():
    frames = {2: empty_bundle()}
    frames[2]["RH"].update(X=[1], Y=[1], Onset="ON", Zones=[["Z"]])

    assert preview_lines_for_save(frames, total_frames=9) == []
