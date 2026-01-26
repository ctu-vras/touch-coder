"""
frame_utils.py
Frame folder management and generation.
"""

import os
import re
import sys
import time
import shutil
import cv2


_FRAME_RE = re.compile(r"^frame(\d+)\.(jpg|jpeg|png)$", re.IGNORECASE)
FRAME_COUNT_TOLERANCE_PCT = 0.001  # allow up to 0.1% missing frames


def check_items_count(folder_path, expected_count):
    items = os.listdir(folder_path) if os.path.exists(folder_path) else []
    total_items = len(items)
    frame_indices = []
    non_frame_items = []

    for name in items:
        match = _FRAME_RE.match(name)
        if match:
            frame_indices.append(int(match.group(1)))
        else:
            non_frame_items.append(name)

    frame_count = len(frame_indices)
    expected_files = (expected_count + 1) if expected_count is not None and expected_count >= 0 else expected_count

    print("INFO: Frames dir exists:", os.path.exists(folder_path))
    print("INFO: Number of files in frames folder:", total_items)
    print("INFO: Frame files detected:", frame_count)
    if expected_files is not None:
        print("INFO: Expected frame files:", expected_files)
        print("INFO: Expected last frame index:", expected_count)
        allowed_missing = max(1, int(expected_files * FRAME_COUNT_TOLERANCE_PCT)) if expected_files > 0 else 0
        print(
            "INFO: Frame count tolerance:",
            f"{FRAME_COUNT_TOLERANCE_PCT * 100:.3f}% (allow {allowed_missing} missing frames)",
        )
    else:
        print("WARN: Expected frame count is undefined.")

    if non_frame_items:
        sample = ", ".join(non_frame_items[:5])
        print(f"WARN: Non-frame items in folder: {len(non_frame_items)} (sample: {sample})")

    if frame_indices:
        min_idx = min(frame_indices)
        max_idx = max(frame_indices)
        print(f"INFO: Frame index range in folder: {min_idx}..{max_idx}")
        if expected_count is not None and expected_count >= 0 and max_idx != expected_count:
            print("WARN: Max frame index does not match expected last index.")

    if expected_files is None:
        return False
    allowed_missing = max(1, int(expected_files * FRAME_COUNT_TOLERANCE_PCT)) if expected_files > 0 else 0
    min_ok = expected_files - allowed_missing
    if frame_count < min_ok or frame_count > expected_files:
        print(f"WARN: Frame file count mismatch: expected {expected_files}, found {frame_count}")
        return False
    if frame_count != expected_files:
        print(
            f"WARN: Frame file count within tolerance: expected {expected_files}, found {frame_count}"
        )
    return True


def create_frames(
    video_path,
    frames_dir,
    labeling_mode,
    video_name,
    progress_cb=None,
    progress_interval_s=1.0,
):
    print("INFO: Checking if frames need to be created...")
    last_progress_ts = 0.0
    start_time = None

    def maybe_report(count, total, stage):
        nonlocal last_progress_ts, start_time
        if progress_cb is None:
            return
        now = time.time()
        if start_time is None:
            start_time = now
        if (now - last_progress_ts) >= progress_interval_s or count >= total:
            last_progress_ts = now
            progress_cb(count, total, stage, now - start_time)

    if labeling_mode == "Reliability":
        original_video_name = video_name.replace("_reliability", "")
        original_frames_dir = os.path.join("Labeled_data", original_video_name, "frames")
        if os.path.exists(original_frames_dir):
            print(f"INFO: Found existing frames at {original_frames_dir}. Copying instead of generating...")
            os.makedirs(frames_dir, exist_ok=True)
            frame_files = os.listdir(original_frames_dir)
            total_files = len(frame_files)
            for index, filename in enumerate(frame_files):
                src = os.path.join(original_frames_dir, filename)
                dst = os.path.join(frames_dir, filename)
                shutil.copy2(src, dst)
                progress = ((index + 1) / total_files) * 100
                sys.stdout.write(f"\rCopying frames... {progress:.2f}%")
                sys.stdout.flush()
                maybe_report(index + 1, total_files, "Copying frames")
            print("\nINFO: Frames copied successfully.")
            return

    print("INFO: Creating frames from video...")
    vidcap = cv2.VideoCapture(video_path)
    is_opened = vidcap.isOpened()
    total_frames = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = vidcap.get(cv2.CAP_PROP_FPS)
    width = int(vidcap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(vidcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"INFO: VideoCapture opened: {is_opened}")
    print(f"INFO: Video properties: frames={total_frames}, fps={fps:.3f}, size={width}x{height}")
    success, image = vidcap.read()
    count = 0
    os.makedirs(frames_dir, exist_ok=True)
    while success:
        frame_path = os.path.join(frames_dir, f"frame{count}.jpg")
        cv2.imwrite(frame_path, image)
        success, image = vidcap.read()
        count += 1
        progress = (count / total_frames) * 100 if total_frames else 0
        sys.stdout.write(f"\rGenerating frames... {progress:.2f}%")
        sys.stdout.flush()
        maybe_report(count, total_frames or count, "Generating frames")
    vidcap.release()
    print("\nINFO: Frames have been created successfully.")
    if total_frames and count != total_frames:
        print(f"WARN: Generated {count} frames, but OpenCV reported {total_frames} total frames.")
