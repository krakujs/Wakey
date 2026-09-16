#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Two-service pipeline demo (docs/two-service-local-test.md).

Runs the full local pipeline — ingest → redact → fingerprint → dedup →
policy → ticket (console sink) — against two dummy services and asserts
the 7 scenarios. Exit 0 = all green. No GitHub, no LLM keys, no network.

Usage:  make demo-two-services   (or python3 demo/two_service_demo.py)
"""

from __future__ import annotations

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

    for name, repo, environment in (
        ("demo-service-a", "acme/demo-a", "prod"),
        ("demo-service-b", "acme/demo-b", "prod"),
        ("demo-service-b-staging", "acme/demo-b", "staging"),
    ):
        key = f"wk_{name}_key"
        storage.save_service(
            Service(name=name, repo=repo, environment=environment, ingest_key_hash=__import__("hashlib").sha256(key.encode()).hexdigest()[:16])
        )

    print("== two-service local demo ==")
    error_a = "ERROR: connection refused to payments-db:5432 for order 84d8e79a-1234-4c0d-9a5f-2b6f9f5f1a33"
    secret_line = "ERROR: auth failed for AKIAIOSFODNN7EXAMPLE at checkout"

    # 1. service-a bursts 50 identical errors -> one fingerprint, one ticket
    r = client.post("/ingest/wk_demo-service-a_key", content="\n".join([error_a] * 50)).json()
    check("1 burst dedup", f"50 events -> {r['outcomes']}", r["accepted"] == 50 and r["outcomes"].get("ticketed") == 1)

    # 2. same error class in service-b -> different fingerprint, own ticket
    error_b = "ERROR: connection refused to payments-db:5432 for order 9999"
    r = client.post("/ingest/wk_demo-service-b_key", content="\n".join([error_b] * 3)).json()
    check("2 service scoping", f"service-b ticketed separately: {r['outcomes']}", r["outcomes"].get("ticketed") == 1)

    # 3. churned ids/uuids/ports in service-a -> still the same fingerprint
    churned = "ERROR: connection refused to payments-db:9999 for order f0e1d2c3-9876-4abc-8def-1122334455aa"
    r = client.post("/ingest/wk_demo-service-a_key", content="\n".join([churned] * 5)).json()
    check("3 churn stability", f"absorbed into existing ticket: {r['outcomes']}", "ticketed" not in r["outcomes"])

    # 4. secrets in logs never reach storage or tickets
    r = client.post("/ingest/wk_demo-service-a_key", content="\n".join([secret_line] * 3)).json()
    ticket_bodies = " ".join(body for _, body in forge.created)
    check("4 redaction", f"ticketed={r['outcomes'].get('ticketed')}, secret in body: {'AKIA' in ticket_bodies}", r["outcomes"].get("ticketed") == 1 and "AKIAIOSFODNN7EXAMPLE" not in ticket_bodies)

    # 5. staging environment scopes fingerprints
    r = client.post("/ingest/wk_demo-service-b-staging_key", content="\n".join([error_b] * 3)).json()
    check("5 env scoping", f"staging fingerprint separate: {r['outcomes']}", r["outcomes"].get("ticketed") == 1)
    assert len(forge.created) == 4, "expected 4 distinct tickets total"

    # 6. mixed good/bad lines: bad JSON dead-lettered, good processed, no crash
    mixed = '{"msg": "good line"}\n{"broken json\nanother plain line'
    r = client.post("/ingest/wk_demo-service-b_key", content=mixed).json()
    check("6 dead letters", f"accepted={r['accepted']}, dead={r['dead_lettered']}", r["accepted"] == 2 and r["dead_lettered"] == 1)

    # 7. below-threshold events recorded without tickets (host-safe, in-memory)
    r = client.post("/ingest/wk_demo-service-b_key", content="ERROR: minor blip\nERROR: minor blip").json()
    check("7 record-only", f"below threshold: {r['outcomes']}", "ticketed" not in r["outcomes"])

    failed = [s for ok, s, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
