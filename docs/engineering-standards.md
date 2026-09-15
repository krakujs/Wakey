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
- **Structure (canonical — this tree is authoritative; proposing a change means editing this section first):**

```
Wakey-ai/
├── SKILL.md · README.md · LICENSE · THIRD-PARTY-NOTICES.md · docker-compose.yml
├── src/wakey/
│   ├── core/          # config, domain models, storage, queue, server bootstrap
│   ├── ingest/        # ingest API, normalizers, adapters (gcp/, webhook/, docker/)
│   ├── fingerprints/  # traceback parsers, fingerprint engine, stores
│   ├── policy/        # thresholds, suppression, autonomy, budgets, work-state machine
│   ├── tickets/       # ticket lifecycle, templates, labels
│   ├── forge/         # ForgePort + adapters: github/, gitlab/ (each self-contained)
│   ├── agents/        # rca/, fix/, model router, tool allowlists, verifier
│   ├── security/      # redaction, credential store, audit log, injection defenses
│   ├── notify/        # notification policies & targets
│   ├── cli/           # wakey CLI
│   └── web/           # dashboard backend + templates/static assets
├── tests/             # unit/ contract/ e2e/ e2e_github/ security/ (mirrors src layout)
├── eval/              # eval bench: fixture repos, seeded bugs, harness
├── demo/              # demo fixture services + emit scripts (two-service demo)
├── docs/              # numbered planning docs (01-…) + workflows/ (WF-…) + STATE/TASKS
└── .github/workflows/ # CI
```

- **Craft discipline (senior-engineer bar):** one responsibility per module; modules ≈≤400 lines, functions ≈≤50 (guideline — exceed only with a written reason); names are explicit and pronounceable, no abbreviations unless domain-standard; no commented-out code, no dead code, no TODO without a task id, no dangling stubs — finish or remove; prefer a boring explicit solution to a clever abstraction (abstractions earn their place on third use); imports ordered and minimal; every module's docstring states its *why*.
- **Documentation structure:** README is the index; planning docs are numbered by creation (`docs/01-…`), workflow specs are `WF-xx-slug.md`, and every doc is reachable from the README/SKILL doc map — no orphan files, no duplicate sources of truth (one topic, one home; cross-link, don't copy).
- **Errors**: typed exceptions per domain; never bare `except:`; external calls always with explicit timeout + bounded retries (idempotent where possible).
- **Dependencies**: pinned, minimal; adding one requires justification in the PR body (what it does, why stdlib/existing deps don't, license check, last-commit recency).
- **No secrets in code/tests/logs, ever.** Fixtures use synthetic generators from `tests/fixtures/`.
- **License hygiene**: every source file carries `SPDX-License-Identifier: Apache-2.0` (header tooling wired in E1-T1); new dependencies pass a license-compatibility check (Apache-2.0-compatible only — GPL/AGPL/SSPL/unlicensed rejected); `THIRD-PARTY-NOTICES.md` is regenerated from installed metadata at every release (see distribution doc).

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

### Continuous quality loop (development never stops, bugs never accumulate)

- **Every commit** in a lane worktree runs `make check` (lint → typecheck → unit → affected contract) before it can be marked `Status: review`.
- **Every wave merge** runs the full suite on `main` (execution-plan §4.5) — `main` is always releasable-shaped.
- **Nightly**: resource benchmark (NFR-6), eval bench, GitHub/forge live sandbox suite, mutation testing on critical modules (`fingerprints/`, `policy/`, `security/redaction`) — mutation score reported; score drops block the owning lane's next merge.
- **Coverage floors** (CI-enforced): `src/wakey/` overall ≥90% lines; critical modules (`fingerprints/`, `policy/`, `security/`) ≥95%; new code cannot lower coverage (diff-cover).
- **Zero-known-bugs policy**: a bug is any behavior deviating from a WF spec or AC. Rules: (1) every bug fix ships with the regression test that reproduces it — no test, no fix; (2) the bug reopens its task (status → `in-progress`); (3) **no phase/gate exits with an open bug** — the bug board lives in STATE.md; (4) flaky tests are bugs: quarantined *with a ticket* and fixed within the same wave, never permanently skipped.

### Safe local testing — host guardrails ("must never crash the PC")

Every test, benchmark, and load simulation runs **resource-capped by default**; nobody should ever fear running `make test`:

1. **One entrypoint, safe defaults**: `make test` = unit + contract + e2e(fake) with caps; `make bench` = resource benchmarks; `make gate-<m0|m1|m2|m3>` = phase gate scripts; `make check` = the pre-commit trio. Nothing required beyond make + docker.
2. **Hard caps everywhere**: every pytest run under `pytest-timeout` (per-test) and capped parallelism (`pytest-xdist -n ≤4`); load generators are **rate-limited by design** (token bucket — the 1k events/s and 5M events/day profiles are simulated schedules, not fork bombs); streaming fixtures, never load-everything lists.
3. **Benchmarks & sandbox runs in disposable containers** with `--memory` / `--cpus` limits (e.g. benchmark container capped at 2GB/2 CPUs regardless of host size) — a runaway benchmark dies at the cap, not on your RAM.
4. **The three never-rules** (apply to any test/bench code review): never fork/spawn without a cap; never allocate unbounded (all queues/buffers sized); never run metal load tests without container caps — load is always *simulated* against the pipeline, never generated by exhausting the host.
5. **Documented cost per suite** (README testing table): worst-case RAM/time per suite on a modest laptop (8GB/4-core reference) — if a suite can't state its cost, it doesn't merge.
6. **Local-first**: all suites — including forge live-sandbox (needs only tokens) and resource benchmarks — run on a developer machine; no paid CI service is ever required to verify a phase. Gate scripts encode each M-gate demo (`make gate-m0` …) so *every phase is tested locally* per the founder requirement.

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
- **Release checklist** (all required): G-gate certified live ≤14 days old · all M-gates evidenced · security review closed with no open highs · eval-bench numbers published · fresh-machine install drill done · **README quickstart boots green (compose drift test)** · docs current (README quickstart, WF specs, CHANGELOG, LICENSE).
- Every behavior change ships with: changelog entry (auto from commits), migration notes if state schema changed, and an updated `wakey.yml` schema version with a documented upgrade path.
- State migrations: forward-only, tested from the previous release tag; rollback = restore backup + previous image (documented).
- Support: latest minor gets fixes; breaking config changes only in majors, with deprecation warnings one minor ahead.

## 8. Resource discipline (founder requirement: least possible resources)

Wakey is a long-running watcher on someone's box — it must be a *good tenant*. Budgets live in NFR-6..8 (feature inventory §14) and are **CI-enforced**: a nightly resource benchmark (synthetic profiles: idle, lean, and 5M events/day steady-state) fails on >20% regression in RSS, CPU-seconds, disk growth, or boot time.

Rules that deliver it:
- **Event-driven, not polling**: zero busy loops; idle CPU ≈ 0 (timers only). Ingest is push-based; scheduled jobs coalesce.
- **Aggregate, don't retain**: the fingerprint store keeps counters/windows, not raw rows; raw redacted events only within retention; a compaction job merges old windows.
- **Lazy everything**: heavy imports (LLM clients, cloud SDKs, parsers) load on first use; boot imports have a budget (boot-to-ready <5s, NFR-6).
- **Lean dependencies**: every dependency justifies install size *and* import cost; prefer stdlib; orjson in hot paths; no heavyweight frontend framework — server-rendered HTML + vanilla JS (NFR-9).
- **Batch by default**: ingest writes, GitHub calls, and notification sends batch/coalesce; per-item work only when counts are small.
- **LLM frugality**: the biggest running cost is AI — dedup-before-LLM (docs/04 C2), cheap-tier routing (RCA-7), response caching per fingerprint version, hard token ceilings per investigation.
- **Lean mode** (NFR-8) trades breadth for footprint on small hosts; all profiles run in the nightly benchmark.
- Fix-agent sandbox runs are on-demand only, capped, and deferred via queue when the host is under load.

## 9. Documentation standards

- Workflow specs are **behavioral contracts**: numbered steps, inputs/outputs, state writes, failure-mode tables, acceptance criteria. If code and spec disagree, either fix code or fix spec in the same PR — divergence is a bug.
- Every user-facing feature has: what it does, how to configure it, what it costs (LLM/compute), how to turn it off.
- Examples directory maintained as living documentation: a working demo service (intentionally buggy) + GCP setup script + expected tickets — used by `wakey doctor` and the eval bench.
