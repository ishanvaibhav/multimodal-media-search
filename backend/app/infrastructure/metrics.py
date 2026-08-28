"""Thread-safe, in-process metrics registry.

Lightweight counters, gauges and latency histograms exposed via
``GET /api/metrics``. No external dependencies.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_counters: dict[str, int] = defaultdict(int)
_gauges: dict[str, float] = {}
_latency: dict[str, list[float]] = defaultdict(list)


def inc(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] += amount


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = value


def observe(name: str, seconds: float) -> None:
    with _lock:
        _latency[name].append(float(seconds))
        if len(_latency[name]) > 500:
            _latency[name] = _latency[name][-250:]


def timed(name: str):
    """Context manager to record a latency observation."""

    class _Timer:
        def __enter__(self):
            self.start = time.perf_counter()
            return self

        def __exit__(self, *exc):
            observe(name, time.perf_counter() - self.start)

    return _Timer()


def snapshot() -> dict:
    with _lock:
        counters = dict(_counters)
        gauges = dict(_gauges)
        latencies = {}
        for k, v in _latency.items():
            if not v:
                continue
            sorted_v = sorted(v)
            n = len(sorted_v)
            latencies[k] = {
                "count": n,
                "mean_s": round(sum(sorted_v) / n, 4),
                "p50_s": round(sorted_v[n // 2], 4),
                "p95_s": round(sorted_v[int(n * 0.95)], 4),
                "max_s": round(sorted_v[-1], 4),
            }
    return {
        "counters": counters,
        "gauges": gauges,
        "latency_s": latencies,
        "uptime_s": round(time.perf_counter() - _start, 1),
    }


def reset() -> None:
    with _lock:
        _counters.clear()
        _gauges.clear()
        _latency.clear()


_start = time.perf_counter()
