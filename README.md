<!-- SPDX-License-Identifier: Apache-2.0 -->

# 🌙 Wakey

**The self-hosted AI SRE that wakes up when your errors do.**

Wakey watches your services' logs, and the moment something breaks it opens
a GitHub ticket, investigates the root cause with your AI model, and opens a
**draft pull request with a suggested fix** — proven against your own test
suite before you ever see it. Human review required: **Wakey never merges
anything.**

- **Self-hosted & open source** — Apache-2.0, runs on a $5 VPS
- **No SDK changes** — point any log source at the HTTP ingest endpoint
- **Forge-first** — tickets and fixes live in GitHub (GitLab via ForgePort on
  the roadmap); GitLab/Bitbucket adapters are pluggable
- **AI-optional** — works without any model keys; add one (any
  Anthropic-compatible endpoint) to upgrade RCA and enable fix proposals

## Status

🚧 **Alpha, not production-certified.** Monitoring, ticketing, RCA and draft-fix
components exist; remaining product and release gates are tracked in
[STATE.md](docs/STATE.md). The new **Wakey Connect** flow uses our shared GitHub
App without giving your deployment the App private key. A managed connector is
deployed and smoke-tested in **IAM-restricted staging**, not open for public
onboarding yet. See the [GCP launch runbook](docs/recipes/wakey-connect-gcp.md).
Do not assume the older `0.1.0` image includes these working-tree changes.
See the [2026-09-18 readiness check](docs/readiness-check-2026-09-18.md)
for fresh live connection evidence, launch blockers and ordered next steps.

## Quickstart

### 1. Run the server

**Recommended: Docker Compose** (Git + Docker with Compose required):

```bash
git clone https://github.com/krakujs/Wakey.git
cd Wakey
sh start.sh
```

The helper checks Docker, builds and waits for a healthy server, then prints
a fresh **setup token**. Open **http://localhost:8477** and enter it. The token
expires after 30 minutes. Re-running the helper preserves data and sessions but
replaces any unused setup token. It never edits `.env` or installs host software.
To generate another token without restarting:

```bash
docker compose exec wakey wakey setup-token --force
```

The named `wakey-data` volume retains your database and automatically generated
encryption key across container replacement. **Do not delete that volume** unless
you intend to reset Wakey. Back up the key alongside the database; production
operators should instead supply `WAKEY_MASTER_KEY` from their secret manager.
Compose exposes the dashboard only on localhost by default.

On a remote server, open a tunnel from your laptop:
`ssh -L 8477:127.0.0.1:8477 your-user@your-server`, then visit localhost on your
laptop. Log senders outside the host need a reachable HTTPS ingest endpoint;
the tunnel alone does not connect production logs. Keep Wakey behind TLS and
authentication; don't expose its plaintext port to the internet.

**Developer alternative** (Python 3.12+, `make`, Linux):

```bash
make install
make serve
```

### 2. Connect GitHub

**Default: our Wakey GitHub App.** Once the hosted service is available, open
**Connect GitHub** in the dashboard:

1. Install the Wakey App on selected repositories (organization approval may be required).
2. Authorize the displayed temporary code on GitHub.
3. Click **I’ve authorized — check connection**, select a repository, then connect.

No PAT, App private key, public webhook port or restart is required. This first
version connects **one repository per deployment** and requires repository-admin
access. Production logs remain local; redacted GitHub webhooks pass through the
hosted connector. See [Wakey Connect](docs/recipes/wakey-connect.md) for the trust
boundary and independent-hosting alternative.

**Current development builds:** the operator must set `WAKEY_CONNECT_URL` to a
real, approved HTTPS connector endpoint before this option can connect. There is
no default public endpoint yet. Until deployment, use BYO-App or a PAT; the UI
will not pretend it is connected.

**Advanced: personal access token.** Put these in `.env` **before starting**;
if already running, recreate the container with `docker compose up -d`:

```bash
# .env
WAKEY_GITHUB_TOKEN=github_pat_...
WAKEY_GITHUB_REPO=you/your-service
WAKEY_ALLOWED_REPOS=you/your-service
```

**Advanced: bring your own GitHub App.** Sign into the dashboard, open
`/setup/github/start` for a pre-filled manifest, create the app, and the
redirect exchanges credentials. Install the App, set `WAKEY_GITHUB_REPO`, then
restart Wakey to activate this advanced connection. Stored private keys require
encryption; Compose's automatic key supplies it, or set `WAKEY_MASTER_KEY`.

Full walkthrough: [docs/recipes/github-app-setup.md](docs/recipes/github-app-setup.md)

### 3. Register a service and ship logs

Dashboard → **Setup** → register your service. With Connect, the repository and
a suggested service name are filled in. You get an ingest key (shown once) and
a ready-to-run sample command. Copy that command to verify your first signal,
then point your production log shipper at the same endpoint. For reference:

```bash
curl -X POST "http://localhost:8477/ingest/wk_YOUR_KEY" \
  --data-binary $'{"level":"error","msg":"connection refused to db"}\n{"level":"error","msg":"connection refused to db"}\n{"level":"error","msg":"connection refused to db"}'
```

Accepts JSON lines, logfmt, or plain text. Three identical errors cross the
default threshold. With a working GitHub connection, a ticket opens and RCA
follows; without one, only the console demo runs. HTTP 202 confirms queued
delivery, not a completed GitHub action. Check the incident board for the result.

**For Wakey maintainers:** the shared App needs a hosted Connect service before
users can follow the default flow. The [operator launch guide](docs/recipes/wakey-connect.md)
includes the ready-to-run broker Compose file and the remaining live acceptance
checks. Customers should never configure that broker or handle its App keys.

## How it works

```text
logs ──▶ ingest (HMAC/OIDC) ──▶ durable queue ──▶ redact ──▶ fingerprint
                                                                  │
             ┌────────────────────────────────────────────────────┘
             ▼
        policy gate ──▶ 🎫 GitHub ticket ──▶ 🔎 RCA (model, redacted prompts)
                                                │
                                   repro-first fix loop (sandboxed)
                                                │
                                     📝 draft PR (tested tree committed)
                                                │
                              merge + deploy event ──▶ 🔁 verification window
                                                │
                                    silent ✔ close   /   recurred ↺ reopen
```

**Suggest-don't-merge is an invariant, not a setting.** Wakey never merges,
never force-pushes, and every autonomous action lands in a tamper-evident,
hash-chained audit log.

## Configuration

All configuration is environment (`WAKEY_*`, see
[.env.example](.env.example)) plus per-service `wakey.yml`:

| Setting | Default | What it does |
|---|---|---|
| `WAKEY_PORT` | `8477` | HTTP port (dashboard binds **127.0.0.1** by default) |
| `WAKEY_GITHUB_TOKEN` / `WAKEY_GITHUB_REPO` | — | PAT + target repo; without them Wakey uses a console dev sink |
| `WAKEY_ALLOWED_REPOS` | — | Hard write-allowlist, comma separated (strongly recommended) |
| `WAKEY_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | — | Any Anthropic-compatible endpoint (GLM, Claude, gateways) |
| `WAKEY_RCA_MAX_PER_HOUR` / `_MAX_TOKENS_PER_HOUR` | `30` / `30000` | Hard hourly RCA call and reserved-token ceilings |
| `WAKEY_MASTER_KEY` | — | Enables AES-256-GCM encryption of stored credentials |
| `WAKEY_AUTO_MASTER_KEY` | `false` (`true` in Compose) | Generate/reuse an owner-only key in the persistent data directory |
| `WAKEY_CONNECT_URL` | — | Approved HTTPS Wakey Connect endpoint; empty keeps independent mode |
| `WAKEY_WEBHOOK_SECRET` | — | HMAC secret for GitHub webhooks/deploys (required for webhooks) |
| `WAKEY_AUTHORIZED_USERS` | *(deny all)* | Logins allowed to run `@wakey` commands |
| `WAKEY_RETENTION_DAYS` | `30` | Event/delivery retention window |
| `WAKEY_SELF_WATCH` | `false` | Wakey watches its own errors |
| `WAKEY_SANDBOX_IMAGE` | — | Pre-provisioned isolated test image; fixes fail closed without a working executor |

Per-service behavior (autonomy dial `observe/triage/digest/fix`, thresholds,
caps, `test_command`) comes from a `wakey.yml` in the service repo — unknown
keys are rejected, not ignored. See
[docs/workflows/WF-01-install-onboarding.md](docs/workflows/WF-01-install-onboarding.md).

## The autonomy dial

| Mode | Tickets | RCA | Fix PRs |
|---|---|---|---|
| `observe` | ✅ | ❌ | ❌ |
| `triage` (default) | ✅ | ✅ | only via explicit `@wakey fix` |
| `fix` | ✅ | ✅ | autonomous within caps |

Even in `fix`, a fingerprint that came back from a failed fix is marked
**chronic** and blocked from another autonomous attempt until a human
acknowledges it.

## CLI

```bash
wakey serve                 # server + workers + RCA dispatch
wakey status                # fingerprints, forge mode
wakey board                 # live incident board
wakey doctor                # staged pipeline check — reports exactly what it tested
wakey setup-token --force   # fresh dashboard token
wakey backup / restore      # SQLite backup + verified restore
```

## Testing & gates

```bash
make check          # format + lint + mypy strict + SPDX + full suite
make test           # capped parallelism (host-safe)
make bench          # resource benchmark vs budgets
make gate-sandbox   # fix-sandbox negative tests in a capped container
make demo-two-services / demo-m0   # end-to-end demos
```

Coverage floor is 90 % and enforced in CI. Fix-executor sandbox negative
tests run inside a network-less, memory/CPU-capped container.

## Documentation

| Doc | Contents |
|---|---|
| [docs/operations.md](docs/operations.md) | Runbook: health, backup/restore, retention, background passes |
| [docs/recipes/wakey-connect.md](docs/recipes/wakey-connect.md) | Shared-App onboarding, operator setup, privacy, limits and release checklist |
| [AGENTS.md](AGENTS.md) | Machine-actionable guide for AI agents operating Wakey |
| [docs/threat-model.md](docs/threat-model.md) | Security posture, assets, controls, accepted risks |
| [docs/security-review.md](docs/security-review.md) | Control checklist with test evidence |
| [docs/workflows/](docs/workflows/) | Behavioral specs WF-01…WF-15 (ingest, RCA, fixes, verification…) |
| [docs/recipes/](docs/recipes/) | GCP Cloud Logging, deploy events, GitHub App setup |
| [docs/engineering-standards.md](docs/engineering-standards.md) | The production-grade bar this repo is held to |

## Contributing

Issues and PRs welcome — start with
[CONTRIBUTING.md](CONTRIBUTING.md) (DCO sign-off, `make check`, small PRs).
Security issues: see [SECURITY.md](SECURITY.md) — please don't open public
issues for them.

## License

[Apache-2.0](LICENSE) © The Wakey Authors
