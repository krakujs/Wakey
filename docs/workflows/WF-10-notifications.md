# WF-10 — Notifications & Human Routing

> Behavioral contract for telling humans what matters, when it matters. Features: NTF-1..5, DET-8 · Tasks: E12-T7/8/9 (P2), E13-T4 (P3). GitHub issue/PR activity is itself the primary "notification" — this workflow is for pushing attention beyond GitHub.

## Purpose & trigger

Deliver policy-based notifications: what (ticket/RCA/PR/verdict), where (Slack/Discord/email/webhook), when (severity, quiet hours, rate). Design rule: **notification is a privilege, not a default** — a team's channel stays quiet unless policy says otherwise. Default: GitHub-only.

## Steps

1. **Policy evaluation**: every notifiable event (ticket created, RCA posted, PR opened, verdict reached, cap breached, injection blocked) is matched against `notify:` policy (per service, from `wakey.yml` + dashboard overrides): event types × minimum severity × target list.
2. **Quiet hours** (`NTF-3`): per-target windows (e.g. Slack: 22:00–07:00 weekend off). During quiet hours, `page`-class events queue as **morning digest** unless severity = critical (critical bypasses with explicit `bypass_quiet_hours: true` config, default false).
3. **Delivery** (`NTF-2`): Slack/Discord/email/webhook senders with: rich blocks (ticket link, one-line RCA or status, counts, cost if notable), per-target dedup (same fingerprint doesn't re-notify within cooldown, default 4h unless escalation), retry with backoff, dead-letter on persistent failure.
4. **Content rules**: notifications link to GitHub (source of truth) and carry **substance** (RCA headline, verdict), never bare alerts. Redaction applies (WF-09 pass applies to any log excerpt embedded — P1: embed no log excerpts in notifications at all, just links).
5. **Daily digest** (`NTF-5`): optional per-target daily summary: opened tickets, RCAs posted, PRs awaiting review, verdicts, spend. Replaces per-event noise for low-touch teams.
6. **Escalation** (P3, `NTF-4`): PagerDuty/Opsgenie targets for critical-class events when policy demands; the page carries the RCA headline + links (the "wake up to an answer, not a question" rule); deduped against open incidents.

## State written

`notification_deliveries` (event, target, status, dedup key), quiet-hours queue, digest state, `audit` (notify actions), metrics (`wakey_notifications_total{target,deduped}`, delivery failures).

## Failure modes

| Failure | Expected behavior |
|---|---|
| Target unreachable (Slack 5xx) | Retry w/ backoff ×3 → dead-letter + dashboard warning; never blocks the main pipeline |
| Notification flood (mass incident) | Per-fingerprint cooldown + global burst cap per target (default 20/hour) → overflow summarized in digest; the flood itself raises a `critical` meta-alert (configurable) |
| Policy misconfig (unknown target) | Rejected at config load (WF-01 §6 semantics) |
| Clock/timezone error in quiet hours | Timezone explicit per target (IANA name, validated at load); invalid → load error, not silent UTC |
| Webhook target content rule violation (too large) | Truncate with link; never fail the source event |

## Acceptance criteria

1. Default install sends zero external notifications (unit: no targets configured → no deliveries).
2. Quiet-hours queueing + critical bypass (fake-clock unit tests).
3. Cooldown dedup: same fingerprint 5 notifications within 4h → 1 delivered (unit).
4. Flood: 100 events in an hour → ≤20 deliveries + overflow noted (unit).
5. Delivery failure → dead-letter + dashboard warning, pipeline unaffected (integration w/ fake target).
6. Timezone validation: invalid IANA name → config load error (unit).
7. No log excerpts embedded in P1 notification bodies (golden-file).
