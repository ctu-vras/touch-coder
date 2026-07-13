"""Headless checks for the Phase C dot-sprite helper."""

from types import SimpleNamespace

import pytest

import config_utils
import theme
from cloth_app import ClothApp
from labeling_app import LabelingApp


@pytest.fixture(autouse=True)
def _isolated_dot_cache():
    theme._dot_cache.clear()
    yield
    theme._dot_cache.clear()


def test_dot_sprite_is_antialiased_sized_and_cached(monkeypatch):
    monkeypatch.setattr(theme.ImageTk, "PhotoImage", lambda image: image)

    sprite = theme.dot_sprite(theme.DOT_ONSET, 6)

    assert sprite.size == (16, 16)
    assert sprite.getpixel((8, 8))[3] == 255
    assert sprite.getpixel((0, 0))[3] < 255
    assert theme.dot_sprite(theme.DOT_ONSET, 6.0) is sprite


def test_hollow_dot_sprite_keeps_its_center_transparent(monkeypatch):
    monkeypatch.setattr(theme.ImageTk, "PhotoImage", lambda image: image)

    sprite = theme.dot_sprite(theme.DOT_ONSET, 6, hollow=True)

    assert sprite.size == (16, 16)
    # LANCZOS may leave a near-transparent ringing pixel at the exact center.
    assert sprite.getpixel((8, 8))[3] <= 5


def test_clothes_dot_uses_sprite_and_preserves_stored_coordinates(monkeypatch):
    sprite = object()
    calls = []
    canvas = SimpleNamespace(
        create_image=lambda *args, **kwargs: calls.append((args, kwargs)) or 42
    )
    app = SimpleNamespace(canvas2=canvas, dot_radius=7, dots={})
    monkeypatch.setattr(theme, "dot_sprite", lambda color, radius: sprite)

    dot_id = ClothApp._create_dot(app, 12.5, 18.25)

    assert dot_id == 42
    assert calls == [((12.5, 18.25), {"image": sprite})]
    assert app.dots == {42: (12.5, 18.25)}


def test_diagram_dots_use_filled_and_hollow_sprites(monkeypatch):
    created = []
    sprite_calls = []
    canvas = SimpleNamespace(
        delete=lambda _tag: None,
        create_image=lambda *args, **kwargs: created.append((args, kwargs)),
    )
    frame = {"X": [10], "Y": [20], "Onset": "ON"}
    video = SimpleNamespace(
        data=True,
        dataRH={4: frame},
        dataLH={},
        dataRL={},
        dataLL={},
        current_frame=4,
        last_green=[(30, 40)],
    )
    app = SimpleNamespace(
        diagram_canvas=canvas,
        video=video,
        dot_size=6,
        diagram_scale=0.5,
        option_var_1=SimpleNamespace(get=lambda: "RH"),
        is_pose_mode=lambda: False,
        on_radio_click=lambda: None,
        find_last_green=lambda _data: None,
    )

    def fake_sprite(color, radius, hollow=False):
        result = object()
        sprite_calls.append((color, radius, hollow, result))
        return result

    monkeypatch.setattr(theme, "dot_sprite", fake_sprite)

    LabelingApp._render_diagram_dots(app)

    assert [(call[0], call[1], call[2]) for call in sprite_calls] == [
        (theme.DOT_ONSET, 6, False),
        (theme.DOT_ONSET, 6, True),
    ]
    assert [args for args, _kwargs in created] == [(5.0, 10.0), (15.0, 20.0)]
    assert [kwargs["image"] for _args, kwargs in created] == [
        sprite_calls[0][3],
        sprite_calls[1][3],
    ]


def test_loading_parameter_names_resets_ttk_state(monkeypatch):
    class Button:
        def __init__(self):
            self.options = {}

        def config(self, **kwargs):
            self.options.update(kwargs)

        def configure(self, **kwargs):
            self.options.update(kwargs)

    par_buttons = {index: Button() for index in (1, 2, 3)}
    limb_buttons = {index: Button() for index in (1, 2, 3)}
    video = SimpleNamespace()
    monkeypatch.setattr(config_utils, "load_config", lambda: {})

    config_utils.load_parameter_names_into(video, par_buttons, limb_buttons)

    for button in (*par_buttons.values(), *limb_buttons.values()):
        assert button.options["style"] == "StateNeutral.TButton"
        assert "bg" not in button.options
