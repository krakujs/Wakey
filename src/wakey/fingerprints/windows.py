# SPDX-License-Identifier: Apache-2.0
"""Sliding-window occurrence counters (WF-03, DET-4/DET-6).

In-memory per-fingerprint rate counting used by the policy gate: wake iff the
trailing-window error count crosses ``wake.rate_per_min`` (DET-6). Occurrences
are timestamps per fingerprint hash; expired ones are dropped lazily on
record/count, or eagerly via :meth:`SlidingWindowCounters.prune`.

This is the hot-path counter only — durable accounting lives in the DB
(``occurrence_windows``, NFR-3); this structure exists so the consumer never
blocks on storage to make a wake decision.

Timestamps are assumed roughly monotonic per key (event time or delivery time,
per the WF-03 clock-skew rule); the deque order and the least-recently-seen
eviction order rely on that.
"""

from __future__ import annotations

from collections import OrderedDict, deque


class SlidingWindowCounters:
    """Bounded in-memory sliding-window counters keyed by fingerprint hash.

    Args:
        window_seconds: Width of the trailing window. An occurrence at exactly
            ``now - window_seconds`` is still in-window (inclusive lower edge).
        max_keys: Maximum number of distinct fingerprints tracked. When
            exceeded, the least-recently-seen key is evicted ("last seen" =
            newest timestamp recorded for that key) so memory stays bounded
            even under fingerprint-cardinality storms.
    """

    def __init__(self, window_seconds: int = 60, max_keys: int = 10000) -> None:
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        # OrderedDict order = recency (least-recently-seen first). Each value
        # is that key's occurrence timestamps, oldest at the left of the deque.
        self._windows: OrderedDict[str, deque[float]] = OrderedDict()

    def record(self, key: str, ts: float) -> None:
        """Store an occurrence of ``key`` at unix-seconds ``ts``."""
        window = self._windows.get(key)
        if window is not None:
            self._drop_expired(key, window, ts)
            window = self._windows.get(key)
        if window is None:
            window = deque[float]()
            self._windows[key] = window
        self._windows.move_to_end(key)
        window.append(ts)
        self._evict_over_limit()

    def count_in_window(self, key: str, now: float) -> int:
        """Occurrences of ``key`` within ``(now - window_seconds, now]``.

        Expired timestamps are pruned as a side effect (lazy expiry).
        """
        window = self._windows.get(key)
        if window is None:
            return 0
        self._drop_expired(key, window, now)
        return len(self._windows.get(key, ()))

    def prune(self, now: float) -> int:
        """Drop every timestamp older than the trailing window; return count dropped."""
        dropped = 0
        for key in list(self._windows):
            window = self._windows[key]
            dropped += self._drop_expired(key, window, now)
        return dropped

    def _drop_expired(self, key: str, window: deque[float], now: float) -> int:
        """Popleft timestamps outside the window; drop the key once empty."""
        cutoff = now - self.window_seconds
        dropped = 0
        while window and window[0] < cutoff:
            window.popleft()
            dropped += 1
        if not window:
            del self._windows[key]
        return dropped

    def _evict_over_limit(self) -> None:
        """Evict least-recently-seen keys until back within ``max_keys``."""
        while len(self._windows) > self.max_keys:
            self._windows.popitem(last=False)
