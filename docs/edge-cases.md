# Edge-Case Register

> Living register of edge cases and how Wakey handles them. **Status**: `covered` (spec exists — pointer given) or `planned` (new feature/task added by this pass). New edge cases get an EC id here first, then a home in a workflow spec / feature ID.

## A. Trigger intelligence & dedup (the "don't trigger twice" family)

| ID | Edge case | Handling | Status |
|---|---|---|---|
| EC-01 | **Error re-triggers while already being worked on** (bursts arrive during RCA/fix) | **Work-state machine** (DET-14, WF-04): while WorkState ∈ {queued-rca, investigating, fixing, verifying}, occurrences only bump counters + one throttled "still occurring ×N" comment; dispatches are single-writer per fingerprint (DB lock) — never a second RCA/PR in parallel | **planned** → E6-T5 |
| EC-02 | Error recurs after a fix verdict ("already been worked on") | Reopen + `chronic` flag; second auto-fix blocked without human ack | covered (TIK-5, VER-4) |
| EC-03 | **Near-duplicate fingerprints**: same root cause, stack shifted after a deploy (line numbers/wrappers change) | Related-fingerprint linking (DET-15): superseded fingerprints attach to the active work item; one investigation covers the family | **planned** → E12-T27 |
| EC-04 | Same root cause, multiple symptoms/services (two errors, one bug) | RCA links related fingerprints to one causal ticket; closing the causal ticket proposes closing the family | planned → E12-T27 |
| EC-05 | Same error across environments | Environment-scoped fingerprints (distinct), cross-linked in RCA when root cause is shared | covered (DET-12) / linking planned |
| EC-06 | Severity escalates while ticket is open (error gets worse) | Reclassify + priority bump in queue + re-notify per policy — never a second ticket | planned → DET-14 (E6-T5) |
| EC-07 | **Cardinality explosion**: flooding of *unique* errors (bad retry loop, scanner) → unbounded fingerprints | Long-tail guard (DET-16): per-window distinct-fingerprint cap; overflow bucketed into one "long tail" summary ticket with top-K samples | **planned** → E12-T28 |
| EC-08 | Occurrences continue while fix **PR is open** (not merged) | Fixing state suppresses new fixes; PR gets throttled "still occurring ×N" comment | planned → DET-14 |
| EC-09 | Human fixes it externally (no wakey PR) | Watcher sees silence + a merged commit touching the culprit file → close as `fixed-externally`, credit the commit | planned → VER-6 (E12-T29) |
| EC-10 | RCA failed on provider outage; error keeps occurring | Retry with backoff on next occurrence window — monitoring never stops | covered (WF-05, NFR-4) |
| EC-11 | Fingerprint silent for weeks (not closed, just quiet) | Auto-archive: state → `archived` after N days quiet; reopens cleanly on return | planned → DET-14 |
| EC-12 | Long-dormant error returns months later | Fresh incident segment on the old ticket (timeline preserved, counts reset per segment) | covered (TIK-5) |

## B. Ingest & data

| ID | Edge case | Handling | Status |
|---|---|---|---|
| EC-13 | Malformed/unknown log lines | Dead-letter with reason, never 5xx-storm, replayable | covered (WF-02) |
| EC-14 | Duplicate deliveries (platform retries) | Idempotency keys, exactly-once effect | covered (WF-02) |
| EC-15 | Clock skew / future timestamps | Clamp + flag | covered (WF-02) |
| EC-16 | Secrets in logs | Redact-before-store/prompt, fail-closed | covered (WF-09) |
| EC-17 | Log flood of *identical* errors | Burst collapsing: one fingerprint + counter | covered (DET-4) |
| EC-18 | Restart mid-pipeline | DB-backed state, redelivery idempotent | covered (NFR-3) |

## C. Forge & repo

| ID | Edge case | Handling | Status |
|---|---|---|---|
| EC-19 | Repo renamed/transferred on the forge (webhooks 404, clone fails) | Service flagged `misconfigured` with remap flow (old path → new path, state preserved); tickets keep history | partially covered (WF-04); remap flow → E17-T4 |
| EC-20 | Repo archived/read-only | Skip + flag, no ticket spam | covered (WF-04) |
| EC-21 | Force-push of default branch during a fix | Re-run tests on rebased worktree; conflicts → abort honestly | covered (WF-06) |
| EC-22 | Ticket/PR deleted by a human mid-loop | Abort gracefully, state preserved on dashboard | covered (WF-05) |

## D. Ops & platform

| ID | Edge case | Handling | Status |
|---|---|---|---|
| EC-23 | LLM provider down / budget out | Degradation ladder L1/L2, issues-only monitoring continues | covered (WF-11) |
| EC-24 | GitHub/forge unreachable ≥5m | L3: fingerprints accumulate, backlog catch-up, no duplicate tickets | covered (WF-11) |
| EC-25 | Upgrade mid-agent-run | Graceful checkpoint ≤5min, resume from persisted state | covered (WF-11) |
| EC-26 | Two admins edit config concurrently | Last-write-wins + conflict banner + both audited | covered (WF-12) |
| EC-27 | DST/timezone shifts in quiet hours and windows | Explicit IANA tz per target; tolerant windows | covered (WF-10/03) |

**Rule:** new edge cases enter this register with an EC id; if they change behavior, the owning WF spec + feature inventory are updated in the same change (SKILL.md session protocol).
