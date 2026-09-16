# SPDX-License-Identifier: Apache-2.0
"""Minimal Prometheus text-exposition metrics (lean: no client dependency).

Only counters for now — the pipeline's key metrics (ingest totals, reject
reasons, fingerprint counts) are all counters. Gauges arrive when a metric
actually needs one; render() emits valid Prometheus text format.
"""

from __future__ import annotations

from typing import Any


class MetricsRegistry:
    """Thread-safe-in-practice counter registry (GIL-atomic dict ops)."""

    def __init__(self) -> None:
        self._meta: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def inc(
        self, name: str, help_text: str, labels: dict[str, str] | None = None, value: float = 1.0
    ) -> None:
        """Increment a counter, registering it on first use.

        Label dict must match the labels the name was first registered with —
        a mismatch is a programming error and raises immediately.
        """
        label_items = tuple(sorted((labels or {}).items()))
        known = self._meta.get(name)
        if known is None:
            self._meta[name] = (help_text, tuple(label for label, _ in label_items))
        elif known[1] != tuple(label for label, _ in label_items):
            msg = f"label mismatch for {name}: expected {known[1]}, got {label_items}"
            raise ValueError(msg)
        key = (name, label_items)
        self._values[key] = self._values.get(key, 0.0) + value

    def render(self) -> str:
        lines: list[str] = []
        for name in sorted(self._meta):
            help_text, _ = self._meta[name]
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            for (metric, label_items), value in sorted(self._values.items()):
                if metric != name:
                    continue
                label_str = ",".join(f'{key}="{val}"' for key, val in label_items)
                rendered = f"{name}{{{label_str}}}" if label_str else name
                lines.append(f"{rendered} {value}")
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict[str, Any]:
        """Test/inspection view of raw counter values keyed by 'name{labels}'."""
        out: dict[str, Any] = {}
        for (metric, label_items), value in self._values.items():
            label_str = ",".join(f'{k}="{v}"' for k, v in label_items)
            key = f"{metric}{{{label_str}}}" if label_str else metric
            out[key] = value
        return out
