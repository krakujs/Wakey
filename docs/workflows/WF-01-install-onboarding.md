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

### Approved default: Wakey-owned App (2026-09-17)

The founder approved Wakey Connect as the default. This supersedes the
user-owned-App default below; manifest creation remains an advanced option.
Implementation is gated on a configured HTTPS Connect endpoint, never an
invented public hostname. No automatic contact with an unpublished service.

Connect uses GitHub's device authorization flow: the local admin starts pairing,
installs the public App on selected repositories, authorizes the displayed code
on github.com, then confirms in Wakey. No arbitrary callback URL, public local
port, PAT, or App private key is required. Org approval may delay connection.
The service checks the authenticated GitHub user's admin access to repositories
in the App's installations before offering those repositories for delegation.
Selecting a repo grants only that repo to this deployment; this initial slice
connects one repository per deployment and must reject cross-repo registration.
Multi-repo runtime routing is a separate acceptance gate, not silently assumed.

The hosted service retains the App signing key; stores only hashed deployment
credentials and encrypted pending device codes; does not collect production
logs. It mints short-lived, explicitly repo-scoped installation tokens and
relays signed GitHub events over outbound polling with durable acknowledgement.
Pairings expire, polling obeys GitHub's interval, and uninstall/suspension or
disconnect revokes access. Webhook content may contain sensitive repo data:
retain only redacted, encrypted, bounded queues; disclose this dependency.

Release gate: GitHub authority checks, wrong-tenant access, replay/restart,
repository narrowing, token renewal, revocation, and real fresh-install flow
must pass before enabling the public service. Initial local integration must
work without restart and never silently fall back to ConsoleForge after pairing.

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

### Managed connector deployment (approved 2026-09-18)

Cloud Run deployment uses a separate `wakey-connect` service and managed
PostgreSQL, never ephemeral SQLite. Shared store transactions serialize admission,
queue caps and compare-and-set changes across replicas. Secrets are mounted from
Secret Manager with access restricted to the connector runtime identity. The App
key must match the configured client ID before provisioning. Infrastructure is
additive and reproducible; existing `wakey` is not replaced. Start with restricted
staging, capped replicas, backups/PITR and monitoring; record live acceptance and
remaining gaps before opening public onboarding. Existing Google key rotation
must preserve consumers and leave no secret values in repo, logs or CLI arguments.

### First-run ergonomics extension (2026-09-17)

`sh start.sh` checks Docker/Compose availability, starts the local Compose service,
waits for health, then prints a fresh one-use login token (invalidating any unused
setup token, not existing sessions). It never installs host packages, edits `.env`,
deletes volumes, or claims GitHub is configured. Failed startup returns nonzero
with a recovery command. Connector hosting remains a separate operator task.
Docker build context excludes secrets, local databases and development caches.

After pairing, service registration pre-fills and locks the connected repository;
fresh login routes to setup (or Connect if configured), existing installations
route to the board.
unpaired Connect deployments return to pairing instead of presenting an unusable
form. The one-time key page provides a ready-to-run synthetic-log curl command
using the configured base URL, with no-store and no-referrer headers. Console
demo mode is explicitly distinguished from real GitHub delivery. A Compose recipe
packages the operator's broker with secret-file mounts and bounded resources,
without inventing a domain or provisioning public infrastructure.

1. Fresh install → doctor green ≤10 min following only README instructions (manual AC at gate M0; automated: doctor stages pass in CI with fakes).
2. Credential store dump contains no plaintext GitHub keys/PATs (SEC-4 test).
3. Forged webhook signature → 401; valid → 200 (contract test).
4. Monorepo registration produces service row with correct path scoping (unit).
5. Invalid `wakey.yml` yields errors with file/line for each problem (unit).
6. All onboarding autonomous-ish actions (key gen/rotation, config snapshot) produce audit lines (unit).
