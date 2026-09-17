<!-- SPDX-License-Identifier: Apache-2.0 -->

# AGENTS.md — operating Wakey as an AI agent

This file is written for AI coding agents (Claude Code, Codex, Gemini CLI,
custom tooling) that install, configure, or operate a Wakey instance. It is
the machine-actionable version of the human docs; every command here is
copy-paste runnable.

## Ground rules

- Never disable the suggest-don't-merge invariant. There is no merge code
  path; do not add one.
- All state is local to the host running wakeyd (SQLite default, Postgres
  optional). Do not exfiltrate logs or credentials.
- Mutating endpoints require an admin session cookie — obtain it via the
  setup token, never by bypassing auth.

## Install and run (development sink)

```bash
git clone https://github.com/krakujs/Wakey && cd Wakey
make install                 # venv + wakey + dev tools
make check                   # gates: format, lint, mypy strict, SPDX, 300+ tests
make serve                   # or: wakey serve   → 127.0.0.1:8477
```

The console prints `first-run setup token (single use, 30 min): WAKE-…`.
Capture it — the dashboard and mutating APIs are locked without it.

## Agent-operable API flow

```bash
BASE=http://127.0.0.1:8477
TOKEN=<token from the serve banner>

# 1. Authenticate (admin session cookie, HttpOnly)
curl -c cookies.txt -X POST $BASE/login -d "token=$TOKEN"

# 2. Register a service (ingest key shown once — persist it)
curl -b cookies.txt -X POST $BASE/api/services \
  -H "Content-Type: application/json" \
  -d '{"name":"my-app","repo":"owner/my-app"}'

# 3. Ship logs (JSON lines, logfmt, or plain text)
curl -X POST "$BASE/ingest/<ingest-key>" --data-binary \
  $'{"level":"error","msg":"connection refused to db"}'

# 4. Read state
curl -b cookies.txt $BASE/board            # live incident board (HTML)
curl -b cookies.txt $BASE/api/services     # registered services (JSON)
curl $BASE/readyz                          # readiness + queue depth (public)
```

## Verification checklist for agents

- `curl $BASE/readyz` → `status: ready`, `pending_deliveries` returns to 0.
- Ingesting the same error 3× opens exactly one ticket (fingerprint dedup).
- Console sink prints `[wakey ticket] …` when no GitHub credentials are set.
- `wakey doctor` exits 0 and lists which stages it actually tested.

## Common agent tasks

| Task | How |
|---|---|
| Rotate dashboard token | `wakey setup-token --force` (prints new token) |
| Rotate an ingest key | `POST /api/services/<name>/rotate` (admin session) |
| Move a service to autonomy=fix | `PATCH /api/services/<name>/config` with the full config JSON |
| Backup (SQLite) | `wakey backup --out <path>` |
| Restore | stop server → `wakey restore <file>` → `wakey doctor` |
| Diagnose a broken install | `wakey doctor` — reports exactly which stages ran |

## CI/CD contract

- `make check` is the gate: ruff format+lint, mypy strict, pytest with
  coverage floor ≥ 90 %, SPDX headers. CI enforces all of it on PRs and
  `main`.
- Postgres integration tests run when a server is reachable; set
  `WAKEYPG_TEST_DSN` (CI provides a postgres:16 service on
  `127.0.0.1:55432`).
- The fix-sandbox gate runs in a capped container: `make gate-sandbox`.

## Never do

- Add a merge code path or auto-merge behavior.
- Store or transmit unredacted secrets (two-pass redaction exists — use it).
- Bypass authentication on mutating endpoints, or ship credentials in code.
- Push real-looking token strings to the repo — use concatenated literals in
  tests (GitHub push protection blocks token-shaped strings).
