# WF-04 — Ticket Lifecycle (GitHub Issues)

> Behavioral contract for tickets as GitHub issues. Features: TIK-1..6 · Tasks: E6-*. Summary: `docs/03-end-to-end-workflow.md` §B4.

## Purpose & trigger

On a wake signal (WF-03), create **one** GitHub issue per new fingerprint, keep it alive while the error lives, close it when the error is gone, reopen on regression. Tickets are the coordination point for RCA (WF-05) and fixes (WF-06).

## Actors & I/O

- **wakeyd ticket manager** ← wake signals, occurrence updates, silence scheduler, regression detector.
- **GitHub** ← issue create/comment/label/close/reopen.
- **Inputs**: fingerprint payload, `wakey.yml` (templates, notify flags), deploy events (correlation).
- **Outputs**: issue URL ↔ fingerprint binding; label set; comments.

## Steps

1. **Cap check** (`SEC-7`): ticket-rate cap per service (default 10/hour, 50/day). Over cap → fingerprint enters `overflow` queue; a single overflow digest comment is added to the most recent ticket of that service ("N more fingerprints suppressed this hour — see dashboard"). Cap breaches are alarmed metrics, never silent drops.
2. **Deploy correlation** (`DET-9`): find last deploy before `first_seen` (same service+environment) → `suspect_commit` (first-bad) + `last_good` deploy. Absent deploy data → omit section, ticket says "no deploy data connected — add deploy source for correlation".
3. **Render ticket** (`TIK-1`): template (golden-file tested) with: title `[wakey] <service>: <template head> (×N in Wm)`; fingerprint id; severity; counts/rate; timeline sparkline (text); **redacted** log excerpt (top 10 lines, chosen = first + worst); deploy correlation block; repo path; links: dashboard fingerprint page, compare URL `last_good…suspect`; footer: status line ("RCA in progress" / "observe mode: not investigating").
4. **Labels** (`TIK-2`): `wakey`, `wakey/sev-<tier>`, `wakey/fp-<hash8>`, `wakey/state-new`. Create-if-missing on first use (cached).
5. **Create issue** via GitHub client (WF-01 §client). Persist binding `(fingerprint ↔ issue id/url)` with idempotency: wake signal retry must not double-create (unique constraint + pre-check).
6. **RCA hand-off**: if autonomy ≥ `triage` and RCA budget available → dispatch WF-05 with issue context; else set `state=observed` and footer says how to investigate manually.
7. **Live updates** (`TIK-3`): while open, throttled comment — max 1/hour/fingerprint: "still occurring ×142 (+38 in last hour), rate 0.6/min". Suppressed during maintenance windows. Updates stop when closed (regressions go through step 8 instead).
8. **Auto-close** (`TIK-4`): scheduler scans open wakey tickets; fingerprint silent ≥ grace (default 30m, per-config) → comment "silent for 30m after ×N total — closing. Reopens automatically if it returns." → close. State → `closed-auto`.
9. **Regression reopen** (`TIK-5`): events for a `closed-auto` fingerprint → **reopen the same issue** (never new), comment with gap analysis: was silent since T1, returned T2, "possibly reintroduced by" commit range from deploys in the gap. If the fingerprint had a merged fix PR (WF-08), route to VER-2 verdicts instead of a generic reopen comment. Second regression without a human ack on the first → still reopen, but flag `wakey/chronic` and stop auto-fix eligibility (VER-4).
10. **Manual human edits are respected**: if a human closed the ticket (not wakey), reopen requires `@wakey reopen` or config; wakey never reopens human-made decisions silently (state records closer actor).

## State written

`tickets` (binding, state machine: `new → investigating → open → closed-auto | closed-human | reopened`), labels cache, `audit` (create/close/reopen with actor).

## Failure modes

| Failure | Expected behavior |
|---|---|
| GitHub API down at create | Retry w/ backoff (bounded); if exhausted → fingerprint stays `pending-ticket`, dashboard shows it, next scheduler pass retries. No event loss (WF-03 keeps counting) |
| Wake signal duplicate (redelivery) | Idempotent create (step 5) → one issue |
| Cap overflow | Overflow digest (step 1) — visible, counted, alarmed; earliest tickets take priority (FIFO) |
| Template render error (weird bytes) | Fall back to minimal template (fp id + counts + raw excerpt fenced); render failure never blocks ticket creation |
| Label creation fails | Ticket still created unlabeled; retry labels later |
| Repo archived/read-only | Service marked `misconfigured`; doctor + dashboard flag; tickets skipped with reason |
| Human closes ticket while error continues | Step 10: stays closed; suppression suggestion offered in dashboard (one click → DET-7 entry) |

## Acceptance criteria

1. Golden-file test of rendered ticket body incl. deploy-correlation block and redacted excerpt.
2. Idempotency: double wake signal → single issue (contract test w/ fake GitHub).
3. Cap: 11th fingerprint in an hour → 10 tickets + 1 overflow digest comment (unit w/ fake clock).
4. Update throttle: 10k events/hour → exactly ≤1 update comment (unit).
5. Auto-close + reopen cycle: fake-clock e2e asserting state transitions and comment texts.
6. Human-closed ticket is not auto-reopened (unit; actor recorded).
7. `observe` mode: ticket created, no RCA dispatch, footer says so (e2e w/ fake LLM counter).
