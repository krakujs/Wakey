# SPDX-License-Identifier: Apache-2.0
# Security Review — M3 sign-off checklist

Status corrected 2026-09-16 per development review finding R-10: the earlier
revision of this checklist marked controls as verified that had no
implementation or test evidence. This revision states only what is
test-enforced today; everything else is an explicit open item. No unchecked
control may be described as verified anywhere; each claimed invariant must
link to a meaningful negative test.

## Verified controls (implementation + negative test on disk)

- [x] Redaction: 18 secret families caught (AWS/GCP/GitHub/Stripe/PyPI/npm/
      SendGrid/JWT/bearer/private-key/url-creds/email/Luhn-guarded cards)
      — `tests/security/test_redaction.py` (SEC-1)
- [x] Fail-closed redaction: engine error destroys field, never leaks
      — `test_redaction.py::test_fail_closed_on_engine_error` (SEC-1)
- [x] Webhook auth: service-key 401, HMAC constant-time compare, signature
      bound to body — `tests/security/test_webhook_auth.py` (ING-11)
- [x] Repo allowlist: `GitHubConfig` refuses construction for non-allowlisted
      repos (founder directive, SKILL.md §8) — note: the allowlist is
      *optional at construction*; making it mandatory for live use is open
      (E10 hardening)
- [x] Autonomy: no merge code path exists (WF-06/WF-13/WF-14 invariants);
      digest approvals stay in GitHub
- [x] Runtime data excluded from version control (R-13, 2026-09-16):
      `wakey-data/` gitignored; local DB preserved
- [x] Dashboard auth (R-07 closure, 2026-09-16): first-run setup token
      single-use + 30-min expiry + banner/host-only issuance; admin
      sessions server-side, HttpOnly + SameSite=Lax cookies; operational
      pages/APIs session-gated — `tests/unit/test_auth.py`,
      `tests/unit/test_web_board.py`
- [x] Verify-only secrets hashed at rest (2026-09-16): setup tokens and
      session ids stored as SHA-256 — a DB dump cannot be replayed to
      authenticate (SEC-4 slice)
- [x] Audit hash chain (E10-T4, 2026-09-16): every audit row chains
      `prev_hash`/`entry_hash`; tamper test proves a modified row breaks
      verification — `test_storage.py::test_audit_chain_detects_tampering`
- [x] Encrypted credential envelope (E10-T3 slice, 2026-09-16):
      AES-256-GCM SecretBox under `WAKEY_MASTER_KEY`; GitHub App callback
      refuses plaintext private-key storage (fail closed) —
      `tests/unit/test_secretbox.py`, `tests/unit/test_github_app.py`
- [x] GCP push OIDC claims (E4-T4 slice, 2026-09-16): issuer/audience/
      expiry/service-account enforced before acceptance —
      `tests/unit/test_oidc.py`, `tests/unit/test_gcp_push.py`;
      signature path (Google JWKS) remains live-gated
- [x] Fix-executor sandbox container gate (E10-T2, 2026-09-16):
      `make gate-sandbox` — network-less, memory/CPU/pids-capped container
      runs the negative suite (escape/egress/limits/cleanup), 8/8

## Previously claimed, NOT verified — downgraded/remaining (R-10 correction)

- [ ] **Prompt-injection controls** (SEC-2): logs-as-data fences, canary
      leakage checks, per-stage tool allowlists — *no implementation or
      tests exist*. Earlier revision falsely marked this verified.
- [x→partial] **Caps & budgets** (SEC-7): RCA hourly budget (exhausted
      budget leaves work queued), per-service ticket/hour cap, PR cap in
      the FIX-1 gate, comment throttling — implemented with tests;
      LLM-spend accounting and banner surfaces remain.
- [ ] **ReDoS bound** (SEC-1): pattern length cap + compile validation limit
      the *configuration* surface only; they do **not** bound regex matching
      time. A real bounded-matching control (timeout/match caps) is open.
- [ ] **Credential storage at rest**: `WAKEY_MASTER_KEY` is accepted but
      unused; service webhook secrets are plaintext SQLite values; `.env`
      gitignore is not encryption. Encryption at rest is open (E10).
- [ ] **Dashboard authentication** (R-07): `/board`, `/api/settings` and all
      operational APIs are unauthenticated; development exposure is
      loopback-only by default (CLI `--host` and compose publish bind
      127.0.0.1) as interim containment. First-run token + admin session
      (WF-01/WF-12) is open.

## Open items (blocking M3 sign-off)

- [ ] External/third-party review pass (E10-T6) — self-review done, external
      review recommended before v1.0.0
- [ ] Penetration probe of the dashboard auth surface (first-run token,
      session handling) — scheduled with GUI completion
- [ ] Supply-chain: pip-audit gate in CI (planned with E1-T2 completion on
      a public remote)
- [ ] Fix-execution sandbox negative tests (R-03): traversal/symlink escape,
      secret-environment access, network egress, resource limits, child
      cleanup — run in capped disposable containers

## Posture summary

Known highs are tracked as findings R-01..R-14 in
[development-review-2026-09-16.md](development-review-2026-09-16.md) with
remediation work packages A–F. The two live credentials (GitHub token, GLM
key) live in `.env` only; both should be rotated after the test phase (both
transited chat).
