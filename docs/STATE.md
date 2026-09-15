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
| E14 | Legacy estate management (LEG-*) | todo | P2 portion after M3; P3 portion after GA |
| E15 | Write-path monitoring ("Ponytail", WPM-*) | todo | P3, after GA |
| E16 | Plugin platform & BYO-AI (EXT-6..10) | todo | T1..T3 gate on M3 (GA); T4..T5 with E15 |

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
| 2026-09-15 | Design system adopted (docs/design.md): Composio-structured tokens, Wakey twist = "night watch" brand — blue-black canvas, scarce Signal Amber accent with dark-on-amber CTAs, Night Watch Grid signature, Pulse motif, product-native components (autonomy dial, confidence meter, status pills) | Founder: base UI on Composio design.md with our own twists; differentiation from blue/purple AI brands; amber scarcity mirrors product truth (light = action) | Keeping Composio blue (undifferentiated); green "pulse" accent (reads generic-healthy) |
| 2026-09-15 | ui-ux-pro-max skill adopted as the UI implementation-intelligence layer; its guardrails (44px touch targets, focus rings, z-index scale, loading states, SVG-only icons, 4.5:1 contrast) are binding in docs/design.md; its generic palette suggestion (slate+green) rejected — brand tokens win | Craft rules are objective and worth binding; palette is a brand decision already made | Adopting skill's slate+green design system |
| 2026-09-15 | Added legacy estate-management layer (LEG-1..8, §13 of feature inventory, epic E14): EOL radar, health register/risk scoring, golden-master safety nets, system atlas, ownership/orphan detection, data-layer signals, decommission assist, auto-postmortems | Founder asked what's missing for companies managing running legacy projects; incident loop alone doesn't manage a *portfolio* (aging runtimes, ownerless services, no tests, undocumented systems, retirement) | Narrower candidates folded in: change-freeze calendar (→ DET-8 extension), compliance evidence pack (→ SEC-5/LEG-8), license scanning (→ ING-9/LEG-1) |
| 2026-09-15 | GUI + CLI specced as first-class P1 surfaces (WF-12, WF-13): dashboard at server start (banner → auto-open → one-time token → wizard), 8 pages, read-mostly (GitHub stays the decision UI); `wakey` CLI core moves from P2 to P1 with a scripting contract (JSON output, exit codes 0/1/2/3/4, `--yes` safety); CLI/GUI are two skins over one API — CLI deliberately has no incident-decision commands | Founder requirement: GUI on server start + CLI to manage; keeps SKILL invariant "GitHub decides" intact; scriptability is table stakes for server software | fat client / separate admin UI; CLI that can trigger fixes (violates trust model) |
| 2026-09-15 | Write-path monitoring adopted as planned P3 direction — the **"Ponytail" plugin** (WPM-1..4, §15, epic E15): coding-agent/IDE plugin monitoring code as written, write-time risk checks, write→runtime attribution, PR-time gate. Binding rule (SKILL.md §9): event spine stays source-plural, attribution metadata reserved | Founder direction; moves Wakey upstream of production and pairs with the AI-codes-more/breaks-more market thesis | Treating it as a separate product (dilutes focus); blocking editors (wrong trust model) |
| 2026-09-15 | **Extensibility as a core promise** (WF-14, EXT-6..10, epic E16): users can add any plugin (manifest + sandboxed, capability-scoped, crash-isolated; points: source/enricher/detector/responder/notifier/panel/command) and configure **any AI tool** (BYO-AI profiles binding any provider or OpenAI-compatible endpoint to task tiers; swap = config, never code). Binding rule (SKILL.md §6): AI is vendor-neutral by construction — only the model router speaks vendor protocols | Founder requirement: any plugin, any AI tool; openness is the moat (docs/01 thesis), and BYO-AI incl. local models doubles as the privacy story | Fixed vendor list; in-process plugins (crash-risk); blocking editors |
| 2026-09-15 | Never build: auto-merge, chat UI, heavy agent frameworks | Focus; auditability (docs/04 "Not doing") | — |

## Open decisions (blocking work — resolve before the listed epic)

| 5 | **Confirm "Ponytail"**: is it our codename for the write-path monitoring plugin (current assumption, SKILL.md §9 / §15 WPM-*), or an existing third-party product to integrate? | E15 (P3, not near-term) | founder | before E15 planning |
| 6 | **Plugin sandbox technology**: out-of-process workers (subprocess/containers) vs WASM modules for EXT-6 — decide with a spike at E16 start | E16 | epic owner | before E16-T1 |
| 1 | P1 connector trio confirmation: GCP + generic webhook + docker sidecar (ING-2/3/4 in, ING-5/6 deferred to P2) | E4 | founder | before E4 start |
| 2 | Legacy language mix priority for traceback parsers (currently Py/Java/JS/PHP for P1) | E5 | founder | before E5 start |
| 3 | Digest/approval mode (FIX-9) in P1 (current plan) or P2 | E8 | founder | before E8 start |
| 4 | Local-LLM (SEC-3) day-one first-class or fast-follow | E7 | founder | before E7 start |

## Blockers / risks currently open

- None. (Add rows as `R-<n>` with mitigation owner.)

## Session log (most recent first, one line per session)

- 2026-09-15 — Planning package complete: SKILL.md, engineering standards, task backlog (E1–E13), 11 workflow specs, feature inventory, competitive research. No code written (per founder: plan first).
