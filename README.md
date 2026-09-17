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

🚧 **Alpha.** The complete P1 loop (ingest → ticket → RCA → draft fix PR →
verification) is implemented, tested (300+ tests, 90 % coverage floor), and
verified end-to-end against real GitHub — but the dashboard auth is basic
(setup-token), the fix executor is best suited to Python services, and
auditing the AI tier before production use is on you. See
[docs/threat-model.md](docs/threat-model.md) for the honest security posture.

## Quickstart

### 1. Run the server

**From source (any machine with Python 3.12+):**

```bash
git clone https://github.com/wakey-ai/wakey && cd wakey
make install               # venv + wakey + dev tools
wakey serve                # http://127.0.0.1:8477
```

The console prints a one-time **setup token** — you'll paste it in the
browser to unlock the dashboard (single use, expires in 30 minutes; lost it?
`wakey setup-token --force`).

**With Docker (published image):**

```bash
docker run -d --name wakey -p 8477:8477 \
  -e WAKEY_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
  ghcr.io/krakujs/wakey:0.1.0
```

**Or build locally:**

```bash
docker compose up --build
```

### 2. Connect GitHub

Either set a personal access token (quick start):

```bash
# .env
WAKEY_GITHUB_TOKEN=github_pat_...
WAKEY_GITHUB_REPO=you/your-service
WAKEY_ALLOWED_REPOS=you/your-service     # hard write-allowlist (recommended)
```

…or use the **GitHub App** flow (preferred): sign into the dashboard, open
`/setup/github/start` for a pre-filled manifest, create the app, and the
redirect completes the exchange automatically. Requires `WAKEY_MASTER_KEY`
in `.env` — the app's private key is stored **encrypted** or not at all.

Full walkthrough: [docs/recipes/github-app-setup.md](docs/recipes/github-app-setup.md)

### 3. Register a service and ship logs

Dashboard → **Setup** → register name + repo. You get an ingest key (shown
once). Then point any log source at it:

```bash
curl -X POST "http://localhost:8477/ingest/wk_YOUR_KEY" \
  --data-binary $'{"level":"error","msg":"connection refused to db"}\n{"level":"error","msg":"connection refused to db"}\n{"level":"error","msg":"connection refused to db"}'
```

Accepts JSON lines, logfmt, or plain text. Three identical errors cross the
default threshold — **ticket opens on GitHub**, RCA follows, and the live
board shows the incident.

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
| `WAKEY_MASTER_KEY` | — | Enables AES-256-GCM encryption of stored credentials |
| `WAKEY_WEBHOOK_SECRET` | — | HMAC secret for GitHub webhooks/deploys (required for webhooks) |
| `WAKEY_AUTHORIZED_USERS` | *(deny all)* | Logins allowed to run `@wakey` commands |
| `WAKEY_RETENTION_DAYS` | `30` | Event/delivery retention window |
| `WAKEY_SELF_WATCH` | `false` | Wakey watches its own errors |

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
