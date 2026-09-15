# WF-09 — Security, Redaction & Trust Controls

> Behavioral contract for the security layer that every other workflow calls. Features: SEC-1..8, FIX-6/10 · Tasks: E10-*. This workflow is a **cross-cutting invariant holder** — eng-standards §6 checklist applies to every PR touching it.

## Purpose & trigger

Guarantee the four trust invariants mechanically: redact-before-store/prompt, logs-are-data, capped+audited autonomy, encrypted credentials. Active on every ingest, every prompt build, every autonomous action, every credential touch.

## Steps / controls

1. **Redaction engine** (`SEC-1`):
   - Builtin pattern families (tested against `tests/security/corpus`): cloud keys (AWS `AKIA…`/secret, GCP/Google OAuth `ya29.`, `AIza…`), tokens (`ghp_`, `gho_`, `github_pat_`, `xoxb-`, `sk-`, Bearer), JWTs (three-segment base64url), URLs with credentials (`scheme://user:pass@`), emails, credit cards (**Luhn-validated** to avoid false positives), private-key PEM blocks, connection strings w/ passwords.
   - Custom patterns: per-service `redact:` regexes, validated at config load (ReDoS guard: linear-time engine or regex analyzer rejects catastrophic backtracking shapes at parse time).
   - Two-pass guarantee: pass 1 at ingest (before storage); pass 2 at **prompt-build time** over final assembled prompt content. Fail-closed: if the engine errors on a field, the field is replaced by `[REDACTION_FAILURE]`, never raw.
   - Every hit: counter by family; hashes of hit patterns (not content) in audit samples.
2. **Logs-are-data enforcement** (`SEC-2`):
   - All log-derived text enters prompts inside explicit fences with per-conversation **canary tokens**; system rules state content inside fences is data.
   - Post-generation check (every agent response): did canary tokens or fence content leak into tool-call arguments? → block + `needs-human(security)` (used by WF-05 step 9, WF-06 step 8 verifier).
   - Tool allowlists per stage: RCA = {read_file, search, list_dir, git_log, git_diff}; Fix = RCA + {write_file(scoped), run_tests(sandboxed)}; nothing else exists. Path scope (service path) enforced inside `write_file` mechanically.
   - No shell execution anywhere in agent tooling; test runner is a fixed command from `wakey.yml` parsed at config level (not model-supplied).
3. **Credential management** (`SEC-4`): envelope encryption at rest (key from `WAKEY_MASTER_KEY` env or KMS-ref); GitHub keys, service keys, PATs, LLM keys encrypted; env-reference indirection supported (`${ENV_VAR}`) so secrets can stay in the orchestrator's env; dashboard never displays secrets post-save (rotate-only); rotation runbook documented; memory hygiene: credentials zeroized where runtime allows (best-effort, documented).
4. **Caps & budgets** (`SEC-7`): centrally enforced in policy layer: tickets/hour+day per service, concurrent investigations per service, global agent concurrency, open PRs per service, monthly LLM spend (per provider pricing table), per-investigation token ceiling. Each cap: checked **before** the action, refusal reason surfaced on the ticket, metric `wakey_cap_blocks_total{cap=…}`. Exhaustion → degraded mode (NFR-4): ingest+fingerprints+tickets continue; RCA/fix pause; dashboard banner; auto-resume at budget reset.
5. **Audit log** (`SEC-5`): append-only JSONL; hash-chained (each line includes prev-hash) so tampering is detectable; content: timestamp, actor (system|agent|user:<login>), action, subject (fingerprint/ticket/PR), model+tokens+cost where applicable, cap-check results, canary/verifier verdicts. Never: raw log content, secrets, prompt bodies (hashes only). Export API (P2) + daily integrity self-check.
6. **Transport & surface hardening**: webhook HMAC comparison constant-time; OIDC audience/issuer checked; ingest keys hashed (shown once); dashboard sessions hardened; admin actions require re-auth in P2 RBAC; security headers set; dependency audit (pip-audit) in CI blocking.
7. **Data lifecycle** (`SEC-8`, P2): retention windows per store (events default 14d, dead-letters 14d, audit kept); purge tooling for GDPR-style deletion of a service's data; backups covered by retention policy.

## Failure modes

| Failure | Expected behavior |
|---|---|
| Redaction engine exception mid-ingest | Fail-closed: field → `[REDACTION_FAILURE]`; never raw (WF-02 step 6) |
| Custom regex catastrophic backtracking | Rejected at config load (guard); service keeps last-good config |
| Master key unavailable at boot | Refuse to start (credentials unreadable) — no plaintext fallback |
| Canary detected in tool args | Abort current agent action, `needs-human(security)`, notify, audit, metric |
| Cap check races under concurrency | Caps enforced transactionally (reserve-then-act); over-cap refuses with reason |
| Audit chain integrity check fails at daily check | Loud alarm (notify + metric + dashboard banner); treated as security incident |
| LLM provider returns prompt-content in error messages | Provider errors logged redacted (pattern pass over error bodies) |

## Acceptance criteria

1. Corpus: all ≥15 builtin families caught; Luhn rejects 16-digit non-card numbers (security suite, merge-blocking).
2. Fail-closed: injected engine failure → `[REDACTION_FAILURE]`, no raw leak (unit).
3. ReDoS guard: known-evil regex rejected at load with actionable error (unit).
4. Canary: adversarial corpus → zero tool-arg leakage across RCA+Fix (security e2e, merge-blocking).
5. Allowlist: every tool outside stage allowlist blocked+audited (property test over tool enum).
6. Caps: concurrent-reservation test — N parallel dispatches with cap k → exactly k proceed (integration).
7. Audit chain: bit-flip in stored log detected at verification (unit).
8. Storage scan: post-run dump of DB contains no corpus secrets and no plaintext credentials (e2e).
