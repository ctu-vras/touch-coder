"""
tests/e2e/test_perf_scenario.py

OBJECTIVE, repeatable performance scenario against the REAL app — the
"robot user" that replaces a human driving the .exe, so before/after
comparisons of buffer/decode changes are apples to apples.

Runs the real LabelingApp (hidden, off-screen, real geometry) through a
FIXED, seeded workload via the same widget callbacks a user hits:

  1. load     — Load Video through the button: copy + frame extraction +
                first paint, wall time
  2. steps    — 100 single `>` steps, per-step paint latency
  3. jumps    — 30 seeded `<<` / `>>` jumps, latency until the target frame
                is painted (median / p95)
  4. playback — 10 s of Play, achieved fps vs the video's frame rate and
                the fraction of time the buffer was stalled
  5. perf     — the app's own PerfLogger counters (load_frame_total,
                display_frame_photo, ...) recorded during the run

Results are printed AND appended as JSON under tests/perf/results/ with
the git revision + a label, so runs can be diffed later.

USAGE (deliberately opt-in — this takes 1-2 minutes):
  $env:TINYTOUCH_PERF = "1"
  $env:TINYTOUCH_PERF_LABEL = "baseline"          # optional, default "run"
  $env:TINYTOUCH_PERF_VIDEO = "videos\\sample_real4k_bbb.mp4"  # optional
  uv run pytest tests/e2e/test_perf_scenario.py -m gui -s

Default video: videos/sample_1080p.mp4 (see tests/perf/benchmark_real_videos.py
for how the samples were generated).
"""

import json
import os
import random
import statistics
import subprocess
import time

import pytest

from gui_driver import click, load_video, pump, wait_until

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RESULTS_DIR = os.path.join(_REPO_ROOT, "tests", "perf", "results")

STEP_COUNT = 100
JUMP_COUNT = 30
JUMP_SECONDS = 5.0          # -> 125 frames at 25 fps; big enough to leave cache
PLAYBACK_SECONDS = 10.0
RNG_SEED = 7

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(
        not os.environ.get("TINYTOUCH_PERF"),
        reason="perf scenario is opt-in: set TINYTOUCH_PERF=1",
    ),
]


def _git_rev():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _perf_snapshot(app):
    """The app's own PerfLogger counters as plain dicts (ms)."""
    out = {}
    with app.perf._lock:
        for name, s in app.perf._stats.items():
            out[name] = {
                "avg_ms": round(s.total / s.count * 1e3, 2) if s.count else 0,
                "max_ms": round(s.max * 1e3, 2),
                "n": s.count,
            }
    return out


def _painted(app, target):
    """True once `target` is the current frame AND its image is buffered
    (display_first_frame paints from the buffer, so this is paint-ready)."""
    return (app.video.current_frame == target
            and app.frame_buffer.get(target) is not None)


def test_perf_scenario(app_factory, workspace):
    # The scenario needs the app's own perf counters and a real jump size;
    # rewrite the sandbox config BEFORE the app is constructed.
    config_path = os.path.join(str(workspace.root), "config.json")
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.update({"perf_enabled": True, "jump_seconds": JUMP_SECONDS})
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)

    video = os.environ.get(
        "TINYTOUCH_PERF_VIDEO", os.path.join("videos", "sample_1080p.mp4"))
    video = os.path.join(_REPO_ROOT, video) if not os.path.isabs(video) else video
    assert os.path.exists(video), (
        f"{video} not found — generate the sample videos first "
        "(see tests/perf/benchmark_real_videos.py)")

    app = app_factory()
    results = {
        "label": os.environ.get("TINYTOUCH_PERF_LABEL", "run"),
        "git_rev": _git_rev(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "video": os.path.basename(video),
        "display_panel": None,   # filled after load
    }

    # --- 1) load: copy + extraction + first frame painted ---------------------
    t0 = time.perf_counter()
    load_video(app, workspace, video)
    results["load_s"] = round(time.perf_counter() - t0, 2)
    results["total_frames"] = app.video.total_frames
    results["frame_rate"] = app.video.frame_rate
    results["display_panel"] = (
        f"{app.video_frame.winfo_width()}x{app.video_frame.winfo_height()}")
    wait_until(app, lambda: app.frame_buffer.buffer_ready,
               what="initial window buffered")

    # --- 2) 100 single steps ---------------------------------------------------
    latencies = []
    for _ in range(STEP_COUNT):
        target = app.video.current_frame + 1
        t0 = time.perf_counter()
        click(app, ">")
        wait_until(app, lambda: _painted(app, target),
                   timeout=10, what=f"step to frame {target}")
        latencies.append((time.perf_counter() - t0) * 1e3)
    results["step_ms_median"] = round(statistics.median(latencies), 1)
    results["step_ms_p95"] = round(sorted(latencies)[int(STEP_COUNT * 0.95)], 1)

    # --- 3) 30 seeded jumps ----------------------------------------------------
    rng = random.Random(RNG_SEED)
    jump = app.jump_frame_count
    total = app.video.total_frames
    latencies = []
    for _ in range(JUMP_COUNT):
        pos = app.video.current_frame
        if pos < 2 * jump:
            direction = 1
        elif pos > total - 2 * jump:
            direction = -1
        else:
            direction = rng.choice((-1, 1))
        target = max(0, min(total, pos + direction * jump))
        t0 = time.perf_counter()
        click(app, ">>" if direction > 0 else "<<")
        wait_until(app, lambda: _painted(app, target),
                   timeout=15, what=f"jump to frame {target}")
        latencies.append((time.perf_counter() - t0) * 1e3)
    results["jump_frames"] = jump
    results["jump_ms_median"] = round(statistics.median(latencies), 1)
    results["jump_ms_p95"] = round(sorted(latencies)[int(JUMP_COUNT * 0.95)], 1)

    # --- 4) 10 s of playback ---------------------------------------------------
    # Rewind first so playback has room. This phase runs under a REAL
    # mainloop(): the playback thread marshals every advance through
    # Tk.after(), which only works while the main thread sits inside the
    # event loop — pump() gaps would drop the advances and measure zero
    # (production always runs inside mainloop, so this is also more faithful).
    while app.video.current_frame > total // 4:
        click(app, "<<")
        pump(app, 0.02)
    start_frame = app.video.current_frame
    stall = {"stalled": 0, "samples": 0}

    def sample_stall():
        stall["samples"] += 1
        if not app.frame_buffer.buffer_ready:
            stall["stalled"] += 1
        if stall["samples"] * 50 < PLAYBACK_SECONDS * 1000:
            app.after(50, sample_stall)

    app.after(50, sample_stall)
    app.after(int(PLAYBACK_SECONDS * 1000), app.quit)
    click(app, "Play")
    t0 = time.perf_counter()
    app.mainloop()
    elapsed = time.perf_counter() - t0
    click(app, "Stop")
    pump(app, 0.1)
    advanced = app.video.current_frame - start_frame
    results["playback_fps"] = round(advanced / elapsed, 1)
    results["playback_target_fps"] = app.video.frame_rate
    results["playback_stall_pct"] = round(
        100 * stall["stalled"] / max(1, stall["samples"]), 1)

    # --- 5) the app's own perf counters ---------------------------------------
    results["perf_counters"] = _perf_snapshot(app)

    # --- report ----------------------------------------------------------------
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_name = (f"perf_{results['label']}_{results['git_rev']}_"
                f"{time.strftime('%Y%m%d-%H%M%S')}.json")
    out_path = os.path.join(RESULTS_DIR, out_name)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    print("\n=== PERF SCENARIO RESULT ===")
    for key in ("label", "git_rev", "video", "display_panel", "total_frames",
                "load_s", "step_ms_median", "step_ms_p95", "jump_frames",
                "jump_ms_median", "jump_ms_p95", "playback_fps",
                "playback_target_fps", "playback_stall_pct"):
        print(f"  {key:22} {results[key]}")
    top = sorted(results["perf_counters"].items(),
                 key=lambda kv: kv[1]["avg_ms"] * kv[1]["n"], reverse=True)[:6]
    for name, s in top:
        print(f"  perf:{name:22} avg {s['avg_ms']:>8.2f} ms  "
              f"max {s['max_ms']:>8.2f} ms  n={s['n']}")
    print(f"  saved -> {os.path.relpath(out_path, _REPO_ROOT)}")
