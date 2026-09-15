# WF-01 — Install, Onboarding & GitHub Connection

> Behavioral contract for first-time setup. Features: ONB-1..8, ING-1, SEC-4 · Tasks: E3-*, E1-T3 · Summary diagram: `docs/03-end-to-end-workflow.md` Phase A.

## Purpose & trigger

A team adopts Wakey by self-hosting `wakeyd`, connecting it to their GitHub, registering services, and pointing log sources at it. Triggered once per installation (and per added service/repo).

## Actors & I/O

- **Admin** (human): performs steps in dashboard + GitHub + cloud console.
- **wakeyd**: wizard server, credential store, GitHub client.
- **GitHub**: OAuth/App installation flows, webhooks.
- **Inputs**: hosting environment vars (`BASE_URL`, `DATA_DIR`, `LLM_API_KEY(S)`, `WEBHOOK_SECRET`), admin's GitHub session, `wakey.yml` in repos.
- **Outputs**: installation credentials (encrypted), service registry rows, per-service ingest keys, verified pipeline (doctor).

## Steps

1. **Host** (`ONB-1`, E1-T3): admin runs `docker compose up -d` (SQLite mode) or points Helm/compose at Postgres. wakeyd starts, runs pending migrations, serves dashboard at `BASE_URL`. If migrations fail → refuse to serve, log remediation.
2. **Bootstrap admin**: first dashboard visit requires setting a local admin credential or (P2) GitHub-identity RBAC. Session cookies `HttpOnly; Secure; SameSite=Lax`.
3. **Connect GitHub — App path** (`ONB-3`, preferred): dashboard "Connect GitHub" → redirect to `https://github.com/settings/apps/new` with pre-filled **manifest** (app name "wakey on <host>", permissions: issues RW, contents RW, pull-requests RW, metadata R; webhook URL `BASE_URL/webhooks/github`, secret `WEBHOOK_SECRET`). Admin clicks Create → GitHub redirects back with setup code → wakeyd exchanges for installation id + private key → stores **encrypted** (SEC-4) → admin selects repositories for the installation.
4. **Connect GitHub — PAT path** (`ONB-4`, fallback): paste fine-grained PAT; stored encrypted; dashboard marks connection "PAT (non-preferred)". Same client interface as App path.
5. **Register a service** (`ING-1`): "Add service" → pick repo + optional path inside it (monorepo `ONB-8`) → wakeyd fetches/validates `wakey.yml` (`ONB-5/6`) → creates service row → generates **ingest key** `wk_<service>_<random>` (shown once; rotatable) → prints the service's ingest endpoint and platform recipes.
6. **Connect logs**: admin follows the recipe for their platform (WF-02 §3) using the endpoint + key. Dashboard shows "waiting for first event" live status.
7. **Verify — `wakey doctor`** (`ONB-7`): one click runs: GitHub perms probe → LLM reachability probe → synthetic error (`wakey-doctor-test` fingerprint) posted to the real ingest endpoint → expects: fingerprint, ticket in a designated sandbox repo (or dry-run echo), auto-close within grace. Report lists each stage pass/fail with the fix hint on failure.
8. **Default state after onboarding**: autonomy = `triage`, thresholds = conservative defaults, redaction = builtin patterns, notify = GitHub-only. Nothing more enabling without explicit config.

## State written

`installation` (auth kind, encrypted credentials), `service` (repo, path, key hash, config snapshot), `config_versions` (wakey.yml snapshots), `audit` lines for credential and key operations.

## Failure modes

| Failure | Expected behavior |
|---|---|
| GitHub App creation abandoned mid-flow | No partial credentials stored; wizard returns to step 1 cleanly |
| Manifest webhook secret mismatch | Webhooks rejected 401; dashboard banner shows exact mismatch hint (no secret material) |
| `wakey.yml` invalid at registration | Service saved in `unconfigured` state; dashboard lists every validation error with file/line; no ingest acceptance until fixed? **No — ingest still accepted and buffered 24h**, so logs aren't lost while admin fixes config |
| Key rotation in progress | Old key valid ≤60s grace, both marked in metrics; dashboard shows overlap window |
| Doctor fails one stage | Report isolates the stage; pipeline state untouched; doctor is idempotent and safe to re-run |
| BASE_URL unreachable from GitHub (webhooks) | Doctor's webhook probe fails with explicit "GitHub cannot reach <url>" guidance |

## Acceptance criteria

1. Fresh install → doctor green ≤10 min following only README instructions (manual AC at gate M0; automated: doctor stages pass in CI with fakes).
2. Credential store dump contains no plaintext GitHub keys/PATs (SEC-4 test).
3. Forged webhook signature → 401; valid → 200 (contract test).
4. Monorepo registration produces service row with correct path scoping (unit).
5. Invalid `wakey.yml` yields errors with file/line for each problem (unit).
6. All onboarding autonomous-ish actions (key gen/rotation, config snapshot) produce audit lines (unit).
