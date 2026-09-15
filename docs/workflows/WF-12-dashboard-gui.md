# WF-12 — Web Dashboard (the GUI)

> Behavioral contract for the GUI served when wakeyd starts. Features: ONB-2, OPS-1, SEC-6 (P2) · Tasks: E11-T1, E3-T5. Visual language: `docs/design.md` §Dashboard application. Setup wizard behavior itself is specced in WF-01 — this file covers the GUI surface that hosts it.

## Purpose & principles

`docker compose up` (or `wakey start`) must leave the user with a working GUI in their browser — that's the moment of first contact. Principles:

1. **The GUI manages; GitHub decides.** The loop (RCA comments, PR review, `@wakey` commands, merges) stays in GitHub. The GUI is for setup, observation, configuration, and operations. Every incident artifact deep-links to GitHub.
2. **Readable at 2am.** The night-watch design system exists because the real user is on call. No marketing elements, no glows except service-status pulses.
3. **Read-mostly.** Actions are configuration and operations (autonomy, budgets, keys, replay, doctor) — never incident decisions. Those live in GitHub by design.
4. **Local-first security.** Served on the host/network the operator chose; no external identity provider in P1 (RBAC via GitHub identity is P2, SEC-6).

## Server-start experience

1. wakeyd boots (WF-11 §1). When ready, the console prints the **start banner**:
   ```
   ▲ wakey v0.3.1 — watching
     Dashboard  → http://localhost:8477
     First-run setup token → WAKE-7K2M-4QPD   (paste once in the browser)
     CLI        → wakey status
     Docs       → http://localhost:8477/docs
   ```
   Default port `8477` (override: `WAKEY_PORT` / `--port`). Headless detection (no display / SSH / `--no-open`) → banner only; otherwise the dashboard auto-opens in the default browser.
2. **First-run setup token**: on a fresh data dir, the GUI is locked until the operator pastes the one-time token from the console. This prevents any other local user/browser tab from performing setup. Token is single-use, 30-minute expiry, and consumed on first admin session.
3. Subsequent starts: existing session auth applies; the banner shows the URL and `wakey status` hint only (no token unless a new one is requested via `wakey setup-token`).

## Pages (P1 inventory)

| # | Page | Contents & actions |
|---|---|---|
| 0 | **Setup wizard** | Hosts WF-01 steps A1–A5 as a full-screen surface-card sheet with an amber progress thread; resumable; validation errors inline with file/line (from config loader). On completion → lands on Overview with a doctor pass/fail summary. |
| 1 | **Overview** (home) | Estate at a glance: one health card per service (status pill `WATCHING/WAKING/…`, 24h error sparkline, open tickets, risk-lite indicators), global strip: agent spend today, degradation-ladder state (banner if ≠ full), queue depth, unread digest existence. Each element deep-links to its page or GitHub. |
| 2 | **Services** | List + detail. Detail: config snapshot + health (validation state, last fetch of `wakey.yml`), ingest endpoint status + key rotation button, autonomy dial (writes require confirm + audit line), thresholds/budgets editors, recent fingerprints, last deploys, test-command probe ("run once" button). Add-service flow = WF-01 §A3 form. |
| 3 | **Fingerprints** | Filterable table (service, severity, state, age): `fingerprint-row` design. Detail view: occurrence chart (deploy markers overlaid), event samples (redacted), linked ticket/PR with GitHub links, RCA summary + confidence meter + cost chips, suppression controls (suppress with duration + reason — same write path as `@wakey drop`/DET-7, audited). |
| 4 | **Tickets & PRs** | Read-only mirror of open wakey issues/PRs per service with sync state; **deep links only** — review happens in GitHub. Shows digest-issue existence in digest mode (approvals stay in GitHub). |
| 5 | **Agent runs & audit** | Investigation/fix run history: duration, model tier, tokens, cost, verdict; filterable. Audit-log viewer (read-only, hash-chain status line; export button P2). |
| 6 | **System** | Degradation ladder state + reason, connector health (per-service ingest heartbeat), queue depths, retention/backup status, self-watch link. Surface for WF-11 evidence. |
| 7 | **Settings** | LLM providers & routing table (cheap/strong/local endpoints — RCA-7/SEC-3), budgets & caps editors (SEC-7), redaction pattern management (SEC-1; every pattern edit re-runs the security corpus smoke set before save), data-dir/backup info, doctor re-run, `wakey setup-token` regeneration. P2 adds: RBAC (SEC-6), notification targets (NTF-1), retention/purge (SEC-8), REST API keys (OPS-7). |

## Cross-cutting GUI rules

- **Auth**: session cookie (HttpOnly/Secure/SameSite per WF-01); login = first-run token (once) then local admin session; P2 adds GitHub-identity RBAC. Remote access over HTTPS is the operator's reverse-proxy responsibility — documented, with a warning banner when `BASE_URL` isn't HTTPS.
- **Read-only mirrors**: any GitHub state shown (issues, PRs, verdicts) is cached with visible freshness ("synced 2m ago"); never editable in the GUI.
- **Destructive/consent actions** (rotate key, change autonomy to `fix`, suppress, budget change): explicit confirm + audit line + where GitHub-visible, a note in the ticket. Autonomy to `fix` additionally warns about PR creation.
- **Empty/first-run states**: every page has a designed empty state with the next action (never a blank table).
- **Responsiveness**: usable at 375px (on-call from a phone); tables collapse to cards; all touch targets ≥44px (design.md guardrails).
- **No CDN/external calls**: fonts/assets self-hosted (air-gap rule); analytics: none.

## State written

Config changes (through the same validated config path as `wakey.yml`/env — the GUI never bypasses validation), service/key/budget mutations, audit lines for every action, session records. The GUI writes **no** GitHub state except through existing workflows (e.g., suppression = DET-7 path).

## Failure modes

| Failure | Expected behavior |
|---|---|
| GitHub unreachable | Mirrors show "stale since T" badge; config/actions not requiring GitHub keep working |
| LLM provider down | System page shows degradation ladder rung; pages render normally (NFR-4) |
| Config save fails validation | Inline errors with file/line; last-good config kept everywhere |
| Session expired mid-edit | Editor state preserved after re-auth where possible; destructive actions always require re-confirm |
| Two admins edit simultaneously | Last-write-wins with explicit conflict banner showing both values; audit records both actors |
| Browser tab left on dead server | Health heartbeat fails visibly; reconnect banner when wakeyd returns |
| First-run token lost (headless install) | `wakey setup-token` prints a fresh single-use token; old one invalidated |

## Acceptance criteria

1. Fresh `docker compose up` on a clean machine → banner URL opens → wizard completes → doctor green, without reading any doc beyond the README quickstart (manual AC at M0; wizard steps Playwright-tested in CI).
2. First-run lock: without the token, wizard is inaccessible (Playwright test); token single-use and expiry enforced (unit).
3. Every GitHub artifact shown links out; no GUI control mutates GitHub incident state (code-review checklist + e2e asserting mutation attempt surfaces only deep links).
4. Every action on the guardrail list (autonomy, keys, suppression, budgets) writes an audit line and shows confirm (integration tests).
5. Degradation (GitHub down / LLM down simulated) → correct badges/banners, no broken pages (e2e with fakes).
6. Playwright smoke suite at 375px and 1440px, keyboard-only pass (focus order, amber rings), per design.md pre-delivery checklist.
