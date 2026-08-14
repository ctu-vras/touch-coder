"""
C2 — pure-logic guard for `LabelingApp._entry_has_focus`.

C2's fix routes every global nav key (arrows / Shift-arrows / space / `d`)
through a guard that early-returns while the Note entry holds keyboard focus,
so typing a note no longer toggles playback, jumps frames, or deletes dots.

The guard's predicate is `_entry_has_focus`. The full event-routing behaviour
is GUI-only and verified by a manual checklist (see the fix plan) — it is not
robustly reproducible headless. But the predicate itself is pure: it depends
only on `self.note_entry` and `self.focus_get()`. We exercise it by calling the
unbound method on a lightweight stub `self`, without a running Tk root.

Run:  uv run pytest tests/ -k C2
"""
from types import SimpleNamespace

from labeling_app import LabelingApp


def _call(note_entry, focus_target, *, raises=False):
    """Invoke the unbound predicate on a stub `self`."""
    def focus_get():
        if raises:
            raise RuntimeError("focus_get failed (window teardown)")
        return focus_target

    stub = SimpleNamespace(note_entry=note_entry, focus_get=focus_get)
    return LabelingApp._entry_has_focus(stub)


def test_true_when_note_entry_is_focused():
    entry = object()
    assert _call(entry, entry) is True


def test_false_when_other_widget_is_focused():
    entry = object()
    other = object()
    assert _call(entry, other) is False


def test_false_when_nothing_is_focused():
    entry = object()
    assert _call(entry, None) is False


def test_false_when_note_entry_missing():
    # Transient during teardown / before the entry exists.
    assert _call(None, None) is False


def test_fails_open_when_focus_get_raises():
    # Fail-open: nav works; leaking *into* the entry is impossible when it
    # isn't the focus.
    entry = object()
    assert _call(entry, entry, raises=True) is False
