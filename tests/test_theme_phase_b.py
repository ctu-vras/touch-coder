"""Headless checks for the Phase B ttkbootstrap theme helpers."""

import sys
from types import SimpleNamespace

import pytest

import theme


class _FakeStyle:
    def __init__(self, theme):
        self.theme = theme
        self.configured = {}
        self.mapped = {}

    def configure(self, name, **kwargs):
        self.configured[name] = kwargs

    def map(self, name, **kwargs):
        self.mapped[name] = kwargs


class _FakeRoot:
    def __init__(self):
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)


@pytest.mark.parametrize(
    ("state", "expected_style"),
    [
        ("ON", "StateOn.TButton"),
        ("OFF", "StateOff.TButton"),
        (None, "StateNeutral.TButton"),
        ("None", "StateNeutral.TButton"),
        ("", "StateNeutral.TButton"),
    ],
)
def test_set_button_state_maps_all_supported_values(state, expected_style):
    button = SimpleNamespace(configure=lambda **kwargs: setattr(button, "style", kwargs["style"]))

    theme.set_button_state(button, state)

    assert button.style == expected_style


def test_init_style_registers_flat_styles_and_preserves_hover_text(monkeypatch):
    monkeypatch.setitem(sys.modules, "ttkbootstrap", SimpleNamespace(Style=_FakeStyle))
    root = _FakeRoot()

    style = theme.init_style(root)

    assert style.theme == theme.THEME_NAME
    assert root.options["bg"] == theme.SURFACE
    assert set(style.configured) == {
        "Tool.TButton",
        "StateOn.TButton",
        "StateOff.TButton",
        "StateNeutral.TButton",
    }
    assert style.configured["Tool.TButton"]["background"] == theme.NEUTRAL
    assert ("active", theme.TEXT) in style.mapped["Tool.TButton"]["foreground"]
    assert ("pressed", theme.TEXT) in style.mapped["Tool.TButton"]["foreground"]
