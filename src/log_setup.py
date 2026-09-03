"""TinyTouch process-wide logging bootstrap.

Only the application composition root imports this module. Runtime modules use
normal ``logging.getLogger(...)`` instances and remain unaware of destinations.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic


DEFAULT_CONSOLE_LEVEL = "INFO"
DEFAULT_KEEP_FILES = 20
# Third-party loggers raised above the root DEBUG level: PIL logs every PNG
# chunk it decodes (hundreds of records per minute in the session file) and
# numexpr announces its thread pool at INFO before TinyTouch's own startup
# line. TinyTouch module records are never silenced here.
THIRD_PARTY_LOG_LEVELS = {
    "PIL": logging.INFO,
    "numexpr": logging.WARNING,
}
_SESSION_NAME = re.compile(
    r"^tinytouch_\d{4}-\d{2}-\d{2}_\d{6}_\d{3}_\d+\.log$"
)


@dataclass(frozen=True)
class LogSession:
    path: Path | None
    started_at: datetime


class _LazyStdoutHandler(logging.Handler):
    """Console handler that resolves and safely encodes stdout at emit time."""

    terminator = "\n"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = sys.stdout
            message = self.format(record) + self.terminator
            try:
                stream.write(message)
            except UnicodeEncodeError:
                encoding = getattr(stream, "encoding", None) or "ascii"
                safe_message = message.encode(encoding, errors="backslashreplace").decode(encoding)
                stream.write(safe_message)
            stream.flush()
        except Exception:
            self.handleError(record)


class _SeverityCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.warning_count = 0
        self.error_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.error_count += 1
        elif record.levelno >= logging.WARNING:
            self.warning_count += 1


_session: LogSession | None = None
_console_handler: _LazyStdoutHandler | None = None
_file_handler: logging.FileHandler | None = None
_counter_handler: _SeverityCounter | None = None
_process_hooks_installed = False
_original_sys_excepthook = None
_original_threading_excepthook = None
_tk_dialog_shown = False
_handling_exception = False
_started_monotonic: float | None = None


def _harden_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            # StringIO, detached streams, and some GUI launchers cannot be
            # reconfigured. _LazyStdoutHandler still makes each emit safe.
            pass


def _candidate_log_directories(app_dir: str | None) -> list[Path]:
    application_dir = Path(app_dir or Path.cwd())
    if not getattr(sys, "frozen", False):
        return [application_dir / "logs"]

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        preferred = Path(local_app_data) / "TinyTouch" / "logs" if local_app_data else None
    else:
        state_home = os.environ.get("XDG_STATE_HOME")
        preferred = (
            Path(state_home) / "tinytouch" / "logs"
            if state_home
            else Path.home() / ".local" / "state" / "tinytouch" / "logs"
        )

    candidates = [preferred, application_dir / "logs"] if preferred else [application_dir / "logs"]
    # A custom app_dir can equal the preferred directory in tests/installations.
    return list(dict.fromkeys(candidates))


def _new_session_path(directory: Path) -> Path:
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d_%H%M%S_") + f"{now.microsecond // 1000:03d}"
    return directory / f"tinytouch_{timestamp}_{os.getpid()}.log"


def _open_file_handler(directory: Path) -> tuple[logging.FileHandler, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    path = _new_session_path(directory)
    handler = logging.FileHandler(path, encoding="utf-8", errors="backslashreplace")
    return handler, path


def configure_logging(app_dir: str | None = None, *, to_file: bool = True) -> LogSession:
    """Configure root logging once and return the active session."""
    global _session, _console_handler, _file_handler, _counter_handler, _started_monotonic

    if _session is not None:
        return _session

    _harden_console_encoding()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for name, level in THIRD_PARTY_LOG_LEVELS.items():
        logging.getLogger(name).setLevel(level)

    console = _LazyStdoutHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%H:%M:%S"))
    console._tinytouch_handler = True
    root.addHandler(console)
    _console_handler = console

    counter = _SeverityCounter()
    counter._tinytouch_handler = True
    root.addHandler(counter)
    _counter_handler = counter

    path = None
    failures = []
    if to_file:
        for directory in _candidate_log_directories(app_dir):
            try:
                file_handler, path = _open_file_handler(directory)
            except OSError as exc:
                failures.append(f"{directory}: {exc}")
                continue
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s [%(threadName)s] %(message)s",
                "%Y-%m-%d %H:%M:%S",
            ))
            file_handler._tinytouch_handler = True
            root.addHandler(file_handler)
            _file_handler = file_handler
            break

    started_at = datetime.now().astimezone()
    _started_monotonic = monotonic()
    _session = LogSession(path=path, started_at=started_at)
    if to_file and path is None:
        logging.getLogger(__name__).warning(
            "could not create a session log; continuing with console logging (%s)",
            "; ".join(failures) or "no writable log directory",
        )
    return _session


def _normalise_console_level(value: object) -> int:
    if isinstance(value, str):
        name = value.strip().upper()
        if name in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return getattr(logging, name)
    logging.getLogger(__name__).warning(
        "invalid console log level %r; using %s", value, DEFAULT_CONSOLE_LEVEL
    )
    return logging.INFO


def _normalise_keep_files(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    logging.getLogger(__name__).warning(
        "invalid log retention %r; keeping %d files", value, DEFAULT_KEEP_FILES
    )
    return DEFAULT_KEEP_FILES


def apply_config(console_level: str, keep_files: int) -> None:
    """Apply validated runtime options and prune completed old sessions."""
    level_value = os.environ.get("TINYTOUCH_LOG_LEVEL", console_level)
    if _console_handler is not None:
        _console_handler.setLevel(_normalise_console_level(level_value))
    retention = _normalise_keep_files(keep_files)
    _prune_session_files(retention)


def _prune_session_files(keep_files: int) -> None:
    if _session is None or _session.path is None:
        return
    current = _session.path
    try:
        candidates = [
            path for path in current.parent.iterdir()
            if path.is_file() and _SESSION_NAME.fullmatch(path.name)
        ]
        newest = sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    except OSError:
        logging.getLogger(__name__).warning("could not inspect old session logs", exc_info=True)
        return

    keep = set(newest[:keep_files])
    keep.add(current)
    # If current was not among the newest, retain it in place of the oldest
    # selected file so the requested bound still holds.
    while len(keep) > keep_files:
        removable = [path for path in keep if path != current]
        if not removable:
            break
        keep.remove(min(removable, key=lambda path: (path.stat().st_mtime_ns, path.name)))
    for path in candidates:
        if path in keep or path == current:
            continue
        try:
            path.unlink()
        except OSError:
            logging.getLogger(__name__).warning("could not remove old session log: %s", path)


def install_process_exception_hooks() -> None:
    global _process_hooks_installed, _original_sys_excepthook, _original_threading_excepthook
    if _process_hooks_installed:
        return
    _original_sys_excepthook = sys.excepthook
    _original_threading_excepthook = threading.excepthook

    def main_exception_hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            return _original_sys_excepthook(exc_type, exc_value, exc_traceback)
        if not _log_unhandled(
            logging.CRITICAL, "unhandled main-thread exception", exc_type, exc_value, exc_traceback
        ):
            _original_sys_excepthook(exc_type, exc_value, exc_traceback)

    def thread_exception_hook(args):
        thread_name = getattr(getattr(args, "thread", None), "name", "unknown")
        if not _log_unhandled(
            logging.ERROR,
            "unhandled exception in worker thread %s",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            thread_name,
        ):
            _original_threading_excepthook(args)

    sys.excepthook = main_exception_hook
    threading.excepthook = thread_exception_hook
    _process_hooks_installed = True


def _log_unhandled(level, message, exc_type, exc_value, exc_traceback, *args) -> bool:
    global _handling_exception
    if _handling_exception:
        return False
    _handling_exception = True
    try:
        logging.getLogger(__name__).log(
            level, message, *args, exc_info=(exc_type, exc_value, exc_traceback)
        )
        return True
    except Exception:
        return False
    finally:
        _handling_exception = False


def install_tk_exception_hook(root) -> None:
    """Log Tk callback failures and show at most one crash dialog per session."""
    if getattr(root, "_tinytouch_exception_hook_installed", False):
        return

    def report_callback_exception(exc_type, exc_value, exc_traceback):
        global _tk_dialog_shown
        logged = _log_unhandled(
            logging.CRITICAL, "unhandled Tk callback exception", exc_type, exc_value, exc_traceback
        )
        if not logged:
            _write_fallback("TinyTouch could not log a Tk callback exception\n")
        if _tk_dialog_shown or getattr(root, "_closing", False):
            return
        _tk_dialog_shown = True
        location = current_log_path()
        detail = (
            f"Diagnostic details were written to {location}. Please send that file "
            "when reporting the problem."
            if location
            else "Diagnostic details were written to the console instead."
        )
        message = (
            "TinyTouch encountered an error. If the application is still open, "
            f"save your work. {detail}"
        )
        try:
            _show_tk_crash_dialog(root, message)
        except Exception:
            _write_fallback(message + "\n")

    root.report_callback_exception = report_callback_exception
    root._tinytouch_exception_hook_installed = True


def _show_tk_crash_dialog(root, message: str) -> None:
    from tkinter import messagebox

    messagebox.showerror("TinyTouch error", message, parent=root)


def _write_fallback(message: str) -> None:
    try:
        sys.__stderr__.write(message)
        sys.__stderr__.flush()
    except Exception:
        pass


def log_session_header(*, version: str, config, app_dir: str) -> None:
    logger = logging.getLogger(__name__)
    logger.info("TinyTouch %s starting", version)
    logger.info("log: %s", current_log_path() or "console only")
    logger.debug(
        "runtime: os=%s python=%s mode=%s app_dir=%s log_dir=%s",
        platform.platform(),
        platform.python_version(),
        "frozen" if getattr(sys, "frozen", False) else "source",
        app_dir,
        _session.path.parent if _session and _session.path else None,
    )
    names = (
        "new_template", "diagram_scale", "dot_size", "video_downscale",
        "jump_seconds", "realtime_arrow_hold", "perf_enabled",
        "perf_log_every_s", "perf_log_top_n", "log_level_console", "log_keep_files",
    )
    logger.debug(
        "config: %s",
        " ".join(f"{name}={getattr(config, name, None)!r}" for name in names),
    )


def log_session_footer() -> None:
    if _session is None:
        return
    elapsed = max(0, int(monotonic() - (_started_monotonic or monotonic())))
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    duration = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
    warnings = _counter_handler.warning_count if _counter_handler else 0
    errors = _counter_handler.error_count if _counter_handler else 0
    logger = logging.getLogger(__name__)
    logger.info(
        "session ended after %s - %d warning%s, %d error%s",
        duration, warnings, "" if warnings == 1 else "s", errors, "" if errors == 1 else "s",
    )
    logger.info("log saved: %s", current_log_path() or "console only")


def current_log_path() -> str | None:
    if _session is None or _session.path is None:
        return None
    return str(_session.path)


def open_logs_folder() -> None:
    if _session is None or _session.path is None:
        raise RuntimeError("No log directory is available for this session")
    _open_directory(_session.path.parent)


def _open_directory(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _reset_for_tests() -> None:
    """Restore process state; intentionally private and used by isolated tests."""
    global _session, _console_handler, _file_handler, _counter_handler
    global _process_hooks_installed, _original_sys_excepthook, _original_threading_excepthook
    global _tk_dialog_shown, _handling_exception, _started_monotonic
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_tinytouch_handler", False):
            root.removeHandler(handler)
            handler.close()
    for name in THIRD_PARTY_LOG_LEVELS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    if _process_hooks_installed:
        sys.excepthook = _original_sys_excepthook
        threading.excepthook = _original_threading_excepthook
    _session = None
    _console_handler = None
    _file_handler = None
    _counter_handler = None
    _process_hooks_installed = False
    _original_sys_excepthook = None
    _original_threading_excepthook = None
    _tk_dialog_shown = False
    _handling_exception = False
    _started_monotonic = None
