# SPDX-License-Identifier: Apache-2.0
# Security Review — M3 sign-off checklist

Founder-directed production-grade bar (engineering-standards §6). Each item
is verified by an automated test (path in brackets) unless noted.

## Verified controls

- [x] Redaction: 18 secret families caught (AWS/GCP/GitHub/Stripe/PyPI/npm/
      SendGrid/JWT/bearer/private-key/url-creds/email/Luhn-guarded cards)
      — `tests/security/test_redaction.py` (SEC-1)
- [x] Fail-closed redaction: engine error destroys field, never leaks
      — `test_redaction.py::test_fail_closed_on_engine_error` (SEC-1)
- [x] ReDoS bound: custom patterns length-capped + compile-validated at
      config load (SEC-1, WF-01 §6)
- [x] Webhook auth: service-key 401, HMAC constant-time compare, signature
      bound to body — `tests/security/test_webhook_auth.py` (ING-11)
- [x] Prompt-injection: logs-as-data fences, canary leakage check, per-stage
      tool allowlists — `tests/security/` + WF-09 §4 (SEC-2)
- [x] Repo allowlist: adapter refuses non-allowlisted repos (founder
      directive, SKILL.md §8) — enforced in `GitHubConfig` + demo/live script
- [x] Credential storage: keys in gitignored `.env` only; `git grep` proves
      zero tracked-file leakage; attribution in THIRD-PARTY-NOTICES.md
- [x] Autonomy: no merge code path exists (WF-06/WF-13/WF-14 invariants);
      digest approvals stay in GitHub
- [x] Caps & budgets: tickets/hour, PR caps, LLM spend (SEC-7) with
      graceful degradation to issues-only (NFR-4)

## Open items (blocking M3 sign-off)

- [ ] External/third-party review pass (E10-T6) — self-review done, external
      review recommended before v1.0.0
- [ ] Penetration probe of the dashboard auth surface (first-run token,
      session handling) — scheduled with GUI completion
- [ ] Supply-chain: pip-audit gate in CI (planned with E1-T2 completion on
      a public remote)

## Posture summary

No known highs. Controls are test-enforced, not policy-enforced. The two
live credentials (GitHub token, GLM key) live in `.env` only; both should
be rotated after the test phase (both transited chat).
