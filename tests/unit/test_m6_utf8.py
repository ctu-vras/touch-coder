"""M6 regression guards for explicit UTF-8 text I/O.

The locale-dependent cases are red before the fix on cp1252 Windows.  They may
already pass on UTF-8-default platforms, but the legacy notes fallback remains
independently testable everywhere.

Working state moved into `state/<video>.db`, so the notes and limb-parameter
CSVs no longer have writers — they are migration inputs only. The reader-side
encoding invariants still matter (those files are exactly what the migration
must decode), so they are asserted against hand-built bytes instead of a
round-trip through a retired writer. SQLite stores text as UTF-8 by definition,
which is re-asserted on the live path so the guarantee is not just implied.
"""

import json

from adapters import config as config_utils
from adapters.sqlite_repo import SqliteRepository
from adapters.unified_repo import load_limb_parameters, load_notes_csv
from domain.model import empty_bundle


NOTE = "Dívá se"


def test_M6_config_reads_utf8(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_bytes(
        json.dumps({"parameter1": NOTE}, ensure_ascii=False).encode("utf-8")
    )
    monkeypatch.setattr(config_utils, "get_config_path", lambda: str(config_path))

    assert config_utils.load_config()["parameter1"] == NOTE


def test_M6_notes_utf8_and_cp1252_fallback(tmp_path, capsys):
    utf8_path = tmp_path / "utf8_notes.csv"
    cp1252_path = tmp_path / "legacy_notes.csv"
    csv_text = f"Frame,Note\r\n7,{NOTE}\r\n"
    utf8_path.write_bytes(csv_text.encode("utf-8"))
    cp1252_path.write_bytes(csv_text.encode("cp1252"))

    assert load_notes_csv(utf8_path) == {7: NOTE}
    assert load_notes_csv(cp1252_path) == {7: NOTE}

    warning = capsys.readouterr().out
    assert str(cp1252_path) in warning
    assert "retrying as cp1252" in warning


def test_M6_limb_parameters_csv_is_read_as_utf8(tmp_path):
    """REPLACES the round-trip through `save_limb_parameters` (deleted with the
    other state writers — it had no app caller). The invariant that mattered was
    always the READ: a limb-parameters CSV holding non-ASCII must decode as
    UTF-8 rather than the Windows ANSI codepage.
    """
    path = tmp_path / "limb_parameters.csv"
    path.write_bytes(
        f"Limb,Frame,Parameter,State\r\nLH,4,Parameter_1,{NOTE}\r\n".encode("utf-8")
    )

    parameter1, parameter2, parameter3 = load_limb_parameters(path)

    assert parameter1 == {("LH", 4): NOTE}
    assert parameter2 == {}
    assert parameter3 == {}


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


def test_M6_legacy_notes_table_round_trips_utf8(tmp_path):
    """The migrated notes sidecar keeps its diacritics inside the DB."""
    repo = SqliteRepository(str(tmp_path / "state" / "vid.db"))
    try:
        with repo.transaction():
            repo.import_legacy_notes({7: NOTE})

        assert repo.load_legacy_notes() == {7: NOTE}
    finally:
        repo.close()
