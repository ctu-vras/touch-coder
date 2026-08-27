import logging
from types import SimpleNamespace

import keyboard

import labeling_app
from domain.model import empty_bundle
from labeling_app import LabelingApp


def _messages(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "annot"]


def _base_app(frame=12, limb="LH"):
    return SimpleNamespace(
        video=SimpleNamespace(current_frame=frame, frames={}),
        option_var_1=SimpleNamespace(get=lambda: limb),
        mark_bundle_changed=lambda *_args: None,
        draw_timeline=lambda: None,
    )


def test_click_logs_one_completed_annotation_action(caplog):
    app = _base_app()
    app.diagram_scale = 1.0
    app.find_image_with_white_pixel = lambda _x, _y: ["Z"]
    app._render_diagram_dots = lambda: None

    with caplog.at_level(logging.INFO, logger="annot"):
        LabelingApp.on_diagram_click(app, SimpleNamespace(x=10, y=20), True)

    assert _messages(caplog) == ["f=12 LH click ON zones=['Z'] points=1"]


def test_successful_delete_logs_once_but_miss_does_not(caplog):
    app = _base_app()
    app.diagram_scale = 1.0
    app._render_diagram_dots = lambda: None
    bundle = empty_bundle()
    bundle["LH"].update({"X": [10], "Y": [20], "Zones": [["Z"]], "Onset": "ON"})
    app.video.frames[12] = bundle

    with caplog.at_level(logging.INFO, logger="annot"):
        LabelingApp.on_middle_click(app, SimpleNamespace(x=10, y=20))
        LabelingApp.on_middle_click(app, SimpleNamespace(x=100, y=200))

    assert _messages(caplog) == ["f=12 LH delete points=0"]


def test_global_and_limb_parameters_log_resulting_state(monkeypatch, caplog):
    app = _base_app(limb="RH")
    app.par1_btn = object()
    app.par2_btn = object()
    app.par3_btn = object()
    app.limb_par1_btn = object()
    app.limb_par2_btn = object()
    app.limb_par3_btn = object()
    monkeypatch.setattr(labeling_app.theme, "set_button_state", lambda *_args: None)

    with caplog.at_level(logging.INFO, logger="annot"):
        LabelingApp.parameter_dic_insert(app, 2)
        LabelingApp.toggle_limb_parameter(app, 1)

    assert _messages(caplog) == [
        "f=12 param P2 -> ON",
        "f=12 RH limbparam LP1 -> ON",
    ]


def test_changed_note_logs_full_text_once_and_unchanged_note_is_silent(
    monkeypatch, caplog
):
    app = _base_app()
    app._get_note_entry_text = lambda: "kicked\nthe toy"
    app.notify_bundle_changed = lambda *_args: None
    monkeypatch.setattr(keyboard, "press_and_release", lambda *_args: None)

    with caplog.at_level(logging.INFO, logger="annot"):
        LabelingApp.save_note(app)
        LabelingApp.save_note(app)

    assert _messages(caplog) == ["f=12 note -> 'kicked\\nthe toy'"]


def test_limb_selection_logs_only_a_real_change(caplog):
    selected = {"value": "LH"}
    app = SimpleNamespace(
        option_var_1=SimpleNamespace(get=lambda: selected["value"]),
        _logged_limb="LH",
        on_radio_click=lambda: None,
    )

    with caplog.at_level(logging.INFO, logger="annot"):
        LabelingApp._on_limb_selected(app)
        selected["value"] = "RL"
        LabelingApp._on_limb_selected(app)

    assert _messages(caplog) == ["limb LH -> RL"]
