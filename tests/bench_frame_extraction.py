"""
Benchmark: frame extraction methods comparison.

  A) Original sequential (cv2 decode + cv2.imwrite one by one)
  B) Threaded writes   (cv2 decode sequential, cv2.imwrite in thread pool)
  C) FFmpeg subprocess (imageio-ffmpeg bundled binary, single call)

Usage:
    .venv\\Scripts\\python.exe tests/bench_frame_extraction.py [video_file] [max_frames]

Examples:
    .venv\\Scripts\\python.exe tests/bench_frame_extraction.py videoplayback.mp4 27000
    .venv\\Scripts\\python.exe tests/bench_frame_extraction.py              # first .mp4, all frames
"""

import os
import sys
import time
import shutil
import subprocess
import concurrent.futures
import cv2

# ---------------------------------------------------------------------------
# Make src/ importable so we can reuse the original create_frames
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VIDEO_DIR = os.path.join(PROJECT_ROOT, "Videos")
DATA_DIR = os.path.join(PROJECT_ROOT, "tests", "data")

# Parse args: [video_file] [max_frames]
if len(sys.argv) > 1:
    VIDEO_FILE = sys.argv[1]
    if not os.path.exists(os.path.join(VIDEO_DIR, VIDEO_FILE)):
        print(f"ERROR: {VIDEO_FILE} not found in Videos/")
        sys.exit(1)
else:
    VIDEO_FILE = None
    for f in os.listdir(VIDEO_DIR):
        if f.lower().endswith(".mp4"):
            VIDEO_FILE = f
            break

MAX_FRAMES = int(sys.argv[2]) if len(sys.argv) > 2 else None

if VIDEO_FILE is None:
    print("ERROR: No .mp4 file found in Videos/")
    sys.exit(1)

VIDEO_PATH = os.path.join(VIDEO_DIR, VIDEO_FILE)
VIDEO_NAME = os.path.splitext(VIDEO_FILE)[0]

FRAMES_DIR_ORIGINAL = os.path.join(DATA_DIR, VIDEO_NAME, "frames_original")
FRAMES_DIR_THREADED = os.path.join(DATA_DIR, VIDEO_NAME, "frames_threaded")
FRAMES_DIR_FFMPEG = os.path.join(DATA_DIR, VIDEO_NAME, "frames_ffmpeg")

WRITER_THREADS = 4


# ---------------------------------------------------------------------------
# Original (sequential) — reimplemented with max_frames support
# ---------------------------------------------------------------------------
def create_frames_original(video_path, frames_dir, max_frames=None):
    """Original sequential approach: cv2.read() + cv2.imwrite() one by one."""
    print("INFO: [original] Extracting frames sequentially...")
    vidcap = cv2.VideoCapture(video_path)
    if not vidcap.isOpened():
        print("ERROR: Could not open video")
        return 0

    total_frames = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = vidcap.get(cv2.CAP_PROP_FPS)
    width = int(vidcap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(vidcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    target = min(total_frames, max_frames) if max_frames else total_frames
    print(f"INFO: [original] Video: frames={total_frames}, fps={fps:.3f}, size={width}x{height}")
    print(f"INFO: [original] Extracting {target} frames")

    os.makedirs(frames_dir, exist_ok=True)

    count = 0
    success, image = vidcap.read()
    while success and (max_frames is None or count < max_frames):
        frame_path = os.path.join(frames_dir, f"frame{count}.jpg")
        cv2.imwrite(frame_path, image)
        count += 1
        if count % 2000 == 0:
            progress = (count / target) * 100
            print(f"  [original] {progress:.1f}%  ({count}/{target})")
        success, image = vidcap.read()

    vidcap.release()
    print(f"INFO: [original] Done. {count} frames written.")
    return count


# ---------------------------------------------------------------------------
# Option A: threaded writes
# ---------------------------------------------------------------------------
def create_frames_threaded(video_path, frames_dir, max_frames=None, num_threads=WRITER_THREADS):
    """Decode sequentially, write JPEGs in a thread pool."""
    print(f"INFO: [threaded] Extracting frames with {num_threads} writer threads...")
    vidcap = cv2.VideoCapture(video_path)
    if not vidcap.isOpened():
        print("ERROR: Could not open video")
        return 0

    total_frames = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = vidcap.get(cv2.CAP_PROP_FPS)
    width = int(vidcap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(vidcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    target = min(total_frames, max_frames) if max_frames else total_frames
    print(f"INFO: [threaded] Video: frames={total_frames}, fps={fps:.3f}, size={width}x{height}")
    print(f"INFO: [threaded] Extracting {target} frames")

    os.makedirs(frames_dir, exist_ok=True)

    count = 0
    futures = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        success, image = vidcap.read()
        while success and (max_frames is None or count < max_frames):
            frame_path = os.path.join(frames_dir, f"frame{count}.jpg")
            futures.append(pool.submit(cv2.imwrite, frame_path, image))
            count += 1
            if count % 2000 == 0:
                progress = (count / target) * 100
                print(f"  [threaded] {progress:.1f}%  ({count}/{target})")
            success, image = vidcap.read()

        for fut in futures:
            fut.result()

    vidcap.release()
    print(f"INFO: [threaded] Done. {count} frames written.")
    return count


# ---------------------------------------------------------------------------
# Option B: FFmpeg subprocess via imageio-ffmpeg
# ---------------------------------------------------------------------------
def create_frames_ffmpeg(video_path, frames_dir, max_frames=None):
    """Use bundled ffmpeg binary to extract all frames in one call."""
    import imageio_ffmpeg

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"INFO: [ffmpeg] Using binary: {ffmpeg_exe}")

    os.makedirs(frames_dir, exist_ok=True)

    output_pattern = os.path.join(frames_dir, "frame%d.jpg")

    cmd = [ffmpeg_exe]

    # Limit duration if max_frames is set
    if max_frames:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
        duration = max_frames / fps
        cmd += ["-t", f"{duration:.3f}"]
        print(f"INFO: [ffmpeg] Limiting to {max_frames} frames ({duration:.1f}s at {fps:.1f}fps)")

    cmd += [
        "-i", video_path,
        "-q:v", "2",           # JPEG quality (2 = high, ~same as cv2 default)
        "-start_number", "0",  # start at frame0.jpg to match our convention
        output_pattern,
    ]

    print(f"INFO: [ffmpeg] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR: [ffmpeg] Exit code {result.returncode}")
        print(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
        return 0

    frame_count = len([f for f in os.listdir(frames_dir) if f.endswith(".jpg")])
    print(f"INFO: [ffmpeg] Done. {frame_count} frames written.")
    return frame_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def clean_dir(path):
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def count_files(path):
    return len(os.listdir(path)) if os.path.exists(path) else 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    cap = cv2.VideoCapture(VIDEO_PATH)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    target = min(total, MAX_FRAMES) if MAX_FRAMES else total

    print("=" * 60)
    print(f"Video:      {VIDEO_FILE}")
    print(f"Path:       {VIDEO_PATH}")
    print(f"Total:      {total} frames ({total/fps:.1f}s at {fps:.1f}fps)")
    print(f"Extracting: {target} frames ({target/fps:.1f}s)")
    print("=" * 60)

    results = []

    # --- Original (sequential) ---
    print("\n--- Original (sequential) ---")
    clean_dir(FRAMES_DIR_ORIGINAL)
    t0 = time.perf_counter()
    create_frames_original(VIDEO_PATH, FRAMES_DIR_ORIGINAL, MAX_FRAMES)
    t_original = time.perf_counter() - t0
    n_original = count_files(FRAMES_DIR_ORIGINAL)
    print(f"Time:   {t_original:.2f}s  |  Frames: {n_original}")
    results.append(("Original (sequential)", t_original, n_original))

    # --- Threaded (Option A) ---
    print("\n--- Threaded writes (Option A) ---")
    clean_dir(FRAMES_DIR_THREADED)
    t0 = time.perf_counter()
    create_frames_threaded(VIDEO_PATH, FRAMES_DIR_THREADED, MAX_FRAMES)
    t_threaded = time.perf_counter() - t0
    n_threaded = count_files(FRAMES_DIR_THREADED)
    print(f"Time:   {t_threaded:.2f}s  |  Frames: {n_threaded}")
    results.append(("Threaded writes (A)", t_threaded, n_threaded))

    # --- FFmpeg subprocess (Option B) ---
    print("\n--- FFmpeg subprocess (Option B) ---")
    clean_dir(FRAMES_DIR_FFMPEG)
    t0 = time.perf_counter()
    create_frames_ffmpeg(VIDEO_PATH, FRAMES_DIR_FFMPEG, MAX_FRAMES)
    t_ffmpeg = time.perf_counter() - t0
    n_ffmpeg = count_files(FRAMES_DIR_FFMPEG)
    print(f"Time:   {t_ffmpeg:.2f}s  |  Frames: {n_ffmpeg}")
    results.append(("FFmpeg subprocess (B)", t_ffmpeg, n_ffmpeg))

    # --- Summary ---
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    baseline = results[0][1]
    for name, elapsed, frames in results:
        speedup = baseline / elapsed if elapsed > 0 else 0
        print(f"  {name:<25s}  {elapsed:7.2f}s  |  {frames} frames  |  {speedup:.2f}x")
    print(f"\n  Frame counts match: {len(set(r[2] for r in results)) == 1}")
    print("=" * 60)


if __name__ == "__main__":
    main()
