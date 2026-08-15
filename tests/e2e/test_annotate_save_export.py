"""
The full researcher round trip through the REAL GUI:

    Load Video -> pick a limb -> step frames -> left-click an onset ->
    step frames -> right-click an offset -> Save -> close

and then, from OUTSIDE the app, the two artifacts that matter:

  * `data/<video>/state/<video>.db`      — the source of truth, read back with a
                                          plain read-only sqlite3 connection so
                                          the assertions do not depend on the
                                          app's own reader;
  * `data/<video>/export/<video>_export.csv` — the frozen publication contract,
                                          read back with `csv.DictReader`.

HOW IT DRIVES THE APP (event vs direct call)
--------------------------------------------
Everything is a real Tk event or a real widget command; no handler is called
directly:

  * "Load Video", ">", "Save" go through `Button.invoke()`, i.e. the widget's
    own `command` — a control wired to the wrong callback fails the test.
  * The limb selector goes through `Radiobutton.invoke()`, so both the
    `option_var_1` write and the `on_radio_click` redraw happen for real.
  * The onset / offset are `<Button-1>` / `<Button-3>` events synthesized on
    `diagram_canvas` with `event_generate`, so the `bind()` table in
    `ui_components._build_diagram_panel` is under test, including the
    display -> data coordinate conversion (`diagram_scale` is 0.5 here, so the
    canvas pixel (96, 168) must land as data (192, 336)).
  * The canvas coordinate is chosen so the click lands inside zone mask "I":
    it is verified against `adapters.zone_masks` + `domain.touch.zones_at` in
    conftest, so a real mask regression shows up as a wrong `Zones` cell rather
    than as a silently-passing test.
  * The mode picker and the close confirmation are the app's own modal
    `Toplevel`s, driven by invoking their real buttons from an `after()` poller
    inside their `wait_window()` loops. Only the OS file picker
    (`filedialog.askopenfilename`) and the native `messagebox`es are stubbed —
    neither can be synthesized.

ONE Tk root for the whole test (see conftest's window discipline).
"""

import csv
import json
import os
import sqlite3

import pytest

from conftest import ZONE_I_CANVAS_XY, ZONE_I_DATA_XY, ZONE_I_NAME
from gui_driver import click_canvas, close, goto_frame, save, select_radio

pytestmark = pytest.mark.gui

ONSET_FRAME = 2
OFFSET_FRAME = 5
LIMB = "LH"
LIMB_RADIO_LABEL = "Left Hand"


def _read_only_rows(db_path, sql):
    """Query the state DB through a SEPARATE read-only connection.

    Deliberately not `SqliteRepository`: the point is to prove the bytes on disk
    are right, and the repository is Tk-thread-bound anyway.
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _export_row(csv_path, frame):
    with open(csv_path, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["Frame"]) == frame:
                return row
    raise AssertionError(f"frame {frame} missing from {csv_path}")


def test_annotate_save_export_round_trip(loaded_app, workspace):
    app = loaded_app
    paths = workspace.project("tiny")

    assert app.video is not None
    assert app.video_name == "tiny"
    assert app.video.total_frames == 9, "the synthetic video should be 10 frames"
    assert os.path.isdir(paths.frames_dir)
    assert len(os.listdir(paths.frames_dir)) == 10, "frame extraction did not run"
    assert workspace.errors() == [], f"the load raised errors: {workspace.errors()}"

    # --- annotate ------------------------------------------------------------
    select_radio(app, LIMB_RADIO_LABEL)
    assert app.option_var_1.get() == LIMB

    goto_frame(app, ONSET_FRAME)
    click_canvas(app.diagram_canvas, "<Button-1>", *ZONE_I_CANVAS_XY)

    goto_frame(app, OFFSET_FRAME)
    click_canvas(app.diagram_canvas, "<Button-3>", *ZONE_I_CANVAS_XY)

    # In memory first: if the coordinate conversion or the mask lookup broke,
    # say so here rather than in an opaque CSV diff.
    expected_x, expected_y = ZONE_I_DATA_XY
    for frame, onset in ((ONSET_FRAME, "ON"), (OFFSET_FRAME, "OFF")):
        record = app.video.frames[frame][LIMB]
        assert record["Onset"] == onset
        assert record["X"] == [expected_x] and record["Y"] == [expected_y]
        assert record["Zones"] == [[ZONE_I_NAME]]
        assert app.video.frames[frame]["Changed"] is True

    # --- save ----------------------------------------------------------------
    save(app)
    assert workspace.errors() == [], f"the save raised errors: {workspace.errors()}"
    # The dirty flags are cleared only for frames that survived the export.
    assert all(not bundle["Changed"] for bundle in app.video.frames.values())

    # --- the state DB, read from outside the app -----------------------------
    assert os.path.exists(paths.state_db), f"no state DB at {paths.state_db}"
    assert _read_only_rows(paths.state_db, "SELECT frame FROM frames ORDER BY frame") == [
        (ONSET_FRAME,), (OFFSET_FRAME,)
    ], "only the two annotated frames should exist in the store"
    assert _read_only_rows(
        paths.state_db, "SELECT frame, limb, onset FROM limb_records ORDER BY frame"
    ) == [(ONSET_FRAME, LIMB, "ON"), (OFFSET_FRAME, LIMB, "OFF")]
    assert _read_only_rows(
        paths.state_db,
        "SELECT frame, limb, click_index, x, y, zones FROM clicks ORDER BY frame",
    ) == [
        (ONSET_FRAME, LIMB, 0, expected_x, expected_y, json.dumps([ZONE_I_NAME])),
        (OFFSET_FRAME, LIMB, 0, expected_x, expected_y, json.dumps([ZONE_I_NAME])),
    ]
    assert _read_only_rows(
        paths.state_db, "SELECT value FROM meta WHERE key = 'video_name'"
    ) == [("tiny",)]

    # --- the export CSV, the published contract ------------------------------
    assert os.path.exists(paths.export_csv), f"no export at {paths.export_csv}"
    zones_cell = json.dumps([[ZONE_I_NAME]])
    for frame, onset in ((ONSET_FRAME, "ON"), (OFFSET_FRAME, "OFF")):
        row = _export_row(paths.export_csv, frame)
        assert row[f"{LIMB}_Onset"] == onset
        assert row[f"{LIMB}_X"] == str(expected_x)
        assert row[f"{LIMB}_Y"] == str(expected_y)
        assert row[f"{LIMB}_Zones"] == zones_cell
        # No other limb may have picked the click up.
        for other in ("LL", "RH", "RL"):
            assert row[f"{other}_Onset"] == ""
            assert row[f"{other}_Zones"] == "[]"

    # One row per frame, 0..total_frames, exactly as the legacy schema demands.
    untouched = _export_row(paths.export_csv, 0)
    assert untouched[f"{LIMB}_Onset"] == "" and untouched[f"{LIMB}_Zones"] == "[]"
    with open(paths.export_csv, "r", encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == app.video.total_frames + 1

    # The metadata sidecar is part of the same save unit of work.
    with open(paths.export_metadata, "r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    assert metadata["Video Name"] == "tiny"
    assert metadata["Labeling Mode"] == "Normal"
    assert metadata["Frame Rate"] == app.frame_rate

    # --- close ---------------------------------------------------------------
    # `on_close` saves again, checkpoints the labeling clock, closes the DB and
    # cancels its repeating timers. The buffering thread may legitimately have an
    # `after(0, ...)` in flight, so assert on the app's OWN repeating timer.
    still_scheduled = close(app)
    leaked = [entry for entry in still_scheduled if "periodic_print_dot" in entry[1]]
    assert leaked == [], f"the diagram refresh timer outlived the root: {leaked}"
    assert app.state_repo is None, "the state DB was not closed on exit"
    assert _read_only_rows(
        paths.state_db, "SELECT value FROM meta WHERE key = 'last_frame'"
    ) == [(str(OFFSET_FRAME),)], "the resume position was not persisted on close"
