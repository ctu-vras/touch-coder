"""Behavioral checks for the note-entry helpers on the multiline Text widget."""

from types import SimpleNamespace

from labeling_app import LabelingApp


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
