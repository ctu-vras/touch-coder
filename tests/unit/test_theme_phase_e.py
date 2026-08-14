"""Headless regression checks for the timeline canvas painters."""

from contextlib import nullcontext
from types import SimpleNamespace

import theme
from labeling_app import LabelingApp


class RecordingCanvas:
    def __init__(self, width=200, height=30):
        self.width = width
        self.height = height
        self.calls = []
        self._next_id = 1

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def _record_item(self, kind, args, kwargs):
        item_id = self._next_id
        self._next_id += 1
        self.calls.append((kind, args, kwargs, item_id))
        return item_id

    def delete(self, *args):
        self.calls.append(("delete", args, {}, None))

    def create_rectangle(self, *args, **kwargs):
        return self._record_item("rectangle", args, kwargs)

    def create_line(self, *args, **kwargs):
        return self._record_item("line", args, kwargs)

    def create_polygon(self, *args, **kwargs):
        return self._record_item("polygon", args, kwargs)

    def coords(self, *args):
        self.calls.append(("coords", args, {}, None))

    def tag_raise(self, *args):
        self.calls.append(("raise", args, {}, None))


def _timeline_app(canvas, video):
    return SimpleNamespace(
        video=video,
        timeline_canvas=canvas,
        timeline2_canvas=canvas,
        option_var_1=SimpleNamespace(get=lambda: "RH"),
        perf=SimpleNamespace(time=lambda _name: nullcontext()),
        _assert_ui_thread=lambda: None,
        parameter_color_at_frame=lambda _frame: None,
        limb_parameter_colors_at_frame=lambda _frame: (None, None, None),
        _update_timeline_playhead=LabelingApp._update_timeline_playhead,
        _timeline_dirty=True,
        _timeline2_dirty=True,
        _timeline_last_zone=None,
        _timeline_last_limb=None,
        _timeline2_last_limb=None,
        _timeline_canvas_size=(0, 0),
        _timeline2_canvas_size=(0, 0),
        _timeline_playhead_id=None,
        _timeline2_playhead_id=None,
        color_during=theme.TL_DURING,
        is_touch_timeline=False,
    )


def test_playhead_helper_creates_and_moves_two_pixel_stem_with_cap():
    canvas = RecordingCanvas()

    item_ids = LabelingApp._update_timeline_playhead(canvas, None, 50, 1, 28)

    line = next(call for call in canvas.calls if call[0] == "line")
    cap = next(call for call in canvas.calls if call[0] == "polygon")
    assert line[1] == (50, 8, 50, 28)
    assert line[2] == {"fill": theme.PLAYHEAD, "width": 2}
    assert cap[1] == (46, 1, 54, 1, 50, 8)

    LabelingApp._update_timeline_playhead(canvas, item_ids, 75, 1, 28)
    moved = [call for call in canvas.calls if call[0] == "coords"]
    assert moved[-2][1][1:] == (75, 8, 75, 28)
    assert moved[-1][1][1:] == (71, 1, 79, 1, 75, 8)


def test_scrub_timeline_insets_interval_fill_from_semantic_ticks():
    canvas = RecordingCanvas(width=200, height=30)
    video = SimpleNamespace(
        total_frames=100,
        current_frame=20,
        frames={
            10: {"RH": {"Onset": "ON"}},
            30: {"RH": {"Onset": "OFF"}},
        },
    )
    app = _timeline_app(canvas, video)

    LabelingApp.draw_timeline2(app)

    rectangles = [call for call in canvas.calls if call[0] == "rectangle"]
    assert rectangles[0][1] == (1, 1, 198, 28)
    interval = next(call for call in rectangles if call[2].get("fill") == theme.TL_DURING)
    onset_x = 1 + 0.10 * 197
    offset_x = 1 + 0.30 * 197
    assert interval[1] == (onset_x + 3, 2, offset_x - 3, 27)

    semantic_lines = [
        call for call in canvas.calls
        if call[0] == "line" and call[2].get("fill") in {theme.TL_ONSET_MARK, theme.TL_OFFSET_MARK}
    ]
    assert [call[1][0] for call in semantic_lines] == [onset_x, offset_x]
    assert all(call[2]["width"] == 2 for call in semantic_lines)


def test_zoom_timeline_uses_borderless_cells_and_one_shared_grid():
    canvas = RecordingCanvas(width=200, height=50)
    video = SimpleNamespace(
        total_frames=3,
        current_frame=1,
        current_frame_zone=0,
        number_frames_in_zone=4,
        dataRH={},
        dataLH={},
        dataRL={},
        dataLL={},
        data={},
        touch_to_next_zone=[False],
    )
    app = _timeline_app(canvas, video)

    LabelingApp.draw_timeline(app)

    rectangles = [call for call in canvas.calls if call[0] == "rectangle"]
    cell_fills = [call for call in rectangles if call[2].get("fill") == theme.TL_EMPTY]
    grids = [call for call in rectangles if call[2].get("outline") == theme.TL_OUTLINE]
    assert len(cell_fills) == 4
    assert all(call[2]["outline"] == "" for call in cell_fills)
    assert len(grids) == 1
    assert grids[0][1] == (1, 1, 198, 48)

    grid_lines = [
        call for call in canvas.calls
        if call[0] == "line" and call[2].get("fill") == theme.TL_OUTLINE
    ]
    assert len(grid_lines) == 3


def test_zoom_timeline_marks_frames_past_video_end_as_unavailable():
    canvas = RecordingCanvas(width=200, height=50)
    video = SimpleNamespace(
        total_frames=2,
        current_frame=2,
        current_frame_zone=0,
        number_frames_in_zone=5,
        dataRH={},
        dataLH={},
        dataRL={},
        dataLL={},
        data={},
        touch_to_next_zone=[False],
    )
    app = _timeline_app(canvas, video)

    LabelingApp.draw_timeline(app)

    unavailable_cells = [
        call for call in canvas.calls
        if call[0] == "rectangle" and call[2].get("fill") == theme.TL_UNAVAILABLE
    ]
    unavailable_marks = [
        call for call in canvas.calls
        if call[0] == "line" and call[2].get("fill") == theme.TL_UNAVAILABLE_MARK
    ]
    assert len(unavailable_cells) == 2
    assert len(unavailable_marks) == 2
    assert unavailable_cells[0][1][0] > 1
