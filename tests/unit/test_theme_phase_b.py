"""Headless checks for the Phase B ttkbootstrap theme helpers."""

import sys
from types import SimpleNamespace

import pytest

from gui import theme


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
