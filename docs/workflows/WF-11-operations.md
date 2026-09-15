# WF-11 — Operations: Running, Upgrading, Degrading, Recovering

> Behavioral contract for wakeyd as a production service. Features: OPS-1..6, NFR-1..5, ONB-1 · Tasks: E1-T3, E2-T3/4/5, E11-*.

## Purpose & trigger

Keep wakeyd itself healthy 24/7 on someone's infrastructure — the tool that watches services must itself be watchable. Covers startup, runtime health, upgrades, degraded modes, backup/restore.

## Steps

1. **Startup sequence** (ordered, fail-loud): config load + validate (WF-01 §6 semantics) → credential store unlock (master key; missing → refuse) → DB migrate (forward-only; failure → refuse with remediation) → GitHub auth probe → LLM provider probe → connectors register → queue workers up → `/readyz` green. Boot report persisted for doctor/dashboard.
2. **Runtime health**: `/healthz` liveness (process up), `/readyz` readiness (DB writable, GitHub reachable ≥1 success/5m, LLM reachable — **LLM down does not fail readiness**, see step 4). Process metrics (CPU/mem/fd), queue depths per stage, oldest-pending-event age.
3. **Self-watch** (`OPS-4`): wakeyd registers itself as a service ("wakey-internal", loopback ingest). Its own panics, queue overflows, cap breaches become fingerprints → tickets in an ops repo. Rule: any wakeyd error-class log also flows through WF-02 as a normal event — dogfooding by construction, not by afterthought.
4. **Degradation ladder** (`NFR-4`, one direction only — always toward *more monitoring, less intelligence*):
   - L0 full: everything.
   - L1 no-fix: fix agent paused (budget/cap) → RCA+tickets continue.
   - L2 no-RCA: + RCA paused (provider down/budget) → ingest+fingerprints+tickets continue; tickets say RCA unavailable + retry scheduled.
   - L3 issues-only-minimum: GitHub unreachable for ticket creation → fingerprints accumulate in DB; backlog catch-up on recovery replays wake signals (idempotency prevents duplicates).
   - Ladder state is a metric + dashboard banner; transitions logged; recovery re-enables in reverse order after health probes pass.
5. **Upgrade procedure** (`OPS-6`, semver per eng-standards §7): backup (step 6) → pull new image → migrate runs automatically forward-only → boot report verified → in-flight agent work: attempt-graceful (finish or checkpoint ≤5min) then resume from persisted state. Rollback = previous image + restored backup; documented as the supported path.
6. **Backup/restore** (`OPS-6`): `wakey backup` (or scheduled job): SQLite file / Postgres dump + credential-store ciphertext + config snapshots. Restore drill runbook: restore → migrations no-op → doctor green → backlog resumes. RPO/RTO documented (P1: RPO 24h manual / RTO 30min).
7. **Capacity & scale** (`NFR-2`): single-box target 25 services / 50 repos / 5M events/day (SQLite); beyond → Postgres path (`OPS-5`); queue depth + ingest latency are the scaling signals (documented thresholds); horizontal ingest replicas is a P3 concern (`OPS-10`), but the queue interface is built replica-ready from E2-T5.
8. **Lifecycle housekeeping**: retention jobs (events, dead-letters, delivery ids, investigations) run on schedule with dry-run mode; disk-usage guard: at 80% watermark → retention accelerates + alert.

## State written

`system_state` (degradation level, boot report), backups, retention job logs, metrics, `audit` (admin-level operations).

## Failure modes

| Failure | Expected behavior |
|---|---|
| Config invalid at boot | Refuse to serve; log every problem with file/line; previous container keeps running (compose `depends_on` healthcheck pattern documented) |
| DB locked/corrupt (SQLite) | L3 + dashboard banner; WAL mode minimizes; restore runbook |
| GitHub unreachable ≥5m | L3 ladder rung; backlog catch-up on recovery; fingerprints unaffected |
| LLM provider outage | L2 rung; monitoring+tickets unaffected; auto-resume on probe pass |
| Disk full | Retention acceleration + hard alert before writes fail; ingest backpressure as last resort |
| Clock jump (NTP step) | Interval schedulers use monotonic anchors where possible; persisted windows tolerated ±5m (WF-03/08 semantics) |
| OOM of wakeyd | Container restart policy; state all DB-backed → clean resume; queue redelivery idempotent (WF-02 step 3) |
| Partial upgrade (image new, migration failed) | Refuse to serve; operator guidance = restore backup + previous image (documented, tested) |

## Acceptance criteria

1. Boot-order test: each dependency failed in isolation → refuses with the right message, never half-serves (unit/integration).
2. Degradation ladder: forced provider outage → L2 with tickets continuing (e2e); recovery → auto-resume (e2e).
3. Backlog catch-up: GitHub down 1h (fake) during a storm → recovery replays exactly the missed wake signals, zero duplicate tickets (e2e).
4. Upgrade drill: backup → wipe → restore → doctor green (CI job with real SQLite/Postgres containers).
5. Self-watch: seeded internal error → wakey-internal fingerprint → ticket in ops repo (e2e).
6. Retention: past-window events purged, fingerprints' aggregates survive (unit).
7. Metrics completeness: every metric named in eng-standards §5 exported with expected labels (contract test).
8. Load: 5M events/day sustained on reference box without queue starvation (nightly perf, fail-on-regression).
