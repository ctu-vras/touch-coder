"""M1 regression guards for canonical cleared limb-parameter state.

`toggle_limb_parameter` once stored the literal string `"None"` instead of a
real `None`, so both live paths that read a limb parameter back must normalize
it: the state DB on load and the exporter on write.

The two tests that pinned this through `load_unified_dataset` /
`import_unified_from_export` went with those readers in 9.0. The invariant did
not go anywhere, so it is pinned here on the rule itself plus the SQLite
round-trip that replaced the journal.
"""

import csv

from adapters.export_writer import export_from_unified
from adapters.sqlite_repo import SqliteRepository
from domain.model import _normalize_param_state, empty_bundle


def test_M1_normalize_param_state_rule():
    """The pure rule: only None / '' / the string 'None' clear a parameter."""
    assert _normalize_param_state("None") is None
    assert _normalize_param_state("") is None
    assert _normalize_param_state(None) is None
    assert _normalize_param_state("ON") == "ON"
    assert _normalize_param_state("OFF") == "OFF"


def test_M1_state_db_normalizes_none_string(tmp_path):
    """A bundle carrying the legacy `"None"` string must come back as a real
    None from the state DB — the live replacement for the journal loader."""
    bundle = empty_bundle()
    bundle["LH"]["LimbParams"] = {"Par1": "None", "Par2": "ON", "Par3": None}
    bundle["Changed"] = True

    db = str(tmp_path / "state" / "vid.db")
    repo = SqliteRepository(db)
    try:
        repo.save_frames({4: bundle}, total_frames=5)
    finally:
        repo.close()

    repo = SqliteRepository(db)
    try:
        limb_params = repo.load_frames()[4]["LH"]["LimbParams"]
    finally:
        repo.close()

    assert limb_params["Par1"] is None
    assert limb_params["Par2"] == "ON"
    assert limb_params["Par3"] is None


def test_M1_export_writes_empty_for_none_string(tmp_path):
    out = tmp_path / "vid_export.csv"
    bundle = empty_bundle()
    bundle["LH"]["LimbParams"] = {"Par1": "None"}

    export_from_unified(
        {0: bundle},
        str(out),
        program_version=7.8,
        video_name="vid",
        labeling_mode="Normal",
        frame_rate=30.0,
        clothes_list=None,
        total_frames=0,
    )

    with out.open(newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["LH_Parameter_1"] == ""
