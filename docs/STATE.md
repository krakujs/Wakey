# Wakey — Project State

> **Living file.** Every session: read this first, update it last. Statuses: `todo / in-progress / review / done / blocked:<reason>`. Task IDs refer to `docs/TASKS.md`; feature IDs to `docs/05-feature-inventory.md`.

## Current position

- **Mode**: 🔒 **PLANNING-ONLY** — locked by the founder on 2026-09-15. **No production code, scaffolding, or implementation may be started until the founder explicitly says to start development.** Planning/refinement of docs is allowed and expected. (Skipped: E1 scaffold earlier in the session — per founder instruction, nothing gets built.)
- **Phase**: Planning complete → **P1 build not started** (E1 is the first epic when the lock is lifted).
- **Next gate**: M0 — "real GCP error → deduplicated GitHub ticket with deploy correlation" (see SKILL.md §5).
- **Next recommended task** (when unlocked): E1-T1 (repo scaffold + CI), then E2-T1.

## Epic board

| Epic | Name | Status | Notes |
|---|---|---|---|
| E1 | Foundation & repo hygiene | todo | First |
| E2 | Core platform (config/models/storage/server) | todo | After E1 |
| E3 | Onboarding & GitHub integration | todo | After E2 |
| E4 | Ingestion & connectors | todo | Parallel with E3 after E2 |
| E5 | Detection & fingerprinting | todo | After E4 |
| E6 | Ticketing | todo | After E3+E5 |
| E7 | RCA agent | todo | After E6 → gate M1 |
| E8 | Fix agent | todo | After E7 |
| E9 | Verification | todo | After E8 → gate M2 |
| E10 | Security hardening | todo | Continuous from E2 |
| E11 | Operations | todo | Continuous from E2 |
| E12 | P2 GA breadth | todo | After M3 |
| E13 | P3 Moat | todo | After GA |

## Task detail

See `docs/TASKS.md` for the authoritative per-task status. (Keep this table epic-level; TASKS.md carries task-level truth.)

## Gates evidence log

| Gate | Date | Evidence (what ran, where, link/notes) |
|---|---|---|
| M0 | — | not attempted |
| M1 | — | not attempted |
| M2 | — | not attempted |
| M3 | — | not attempted |

## Decision log (append-only)

| Date | Decision | Why | Alternatives rejected |
|---|---|---|---|
| 2026-09-15 | Open-source + self-hosted first; user-owned GitHub App via manifest (not vendor-operated app) | Data stays in-house; standard OSS trust pattern | Vendor-hosted app (easier install, wrong trust model) |
| 2026-09-15 | GitHub is the only UI in v1; dashboard is setup/status only | Teams already live in GitHub; less surface to build/secure | Standalone issue UI |
| 2026-09-15 | Suggest-don't-merge; autonomy dial observe→triage→fix | Trust is the product; matches founder requirement | Auto-merge for high confidence (rejected: kills trust) |
| 2026-09-15 | Python 3.12+, FastAPI, SQLite→Postgres | Ecosystem fit for logs/LLM; simple self-host path | Node/TS stack |
| 2026-09-15 | Fix agent is repro-first; diff spot-check verifier | Biggest levers on fix quality/trust (docs/04 A1, A4) | Patch-only agent |
| 2026-09-15 | **Planning-only mode until founder explicitly says to start development** — applies to all concurrent/future requests | Founder directive; plan everything before building | Starting E1 scaffold early (rejected) |
| 2026-09-15 | **Local version control only — never push to GitHub or add any remote** | Founder directive; project stays private/local for now | Adding origin remote (rejected) |
| 2026-09-15 | Never build: auto-merge, chat UI, heavy agent frameworks | Focus; auditability (docs/04 "Not doing") | — |

## Open decisions (blocking work — resolve before the listed epic)

| # | Decision | Blocks | Owner | Deadline |
|---|---|---|---|---|
| 1 | P1 connector trio confirmation: GCP + generic webhook + docker sidecar (ING-2/3/4 in, ING-5/6 deferred to P2) | E4 | founder | before E4 start |
| 2 | Legacy language mix priority for traceback parsers (currently Py/Java/JS/PHP for P1) | E5 | founder | before E5 start |
| 3 | Digest/approval mode (FIX-9) in P1 (current plan) or P2 | E8 | founder | before E8 start |
| 4 | Local-LLM (SEC-3) day-one first-class or fast-follow | E7 | founder | before E7 start |

## Blockers / risks currently open

- None. (Add rows as `R-<n>` with mitigation owner.)

## Session log (most recent first, one line per session)

- 2026-09-15 — Planning package complete: SKILL.md, engineering standards, task backlog (E1–E13), 11 workflow specs, feature inventory, competitive research. No code written (per founder: plan first).
