"""Loading a SECOND video in the same session, through the real GUI.

The Load Video button used to be disabled after the first load because a
reload could mix two projects' data. These tests drive the re-enabled flow
end to end and prove the separation on disk and in memory:

  * annotations made on video A are saved by the unload and stay in A's DB;
  * video B starts from its own empty DB and an empty in-memory frames dict;
  * reloading A brings A's annotations back, untouched by B's;
  * cancelling either dialog leaves the currently open video fully alive.

ONE Tk root per test (see conftest's window discipline); the second "video"
is a byte-copy of the synthetic one under a different name, which is a
different project identity (projects are keyed by file basename).
"""

import shutil
import sqlite3

import pytest

from conftest import ZONE_I_CANVAS_XY
from gui_driver import (
    click_canvas,
    dismiss_dialog,
    goto_frame,
    load_video,
    pump,
    select_radio,
    wait_until,
)

pytestmark = pytest.mark.gui


def _rows(db_path, sql):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_second_video_loads_without_mixing_data(loaded_app, workspace, tmp_path):
    app = loaded_app  # video A ("tiny") loaded through the real button
    assert str(app.load_video_btn.cget("state")) != "disabled", (
        "the Load Video button must stay enabled after a load"
    )

    # --- annotate video A: LH onset on frame 2, left UNSAVED -------------------
    select_radio(app, "Left Hand")
    goto_frame(app, 2)
    click_canvas(app.diagram_canvas, "<Button-1>", *ZONE_I_CANVAS_XY)
    pump(app)
    assert app.video.frames[2]["LH"]["Onset"] == "ON"
    assert app.video.frames[2]["Changed"] is True

    # --- load video B in the same session ---------------------------------------
    second_source = tmp_path / "second.mp4"
    shutil.copy(workspace.video, second_source)
    load_video(app, workspace, second_source)

    assert app.video_name == "second"
    assert app.labeling_mode == "Normal"

    # A's unsaved click was persisted by the unload, into A's OWN database.
    paths_a = workspace.project("tiny")
    assert _rows(
        paths_a.state_db, "SELECT frame, limb, onset FROM limb_records"
    ) == [(2, "LH", "ON")]
    assert _rows(
        paths_a.state_db, "SELECT value FROM meta WHERE key = 'video_name'"
    ) == [("tiny",)]

    # B is a fresh project: empty DB, empty in-memory frames, nothing of A.
    paths_b = workspace.project("second")
    assert _rows(paths_b.state_db, "SELECT COUNT(*) FROM frames") == [(0,)]
    assert _rows(
        paths_b.state_db, "SELECT value FROM meta WHERE key = 'video_name'"
    ) == [("second",)]
    assert app.video.frames == {}, "video B inherited in-memory frames from A"
    assert app.video.current_frame == 0

    # The buffer serves B's frames (it was emptied before B was published).
    wait_until(app, lambda: 0 in app.frame_buffer, what="frame 0 of video B")

    # --- annotate B, then reload A ----------------------------------------------
    select_radio(app, "Right Hand")
    goto_frame(app, 3)
    click_canvas(app.diagram_canvas, "<Button-1>", *ZONE_I_CANVAS_XY)
    pump(app)
    assert app.video.frames[3]["RH"]["Onset"] == "ON"

    load_video(app, workspace, workspace.video)  # back to A

    assert app.video_name == "tiny"
    # A's annotation round-tripped; B's did not leak in.
    assert sorted(app.video.frames.keys()) == [2]
    assert app.video.frames[2]["LH"]["Onset"] == "ON"
    # A's resume position (frame 2, saved on the first unload) was restored.
    assert app.video.current_frame == 2
    # B's annotation was saved by ITS unload, into B's own database.
    assert _rows(
        paths_b.state_db, "SELECT frame, limb, onset FROM limb_records"
    ) == [(3, "RH", "ON")]

    assert workspace.errors() == [], f"reloading raised errors: {workspace.errors()}"


def test_cancelling_the_file_dialog_keeps_the_current_video(loaded_app, workspace):
    app = loaded_app
    frames_before = app.video.frames

    workspace.chosen_video = ""  # the user cancels the OS file picker
    mode_dialog = dismiss_dialog(app, "Select Mode", "Continue")
    app.load_video_btn.invoke()

    assert mode_dialog["clicked"] is True
    # The open session is completely untouched: same video, same repo, usable.
    assert app.video is not None and app.video_name == "tiny"
    assert app.video.frames is frames_before
    assert app.state_repo is not None
    goto_frame(app, 1)
    assert app.video.current_frame == 1
    assert workspace.errors() == []


def test_cancelling_the_mode_dialog_keeps_the_current_video(loaded_app, workspace):
    app = loaded_app
    workspace.chosen_video = str(workspace.video)

    # Close the mode picker via its window manager button (no Continue).
    from gui_driver import find_toplevel

    state = {"closed": False, "left": 400}

    def tick():
        if state["closed"]:
            return
        window = find_toplevel(app, "Select Mode")
        if window is not None:
            state["closed"] = True
            window.destroy()
            return
        state["left"] -= 1
        if state["left"] > 0:
            app.after(15, tick)

    app.after(15, tick)
    app.load_video_btn.invoke()

    assert state["closed"] is True, "the mode dialog never appeared"
    assert app.video is not None and app.video_name == "tiny"
    assert app.state_repo is not None
    assert workspace.errors() == []
