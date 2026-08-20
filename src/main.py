import sys

from labeling_app import LabelingApp


def _harden_console_encoding():
    """Make logging survive a redirected stdout on Windows.

    Attached to a real console, Python writes UTF-16 through the Win32 console
    API and anything prints fine. Redirect the process to a file or a pipe
    (`TinyTouch.exe > log.txt`, a CI runner, a wrapper script) and Python falls
    back to the LOCALE encoding — cp1252 / cp1250 — where a Czech note, an
    unusual character in a video path or a box-drawing character raises
    UnicodeEncodeError from inside `print`. That exception then surfaces
    wherever the log line happened to be, which has already cost one silent
    data-loss bug: a DEBUG line with a Unicode arrow raised inside the retired
    `unified_repo.load_unified_dataset`, whose `except Exception` guard read it
    as "unreadable CSV" and migrated zero frames.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception as exc:  # pragma: no cover - non-reconfigurable stream
            print(f"WARN: could not switch {stream!r} to UTF-8: {exc!r}")


if __name__ == "__main__":
    _harden_console_encoding()
    print("Labeling App starting...")
    app = LabelingApp()
    app.mainloop()
