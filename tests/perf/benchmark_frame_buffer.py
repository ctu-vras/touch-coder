"""
tests/perf/benchmark_frame_buffer.py

Standalone benchmark comparing the CURRENT FrameBuffer container strategy
(dict keyed by frame index + one RLock + sorted()-based byte eviction, see
src/adapters/frame_buffer.py) against alternative stdlib structures the
Fluent Python "queues" chapter suggests (queue.Queue, collections.deque,
OrderedDict-as-LRU, plain dict with a non-reentrant Lock, no lock at all).

It simulates the real workload of FrameBuffer.background_update:
  - a 10 ms tick that scans an asymmetric prefetch window (50 ahead / 30
    behind), stores frames entering the window, trims frames leaving the
    keep-range, and evicts by distance-from-current until under budget;
  - two access patterns: sequential playback (+1 per tick) and random
    scrub jumps (~±120 frames);
  - a threaded phase: 3 decode-pool writers + 1 UI-thread reader hammering
    the same container, like the real app;
  - a reality-check phase: the actual per-frame JPEG decode + LANCZOS
    resize cost, to show what fraction of frame time the container can
    possibly account for.

Run:  uv run python tests/perf/benchmark_frame_buffer.py
Not collected by pytest (no test_ prefix); lives here as a perf harness.
"""

import io
import os
import queue
import random
import statistics
import sys
import tempfile
import threading
import time
from collections import OrderedDict, deque

from PIL import Image

# --- Tunables (globals per project convention) -------------------------------
SEQ_TICKS = 2_000            # sequential-playback ticks per implementation
JUMP_TICKS = 500             # random-jump ticks per implementation
TOTAL_FRAMES = 20_000        # simulated video length
AHEAD, BEHIND = 50, 30       # prefetch window (base values from frame_buffer)
KEEP_RANGE = 200             # trim keep-range each side (base value)
EST_BYTES = 2_764_800        # ~1280x720x3, per-frame estimate like the app's
MAX_BYTES = 1_000_000_000    # BUFFER_MAX_BYTES from frame_buffer (≈360 frames)
THREADED_SECONDS = 1.5       # duration of each threaded contention run
DECODE_REPEATS = 30          # JPEG decode+resize samples for the reality check
JPEG_SIZE = (1920, 1080)     # simulated source frame size
DISPLAY = (1280, 720)        # simulated display target
RNG_SEED = 42

PAYLOAD = object()           # stored value; containers hold references only


# --- Buffer implementations ---------------------------------------------------
# All expose: store(i), get(i), contains(i), trim(lo, hi), evict(current).
# store/trim/evict take the writer lock (as in the real code); get/contains
# are the UI-thread read path (unlocked in the current implementation).

class CurrentDictRLock:
    """Faithful mirror of FrameBuffer: dict + bytes dict + RLock,
    unlocked reads, sorted()-by-distance byte eviction."""
    name = "dict + RLock (current)"
    lock_factory = threading.RLock

    def __init__(self):
        self._lock = self.lock_factory()
        self._buffer = {}
        self._bytes = {}
        self._total = 0

    def store(self, i):
        with self._lock:
            if i in self._bytes:
                self._total -= self._bytes[i]
            self._buffer[i] = PAYLOAD
            self._bytes[i] = EST_BYTES
            self._total += EST_BYTES

    def get(self, i):
        return self._buffer.get(i)

    def contains(self, i):
        return i in self._buffer

    def _remove(self, i):
        self._buffer.pop(i, None)
        self._total -= self._bytes.pop(i, 0)

    def trim(self, lo, hi):
        with self._lock:
            for k in [k for k in self._buffer if k < lo or k > hi]:
                self._remove(k)

    def evict(self, current):
        with self._lock:
            if self._total <= MAX_BYTES:
                return
            for k in sorted(self._buffer, key=lambda k: abs(k - current), reverse=True):
                if k == current:
                    continue
                self._remove(k)
                if self._total <= MAX_BYTES:
                    break


class DictLock(CurrentDictRLock):
    """Same structure, plain (non-reentrant) Lock instead of RLock."""
    name = "dict + Lock"
    lock_factory = threading.Lock


class DictNoLock(CurrentDictRLock):
    """Same structure, no lock at all — upper bound / GIL-atomicity baseline."""
    name = "dict, no lock"

    class _Null:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    lock_factory = _Null


class LruOrderedDict:
    """OrderedDict as an LRU cache. NOTE: eviction semantics differ — evicts
    least-recently-USED, not farthest-from-current, so a backward jump after
    forward playback lands on evicted frames the current design would keep."""
    name = "OrderedDict LRU"

    def __init__(self):
        self._lock = threading.Lock()
        self._od = OrderedDict()
        self._total = 0

    def store(self, i):
        with self._lock:
            if i in self._od:
                self._od.move_to_end(i)
            else:
                self._od[i] = PAYLOAD
                self._total += EST_BYTES

    def get(self, i):
        with self._lock:  # get MUST lock: move_to_end mutates order
            if i in self._od:
                self._od.move_to_end(i)
                return self._od[i]
            return None

    def contains(self, i):
        return i in self._od

    def trim(self, lo, hi):
        with self._lock:
            for k in [k for k in self._od if k < lo or k > hi]:
                del self._od[k]
                self._total -= EST_BYTES

    def evict(self, current):
        with self._lock:
            while self._total > MAX_BYTES and len(self._od) > 1:
                k, _ = self._od.popitem(last=False)
                if k == current:  # never evict the visible frame
                    self._od[k] = PAYLOAD
                    self._od.move_to_end(k, last=False)
                    continue
                self._total -= EST_BYTES


class DequeBuffer:
    """collections.deque of (index, image) — the literal 'use a deque' answer.
    O(1) at the ends, but membership/get are O(n) scans and trim rebuilds."""
    name = "deque of (idx, img)"

    def __init__(self):
        self._lock = threading.Lock()
        self._dq = deque()
        self._total = 0

    def store(self, i):
        with self._lock:
            self._dq.append((i, PAYLOAD))
            self._total += EST_BYTES

    def get(self, i):
        for k, v in self._dq:
            if k == i:
                return v
        return None

    def contains(self, i):
        return any(k == i for k, _ in self._dq)

    def trim(self, lo, hi):
        with self._lock:
            kept = deque((k, v) for k, v in self._dq if lo <= k <= hi)
            self._total -= (len(self._dq) - len(kept)) * EST_BYTES
            self._dq = kept

    def evict(self, current):
        with self._lock:
            while self._total > MAX_BYTES and self._dq:
                k, _ = self._dq.popleft()  # FIFO eviction (oldest stored)
                if k == current:
                    self._dq.append((k, PAYLOAD))
                    continue
                self._total -= EST_BYTES


class QueueFifoCache:
    """queue.Queue for FIFO eviction order + an aux dict for random access.
    Shows that a Queue cannot REPLACE the dict — random access still needs
    one — it can only add an eviction-order side structure with its own lock."""
    name = "queue.Queue + aux dict"

    def __init__(self):
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self._map = {}
        self._total = 0

    def store(self, i):
        with self._lock:
            if i not in self._map:
                self._q.put(i)
                self._map[i] = PAYLOAD
                self._total += EST_BYTES

    def get(self, i):
        return self._map.get(i)

    def contains(self, i):
        return i in self._map

    def trim(self, lo, hi):
        with self._lock:
            doomed = [k for k in self._map if k < lo or k > hi]
            for k in doomed:
                del self._map[k]
                self._total -= EST_BYTES
            # queue entries for trimmed frames become stale; skipped in evict()

    def evict(self, current):
        with self._lock:
            while self._total > MAX_BYTES:
                try:
                    k = self._q.get_nowait()
                except queue.Empty:
                    break
                if k not in self._map:      # stale (already trimmed)
                    continue
                if k == current:
                    self._q.put(k)
                    continue
                del self._map[k]
                self._total -= EST_BYTES


IMPLEMENTATIONS = [CurrentDictRLock, DictLock, DictNoLock,
                   LruOrderedDict, DequeBuffer, QueueFifoCache]


# --- Workload: one background_update tick ------------------------------------
def tick(buf, current):
    """Replicates the per-tick work of FrameBuffer.background_update."""
    if not buf.contains(current):          # priority load of visible frame
        buf.store(current)
    end = min(TOTAL_FRAMES, current + AHEAD)
    start = max(0, current - BEHIND)
    for i in range(current + 1, end + 1):          # forward window
        if not buf.contains(i):
            buf.store(i)
    for i in range(current - 1, start - 1, -1):    # backward window
        if not buf.contains(i):
            buf.store(i)
    buf.trim(max(0, current - KEEP_RANGE), min(TOTAL_FRAMES, current + KEEP_RANGE))
    buf.evict(current)
    buf.get(current)                        # UI repaint read


def run_pattern(impl_cls, positions):
    buf = impl_cls()
    t0 = time.perf_counter()
    for pos in positions:
        tick(buf, pos)
    return (time.perf_counter() - t0) / len(positions) * 1e6  # µs per tick


def sequential_positions():
    start = 1000
    return [start + i for i in range(SEQ_TICKS)]


def jump_positions():
    rng = random.Random(RNG_SEED)
    pos, out = 5000, []
    for _ in range(JUMP_TICKS):
        pos = min(TOTAL_FRAMES - 300, max(300, pos + rng.choice((-1, 1)) * rng.randint(60, 180)))
        out.append(pos)
    return out


# --- Threaded contention phase ------------------------------------------------
def threaded_run(impl_cls):
    """Faithful thread topology of the real app on one shared buffer:
      - 1 maintenance thread = the buffering loop (window scan, trim, evict,
        advancing current_frame) on a fast tick;
      - 3 writer threads = the decode pool, storing frames inside the window;
      - 1 reader thread = the Tk UI thread (contains + get on current frame),
        with latency sampled every 100th read — p99 is what scrubbing feels.
    Returns dict(writes_s, reads_s, p50_us, p99_us, errors)."""
    buf = impl_cls()
    stop = threading.Event()
    current = [5_000]
    write_counts = [0, 0, 0]
    read_count = [0]
    read_lat_ns = []
    errors = []

    def guarded(name, fn):
        def run():
            try:
                fn()
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {e}")
        return run

    def maintenance():
        while not stop.is_set():
            c = current[0]
            for i in range(c - BEHIND, c + AHEAD + 1):
                buf.contains(i)
            buf.trim(max(0, c - KEEP_RANGE), min(TOTAL_FRAMES, c + KEEP_RANGE))
            buf.evict(c)
            current[0] = min(TOTAL_FRAMES - KEEP_RANGE, c + 1)
            time.sleep(0.001)   # real loop ticks every 10 ms; 1 ms stresses more

    def writer(wid):
        rng = random.Random(RNG_SEED + wid)
        while not stop.is_set():
            c = current[0]
            buf.store(rng.randint(max(0, c - BEHIND), c + AHEAD))
            write_counts[wid] += 1

    def reader():
        n = 0
        while not stop.is_set():
            c = current[0]
            if n % 100 == 0:
                t0 = time.perf_counter_ns()
                buf.contains(c)
                buf.get(c)
                read_lat_ns.append(time.perf_counter_ns() - t0)
            else:
                buf.contains(c)
                buf.get(c)
            n += 1
            read_count[0] = n

    threads = [threading.Thread(target=guarded("maintenance", maintenance))]
    threads += [threading.Thread(target=guarded(f"writer{w}", lambda w=w: writer(w)))
                for w in range(3)]
    threads.append(threading.Thread(target=guarded("reader (UI)", reader)))
    for t in threads:
        t.start()
    time.sleep(THREADED_SECONDS)
    stop.set()
    for t in threads:
        t.join()

    lat_sorted = sorted(read_lat_ns) or [0]
    return {
        "writes_s": sum(write_counts) / THREADED_SECONDS,
        "reads_s": read_count[0] / THREADED_SECONDS,
        "p50_us": lat_sorted[len(lat_sorted) // 2] / 1e3,
        "p99_us": lat_sorted[int(len(lat_sorted) * 0.99)] / 1e3,
        "errors": errors,
    }


# --- Reality check: actual decode + resize cost -------------------------------
def decode_reality_check():
    """Generate one 1080p JPEG, then time the real _load_frame_to_buffer work:
    open -> copy (full decode) -> LANCZOS resize. Returns list of ms samples."""
    img = Image.new("RGB", JPEG_SIZE)
    px = img.load()
    for y in range(0, JPEG_SIZE[1], 4):          # cheap gradient, non-trivial JPEG
        for x in range(0, JPEG_SIZE[0], 4):
            px[x, y] = (x % 256, y % 256, (x * y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    fd, path = tempfile.mkstemp(suffix=".jpg")
    with os.fdopen(fd, "wb") as f:
        f.write(buf.getvalue())

    samples = []
    try:
        for _ in range(DECODE_REPEATS):
            t0 = time.perf_counter()
            with Image.open(path) as opened:
                decoded = opened.copy()
            ratio = decoded.width / decoded.height
            h = DISPLAY[1]
            decoded.resize((int(h * ratio), h), Image.Resampling.LANCZOS)
            samples.append((time.perf_counter() - t0) * 1e3)
    finally:
        os.remove(path)
    return samples


# --- Report -------------------------------------------------------------------
def main():
    print(f"Python {sys.version.split()[0]} | "
          f"{SEQ_TICKS} sequential + {JUMP_TICKS} jump ticks | "
          f"window +{AHEAD}/-{BEHIND}, keep ±{KEEP_RANGE}, "
          f"budget {MAX_BYTES // EST_BYTES} frames\n")

    print("Phase 1 — single-thread tick cost (µs per background_update tick)")
    print(f"{'implementation':28} {'sequential':>12} {'jumps':>12}")
    seq_pos, jmp_pos = sequential_positions(), jump_positions()
    for cls in IMPLEMENTATIONS:
        seq = run_pattern(cls, seq_pos)
        jmp = run_pattern(cls, jmp_pos)
        print(f"{cls.name:28} {seq:>10.1f}us {jmp:>10.1f}us")

    print("\nPhase 2 — threaded: 1 buffering loop + 3 decode writers + 1 UI reader")
    print(f"{'implementation':28} {'writes/s':>12} {'reads/s':>12} "
          f"{'read p50':>10} {'read p99':>10}")
    for cls in IMPLEMENTATIONS:
        r = threaded_run(cls)
        print(f"{cls.name:28} {r['writes_s']:>12,.0f} {r['reads_s']:>12,.0f} "
              f"{r['p50_us']:>8.1f}us {r['p99_us']:>8.1f}us")
        for err in r["errors"]:
            print(f"{'':28}   CRASHED thread -> {err}")

    print("\nPhase 3 — reality check: real per-frame decode work "
          f"(1080p JPEG open+decode+LANCZOS to {DISPLAY[1]}p)")
    samples = decode_reality_check()
    print(f"  median {statistics.median(samples):.2f} ms   "
          f"min {min(samples):.2f} ms   max {max(samples):.2f} ms   "
          f"(n={len(samples)})")
    print("  -> compare: one decode ≈ thousands of container ticks. The container")
    print("     is not the bottleneck unless a tick costs >1000 µs above.")


if __name__ == "__main__":
    main()
