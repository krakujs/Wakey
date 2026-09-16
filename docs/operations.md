# SPDX-License-Identifier: Apache-2.0
# Wakey Operations Runbook (WF-11, E11)

Daily-operation recipes for a running wakeyd instance. See
[docs/distribution.md](distribution.md) for install/container details.

## Health and status

| Task | Command / check |
|---|---|
| Is the process alive? | `curl :8477/healthz` → `{"status":"ok"}` (no auth: liveness is public) |
| Is it ready to work? | `curl :8477/readyz` → `status`, `services_configured`, `ingest_enabled`, `pending_deliveries` |
| Prometheus scrape | `GET /metrics` — aggregate counters only (public by design; scrape on localhost or via a proxy) |
| Incident board | open `/board` in a browser → log in with a setup token (session lasts 7 days) |
| Local summary | `wakey status` — active fingerprints, forge mode (console dev vs live github) |
| Live rows | `wakey board --service <name>` |
| Pipeline check | `wakey doctor` — runs the local pipeline + live model tier when configured, and states explicitly what it does **not** test (real HTTP, live forge) |

## Dashboard access

1. On first start the banner prints a **setup token** (`WAKE-XXXX-XXXX-XXXX`,
   single use, expires in 30 minutes).
2. Open `/board` → you are redirected to `/login` → paste the token.
3. Lost the token? `wakey setup-token --force` prints a fresh one and
   invalidates the old. Sessions survive restarts; `POST /logout` revokes.

## Registering a service

Dashboard: `/setup` → fill name/repo/environment → **copy the ingest key,
it is shown once**. CLI/API equivalent:

```bash
curl -b cookies.txt -X POST :8477/api/services \
  -d '{"name":"payments-api","repo":"acme/payments","environment":"prod"}'
# → {"service": {...}, "ingest_key": "wk_..."}   # shown once
```

Rotation (old key invalid immediately):

```bash
curl -b cookies.txt -X POST :8477/api/services/payments-api/rotate
```

## Backup and restore (OPS-6)

Backup is an online, consistent SQLite snapshot (safe while wakeyd runs):

```bash
wakey backup                       # → <data-dir>/wakey-backup-<timestamp>.db
wakey backup --out /backups/weekly.db
```

Restore (server must be **stopped**):

```bash
systemctl stop wakey            # or: docker compose stop wakey
wakey restore /backups/weekly.db
systemctl start wakey && wakey doctor
```

`wakey restore` verifies the restored file opens and answers before
declaring success. Recommended cadence: daily backup, one restore drill
per month (the drill is: backup → wipe data dir → restore → `wakey
doctor` exits 0).

## Retention (R-14)

Events and settled deliveries (completed/failed) are pruned at boot and
hourly. Default window: 30 days; configure with `WAKEY_RETENTION_DAYS`.
Disk growth is bounded by the resource budget benchmark (`make bench`);
verify after configuration changes.

## Self-watch (OPS-4)

Set `WAKEY_SELF_WATCH=1` to make wakey watch its own errors: ERROR+ log
records flow through the same pipeline (registered internally as
`wakey-self`). Recursion is guarded — an error while handling an error is
dropped, never looped.

## Background passes (what the runtime runs)

| Pass | Interval | Effect |
|---|---|---|
| Delivery workers | continuous (poll 0.25s) | durable deliveries → parse → redact → policy |
| RCA dispatch | 30s | investigates `queued-rca` fingerprints (hourly budget) |
| Verification | 60s | applies verdicts to `verifying` fingerprints (close/reopen + labels) |
| Lifecycle | 5min | auto-closes silent tickets; recurrence reopens |
| Config reload | 30s | fetches `wakey.yml` from the forge; invalid file keeps last-good |
| Retention | hourly | prunes events + settled deliveries past `WAKEY_RETENTION_DAYS` |

## Connecting GitHub

Two paths:

- **PAT (quick)**: set `WAKEY_GITHUB_TOKEN` + `WAKEY_GITHUB_REPO` (+
  `WAKEY_ALLOWED_REPOS` strongly recommended). Restart; the banner and
  `wakey status` switch from "console forge (dev)" to "live github".
- **GitHub App (preferred)**: log into the dashboard, `GET
  /setup/github/start` returns the pre-filled manifest and creation URL;
  after creating the app, GitHub redirects to
  `/setup/github/callback?code=...` and wakey exchanges + stores the
  credentials **encrypted** (requires `WAKEY_MASTER_KEY`; the callback
  refuses to store the private key unencrypted).
