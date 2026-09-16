#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Resource benchmark (E2-T6, NFR-6): pipeline load vs memory/time budgets.

Runs SYNTHETIC events (no network, no LLM) through the real pipeline and
asserts the NFR-6 budgets: RSS <= 350MB, wall time within the throughput
target, host-safe by design (rate-limited generator, bounded queues).
Exit 0 = within budget. CI fails on regression.
"""

from __future__ import annotations

import hashlib
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from wakey.core.config import Settings  # noqa: E402
from wakey.core.metrics import MetricsRegistry  # noqa: E402
from wakey.core.models import Service  # noqa: E402
from wakey.core.server import create_app  # noqa: E402
from wakey.core.storage import SQLiteStorage  # noqa: E402


class CountingForge:
    """Silent ForgePort: counts calls, never prints (bench stays quiet)."""

    def __init__(self) -> None:
        self.created = 0

    def create_ticket(self, fingerprint: object, title: str, body: str) -> object:
        self.created += 1
        return type("R", (), {"issue_id": str(self.created), "url": "bench://1"})()

    def add_comment(self, ticket: object, body: str) -> None:
        pass

    def open_draft_proposal(self, branch: str, title: str, body: str) -> object:
        return type("R", (), {"issue_id": "1", "url": "bench://pull/1"})()


N_EVENTS = 5000
RSS_BUDGET_MB = 350.0


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KB -> MB


def main() -> int:
    key = "wk_bench_key"
    key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]

    with tempfile.TemporaryDirectory() as tmp:
        storage = SQLiteStorage(Path(tmp) / "wakey.db")
        storage.save_service(Service(name="bench-api", repo="acme/bench", ingest_key_hash=key_hash))
        app = create_app(Settings(), storage, MetricsRegistry(), forge=CountingForge())
        client = TestClient(app)

        # 50 distinct error families x 100 churned occurrences = 5000 events, 50 fingerprints
        start = time.monotonic()
        for family in range(50):
            lines = [
                f"ERROR: deadlock detected in worker {family} transaction {i} on shard {i % 8}"
                for i in range(100)
            ]
            client.post(f"/ingest/{key}", content="\n".join(lines))
        elapsed = time.monotonic() - start

        rss = rss_mb()
        fingerprints = storage.list_active_fingerprints()
        throughput = N_EVENTS / elapsed

        print(f"events: {N_EVENTS} | elapsed: {elapsed:.1f}s | throughput: {throughput:.0f}/s")
        print(f"rss: {rss:.0f}MB (budget 350MB) | fingerprints: {len(fingerprints)}")

        ok = rss <= 350.0 and throughput >= 500
        print("BENCH", "PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
