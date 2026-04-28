"""
frame_utils.py
Frame folder management and generation.
"""

import os
import re
import sys
import time
import shutil
import subprocess
import threading
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


def _get_ffmpeg_exe():
    """Return path to bundled ffmpeg binary, or None if unavailable."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except ImportError:
        pass
    return None


def _count_jpg_files(frames_dir):
    try:
        return sum(1 for f in os.listdir(frames_dir) if f.endswith(".jpg"))
    except FileNotFoundError:
        return 0


def _extract_frames_ffmpeg(video_path, frames_dir, progress_cb, progress_interval_s):
    """Extract frames using the bundled ffmpeg binary (fast path)."""
    ffmpeg_exe = _get_ffmpeg_exe()
    if ffmpeg_exe is None:
        print("INFO: ffmpeg binary not available; will fall back to OpenCV.")
        return False

    print(f"INFO: Using ffmpeg for frame extraction: {ffmpeg_exe}")

    os.makedirs(frames_dir, exist_ok=True)
    output_pattern = os.path.join(frames_dir, "frame%d.jpg")

    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    print(f"INFO: Video properties: frames={total_frames}, fps={fps:.3f}, size={width}x{height}")

    # -nostats -loglevel error: keeps stderr almost silent, so the OS pipe
    # buffer can't fill up and deadlock ffmpeg while we poll for progress.
    cmd = [
        ffmpeg_exe,
        "-nostats",
        "-loglevel", "error",
        "-i", video_path,
        "-q:v", "2",
        "-start_number", "0",
        output_pattern,
    ]

    start_time = time.time()
    last_progress_ts = 0.0

    print(f"INFO: Spawning ffmpeg: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    print(f"INFO: ffmpeg started (pid={process.pid})")

    # Defense-in-depth: drain stdout/stderr concurrently so the pipes can
    # never block ffmpeg even if a future change makes it chatty again.
    stdout_chunks = []
    stderr_chunks = []

    def _drain(stream, sink):
        try:
            for chunk in iter(lambda: stream.read(4096), b""):
                if not chunk:
                    break
                sink.append(chunk)
        except Exception as exc:
            print(f"WARN: pipe drainer error: {exc}")

    stdout_thread = threading.Thread(target=_drain, args=(process.stdout, stdout_chunks), daemon=True)
    stderr_thread = threading.Thread(target=_drain, args=(process.stderr, stderr_chunks), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    while process.poll() is None:
        time.sleep(progress_interval_s)
        if progress_cb and total_frames:
            now = time.time()
            if (now - last_progress_ts) >= progress_interval_s:
                last_progress_ts = now
                count = _count_jpg_files(frames_dir)
                progress_cb(count, total_frames, "Generating frames", now - start_time)

    rc = process.returncode
    duration = time.time() - start_time
    print(f"INFO: ffmpeg exited rc={rc} after {duration:.1f}s")

    # Wait briefly for drainers; pipes should be closed once ffmpeg exited.
    stdout_thread.join(timeout=5.0)
    stderr_thread.join(timeout=5.0)

    if rc != 0:
        stderr = b"".join(stderr_chunks).decode(errors="replace")
        print(f"ERROR: ffmpeg failed (exit {rc}): {stderr[-1000:]}")
        return False

    print("INFO: Counting extracted frame files (this can take a moment with very long videos)...")
    listdir_start = time.time()
    frame_count = _count_jpg_files(frames_dir)
    print(f"INFO: ffmpeg extracted {frame_count} frames "
          f"(file count took {time.time() - listdir_start:.1f}s).")

    # Final progress report.
    if progress_cb and total_frames:
        progress_cb(frame_count, total_frames, "Generating frames", time.time() - start_time)

    if total_frames and abs(frame_count - total_frames) > max(1, int(total_frames * FRAME_COUNT_TOLERANCE_PCT)):
        print(f"WARN: ffmpeg generated {frame_count} frames, but expected {total_frames}.")

    return True


def _extract_frames_opencv(video_path, frames_dir, progress_cb, progress_interval_s):
    """Extract frames using OpenCV sequential decode+write (fallback path)."""
    print("INFO: Using OpenCV for frame extraction (fallback).")

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

    last_progress_ts = 0.0
    start_time = time.time()

    while success:
        frame_path = os.path.join(frames_dir, f"frame{count}.jpg")
        cv2.imwrite(frame_path, image)
        success, image = vidcap.read()
        count += 1
        progress = (count / total_frames) * 100 if total_frames else 0
        sys.stdout.write(f"\rGenerating frames... {progress:.2f}%")
        sys.stdout.flush()
        if progress_cb:
            now = time.time()
            if (now - last_progress_ts) >= progress_interval_s or count >= total_frames:
                last_progress_ts = now
                progress_cb(count, total_frames or count, "Generating frames", now - start_time)

    vidcap.release()
    sys.stdout.write("\n")
    print(f"INFO: OpenCV extracted {count} frames in {time.time() - start_time:.1f}s.")
    if total_frames and count != total_frames:
        print(f"WARN: Generated {count} frames, but OpenCV reported {total_frames} total frames.")


def create_frames(
    video_path,
    frames_dir,
    labeling_mode,
    video_name,
    progress_cb=None,
    progress_interval_s=1.0,
):
    print("INFO: Checking if frames need to be created...")

    if labeling_mode == "Reliability":
        original_video_name = video_name.replace("_reliability", "")
        original_frames_dir = os.path.join("Labeled_data", original_video_name, "frames")
        if os.path.exists(original_frames_dir):
            print(f"INFO: Found existing frames at {original_frames_dir}. Copying instead of generating...")
            os.makedirs(frames_dir, exist_ok=True)
            frame_files = os.listdir(original_frames_dir)
            total_files = len(frame_files)
            start_time = time.time()
            for index, filename in enumerate(frame_files):
                src = os.path.join(original_frames_dir, filename)
                dst = os.path.join(frames_dir, filename)
                shutil.copy2(src, dst)
                if progress_cb:
                    now = time.time()
                    progress_cb(index + 1, total_files, "Copying frames", now - start_time)
            print(f"INFO: Frames copied successfully ({total_files} files in {time.time() - start_time:.1f}s).")
            return

    print("INFO: Creating frames from video...")

    # Try ffmpeg first (faster), fall back to OpenCV.
    if not _extract_frames_ffmpeg(video_path, frames_dir, progress_cb, progress_interval_s):
        print("INFO: Falling back to OpenCV extraction.")
        _extract_frames_opencv(video_path, frames_dir, progress_cb, progress_interval_s)
    print("INFO: create_frames() finished.")
