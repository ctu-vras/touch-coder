"""
adapters/unified_repo.py
READ-ONLY legacy state loaders: the unified-CSV journal, export-CSV import,
per-limb CSVs, notes, limb parameters, and the clothes sidecar.

These are the migration + disaster-recovery path, kept permanently. The
working-state source of truth is now ONE SQLite database per video
(`adapters.sqlite_repo`), imported from these readers exactly once by
`service_layer.state_migration`.

Nothing in this module WRITES any more. The journal writer
(`save_unified_dataset`), its row serializer, its torn-tail repair and its
load-time compaction were deleted with the journal itself: an in-place UPSERT
inside one transaction makes duplicate rows, tail repair and compaction
meaningless. `load_unified_dataset` still resolves duplicate `Frame` rows
last-writer-wins and still tolerates a torn final row, because journals written
by older builds are exactly what it has to migrate.
"""

import csv
import io
import json
import os
import time
import pandas as pd
from typing import Dict, Optional

from domain.model import (
    FrameBundle,
    FrameRecord,
    _normalize_param_state,
    empty_record,
)


UNIFIED_COLUMNS = ["Frame", "Note", "Params", "LH", "RH", "LL", "RL"]


def _read_unified_journal(csv_path: str) -> pd.DataFrame:
    """Parse the journal, tolerating a crash-torn final row.

    An older build could be killed mid-append and leave a partial last line.
    The bytes before the last complete newline are always intact, so parse
    those and drop the fragment. (SQLite cannot produce this state at all —
    this exists purely to migrate journals that already have it.)
    """
    try:
        return pd.read_csv(csv_path)
    except pd.errors.ParserError:
        with open(csv_path, "rb") as f:
            data = f.read()
        if data.endswith(b"\n"):
            raise
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            raise
        print(
            f"WARNING: Ignoring crash-torn final unified row -> {csv_path}",
            flush=True,
        )
        return pd.read_csv(io.BytesIO(data[: last_newline + 1]))


def load_unified_dataset(csv_path: str, progress_cb=None) -> Dict[int, FrameBundle]:
    """Legacy journal -> in-memory frames store, duplicate rows resolved
    last-writer-wins (rows are iterated in file order, so a later row for the
    same frame overwrites an earlier one)."""
    frames: Dict[int, FrameBundle] = {}
    if not (csv_path and os.path.exists(csv_path)):
        print(f"DEBUG: Unified not found -> {csv_path}", flush=True)
        return frames
    # DELIBERATE: the diagnostics live OUTSIDE the try below. This function is
    # the first rung of the disaster-recovery ladder and its `except Exception`
    # means "assume there is no data" — a one-way decision, because the caller
    # (state_migration) then renames the sources `*.migrated`. A print must
    # never be able to trigger it. It once could: the log line below used to
    # contain a Unicode arrow, and on a redirected stdout (cp1252 locale
    # encoding) the resulting UnicodeEncodeError was caught as "unreadable CSV"
    # and the researcher's whole journal was imported as ZERO frames.
    try:
        size = os.path.getsize(csv_path)
    except OSError as exc:
        print(f"ERROR: Could not stat unified CSV ({exc}); starting empty", flush=True)
        return frames
    print(f"DEBUG: Unified exists ({size} bytes) -> {csv_path}", flush=True)
    if size == 0:
        print("DEBUG: Unified is empty (0 bytes); starting with empty frames", flush=True)
        return frames

    t0 = time.time()
    print("DEBUG: load_unified_dataset: pd.read_csv starting...", flush=True)
    try:
        df = _read_unified_journal(csv_path)
    except pd.errors.EmptyDataError:
        print("DEBUG: Unified had no columns (EmptyDataError) — starting with empty frames", flush=True)
        return frames
    except Exception as e:
        print(f"ERROR: Failed to read unified CSV: {e} — starting empty", flush=True)
        return frames
    print(f"DEBUG: load_unified_dataset: pd.read_csv done in {time.time() - t0:.2f}s "
          f"(rows={len(df)}, cols={len(df.columns)})", flush=True)

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
        print(f"DEBUG: import_unified_from_export: file does not exist -> {export_csv_path}", flush=True)
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
    print(f"DEBUG: import_unified_from_export -> frames={len(frames)} "
          f"from {export_csv_path} in {time.time() - iter_start:.1f}s", flush=True)
    return frames

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


def load_limb_parameters(csv_path):
    """
    Returns three dicts (for Parameter_1..3) keyed by (limb, frame) -> state.

    Migration reader for `state/<name>_limb_parameters.csv`. That sidecar has
    had no app reader and no app writer for several versions (the states live
    in the journal's limb blobs), so `state_migration` quarantines its rows in
    `legacy_limb_params` rather than folding them into LimbParams — see that
    module.
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
    """Zone names from a legacy `state/<name>_clothes.txt`, in the exact shape
    the export metadata contract expects: set-deduplicated, UNSORTED, and a
    multi-zone dot contributing its whole comma-joined `Zones=` tail as ONE
    entry.

    Disaster-recovery reader. The live path is
    `SqliteRepository.clothes_zone_list`, which reproduces this byte-for-byte
    (pinned by tests/unit/test_sqlite_repo.py).
    """
    if not file_path or not os.path.exists(file_path):
        return None
    zones = set()
    with open(file_path, 'r', encoding="utf-8") as f:
        for line in f:
            if 'Zones=' in line:
                zones.add(line.split('Zones=')[-1].strip())
    return list(zones) if zones else None
