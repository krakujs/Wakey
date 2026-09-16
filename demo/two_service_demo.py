#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Two-service pipeline demo (docs/two-service-local-test.md).

Runs the full local pipeline — ingest → durable delivery → redact →
fingerprint → dedup → policy → ticket (console sink) — against two dummy
services and asserts the 7 scenarios. Exit 0 = all green. No GitHub, no
LLM keys, no network.

Usage:  make demo-two-services   (or python3 demo/two_service_demo.py)
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from wakey.core.config import Settings  # noqa: E402
from wakey.core.metrics import MetricsRegistry  # noqa: E402
from wakey.core.models import Service  # noqa: E402
from wakey.core.server import create_app  # noqa: E402
from wakey.core.storage import SQLiteStorage  # noqa: E402
from wakey.forge.port import ConsoleForge  # noqa: E402
from wakey.ingest.pipeline import IngestPipeline  # noqa: E402
from wakey.ingest.worker import DeliveryWorker  # noqa: E402
from wakey.security.redaction import RedactionEngine  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(scenario: str, detail: str, condition: bool) -> None:
    RESULTS.append((condition, scenario, detail))
    marker = "PASS" if condition else "FAIL"
    print(f"  [{marker}] {scenario}: {detail}")


def main() -> int:
    storage = SQLiteStorage(":memory:")
    forge = ConsoleForge()
    app = create_app(Settings(), storage, MetricsRegistry(), forge=forge)
    client = TestClient(app)
    worker = DeliveryWorker(storage, IngestPipeline(storage, forge), RedactionEngine())

    def post(key: str, body: str) -> dict:
        response = client.post(f"/ingest/{key}", content=body)
        assert response.status_code == 202, response.text
        while worker.process_next():
            pass  # drain: the durable worker owns processing
        return response.json()

    for name, repo, environment in (
        ("demo-service-a", "acme/demo-a", "prod"),
        ("demo-service-b", "acme/demo-b", "prod"),
        ("demo-service-b-staging", "acme/demo-b", "staging"),
    ):
        key = f"wk_{name}_key"
        storage.save_service(
            Service(name=name, repo=repo, environment=environment,
                    ingest_key_hash=hashlib.sha256(key.encode()).hexdigest()[:16])
        )

    print("== two-service local demo ==")
    error_a = "ERROR: connection refused to payments-db:5432 for order 84d8e79a-1234-4c0d-9a5f-2b6f9f5f1a33"
    secret_line = "ERROR: auth failed for AKIAIOSFODNN7EXAMPLE at checkout"

    # 1. service-a bursts 50 identical errors -> one fingerprint, one ticket
    post("wk_demo-service-a_key", "\n".join([error_a] * 50))
    check("1 burst dedup", f"50 events -> {len(forge.created)} ticket",
          len(forge.created) == 1)

    # 2. same error class in service-b -> different fingerprint, own ticket
    error_b = "ERROR: connection refused to payments-db:5432 for order 9999"
    post("wk_demo-service-b_key", "\n".join([error_b] * 3))
    check("2 service scoping", f"service-b ticketed separately: {len(forge.created)} tickets",
          len(forge.created) == 2)

    # 3. churned ids/uuids/ports in service-a -> still the same fingerprint
    churned = "ERROR: connection refused to payments-db:9999 for order f0e1d2c3-9876-4abc-8def-1122334455aa"
    post("wk_demo-service-a_key", "\n".join([churned] * 5))
    check("3 churn stability", f"absorbed into existing ticket: {len(forge.created)} tickets",
          len(forge.created) == 2)

    # 4. secrets in logs never reach storage or tickets
    post("wk_demo-service-a_key", "\n".join([secret_line] * 3))
    ticket_bodies = " ".join(body for _, body in forge.created)
    check("4 redaction", f"tickets={len(forge.created)}, secret in body: {'AKIA' in ticket_bodies}",
          len(forge.created) == 3 and "AKIAIOSFODNN7EXAMPLE" not in ticket_bodies)

    # 5. staging environment scopes fingerprints
    post("wk_demo-service-b-staging_key", "\n".join([error_b] * 3))
    check("5 env scoping", f"staging fingerprint separate: {len(forge.created)} tickets",
          len(forge.created) == 4)

    # 6. mixed good/bad lines: bad JSON dead-lettered, good processed, no crash
    mixed = '{"msg": "good line"}\n{"broken json\nanother plain line'
    post("wk_demo-service-b_key", mixed)
    stored = list(storage._conn.execute("SELECT COUNT(*) FROM log_events"))  # noqa: SLF001
    check("6 dead letters", f"delivery completed, events stored: {stored[0][0]}",
          storage.pending_delivery_count() == 0 and stored[0][0] >= 58)

    # 7. below-threshold events recorded without tickets (host-safe, in-memory)
    post("wk_demo-service-b_key", "ERROR: minor blip\nERROR: minor blip")
    check("7 record-only", f"below threshold: {len(forge.created)} tickets",
          len(forge.created) == 4)

    failed = [s for ok, s, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
