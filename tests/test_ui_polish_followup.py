"""Regression checks for the post-modernization layout polish."""

import inspect
from types import SimpleNamespace

import theme
from labeling_app import LabelingApp
from ui_components import _build_diagram_panel, build_ui


def test_limb_selector_is_centered_as_a_left_aligned_group():
    source = inspect.getsource(LabelingApp.rebuild_annotation_controls)

    assert "limb_selector_frame = ttk.Frame(self.mode_controls_frame)" in source
    assert 'limb_selector_frame.pack(anchor="n")' in source
    assert "ttk.Radiobutton(\n                    limb_selector_frame," in source
    assert ').pack(anchor="w")' in source


def test_border_hugs_video_image_instead_of_the_empty_stage():
    build_source = inspect.getsource(build_ui)
    display_source = inspect.getsource(LabelingApp.display_first_frame)

    assert "highlightbackground=theme.BORDER" not in build_source
    assert "highlightbackground=theme.BORDER" in display_source
    assert "highlightthickness=2" in display_source
    assert theme.BORDER == "#ced4da"


def test_general_and_limb_parameter_buttons_share_spacing_and_style():
    limb_source = inspect.getsource(LabelingApp.rebuild_annotation_controls)
    general_source = inspect.getsource(_build_diagram_panel)

    for name in ("limb_par1_btn", "limb_par2_btn", "limb_par3_btn"):
        assert f'self.{name}.pack(anchor="n", pady=4)' in limb_source
    for name in ("par1_btn", "par2_btn", "par3_btn"):
        assert f'app.{name}.pack(anchor="n", pady=4)' in general_source
    assert limb_source.count('style="StateNeutral.TButton"') == 3
    assert general_source.count('style="StateNeutral.TButton"') == 3


def test_note_editor_is_two_lines_and_buttons_share_one_row():
    source = inspect.getsource(_build_diagram_panel)

    assert "app.note_entry = tk.Text(" in source
    assert "width=30" in source
    assert "height=2" in source
    assert 'note_button_row = ttk.Frame(note_controls)' in source
    assert 'note_button_row.pack(fill="x"' in source
    assert 'uniform="note_actions"' in source
    assert 'app.save_note_button.grid(row=0, column=0, sticky="ew"' in source
    assert 'app.select_frame_button.grid(row=0, column=1, sticky="ew"' in source


def test_note_helpers_use_multiline_text_indices():
    calls = []

    class NoteWidget:
        def get(self, *args):
            calls.append(("get", args))
            return "first line\nsecond line"

        def delete(self, *args):
            calls.append(("delete", args))

        def insert(self, *args):
            calls.append(("insert", args))

    widget = NoteWidget()
    stub = SimpleNamespace(note_entry=widget)

    assert LabelingApp._get_note_entry_text(stub) == "first line\nsecond line"
    LabelingApp._clear_note_entry(stub)
    stub._clear_note_entry = lambda: LabelingApp._clear_note_entry(stub)
    LabelingApp._set_note_entry_text(stub, "saved\ntext")

    assert calls == [
        ("get", ("1.0", "end-1c")),
        ("delete", ("1.0", "end")),
        ("delete", ("1.0", "end")),
        ("insert", ("1.0", "saved\ntext")),
    ]
