"""
tests/perf/benchmark_decode_pipeline.py

Experiment: how much faster can the per-frame load path
(FrameBuffer._load_frame_to_buffer: JPEG open -> full decode -> LANCZOS
resize) get, across source resolutions from 480p to 4K?

Pipelines compared (all produce the SAME final image dimensions):
  current          Image.open -> copy (full decode) -> LANCZOS resize
                   (the OLD pre-optimization pipeline, inlined here — the
                   production `resize_for_buffer` is now fit-box + BILINEAR,
                   and draft happens in `_load_frame_to_buffer`)
  draft+LANCZOS    Image.open -> draft() (libjpeg decodes at 1/2..1/8 DCT
                   scale, nearly free) -> copy -> LANCZOS resize
  draft+BILINEAR   same, cheaper final filter (what production now does)
  full+BILINEAR    full decode + the production resize_for_buffer
                   (isolates the filter/fit effect without draft)

Also reports, per source resolution: JPEG file size on disk and per-frame
quality drift of each variant vs the current pipeline's output (RMS pixel
difference, 0-255 scale) — to prove speed is not bought with visible quality.

The resolution rows double as the "extract 4K videos at HD" experiment:
a 4K video extracted at 1080p/720p pays the decode cost of THAT row forever.

Source content: a real extracted frame (data/cat3/frames, read-only) when
available, else a synthetic noisy gradient. Variants are written to the
system temp dir and deleted afterwards.

Run:  uv run python tests/perf/benchmark_decode_pipeline.py
"""

import math
import os
import statistics
import sys
import tempfile
import time

from PIL import Image, ImageChops

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from adapters.frame_buffer import compute_target_size, resize_for_buffer  # noqa: E402

# --- Tunables -----------------------------------------------------------------
REAL_FRAME = os.path.join(os.path.dirname(__file__), "..", "..",
                          "data", "cat3", "frames", "frame100.jpg")
SOURCE_RESOLUTIONS = [           # (label, width, height)
    ("4K   3840x2160", 3840, 2160),
    ("1440p 2560x1440", 2560, 1440),
    ("1080p 1920x1080", 1920, 1080),
    ("720p  1280x720", 1280, 720),
    ("480p   854x480", 854, 480),
]
DISPLAY_W, DISPLAY_H = 1280, 720   # typical canvas area in the app
DOWNSCALE = 1.0                    # config video_downscale
JPEG_QUALITY = 90                  # ffmpeg -q:v 2 is roughly this
REPEATS = 15


def final_size(orig_w, orig_h, display_w, display_h, downscale):
    """The production target-size rule (fit-box, upscale allowed), computed from
    the ORIGINAL dimensions so all pipelines land on identical output dims."""
    return compute_target_size(orig_w, orig_h, display_w, display_h, downscale)


# --- Pipelines (each: path -> final PIL image) ---------------------------------
def pipe_current(path):
    """The OLD pipeline: full decode + LANCZOS (at the shared target dims)."""
    with Image.open(path) as opened:
        img = opened.copy()
    tw, th = final_size(*img.size, DISPLAY_W, DISPLAY_H, DOWNSCALE)
    if img.size == (tw, th):
        return img
    return img.resize((tw, th), Image.Resampling.LANCZOS)


def _pipe_draft(path, filt):
    with Image.open(path) as opened:
        tw, th = final_size(*opened.size, DISPLAY_W, DISPLAY_H, DOWNSCALE)
        opened.draft("RGB", (tw, th))   # decode at reduced DCT scale (>= target)
        img = opened.copy()
    if img.size == (tw, th):
        return img
    return img.resize((tw, th), filt)


def pipe_draft_lanczos(path):
    return _pipe_draft(path, Image.Resampling.LANCZOS)


def pipe_draft_bilinear(path):
    return _pipe_draft(path, Image.Resampling.BILINEAR)


def pipe_full_bilinear(path):
    """Full decode + the production resize_for_buffer (fit-box BILINEAR)."""
    with Image.open(path) as opened:
        img = opened.copy()
    return resize_for_buffer(img, DISPLAY_W, DISPLAY_H, DOWNSCALE)


PIPELINES = [
    ("current (full+LANCZOS)", pipe_current),
    ("draft + LANCZOS", pipe_draft_lanczos),
    ("draft + BILINEAR", pipe_draft_bilinear),
    ("full  + BILINEAR", pipe_full_bilinear),
]


# --- Helpers --------------------------------------------------------------------
def load_base_image():
    if os.path.exists(REAL_FRAME):
        with Image.open(REAL_FRAME) as f:
            return f.convert("RGB"), f"real frame {os.path.basename(REAL_FRAME)}"
    img = Image.new("RGB", (1920, 1080))
    px = img.load()
    for y in range(1080):
        for x in range(0, 1920, 2):
            v = (x * 7 + y * 13 + (x * y) % 97) % 256
            px[x, y] = (v, (v * 3) % 256, (255 - v))
            px[x + 1, y] = px[x, y]
    return img, "synthetic gradient (no real frame found)"


def rms_diff(a, b):
    """RMS pixel difference on 0-255 scale between two same-size RGB images."""
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    hist = diff.histogram()
    total_sq, n = 0, 0
    for band in range(3):
        for value, count in enumerate(hist[band * 256:(band + 1) * 256]):
            total_sq += count * value * value
            n += count
    return math.sqrt(total_sq / n) if n else 0.0


def bench(fn, path):
    samples = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        fn(path)
        samples.append((time.perf_counter() - t0) * 1e3)
    return statistics.median(samples)


def main():
    base, base_desc = load_base_image()
    print(f"Source content: {base_desc} | display {DISPLAY_W}x{DISPLAY_H}, "
          f"downscale={DOWNSCALE}, JPEG q={JPEG_QUALITY}, median of {REPEATS}\n")

    tmp = tempfile.mkdtemp(prefix="tinytouch_decode_bench_")
    paths = {}
    try:
        for label, w, h in SOURCE_RESOLUTIONS:
            variant = base.resize((w, h), Image.Resampling.BICUBIC)
            p = os.path.join(tmp, f"src_{w}x{h}.jpg")
            variant.save(p, format="JPEG", quality=JPEG_QUALITY)
            paths[label] = p

        header = f"{'source':18} {'disk':>8} " + "".join(
            f"{name:>24}" for name, _ in PIPELINES) + f"{'best speedup':>14}"
        print(header)
        quality_rows = []
        for label, w, h in SOURCE_RESOLUTIONS:
            p = paths[label]
            disk_kb = os.path.getsize(p) / 1024
            times = [bench(fn, p) for _, fn in PIPELINES]
            speedup = times[0] / min(times) if min(times) > 0 else float("inf")
            row = f"{label:18} {disk_kb:>6.0f}kB " + "".join(
                f"{t:>21.1f} ms" for t in times) + f"{speedup:>13.1f}x"
            print(row)

            reference = pipe_current(p)
            drift = []
            for name, fn in PIPELINES[1:]:
                out = fn(p)
                assert out.size == reference.size, \
                    f"{name} produced {out.size}, current {reference.size}"
                drift.append((name, rms_diff(reference, out)))
            quality_rows.append((label, reference.size, drift))

        print("\nQuality drift vs current output (RMS pixel diff, 0-255; "
              "JPEG re-save alone is ~2-4):")
        for label, size, drift in quality_rows:
            drift_s = "   ".join(f"{n}: {d:5.2f}" for n, d in drift)
            print(f"  {label:18} -> {size[0]}x{size[1]}   {drift_s}")

        print("\nPlayback capability (per decode worker; the app pool is "
              "adaptive, 3-8 workers):")
        for label, _, _ in SOURCE_RESOLUTIONS:
            p = paths[label]
            cur = bench(pipe_current, p)
            fast = bench(pipe_draft_bilinear, p)
            print(f"  {label:18} current {1000 / cur:>6.1f} fps/worker -> "
                  f"draft+BILINEAR {1000 / fast:>6.1f} fps/worker")
    finally:
        for p in paths.values():
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    main()
