"""
data_utils.py
I/O helpers split from LabelingApp – CSV/notes/parameters and a small
CSV loader. Pure functions where possible; controller passes what’s needed.
"""

import csv
import json
import os
import pandas as pd
from typing import TypedDict, NotRequired, List, Optional, Dict

class FrameRecord(TypedDict):
    X: List[int]                 # 0+ points in X
    Y: List[int]                 # 0+ points in Y (aligned with X)
    Onset: str                   # "On" | "Off" | ""
    Bodypart: str                # "LH"|"RH"|"LL"|"RL"|"" (for the owning limb CSV this is redundant, but present)
    Look: str                    # "Yes"|"No"|""
    Zones: List[str]             # always list, may be []
    Touch: Optional[int]         # 1 | 0 | None
    changed: NotRequired[bool]   # in-memory only, not written to CSV

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
                touch_val = 1 if onset == "On" else 0
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

    # normalize look columns
    for limb in ['LH', 'LL', 'RH', 'RL']:
        merged_df = merged_df.drop([c for c in merged_df.columns if c == f'{limb}_Look_x'], axis=1)
        merged_df = merged_df.rename(columns={f'{limb}_Look_y': f'{limb}_Look_x'})

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


def _prepend_header(path, program_version, video_name, labeling_mode, frame_rate, clothes_list):
    with open(path, 'r') as f:
        data = f.read()
    with open(path, 'w') as f:
        f.write(f"Program Version: {program_version}\n")
        f.write(f"Video Name: {video_name}\n")
        f.write(f"Labeling Mode: {labeling_mode}\n")
        f.write(f"Frame Rate: {frame_rate}\n")
        f.write(f"Zones Covered With Clothes: {clothes_list}\n")
        f.write("\n")
        f.write(data)


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
