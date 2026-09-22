# P1 implementation follow-up — 2026-09-17

## Outcome

The reliability and local execution-safety implementation below is complete and
tested. **The entire P1 product is not complete or release-certified.** This
checkpoint does not replace the outstanding acceptance criteria in TASKS.md.
Existing worktree changes were preserved. Nothing was committed, pushed or
deployed, and no live GitHub/model credentials were exercised.

## Implemented in this follow-up

| Area | Change | Evidence |
|---|---|---|
| Immediate crash recovery | Requeue processing deliveries and release ticketless reservations at startup; replay without recounting | `test_completion_reliability.py`, PostgreSQL parity tests |
| GitHub retry | Persist redacted payload before dispatch; complete only after success; background retry survives restart | `test_completion_reliability.py` |
| Exact verification | Persist event→fingerprint binding; ignore unrelated logs; legacy unbound observations stay inconclusive | `test_completion_reliability.py`, `test_storage_postgres.py` |
| Live execution isolation | Fresh non-root Docker container per attempt; no network; read-only root/checkout; capped CPU, RAM, PIDs, tmpfs, output and time; no host fallback | `test_sandbox_runner.py`, including real Docker run |
| Workspace safety | Unique checkout per job; failed prepare cleanup; no host pip installation; reject post-prefix archive traversal | `test_workspace.py` |
| Fix eligibility | Repository-wide proposal cap; one execution per executor; reject duplicate/busy proposals; token failures release reservation | `test_fix_executor.py` |
| Service scope | Model/test workspace restricted to `wakey.yml.path`; remap published paths to repo root; reject repro-path alias tampering | `test_fix_executor.py`, `test_fix.py` |
| Human requests | Authorized GitHub command and authenticated dashboard use the shared executor; explicit triage/digest/chronic acknowledgement without bypassing other gates | `test_fix_executor.py`, `test_web_fingerprints.py` |
| Shutdown | Drain active delivery threads before storage shutdown | `test_worker.py` |
| CI | Add PostgreSQL to both Python test jobs; fail rather than silently skip if required DB unavailable; separate real runner Docker job | `.github/workflows/ci.yml` (remote execution not claimed) |

## Verification

- `make check`: formatting, lint, strict typing, SPDX checks and full tests.
- Real runner gate: `WAKEY_DOCKER_TESTS=1 .venv/bin/pytest -q tests/unit/test_sandbox_runner.py` — **11 passed**.
- PostgreSQL tests ran against the local test instance, rather than skipping.
- `git diff --check`: clean.
- Default suite intentionally skips four live-GitHub tests, four container-only
  tests and one opt-in runner test. The opt-in runner test passed separately.
- Existing unclosed-test-database resource warnings and Starlette/httpx
  deprecation warnings remain; no claim of warning-free operation.

The final full-suite count and coverage are recorded in STATE.md.

## Operating instructions for this implementation

1. Keep autonomous fixing disabled on the deployed service until the executor
   integration is validated. Ingest, ticketing and RCA do not require Docker.
2. On a dedicated Docker-capable executor host, pre-provision a **trusted** test
   image containing the target service's runtime and test dependencies. Pin its
   digest in production. Do not build images from model-generated Dockerfiles.
3. Set `WAKEY_SANDBOX_IMAGE` in the server environment. The local runner requires
   its workspace path to exist on the same Docker daemon host. It never pulls an
   image, installs dependencies on the host, or passes host secrets to tests.
4. Set the service's `path` and `test_command` for that image. Commands run from
   the scoped directory, not the repository root or Wakey interpreter.
5. Keep **one active application instance** for the current startup recovery
   protocol. Horizontal replicas need a durable lease/reconciliation design.
6. Back up the database before deploying these migrations: SQLite schema 7,
   PostgreSQL schema 3. Legacy events are retained; unbound legacy windows stay
   inconclusive. No downgrade migration or historical backfill is supplied.
7. Re-run local gates, then run the authorized live integration gates before
   changing deployed autonomy or claiming readiness. No deployment was performed
   by this follow-up.

For the local synthetic image/runner check:

```bash
make image-sandbox
WAKEY_DOCKER_TESTS=1 .venv/bin/pytest -q tests/unit/test_sandbox_runner.py
make check
```

**Cloud Run caveat:** the current application image has no usable Docker daemon.
It will refuse fix execution with this implementation. A separate authenticated,
isolated executor service is required to preserve Cloud Run hosting; do not
restore the host-subprocess fallback to make the check green. Docker control
belongs on a dedicated execution host, not a shared production host containing
unrelated workloads or credentials.

## Remaining work, in order

1. **Remote execution and durable external effects** — add a production executor
   appropriate to the existing deployment; persist job ownership and completion;
   reconcile ticket/PR creation after ambiguous remote success; add command/reply
   outbox processing. Acceptance: crash after each external success creates no
   duplicate ticket/PR and loses no command or reply. Current semantics are
   at-least-once external effects, not exactly-once.
2. **Fix quality gates (E8-T2/T6/T7)** — prove the failure is the target bug,
   distinguish pre-existing failures and dependency/setup failures, independent
   spot-check verifier, no-test behavior and model cost/iteration audit. Pin the
   fetched/tested base commit through publication and handle base movement.
3. **Review and approval features (E8-T8/T9)** — reviewer feedback on the same PR
   with bounded retries; idempotent digest candidates and authorized approvals.
4. **RCA/model completeness** — repository/deploy-diff evidence, tiered/local
   provider routing, spend accounting and enforceable cost ceilings.
5. **Remaining product surfaces** — sidecar ingestion, remaining dashboard/CLI
   operations, metrics, resource-profile support and SQLite→PostgreSQL migration
   drill. Reconcile each TASKS acceptance criterion rather than marking an epic
   done because its core module exists.
6. **Release proof** — eval bench with negative fixtures, critical-module coverage
   targets, live G-gate, resource benchmarks, security review and all milestone
   acceptance criteria. Passing local tests alone does not certify these gates.

Do not interpret this list as a fresh percentage estimate: implementation,
operational integration and release evidence are separate dimensions.
