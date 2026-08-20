"""
atomic_io.py
Crash-safe file writes (stdlib only — no project imports, so any module can
import this without introducing an import cycle).

The whole app used to serialize directly onto the live destination file
(open in "w" mode, then stream bytes). A kill / disk-full / OS crash during
that window left the file truncated — for a published export that is silent,
permanent corruption.

`atomic_write` removes that hazard: it streams into a sibling `<path>.tmp`,
flushes + fsyncs it, then `os.replace`s it onto `path`. `os.replace` is an
atomic same-volume rename on both Windows and POSIX, so a reader ever only sees
the previous complete file or the new complete file — never a half-written one.
On ANY exception before the replace the temp file is discarded and the original
is left untouched.

Live callers: `adapters.export_writer` (export CSV + metadata sidecar) and
`adapters.config` (config.json). Working state goes through SQLite, so this is
the only file-write primitive left. Its sibling `durable_append` was deleted with
the unified-CSV journal — appending is exactly what SQLite replaced — and the
`keep_backup` `.bak` path went with it (it existed only for the two journal
writers).
"""

import os


def atomic_write(path, write_fn, *, encoding="utf-8", newline=""):
    """Atomically (re)write `path`.

    `write_fn(f)` streams into a temp file; on success the temp is
    `os.replace()`d onto `path`. On ANY exception the original file is left
    untouched. `newline=""` matches pandas' own path-mode file handling so CSV
    bytes are byte-for-byte identical to a direct `df.to_csv(path)`.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding=encoding, newline=newline) as f:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on same volume (Win + POSIX)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)  # best-effort cleanup; never mask original error
        except OSError as cleanup_err:
            print(f"WARN: atomic_write temp cleanup failed for {tmp}: {cleanup_err}")
        raise
