"""Headless regression checks for the Phase D dialog refactor."""

import inspect
from types import SimpleNamespace

from labeling_app import LabelingApp


def test_progress_window_wrappers_delegate_without_changing_contract():
    calls = []
    sentinel = (object(), object())
    app = SimpleNamespace(
        _open_progress_window=lambda title, heading: calls.append((title, heading)) or sentinel
    )

    assert LabelingApp._open_frame_progress_window(app) is sentinel
    assert LabelingApp._open_video_copy_progress_window(app) is sentinel
    assert LabelingApp._open_data_progress_window(app) is sentinel
    assert calls == [
        ("Preparing Frames", "Preparing frames..."),
        ("Copying Video", "Copying video to project..."),
        ("Loading Data", "Loading labeled data..."),
    ]


def test_loading_and_saving_progress_bars_use_green_success_style():
    loading_source = inspect.getsource(LabelingApp._open_progress_window)
    saving_source = inspect.getsource(LabelingApp._run_export_with_progress)

    assert 'bootstyle="success-striped"' in loading_source
    assert 'bootstyle="success-striped"' in saving_source
    assert 'bootstyle="info-striped"' not in loading_source + saving_source
