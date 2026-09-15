# WF-13 — CLI (`wakey`)

> Behavioral contract for the command-line interface. Features: OPS-8, ONB-7, SEC-5/7, OPS-6 · Tasks: E11-T7 (P1 core), E12-T17 (P2 full). The CLI wraps the same REST surface as the GUI (OPS-7) — one brain, two skins.

## Purpose & principles

Companies running servers live in terminals and scripts. The CLI is: (1) the operator's remote control for daily management, (2) the automation/CI entry point (everything scriptable), (3) the rescue tool when the GUI can't help (headless boxes, broken dashboards). Principles:

1. **Everything the GUI does, the CLI does** (minus rendering state) — parity is a release requirement, not an aspiration.
2. **Machine-readable by default option**: every command supports `--output json|table|quiet`; same exit codes; stable JSON schema (documented, additive-only changes).
3. **Safe by default**: mutating commands print a plan and require `--yes` (or TTY confirmation); read commands never mutate.
4. **No magic**: no hidden state, no different config path than the server; the CLI talks to wakeyd's API, it doesn't read the DB.

## Invocation & auth

- Binary: `wakey`, shipped inside the wakey docker image (`docker compose exec wakey wakey …`) and as a standalone binary/pipx package for hosts.
- **Local mode (default)**: reads the loopback admin token from the data dir (`<data_dir>/cli-token`, mode 0600, created at boot) and talks to `http://127.0.0.1:$WAKEY_PORT`. No prompts.
- **Remote mode**: `--host https://wakey.corp.example --token …` or `WAKEY_HOST`/`WAKEY_TOKEN` env (for CI/scripts); token = admin API key minted in Settings (P2, OPS-7).
- Global flags: `--config` (server selection file), `--output`, `--yes`, `--timeout <s>`, `--no-color`, `--quiet`.

## Command tree

### Lifecycle & rescue (P1)
```
wakey start [--port N] [--no-open]      # start daemon (foreground or detached); prints WF-12 banner
wakey stop                              # graceful stop; drains queue (WF-11)
wakey status                            # one-glance: version, uptime, degradation rung, queue depth, services ok/total
wakey logs [-f] [--since 1h] [--level]  # structured server logs (human-rendered)
wakey doctor [--service X]              # WF-01 §A5 pipeline check; exit code = result
wakey setup-token                       # mint a fresh single-use first-run token (WF-12)
```

### Services & config (P1)
```
wakey services list                     # table: service, repo, autonomy, 24h fps, state
wakey services add <owner/repo> [--path p] [--autonomy triage] [--env prod]
wakey services show <name>              # config snapshot, ingest heartbeat, key fingerprint, budgets
wakey services rotate-key <name>        # prints new key once; confirms
wakey services set <name> autonomy=fixed thresholds=...   # explicit key=value config edits
wakey config validate [--repo r]        # validate wakey.yml offline or server-side; errors with file:line
```

### Fingerprints & incidents (P1)
```
wakey fps list [--service s] [--state new|open|suppressed|chronic] [--sev sev-high] [--since 24h]
wakey fp show <id>                      # counts, timeline, linked ticket/PR, RCA summary, cost
wakey fp suppress <id> --for 7d --reason "accepted noise"   # same path as GUI/`@wakey drop` (DET-7)
wakey fp wake <id>                      # test/manual wake: runs policy gate as if events arrived (dry)
```

### Operations (P1 core / P2 full)
```
wakey budget show|set --service s --monthly-usd N   # SEC-7 budgets (P1)
wakey caps show                         # effective caps & current usage (P1)
wakey replay <dead-letter-id>           # re-ingest a dead-lettered batch (P2, ING-12)
wakey backup [--out path] / wakey restore <path>    # WF-11 §6 (P2 binary, P1 documented via compose)
wakey audit tail [-f] [--actor agent]   # stream the audit log (P2; P1 reads file via `wakey logs`)
wakey open                              # prints/opens the dashboard URL (P1)
wakey version                           # version + build info
```

### Deliberately absent
No commands to create incidents, trigger fixes, merge/review PRs, or approve digests — those are GitHub's, by design (SKILL.md invariant 1; `@wakey` commands live in WF-07). The CLI can *observe* all of it and *configure* the policies that govern it.

## Output & exit-code contract

- **Table output**: human tables using the terminal voice of design.md (status-pill vocabulary, amber only for attention/action states in color mode; `--no-color` must lose no information — status is text, color is reinforcement).
- **JSON output**: `--output json` emits `{ "ok": true, "data": …, "warnings": [...] }`; schemas versioned (`"schema": "wakey.cli.fps.v1"`), additive-only evolution; suitable for CI andjq/crum consumption.
- **Exit codes**: `0` success · `1` operation failed (details on stderr) · `2` usage error · `3` succeeded but degraded (e.g., `doctor` passes with warnings, `status` while degradation ladder ≠ L0) · `4` auth/token rejected.
- **Non-TTY behavior**: no interactive prompts, no color, tables become TSV unless `--output json|table` explicit — safe for cron and pipes.
- **Help**: every command has `--help` with a realistic example; top-level `wakey --help` doubles as a quickstart card.

## State written

Nothing directly — the CLI mutates through the server's validated API/config paths (same validation, same audit lines, same confirmations; `--yes` fulfills the confirmation, which is still audited with actor=`cli:<user>`). Local writes: cached credentials file only (`~/.config/wakey/credentials`, 0600).

## Failure modes

| Failure | Expected behavior |
|---|---|
| Server unreachable | One clear error line with the host it tried + `wakey status --help` hint; exit 1; never hangs past `--timeout` (default 10s) |
| Token rejected/expired | Exit 4 with re-mint instructions (`wakey setup-token`, Settings → API keys) |
| Mutating command without `--yes` on non-TTY | Refuse with the plan that *would* have run (dry-print), exit 2 — scriptable but never accidental |
| Ambiguous service/fp id | List matches with ids, exit 2 (no guessing) |
| Partial failures in batch-shaped output (e.g., `services list` while one service errors) | Render what's known, mark failed rows, exit 3, warnings populated in JSON |
| Old CLI vs newer server | Capability negotiation via server version; unknown commands → clear upgrade message; JSON schema additive rule keeps old scripts working |

## Acceptance criteria

1. Parity test: for every GUI mutation action there's a CLI equivalent asserting the same API call and audit-line shape (contract test over the action registry).
2. `wakey doctor` exit codes: 0 pass / 1 fail / 3 warn — asserted with fakes (integration).
3. Non-TTY safety: every mutating command without `--yes` refuses with a dry-printed plan, exit 2 (property test over command registry).
4. JSON schemas: snapshot tests; breaking-change detector in CI fails any non-additive schema diff.
5. First-run UX: fresh install → `wakey start` → banner → `wakey status` works with zero prompts (e2e with real daemon in CI).
6. `--no-color` and non-TTY outputs contain identical information to colored TTY (golden-file).
7. All commands complete ≤ `--timeout`; no command hangs on unreachable host (integration).
