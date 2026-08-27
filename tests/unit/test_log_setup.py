import io
import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import log_setup
from adapters import config as config_adapter


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch):
    """Keep production handlers and exception hooks out of other tests."""
    log_setup._reset_for_tests()
    original_handlers = list(logging.root.handlers)
    original_level = logging.root.level
    original_sys_hook = sys.excepthook
    original_thread_hook = threading.excepthook
    yield
    log_setup._reset_for_tests()
    logging.root.handlers[:] = original_handlers
    logging.root.setLevel(original_level)
    sys.excepthook = original_sys_hook
    threading.excepthook = original_thread_hook


def _flush_handlers():
    for handler in logging.root.handlers:
        handler.flush()


def test_configure_is_idempotent_and_routes_levels(tmp_path, monkeypatch):
    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", console)

    first = log_setup.configure_logging(str(tmp_path))
    second = log_setup.configure_logging(str(tmp_path))
    logging.getLogger("example").debug("file detail")
    logging.getLogger("annot").info("f=12 LH click ON")
    _flush_handlers()

    assert second is first
    assert first.path is not None
    assert len(list((tmp_path / "logs").glob("tinytouch_*.log"))) == 1
    assert "file detail" not in console.getvalue()
    assert "f=12 LH click ON" in console.getvalue()
    contents = first.path.read_text(encoding="utf-8")
    assert contents.count("file detail") == 1
    assert contents.count("f=12 LH click ON") == 1


def test_console_uses_current_stdout_and_environment_override(tmp_path, monkeypatch):
    configured_stream = io.StringIO()
    current_stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", configured_stream)
    log_setup.configure_logging(str(tmp_path))
    monkeypatch.setenv("TINYTOUCH_LOG_LEVEL", "debug")
    log_setup.apply_config("WARNING", 20)
    monkeypatch.setattr(sys, "stdout", current_stream)

    logging.getLogger("example").debug("visible detail")

    assert configured_stream.getvalue() == ""
    assert "visible detail" in current_stream.getvalue()


def test_unicode_is_preserved_in_file_and_safe_for_limited_console(tmp_path, monkeypatch):
    class AsciiConsole(io.StringIO):
        encoding = "ascii"

        def write(self, value):
            value.encode(self.encoding)
            return super().write(value)

    console = AsciiConsole()
    monkeypatch.setattr(sys, "stdout", console)
    session = log_setup.configure_logging(str(tmp_path))

    logging.getLogger("annot").info("note=%r", "Pohled ěščřž")
    _flush_handlers()

    assert "Pohled ěščřž" in session.path.read_text(encoding="utf-8")
    assert r"Pohled \u011b\u0161\u010d\u0159\u017e" in console.getvalue()


def test_apply_config_prunes_only_matching_old_session_files(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "tinytouch_2026-01-01_000000_000_1.log"
    newer = logs / "tinytouch_2026-01-02_000000_000_1.log"
    unrelated = logs / "keep-me.log"
    old.write_text("old")
    newer.write_text("newer")
    unrelated.write_text("other")
    session = log_setup.configure_logging(str(tmp_path))

    log_setup.apply_config("INFO", 2)

    matching = list(logs.glob("tinytouch_*.log"))
    assert set(matching) == {newer, session.path}
    assert unrelated.exists()


def test_directory_failure_keeps_console_logging(tmp_path, monkeypatch):
    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setattr(log_setup, "_candidate_log_directories", lambda app_dir: [tmp_path / "a", tmp_path / "b"])
    monkeypatch.setattr(log_setup, "_open_file_handler", lambda directory: (_ for _ in ()).throw(OSError("denied")))

    session = log_setup.configure_logging(str(tmp_path))
    logging.getLogger("example").warning("still alive")

    assert session.path is None
    assert log_setup.current_log_path() is None
    assert "could not create a session log" in console.getvalue()
    assert "still alive" in console.getvalue()


def test_process_hooks_are_idempotent_and_log_worker_traceback(tmp_path):
    session = log_setup.configure_logging(str(tmp_path))
    log_setup.install_process_exception_hooks()
    sys_hook = sys.excepthook
    thread_hook = threading.excepthook
    log_setup.install_process_exception_hooks()

    try:
        raise RuntimeError("worker failed")
    except RuntimeError:
        exc_type, exc_value, exc_tb = sys.exc_info()
    threading.excepthook(SimpleNamespace(
        exc_type=exc_type,
        exc_value=exc_value,
        exc_traceback=exc_tb,
        thread=SimpleNamespace(name="decoder-2"),
    ))
    _flush_handlers()

    assert sys.excepthook is sys_hook
    assert threading.excepthook is thread_hook
    contents = session.path.read_text(encoding="utf-8")
    assert "decoder-2" in contents
    assert "RuntimeError: worker failed" in contents


def test_tk_hook_logs_and_shows_only_one_dialog(tmp_path, monkeypatch):
    session = log_setup.configure_logging(str(tmp_path))
    shown = []
    monkeypatch.setattr(log_setup, "_show_tk_crash_dialog", lambda root, message: shown.append(message))
    root = SimpleNamespace(_closing=False)
    log_setup.install_tk_exception_hook(root)
    hook = root.report_callback_exception
    log_setup.install_tk_exception_hook(root)

    for message in ("first callback", "second callback"):
        try:
            raise ValueError(message)
        except ValueError:
            hook(*sys.exc_info())
    _flush_handlers()

    contents = session.path.read_text(encoding="utf-8")
    assert "ValueError: first callback" in contents
    assert "ValueError: second callback" in contents
    assert len(shown) == 1
    assert str(session.path) in shown[0]


def test_session_header_and_footer_include_diagnostics(tmp_path, monkeypatch):
    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", console)
    session = log_setup.configure_logging(str(tmp_path))
    config = SimpleNamespace(
        new_template=False, diagram_scale=0.5, dot_size=10.0,
        video_downscale=1.0, jump_seconds=0.28, realtime_arrow_hold=False,
        perf_enabled=False, perf_log_every_s=2.0, perf_log_top_n=10,
        log_level_console="INFO", log_keep_files=20,
    )
    log_setup.log_session_header(version="9.0.0", config=config, app_dir=str(tmp_path))
    logging.getLogger("example").warning("oddity")
    logging.getLogger("example").error("failure")
    log_setup.log_session_footer()
    _flush_handlers()

    contents = session.path.read_text(encoding="utf-8")
    assert "TinyTouch 9.0.0 starting" in contents
    assert "jump_seconds=0.28" in contents
    assert "session ended after" in contents
    assert "1 warning, 1 error" in contents
    assert "log saved:" in console.getvalue()


def test_open_logs_folder_uses_session_directory(tmp_path, monkeypatch):
    session = log_setup.configure_logging(str(tmp_path))
    opened = []
    monkeypatch.setattr(log_setup, "_open_directory", lambda path: opened.append(path))

    log_setup.open_logs_folder()

    assert opened == [session.path.parent]


def test_packaged_windows_prefers_local_app_data_then_app_dir(tmp_path, monkeypatch):
    local = tmp_path / "local"
    app_dir = tmp_path / "install"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    assert log_setup._candidate_log_directories(str(app_dir)) == [
        local / "TinyTouch" / "logs",
        app_dir / "logs",
    ]


def test_app_config_validates_logging_options(monkeypatch, caplog):
    monkeypatch.setattr(config_adapter, "load_config", lambda: {
        "log_level_console": "verbose",
        "log_keep_files": 0,
    })

    with caplog.at_level(logging.WARNING):
        config = config_adapter.load_app_config()

    assert config.log_level_console == "INFO"
    assert config.log_keep_files == 20
    assert "Invalid log_level_console" in caplog.text
    assert "Invalid log_keep_files" in caplog.text
