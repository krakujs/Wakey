# WF-02 — Ingestion Pipeline

> Behavioral contract: from platform delivery to stored, redacted LogEvent. Features: ING-1..13, DET-1, SEC-1 · Tasks: E4-*, E5-T1.

## Purpose & trigger

Every log/deploy/vuln event a platform delivers enters through here. Triggered continuously. **Ingest never calls an LLM and never decides anything semantic** — it authenticates, normalizes, redacts, persists, and hands off to the queue (WF-03 decides).

## Actors & I/O

- **Log platforms**: GCP Pub/Sub push, generic webhooks, `wakey-agent` sidecar, (P2) CloudWatch/Azure/k8s.
- **Inputs**: platform payloads → `POST /ingest/<service-key>`; GitHub deploy webhooks → `POST /webhooks/github`.
- **Outputs**: redacted `LogEvent` rows + queue messages; dead-letter entries; metrics.

## Steps

1. **Authenticate** (`ING-1`): service key → service lookup. Per-key rate limit and payload cap: over → `429` + `Retry-After`; oversized → `413`. Unknown key → `401`, counted on `wakey_ingest_rejected_total{reason="auth"}`.
2. **Verify source** (`ING-2/3/4`): GCP push → OIDC token (audience = ingest URL, issuer accounts.google.com); generic webhook → HMAC header over raw body; sidecar → HMAC. Any failure → `401` + dead-letter on repeated failures.
3. **Idempotency** (`ING-11`): extract delivery/event id (Pub/Sub `messageId`, `X-Wakey-Delivery-Id`, or payload hash); if already processed → `200` no-op. Retention of ids: 7 days.
4. **Envelope decode & format auto-detect** (`ING-3`, `DET-1`): try known envelopes in order — GCP Pub/Sub→Cloud Logging, JSON line, JSON array, logfmt, plain text. Result: candidate raw lines + timestamps + severity hints + attributes. Undetectable → dead-letter with reason, `200` to prevent platform retry storms (configurable to `400` for dev-time strictness).
5. **Parse into events** (per line): timestamp (missing → delivery time), message, severity (map platform severities → internal `debug/info/warning/error/critical`), extracted **stack trace** if the line(s) contain one (language detectors feed WF-03), **trace/request/span IDs** (`ING-13`) where present, service, source platform, environment label (from key/config).
6. **Redact — first pass** (`SEC-1`, WF-09 §3): builtin patterns + service `redact:` list run over every string field before anything else. Redaction replaces with `[REDACTED:<type>]` and increments counters; a sample hash (never content) of redaction hits is auditable.
7. **Persist & enqueue**: `LogEvent` row (redacted) + queue message for WF-03. Queue full → apply backpressure: `429` to pull-pushers; push platforms get buffering in dead-letter with replay (step 8). **Never drop.**
8. **Dead-letter & replay** (`ING-12`): failed/unparsed batches stored redacted, retention per config (default 14d), replayable from dashboard/API with identical processing semantics (replay is idempotent by step 3).
9. **Deploy events** (`ING-8`): GitHub deployment statuses and Cloud Run revision labels parse into `DeployEvent` rows (service, environment, commit SHA, revision, timestamp). Deploy events are **never** redaction candidates (they contain no logs) but are size-capped and schema-validated.
10. **Ack semantics**: pull pushers only after durable persist; at-least-once overall with exactly-once effect via step 3 (NFR-3).

## State written

`log_events` (redacted), `deploy_events`, `dead_letters`, `delivery_ids`, `audit` (auth failures patterns only — no payloads).

## Failure modes

| Failure | Expected behavior |
|---|---|
| Storage down | Reject with `503` + Retry-After; platforms retry per their policy; metrics alarm |
| Queue saturated | Backpressure (`429`) to pull pushers; buffer+dead-letter for push platforms; never block the event loop |
| Malformed single line in batch | Skip that line to dead-letter with reason; process the rest of the batch |
| Clock skew (future/past timestamps) | Clamp to [now-7d, now+5m], flag `timestamp_clamped` |
| Unknown envelope format | Dead-letter + counter; never 5xx-spam the platform |
| Redaction engine error (bad custom regex) | Config rejected at load (validation), so runtime redaction is total; defensive: on engine exception, drop the string field entirely (fail-closed) rather than store raw |
| Duplicate delivery | `200` no-op, counted separately |

## Acceptance criteria

1. Security corpus (15+ secret shapes) pushed through ingest → zero secret substrings in `log_events` (e2e, blocks merge on regression).
2. Replay of the same Pub/Sub envelope 2× → exactly one stored event (idempotency test).
3. Expired/wrong-audience OIDC → 401 (contract).
4. 1k events/s for 60s → zero loss, p95 ingest→queued latency <2s (perf test).
5. Batch with 1 bad line + 99 good → 99 processed, 1 dead-lettered (unit).
6. Every rejection reason has a metric (unit asserts counter names/labels).
