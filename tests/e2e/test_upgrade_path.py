"""
The owner's ACTUAL upgrade path, end to end through the real GUI.

A researcher who has been labeling with a pre-8.1 build has a tree that is old
in TWO independent ways at once:

    Videos/tiny.mp4                                  <- pre-rename source folder
    Labeled_data/tiny/data/tiny_unified.csv          <- pre-rename output tree
    Labeled_data/tiny/data/tiny_clothes.txt              AND pre-SQLite state
    Labeled_data/tiny/data/tiny_last_position.json
    Labeled_data/tiny/data/tiny_metadata.json
    Labeled_data/tiny/data/tiny_notes.csv

Both migrations have to run, in the right order, on the same launch:

  1. `migration_service.migrate_layout()` — directory renames only:
     `Labeled_data/` -> `data/`, `<video>/data/` -> `<video>/state/`,
     `Videos/` -> `videos/`.
  2. `state_migration.migrate_state_to_sqlite()` — the legacy CSV/JSON state
     under the NEW `state/` folder into `state/tiny.db`, renaming every consumed
     source `*.migrated` and deleting nothing.

Both are unit/integration tested in isolation already. What is only testable
here is that they compose on a real launch: the layout pass runs from `main.py`
BEFORE the Tk root exists, the state import runs from inside `load_video`, and
the annotation the researcher recorded years ago has to come out the other end
intact and re-exportable.

HOW IT DRIVES THE APP
---------------------
`migrate_layout()` is invoked by the `app_factory` fixture in exactly the place
`src/main.py` invokes it (before `LabelingApp()`), because that IS the app's
startup sequence — the Tk controller deliberately knows nothing about the old
folder names. Everything after that is real GUI: "Load Video" via
`Button.invoke()`, the mode picker answered by invoking its own Continue button,
"Save" via `Button.invoke()`, and the close confirmation likewise. Only the OS
file picker is stubbed.

ONE Tk root (see conftest's window discipline).
"""

import csv
import json
import os
import shutil
import sqlite3

import pytest

from gui_driver import close, load_video, save

pytestmark = pytest.mark.gui

VIDEO = "tiny"
ONSET_FRAME = 3
OFFSET_FRAME = 6
LIMB = "LH"
CLICK_X, CLICK_Y = 192, 336
ZONE = "I"
LEGACY_NOTE = "stará poznámka ěščř"
LEGACY_LAST_FRAME = 4
LEGACY_LABELING_SECONDS = 1234.5
CLOTHES_DOT = (1, 110.0, 220.0, "I")


def _journal_cell(value):
    return '"' + value.replace('"', '""') + '"'


def _limb_blob(limb, xs, ys, onset, zones):
    return json.dumps({
        "X": list(xs), "Y": list(ys), "Onset": onset, "Bodypart": limb,
        "Zones": zones, "Touch": None,
    })


def _empty_blob(limb):
    return _limb_blob(limb, [], [], "", [])


def _journal_row(frame, note, params, limbs):
    cells = [
        str(frame),
        note,
        json.dumps(params),
        limbs.get("LH", _empty_blob("LH")),
        limbs.get("RH", _empty_blob("RH")),
        limbs.get("LL", _empty_blob("LL")),
        limbs.get("RL", _empty_blob("RL")),
    ]
    return ",".join(_journal_cell(cell) for cell in cells) + "\n"


def _seed_legacy_tree(root, source_video):
    """Write a pre-8.1 tree: old folder NAMES and pre-SQLite state FILES."""
    legacy_videos = os.path.join(root, "Videos")
    legacy_state = os.path.join(root, "Labeled_data", VIDEO, "data")
    os.makedirs(legacy_videos, exist_ok=True)
    os.makedirs(legacy_state, exist_ok=True)
    shutil.copyfile(source_video, os.path.join(legacy_videos, f"{VIDEO}.mp4"))

    with open(os.path.join(legacy_state, f"{VIDEO}_unified.csv"),
              "w", encoding="utf-8", newline="") as handle:
        handle.write("Frame,Note,Params,LH,RH,LL,RL\n")
        handle.write(_journal_row(
            ONSET_FRAME, LEGACY_NOTE, {"Par1": "ON"},
            {"LH": _limb_blob(LIMB, [CLICK_X], [CLICK_Y], "ON", [[ZONE]])},
        ))
        handle.write(_journal_row(
            OFFSET_FRAME, "", {},
            {"LH": _limb_blob(LIMB, [CLICK_X], [CLICK_Y], "OFF", [[ZONE]])},
        ))

    with open(os.path.join(legacy_state, f"{VIDEO}_clothes.txt"),
              "w", encoding="utf-8") as handle:
        handle.write("DiagramScale: 0.5\n")
        dot_id, x, y, zones = CLOTHES_DOT
        handle.write(f"Dot ID {dot_id}: X={x}, Y={y}, Zones={zones}\n")

    with open(os.path.join(legacy_state, f"{VIDEO}_last_position.json"),
              "w", encoding="utf-8") as handle:
        json.dump({"frame": LEGACY_LAST_FRAME, "total_frames": 9}, handle)

    with open(os.path.join(legacy_state, f"{VIDEO}_metadata.json"),
              "w", encoding="utf-8") as handle:
        json.dump({"Total Labeling Time (seconds)": LEGACY_LABELING_SECONDS}, handle)

    with open(os.path.join(legacy_state, f"{VIDEO}_notes.csv"),
              "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Frame", "Note"])
        writer.writerow([ONSET_FRAME, "sidecar note (quarantined)"])

    return legacy_state


def _read_only_rows(db_path, sql):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_pre_refactor_tree_is_migrated_and_survives(workspace, app_factory):
    root = str(workspace.root)
    _seed_legacy_tree(root, workspace.video)

    # `app_factory` runs migrate_layout() before building the root, exactly as
    # src/main.py does. Constructing the app is therefore migration step 1.
    app = app_factory()

    # --- migration 1: directory layout ---------------------------------------
    assert "Labeled_data" not in os.listdir(root), "the output root was not renamed"
    assert "Videos" not in os.listdir(root), "the videos root was not renamed"
    assert os.path.isdir(os.path.join(root, "videos"))
    video_dir = os.path.join(root, "data", VIDEO)
    assert os.path.isdir(os.path.join(video_dir, "state")), "state/ was not created"
    assert not os.path.isdir(os.path.join(video_dir, "data")), \
        "the pre-rename per-video working dir is still there"
    migrated_video = os.path.join(root, "videos", f"{VIDEO}.mp4")
    assert os.path.exists(migrated_video)

    # --- migration 2: legacy state files -> state/<video>.db -----------------
    # Triggered from inside load_video -> project_service.open_state.
    load_video(app, workspace, migrated_video)
    assert workspace.errors() == [], f"the load raised errors: {workspace.errors()}"

    paths = workspace.project(VIDEO)
    assert os.path.exists(paths.state_db), "no state DB was built from the legacy files"
    state_files = sorted(os.listdir(paths.state_dir))
    assert f"{VIDEO}.db" in state_files
    # Every consumed source is renamed, never deleted.
    for sidecar in (f"{VIDEO}_unified.csv", f"{VIDEO}_clothes.txt",
                    f"{VIDEO}_last_position.json", f"{VIDEO}_metadata.json",
                    f"{VIDEO}_notes.csv"):
        assert f"{sidecar}.migrated" in state_files, f"{sidecar} was not consumed"
        assert sidecar not in state_files, f"{sidecar} was left behind un-migrated"

    # --- the annotation survived intact --------------------------------------
    for frame, onset in ((ONSET_FRAME, "ON"), (OFFSET_FRAME, "OFF")):
        record = app.video.frames[frame][LIMB]
        assert record["Onset"] == onset
        assert record["X"] == [CLICK_X] and record["Y"] == [CLICK_Y]
        assert record["Zones"] == [[ZONE]]
    assert app.video.frames[ONSET_FRAME]["Note"] == LEGACY_NOTE
    assert app.video.frames[ONSET_FRAME]["Params"].get("Par1") == "ON"
    # A migrated frame must NOT come back dirty, or every open would rewrite the
    # whole export.
    assert all(not bundle["Changed"] for bundle in app.video.frames.values())

    # ... and so did the sidecar state around it.
    assert app.video.current_frame == LEGACY_LAST_FRAME, "resume position lost"
    assert app.labeling_timer.total_s == pytest.approx(LEGACY_LABELING_SECONDS)
    assert app.state_repo.load_clothes_rows() == [CLOTHES_DOT]
    assert app.state_repo.load_legacy_notes() == {ONSET_FRAME: "sidecar note (quarantined)"}
    assert str(app.cloth_btn.cget("state")) == "normal"

    # --- and it is re-exportable, which is what the researcher actually needs -
    save(app)
    assert workspace.errors() == [], f"the save raised errors: {workspace.errors()}"
    with open(paths.export_csv, "r", encoding="utf-8", newline="") as handle:
        rows = {int(row["Frame"]): row for row in csv.DictReader(handle)}
    assert rows[ONSET_FRAME][f"{LIMB}_Onset"] == "ON"
    assert rows[ONSET_FRAME][f"{LIMB}_X"] == str(CLICK_X)
    assert rows[ONSET_FRAME][f"{LIMB}_Zones"] == json.dumps([[ZONE]])
    assert rows[ONSET_FRAME]["Note"] == LEGACY_NOTE
    assert rows[ONSET_FRAME]["Parameter_1"] == "ON"
    assert rows[OFFSET_FRAME][f"{LIMB}_Onset"] == "OFF"

    with open(paths.export_metadata, "r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    assert metadata["Zones Covered With Clothes"] == [ZONE], \
        "the migrated clothes zones did not reach the export metadata"

    # The DB records what it was built from, so an orphaned file is
    # self-describing and the import can never silently run twice.
    assert _read_only_rows(
        paths.state_db, "SELECT value FROM meta WHERE key = 'migrated_sources'"
    ), "the migration provenance was not stamped"

    # --- close cleanly -------------------------------------------------------
    # NOTE: a SECOND launch (proving the DB wins and nothing is re-imported) is
    # deliberately NOT built here — `tests/integration/test_sqlite_migration.py`
    # pins that idempotence directly, and it is not worth a second Tk root.
    still_scheduled = close(app)
    assert [entry for entry in still_scheduled if "periodic_print_dot" in entry[1]] == []
    assert app.state_repo is None, "the state DB was not closed on exit"
