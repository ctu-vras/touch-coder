"""
Red/green regression guard for finding M4 (division by zero on 0-FPS video).

See docs/reviews/2026-07-12/to_do/fix_M4.md.

`export_from_unified` computes `Time_ms = (f / frame_rate) * 1000.0` with no guard
(data_utils.py, ~line 520). Some containers make OpenCV's CAP_PROP_FPS probe return
0, so `frame_rate` reaches the exporter as 0.0 and the FIRST frame (f=0 -> 0/0)
raises ZeroDivisionError — aborting the whole export AFTER the unified CSV was
already written (a partial, inconsistent save).

The exporter should guard this (`... if frame_rate else 0.0`).

  RED before:  ZeroDivisionError from (f / 0).
  GREEN after: export succeeds; Time_ms falls back to 0.0 when fps is unknown.
               Column schema is UNCHANGED (also asserted by test_export_schema).

Run:  uv run pytest tests/ -k M4
"""
import pandas as pd

from data_utils import export_from_unified


def test_M4_export_survives_zero_fps(tmp_path, one_touch_frames):
    out = tmp_path / "vid_export.csv"

    # RED today: raises ZeroDivisionError and never writes the file.
    export_from_unified(
        one_touch_frames, str(out), program_version=7.8, video_name="vid",
        labeling_mode="Normal", frame_rate=0.0, clothes_list=None, total_frames=5,
    )

    assert out.exists(), "export aborted on a 0-FPS video"
    df = pd.read_csv(out)
    # Value-only contract: unknown fps -> Time_ms 0.0, no crash, columns intact.
    assert (df["Time_ms"] == 0).all()
    assert len(df) == 6  # frames 0..5 inclusive
