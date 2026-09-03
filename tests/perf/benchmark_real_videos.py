"""
tests/perf/benchmark_real_videos.py

End-to-end measurement on REAL videos: extracts frames from the generated
sample videos in videos/ (sample_480p / sample_1080p / sample_2160p.mp4,
created from cat3.mp4 with added grain) using the app's exact ffmpeg flags
(-q:v 2, frame%d.jpg — see adapters/frame_extractor.py), then times the
frame-load pipelines from benchmark_decode_pipeline across all extracted
frames (not one frame repeated), so JPEG content variety is included.

Reports per resolution: extraction speed, disk cost per frame, per-frame
load times for the current pipeline vs draft variants, and the projected
frames-directory size for a 30-minute recording.

Run:  uv run python tests/perf/benchmark_real_videos.py
Requires the sample videos; regenerate with the ffmpeg command in the
perf-experiment notes (loop cat3.mp4, scale + noise, libx264 crf 20).
"""

import os
import statistics
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_SRC = os.path.join(_ROOT, "src")
for p in (_SRC, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import imageio_ffmpeg  # noqa: E402

from benchmark_decode_pipeline import PIPELINES  # noqa: E402

# --- Tunables -----------------------------------------------------------------
VIDEOS = [  # (label, filename)
    ("480p", "sample_480p.mp4"),
    ("1080p", "sample_1080p.mp4"),
    ("4K-syn", "sample_2160p.mp4"),        # cat3 upscaled + grain (worst case)
    ("4K-real", "sample_real4k_bbb.mp4"),  # genuine 4K render (Big Buck Bunny)
]
FRAMES_PER_VIDEO = 100
PASSES = 3                      # timing passes over the extracted frame set
PROJECTION_MINUTES = 30         # frames-dir size projection for a recording
PROJECTION_FPS = 25


def extract_frames(ffmpeg, video_path, out_dir):
    """Extract FRAMES_PER_VIDEO frames with the app's exact quality flags."""
    os.makedirs(out_dir, exist_ok=True)
    cmd = [ffmpeg, "-y", "-v", "error", "-i", video_path,
           "-q:v", "2", "-start_number", "0",
           "-frames:v", str(FRAMES_PER_VIDEO),
           os.path.join(out_dir, "frame%d.jpg")]
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=True)
    elapsed = time.perf_counter() - t0
    frames = [os.path.join(out_dir, f"frame{i}.jpg")
              for i in range(FRAMES_PER_VIDEO)]
    frames = [f for f in frames if os.path.exists(f)]
    return frames, elapsed


def time_pipeline(fn, frames):
    """Median per-frame ms across all frames x PASSES (varied JPEG content)."""
    samples = []
    for _ in range(PASSES):
        for f in frames:
            t0 = time.perf_counter()
            fn(f)
            samples.append((time.perf_counter() - t0) * 1e3)
    return statistics.median(samples)


def main():
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"{FRAMES_PER_VIDEO} frames per video, app flags (-q:v 2), "
          f"median across frames x {PASSES} passes\n")

    rows = []
    with tempfile.TemporaryDirectory(prefix="tinytouch_video_bench_") as tmp:
        for label, filename in VIDEOS:
            video_path = os.path.join(_ROOT, "videos", filename)
            if not os.path.exists(video_path):
                print(f"SKIP {label}: {video_path} not found")
                continue
            frames, ext_s = extract_frames(
                ffmpeg, video_path, os.path.join(tmp, label))
            kb_per_frame = statistics.mean(
                os.path.getsize(f) for f in frames) / 1024
            times = [(name, time_pipeline(fn, frames)) for name, fn in PIPELINES]
            rows.append((label, ext_s, kb_per_frame, times))

    print(f"{'video':8} {'extract':>10} {'kB/frame':>9} " +
          "".join(f"{name:>24}" for name, _ in PIPELINES))
    for label, ext_s, kb, times in rows:
        rate = FRAMES_PER_VIDEO / ext_s
        print(f"{label:8} {rate:>6.0f}fr/s {kb:>8.0f}k " +
              "".join(f"{t:>21.2f} ms" for _, t in times))

    print(f"\nProjection for a {PROJECTION_MINUTES}-minute {PROJECTION_FPS}fps "
          f"recording ({PROJECTION_MINUTES * 60 * PROJECTION_FPS:,} frames):")
    for label, _, kb, times in rows:
        total_gb = kb * PROJECTION_MINUTES * 60 * PROJECTION_FPS / 1024 / 1024
        cur = dict(times)["current (full+LANCZOS)"]
        fast = dict(times)["draft + BILINEAR"]
        print(f"  {label:6} frames dir ~{total_gb:5.1f} GB | per worker: "
              f"current {1000 / cur:>6.1f} fps -> draft+BILINEAR "
              f"{1000 / fast:>6.1f} fps")


if __name__ == "__main__":
    main()
