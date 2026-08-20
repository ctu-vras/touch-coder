"""M6 regression guards for explicit UTF-8 text I/O.

The locale-dependent cases are red before the fix on cp1252 Windows.  They may
already pass on UTF-8-default platforms, but the legacy notes fallback remains
independently testable everywhere.

Working state lives in `state/<video>.db`. The legacy notes and
limb-parameter CSV readers were deleted in 9.0 with the rest of the migration,
so their encoding tests went with them; what remains is the CURRENT text I/O:
config.json and the SQLite store (UTF-8 by definition, re-asserted so the
guarantee is not just implied).
"""

import json

from adapters import config as config_utils
from adapters.sqlite_repo import SqliteRepository
from domain.model import empty_bundle


NOTE = "Dívá se"


def test_M6_config_reads_utf8(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_bytes(
        json.dumps({"parameter1": NOTE}, ensure_ascii=False).encode("utf-8")
    )
    monkeypatch.setattr(config_utils, "get_config_path", lambda: str(config_path))

    assert config_utils.load_config()["parameter1"] == NOTE


def test_M6_state_db_round_trips_utf8_notes_and_params(tmp_path):
    """The live store's equivalent: SQLite text is UTF-8, so a non-ASCII note
    and param state survive a close/reopen with no encoding negotiation."""
    db = str(tmp_path / "state" / "vid.db")
    bundle = empty_bundle()
    bundle["Note"] = NOTE + ", s čárkou"
    bundle["LH"]["LimbParams"] = {"Par1": NOTE}
    bundle["Changed"] = True

    first = SqliteRepository(db)
    try:
        first.save_frames({4: bundle}, total_frames=5)
    finally:
        first.close()

    second = SqliteRepository(db)
    try:
        loaded = second.load_frames()[4]
        assert loaded["Note"] == NOTE + ", s čárkou"
        assert loaded["LH"]["LimbParams"] == {"Par1": NOTE}
    finally:
        second.close()


# === log-line encoding (the arrow that ate a migration) =======================
#
# Attached to a real Windows console Python writes through the console API and
# any character prints. REDIRECT the process (`TinyTouch.exe > log.txt`, a CI
# runner, a wrapper script) and Python falls back to the locale encoding —
# cp1252 / cp1250 — where an unencodable character raises UnicodeEncodeError
# from inside `print()` itself.
#
# That is not cosmetic. `adapters.unified_repo.load_unified_dataset` wraps the
# journal read in `except Exception: return {}` ("assume there is no data"), and
# its own DEBUG line used to contain a Unicode arrow. On a redirected stdout the
# print raised, the guard read it as "unreadable CSV", and the one-time
# legacy -> SQLite migration imported ZERO frames before renaming the sources
# `*.migrated`. So: literals handed to `print()` stay inside cp1252.
#
# Only LITERALS are checked. Interpolated values (a Czech note, an exotic path)
# can still be unencodable; hardening those is `main._harden_console_encoding`'s
# job, not something a static check can promise.

import ast
import os

_SRC_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src")
)


def _print_literals(tree):
    """Every string constant that appears inside a `print(...)` call."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                yield inner.lineno, inner.value


def test_M6_print_literals_are_cp1252_safe():
    offenders = []
    for dirpath, _dirnames, filenames in os.walk(_SRC_ROOT):
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for lineno, text in _print_literals(tree):
                try:
                    text.encode("cp1252")
                except UnicodeEncodeError:
                    rel = os.path.relpath(path, _SRC_ROOT)
                    offenders.append(f"{rel}:{lineno}: {text!r}")
    assert offenders == [], (
        "these log literals cannot be written to a redirected stdout on "
        "Windows (cp1252); use ASCII in log text:\n  " + "\n  ".join(offenders)
    )
