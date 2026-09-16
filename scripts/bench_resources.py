#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Resource benchmark (E2-T6, NFR-6): pipeline load vs memory/time budgets.

Runs SYNTHETIC events (no network, no LLM) through the real pipeline and
asserts the NFR-6 budgets: RSS <= 350MB, wall time within the throughput
target, host-safe by design (rate-limited generator, bounded queues).

Correctness rules (R-14): error families are *semantically distinct
messages* (not numbers that templating erases); throughput counts only
verified-accepted events (HTTP 202 + stored rows); every delivery must
complete. Exit 0 = within budget.
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
from wakey.ingest.pipeline import IngestPipeline  # noqa: E402
from wakey.ingest.worker import DeliveryWorker  # noqa: E402
from wakey.security.redaction import RedactionEngine  # noqa: E402


class CountingForge:
    """Silent ForgePort: counts calls, never prints (bench stays quiet)."""

    def __init__(self) -> None:
        self.created = 0

    def create_ticket(self, fingerprint: object, title: str, body: str) -> object:
        self.created += 1
        return type("R", (), {"issue_id": str(self.created), "url": "bench://1"})()

    def add_comment(self, ticket: object, body: str) -> None:
        pass

    def open_draft_proposal(self, branch: str, title: str, body: str, base: str) -> object:
        return type("R", (), {"issue_id": "1", "url": "bench://pull/1"})()

    def default_branch(self) -> str:
        return "main"

    def create_branch(self, branch: str, base: str) -> None:
        pass

    def commit_files(self, branch: str, message: str, files: dict[str, str]) -> str:
        return "bench-sha"

    def close_ticket(self, ticket: object, comment: str | None = None) -> None:
        pass

    def reopen_ticket(self, ticket: object, comment: str | None = None) -> None:
        pass

    def add_label(self, ticket: object, label: str) -> None:
        pass


N_EVENTS = 5000
N_FAMILIES = 50
RSS_BUDGET_MB = 350.0
THROUGHPUT_FLOOR = 500  # events/s verified-accepted

# Semantically distinct messages: templating cannot merge these families.
FAMILY_MESSAGES = [
    f"ERROR: {message}"
    for message in (
        "deadlock detected in transaction coordinator",
        "connection refused to payments-db",
        "null pointer dereferenced in invoice renderer",
        "disk quota exceeded on shard volume",
        "authentication failed for ldap bind",
        "certificate expired for upstream gateway",
        "cache stampede on session store",
        "unhandled promise rejection in checkout worker",
        "segmentation fault in image resizer",
        "permission denied writing temp file",
        "queue depth exceeded for email worker",
        "schema drift detected in events table",
        "clock skew beyond tolerance on node",
        "connection reset by load balancer",
        "out of memory in report generator",
        "index corruption detected in search shard",
        "handshake timeout with s3 gateway",
        "division by zero in pricing calculator",
        "stack overflow in recursive parser",
        "license checkout failed for solver",
        "orphaned job in scheduler registry",
        "lock contention on inventory row",
        "message deserialization failed on bus",
        "retry budget exhausted for webhook",
        "thread pool starvation in api server",
        "partial write detected in ledger append",
        "checksum mismatch on artifact download",
        "eviction storm on hot cache partition",
        " tls handshake failure with internal ca".strip(),
        "replication lag beyond warn threshold",
        "failed to acquire advisory lock",
        "connection pool exhausted for postgres",
        "uncaught typeerror in report renderer",
        "ambiguous column reference in query planner",
        "rate limiter tripped on partner api",
        "zombie process detected in worker pool",
        "file descriptor leak in export service",
        "garbage collection pause exceeded slc",
        "quota exceeded for pubsub publisher",
        "signature verification failed for hook",
        "secret rotation mismatch on vault client",
        "circuit breaker open for recommendations",
        "feature flag service unreachable",
        "session affinity broken behind proxy",
        "batch job exceeded wall clock budget",
        "snapshot consistency check failed",
        "dead letter queue depth above alert",
        "health probe flapped for instance",
        "compaction backlog on timeseries store",
        "buffer overflow caught in codec",
    )
]


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KB -> MB


def main() -> int:
    key = "wk_bench_key"
    key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
    per_family = N_EVENTS // N_FAMILIES

    with tempfile.TemporaryDirectory() as tmp:
        storage = SQLiteStorage(Path(tmp) / "wakey.db")
        storage.save_service(Service(name="bench-api", repo="acme/bench", ingest_key_hash=key_hash))
        forge = CountingForge()
        app = create_app(Settings(), storage, MetricsRegistry(), forge=forge)
        client = TestClient(app)
        worker = DeliveryWorker(storage, IngestPipeline(storage, forge), RedactionEngine())

        accepted = 0
        start = time.monotonic()
        for family, message in enumerate(FAMILY_MESSAGES[:N_FAMILIES]):
            lines = [f"{message} on shard {i % 8}" for i in range(per_family)]
            response = client.post(f"/ingest/{key}", content="\n".join(lines))
            if response.status_code != 202:  # R-14: response status is verified
                print(f"BENCH FAIL: delivery {family} rejected: {response.status_code}")
                return 1
            if response.json().get("accepted") is not True:
                print(f"BENCH FAIL: delivery {family} not accepted")
                return 1
            accepted += per_family
            while worker.process_next():
                pass
        elapsed = time.monotonic() - start

        stored_rows = storage._conn.execute("SELECT COUNT(*) FROM log_events").fetchone()[0]  # noqa: SLF001
        fingerprints = storage.list_active_fingerprints()
        throughput = accepted / elapsed

        print(f"events: {accepted} verified | elapsed: {elapsed:.1f}s | {throughput:.0f}/s")
        print(f"rss: {rss_mb():.0f}MB (budget 350MB) | fingerprints: {len(fingerprints)}")

        ok = (
            rss_mb() <= RSS_BUDGET_MB
            and throughput >= THROUGHPUT_FLOOR
            and stored_rows == N_EVENTS
            and len(fingerprints) == N_FAMILIES
            and storage.pending_delivery_count() == 0
        )
        if stored_rows != N_EVENTS:
            print(f"BENCH FAIL: stored {stored_rows} != accepted {N_EVENTS}")
        if len(fingerprints) != N_FAMILIES:
            print(f"BENCH FAIL: expected {N_FAMILIES} distinct families, got {len(fingerprints)}")
        print("BENCH", "PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
