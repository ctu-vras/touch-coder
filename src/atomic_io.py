"""
atomic_io.py
Crash-safe file writes (stdlib only — no project imports, so any module can
import this without introducing an import cycle).

The whole app used to serialize directly onto the live destination file
(open in "w" mode, then stream bytes). A kill / disk-full / OS crash during
that window left the file truncated — for the unified CSV (the source of truth)
that is silent, permanent annotation loss.

`atomic_write` removes that hazard: it streams into a sibling `<path>.tmp`,
flushes + fsyncs it, then `os.replace`s it onto `path`. `os.replace` is an
atomic same-volume rename on both Windows and POSIX, so a reader ever only sees
the previous complete file or the new complete file — never a half-written one.
On ANY exception before the replace the temp file is discarded and the original
is left untouched.
"""

import os
import shutil


def atomic_write(path, write_fn, *, encoding="utf-8", newline="", keep_backup=False):
    """Atomically (re)write `path`.

    `write_fn(f)` streams into a temp file; on success the temp is
    `os.replace()`d onto `path`. On ANY exception the original file is left
    untouched. `newline=""` matches pandas' own path-mode file handling so CSV
    bytes are byte-for-byte identical to a direct `df.to_csv(path)`.

    When `keep_backup=True` and `path` already exists, the previous good file is
    copied to `<path>.bak` right before the replace, giving a manual recovery
    point (used only by the two unified-CSV writers).
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding=encoding, newline=newline) as f:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        if keep_backup and os.path.exists(path):
            try:
                shutil.copyfile(path, path + ".bak")
            except OSError as backup_err:
                # Best-effort recovery point; never block the save for it.
                print(f"WARN: atomic_write backup failed for {path}: {backup_err}")
        os.replace(tmp, path)  # atomic on same volume (Win + POSIX)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)  # best-effort cleanup; never mask original error
        except OSError as cleanup_err:
            print(f"WARN: atomic_write temp cleanup failed for {tmp}: {cleanup_err}")
        raise
