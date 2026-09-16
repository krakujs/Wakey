# Wakey — Project State

> **Living file.** Every session: read this first, update it last. Statuses: `todo / in-progress / review / done / blocked:<reason>`. Task IDs refer to `docs/TASKS.md`; feature IDs to `docs/05-feature-inventory.md`.

## Current position

- **Mode**: 🚧 **DEVELOPMENT** — unlocked by the founder on 2026-09-15 ("Start the development please"). Planning docs remain binding (SKILL.md §2); behavior changes still update specs in the same change.
- **Phase**: **P0 — Foundation (Wave 0)** · active task: **E1-T1** (repo scaffold), L-P lane, single-agent (integrator) mode — worktree protocol applies when parallel agents join.
- **Next gate**: M0 — "real GCP error → deduplicated GitHub ticket with deploy correlation" (see SKILL.md §5).
- **Next up after E1-T1**: E1-T2 (CI), E1-T6 (safe test runner), then E2.

## Epic board

| Epic | Name | Status | Notes |
|---|---|---|---|
| E1 | Foundation & repo hygiene | **in-progress** | E1-T1 review (local green); E1-T3 **done** (verified live); E1-T2/T6 in-progress; rest todo |
| E2 | Core platform (config/models/storage/server) | **in-progress** | E2-T1..T4 **done** (28 tests green); E2-T5 (queue) next |
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
| 2026-09-15 | **Execution model: phases + parallel lanes + worktrees + G-gate** (docs/execution-plan.md): 3 lanes (L-P platform/ops, L-G GitHub/interaction, L-D data/detection) with disjoint module ownership; P1 scheduled in 8 waves; agents work in per-task git worktrees (`agent/<name>/<task>-<slug>` branches, commit-trailer progress, integrator merges waves, shared docs edited only at merges); **G-gate** = GitHub-integration release bar (contract fixtures every PR, live sandbox suite nightly + ≤14 days before any release, webhook robustness, failure drills, least-privilege proof), harness built as E3-T8 | Founder requirements: GitHub properly implemented & tested before release, proper phases, parallel agents, worktree working mode | Free-for-all parallelism (merge hell); agents editing shared STATE/TASKS (collisions); GitHub tested only by mocks (would ship broken) |
| 2026-09-15 | **Resource frugality is a release requirement** (NFR-6..9, eng-standards §8, tasks E2-T6/E4-T10/E11-T8): CI-enforced budgets (idle ≤120MB, steady RSS ≤350MB @5M events/day on 1 vCPU/1GB, ≤25MB/day disk, boot <5s, image ≤200MB), lean mode for RPi/small-VPS, aggregate-don't-retain storage, lazy imports, batched I/O, server-rendered frontend, LLM frugality | Founder requirement: run on least possible resources; a watcher must be a good tenant on someone's box — "runs on a $5 VPS" is also a self-hosted OSS selling point | Rewriting in Go/Rust (premature; Python budgets are achievable at our event rates and the ecosystem wins outweigh); heavy SPA dashboard |
| 2026-09-15 | **Forge-neutral core** (WF-15, FORGE-1..6, epic E17): ForgePort abstraction built in P1 with GitHub as reference adapter; GitLab adapter + `forge` plugin point in P2; Gitea/Forgejo, Bitbucket, Azure DevOps in P3. Binding rule (SKILL.md §6): forge SDKs/APIs only inside adapters (import-linter enforced); G-gate generalizes to a per-forge gate | Founder requirement: connect with any GitLab/other VCS *easily*; building E6/E8 directly on GitHub's API would make multi-forge a rewrite — the abstraction must exist before the loop is built on it | GitHub-only core with "later migration" (guaranteed rewrite); building all forges day one (boil the ocean) |
| 2026-09-15 | **Public Docker distribution planned** (docs/distribution.md): ghcr.io/wakey-ai/wakey primary + Docker Hub mirror; tags X.Y.Z/X.Y/1/sha; multi-arch non-root image ≤200MB with SBOM+provenance; README quickstart (docker run one-liner + compose + .env) prepared and kept byte-identical to repo compose via CI drift test; E1-T4 extended accordingly | Founder requirement: anyone can pull and start using Wakey almost immediately; drift test keeps docs honest | README-only compose (drifts from repo); latest-only tags (unpredictable upgrades); single registry |
| 2026-09-15 | **Licensing made concrete**: Apache-2.0 LICENSE file added (was planned-only); THIRD-PARTY-NOTICES.md with auto-regen policy + license-compatibility review rule; README legal section (copyright "The Wakey Authors" placeholder, DCO contributions inbound=outbound, SPDX headers automated in E1-T1, trademark placeholder pending branding check) | Founder: add all license and everything to README/license files; Apache-2.0 was already the decision (docs/02) | CLA (friction without benefit at this stage); GPL-family (incompatible with permissive ecosystem goals) |
| 2026-09-15 | **Continuous quality + host-safe testing** (eng-standards §3, execution-plan §6, task E1-T6): commit→check / wave→full suite / nightly benchmark+eval+live-sandbox cadence; zero-known-bugs policy (regression test per bug, no gate exits with open bugs, coverage floors ≥90%/95%); all testing resource-capped by default (timeouts, ≤4 workers, containerized benchmarks, rate-limited load generators, three never-rules); every phase gate runnable locally via `make gate-*` | Founder requirements: continuous production-grade development, no bugs, every phase tested locally, testing must not crash the PC | "Fix bugs later" batching (bugs compound); metal load tests (host risk); CI-service-dependent verification (breaks local-first) |
| 2026-09-15 | **Senior-craft directive encoded** (SKILL.md §6, eng-standards §2): work as a 10-year-experienced engineer — small single-purpose modules (~≤400 lines) and functions (~≤50), explicit names, boring-over-clever, no dead code/TODOs-without-task-ids/dangling stubs, self-review before review status; canonical project structure tree pinned in eng-standards §2 (authoritative; docs/02 older layout line now defers to it); documentation structure rules (numbered docs, WF-slug workflow files, no orphan/duplicate docs) | Founder directive: no messy code, docs and code well structured; structure disputes resolved by editing the canonical section first | "Clean code is subjective" (it isn't — the rules above are checkable); letting structure emerge (becomes messy under parallel lanes) |
| 2026-09-15 | **Edge-case sweep + work-state machine + live board**: docs/edge-cases.md register (EC-01..27); new P1 features DET-14 (work-state machine, in-flight suppression — never re-trigger an error already being worked on) and OPS-11 (live incident board: every error → work item → live status, dashboard + `wakey board`); P2: DET-15 related-fingerprint families, DET-16 cardinality explosion guard, VER-6 external-fix credit, board live updates. Tasks E6-T5, E11-T9 (P1), E12-T27..30 (P2); backlog now 140 | Founder: no duplicate triggers for already-worked errors; task board with live statuses for everyone | Relying on implicit dispatch caps (was ambiguous); separate "board" product (board = aggregation of existing state, not a new source of truth) |
| 2026-09-15 | Founder requested a local two-dummy-service test run; **not executed** — planning-only lock stands and no code exists. Delivered docs/two-service-local-test.md instead (becomes `make demo-two-services` + E1-T5 fixtures at dev start). Keys stance made policy (SKILL.md §8): no credential extraction from other tools' stores; demo needs no LLM keys (fake tier) | Respect standing planning-only lock; request ambiguous as an unlock; pipeline test factually needs no LLM | Treating the request as development unlock (not explicit enough); scraping Claude Code key storage (security violation) |
| 2026-09-15 | Never build: auto-merge, chat UI, heavy agent frameworks | Focus; auditability (docs/04 "Not doing") | — |

## Open decisions (blocking work — resolve before the listed epic)

| 5 | **Confirm "Ponytail"**: is it our codename for the write-path monitoring plugin (current assumption, SKILL.md §9 / §15 WPM-*), or an existing third-party product to integrate? | E15 (P3, not near-term) | founder | before E15 planning |
| 6 | **Plugin sandbox technology**: out-of-process workers (subprocess/containers) vs WASM modules for EXT-6 — decide with a spike at E16 start | E16 | epic owner | before E16-T1 |
| 7 | **Forge priorities beyond GitLab**: which forges matter for the target audience first — Gitea/Forgejo vs Bitbucket vs Azure DevOps (current plan: Gitea/Forgejo next, as the natural self-hosted pairing) | E17-T5..T6 | founder | before GA |
| 1 | P1 connector trio confirmation: GCP + generic webhook + docker sidecar (ING-2/3/4 in, ING-5/6 deferred to P2) | E4 | founder | before E4 start |
| 2 | Legacy language mix priority for traceback parsers (currently Py/Java/JS/PHP for P1) | E5 | founder | before E5 start |
| 3 | Digest/approval mode (FIX-9) in P1 (current plan) or P2 | E8 | founder | before E8 start |
| 4 | Local-LLM (SEC-3) day-one first-class or fast-follow | E7 | founder | before E7 start |

## Blockers / risks currently open

- None. (Add rows as `R-<n>` with mitigation owner.)

1. ✅ E10-T1 redaction engine — done, committed (13 corpus tests)
2. ✅ E5-T1/T2 fingerprint engine — done, committed (all 4 P1 languages: python/java/js/php; 11 tests)
3. ✅ E2-T5 key-partitioned bounded queue + worker pool — done (4 tests)
4. ✅ E4-T2 normalizers — done (6 tests)
5. ✅ E5-T5 policy gate — done (6 tests; rate windows -> E5-T3)
6. ✅ E17-T1 ForgePort + console adapter — done (lite; GitHub adapter refactor pending)
7. ✅ E4-T1 ingest endpoint + HMAC webhook auth (migration v2) — done core; 429/queue wrap + rate limits remain
8. ✅ E6-T1 ticket creation via ForgePort — done (core; labels/caps with E3)
9. ✅ Two-service demo 7/7 — M0 rehearsal complete
10. ✅ E3 GitHub adapter core — done (ForgePort implementation + 6 fixture contract tests; registration wizard + live gate need founder credentials)
11. ✅ M1 RCA skeleton started — heuristic classifier (infra/config/code-fix/needs-human + confidence), RCA comment posting, audit; live model tier needs key/Ollama
12. 🔶 M0 live gate: GitHub half PASSED — real issue #1 created+commented on krakujs/linux-clipboard-manager via live credential check (demo/live_github_check.py, allowlist-guarded); remaining: real GCP error flowing through ingest
    ✅ 9b. M0 REHEARSAL with simulated GitHub: done — make demo-m0 / tests/e2e/test_m0_rehearsal.py (real adapter over loopback HTTP to the local simulator; simulated token only)

Rules: `make check` green per task; commit per task with trailers; spec updates ride along; STATE session log updated at wrap-up or gate.

## Session log (most recent first, one line per session)

- 2026-09-16 (cont. 19) — Verification scheduler (E9-T1 scheduling) done: bounded rounds, injectable clock/sleep, crash-tolerant passes; tests caught an order assumption (verified fingerprint leaves the watched set). 125 tests green. Process note: one more amend slip (scheduler code folded into a docs commit) — the SKILL ban now includes using plain commits only, checked against HEAD first. Development ≈ 58% of backlog.

- 2026-09-16 (cont. 18) — Skill registry (founder directive: models connect to skills/plugins, complete user authority, simple loading): SkillManifest validation, SkillRegistry with disabled-by-default + explicit enable + profile binding (hard errors on unknown/disabled). src/wakey/skills/. 5 tests; 108 green. Development ≈ 57% of backlog tasks. Remaining: entrypoint execution via plugin runtime (E16), delivery wiring, GUI, M3 hardening.

- 2026-09-16 (cont. 17) — doctor now exercises the live LLM RCA tier when WAKEY_LLM_* is configured (verified live: GLM classified the synthetic error). 118 tests green. History note: doctor wiring folded into b4f446b (message under-describes it); amend now banned in SKILL section 8. Development ≈ 55% of backlog tasks, ≈ 80% of P1 effort. Remaining: fix-proposal delivery wiring into ingest, verification watcher scheduling loop, GUI wizard/settings, M3 hardening, M0 GCP leg.

- 2026-09-16 (cont. 16) — **LIVE RCA PASS**: real TypeError traceback -> GLM (z.ai anthropic-compatible endpoint) -> structured RCA: CLASS code-fix, confidence 0.75, correct root cause and evidence (demo/live_rca_check.py). M1 live gate core proven. 105 tests green (make check). Development ≈ 52% of backlog tasks, ≈ 75% of P1 effort. Remaining: pipeline wiring of live RCA + fix proposal delivery into the ingest flow, verification watcher scheduling, GUI wizard/settings, M3 hardening, M0 GCP leg (needs GCP log source).

- 2026-09-16 (cont. 15) — **Live LLM tier unblocked**: GLM key migrated from Claude settings to .env on founder instruction (SKILL rule amended: founder-authorized migration allowed, key never printed/committed — git grep verified 0 tracked files). AnthropicCompatibleModel implemented; live ping PASS on glm-4.5-air via z.ai anthropic endpoint. Settings gained llm_* fields. 118 tests green. Development ≈ 50% of backlog tasks, ≈ 70% of P1 effort. Remaining: wire live model into RCA agent, GUI wizard/settings pages, M3 hardening, live GCP leg.

- 2026-09-16 (cont. 14) — **First multi-agent parallel round** (3 agents, lane-partitioned, no worktree races — disjoint module ownership held): L-D sliding-window rate counters (E5-T3), L-G fix-proposal delivery (E8-T5), L-P live board page (OPS-11/WF-12). Integrator verified, committed per lane. 118 tests green, make check green. Development ≈ 48% of backlog tasks, ≈ 65% of P1 effort. Remaining: M2 delivery wiring into pipeline, GUI polish (wizard/settings pages), M3 hardening (fuzz/perf/review), M0 GCP live leg, M1 live model.

- 2026-09-16 (cont. 13) — Verification watcher pass (E9-T1): run_once evaluates verifying fingerprints via injected grace inputs, applies close/reopen with audit. 105 tests green. Development ≈ 40% of backlog tasks. Remaining buildable: fix-proposal delivery wiring, GUI pages, M3 hardening (fuzz/perf/review). Live gates: GCP log source + LLM key still with founder.

- 2026-09-16 (cont. 12) — `wakey doctor` command live (E3-T7 core): synthetic error through the real pipeline; ConsoleForge gained open_draft_proposal (protocol now fully implemented by both adapters). 103 tests green. Development ≈ 38% of backlog. Next buildable: fix-proposal delivery wiring (fix diff -> draft PR via adapter), verification watcher scheduling, GUI pages; live gates still await founder GitHub token use in .env (already stored) + LLM endpoint.

- 2026-09-16 (cont. 11) — E8-T5 core done: ForgePort.open_draft_proposal + GitHub draft-PR adapter + simulator /pulls endpoint + contract test. 103 tests green. History note: this content landed folded into a docs commit (5fe5f79) — content verified, message under-describes it; amend usage is now banned in session protocol.

- 2026-09-16 (cont. 10) — **Live GitHub check PASS**: founder token (stored in .env only, allowlist-guarded adapter) created issue #1 + comment on krakujs/linux-clipboard-manager through the real GitHubAdapter. Adapter refactored to GitHubConfig dataclass + allowlist guard. SKILL.md section 8: repo allowlist never-rule. Token rotation recommended after test phase (shared via chat). 102 tests green.

- 2026-09-16 (cont. 9) — CLI core live: `wakey status` (version + active fingerprint count) and `wakey board` (--service filter) over the new Storage.list_active_fingerprints query. 102 tests green. Development ≈ 35% of backlog tasks, ≈ 55% of P1 effort (all core engines + CLI slice). Remaining: fix-proposal delivery, verification watcher, GUI dashboard pages, M3 hardening; live gates need founder GitHub token + LLM key/Ollama in .env.

- 2026-09-16 (cont. 8) — Verification verdict core (VER-1/2): pure decide_verdict over watcher inputs (success/insufficient/inconclusive, revert-aware, deploy-aware). 98 tests green. Development ≈ 32% of backlog tasks. Remaining buildable: fix-proposal delivery via forge, verification watcher scheduling, dashboard/CLI, M3 hardening. Live gates: GitHub token + LLM key still with founder.

- 2026-09-16 (cont. 7) — M2 fix agent core (E8-T1..T4): EligibilityInput gate with named refusals, ReproFirstFixer loop (failing repro required pre-patch, else abort "does not reproduce"; sandboxed test runs with timeout; unified diff incl. repro file; bounded iterations). Scripted proposer stands in for the LLM tier. 97 tests green. Development ≈ 30% of backlog tasks, ≈ 45% of P1 effort-weighted. Remaining: fix-proposal delivery (draft MR via forge), verification loop, dashboard/CLI, M3 hardening; live gates await founder credentials.

- 2026-09-16 (cont. 6) — **Local GitHub simulator** (forge/simulator.py): in-memory GitHub API subset with token auth; M0 gate rehearsal runs GitHubAdapter over loopback HTTP to it — full production path with a simulated credential, zero live calls (make demo-m0 + e2e test). 94 tests green. Founder directive honored: no live GitHub touched.

- 2026-09-16 (cont. 5) — RCA agent skeleton: heuristic classifier (infra/config/code-fix/needs-human with confidence), forge comment posting, audit. Tests caught a case-sensitivity bug in the markers. 93 tests green. Development ≈ 22% of backlog tasks; M0 rehearsal done, M1 skeleton done, M2 fix agent + M3 hardening remaining.

- 2026-09-16 (cont. 4) — GitHub adapter built: ForgePort implementation (Bearer auth, typed auth/rate-limit errors, bounded 5xx retries) + 6 MockTransport contract tests; tests caught a transport/client misuse and two import slips, all fixed. 88 tests green. Note: two local history collapses this session (content preserved; messages under-describe tail commits) — no more amends; commits verified against HEAD before creation.
- 2026-09-16 (cont. 3) — E5-T1 completed (java/js/php parsers + dispatcher; tests caught typeshed Match-iterability and a precedence bug) and HMAC webhook auth (migration v2, constant-time compare, body-binding tests). 82 tests, make check green. Queue: items 1-9 done; next = E3 GitHub adapter + M1 RCA skeleton. Founder input needed for: GitHub App credentials (M0 live), LLM key/.env or Ollama endpoint (M1 live).

- 2026-09-16 (cont. 2) — **Two-service demo: 7/7 green** (make demo-two-services). Full local pipeline real: ingest API (key auth + delivery dedup) -> normalizers -> redaction -> fingerprinting -> policy -> console tickets. Backlog: E2-T5, E4-T1(core), E4-T2, E5-T1(core), E5-T2, E5-T5(core), E6-T1(core), E10-T1(core), E17-T1(lite) — 73 tests, make check green. Development ≈ 15% of backlog by tasks; M0 gate rehearsal complete, real forge/GCP demo still pending. Next: queue item — Java/JS/PHP parsers, HMAC auth, E3 GitHub App (needs founder credentials), then M1 RCA agent.

- 2026-09-16 (cont.) — Queue items 1–2 built: redaction engine (14 families, Luhn guard, fail-closed; corpus caught 2 design bugs — exact-count regex + \b, and Exception-not-re.error) and fingerprint engine (templating, Python traceback parser, env-scoped hashing; caught '2.5s' decimal-\b gap and missing lazy traceback parse). 53 tests green. History note: local-only rewrite collapsed commits — redaction engine content lives inside 7a7f8f7 (chore) whose message under-describes it; content verified complete, no data lost. Next: queue item 3 (E2-T5).

- 2026-09-16 — E2-T1..T4 complete: domain models, config system (Settings + wakey.yml loader), SQLite storage (migrations, delivery dedup, occurrence accumulation — tests caught 2 real bugs: migration-before-create, misaligned UPDATE params), FastAPI server (/healthz /readyz /metrics, request-id middleware, JSON logs, lean metrics registry, serve subcommand). E1-T3 completed and verified live in Docker (compose up → /healthz 200, /readyz ready, /metrics; non-root; 149MB). 28 tests green, make check green; 5 commits. Next: E2-T5 queue, then E4 ingest (lane L-D work begins).
- 2026-09-15 — **Development started** (founder unlocked). Wave 0, E1-T1: repo scaffold per canonical tree (src/wakey 11 packages, pyproject w/ ruff+mypy-strict+pytest-timeout caps, SPDX header tooling, Makefile check/test, CONTRIBUTING/SECURITY/.env.example, CI workflow, Dockerfile+compose). Versions grounded against live PyPI (fastapi 0.141, pydantic 2.13, mypy 2.3, ruff 0.16). Local verification green: ruff format/check, mypy strict (13 files), pytest 2/2, headers-check, docker build+run at 149MB non-root. E1-T1 → review (CI execution pending remote); E1-T2/T3/T6 in-progress. Next: E2-T1..T4 (platform skeleton + server), then E1-T3 completion.

- 2026-09-15 — Planning package complete: SKILL.md, engineering standards, task backlog (E1–E13), 11 workflow specs, feature inventory, competitive research. No code written (per founder: plan first).
