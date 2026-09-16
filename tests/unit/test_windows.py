# SPDX-License-Identifier: Apache-2.0
"""Unit tests: SlidingWindowCounters — per-fingerprint sliding-window counts (DET-4/DET-6)."""

from __future__ import annotations

from wakey.fingerprints.windows import SlidingWindowCounters

WINDOW = 60
T0 = 1000.0


def test_burst_counting_within_window() -> None:
    counters = SlidingWindowCounters(window_seconds=WINDOW)
    for i in range(50):
        counters.record("fp-aaa", T0 + i)
    assert counters.count_in_window("fp-aaa", T0 + 49) == 50


def test_expiry_beyond_window() -> None:
    counters = SlidingWindowCounters(window_seconds=WINDOW)
    for i in range(5):
        counters.record("fp-aaa", T0 + i)
    # cutoff = 1059 - 60 = 999: all five still in-window.
    assert counters.count_in_window("fp-aaa", T0 + 59) == 5
    # cutoff = 1065 - 60 = 1005: every ts (1000..1004) is older than the window.
    assert counters.count_in_window("fp-aaa", T0 + 65) == 0


def test_event_exactly_at_window_edge_still_counts() -> None:
    counters = SlidingWindowCounters(window_seconds=WINDOW)
    counters.record("fp-aaa", T0)
    # cutoff == ts: the inclusive lower edge keeps the boundary occurrence.
    assert counters.count_in_window("fp-aaa", T0 + WINDOW) == 1
    assert counters.count_in_window("fp-aaa", T0 + WINDOW + 1) == 0


def test_evicts_least_recently_seen_key_when_max_keys_exceeded() -> None:
    counters = SlidingWindowCounters(window_seconds=WINDOW, max_keys=2)
    counters.record("fp-a", T0 + 1)
    counters.record("fp-b", T0 + 2)
    counters.record("fp-c", T0 + 3)  # fp-a is least-recently-seen -> evicted
    assert counters.count_in_window("fp-a", T0 + 3) == 0
    assert counters.count_in_window("fp-b", T0 + 3) == 1
    assert counters.count_in_window("fp-c", T0 + 3) == 1

    # Touch fp-b so it becomes the most recently seen, then overflow again:
    # fp-c (not fp-b) must now be the one evicted — order is LRU, not FIFO.
    counters.record("fp-b", T0 + 4)
    counters.record("fp-d", T0 + 5)
    assert counters.count_in_window("fp-b", T0 + 5) == 2
    assert counters.count_in_window("fp-c", T0 + 5) == 0
    assert counters.count_in_window("fp-d", T0 + 5) == 1


def test_per_key_isolation() -> None:
    counters = SlidingWindowCounters(window_seconds=WINDOW)
    for i in range(10):
        counters.record("fp-noisy", T0 + i)
    counters.record("fp-quiet", T0 + 30)
    assert counters.count_in_window("fp-noisy", T0 + 30) == 10
    assert counters.count_in_window("fp-quiet", T0 + 30) == 1

    # Recording more into one key never leaks into the other.
    counters.record("fp-quiet", T0 + 40)
    assert counters.count_in_window("fp-noisy", T0 + 40) == 10
    assert counters.count_in_window("fp-quiet", T0 + 40) == 2

    # A burst expiring in one key leaves the other untouched.
    assert counters.count_in_window("fp-noisy", T0 + 100) == 0
    # quiet's last stamp (T0 + 40) ages out one second later (inclusive edge).
    assert counters.count_in_window("fp-quiet", T0 + 101) == 0


def test_prune_returns_dropped_count_and_frees_keys() -> None:
    counters = SlidingWindowCounters(window_seconds=WINDOW)
    for i in range(5):
        counters.record("fp-old", T0 + i)
    for i in range(3):
        counters.record("fp-fresh", T0 + 100 + i)

    # cutoff = 1105 - 60 = 1045: the five old stamps drop, the fresh stay.
    assert counters.prune(T0 + 105) == 5
    assert counters.count_in_window("fp-old", T0 + 105) == 0
    assert counters.count_in_window("fp-fresh", T0 + 105) == 3

    # Idempotent: nothing left to drop for the old key.
    assert counters.prune(T0 + 105) == 0

    # Once the fresh stamps age out too, they drop and the key is freed.
    assert counters.prune(T0 + 200) == 3
    assert counters.count_in_window("fp-fresh", T0 + 200) == 0
