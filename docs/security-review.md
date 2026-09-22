# SPDX-License-Identifier: Apache-2.0
# Security Review — M3 sign-off checklist

Status corrected 2026-09-16 per development review finding R-10: the earlier
revision of this checklist marked controls as verified that had no
implementation or test evidence. This revision states only what is
test-enforced today; everything else is an explicit open item. No unchecked
control may be described as verified anywhere; each claimed invariant must
link to a meaningful negative test.

## Verified controls (implementation + negative test on disk)

- [x] Managed connector PostgreSQL persistence and cross-replica admission/relay
      serialization: real database tests in `tests/contract/test_connect_postgres.py`.
      The Cloud Run operator refuses ephemeral SQLite; staging readiness verifies
      live database access. See `docs/recipes/wakey-connect-gcp.md` for evidence.
- [x] Old cryptography constraint remediated after an image audit; `50.0.1` pinned,
      full suite rerun and patched image audit passed. CI now audits Python
      dependencies in the built image. This does not replace OS image scanning or
      independent review of Wakey code. Exposed Google key rotation remains open.

- [x] Wakey Connect local regression checks (2026-09-17): device pairing,
      verified repository selection, scoped token minting, credential rejection,
      cross-tenant relay isolation, persist-before-ack delivery, signed revocation,
      encrypted connection storage, polling/admission limits and local form CSRF.
      Evidence: `tests/unit/test_connect.py`, `tests/contract/test_connect_github.py`.
      This is simulated-provider evidence, not hosted security certification.
      [Launch checklist and trust boundaries](recipes/wakey-connect.md) remain open,
      including ingress protection, independent review and live multi-account tests.

- [x] Live runner isolation (2026-09-17): `FixExecutor` uses per-attempt Docker,
      not host subprocesses; no host pip bootstrap, read-only checkout and root,
      non-root user, no network, bounded tmpfs/resources/output, timeout cleanup.
      `WAKEY_DOCKER_TESTS=1 pytest tests/unit/test_sandbox_runner.py` directly
      exercises the live runner. Deployment integration is still open; the
      existing Cloud Run service cannot use this local Docker runner as-is.
- [x] Service subdirectory containment and repro-alias tampering rejection:
      `tests/unit/test_fix_executor.py`, `tests/unit/test_fix.py`.

- [x] Redaction: 18 secret families caught (AWS/GCP/GitHub/Stripe/PyPI/npm/
      SendGrid/JWT/bearer/private-key/url-creds/email/Luhn-guarded cards)
      — `tests/security/test_redaction.py` (SEC-1)
- [x] Fail-closed redaction: engine error destroys field, never leaks
      — `test_redaction.py::test_fail_closed_on_engine_error` (SEC-1)
- [x] Webhook auth (updated 2026-09-17): service-key 401, HMAC
      constant-time compare, signature bound to body, native GitHub
      `X-Hub-Signature-256` accepted against the connected App's stored
      webhook secret or `WAKEY_WEBHOOK_SECRET`, `X-GitHub-Delivery`
      dedup, fail-closed with no secret —
      `tests/security/test_webhook_auth.py`,
      `tests/unit/test_reliability_regressions.py` (ING-11)
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

- [x→partial] **Prompt-injection controls** (SEC-2): fenced prompts and the
      adversarial corpus exist (`tests/unit/test_injection_corpus.py`). These
      do not prove semantic patch safety; independent spot-check verification
      and complete per-stage tool/canary coverage remain open.
- [x→partial] **Caps & budgets** (SEC-7): RCA hourly budget (exhausted
      budget leaves work queued), per-service ticket/hour cap, PR cap in
      the FIX-1 gate, comment throttling — implemented with tests;
      LLM-spend accounting and banner surfaces remain.
- [ ] **ReDoS bound** (SEC-1): pattern length cap + compile validation limit
      the *configuration* surface only; they do **not** bound regex matching
      time. A real bounded-matching control (timeout/match caps) is open.
- [ ] **Credential storage at rest** (narrowed 2026-09-17): with
      `WAKEY_MASTER_KEY` configured, SecretBox (AES-256-GCM) now encrypts
      service webhook secrets and the GitHub App private key/webhook
      secret at rest; without it the App callback still refuses plaintext
      key storage (fail-closed) but service webhook secrets stay
      plaintext. Remaining open: enforcing master-key configuration in
      production boot, and `.env` remains plain file secrecy, not
      encryption.
- [x→resolved] **Dashboard authentication** (R-07): superseded by the
      verified entry above — setup token (single-use, 30-min) + server-side
      admin sessions gate the operational pages/APIs (2026-09-16 closure).
      The residual open slice is the penetration probe of that surface
      (listed under Open items).

## Open items (blocking M3 sign-off)

- [ ] External/third-party review pass (E10-T6) — self-review done, external
      review recommended before v1.0.0
- [ ] Penetration probe of the dashboard auth surface (first-run token,
      session handling) — scheduled with GUI completion
- [ ] Supply-chain: pip-audit gate in CI (planned with E1-T2 completion on
      a public remote)
- [x] Fix-execution sandbox negative tests (R-03): traversal/symlink escape,
      secret-environment access, network egress, resource limits and child
      cleanup run in capped disposable containers. The 2026-09-22 follow-up
      also exercised the executor image through the restricted Docker proxy,
      with workspace streaming into tmpfs and no shared host path.

## Posture summary

Known highs are tracked as findings R-01..R-14 in
[development-review-2026-09-16.md](development-review-2026-09-16.md) with
remediation work packages A–F. The two live credentials (GitHub token, GLM
key) live in `.env` only; both should be rotated after the test phase (both
transited chat).
