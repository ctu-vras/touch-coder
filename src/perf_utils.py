import time
import threading
from dataclasses import dataclass


@dataclass
class PerfStats:
    count: int = 0
    total: float = 0.0
    max: float = 0.0
    min: float = 0.0
    last: float = 0.0

    def update(self, dt: float) -> None:
        if self.count == 0:
            self.min = dt
            self.max = dt
        else:
            if dt < self.min:
                self.min = dt
            if dt > self.max:
                self.max = dt
        self.count += 1
        self.total += dt
        self.last = dt


class _PerfTimer:
    __slots__ = ("_logger", "_name", "_start")

    def __init__(self, logger, name: str):
        self._logger = logger
        self._name = name
        self._start = time.perf_counter()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.perf_counter() - self._start
        self._logger.record(self._name, dt)
        return False


class _NullTimer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


_NULL_TIMER = _NullTimer()


class PerfLogger:
    def __init__(self, enabled: bool = False, log_every_s: float = 2.0, top_n: int = 6):
        self.enabled = enabled
        self.log_every_s = log_every_s
        self.top_n = top_n
        self._lock = threading.Lock()
        self._stats = {}
        self._last_log_ts = time.perf_counter()

    def time(self, name: str):
        if not self.enabled:
            return _NULL_TIMER
        return _PerfTimer(self, name)

    def record(self, name: str, dt: float) -> None:
        if not self.enabled:
            return
        with self._lock:
            stats = self._stats.get(name)
            if stats is None:
                stats = PerfStats()
                self._stats[name] = stats
            stats.update(dt)
            now = time.perf_counter()
            if now - self._last_log_ts >= self.log_every_s:
                self._last_log_ts = now
                self._print_summary_locked()

    def _print_summary_locked(self) -> None:
        if not self._stats:
            return
        items = sorted(self._stats.items(), key=lambda kv: kv[1].total, reverse=True)
        parts = []
        for name, stats in items[: self.top_n]:
            avg = stats.total / stats.count if stats.count else 0.0
            parts.append(
                f"{name}: avg={avg * 1000:.2f}ms max={stats.max * 1000:.2f}ms n={stats.count}"
            )
        print("PERF:", " | ".join(parts))
