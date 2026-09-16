# SPDX-License-Identifier: Apache-2.0
"""Ingest end-to-end tests over the durable delivery path (R-02/R-04/R-05).

Contract: POST /ingest persists a pending delivery atomically and answers
202 — parsing, redaction of split fields, and pipeline dispatch happen in
the DeliveryWorker. These tests assert the *desired* behavior: no lost
events on failure, no duplicate tickets on retry, no secrets at rest.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Service
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge, TicketRef
from wakey.ingest.pipeline import IngestPipeline
from wakey.ingest.worker import DeliveryWorker
from wakey.security.redaction import RedactionEngine

KEY = "wk_demo_service_key"


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class FlakyForge:
    """ConsoleForge whose create_ticket fails the first ``fail_times`` calls."""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.created: list[tuple[object, str]] = []
        self.comments: list[tuple[TicketRef, str]] = []

    def create_ticket(self, fingerprint: object, title: str, body: str) -> TicketRef:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("forge outage")
        self.created.append((fingerprint, body))
        return TicketRef(issue_id=f"console-{len(self.created)}", url="console://tickets/1")

    def add_comment(self, ticket: TicketRef, body: str) -> None:
        self.comments.append((ticket, body))

    def open_draft_proposal(self, branch: str, title: str, body: str) -> TicketRef:
        return TicketRef(issue_id=f"console-{branch}", url=f"console://pulls/{branch}")


@pytest.fixture()
def env(tmp_path: Path):
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        Service(name="payments-api", repo="acme/payments", ingest_key_hash=key_hash(KEY))
    )
    forge = ConsoleForge()
    redactor = RedactionEngine()
    pipeline = IngestPipeline(storage, forge)
    worker = DeliveryWorker(storage, pipeline, redactor)
    app = create_app(Settings(), storage, MetricsRegistry(), forge=forge, redactor=redactor)
    app.state.forge = forge
    return TestClient(app), storage, forge, worker


def drain(worker: DeliveryWorker) -> int:
    """Process every pending delivery; returns how many were handled."""
    handled = 0
    while worker.process_next():
        handled += 1
    return handled


def test_unknown_key_rejected(env) -> None:
    client, _, _, _ = env
    response = client.post("/ingest/wrong-key", content='{"msg": "boom"}')
    assert response.status_code == 401


def test_accepted_delivery_is_durable_then_ticketed_once(env) -> None:
    client, storage, forge, worker = env
    burst = "\n".join(["ERROR: connection refused to db"] * 3)
    response = client.post(f"/ingest/{KEY}", content=burst)
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["state"] == "pending"
    assert body["delivery_id"]
    assert storage.pending_delivery_count() == 1  # durable before the answer

    assert drain(worker) == 1
    assert len(forge.created) == 1  # threshold crossed exactly once

    # in-flight suppression: more of the same never re-tickets (DET-14)
    more = client.post(f"/ingest/{KEY}", content="ERROR: connection refused to db")
    assert more.status_code == 202
    drain(worker)
    assert len(forge.created) == 1


def test_replayed_delivery_deduplicates_even_mid_flight(env) -> None:
    client, _, forge, worker = env
    burst = "\n".join(["ERROR: boom one"] * 3)
    first = client.post(f"/ingest/{KEY}", content=burst, headers={"X-Delivery-Id": "d-1"})
    assert first.status_code == 202
    replay = client.post(f"/ingest/{KEY}", content=burst, headers={"X-Delivery-Id": "d-1"})
    assert replay.json() == {"deduplicated": True}
    drain(worker)
    drain(worker)
    assert len(forge.created) == 1  # exactly one ticket across both requests


def test_two_services_sharing_a_delivery_id_both_process(env) -> None:
    client, storage, forge, worker = env
    storage.save_service(
        Service(name="other-api", repo="acme/other", ingest_key_hash=key_hash("wk_other_key"))
    )
    burst = "\n".join(["ERROR: distinct failure"] * 3)
    for key in (KEY, "wk_other_key"):
        response = client.post(f"/ingest/{key}", content=burst, headers={"X-Delivery-Id": "shared"})
        assert response.status_code == 202
    drain(worker)
    # namespace by service: the shared id must not swallow the second service
    assert len(forge.created) == 2


def test_forge_outage_loses_nothing_and_never_double_tickets(env) -> None:
    client, storage, _, _ = env
    flaky = FlakyForge(fail_times=1)
    pipeline = IngestPipeline(storage, flaky)
    worker = DeliveryWorker(storage, pipeline, RedactionEngine())

    response = client.post(
        f"/ingest/{KEY}",
        content="\n".join(["ERROR: connection refused to db"] * 3),
        headers={"X-Delivery-Id": "d-outage"},
    )
    assert response.status_code == 202
    worker.process_next()  # forge fails mid-processing
    state_before = storage.pending_delivery_count()
    assert state_before == 1  # still recoverable, not lost

    drain(worker)
    assert len(flaky.created) == 1  # retried once to completion, no duplicates

    replay = client.post(
        f"/ingest/{KEY}",
        content="\n".join(["ERROR: connection refused to db"] * 3),
        headers={"X-Delivery-Id": "d-outage"},
    )
    assert replay.json() == {"deduplicated": True}


def test_process_restart_recovers_stale_processing_delivery(env) -> None:
    client, storage, forge, worker = env
    client.post(f"/ingest/{KEY}", content="\n".join(["ERROR: boom"] * 3))
    delivery = storage.claim_next_delivery()
    assert delivery is not None  # crashed here: claimed but never completed

    recovered = storage.recover_stale_deliveries()
    assert recovered == 1
    drain(worker)
    assert len(forge.created) == 1
    assert storage.pending_delivery_count() == 0


def test_mid_batch_malformed_line_isolated_valid_siblings_process(env) -> None:
    client, storage, forge, worker = env
    payload = "\n".join(
        (
            '{"level": "error", "message": "valid sibling one"}',
            '{"level": [], "message": "malformed severity"}',
            '{"level": "error", "message": "valid sibling two"}',
        )
    )
    client.post(f"/ingest/{KEY}", content=payload)
    drain(worker)  # must not raise; valid siblings processed
    assert storage.pending_delivery_count() == 0


def test_secrets_never_reach_the_ticket_body(env) -> None:
    client, _, forge, worker = env
    line = "ERROR: auth failed for AKIAIOSFODNN7EXAMPLE at checkout"
    client.post(f"/ingest/{KEY}", content="\n".join([line] * 3))
    drain(worker)
    assert forge.created, "ticket expected for auth failure error"
    _, body = forge.created[-1]
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert "[REDACTED:aws_access_key]" in body


def test_secrets_in_ids_and_attributes_are_redacted_at_rest(env) -> None:
    client, storage, _, worker = env
    payload = (
        '{"level": "error", "message": "boom",'
        ' "trace_id": "tok ghp_Abcdefghijklmnopqrstuvwxyz123456",'
        ' "request_id": "ghp_Zyxwvutsrqponmlkjihgfedcba654321",'
        ' "api_key": "ghp_KeyAttrOverTwentyCharsLong"}'
    )
    client.post(f"/ingest/{KEY}", content=payload)
    drain(worker)
    events = list(storage._conn.execute("SELECT * FROM log_events"))  # noqa: SLF001
    assert events, "event persisted"
    row = events[-1]
    assert "ghp_" not in (row["trace_id"] or "")
    assert "ghp_" not in (row["request_id"] or "")
    assert "ghp_" not in row["attributes_json"]


def test_multiline_private_key_block_redacted_before_storage(env) -> None:
    client, storage, _, worker = env
    pem = (
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEF\n"
        "AASCBKwKggQEBAQAggEWA0x2999=\n-----END PRIVATE KEY-----"
    )
    client.post(f"/ingest/{KEY}", content=pem)
    drain(worker)
    rows = list(storage._conn.execute("SELECT message FROM log_events"))  # noqa: SLF001
    assert rows, "multiline event persisted as one message"
    assert "MIIEvQ" not in rows[-1]["message"]
    assert (
        "REDACTED:private_key" in rows[-1]["message"]
        or "[REDACTION_FAILURE]" in rows[-1]["message"]
    )


def test_multiline_traceback_stays_one_event_and_gains_frames(env) -> None:
    client, storage, _, worker = env
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "app/billing.py", line 87, in charge\n'
        "    total = invoice['total']\n"
        "TypeError: 'NoneType' object is not subscriptable"
    )
    client.post(f"/ingest/{KEY}", content=traceback)
    drain(worker)
    rows = list(storage._conn.execute("SELECT message FROM log_events"))  # noqa: SLF001
    assert len(rows) == 1, "traceback reassembled into one event"
    fps = storage.list_active_fingerprints()
    assert fps and fps[0].frames, "frames extracted from reassembled traceback"


def test_info_only_stream_creates_no_incident(env) -> None:
    client, storage, forge, worker = env
    burst = "\n".join(["INFO: worker heartbeat ok"] * 12)
    client.post(f"/ingest/{KEY}", content=burst)
    drain(worker)
    assert not forge.created, "INFO must never ticket"
    fps = storage.list_active_fingerprints()
    assert all(fp.state.value == "new" for fp in fps)


def test_error_burst_creates_bounded_comments_not_flood(env) -> None:
    client, _, forge, worker = env
    client.post(f"/ingest/{KEY}", content="\n".join(["ERROR: flaky dep timeout"] * 3))
    drain(worker)
    assert len(forge.created) == 1
    # 50 further occurrences arrive in bursts; comment throttle must cap noise
    for _ in range(5):
        client.post(f"/ingest/{KEY}", content="\n".join(["ERROR: flaky dep timeout"] * 10))
        drain(worker)
    assert len(forge.comments) <= 6, f"comment flood: {len(forge.comments)}"


def test_payload_over_limit_rejected_with_413(env) -> None:
    client, _, _, _ = env
    big = "x" * (1 * 1024 * 1024 + 1)
    response = client.post(f"/ingest/{KEY}", content=big)
    assert response.status_code == 413


def test_observe_mode_still_tickets_but_never_dispatches_rca(env) -> None:
    client, storage, forge, worker = env
    service = storage.get_service("payments-api")
    assert service is not None
    storage.save_service(service.model_copy(update={"config_json": '{"autonomy": "observe"}'}))
    client.post(f"/ingest/{KEY}", content="\n".join(["ERROR: observed failure"] * 3))
    drain(worker)
    assert len(forge.created) == 1, "observe mode creates the ticket for humans"
    fps = storage.list_active_fingerprints()
    assert fps[0].state.value == "open", "observe never queues RCA"
