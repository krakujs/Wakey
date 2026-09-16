# SPDX-License-Identifier: Apache-2.0
# Threat model and security posture (WF-09 §8, E10-T6 self-review)

Scope: a self-hosted wakeyd instance (dashboard + ingest + webhooks +
workers), its SQLite store, and the credentials it holds. Trust boundary:
everything reachable over HTTP, plus the local data directory.

## Assets and adversaries

| Asset | Adversary of concern |
|---|---|
| Ingest keys (hashed) | an outside party who learned a key → can inject fake errors |
| Webhook secrets | an attacker who can spoof GitHub/CI pushes |
| App private key (after E3-T1 connect) | anyone with read access to the data dir |
| Dashboard session | a malicious webpage the operator visits (CSRF/XSS) |
| Log content (untrusted input) | the log itself: prompt injection into the model tier |
| Audit trail | an attacker with write access to the DB covering their tracks |

## Surfaces, controls, status

| Surface | Threat | Control | Status |
|---|---|---|---|
| `POST /ingest/<key>` | key guessing / spoofing | 128-bit random keys, SHA-256 at rest, constant-time hash compare | verified (tests) |
| `POST /ingest/gcp/<key>` | forged Pub/Sub push | OIDC Bearer verification (issuer/audience/expiry/service account) when configured; key auth always required | verified (claims); signature path live-gated |
| `POST /webhooks/*` | forged pushes | HMAC-SHA256 over the body, constant-time compare, **fail-closed without a configured secret**; delivery dedup | verified (tests) |
| payload size / floods | DoS | 1 MB cap → 413; durable queue with bounded retries + dead-letter; hourly per-service ticket cap | verified (tests) |
| secrets at rest | DB dump | ingest keys hashed; sessions/setup tokens stored as SHA-256; reversible secrets (webhook secrets, app private key) AES-256-GCM encrypted when `WAKEY_MASTER_KEY` is set — the GitHub App callback refuses plaintext storage | verified (tests) |
| audit trail | tampering | append-only hash chain (`prev_hash`/`entry_hash`), tamper test; `verify_audit_chain()` | verified (tests) |
| dashboard | anonymous access / CSRF | first-run token (single-use, 30 min) + HttpOnly SameSite=Lax sessions; `/board`,`/settings`,`/api/services*` session-gated; setup token printable only from the host (`wakey setup-token`) | verified (tests) |
| `/metrics` | info leak | aggregate counters only, no service names in labels | by design |
| model prompts | log-content prompt injection | system-prompt data fencing + second-pass redaction of every prompt (ModelRca); reply parsing restricted to `CLASS/CONFIDENCE/SUMMARY` lines | partial: fences + redaction tested; adversarial corpus (E10-T2) open |
| fix executor (R-03) | malicious patch / escape | workspace path containment, minimal env, RLIMITs, process-group kill, `make gate-sandbox` container gate (network-less, capped) | gate green; **live fixing disabled by default** |

## Accepted risks (explicit)

1. **Webhook secrets and app keys are plaintext at rest when
   `WAKEY_MASTER_KEY` is unset.** Development convenience mode; the
   GitHub App callback refuses to run in this mode. Production rule: set
   the master key.
2. **Dashboard over plain HTTP** — local/reverse-proxy deployment
   assumes the proxy terminates TLS; cookies gain `Secure`
   automatically when `BASE_URL` is HTTPS.
3. **SameSite=Lax is the P1 CSRF control.** A dedicated CSRF token for
   mutations is tracked for P2 (RBAC work, SEC-6).
4. **Regex ReDoS**: custom redaction patterns are length-capped and
   compile-validated, but matching time is not formally bounded.

## Open items

- E10-T2 adversarial prompt-injection corpus and per-stage tool
  allowlists (required before enabling live fixing).
- E10-T3 rotation runbook for app keys (mechanism exists: rotate key +
  `WAKEY_MASTER_KEY` re-encrypt on next rewrite).
- E10-T6 external review pass — this self-review does not replace it.
