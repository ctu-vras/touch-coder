"""
data_utils.py
I/O helpers split from LabelingApp – CSV/notes/parameters and a small
CSV loader. Pure functions where possible; controller passes what’s needed.
"""

import csv
import json
import os
import time
import pandas as pd
from typing import TypedDict, NotRequired, List, Optional, Dict
# --- ADD to data_utils.py (near the top with other imports) ---
from typing import TypedDict, Dict, Optional
import json
import pandas as pd
import os

class FrameRecord(TypedDict):
    X: List[int]                 # 0+ points in X
    Y: List[int]                 # 0+ points in Y (aligned with X)
    Onset: str                   # "ON" | "OFF" | ""
    Bodypart: str                # "LH"|"RH"|"LL"|"RL"|"" (for the owning limb CSV this is redundant, but present)
    Look: str                    # "Yes"|"No"|""
    Zones: List[str]             # always list, may be []
    Touch: Optional[int]          # Parameter_1..3 states ("ON"/"OFF"/None)
    LimbParams: NotRequired[Dict[str, Optional[str]]]        # same, but limb-specific

# Reuse your existing FrameRecord type
class FrameBundle(TypedDict):
    LH: FrameRecord
    RH: FrameRecord
    LL: FrameRecord
    RL: FrameRecord
    Note: Optional[str]
    Params: Dict[str, Optional[str]]
    Changed: NotRequired[bool]   # per-frame (global) params

def write_export_metadata(meta_path: str,
                          program_version,
                          video_name,
                          labeling_mode,
                          frame_rate,
                          clothes_list,
                          param_labels: dict | None = None,
                          limb_param_labels: dict | None = None,
                          labeling_time_seconds: float | None = None) -> None:
    """
    Writes a JSON sidecar with all non-tabular export metadata that used to be
    stuffed into the first 5 lines of *_export.csv.
    """
    meta = {
        "Program Version": program_version,
        "Video Name": video_name,
        "Labeling Mode": labeling_mode,
        "Frame Rate": frame_rate,
        "Zones Covered With Clothes": clothes_list,
        "Param Labels": param_labels or {},
        "Limb Param Labels": limb_param_labels or {},
    }
    if labeling_time_seconds is not None:
        meta["Total Labeling Time (hours)"] = round(float(labeling_time_seconds) / 3600.0, 4)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

def _prepend_header(
    path,
    program_version,
    video_name,
    labeling_mode,
    frame_rate,
    clothes_list,
    param_labels: dict | None = None,
    limb_param_labels: dict | None = None,
):
    def _fmt_label_map(d):
        if not d:
            return ""
        # Expecting {"Par1": "Looking", "Par2": "…", "Par3": "…"}
        items = [f'{k}="{v}"' for k, v in (d.items())]
        return ", ".join(items)

    with open(path, 'r') as f:
        data = f.read()

    # Build the 5th line with optional mappings appended
    fifth = f"Zones Covered With Clothes: {clothes_list}"
    gl = _fmt_label_map(param_labels)
    ll = _fmt_label_map(limb_param_labels)
    if gl:
        fifth += f" ; Param Labels: {gl}"
    if ll:
        fifth += f" ; Limb Param Labels: {ll}"

    with open(path, 'w') as f:
        f.write(f"Program Version: {program_version}\n")
        f.write(f"Video Name: {video_name}\n")
        f.write(f"Labeling Mode: {labeling_mode}\n")
        f.write(f"Frame Rate: {frame_rate}\n")
        f.write(fifth + "\n")
        f.write("\n")  # blank separator line
        f.write(data)
        
def bundle_summary_dict(b):
    """
    Return a compact, readable dict of everything we care about in a FrameBundle,
    including Look/Onset/Touch/Zones per limb + top-level Note/Params.
    """
    def limb_view(rec, label):
        if not rec:
            return {"_missing": True}
        return {
            "Onset": rec.get("Onset"),
            "Look": rec.get("Look"),
            "Touch": rec.get("Touch"),
            "Zones": rec.get("Zones") or [],
            "Points": len(rec.get("X") or []),  # quick sanity check of clic
            "LimbParams": rec.get("LimbParams") or {},
        }

    return {
        "Note": b.get("Note"),
        "Params": b.get("Params") or {},
        "LH": limb_view(b.get("LH"), "LH"),
        "RH": limb_view(b.get("RH"), "RH"),
        "LL": limb_view(b.get("LL"), "LL"),
        "RL": limb_view(b.get("RL"), "RL"),
    }

def bundle_summary_str(b, frame_index=None):
    import json
    head = {} if frame_index is None else {"Frame": frame_index}
    data = bundle_summary_dict(b)
    data = {**head, **data}
    return json.dumps(data, indent=2, ensure_ascii=False)

def empty_record(limb: str) -> FrameRecord:
    return FrameRecord(
        X=[], Y=[], Onset="", Bodypart=limb, Look="", Zones=[], Touch=None
    )

def empty_bundle() -> FrameBundle:
    return {
        "LH": empty_record("LH"),
        "RH": empty_record("RH"),
        "LL": empty_record("LL"),
        "RL": empty_record("RL"),
        "Note": None,
        "Params": {},
    }

def save_unified_dataset(csv_path: str, total_frames: int, frames: Dict[int, FrameBundle], changed_only: bool = True) -> None:
    """
    Incremental save of unified CSV.
    - If no changed frames: do NOT overwrite the file (keep previous rows).
    - If there are changed frames: merge them into the on-disk file (by Frame) and write the union.
    Always keeps columns stable, and prints clear debug info.
    """
    if not csv_path:
        return

    # 1) collect rows for changed frames
    changed_rows = []
    changed_frames = []

    def bundle_is_changed(b: FrameBundle) -> bool:
        # NEW: if the frame's bundle-level flag is set, it's dirty
        if b.get("Changed"):
            return True
        # existing per-limb flags still count
        return any(isinstance(b.get(limb), dict) and b[limb].get("changed")
                   for limb in ("LH", "RH", "LL", "RL"))

    for f in range(total_frames + 1):
        b = frames.get(f)
        if changed_only:
            if not isinstance(b, dict) or not b.get("Changed"):
                continue
        else:
            # Full write: ensure we always have a bundle to serialize
            if not isinstance(b, dict):
                b = empty_bundle()
        changed_frames.append(f)
        changed_rows.append({
            "Frame": f,
            "Note": b.get("Note"),
            "Params": json.dumps(b.get("Params", {})),
            "LH": json.dumps(b["LH"]),
            "RH": json.dumps(b["RH"]),
            "LL": json.dumps(b["LL"]),
            "RL": json.dumps(b["RL"]),
        })

    if changed_only and not changed_rows:
        print(f"DEBUG: Unified → {csv_path}")
        print(f"DEBUG: total_frames={total_frames}, changed_only={changed_only}, rows_written=0 (skipped writing; kept previous file)")
        return

    # 2) load existing on-disk rows (if any), then upsert changed_rows by Frame
    existing_map: Dict[int, dict] = {}
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        try:
            existing_df = pd.read_csv(csv_path)
            for _, r in existing_df.iterrows():
                try:
                    fr = int(r["Frame"])
                except Exception:
                    continue
                existing_map[fr] = {
                    "Frame": fr,
                    "Note": (None if pd.isna(r.get("Note")) else r.get("Note")),
                    "Params": r.get("Params"),
                    "LH": r.get("LH"),
                    "RH": r.get("RH"),
                    "LL": r.get("LL"),
                    "RL": r.get("RL"),
                }
        except pd.errors.EmptyDataError:
            pass  # treat as no existing rows

    # upsert
    for row in changed_rows:
        existing_map[row["Frame"]] = row

    # 3) write union (sorted by Frame); ensure header even if union empty
    union_rows = [existing_map[k] for k in sorted(existing_map.keys())]
    cols = ["Frame", "Note", "Params", "LH", "RH", "LL", "RL"]
    df = pd.DataFrame(union_rows, columns=cols)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    df.to_csv(csv_path, index=False)

    print(f"DEBUG: Unified → {csv_path}")
    print(f"DEBUG: total_frames={total_frames}, changed_only={changed_only}, rows_written={len(changed_rows)}, union_rows={len(union_rows)}")

def load_unified_dataset(csv_path: str, progress_cb=None) -> Dict[int, FrameBundle]:
    frames: Dict[int, FrameBundle] = {}
    if not (csv_path and os.path.exists(csv_path)):
        print(f"DEBUG: Unified not found → {csv_path}", flush=True)
        return frames
    try:
        size = os.path.getsize(csv_path)
        print(f"DEBUG: Unified exists ({size} bytes) → {csv_path}", flush=True)
        if size == 0:
            print("DEBUG: Unified is empty (0 bytes) — starting with empty frames", flush=True)
            return frames
        t0 = time.time()
        print("DEBUG: load_unified_dataset: pd.read_csv starting...", flush=True)
        df = pd.read_csv(csv_path)
        print(f"DEBUG: load_unified_dataset: pd.read_csv done in {time.time() - t0:.2f}s "
              f"(rows={len(df)}, cols={len(df.columns)})", flush=True)
    except pd.errors.EmptyDataError:
        print("DEBUG: Unified had no columns (EmptyDataError) — starting with empty frames", flush=True)
        return frames
    except Exception as e:
        print(f"ERROR: Failed to read unified CSV: {e} — starting empty", flush=True)
        return frames

    total_rows = len(df)
    log_every = max(10000, total_rows // 20) if total_rows else 10000
    last_log_t = time.time()
    iter_start = time.time()

    # itertuples is ~50-100x faster than iterrows on wide frames because it
    # does NOT construct a fresh pandas Series per row.
    cols = list(df.columns)
    col_idx = {c: i for i, c in enumerate(cols)}
    frame_i = col_idx.get("Frame", -1)
    note_i  = col_idx.get("Note",  -1)
    params_i = col_idx.get("Params", -1)
    limb_i = {limb: col_idx.get(limb, -1) for limb in ("LH", "RH", "LL", "RL")}

    if frame_i < 0:
        print("ERROR: load_unified_dataset: 'Frame' column missing — aborting", flush=True)
        return frames

    print(f"DEBUG: load_unified_dataset: iterating {total_rows} rows via itertuples...", flush=True)

    for row_idx, row in enumerate(df.itertuples(index=False, name=None)):
        frame_v = row[frame_i]
        try:
            f = int(frame_v)
        except (ValueError, TypeError):
            continue

        note_v = row[note_i] if note_i >= 0 else None
        # NaN floats compare unequal to themselves; treat as missing.
        if isinstance(note_v, float) and note_v != note_v:
            note_v = None

        def _json_at(idx, default):
            if idx < 0:
                return default
            v = row[idx]
            if isinstance(v, float) and v != v:
                return default
            if not v:
                return default
            try:
                return json.loads(v)
            except Exception:
                return default

        frames[f] = {
            "Note": (None if note_v is None else str(note_v)),
            "Params": _json_at(params_i, {}),
            "LH": _json_at(limb_i["LH"], None) or empty_record("LH"),
            "RH": _json_at(limb_i["RH"], None) or empty_record("RH"),
            "LL": _json_at(limb_i["LL"], None) or empty_record("LL"),
            "RL": _json_at(limb_i["RL"], None) or empty_record("RL"),
        }

        if (row_idx + 1) % log_every == 0 or (time.time() - last_log_t) >= 0.25:
            elapsed = time.time() - iter_start
            pct = (row_idx + 1) / total_rows * 100 if total_rows else 0
            if progress_cb:
                try:
                    progress_cb(row_idx + 1, total_rows, "Loading unified dataset", elapsed)
                except Exception as exc:
                    print(f"WARN: progress_cb failed: {exc}", flush=True)
            if (time.time() - last_log_t) >= 5.0 or (row_idx + 1) % log_every == 0:
                print(f"DEBUG: load_unified_dataset: parsed {row_idx + 1}/{total_rows} rows "
                      f"({pct:.1f}%, {elapsed:.1f}s elapsed)", flush=True)
            last_log_t = time.time()
    if progress_cb:
        try:
            progress_cb(total_rows, total_rows, "Loading unified dataset", time.time() - iter_start)
        except Exception:
            pass
    print(f"DEBUG: Unified loaded rows={len(frames)} in {time.time() - iter_start:.1f}s", flush=True)
    return frames

def import_unified_from_export(export_csv_path: str, progress_cb=None) -> Dict[int, FrameBundle]:
    """
    Reconstruct a unified in-memory dict from a legacy *_export.csv.
    Reads after the 5 meta lines + 1 blank line (skiprows=6).
    Maps global Parameter_1..3 -> Params['Par1'..'Par3']
    and limb {LH,LL,RH,RL}_Parameter_{1..3} -> rec['LimbParams']['Par1'..'Par3'].
    """
    frames: Dict[int, FrameBundle] = {}
    if not (export_csv_path and os.path.exists(export_csv_path)):
        print(f"DEBUG: import_unified_from_export: file does not exist → {export_csv_path}", flush=True)
        return frames
    try:
        size = os.path.getsize(export_csv_path)
        print(f"DEBUG: import_unified_from_export: reading {size} bytes from {export_csv_path}", flush=True)
        skip = 0
        with open(export_csv_path, "r", encoding="utf-8", errors="ignore") as fh:
            first = (fh.readline() or "").strip()
            if first.startswith("Program Version:"):
                skip = 6
        print(f"DEBUG: import_unified_from_export: skiprows={skip}, calling pd.read_csv...", flush=True)
        t0 = time.time()
        df = pd.read_csv(export_csv_path, skiprows=skip)
        print(f"DEBUG: import_unified_from_export: pd.read_csv done in {time.time() - t0:.2f}s "
              f"(rows={len(df)}, cols={len(df.columns)})", flush=True)
    except Exception as e:
        print(f"ERROR: import_unified_from_export read failed: {e}", flush=True)
        return frames

    def parse_xy(s) -> list[int]:
        if not isinstance(s, str) or not s.strip():
            return []
        return [int(x) for x in s.split(",") if x.strip().isdigit()]

    def _clean(v):
        # Normalize NaN / empty-string to None.
        if v is None:
            return None
        if isinstance(v, float) and v != v:  # NaN
            return None
        if v == "":
            return None
        return v

    # itertuples is ~50-100x faster than iterrows on wide frames because it
    # does NOT construct a fresh pandas Series per row (that was the cause
    # of the 4-rows-in-20-seconds freeze).
    cols = list(df.columns)
    col_idx = {c: i for i, c in enumerate(cols)}
    frame_i = col_idx.get("Frame", -1)
    note_i  = col_idx.get("Note",  -1)
    global_param_i = [col_idx.get(f"Parameter_{p}", -1) for p in (1, 2, 3)]
    limb_field_i: Dict[str, Dict[str, int]] = {}
    for limb in ("LH", "LL", "RH", "RL"):
        limb_field_i[limb] = {
            "X":     col_idx.get(f"{limb}_X", -1),
            "Y":     col_idx.get(f"{limb}_Y", -1),
            "Onset": col_idx.get(f"{limb}_Onset", -1),
            "Look":  col_idx.get(f"{limb}_Look", -1),
            "Zones": col_idx.get(f"{limb}_Zones", -1),
            "P1":    col_idx.get(f"{limb}_Parameter_1", -1),
            "P2":    col_idx.get(f"{limb}_Parameter_2", -1),
            "P3":    col_idx.get(f"{limb}_Parameter_3", -1),
        }

    if frame_i < 0:
        print("ERROR: import_unified_from_export: 'Frame' column missing — aborting", flush=True)
        return frames

    total_rows = len(df)
    log_every = max(10000, total_rows // 20) if total_rows else 10000
    last_log_t = time.time()
    iter_start = time.time()
    print(f"DEBUG: import_unified_from_export: iterating {total_rows} rows via itertuples "
          f"(progress every {log_every} rows or 5s)...", flush=True)

    def _at(row, idx, default=None):
        if idx < 0:
            return default
        return row[idx]

    for row_idx, row in enumerate(df.itertuples(index=False, name=None)):
        try:
            f = int(row[frame_i])
        except (ValueError, TypeError):
            continue

        note_v = _clean(_at(row, note_i))
        b: FrameBundle = {
            "Note": (None if note_v is None else str(note_v)),
            "Params": {},
            "LH": empty_record("LH"),
            "LL": empty_record("LL"),
            "RH": empty_record("RH"),
            "RL": empty_record("RL"),
        }

        # Global params Parameter_1..3 -> Par1..Par3
        params: Dict[str, Optional[str]] = {}
        for p_num, gp_idx in enumerate(global_param_i, start=1):
            params[f"Par{p_num}"] = _clean(_at(row, gp_idx))
        if any(v is not None for v in params.values()):
            b["Params"] = params

        # Limbs
        for limb in ("LH", "LL", "RH", "RL"):
            li = limb_field_i[limb]
            xr = parse_xy(_at(row, li["X"], ""))
            yr = parse_xy(_at(row, li["Y"], ""))
            onset = _at(row, li["Onset"], "") or ""
            look  = _at(row, li["Look"], "") or ""
            if isinstance(onset, float) and onset != onset:
                onset = ""
            if isinstance(look, float) and look != look:
                look = ""
            zones_raw = _at(row, li["Zones"], "[]")
            try:
                if isinstance(zones_raw, str):
                    zones = json.loads(zones_raw)
                elif isinstance(zones_raw, float) and zones_raw != zones_raw:
                    zones = []
                else:
                    zones = zones_raw or []
            except Exception:
                zones = []

            rec: FrameRecord = {
                "X": xr, "Y": yr, "Onset": onset, "Bodypart": limb, "Look": look,
                "Zones": zones, "Touch": None,
            }

            lp: Dict[str, Optional[str]] = {
                "Par1": _clean(_at(row, li["P1"])),
                "Par2": _clean(_at(row, li["P2"])),
                "Par3": _clean(_at(row, li["P3"])),
            }
            if any(v is not None for v in lp.values()):
                rec["LimbParams"] = lp

            b[limb] = rec

        frames[f] = b

        if (row_idx + 1) % log_every == 0 or (time.time() - last_log_t) >= 0.25:
            elapsed = time.time() - iter_start
            pct = (row_idx + 1) / total_rows * 100 if total_rows else 0
            if progress_cb:
                try:
                    progress_cb(row_idx + 1, total_rows, "Importing labels from export", elapsed)
                except Exception as exc:
                    print(f"WARN: progress_cb failed: {exc}", flush=True)
            if (time.time() - last_log_t) >= 5.0 or (row_idx + 1) % log_every == 0:
                print(f"DEBUG: import_unified_from_export: parsed {row_idx + 1}/{total_rows} rows "
                      f"({pct:.1f}%, {elapsed:.1f}s elapsed)", flush=True)
            last_log_t = time.time()

    if progress_cb:
        try:
            progress_cb(total_rows, total_rows, "Importing labels from export", time.time() - iter_start)
        except Exception:
            pass
    print(f"DEBUG: import_unified_from_export → frames={len(frames)} "
          f"from {export_csv_path} in {time.time() - iter_start:.1f}s", flush=True)
    return frames

def export_from_unified(frames: Dict[int, FrameBundle],
                        out_csv: str,
                        program_version: float,
                        video_name: str,
                        labeling_mode: str,
                        frame_rate: float,
                        clothes_list,
                        total_frames: int,
                        param_labels: Dict[str, str] | None = None,
                        limb_param_labels: Dict[str, str] | None = None) -> None:
    """
    Emit the legacy *_export.csv with EXACT schema/order and a row for EVERY frame 0..total_frames.
    - Global Params: Par1..Par3 → Parameter_1..3
    - Limb Params:  Par1..Par3 → {LH,LL,RH,RL}_Parameter_1..3
    Appends label mappings to the 5th header line (keeps 5-line header + blank).
    """
    rows = []

    def _xy_str(lst):
        return ",".join(map(str, lst)) if lst else ""

    for f in range(total_frames + 1):
        b = frames.get(f, empty_bundle())
        row = {"Frame": f, "Time_ms": (f / frame_rate) * 1000.0}

        # Limb blocks in order: LH, LL, RH, RL
        for limb in ["LH", "LL", "RH", "RL"]:
            rec = b.get(limb, {}) if isinstance(b, dict) else {}
            row[f"{limb}_X"] = _xy_str(rec.get("X", []))
            row[f"{limb}_Y"] = _xy_str(rec.get("Y", []))
            row[f"{limb}_Onset"] = rec.get("Onset", "")
            row[f"{limb}_Zones"] = json.dumps(rec.get("Zones", []) or [])

        # Global params (canonical keys → fixed columns)
        params = (b.get("Params") or {}) if isinstance(b, dict) else {}
        for i in (1, 2, 3):
            val = params.get(f"Par{i}")
            row[f"Parameter_{i}"] = "" if (val is None or val == "") else val

        # Limb-specific params (canonical keys → fixed columns)
        for limb in ["LH", "LL", "RH", "RL"]:
            rec = b.get(limb, {}) if isinstance(b, dict) else {}
            lp = rec.get("LimbParams", {}) if isinstance(rec, dict) else {}
            for i in (1, 2, 3):
                val = lp.get(f"Par{i}")
                row[f"{limb}_Parameter_{i}"] = "" if (val is None or val == "") else val

        row["Note"] = b.get("Note", "") if isinstance(b, dict) else ""
        rows.append(row)

    # Exact legacy column order
    cols = ["Frame", "Time_ms"]
    for limb in ["LH", "LL", "RH", "RL"]:
        cols += [f"{limb}_X", f"{limb}_Y", f"{limb}_Onset", f"{limb}_Zones"]
    cols += ["Parameter_1", "Parameter_2", "Parameter_3"]
    for limb in ["LH", "LL", "RH", "RL"]:
        cols += [f"{limb}_Parameter_1", f"{limb}_Parameter_2", f"{limb}_Parameter_3"]
    cols += ["Note"]

    df = pd.DataFrame(rows, columns=cols)
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    df.to_csv(out_csv, index=False)

    # Keep 5-line header; append label mappings to the last line
    '''
    _prepend_header(
        out_csv,
        program_version,
        video_name,
        labeling_mode,
        frame_rate,
        clothes_list,
        param_labels=param_labels,
        limb_param_labels=limb_param_labels,
    )'''
    # CSV remains clean (no preamble). Metadata is written separately by caller.
    print(f"DEBUG: Export → {out_csv} (rows={len(rows)})")

def preview_lines_for_save(frames: Dict[int, FrameBundle],
                           total_frames: int,
                           changed_only: bool = True,
                           limbs_order = ("LH","LL","RH","RL")) -> list[str]:
    """
    Build human-readable preview lines for frames that are about to be saved.
    Format per limb present: 'frame=23 | LH: On ["17L"] | RH: Off [] ...'
    If changed_only=True, only lists frames where any limb has rec['changed'].
    """
    lines: list[str] = []

    def bundle_is_changed(b: FrameBundle) -> bool:
        if b.get("Changed"):
            return True
        return any(isinstance(b.get(l), dict) and b[l].get("changed") for l in limbs_order)

    for f in range(total_frames + 1):
        b = frames.get(f)
        if not isinstance(b, dict):
            continue
        if changed_only and not b.get("Changed"):
            continue

        parts = [f"frame={f:>5}"]
        for limb in limbs_order:
            rec = b.get(limb, {})
            xs = rec.get("X", [])
            ys = rec.get("Y", [])
            if not xs or not ys:
                # skip empty limb (keeps preview concise)
                continue
            onset = rec.get("Onset", "")
            zones = rec.get("Zones", [])
            parts.append(f"{limb}: {onset} {zones}")
        # include note/params if present
        note = b.get("Note")
        if note:
            parts.append(f'Note="{note}"')
        params = b.get("Params") or {}
        if any(v is not None for v in params.values()):
            # Show only ON/OFF/None summary
            par_show = ", ".join(f"{k}:{v}" for k, v in params.items())
            parts.append(f"Params[{par_show}]")

        if len(parts) > 1:  # at least one limb had content or note/params
            lines.append(" | ".join(parts))

    return lines

def csv_to_dict(csv_path) -> Dict[int, "FrameRecord"]:
    data: Dict[int, FrameRecord] = {}
    import csv, json
    with open(csv_path, mode='r', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            frame = int(row['Frame'])
            xs = [int(x) for x in row['X'].split(',')] if row['X'] else []
            ys = [int(y) for y in row['Y'].split(',')] if row['Y'] else []
            onset = row.get('Onset', '') or ''
            bodypart = row.get('Bodypart', '') or ''
            look = row.get('Look', '') or ''
            # Normalize Zones => list[str]
            try:
                z_parsed = json.loads(row.get('Zones', '[]') or '[]')
                zones = [str(z) for z in z_parsed] if isinstance(z_parsed, list) else ([str(z_parsed)] if z_parsed else [])
            except json.JSONDecodeError:
                z = row.get('Zones', '')
                zones = [z] if z else []
            touch_raw = row.get('Touch', '')
            touch = None
            if isinstance(touch_raw, str) and touch_raw.strip() != '':
                try:
                    touch = int(touch_raw)
                except ValueError:
                    touch = None
            data[frame] = {
                'X': xs, 'Y': ys,
                'Onset': onset, 'Bodypart': bodypart, 'Look': look,
                'Zones': zones, 'Touch': touch,
                'changed': False,
            }
    return data

def save_dataset(csv_path, total_frames, data, with_touch: bool = False):
    if not csv_path:
        return
    import csv, json
    with open(csv_path, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Frame', 'X', 'Y', 'Onset', 'Bodypart', 'Look', 'Zones', 'Touch'])
        for frame in range(total_frames + 1):
            rec: FrameRecord | None = data.get(frame)
            if not rec:
                writer.writerow([frame, '', '', '', '', '', json.dumps([]), ''])
                continue
            xs = rec.get('X', []) or []
            ys = rec.get('Y', []) or []
            x_str = ','.join(map(str, xs))
            y_str = ','.join(map(str, ys))
            onset = rec.get('Onset', '')
            bodypart = rec.get('Bodypart', '')
            look = rec.get('Look', '')
            zones = json.dumps(rec.get('Zones', []) or [])
            touch_val = ''
            if with_touch and onset:
                touch_val = 1 if onset == "ON" else 0
            elif rec.get('Touch') is not None:
                touch_val = rec['Touch']
            writer.writerow([frame, x_str, y_str, onset, bodypart, look, zones, touch_val])

def save_parameter_to_csv(path, param_dict):
    if not path:
        return
    with open(path, mode='w', newline='') as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(['Frame', 'State'])
        for key, value in param_dict.items():
            writer.writerow([key, value])

def load_parameter_from_csv(path):
    d = {}
    if not path or not os.path.exists(path):
        return d
    with open(path, mode='r') as csv_file:
        reader = csv.reader(csv_file)
        next(reader, None)
        for row in reader:
            if len(row) == 2:
                key = int(row[0])
                value = row[1]
                d[key] = value
    return d

def save_limb_parameters(csv_path, limb_param_dicts):
    """
    limb_param_dicts: { 'Parameter_1': dict, 'Parameter_2': dict, 'Parameter_3': dict }
    dict keys are (limb, frame) tuples; values are 'ON'/'OFF'/None.
    """
    with open(csv_path, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Limb", "Frame", "Parameter", "State"])
        for param_name, param_dict in limb_param_dicts.items():
            for (limb, frame), state in param_dict.items():
                writer.writerow([limb, frame, param_name, state])

def load_limb_parameters(csv_path):
    """
    Returns three dicts (for Parameter_1..3) keyed by (limb, frame) -> state
    """
    p1, p2, p3 = {}, {}, {}
    if not os.path.exists(csv_path):
        return p1, p2, p3
    with open(csv_path, 'r', newline='') as file:
        reader = csv.reader(file)
        next(reader, None)
        for row in reader:
            limb, frame, param_name, state = row
            frame = int(frame)
            if param_name == "Parameter_1":
                p1[(limb, frame)] = state
            elif param_name == "Parameter_2":
                p2[(limb, frame)] = state
            elif param_name == "Parameter_3":
                p3[(limb, frame)] = state
    return p1, p2, p3

def merge_and_flip_export(
    lh_csv, ll_csv, rh_csv, rl_csv,
    param_paths, notes_path, limb_params_path,
    video_name, frame_rate, program_version, labeling_mode,
    clothes_list, out_folder
):
    # Load limb CSVs
    lh_df = pd.read_csv(lh_csv)
    ll_df = pd.read_csv(ll_csv)
    rh_df = pd.read_csv(rh_csv)
    rl_df = pd.read_csv(rl_csv)

    for df, limb in zip([lh_df, ll_df, rh_df, rl_df], ['LH', 'LL', 'RH', 'RL']):
        df.columns = [f"{limb}_{col}" if col != "Frame" else "Frame" for col in df.columns]

    merged_df = lh_df.merge(ll_df, on="Frame", how="outer") \
                     .merge(rh_df, on="Frame", how="outer") \
                     .merge(rl_df, on="Frame", how="outer")

    # parameters 1..3
    for i, p in enumerate(param_paths, start=1):
        if p and os.path.exists(p):
            param_df = pd.read_csv(p)
            param_df.columns = ['Frame', f'Parameter_{i}']
            merged_df = merged_df.merge(param_df, on='Frame', how='outer')
        else:
            col = f'Parameter_{i}'
            if col not in merged_df.columns:
                merged_df[col] = None

    # Drop legacy Look columns (no longer used in exports).
    merged_df = merged_df.drop(columns=[c for c in merged_df.columns if "_Look" in c], errors="ignore")

    merged_df = merged_df.drop_duplicates(subset=['Frame'])
    merged_df = merged_df.drop([c for c in merged_df.columns if c.endswith('_y')], axis=1)
    merged_df.columns = [c.replace('_x', '') for c in merged_df.columns]
    merged_df = merged_df.drop([c for c in merged_df.columns if c.endswith('_Touch')], axis=1)

    # notes
    if notes_path and os.path.exists(notes_path):
        notes_df = pd.read_csv(notes_path)
        merged_df = merged_df.merge(notes_df, on='Frame', how='outer')
    else:
        if 'Note' not in merged_df.columns:
            merged_df['Note'] = None

    # limb params
    if limb_params_path and os.path.exists(limb_params_path):
        limb_params_df = pd.read_csv(limb_params_path)
        if not limb_params_df.empty:
            limb_params_df = limb_params_df.pivot(index='Frame', columns=['Limb', 'Parameter'], values='State')
            limb_params_df.columns = [f"{limb}_{param}" for limb, param in limb_params_df.columns]
            limb_params_df.reset_index(inplace=True)
        merged_df = pd.merge(merged_df, limb_params_df, on='Frame', how='outer')

    # expected columns scaffold
    expected_columns = ['Frame']
    for limb in ['LH', 'LL', 'RH', 'RL']:
        for i in range(1, 4):
            col = f"{limb}_Parameter_{i}"
            if col not in merged_df.columns:
                merged_df[col] = None
            expected_columns.append(col)

    merged_df = merged_df.drop_duplicates(subset=['Frame'])
    existing_columns = [c for c in expected_columns if c in merged_df.columns]
    remaining_columns = [c for c in merged_df.columns if c not in existing_columns]
    merged_df = merged_df[existing_columns + remaining_columns]
    merged_df['Time_ms'] = (merged_df['Frame'] / frame_rate) * 1000

    # final ordering (keep misc first)
    limb_params_cols = [f"{limb}_Parameter_{i}" for limb in ['LH', 'LL', 'RH', 'RL'] for i in range(1, 4)]
    other_cols = [c for c in merged_df.columns if c not in limb_params_cols and c not in ('Note', 'Time_ms')]
    final_cols = other_cols + limb_params_cols + (['Note'] if 'Note' in merged_df.columns else []) + (['Time_ms'] if 'Time_ms' in merged_df.columns else [])
    merged_df = merged_df[final_cols]

    os.makedirs(out_folder, exist_ok=True)
    pref = os.path.join(out_folder, f"{video_name}_export_flipped.csv")
    merged_df.to_csv(pref, index=False)

    # prepend header meta to file
    _prepend_header(
        pref, program_version, video_name, labeling_mode, frame_rate,
        clothes_list
    )

    # produce L<->R flipped version
    flipped = _swap_lr_columns(merged_df)
    flipped = flipped.applymap(_swap_lr_in_string)
    out_csv = os.path.join(out_folder, f"{video_name}_export.csv")
    flipped.to_csv(out_csv, index=False)
    _prepend_header(
        out_csv, program_version, video_name, labeling_mode, frame_rate,
        clothes_list
    )
    return pref, out_csv


def _swap_lr_in_string(val):
    if not isinstance(val, str):
        return val
    return val.replace('L', '§').replace('R', 'L').replace('§', 'R')

def _swap_lr_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for left_prefix, right_prefix in (('LH_', 'RH_'), ('LL_', 'RL_')):
        left_cols  = [c for c in out.columns if c.startswith(left_prefix)]
        right_cols = [c for c in out.columns if c.startswith(right_prefix)]
        for l, r in zip(sorted(left_cols), sorted(right_cols)):
            out[l], out[r] = out[r].copy(), out[l].copy()
    return out

def extract_zones_from_file(file_path):
    if not file_path or not os.path.exists(file_path):
        return None
    zones = set()
    with open(file_path, 'r') as f:
        for line in f:
            if 'Zones=' in line:
                zones.add(line.split('Zones=')[-1].strip())
    return list(zones) if zones else None
