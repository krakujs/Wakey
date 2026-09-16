# Wakey — Task Backlog (all tasks to build the product)

> Authoritative work queue. Statuses: `todo / in-progress / review / done / blocked:<reason>`. Update statuses here as work progresses; epic roll-ups live in `docs/STATE.md`.
> **Parallel execution:** tasks are scheduled in lanes & waves per `docs/execution-plan.md` §3. In parallel runs, agents do **not** edit this file — progress is tracked via commit trailers (`Task:` / `Lane:` / `Status:`) and the integrator updates statuses at wave merges.
> Every task lists: **Features** (IDs from `docs/05-feature-inventory.md`), **Spec** (workflow file in `docs/workflows/` to read first — § numbers are indicative pointers, the workflow file is the contract), **AC** (acceptance criteria — each needs a test), **Deps**, **Size** (S ≤ half day, M ≤ 2 days, L ≤ a week of focused work).

## Phase P1 — MVP ("the loop")

### E1 — Foundation & repo hygiene

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E1-T1 | Repo scaffold: `src/wakey` layout, pyproject, ruff+mypy strict, pytest config, Apache-2.0 LICENSE, CONTRIBUTING, SECURITY.md, `.env.example` | — | eng-standards §2 | CI runs lint+typecheck+empty test suite green on PR; `pip install -e .[dev]` works on clean machine | — | S | review (all local verification green; "CI runs on PR" unverified until a remote exists — see E1-T2) |
| E1-T2 | CI pipeline: PR gates (lint→type→unit→contract→security→e2e-fake→image build), nightly slot reserved for eval bench | — | eng-standards §4 | PR without green CI cannot merge; pipeline <10min; image builds with SBOM | E1-T1 | M | in-progress (workflow committed; coverage floor ≥90% enforced in the test job (R-11); nightly/eval/SBOM slots land with later tasks; execution pending remote) |
| E1-T3 | Docker packaging: Dockerfile (non-root, slim), docker-compose dev + prod profiles, healthchecks | ONB-1 | WF-11 | `docker compose up` starts wakeyd+DB, `/healthz` green, container runs as non-root | E1-T2 | S | **done** (verified live: compose up → /healthz 200, /readyz ready, /metrics; non-root; 149MB; SQLite embedded, Postgres profile → OPS-5) |
| E1-T4 | Conventional commits + changelog automation + release workflow (tag → image publish to ghcr.io **and** Docker Hub, `X.Y.Z`/`X.Y`/`1`/`sha-*`, multi-arch amd64+arm64, SBOM+provenance); README-quickstart drift test (repo compose ≡ README block) | ONB-1, NFR-6 | eng-standards §7, docs/distribution.md | Tagging `v0.1.0` publishes both registries with attestations; CI boots the README quickstart compose green; image ≤200MB, non-root, multi-arch manifest | E1-T2 | M | todo |
| E1-T5 | Demo fixture service: intentionally buggy small API + log generators (JSON and plaintext modes) + synthetic secret-bearing log generator for security tests | EXT-5 | eng-standards §8 | Generators produce deterministic fixtures; security corpus includes ≥15 secret patterns that redaction must catch | E1-T1 | M | todo |
| E1-T6 | **Safe test runner & local gate scripts**: `make check / test / bench / gate-m0..m3`; pytest-timeout + capped xdist (≤4 workers); containerized benchmarks with memory/CPU caps; rate-limited load generators; per-suite cost table | — | eng-standards §3 (host guardrails) | `make test` completes on the 8GB/4-core reference laptop within documented caps; load generator verified rate-limited (no fork-burst); benchmark container dies at its memory cap, host unaffected; every gate script runs locally end-to-end | E1-T2 | M | in-progress (`make check`/`test` with timeout + ≤4 workers live; `bench`/`gate-*` scripts arrive with E2-T6 and the M-gates) |

### E2 — Core platform

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E2-T1 | Domain models: LogEvent, Service, Fingerprint, Occurrence, Ticket, FixPR, AuditEvent (pydantic, frozen where sensible) | — | — | Round-trip serialization; invalid models rejected with actionable errors | E1-T1 | M **done** (LogEvent/Fingerprint/Service/TraceFrame/AuditEvent + Severity/WorkState rank+busy; 7 tests) |
| E2-T2 | Config system: env + `wakey.yml` loader, JSON schema, validation errors with file/line, defaults documented | ONB-5, ONB-6 | WF-01 §6 | Invalid config → startup fails listing every problem; schema published for editors; unknown keys rejected | E1-T1 | M **done** (Settings.from_env + load_wakey_yaml, collected problems, redact regex validation, unknown keys rejected extra=forbid, unsupported DB backends rejected (R-12); JSON schema file → E12-T17; 12 tests) |
| E2-T3 | Storage interface + SQLite implementation + forward-only migrations | OPS-5 | WF-11 §5 | All state ops through interface; migration from schema v1→v3 tested; SQLite WAL mode; single-writer safety | E2-T1 | M **done** (migrations v1→v3: durable per-service-namespaced deliveries, dispatch journal, fingerprint lifecycle/throttle anchors, config_json; retention prune_events/prune_deliveries (R-14); 10 tests) |
| E2-T4 | HTTP server skeleton: FastAPI app, `/healthz`, `/readyz`, `/metrics` scaffolding, structured JSON logging with request IDs | OPS-3, OPS-4 | WF-11 | Request ID propagates into all log lines of a request; /readyz reports dependency status | E2-T2 | M **done** (create_app + request-id middleware + lean metrics registry + JSON logging + serve; /readyz reports configured/unconfigured state + ingest backlog + pending deliveries (R-01); lifespan-owned workers; 6 tests) |
| E2-T5 | Internal queue + worker pool: bounded queues, backpressure, graceful shutdown drains in-flight work | NFR-3, ING-11 | WF-02 §7 | Load test: 1k events/s burst → zero loss, ordered per fingerprint, clean shutdown drains | E2-T4 | L **done** (KeyPartitionQueue: crc32 partitions, per-key FIFO, QueueFull backpressure, join-based drain; 4 tests) |
| E2-T6 | **Resource budget harness**: nightly benchmark profiles (idle / lean / 5M-events steady-state) measuring RSS, CPU-seconds, disk growth, boot time — CI fails on >20% regression | NFR-6 | eng-standards §8 | Budgets asserted green at baseline; artificial +20% memory regression fails CI; report artifact published per run | E2-T5 | M | in-progress (bench rewritten per R-14: semantically distinct families, verified accepted/stored counts, statuses asserted; 5000 events @3289/s 59MB RSS; retention enforced boot+hourly; nightly/CI slot pending remote) |

### E3 — Onboarding & GitHub integration

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E3-T1 | GitHub App manifest flow: redirect→create→exchange; private key encrypted at rest (SEC-4) | ONB-3, SEC-4 | WF-01 §3 | Contract test: manifest→installation→token minting; key never persisted unencrypted (test greps storage) | E2-T4 | M | todo |
| E3-T2 | GitHub client: issues (create/comment/label/close/reopen), PRs (create draft), repos (clone URL), rate-limit aware, retry policy | TIK-*, FIX-5 | WF-04 | Contract tests vs recorded fixtures; respects rate limit; transient 5xx retried w/ backoff; dry-run mode emits planned calls | E3-T1 | M | in-progress (issues+comments+labels+close/reopen+draft PRs+git-data branch/commit publication (R-06), allowlist guard, typed errors, bounded retries; contract tests + simulator git API; dry-run mode and recorded-fixture CI artifacts remain) |
| E3-T3 | GitHub webhook receiver: HMAC signature verify, ping, issue/PR comment events → `@wakey` command bus (parsing only) | RCA-5 | WF-01 §4, WF-07 | Forged signature rejected 401 (tested); commands parse into typed objects | E3-T2 | M | **done (core)** (HMAC fail-closed webhook + delivery dedup + typed parse/dispatch; drop/reopen/explain/status live, fix/retry named refusals; deny-by-default via WAKEY_AUTHORIZED_USERS; tests in test_commands/test_webhooks) |
| E3-T4 | PAT fallback auth | ONB-4 | WF-01 §3.5 | Same client interface as App auth; flagged "non-preferred" in dashboard | E3-T2 | S | todo |
| E3-T5 | Setup wizard v1 + service registration: repo pick, service↔repo/path mapping, ingest key generation + rotation | ONB-2, ING-1 | WF-01 §5 | New service gets working ingest key in <2min; key rotation invalidates old within 60s | E2-T2, E3-T5 | L | in-progress (registration API + wizard page live: one-time key display, hash-only storage, immediate-rotation invalidation (tests in test_registration); GitHub App manifest flow + encrypted key storage (E3-T1/SEC-4) remain) |
| E3-T6 | Per-repo `wakey.yml` fetch/validate/cache (hot reload) | ONB-5, ONB-6, OPS-2 | WF-01 §6 | Config edit on GitHub reflected ≤30s without restart; invalid config keeps last-good with dashboard warning | E2-T2, E3-T2 | M | **done (core)** (30s refetch loop via ForgePort.fetch_file → wakey.yml validated → stored config drives policy; invalid file keeps last-good + metric; tests via simulator git contents; dashboard warning surface remains) |
| E3-T7 | `wakey doctor`: synthetic error through real pipeline → test ticket → auto-close; env report | ONB-7 | WF-01 §8 | Fresh install passes doctor; failures pinpoint the broken stage (ingest/perm/LLM) with fix hint | E3-T5, E4-T1 | M | in-progress (doctor runs the local pipeline + live LLM tier when configured and states explicitly what it does NOT test (real HTTP/live forge) (R-12); stage pinpointing + auto-close remain) |
| E3-T8 | **GitHub test harness & live sandbox suite** (the G-gate engine): recorded-fixture contract tests for every endpoint used; dedicated test org + App; live e2e (install→ticket→RCA→draft PR→commands→merge webhook→verdict); webhook forgery/replay/out-of-order tests; failure drills (revoked token, narrowed perms, deleted objects, rate-limit backoff); least-privilege assertion | — | execution-plan §2 | Contract suite runs on every PR; live suite runs nightly + on demand with `WAKEY_E2E_*` secrets and never trips secondary rate limits; every failure drill has an automated test; least-privilege test asserts client calls stay in declared perms | E3-T2, E3-T3 | L | todo |

### E4 — Ingestion & connectors

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E4-T1 | Ingest API: `POST /ingest/<key>`, key auth, per-key rate limits, payload size caps | ING-1, ING-11 | WF-02 §2 | Wrong key 401; oversized 413; rate-limited 429+Retry-After; all metrics counted | E2-T5 **done (core)** (HMAC signature verification + migration v2 landed; 429/queue wrap + rate limits remain; 1MB payload cap → 413; durable 202 persistence (R-02)) |
| E4-T2 | Normalizer framework + generic JSON/JSON-lines/logfmt parsing | DET-1, ING-3 | WF-02 §4 | Contract tests per format; unknown format → routed to dead-letter with reason, never dropped silently | E2-T1 | M **done** (JSON/logfmt/plain auto-detect, severity mapping, delivery-scoped ids, dead letters; 6 tests) |
| E4-T3 | Redaction hook in ingest path (uses SEC-1 engine) | SEC-1 | WF-09 §3 | No raw secret survives to storage: security corpus e2e asserts zero matches post-ingest | E4-T2 | S | **done** (two-pass: whole-payload redaction before persistence, multiline-capable, + per-field sanitize of ids/attribute keys-values (R-04); secrets-at-rest tests in test_ingest_api) |
| E4-T4 | GCP Cloud Logging adapter: Pub/Sub push docs+recipe, OIDC token verification | ING-2 | WF-02 §3 | Contract test: valid OIDC accepted, expired/wrong-audience rejected; recipe deploys on fresh GCP project (manual AC at gate) | E4-T1 | M | in-progress (envelope decoder + claims validation (issuer/audience/expiry/service-account) tested; JwtVerifier via Google JWKS with optional `cryptography`; live push acceptance + deploy recipe need founder GCP project) |
| E4-T5 | Docker/journald sidecar `wakey-agent`: tail, batch, reconnect, HMAC | ING-4 | WF-02 §3.3 | Container restart → resumes without duplicates (idempotency keys); survives 5min broker outage | E4-T1 | M | todo |
| E4-T6 | Deploy events ingestion: Cloud Run revision labels, GitHub deployment statuses | ING-8 | WF-02 §6 | Deploy recorded with commit SHA; unknown-shape deploy events dead-lettered | E4-T2 | M | todo |
| E4-T7 | Idempotency + replay-safe processing (event dedup by id) | ING-11, NFR-3 | WF-02 §7 | Duplicate delivery → processed exactly once (test: replay 2× → same single fingerprint count) | E2-T5 | M | **done** (deliveries namespaced per service; replay → deduplicated regardless of state; dispatch journal re-dispatches stored-but-undispatched events; outage/restart/shared-id tests in test_ingest_api) |
| E4-T8 | Trace/request-ID extraction into LogEvent | ING-13 | WF-02 §4 | Extracted IDs queryable in storage; redacted-safe | E4-T2 | S | **done** (extraction at normalize + redaction of ids/attribute keys at the sanitize pass (R-04); secrets-at-rest test) |
| E4-T9 | Dead-letter store + raw event retention policy (redacted) | ING-12 | WF-02 §8 | Retention window enforced; replay of dead-lettered batch reproduces same fingerprints | E4-T7 | M | in-progress (line-level dead letters with reasons+metrics; delivery dead-letter after attempt cap; retention prune at boot+hourly via WAKEY_RETENTION_DAYS; replay tool remains) |
| E4-T10 | Ingest efficiency pass: batched writes, orjson hot path, lazy heavy imports, counters-not-rows aggregation, window compaction job | NFR-6, NFR-7 | eng-standards §8 | 5M events/day steady profile inside budget (RSS ≤350MB, ≤25MB/day disk) on reference box; compaction verified to bound growth | E2-T6, E4-T9 | M | todo |

### E5 — Detection & fingerprinting

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E5-T1 | Traceback parsers: Python, Java, JS/TS, PHP (+ mixed-format logs) | DET-2 | WF-03 §3 | Corpus per language incl. old formats (Java 8, PHP 5, Django 1.x): ≥95% frame extraction on corpus; property test: parse→template stable under id/number churn | E4-T2 **done** (python/java/javascript/php; ruby/go/c# in P2) |
| E5-T2 | Fingerprint engine: message templating (ids/numbers/uuids/ips→slots) + top-frame hashing; fp hash + storage | DET-3 | WF-03 §4 | Same error w/ different ids → same fp (property test, 10k mutations); different errors → different fp ≥99% on corpus | E5-T1 | L **done** (templating + sha256 identity, env-scoped; 6 stability/scoping tests) |
| E5-T3 | Burst collapsing + occurrence counters | DET-4 | WF-03 §5 | 500 identical in 60s → 1 fingerprint, count=500; counter survives restart (DB-backed) | E5-T2 | M | todo |
| E5-T4 | Severity classifier: panic/OOM/unhandled/5xx-spike/dependency-down | DET-5 | WF-03 §6 | Labeled corpus ≥90% agreement; spikes detected over sliding window | E5-T2 | M | todo |
| E5-T5 | Policy gate: severity×rate thresholds, suppression list w/ expiry, maintenance windows (no-op stub ok in P1), autonomy levels, caps wiring | DET-6, DET-7, SEC-7 | WF-03 §7 | Unit tests per rule incl. boundary cases; suppressed fp w/ expired entry wakes again; observe mode → no agent calls (assert via fake LLM counter) | E5-T3, E5-T4 | M **done (core)** (severity floor — INFO never wakes (R-05); count thresholds; busy-absorb; suppression; observe resolved per WF-07 contract; burst rate windows wired via SlidingWindowCounters; 12 tests) |
| E5-T6 | Deploy correlation: first-bad/last-good from deploy events + fingerprint timeline | DET-9 | WF-03 §8 | Synthetic timeline: fp first-seen between deploys A,B → ticket names B, lists A as last-good | E4-T6, E5-T3 | M | todo |

### E6 — Ticketing

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E6-T1 | Ticket creation: template renderer (counts, timeline, redacted excerpt, deploy correlation, dashboard link), labels taxonomy | TIK-1, TIK-2 | WF-04 §2 | Golden-file test of rendered issue; label set asserted; ≥1 ticket/hour/service cap enforced | E3-T2, E5-T5, E5-T6 | M **done (core)** (template renderer + ConsoleForge sink verified in demo; per-service hourly ticket cap + QUEUED_RCA dispatch reservation (R-05); labels taxonomy with E3) |
| E6-T2 | Live occurrence updates (throttled comments) | TIK-3 | WF-04 §3 | Burst → exactly 1 update/hour despite 10k events; content includes ×count | E6-T1 | S | **done (core)** (comment throttle: only when occurrences double since last comment, persisted anchor (R-05); flood test in test_ingest_api) |
| E6-T3 | Auto-close on silence | TIK-4 | WF-04 §4 | Fingerprint silent past grace window → close comment with evidence; clock logic unit-tested (fake clock) | E6-T1 | S | todo |
| E6-T4 | Regression reopen w/ reintroducing-commit range | TIK-5 | WF-04 §5 | fp returns after close → reopen (never new issue) + commit range; second reopen suppressed w/o human ack (VER-4 interplay) | E6-T3 | M | todo |
| E6-T5 | **Work-state machine + in-flight suppression**: unified WorkState per fingerprint; single-writer dispatch lock; busy-state absorption (counts+comment only); severity escalation; auto-archive | DET-14 | WF-04 §0 | Concurrent dispatch attempts → exactly one active work item (lock test); bursts during investigating/fixing → zero new dispatches, counts bump (e2e); severity jump reclassifies same ticket; archive+clean reopen cycle (fake clock) | E6-T1 | M | **done (core)** (WorkState busy-absorption enforced in gate; QUEUED_RCA reservation before forge call, released on failure (DET-14/R-05); lifecycle decisions E6-T2/T3/T4 landed earlier) |

**GATE M0** after E6: run `tests/e2e/test_gate_m0.py` + real GCP demo; **G-gate certified** (GitHub contract + live sandbox + failure drills per `docs/execution-plan.md` §2); record evidence in STATE.md.

### E7 — RCA agent

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E7-T1 | Agent runtime: work queue, shallow-clone worktree cache, tool allowlist per stage, cost/token metering, audit lines | RCA-7, SEC-2, SEC-5, SEC-7, FIX-10 | WF-05 §2 | Tool calls outside allowlist blocked+audited (test); worktree reused across runs; cost metered per run | E6-T1 | L | in-progress (RcaDispatcher over QUEUED_RCA with hourly budget + graceful degradation + audited verdicts; ModelRca redacts prompts pre-call (R-10 slice); worktree cache + cost metering remain) |
| E7-T2 | Model router: cheap/strong tiers + local-endpoint (Ollama/vLLM) config | RCA-7, SEC-3 | WF-05 §3 | Routing table unit-tested; LLM outage → degraded issues-only mode (NFR-4) with metric + banner | E7-T1 | M | todo |
| E7-T3 | Repo investigation: locate code path from stack frames + search; framework-agnostic | RCA-1 | WF-05 §4 | On eval fixtures: top-5 candidate files contain true file ≥80% (bench harness) | E7-T1 | L | todo |
| E7-T4 | Deploy-diff reasoning stage (feeds diff to model with framing rules) | RCA-2 | WF-05 §5 | Diff context present in ≥95% of RCA prompts when deploy known; injection corpus: diff/log content can't trigger tools | E7-T3 | M | todo |
| E7-T5 | Classification + confidence output (code-fix/config/infra/known-noise/needs-human) | RCA-3 | WF-05 §6 | Structured output validated; unknown class rejected; confidence calibration baseline recorded on eval bench | E7-T3 | M | in-progress (heuristic classifier + model-refined CLASS/CONFIDENCE parsing with fallback (ModelRca); eval bench remains) |
| E7-T6 | RCA report comment renderer + posting | RCA-4 | WF-05 §7 | Golden-file template; includes evidence links + ruled-out list; posts once per fingerprint (idempotent) | E7-T5, E6-T1 | S | **done (core)** (RCA comment posted once per dispatch with class+confidence+summary; idempotency via QUEUED_RCA→OPEN/AWAITING_HUMAN transition) |
| E7-T7 | `@wakey` command handling end-to-end (fix/explain/retry/drop) | RCA-5 | WF-07 §2 | Each command has e2e test via fake GitHub; unauthorized user commands rejected (install scope check) | E3-T3, E7-T6 | M | todo |
| E7-T8 | Config/infra routing: checklist comments, evidence-backed infra reports | RCA-6 | WF-05 §8 | config class → checklist rendered; infra class names dependency/quota evidence from logs | E7-T5 | M | todo |

**GATE M1** after E7: real ticket gains classified RCA. Features RCA-1..7 done.

### E8 — Fix agent

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E8-T1 | Eligibility gate (class ∧ confidence ∧ autonomy ∧ PR cap) with reasons surfaced | FIX-1 | WF-06 §2 | Each failing condition → distinct refusal reason (unit); caps tested under burst | E7-T5 | S | **done** (named refusals incl. chronic guard; 5 tests) |
| E8-T2 | Repro-first loop: generate failing test from evidence → run → patch → green | FIX-2 | WF-06 §3 | Eval bench: repro test present in ≥90% of eligible attempts; patch-only attempts (no repro) rejected by gate | E8-T1, E7-T1 | L | in-progress (ReproFirstFixer: failing repro required, immutable original baseline, final diff vs original, failure output to proposer, repro-tamper rejection (R-08); eval bench remains) |
| E8-T3 | Constrained patcher: scope to service path, style-following, no drive-by refactors | FIX-3, SEC-2 | WF-06 §4 | Diff outside service path → rejected; AST-level "minimal diff" heuristic test on fixtures | E8-T2 | M | in-progress (safe_resolve rejects absolute/traversal/symlink escapes; supported-extension snapshot with vendor/VCS skip + size cap; AST minimal-diff heuristic remains) |
| E8-T4 | Sandboxed test runner: repo `test_command`, network policy (off by default), resource limits, timeout | FIX-4, FIX-10 | WF-06 §5 | Suite timeout → clean failure report; network-blocked test env verified; results parsed into PR body | E8-T2 | M | **done (gate executable)** (minimal env, RLIMITs, process-group kill, unshare net-ns (R-03); `make gate-sandbox` runs the negative suite inside a network-less 512MB/1-CPU/64-pid container: 8/8 green — egress blocked, memory cap enforced, grandchild cleanup, traversal/symlink rejection; live fixing still disabled by default) |
| E8-T5 | Draft PR pipeline: branch naming, PR template (RCA link, what/why, tests, confidence, rollback), linked issue | FIX-5 | WF-06 §6 | Golden-file PR body; PR is draft on creation (asserted); issue cross-linked | E8-T4, E3-T2 | M | **done** (delivery resolves default branch, collision-suffixed wakey/fix-* branch, commits the tested files (git data API, force=false), draft PR bound to base, fingerprint binding persisted (R-06); M2 loop E2E over simulator) |
| E8-T6 | Spot-check verifier (2nd cheap model): diff-vs-RCA justification + injection leak check | FIX-6 | WF-06 §7 | Adversarial fixture (log-injected change) blocked; verifier verdict recorded in audit + PR hidden comment | E8-T5 | M | todo |
| E8-T7 | No-test-suite handling + `allow_without_tests` flag | FIX-7 | WF-06 §8 | Repo w/o tests → no PR by default, RCA notes it; flag=true → PR labeled `wakey/untested` + lower confidence floor | E8-T5 | S | todo |
| E8-T8 | Review feedback loop: `@wakey retry` with comments as context, new commit on same PR | FIX-8 | WF-07 §3 | Retry incorporates reviewer comment (eval fixture); second retry on same PR allowed, third requires human edit (cap) | E7-T7, E8-T5 | M | todo |
| E8-T9 | Digest/approval mode (FIX-9): batched digest issue, ✅ releases candidate PRs | FIX-9 | WF-07 §4 | Digest contains exactly eligible candidates; ✅ by non-authorized user ignored; per-repo opt-in | E8-T5 | M | todo |

### E9 — Verification

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E9-T1 | Post-merge fingerprint watcher (merge event → extended watch window) | VER-1 | WF-08 §2 | Watch state transitions unit-tested w/ fake clock | E6-T3 | S | **done (core)** (persisted verification_started_at/start-occurrences anchors; restart resumes the same window (R-09); merge-event binding live via the webhook) |
| E9-T2 | Verdicts: success (silent past grace) / insufficient (still firing → reopen + evidence) | VER-2 | WF-08 §3 | Both paths e2e-tested; insufficient → ticket reopens, PR labeled | E9-T1, E6-T4 | M | **done (core)** (verdicts applied locally AND on the forge: close+wakey-verified label, reopen+wakey-recurred label (R-09); merge binding + `POST /webhooks/deploy` records deploys; deploy-aware grace waits for the deploy; M2 loop E2E) |
| E9-T3 | Rollback suggestion on deploy-correlated spikes | VER-3 | WF-08 §4 | Suggestion only when deploy correlation + rate threshold met; wording template tested | E5-T6 | S | todo |
| E9-T4 | One-shot-per-fingerprint guard | VER-4 | WF-08 §5 | Second auto-fix attempt after "insufficient" blocked without human ack (tested) | E9-T2 | S | **done** (chronic flag persisted on INSUFFICIENT verdict; FIX-1 gate refuses chronic fingerprints) |

**GATE M2** after E9: repro-first draft PR + post-merge verdict, real infra, evidence logged.

### E10 — Security hardening (continuous; final sign-off at M3)

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E10-T1 | Redaction engine: builtin patterns (AWS/GCP keys, JWTs, cards w/ Luhn, emails, private-key blocks, URLs w/ creds) + custom regexes + second pre-LLM pass | SEC-1 | WF-09 §3 | Fuzz corpus zero-leak test; Luhn validation; custom pattern smoke test; perf: <5ms/KB p95 | E4-T3 | M **done (core)** (18 families, Luhn guard, fail-closed; second-pass sanitize on every external field incl. ids/attribute keys + pre-prompt re-redaction (R-04); 13 corpus + integration tests; fuzz+perf expansion pending) |
| E10-T2 | Injection defense suite: data-quoting conventions, per-stage tool allowlists, log-content canaries in prompts, adversarial corpus | SEC-2 | WF-09 §4 | Adversarial corpus: 0 tool misuse; canary tokens never appear in tool calls | E7-T1 | M | todo (data-fencing system prompt + prompt re-redaction in ModelRca; adversarial corpus + allowlist enforcement still open — see security-review.md open items (R-10)) |
| E10-T3 | Credential store: encrypted-at-rest envelope, env-ref support, rotation docs | SEC-4 | WF-09 §5 | Dump of DB yields no plaintext secrets (test); rotation runbook exercised | E3-T1 | M | todo |
| E10-T4 | Audit log hardening: append-only, hash-chained lines, export API stub | SEC-5 | WF-09 §6 | Tamper test: modified line detected via hash chain | E7-T1 | S | todo |
| E10-T5 | Budgets & graceful degradation: global+per-service caps, exhausted→issues-only mode | SEC-7, NFR-4 | WF-09 §7 | Cap breach stops agents but ingest+tickets continue (e2e); banner+metric emitted | E5-T5, E7-T2 | M | in-progress (RCA hourly budget with queued-not-dropped overflow; per-service ticket cap; PR cap in FIX-1; banner/metric surfaces remain) |
| E10-T6 | External security review pass (self or third party) + threat-model doc | — | WF-09 §8 | Threat model covers OWASP-top-10-for-agents; findings tracked as tasks | all above | L | todo |

### E11 — Operations (continuous)

| ID | Task | Features | Spec | Acceptance criteria | Deps | Size | Status |
|---|---|---|---|---|---|---|---|
| E11-T1 | Dashboard v1 (GUI): server-start banner + auto-open + first-run token; setup wizard hosting; overview/services/fingerprints/tickets-mirror/agent-runs/system/settings pages | OPS-1 | WF-12 | Playwright smoke: `compose up` → banner URL → token → register service → see fingerprint → ticket deep-link; first-run lock without token; guardrail actions audited | E3-T5, E5-T2 | L | in-progress (auth session/CSRF live (R-07 slice); board + settings + **fingerprint detail page with stored events/RCA/audit + manual fix trigger** (WF-12 page 3b, founder 2026-09-16) live; wizard, remaining pages, Playwright suite remain) |
| E11-T2 | Config hot reload end-state (file watch + GitHub refetch) | OPS-2 | WF-11 §4 | Threshold change applied to next event ≤30s, no restart (e2e) | E3-T6 | S | todo |
| E11-T3 | Prometheus metric set complete per eng-standards §5 | OPS-3 | WF-11 §5 | All named metrics exported; Grafana dashboard JSON shipped in repo | E2-T4 | M | todo |
| E11-T4 | Self-watch: wakeyd ingests its own errors via loopback service | OPS-4 | WF-11 §6 | Seeded internal error appears as ticket in ops repo (e2e) | E4-T1 | S | **done (core)** (SelfWatchHandler feeds wakey ERROR+ records through the real pipeline when WAKEY_SELF_WATCH=1; recursion-guarded; unit tests; loopback-HTTP variant remains) |
| E11-T5 | Postgres support behind storage interface + migration from SQLite | OPS-5 | WF-11 §7 | Data migration SQLite→Postgres verified; CI matrix covers both | E2-T3 | M | todo |
| E11-T6 | Backup/restore tooling + runbook | OPS-6 | WF-11 §8 | Restore drill: backup → wipe → restore → doctor green | E2-T3 | M | **done (core)** (`wakey backup` via SQLite backup API, `wakey restore` verifies the restored store answers; CLI tests; runbook doc remains) |
| E11-T7 | CLI core (`wakey`): lifecycle (start/stop/status/logs/doctor/open/setup-token), services & config, fingerprints, budgets/caps — per the WF-13 contract (JSON output, exit codes, `--yes` safety) | OPS-8 | WF-13 | First-run e2e: `start` → banner → `status` with zero prompts; non-TTY mutation without `--yes` refuses with dry-print (exit 2); JSON schema snapshots stable; parity test vs GUI actions | E2-T4, E3-T5 | M | in-progress (wakey console script installed (pyproject [project.scripts]); status/board/doctor live and honest about scope; setup-token (fresh single-use dashboard tokens) + backup/restore live; logs subcommand + full JSON contract remain) |
| E11-T8 | **Lean mode** + small-host story: `resource_profile` config (lean/balanced/full), Raspberry-Pi / small-VPS benchmark run, published reference numbers | NFR-8 | eng-standards §8 | Lean profile on 1GB/RPi-class host: idle ≤120MB, steady within budget; numbers published in docs; GUI refresh + agent concurrency scale down in lean | E2-T6, E11-T3 | M | todo |
| E11-T9 | **Live incident board (basic)**: dashboard kanban of WorkStates + `wakey board` CLI table; aggregated on-load | OPS-11 | WF-12 p3, WF-04 §0 | Board renders all active fingerprints with correct WorkState (e2e vs fixture stream); card click-through to detail; CLI table parity with dashboard | E6-T5, E11-T1 | M | todo |

**GATE M3** after E10+E11 (+P1 polish): OSS launch checklist (README quickstart, examples dir, eval bench v0 published, SECURITY.md, first release `v1.0.0`).

---

## Phase P2 — GA ("trust & breadth") — coarse tasks, detail at planning time

| ID | Task | Features | Size |
|---|---|---|---|
| E12-T1 | CloudWatch adapter (Firehose HTTPS) + conformance tests | ING-5 | M |
| E12-T2 | Azure Monitor adapter (Event Hub forwarder) | ING-6 | M |
| E12-T3 | Kubernetes events adapter (crashloop/OOM/probe) | ING-7 | M |
| E12-T4 | Adapter SDK: `BaseAdapter`, conformance test kit, recipe template docs | ING-10, EXT-1 | L |
| E12-T5 | Vulnerability event sources: Dependabot + osv-scanner webhooks → pipeline | ING-9 | M |
| E12-T6 | Dependency-bump fix flow (upgrade PR + changelog/CVE notes) | FIX-12 | M |
| E12-T7 | Notifications: Slack/Discord/email targets + policy engine + templates | NTF-1, NTF-2 | M |
| E12-T8 | Quiet hours + severity routing | NTF-3 | S |
| E12-T9 | Daily digest (team-level summary) | NTF-5 | S |
| E12-T10 | Environment scoping + staging-first mode | DET-12 | M |
| E12-T11 | Baseline learning + anomaly spikes | DET-10, DET-11 | L |
| E12-T12 | Maintenance windows | DET-8 | S |
| E12-T13 | Runbook links on fingerprints | DET-13 | S |
| E12-T14 | Baselines/suppression UX in dashboard | DET-7/10 | M |
| E12-T15 | RBAC on dashboard (GitHub identity) | SEC-6 | M |
| E12-T16 | REST API complete + OpenAPI publish | OPS-7 | M |
| E12-T17 | CLI (`wakey doctor/list/replay/validate`) | OPS-8 | M |
| E12-T18 | Issue/PR template overrides | TIK-6 | S |
| E12-T19 | Raw event retention + replay UI/API | ING-12 | M |
| E12-T20 | Retention & purge controls (GDPR-style delete) | SEC-8 | M |
| E12-T21 | Eval bench v1 public + calibration report page | ANA-4, EXT-5 | L |
| E12-T22 | Impact report + noise report | ANA-2, ANA-3 | M |
| E12-T23 | PR hygiene: auto-rebase, stale-PR reaper | FIX-11 | S |
| E12-T24 | Outbound webhooks | EXT-4 | S |
| E12-T25 | GitHub Action (`wakey-action`) | EXT-2 | M |
| E12-T26 | Onboarding: multi-repo batch + demo sandbox | ONB-9, ONB-10 | M |
| E12-T27 | Related-fingerprint linking: near-duplicate/supersede detection, work-item families, cross-service causal links | DET-15 | L |
| E12-T28 | Cardinality explosion guard: distinct-fingerprint caps, long-tail bucket ticket, top-K samples | DET-16 | M |
| E12-T29 | External-fix credit: silence + culprit-commit correlation → `fixed-externally` close | VER-6 | S |
| E12-T30 | Live board updates (SSE) + board filters/saved views | OPS-11 (live) | M |

## Phase P3 — Moat — coarse

| ID | Task | Features |
|---|---|---|
| E13-T1 | Cross-service causality (trace-ID graph + upstream walk) | RCA-8, ING-13 |
| E13-T2 | Org memory: local embeddings, similar-incident retrieval in RCA | RCA-9 |
| E13-T3 | Upstream-issue mode (dependency repro filing) | RCA-10 |
| E13-T4 | On-call escalation (PagerDuty/Opsgenie) with RCA-carrying pages | NTF-4 |
| E13-T5 | Canary verification | VER-5 |
| E13-T6 | Multi-tenant wakeyd | ONB-11 |
| E13-T7 | MCP server (fingerprints/RCAs as production context) | EXT-3 |
| E13-T8 | External tracker sync (Jira/Linear) | TIK-7 |
| E13-T9 | Helm chart + HA mode | OPS-9, OPS-10 |

### E14 — Legacy estate management (P2/P3 coarse)

| ID | Task | Features | Phase | Size |
|---|---|---|---|---|
| E14-T1 | EOL & lifecycle radar: manifest/runtime inventory, EOL data source + feed, escalation advisories, upgrade-path PRs | LEG-1 | P2 | L |
| E14-T2 | Health register & risk scoring: factor model, scoring job, dashboard register + exportable report | LEG-2 | P2 | L |
| E14-T3 | Golden-master safety nets: behavior capture → characterization test generation PR → continuous re-run + drift alerts; FIX-7 interlock (untested+golden-passing = fix-eligible) | LEG-3 | P2 | L |
| E14-T4 | Ownership registry: CODEOWNERS ingestion, routing + escalation ladders, orphan detection scan + quarterly report | LEG-5 | P2 | M |
| E14-T5 | Data-layer signals: DB-shaped fingerprint class, migration-drift correlation, fixable-index/query suggestions into FIX pipeline | LEG-6 | P2 | L |
| E14-T6 | Living system atlas: atlas generator (inventory, trace-based map, baselines, ownership, health), markdown PRs, refresh loop | LEG-4 | P3 | L |
| E14-T7 | Decommission assist: traffic-based sunset candidates, checklist PR, post-sunset watch mode + rollback guidance | LEG-7 | P3 | M |
| E14-T8 | Auto-postmortems: verdict-triggered timeline assembly → markdown postmortem PR for human ownership | LEG-8 | P3 | M |

*Detail rule (per SKILL.md): coarse here; full AC written in the epic-start planning pass. E14-T1..T5 gate on M3 (GA phase); T6..T8 on GA.*

### E15 — Write-path monitoring, "Ponytail" plugin (P3 coarse — see SKILL.md §9)

| ID | Task | Features | Phase | Size |
|---|---|---|---|---|
| E15-T1 | Write-path event contract: agent/IDE event schema (diffs, commits, sessions), ingestion via ING-3 webhook, redaction pass, fingerprint spine compatibility | WPM-1 | P3 | M |
| E15-T2 | Plugins for major coding agents & IDEs (Claude Code, Cursor, Codex, VS Code) — opt-in per repo, local-first delivery | WPM-1 | P3 | L |
| E15-T3 | Write-time risk-check API + inline advisory UX (chronic paths, untested modules, EOL APIs; never editor-blocking) | WPM-2 | P3 | L |
| E15-T4 | Write→runtime attribution: session/commit ↔ fingerprint linkage feeding RCA, LEG-2, LEG-8 | WPM-3 | P3 | M |
| E15-T5 | PR-time gate bridge via wakey-action (EXT-2) | WPM-4 | P3 | S |

*Gates on GA + SKILL.md §9 architecture reviews. Full AC written at epic start.*

### E16 — Plugin platform & BYO-AI (coarse; spec WF-14)

| ID | Task | Features | Phase | Size |
|---|---|---|---|---|
| E16-T1 | Plugin runtime: manifest, loader, sandbox (out-of-process, limits, egress allowlist), capability permissions, lifecycle + hot-load, crash isolation/circuit breakers | EXT-6 | P2 | L |
| E16-T2 | Extension-point taxonomy + conformance test kits (source/enricher/detector/responder/notifier/panel/command) incl. redaction-preservation and injection suites | EXT-7 | P2 | L |
| E16-T3 | BYO-AI profiles: provider adapters + OpenAI-compatible binding, task→profile routing surface, save-time probes, per-profile budgets/spend, GUI+CLI parity | EXT-8 | P2 | M |
| E16-T4 | Agent-agnostic coding-tool standards (hooks/MCP/stdio) + community adapter path for WPM-1 | EXT-9 | P3 | M |
| E16-T5 | Community registry: signing, compatibility badges, quality scores, `wakey plugins install <name>` | EXT-10 | P3 | M |

*Gates: E16-T1..T3 on GA (M3); T4..T5 alongside E15. Full AC written at epic start.*

### E17 — Multi-forge support (coarse; spec WF-15)

| ID | Task | Features | Phase | Size |
|---|---|---|---|---|
| E17-T1 | **ForgePort abstraction + GitHub adapter refactor**: normalized ticket/proposal/event interface; import-linter architecture test (forge SDKs only inside adapters); E3/E6/E8 compile against the port | FORGE-1 | **P1** | L |
| E17-T2 | GitLab adapter: project/group access tokens (gitlab.com + self-managed, pinned API versions), issues, Draft MRs, award-emoji approvals, webhooks, comment commands; passes Forge-gate on both | FORGE-2 | P2 | L |
| E17-T3 | `forge` extension point in the plugin SDK + Forge-gate conformance kit for community adapters | FORGE-3 | P2 | M |
| E17-T4 | Onboarding forge parity: wizard forge step, per-forge doctor validation, least-privilege docs | FORGE-6 | P2 | S |
| E17-T5 | Gitea / Forgejo adapter (natural pairing with self-hosted Wakey) | FORGE-4 | P3 | M |
| E17-T6 | Bitbucket Cloud + Azure DevOps adapters (demand-driven) | FORGE-5 | P3 | L |

*E17-T1 is P1 and blocks E6/E8: building the core loop directly on GitHub's API would make FORGE-1 a rewrite instead of a refactor. Gates: T1 at M0; T2..T4 on GA; T5..T6 in P3.*

---

## Counting & completeness check

- P1: **78 tasks** across E1–E11 — every P1 feature from `docs/05-feature-inventory.md` is covered by ≥1 task (verified against the inventory's phase summaries).
- Gate mapping: M0 = E1–E6 · M1 = E7 · M2 = E8–E9 · M3 = E10–E11 (SKILL.md §5); **Forge-gate (G-gate) certified at M0 and ≤14 days before any release, per supported forge** (execution-plan §2); resource budgets (NFR-6..8) enforced by the E2-T6 nightly benchmark from Wave 1 onward; **every gate has a local script** (`make gate-*`, E1-T7) and all testing is host-safe by default (eng-standards §3). Edge cases are tracked in `docs/edge-cases.md` (EC-*).
- P2: 41 coarse tasks (E12 + E14-T1..T5 + E16-T1..T3 + E17-T2..T4); P3: 21 coarse tasks (E13 + E14-T6..T8 + E15 + E16-T4..T5 + E17-T5..T6). Total backlog: **140 tasks**. Detailed AC for P2/P3 is written when their epic starts (per SKILL.md session protocol), not now — detail rots.
