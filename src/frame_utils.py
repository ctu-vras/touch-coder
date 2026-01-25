"""
frame_utils.py
Frame folder management and generation.
"""

import os
import sys
import time
import shutil
import cv2


def check_items_count(folder_path, expected_count):
    items = os.listdir(folder_path) if os.path.exists(folder_path) else []
    print("INFO: Number of files in frames folder: ", len(items)-1)
    print("INFO: Number of expected frames in the folder", expected_count)
    return (len(items)-1) == expected_count


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
    total_frames = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
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
    print("\nINFO: Frames have been created successfully.")
