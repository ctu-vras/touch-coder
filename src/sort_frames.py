import os, json, shutil, pandas as pd, math, re, ast
from collections import defaultdict, OrderedDict

# ───────── Global constant – change here if you need another FPS ───────────
FRAME_RATE = 30
MS_PER_FRAME = 1000.0 / FRAME_RATE


def process_touch_data_strict_transitions(csv_path: str, images_dir: str, output_dir: str) -> None:
    print(f"SORT: Loading CSV: {csv_path}")
    df = pd.read_csv(csv_path, skiprows=11)
    os.makedirs(output_dir, exist_ok=True)

    limbs = ['LH', 'LL', 'RH', 'RL']
    touch_seq_id = {l: 0 for l in limbs}
    active_touch_id = {l: None for l in limbs}
    last_zones = {l: [] for l in limbs}
    last_transition = {l: None for l in limbs}

    meta_per_touch = {}
    frames_per_touch = defaultdict(list)
    all_touch_frames = set()

    print(f"SORT: Starting touch analysis across {len(df)} frames...")

    for _, row in df.iterrows():
        frame = int(row['Frame'])
        for limb in limbs:
            onset = str(row.get(f'{limb}_Onset', '')).lower()
            raw_z = row.get(f'{limb}_Zones', '[]')
            try:
                zones = ast.literal_eval(raw_z) if isinstance(raw_z, str) else []
            except Exception:
                zones = []

            prev_z = set(last_zones[limb])
            curr_z = set(zones)

            if onset == 'on' and active_touch_id[limb] is None:
                touch_seq_id[limb] += 1
                tid = f'{limb}_{touch_seq_id[limb]}'
                active_touch_id[limb] = tid
                meta_per_touch[tid] = {
                    'limb': limb,
                    'start_frame': frame,
                    'end_frame': None,
                    'frames': [],
                    'zone_transitions': [{'frame': frame, 'zones': zones, 'type': 'on'}]
                }
                last_transition[limb] = frame

            if active_touch_id[limb] is not None:
                tid = active_touch_id[limb]
                meta_per_touch[tid]['frames'].append(frame)
                frames_per_touch[tid].append(frame)
                all_touch_frames.add(frame)

                if onset != 'off' and curr_z != prev_z and frame != last_transition[limb]:
                    meta_per_touch[tid]['zone_transitions'].append(
                        {'frame': frame, 'zones': zones, 'type': 'on'}
                    )
                    last_transition[limb] = frame

                if onset == 'off':
                    meta_per_touch[tid]['zone_transitions'].append(
                        {'frame': frame, 'zones': zones, 'type': 'off'}
                    )
                    meta_per_touch[tid]['end_frame'] = frame
                    active_touch_id[limb] = None
                    last_transition[limb] = frame

            last_zones[limb] = zones

    frames_per_touch['touch'] = sorted(all_touch_frames)

    existing_frames = {
        int(fname[5:-4])
        for fname in os.listdir(images_dir)
        if fname.startswith('frame') and fname.endswith('.jpg') and fname[5:-4].isdigit()
    }

    frames_per_touch['no_touch'] = sorted(existing_frames - all_touch_frames)

    print(f"SORT: Copying images and writing metadata...")

    for tid, fr_list in frames_per_touch.items():
        if tid == 'touch':
            dst_folder = os.path.join(output_dir, 'touch')
        elif tid == 'no_touch':
            dst_folder = os.path.join(output_dir, 'no_touch')
        else:
            dst_folder = os.path.join(output_dir, 'touches', tid)

        os.makedirs(dst_folder, exist_ok=True)

        for f in fr_list:
            src = os.path.join(images_dir, f'frame{f}.jpg')
            dst = os.path.join(dst_folder, f'frame{f}.jpg')
            if os.path.exists(src):
                shutil.copyfile(src, dst)

        if tid not in ('touch', 'no_touch'):
            meta = meta_per_touch[tid]
            ordered = {'end_frame': meta['end_frame']}
            ordered.update({k: v for k, v in meta.items() if k != 'end_frame'})
            with open(os.path.join(dst_folder, 'metadata.json'), 'w') as fh:
                json.dump(ordered, fh, indent=2)

    print(f"SORT: DONE – Frames sorted into: {output_dir}")


def datavuy_process_touch_data_strict_transitions(csv_dir: str, images_dir: str, output_dir: str, fps: int | None = None) -> None:
    frame_rate = fps or FRAME_RATE
    ms_per_frame = 1000.0 / frame_rate

    os.makedirs(output_dir, exist_ok=True)

    touch_seq = defaultdict(int)
    frames_by_tid = defaultdict(list)
    meta = {}
    all_touch = set()

    print(f"SORT: Scanning CSV directory: {csv_dir}")
    csv_files = sorted([f for f in os.listdir(csv_dir) if f.lower().endswith('.csv')])
    print(f"SORT: Found {len(csv_files)} CSV file(s)")

    for idx, fname in enumerate(csv_files, 1):
        print(f"\nSORT: [{idx}/{len(csv_files)}] Processing: {fname}")
        limb_match = re.search(r"_([A-Za-z]{2})\.csv$", fname)
        limb = limb_match.group(1) if limb_match else "XX"

        df = pd.read_csv(os.path.join(csv_dir, fname))
        print(f"SORT: Loaded {len(df)} rows from {fname}")

        loc_col = next((c for c in df.columns if c.startswith("location_")), None)
        onset_col = next((c for c in df.columns if c.endswith("_onset")), None)
        offset_col = next((c for c in df.columns if c.endswith("_offset")), None)
        type_col = next((c for c in df.columns if c.startswith("touch_type_") and not c.endswith(("_onset", "_offset"))), None)

        if None in (loc_col, onset_col, offset_col, type_col):
            print(f"SORT: Skipping {fname} – missing expected columns")
            continue

        touch_count = 0
        for _, row in df.iterrows():
            onset_ms = row[onset_col]
            offset_ms = row[offset_col]
            if pd.isna(onset_ms) or pd.isna(offset_ms) or not (pd.api.types.is_number(onset_ms) and pd.api.types.is_number(offset_ms)):
                continue

            start_fr = int(math.floor(onset_ms / ms_per_frame))
            end_fr = int(math.floor(offset_ms / ms_per_frame))
            if start_fr > end_fr:
                continue

            loc = "" if pd.isna(row[loc_col]) else str(row[loc_col])
            t_type = "" if pd.isna(row[type_col]) else str(row[type_col])

            touch_seq[limb] += 1
            tid = f"{limb}_{touch_seq[limb]}"
            frames = list(range(start_fr, end_fr + 1))
            frames_by_tid[tid] = frames
            all_touch.update(frames)

            meta[tid] = {
                "limb": limb,
                "start_frame": start_fr,
                "end_frame": end_fr,
                "frames": frames,
                "zone_transitions": [
                    {"frame": start_fr, "zones": [z.strip() for z in loc.split(',') if z.strip()], "type": "on"},
                    {"frame": end_fr, "zones": [z.strip() for z in loc.split(',') if z.strip()], "type": "off"}
                ],
                "touch_type": t_type,
                "row": row.to_dict()
            }

            touch_count += 1

        print(f"SORT: Detected {touch_count} touches in {fname}")

    print(f"SORT: Aggregating frames into 'touch' and 'no_touch'")
    frames_by_tid["touch"] = sorted(all_touch)
    existing_frames = {
        int(f[5:-4]) for f in os.listdir(images_dir)
        if f.startswith("frame") and f.endswith(".jpg") and f[5:-4].isdigit()
    }
    frames_by_tid["no_touch"] = sorted(existing_frames - all_touch)

    print(f"SORT: Found {len(existing_frames)} total frames")
    print(f"SORT: Touch frames: {len(frames_by_tid['touch'])}, No-touch frames: {len(frames_by_tid['no_touch'])}")
    print(f"SORT: Copying frames and saving metadata...")

    for i, (tid, fr_list) in enumerate(frames_by_tid.items(), 1):
        dst_folder = (
            os.path.join(output_dir, "touch") if tid == "touch" else
            os.path.join(output_dir, "no_touch") if tid == "no_touch" else
            os.path.join(output_dir, "touches", tid)
        )
        os.makedirs(dst_folder, exist_ok=True)

        print(f"SORT: [{i}/{len(frames_by_tid)}] Copying {len(fr_list)} frames to '{dst_folder}'")
        copied = 0
        for f in fr_list:
            src = os.path.join(images_dir, f"frame{f}.jpg")
            dst = os.path.join(dst_folder, f"frame{f}.jpg")
            if os.path.exists(src):
                shutil.copyfile(src, dst)
                copied += 1
        print(f"SORT:   {copied}/{len(fr_list)} frames copied")

        if tid not in ("touch", "no_touch"):
            ordered = OrderedDict([("end_frame", meta[tid]["end_frame"])])
            ordered.update((k, v) for k, v in meta[tid].items() if k != "end_frame")
            with open(os.path.join(dst_folder, "metadata.json"), "w") as fh:
                json.dump(ordered, fh, indent=2)
            print(f"SORT:   metadata.json saved")

    print(f"\nSORT: DONE – Datavyu frames sorted into: {output_dir} (fps = {frame_rate})")


# Example usage (adapt as needed)
if __name__ == '__main__':
    csv_path = 'datavyu_test'
    images_dir = 'Labeled_data/cat3_mp4/frames'
    output_dir = 'datavyu_test_frames'
    datavuy_process_touch_data_strict_transitions(csv_path, images_dir, output_dir)
