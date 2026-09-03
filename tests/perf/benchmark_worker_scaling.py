"""
tests/perf/benchmark_worker_scaling.py

Does giving the decode pool more threads (FrameBuffer max_workers=3 today)
speed up window filling? PIL releases the GIL during decode and resize, so
scaling should be near-linear up to the physical core count — this measures
where it actually flattens on THIS machine, using real 4K frames (the
heaviest case) and the current production pipeline.

Run:  uv run python tests/perf/benchmark_worker_scaling.py
Requires videos/sample_real4k_bbb.mp4 (see benchmark_real_videos.py).
"""

import os
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _HERE)

import imageio_ffmpeg  # noqa: E402

from benchmark_decode_pipeline import pipe_current, pipe_draft_bilinear  # noqa: E402

# --- Tunables -----------------------------------------------------------------
SOURCE_VIDEO = os.path.join(_ROOT, "videos", "sample_real4k_bbb.mp4")
FRAME_COUNT = 50            # one prefetch-window fill (base ahead=50)
WORKER_COUNTS = [1, 2, 3, 4, 6, 8, 12]
PASSES = 3


def fill_window(frames, workers, pipeline):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        t0 = time.perf_counter()
        list(pool.map(pipeline, frames))
        return time.perf_counter() - t0


def main():
    if not os.path.exists(SOURCE_VIDEO):
        sys.exit(f"missing {SOURCE_VIDEO} — generate the sample videos first")
    print(f"CPU count (logical): {os.cpu_count()} | filling a "
          f"{FRAME_COUNT}-frame window with real 4K frames, best of {PASSES}\n")

    with tempfile.TemporaryDirectory(prefix="tinytouch_scaling_bench_") as tmp:
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error",
             "-i", SOURCE_VIDEO, "-q:v", "2",
             "-frames:v", str(FRAME_COUNT), "-start_number", "0",
             os.path.join(tmp, "frame%d.jpg")],
            check=True, capture_output=True)
        frames = [os.path.join(tmp, f"frame{i}.jpg") for i in range(FRAME_COUNT)]
        frames = [f for f in frames if os.path.exists(f)]

        for name, pipeline in [("current (full+LANCZOS)", pipe_current),
                               ("draft + BILINEAR", pipe_draft_bilinear)]:
            print(f"{name}:")
            print(f"  {'workers':>8} {'window fill':>12} {'frames/s':>10} "
                  f"{'vs 1 worker':>12}")
            base = None
            for w in WORKER_COUNTS:
                secs = min(fill_window(frames, w, pipeline)
                           for _ in range(PASSES))
                fps = len(frames) / secs
                base = base or fps
                marker = "  <- current" if w == 3 else ""
                print(f"  {w:>8} {secs * 1e3:>10.0f}ms {fps:>10.1f} "
                      f"{fps / base:>11.1f}x{marker}")
            print()


if __name__ == "__main__":
    main()
