"""M6 regression guards for explicit UTF-8 text I/O.

The locale-dependent cases are red before the fix on cp1252 Windows.  They may
already pass on UTF-8-default platforms, but the legacy notes fallback remains
independently testable everywhere.
"""

import json

from adapters import config as config_utils
from adapters.unified_repo import load_limb_parameters, load_notes_csv, save_limb_parameters


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


def test_M6_limb_parameters_roundtrip_utf8(tmp_path):
    path = tmp_path / "limb_parameters.csv"
    source = {
        "Parameter_1": {("LH", 4): NOTE},
        "Parameter_2": {},
        "Parameter_3": {},
    }

    save_limb_parameters(path, source)

    assert NOTE in path.read_bytes().decode("utf-8")
    parameter1, parameter2, parameter3 = load_limb_parameters(path)
    assert parameter1 == {("LH", 4): NOTE}
    assert parameter2 == {}
    assert parameter3 == {}
