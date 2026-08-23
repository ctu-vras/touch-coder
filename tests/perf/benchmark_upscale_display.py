"""
tests/perf/benchmark_upscale_display.py

Experiment for the LOW-RES VIDEO ON A BIG MONITOR problem: today
resize_for_buffer never upscales (max_width = min(display, target)), so a
480p video on a 4K monitor renders at 854x480 — too small to label.
Fixing that means upscaling somewhere; this measures where it should live.

Measured pieces, for a real 480p frame upscaled to 720p/1080p/1440p/4K:
  1. upscale cost per filter (NEAREST / BILINEAR / BICUBIC / LANCZOS)
     + quality drift vs a LANCZOS reference;
  2. ImageTk.PhotoImage creation cost at each size — this runs on the Tk
     UI thread on EVERY repaint (labeling_app.display_first_frame);
  3. RAM per stored frame at each size and how many frames fit the 1 GB
     buffer budget;
  4. end-to-end strategy comparison per displayed frame:
       A: worker decodes + upscales, buffer stores BIG
          -> worker ms/frame, UI repaint = PhotoImage(big)
       B: buffer stores NATIVE, UI upscales at paint time
          -> worker ms/frame = decode only,
             UI repaint = resize(native->big) + PhotoImage(big)
     against the 40 ms/frame budget of 25 fps playback.

Run:  uv run python tests/perf/benchmark_upscale_display.py
Requires videos/sample_480p.mp4 (see benchmark_real_videos.py) and a display
(creates a hidden Tk root for the PhotoImage timings).
"""

import os
import statistics
import subprocess
import sys
import tempfile
import time
import tkinter as tk

from PIL import Image, ImageTk

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _HERE)

import imageio_ffmpeg  # noqa: E402

from benchmark_decode_pipeline import rms_diff  # noqa: E402

# --- Tunables -----------------------------------------------------------------
SOURCE_VIDEO = os.path.join(_ROOT, "videos", "sample_480p.mp4")
DISPLAY_TARGETS = [   # (label, width, height) — canvas sizes to upscale into
    ("720p", 1280, 720),
    ("1080p", 1920, 1080),
    ("1440p", 2560, 1440),
    ("4K", 3840, 2160),
]
FILTERS = [
    ("NEAREST", Image.Resampling.NEAREST),
    ("BILINEAR", Image.Resampling.BILINEAR),
    ("BICUBIC", Image.Resampling.BICUBIC),
    ("LANCZOS", Image.Resampling.LANCZOS),
]
REPEATS = 20
BUFFER_BUDGET = 1_000_000_000     # BUFFER_MAX_BYTES
PLAYBACK_BUDGET_MS = 40.0         # 25 fps frame budget


def fit_size(src_w, src_h, dst_w, dst_h):
    """Aspect-preserving size filling the target box (allows upscaling)."""
    scale = min(dst_w / src_w, dst_h / src_h)
    return max(1, int(src_w * scale)), max(1, int(src_h * scale))


def median_ms(fn, repeats=REPEATS):
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1e3)
    return statistics.median(samples)


def main():
    if not os.path.exists(SOURCE_VIDEO):
        sys.exit(f"missing {SOURCE_VIDEO} — generate the sample videos first")

    # One real extracted 480p frame, app extraction flags.
    with tempfile.TemporaryDirectory(prefix="tinytouch_upscale_bench_") as tmp:
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error",
             "-ss", "3", "-i", SOURCE_VIDEO, "-q:v", "2", "-frames:v", "1",
             os.path.join(tmp, "frame0.jpg")],
            check=True, capture_output=True)
        frame_path = os.path.join(tmp, "frame0.jpg")
        with Image.open(frame_path) as f:
            native = f.convert("RGB")

        decode_ms = median_ms(lambda: Image.open(frame_path).copy())
        print(f"Source: real {native.width}x{native.height} frame from "
              f"sample_480p.mp4 | decode alone {decode_ms:.1f} ms | "
              f"median of {REPEATS}\n")

        root = tk.Tk()
        root.withdraw()   # hidden root so ImageTk works headlessly-ish
        try:
            print("1) Upscale cost, ms per frame (quality = RMS diff vs "
                  "LANCZOS output, 0-255)")
            print(f"{'target':8}" + "".join(f"{n:>22}" for n, _ in FILTERS))
            upscaled = {}   # target label -> LANCZOS-upscaled image
            for label, dw, dh in DISPLAY_TARGETS:
                tw, th = fit_size(native.width, native.height, dw, dh)
                cells = []
                ref = native.resize((tw, th), Image.Resampling.LANCZOS)
                upscaled[label] = ref
                for _, filt in FILTERS:
                    ms = median_ms(lambda f=filt: native.resize((tw, th), f))
                    q = rms_diff(ref, native.resize((tw, th), filt))
                    cells.append(f"{ms:>13.1f}ms {q:>5.1f}q")
                print(f"{label:8}" + "".join(f"{c:>22}" for c in cells))

            print("\n2) ImageTk.PhotoImage cost on the UI thread (per repaint)"
                  " and RAM per stored frame")
            print(f"{'stored size':16} {'PhotoImage':>12} {'RAM/frame':>11} "
                  f"{'frames in 1GB':>14}")
            sizes = [("native 480p", native)] + \
                    [(lb, im) for lb, im in upscaled.items()]
            photo_ms = {}
            for label, img in sizes:
                ms = median_ms(lambda i=img: ImageTk.PhotoImage(i))
                photo_ms[label] = ms
                ram = img.width * img.height * 3
                print(f"{label:16} {ms:>10.1f}ms {ram / 1e6:>9.1f}MB "
                      f"{BUFFER_BUDGET // ram:>14,}")

            print("\n3) Strategy totals per displayed frame "
                  f"(playback budget {PLAYBACK_BUDGET_MS:.0f} ms at 25 fps)")
            print(f"{'target':8} {'A worker ms':>12} {'A repaint ms':>13} "
                  f"{'B repaint ms (resize+Photo)':>28}")
            for label, dw, dh in DISPLAY_TARGETS:
                tw, th = fit_size(native.width, native.height, dw, dh)
                a_worker = decode_ms + median_ms(
                    lambda: native.resize((tw, th), Image.Resampling.BILINEAR))
                a_paint = photo_ms[label]
                b_paint = median_ms(
                    lambda: ImageTk.PhotoImage(
                        native.resize((tw, th), Image.Resampling.BILINEAR)))
                verdict_a = "OK" if a_paint < PLAYBACK_BUDGET_MS else "OVER"
                verdict_b = "OK" if b_paint < PLAYBACK_BUDGET_MS else "OVER"
                print(f"{label:8} {a_worker:>10.1f}ms {a_paint:>9.1f}ms "
                      f"{verdict_a:>4} {b_paint:>21.1f}ms {verdict_b:>5}")
            print("\n  A = upscale in decode workers (store big), "
                  "B = store native, upscale at paint time.")
            print("  'repaint ms' runs on the Tk thread per frame shown; "
                  "worker ms is parallel x3.")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()
