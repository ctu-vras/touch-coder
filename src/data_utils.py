"""
data_utils.py
I/O helpers split from LabelingApp – CSV/notes/parameters and a small
CSV loader. Pure functions where possible; controller passes what’s needed.
"""

import csv
import io
import json
import os
import time
import pandas as pd
from typing import TypedDict, NotRequired, List, Optional, Dict

from atomic_io import atomic_write, durable_append
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
    atomic_write(meta_path, lambda f: json.dump(meta, f, indent=2, ensure_ascii=False))

def bundle_summary_dict(b):
    """
    Return a compact, readable dict of everything we care about in a FrameBundle,
    including Onset/Touch/Zones per limb + top-level Note/Params.
    """
    def limb_view(rec, label):
        if not rec:
            return {"_missing": True}
        return {
            "Onset": rec.get("Onset"),
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
        X=[], Y=[], Onset="", Bodypart=limb, Zones=[], Touch=None
    )


def _normalize_param_state(v):
    # Legacy artifact: toggle_limb_parameter used to store the string "None" (M1).
    return None if v in (None, "", "None") else v

def empty_bundle() -> FrameBundle:
    return {
        "LH": empty_record("LH"),
        "RH": empty_record("RH"),
        "LL": empty_record("LL"),
        "RL": empty_record("RL"),
        "Note": None,
        "Params": {},
    }


UNIFIED_COLUMNS = ["Frame", "Note", "Params", "LH", "RH", "LL", "RL"]
UNIFIED_COMPACT_FACTOR = 2


def _unified_row(frame: int, bundle: FrameBundle) -> dict:
    return {
        "Frame": frame,
        "Note": bundle.get("Note"),
        "Params": json.dumps(bundle.get("Params", {})),
        "LH": json.dumps(bundle["LH"]),
        "RH": json.dumps(bundle["RH"]),
        "LL": json.dumps(bundle["LL"]),
        "RL": json.dumps(bundle["RL"]),
    }


def _read_unified_journal(csv_path: str):
    try:
        return pd.read_csv(csv_path), False
    except pd.errors.ParserError:
        with open(csv_path, "rb") as f:
            data = f.read()
        if data.endswith(b"\n"):
            raise
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            raise
        print(
            f"WARNING: Ignoring crash-torn final unified row and repairing journal → {csv_path}",
            flush=True,
        )
        return pd.read_csv(io.BytesIO(data[: last_newline + 1])), True


def _compact_unified_dataset(
    csv_path: str,
    frames: Dict[int, FrameBundle],
    rows_on_disk: int,
    *,
    force: bool = False,
) -> None:
    distinct_frames = len(frames)
    if not distinct_frames or (
        not force and rows_on_disk <= distinct_frames * UNIFIED_COMPACT_FACTOR
    ):
        return
    rows = [_unified_row(frame, frames[frame]) for frame in sorted(frames)]
    df = pd.DataFrame(rows, columns=UNIFIED_COLUMNS)
    atomic_write(csv_path, lambda f: df.to_csv(f, index=False), keep_backup=True)
    print(
        f"INFO: Unified compacted {rows_on_disk} journal rows to "
        f"{distinct_frames} distinct frames → {csv_path}"
    )

def save_unified_dataset(csv_path: str, total_frames: int, frames: Dict[int, FrameBundle], changed_only: bool = True) -> None:
    """
    Append changed frames to the unified CSV journal.

    Duplicate frame rows intentionally resolve last-writer-wins in the loader;
    load-time compaction bounds journal growth without making each save depend
    on the amount of previously persisted data.
    """
    if not csv_path:
        return

    # 1) collect rows for changed frames
    changed_rows = []

    for f in range(total_frames + 1):
        b = frames.get(f)
        if changed_only:
            if not isinstance(b, dict) or not b.get("Changed"):
                continue
        else:
            # Full write: ensure we always have a bundle to serialize
            if not isinstance(b, dict):
                b = empty_bundle()
        changed_rows.append(_unified_row(f, b))

    if changed_only and not changed_rows:
        print(f"DEBUG: Unified → {csv_path}")
        print(f"DEBUG: total_frames={total_frames}, changed_only={changed_only}, rows_written=0 (skipped writing; kept previous file)")
        return

    df = pd.DataFrame(changed_rows, columns=UNIFIED_COLUMNS)
    durable_append(
        csv_path,
        lambda f, is_new_file: df.to_csv(f, index=False, header=is_new_file),
    )

    print(f"DEBUG: Unified → {csv_path}")
    print(
        f"DEBUG: total_frames={total_frames}, changed_only={changed_only}, "
        f"rows_appended={len(changed_rows)}"
    )

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
        df, recovered_torn_tail = _read_unified_journal(csv_path)
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

        bundle: FrameBundle = {
            "Note": (None if note_v is None else str(note_v)),
            "Params": _json_at(params_i, {}),
            "LH": _json_at(limb_i["LH"], None) or empty_record("LH"),
            "RH": _json_at(limb_i["RH"], None) or empty_record("RH"),
            "LL": _json_at(limb_i["LL"], None) or empty_record("LL"),
            "RL": _json_at(limb_i["RL"], None) or empty_record("RL"),
        }
        for limb in ("LH", "RH", "LL", "RL"):
            rec = bundle[limb]
            limb_params = rec.get("LimbParams") if isinstance(rec, dict) else None
            if isinstance(limb_params, dict):
                for key, value in limb_params.items():
                    limb_params[key] = _normalize_param_state(value)
        frames[f] = bundle

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
    _compact_unified_dataset(csv_path, frames, total_rows, force=recovered_torn_tail)
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
        df = pd.read_csv(export_csv_path, skiprows=skip, keep_default_na=False)
        print(f"DEBUG: import_unified_from_export: pd.read_csv done in {time.time() - t0:.2f}s "
              f"(rows={len(df)}, cols={len(df.columns)})", flush=True)
    except Exception as e:
        print(f"ERROR: import_unified_from_export read failed: {e}", flush=True)
        return frames

    dropped_pairs = 0

    def _xy_tokens(v) -> list[str]:
        if isinstance(v, float) and v != v:
            return []
        if isinstance(v, (int, float)):
            return [str(v)]
        if not isinstance(v, str) or not v.strip():
            return []
        return [token for token in v.split(",") if token.strip()]

    def parse_xy_pairs(x_cell, y_cell, zones, frame, limb):
        nonlocal dropped_pairs
        x_tokens = _xy_tokens(x_cell)
        y_tokens = _xy_tokens(y_cell)
        pair_count = min(len(x_tokens), len(y_tokens))
        if len(x_tokens) != len(y_tokens):
            unmatched = abs(len(x_tokens) - len(y_tokens))
            dropped_pairs += unmatched
            print(
                f"WARNING: import_unified_from_export: frame={frame} {limb}: "
                f"{len(x_tokens)} X vs {len(y_tokens)} Y tokens — "
                f"keeping first {pair_count} pairs",
                flush=True,
            )

        xs, ys, aligned_zones = [], [], []
        for i in range(pair_count):
            try:
                x = int(float(x_tokens[i]))
                y = int(float(y_tokens[i]))
            except (ValueError, TypeError):
                dropped_pairs += 1
                print(
                    f"WARNING: import_unified_from_export: frame={frame} {limb}: "
                    f"dropping click {i} (X={x_tokens[i]!r}, Y={y_tokens[i]!r})",
                    flush=True,
                )
                continue
            xs.append(x)
            ys.append(y)
            aligned_zones.append(zones[i] if i < len(zones) else [])
        return xs, ys, aligned_zones

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
            onset = _at(row, li["Onset"], "") or ""
            if isinstance(onset, float) and onset != onset:
                onset = ""
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
            xr, yr, zones = parse_xy_pairs(
                _at(row, li["X"], ""),
                _at(row, li["Y"], ""),
                zones,
                f,
                limb,
            )

            rec: FrameRecord = {
                "X": xr, "Y": yr, "Onset": onset, "Bodypart": limb,
                "Zones": zones, "Touch": None,
            }

            raw_lp = {
                "Par1": _at(row, li["P1"]),
                "Par2": _at(row, li["P2"]),
                "Par3": _at(row, li["P3"]),
            }
            lp: Dict[str, Optional[str]] = {
                key: _normalize_param_state(_clean(value))
                for key, value in raw_lp.items()
            }
            if any(v is not None for v in lp.values()) or "None" in raw_lp.values():
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
    if dropped_pairs:
        print(
            "WARNING: import_unified_from_export: "
            f"total coordinate pairs dropped={dropped_pairs}",
            flush=True,
        )
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
    """
    rows = []

    def _xy_str(lst):
        return ",".join(map(str, lst)) if lst else ""

    for f in range(total_frames + 1):
        b = frames.get(f, empty_bundle())
        # 0-FPS probe (some containers) must not abort the export.
        row = {"Frame": f, "Time_ms": (f / frame_rate) * 1000.0 if frame_rate else 0.0}

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
                val = _normalize_param_state(lp.get(f"Par{i}"))
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
    atomic_write(out_csv, lambda f: df.to_csv(f, index=False))

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
    with open(csv_path, mode='r', newline='', encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            frame = int(row['Frame'])
            xs = [int(x) for x in row['X'].split(',')] if row['X'] else []
            ys = [int(y) for y in row['Y'].split(',')] if row['Y'] else []
            onset = row.get('Onset', '') or ''
            bodypart = row.get('Bodypart', '') or ''
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
                'Onset': onset, 'Bodypart': bodypart,
                'Zones': zones, 'Touch': touch,
                'changed': False,
            }
    return data

def save_dataset(csv_path, total_frames, data, with_touch: bool = False):
    if not csv_path:
        return
    import csv, json
    with open(csv_path, mode='w', newline='', encoding="utf-8") as file:
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

def load_notes_csv(path) -> dict[int, str]:
    """Load notes as UTF-8, falling back to legacy Windows cp1252 files."""
    def _read(encoding: str) -> dict[int, str]:
        notes = {}
        with open(path, mode="r", newline="", encoding=encoding) as csv_file:
            reader = csv.reader(csv_file)
            next(reader, None)
            for row in reader:
                if len(row) == 2:
                    notes[int(row[0])] = row[1]
        return notes

    try:
        return _read("utf-8")
    except UnicodeDecodeError:
        print(f"WARN: {path} is not UTF-8; retrying as cp1252 (legacy notes file).")
        return _read("cp1252")


def save_parameter_to_csv(path, param_dict):
    if not path:
        return
    with open(path, mode='w', newline='', encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(['Frame', 'State'])
        for key, value in param_dict.items():
            writer.writerow([key, value])

def load_parameter_from_csv(path):
    d = {}
    if not path or not os.path.exists(path):
        return d
    with open(path, mode='r', encoding="utf-8") as csv_file:
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
    with open(csv_path, 'w', newline='', encoding="utf-8") as file:
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
    with open(csv_path, 'r', newline='', encoding="utf-8") as file:
        reader = csv.reader(file)
        next(reader, None)
        for row in reader:
            limb, frame, param_name, state = row
            frame = int(frame)
            state = _normalize_param_state(state)
            if param_name == "Parameter_1":
                p1[(limb, frame)] = state
            elif param_name == "Parameter_2":
                p2[(limb, frame)] = state
            elif param_name == "Parameter_3":
                p3[(limb, frame)] = state
    return p1, p2, p3

def extract_zones_from_file(file_path):
    if not file_path or not os.path.exists(file_path):
        return None
    zones = set()
    with open(file_path, 'r', encoding="utf-8") as f:
        for line in f:
            if 'Zones=' in line:
                zones.add(line.split('Zones=')[-1].strip())
    return list(zones) if zones else None
