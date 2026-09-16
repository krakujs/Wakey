# WF-08 — Verification & Closing the Loop

> Behavioral contract for post-merge verification. Features: VER-1..5, TIK-4/5 · Tasks: E9-*. Summary: `docs/03-end-to-end-workflow.md` §B9.

## Purpose & trigger

Prove whether a suggested fix actually fixed the error. Triggered when a wakey PR is merged (webhook), then a scheduled watcher runs until verdict. This is what makes Wakey "someone always watching" instead of a one-shot generator.

## Actors & I/O

- **Watcher** (scheduler + fingerprint counters), **GitHub** (PR merge events, verdict comments), **rollback advisor** (deploy correlation).
- **Inputs**: merged PR ↔ ticket ↔ fingerprint binding, occurrence stream, deploy events, grace config.
- **Outputs**: verdict comment + ticket close/reopen, PR labels, metrics, audit.

## Steps

1. **Bind merge**: on wakey PR merge → record `fix_shipped_at` = merge time + the SHA; ticket state → `verifying`; watcher arms with the **extended** silence grace (default: 3× normal grace, min 1h, config per service). Environment note: verification assumes the fix reaches the emitting environment — if deploy events show the fix hasn't deployed there yet, watcher waits for that deploy before starting the clock (deploy-aware verification). *Implemented 2026-09-16:* `POST /webhooks/github` (`pull_request.closed` + merged) matches the fingerprint's stored `proposal_branch` → state `verifying` with persisted `verification_started_at`/`verification_start_occurrences` anchors; `POST /webhooks/deploy` (same HMAC) records deploy events and the composed watcher delays the clock until a deploy at/after the merge lands; no deploy source configured = deploy-blind mode (documented limitation until E4-T6).
2. **Watch** (`VER-1`): during the window, count fingerprint occurrences. Expected decay curves documented: immediate-zero, gradual (traffic-dependent). Watcher is pure counting — no LLM.
3. **Success verdict** (`VER-2a`): zero occurrences for the full window → comment on ticket: "fix confirmed: fingerprint silent for <window> after merge <sha>; closing." Ticket → `closed-verified`; PR label `wakey/verified`; metric `wakey_fix_confirmed`.
4. **Insufficient verdict** (`VER-2b`): occurrences continue past window at ≥ `verdict.threshold` (default: any) → comment: "fix insufficient: ×N occurrences since merge; root cause likely partial/incomplete — reopening." Ticket → reopened (`TIK-5` semantics), PR labeled `wakey/insufficient`, fingerprint → `chronic-candidate` (VER-4 guard below). Metric `wakey_fix_insufficient`.
5. **One-shot guard** (`VER-4`): fingerprint with an `insufficient` verdict is **not eligible** for another autonomous fix (WF-06 step 1) until a human acks — via `@wakey fix` on the reopened ticket (explicit human intent) or dashboard. This prevents the worst failure loop: agent "fixing" the same thing badly forever.
6. **Rollback advisor** (`VER-3`): independent of fixes — when a deploy-correlated fingerprint's rate exceeds `rollback.suggest_rate` (spike ≥ X× baseline, P1: absolute threshold), comment once per deploy on the ticket: "error rate Y× since deploy <rev> (commit <sha>) at <time>; last good <rev>. Consider rollback while investigating." Never repeated for the same deploy unless rate doubles again.
7. **Chronic fingerprints**: ≥2 insufficient verdicts (or ≥3 regressions, WF-04 step 9) → label `wakey/chronic`; dashboard surfaces for human triage; suppression/runbook suggestion (P2 `DET-13`) offered. Chronic ≠ hidden — it's escalated.
8. **Recovery edge**: if occurrences were environmental (deploy rolled back by humans) → watcher detects deploy reversal + silence → verdict comment notes "resolved by rollback <rev>", no fix credit.

## State written

`tickets` (`verifying`, `closed-verified`, `reopened`), `fix_attempts.outcome`, fingerprint `chronic` flags, verdict metrics, `audit` (verdicts with evidence counts).

## Failure modes

| Failure | Expected behavior |
|---|---|
| Fix never deploys to emitting environment | Watcher waits (step 1 deploy-awareness); timeout (default 24h) → "verification inconclusive — fix not observed in <env>" + ticket stays open w/ `wakey/unverified` |
| Traffic seasonality (night silence looks like success) | Window config per service; minimum window floor regardless of silence (default 1h); dashboard shows verification caveats |
| Fingerprint mutates slightly post-fix (template drift) | Related-fingerprint match (same normalized core, P2) counts as recurrence; P1: exact-fp only, documented limitation |
| PR reverted by human | Revert event → verdict cancelled, ticket returns to `open`, comment explains |
| Merge webhook lost | Scheduled reconciliation: open wakey PRs checked against merged state every 15m |
| Watcher down during window | State machine persists; on restart, window resumes from persisted timestamps (never re-arms from zero) |

## Acceptance criteria

1. Fake-clock e2e: merge → silence → success verdict + close + labels (exact comment texts golden-filed).
2. Fake-clock e2e: merge → continuing events → insufficient verdict + reopen + chronic guard armed.
3. One-shot guard: post-insufficient, WF-06 refuses auto-fix with cited reason; `@wakey fix` overrides (unit + e2e).
4. Deploy-aware waiting: fix not deployed to env → no verdict before deploy; inconclusive timeout path tested.
5. Rollback advisor: fires once per deploy threshold crossing; not repeated at same rate (unit).
6. Revert handling: PR reverted → verification cancelled, ticket reopened (unit).
7. Watcher restart: window resumes from persisted state, not reset (unit).
