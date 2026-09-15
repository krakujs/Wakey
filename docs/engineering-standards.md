# Wakey — Engineering Standards ("production grade" defined)

> The bar every change must clear before it's called done. AI agents and humans are held to the same standard. SKILL.md §1.5 makes this binding.

## 1. Definition of Done (every task)

A task is **done** when **all** of the following hold:

1. **Behavior matches its spec** — the workflow file(s) in `docs/workflows/` describing it, including every listed failure mode.
2. **Every acceptance criterion has an automated test** asserting it (or, where genuinely hardware/credential-bound, a documented manual check recorded in the task).
3. **Tests pass in CI** on a clean checkout: unit + contract + relevant e2e. No skipped/flaky tests.
4. **Observability shipped**: structured logs at decision points, metrics for the workflow's key counters/latencies (see §5), audit-log line for any autonomous action.
5. **Security reviewed against §6** — especially anywhere logs, secrets, or LLM prompts are involved.
6. **Docs updated in the same change**: workflow spec, feature inventory (if scope moved), and user-facing docs (`README`, examples) if behavior is visible.
7. **Configuration externalized**: all thresholds/limits/tunables in `wakey.yml`/env with sane defaults — no magic numbers.
8. **Degraded mode defined**: what happens when this component's dependencies fail (timeout → retry → degraded → drop), consistent with NFR-4.

## 2. Code standards

- **Python 3.12+**, fully type-annotated (`mypy --strict` clean for `src/wakey/`), `ruff` clean (lint + format).
- **Async-first**: no blocking I/O in async paths; CPU-bound work (fingerprinting) in workers via queue, not the request path.
- **Structure**: `src/wakey/` monorepo — `ingest/`, `fingerprints/`, `policy/`, `tickets/`, `agents/`, `github/`, `security/`, `ops/`. One responsibility per module; module docstring states the *why*.
- **Errors**: typed exceptions per domain; never bare `except:`; external calls always with explicit timeout + bounded retries (idempotent where possible).
- **Dependencies**: pinned, minimal; adding one requires justification in the PR body (what it does, why stdlib/existing deps don't, license check, last-commit recency).
- **No secrets in code/tests/logs, ever.** Fixtures use synthetic generators from `tests/fixtures/`.

## 3. Testing strategy (test pyramid, enforced by CI)

| Layer | What | Where | Gate |
|---|---|---|---|
| **Unit** | Fingerprinting, redaction, policy, classification logic, config — pure logic, fast (<10ms each) | `tests/unit/` | every PR |
| **Contract** | Adapters & clients against recorded/simulated payloads: GCP Pub/Sub envelope, GitHub REST (recorded fixtures), webhook HMAC/OIDC verification | `tests/contract/` | every PR |
| **GitHub live sandbox** | Real end-to-end against a dedicated test org + GitHub App (install → ticket → RCA → draft PR → commands → merge webhook → verdict); rate-limit/secondary-limit aware; failure drills (revoked token, narrowed perms, deleted objects, replay storms) | `tests/e2e_github/` | nightly + pre-release (G-gate, execution-plan §2) |
| **E2E (gates)** | The four gate demos (SKILL.md §5) running the real pipeline with a fake GitHub and synthetic log streams; real-infra runs recorded manually in STATE.md at gate time | `tests/e2e/` | every PR (fake GitHub), at gates (real) |
| **Security** | Redaction fuzz corpus (must catch: AWS/GCP keys, JWTs, cards, emails, private-key blocks), prompt-injection corpus (log payloads attempting instruction injection must not alter agent tool use) | `tests/security/` | every PR |
| **Eval bench** | Agent quality: fixture repos with seeded bugs; measures repro-first success, patch-green rate, noise rate (PRs opened for non-bugs) | `eval/` | nightly + release; results published (ANA-4, EXT-5) |

**Performance tests**: ingest throughput and error→fingerprint latency budgets (NFR-1/2) as CI benchmarks with fail-on-regression (>20% worse fails).

## 4. CI/CD pipeline (GitHub Actions)

PR pipeline: `lint → typecheck → unit → contract → security → e2e(fake) → build image`. 
Main: + nightly eval bench + nightly **GitHub live sandbox suite** (gated by CI secret; PRs use fixtures only so rate limits are never spent on ordinary merges) + performance benchmarks. 
Releases: tagged semver, changelog auto-generated (Conventional Commits), images published with SBOM + provenance attestation, migration tested from previous tag — and **no release tags without a G-gate certification ≤14 days old** (execution-plan §2, §5).

## 5. Observability requirements

- **Logs**: JSON, level, `request_id`/`event_id` correlation from ingest through every downstream action; no PII (post-redaction fields only).
- **Metrics** (Prometheus, one namespace `wakey_*`): per-workflow counters + histograms — ingest accepted/rejected, fingerprint new/dup rates, time-to-ticket, time-to-RCA, time-to-PR, agent runs/tokens/cost by model tier, GitHub API failures, budget-exhaustion events.
- **Audit log**: append-only JSONL, one line per autonomous action (actor=agent|system, action, inputs hash, model, cost, decision, cap-checks). Export API in P2 (SEC-5).
- **Health**: `/healthz` (liveness), `/readyz` (deps: DB, GitHub reachable, LLM reachable), `/metrics`.

## 6. Security checklist (apply at PR time)

1. Any new input path → is it validated, size-limited, and authenticated (service key, HMAC, or OIDC)?
2. Any new persistence of log-derived data → was it redacted first (SEC-1)? Are redaction patterns tested against the fuzz corpus?
3. Any new prompt construction → is log content quoted as data? Tool allowlist minimal for the stage? Scope (paths/branches) enforced?
4. Any new autonomous action → capped? audited? degraded-mode defined?
5. Any new credential handling → encrypted at rest, never logged, rotation documented?
6. Any regex/redaction change → run the full security corpus; a regression here blocks merge regardless of anything else.

## 7. Release & versioning

- Semver. `0.x` until M3 launch → `1.0.0`.
- **Release checklist** (all required): G-gate certified live ≤14 days old · all M-gates evidenced · security review closed with no open highs · eval-bench numbers published · fresh-machine install drill done · docs current (README quickstart, WF specs, CHANGELOG, LICENSE).
- Every behavior change ships with: changelog entry (auto from commits), migration notes if state schema changed, and an updated `wakey.yml` schema version with a documented upgrade path.
- State migrations: forward-only, tested from the previous release tag; rollback = restore backup + previous image (documented).
- Support: latest minor gets fixes; breaking config changes only in majors, with deprecation warnings one minor ahead.

## 8. Documentation standards

- Workflow specs are **behavioral contracts**: numbered steps, inputs/outputs, state writes, failure-mode tables, acceptance criteria. If code and spec disagree, either fix code or fix spec in the same PR — divergence is a bug.
- Every user-facing feature has: what it does, how to configure it, what it costs (LLM/compute), how to turn it off.
- Examples directory maintained as living documentation: a working demo service (intentionally buggy) + GCP setup script + expected tickets — used by `wakey doctor` and the eval bench.
