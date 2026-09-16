# SPDX-License-Identifier: Apache-2.0
# Changelog

All notable changes to Wakey are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-09-16

First open-source release of the complete P1 monitoring-and-repair loop.

### The loop

- **Ingestion** — `POST /ingest/<key>` accepts JSON lines, logfmt, or plain
  text; durable delivery persistence (202 before processing), per-service
  namespaced deduplication, dispatch journal for exactly-once pipeline
  effects, bounded retries with dead-lettering, 1 MB payload cap.
- **GCP Cloud Logging** — Pub/Sub push route with OIDC token verification
  (issuer/audience/expiry/service account) and messageId-based dedup.
- **Detection** — error fingerprinting (Python/Java/JS/PHP tracebacks,
  templated noise reduction), severity floor, sliding-window burst rates.
- **Ticketing** — GitHub issues with deduplicated fingerprints, per-service
  hourly caps, comment throttling, dispatch reservation before external calls.
- **RCA** — heuristic + live-model root-cause analysis, hourly budget,
  redacted prompts, posted to the ticket with class and confidence.
- **Fix proposals** — repro-first fix loop (failing test required), model
  patch generation, test-suite verification, draft PR publication with the
  *tested* tree committed to a real branch; never merges, never force-pushes.
- **Verification** — merge-webhook binding arms a post-fix watch window;
  deploy-aware grace; success closes the ticket with `wakey-verified`,
  recurrence reopens with `wakey-recurred` and blocks repeat auto-fixes.
- **Lifecycle** — auto-close on silence, reopen on recurrence, human
  decisions sticky.
- **Commands** — `@wakey drop/reopen/explain/status` via HMAC-verified
  webhooks; deny-by-default authorization.

### Platform

- **Dashboard** — first-run setup token (single-use, 30 min), admin sessions
  (HttpOnly SameSite=Lax cookies), setup wizard, live board, fingerprint
  detail pages, manual fix trigger, service config API.
- **Security** — two-pass redaction (18 secret families), SHA-256-hashed
  verify-only secrets, AES-256-GCM encrypted credential envelope under
  `WAKEY_MASTER_KEY` (GitHub App keys stored fail-closed), tamper-evident
  hash-chained audit log, loopback-only defaults until explicitly exposed.
- **Operations** — CLI (`serve/status/board/doctor/setup-token/backup/
  restore`), retention with hourly pruning, self-watch, resource benchmark,
  capped-container sandbox gate (`make gate-sandbox`), coverage floor ≥ 90 %
  enforced in CI.

### GitHub App

- Manifest-based onboarding (`/setup/github/start`), one-time code exchange,
  encrypted private-key storage, repo allowlist enforcement, and the signed
  webhook surface for issue comments and PR merges.

### Known limitations

- Cloud Run / container SQLite storage is ephemeral across revisions —
  Postgres (OPS-5) is the durable path.
- Fix execution requires a Python service with a `test_command`; repos
  needing system packages fail their repro honestly.
- Live GitHub/GCP gates were exercised against real endpoints only with
  founder credentials; see docs/threat-model.md for the security posture.
