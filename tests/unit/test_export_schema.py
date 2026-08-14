"""
Export-schema lock (regression tripwire).

External research pipelines read `export/<video>_export.csv` by column name and
order. This test pins the EXACT current columns of the touch export. It is GREEN
today and must stay green: any fix or feature that adds, removes, renames, or
reorders an export column turns it RED — which is the signal that the pipeline
contract is about to break.

If a schema change is ever genuinely intended, it must be coordinated with the
downstream pipeline and this golden list updated deliberately in the same change.

Run:  uv run pytest tests/ -k schema
"""
import pandas as pd

from data_utils import export_from_unified


TOUCH_EXPORT_COLUMNS = [
    "Frame", "Time_ms",
    "LH_X", "LH_Y", "LH_Onset", "LH_Zones",
    "LL_X", "LL_Y", "LL_Onset", "LL_Zones",
    "RH_X", "RH_Y", "RH_Onset", "RH_Zones",
    "RL_X", "RL_Y", "RL_Onset", "RL_Zones",
    "Parameter_1", "Parameter_2", "Parameter_3",
    "LH_Parameter_1", "LH_Parameter_2", "LH_Parameter_3",
    "LL_Parameter_1", "LL_Parameter_2", "LL_Parameter_3",
    "RH_Parameter_1", "RH_Parameter_2", "RH_Parameter_3",
    "RL_Parameter_1", "RL_Parameter_2", "RL_Parameter_3",
    "Note",
]


def test_touch_export_schema_is_frozen(tmp_path):
    out = tmp_path / "vid_export.csv"
    export_from_unified(
        {}, str(out), program_version=7.8, video_name="vid",
        labeling_mode="Normal", frame_rate=30.0, clothes_list=None, total_frames=2,
    )
    cols = list(pd.read_csv(out).columns)
    assert cols == TOUCH_EXPORT_COLUMNS, (
        "Touch export schema drifted — downstream pipelines will break.\n"
        f"got:      {cols}\nexpected: {TOUCH_EXPORT_COLUMNS}"
    )
