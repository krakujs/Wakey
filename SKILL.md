# SKILL.md — Wakey Build Orchestration

> **Operating manual for any AI agent (or human) working on Wakey.** Read this file first, every session. It tells you where everything lives, how to work, and what "done" means. Wakey is an open-source, self-hosted agent that watches production logs, opens tickets, finds root causes, and opens suggested-fix PRs — see `README.md`.

---

## 1. Non-negotiable invariants (never violate, never "temporarily" violate)

1. **Wakey never merges.** No code path may merge, force-push, or auto-approve a PR. Every fix is a draft PR for human review.
2. **Logs are data, not instructions.** Log content is never interpreted as commands for the agent. Enforce quoting, tool allowlists, and scope limits everywhere logs enter a prompt.
3. **Redact before store, redact before prompt.** No raw log line is persisted or sent to an LLM before passing the redaction engine (WF-09).
4. **Everything autonomous is capped and audited.** Tickets/hour, concurrent investigations, open PRs, LLM spend — hard caps with graceful degradation; one audit-log line per autonomous action.
5. **Production grade or it doesn't ship.** Nothing is "done" without meeting `docs/engineering-standards.md` (tests, observability, security review, docs).

## 2. Document map (source of truth)

| File | What it is | When you read/update it |
|---|---|---|
| `README.md` | Product definition + doc index | Update when adding top-level docs |
| `SKILL.md` | This file — the operating manual | Read first every session; change only with explicit justification |
| `docs/STATE.md` | **Living project state** — current phase, task board, decisions log | Read first; **update last** in every session |
| `docs/01-competitive-landscape.md` | Market research + positioning | Rarely; refresh before launch |
| `docs/02-mvp-plan.md` | Architecture, stack, milestones | When architecture changes |
| `docs/03-end-to-end-workflow.md` | The big-picture behavioral spec | When a system-level behavior changes |
| `docs/04-improvements.md` | Design decisions that differentiate Wakey | When adding/removing a design principle |
| `docs/05-feature-inventory.md` | **Canonical feature list (~70, IDs F-xx)** | When scope changes; all work references these IDs |
| `docs/TASKS.md` | **Complete task backlog** — epics E1–E16, tasks with AC | Your work queue; update task statuses |
| `docs/execution-plan.md` | **Phases, parallel lanes, worktree protocol, G-gate (GitHub release bar)** | Read before claiming work in parallel runs; integrators live here |
| `docs/engineering-standards.md` | Definition of done, testing, CI, release, security bar | Before claiming any task "done" |
| `docs/workflows/WF-01…WF-15.md` | **Exact behavioral specs per workflow** (WF-12 = dashboard GUI, WF-13 = CLI, WF-14 = plugins & AI config, WF-15 = forge abstraction / multi-VCS) | Read before implementing anything in that area; update when behavior changes |

**Rule: no code without a spec.** If a workflow file or task doesn't describe the behavior you're about to implement, write the spec first, then code.

## 3. Session protocol (do this every session, in order)

0. **Check the mode in `docs/STATE.md`.** While it says **PLANNING-ONLY** (locked by the founder 2026-09-15): do **not** write, scaffold, or refactor production code — only plan, spec, review, and refine docs. Implementation begins only when the founder explicitly says to start development (and the mode line in STATE.md is updated in the same breath).
1. **Read `docs/STATE.md`** — know the current phase, what's done, what's in flight, and open decisions.
2. **Pick work from `docs/TASKS.md`** in dependency order (§5 here, and task `Deps:` columns). Never start a task whose dependencies aren't `done`. In parallel runs, claim within your **lane** and follow the **worktree protocol** (`docs/execution-plan.md` §3–4) — one worktree + branch per task, shared docs edited only by the integrator.
3. **Read the workflow spec(s)** covering the task (`docs/workflows/WF-xx.md`) and the feature IDs it implements (`docs/05-feature-inventory.md`).
4. **Plan → implement → test → document** per `docs/engineering-standards.md`.
5. **Update the docs you changed behavior in** (workflow spec, feature inventory if scope moved).
6. **Update `docs/STATE.md` last**: task status, new decisions, blockers, next recommended task.
7. **Commit** with conventional messages (`feat(detect): …`, `fix(ingest): …`, `docs(wf-05): …`), one logical change per commit. Reference task + feature IDs in the body (`Task: E5-T2 · Features: DET-3, DET-4`).

## 4. Task lifecycle

Tasks in `docs/TASKS.md` move through: `todo → in-progress → review → done`, plus `blocked:<reason>`.
- A task is **in-progress** only when its dependencies are `done`.
- A task is **review** when implementation is complete and standards are met but a second pass (self-review against AC + workflow spec) hasn't happened.
- A task is **done** only when every acceptance criterion (AC) has an automated test or a documented manual check, docs are updated, and CI is green.
- If a task reveals a scope/behavior change: update the workflow spec + feature inventory **in the same change**, and note it in STATE.md decisions.

## 5. Build order & phase gates

```
E1 Foundation → E2 Core platform ─┬─→ E3 Onboarding/GitHub ─┐
                                  └─→ E4 Ingestion ─────────┼─→ E5 Detection → E6 Ticketing
                                                             │        = GATE M0: "error → ticket" demo
                                                             ▼
                                              E7 RCA ──── GATE M1: "ticket → RCA" demo
                                                             ▼
                                     E8 Fix agent → E9 Verification ── GATE M2: "RCA → fix PR → verified"
                                                             ▼
                                     E10 Security hardening + E11 Operations ── GATE M3: OSS launch ready
                                                             ▼
                                     E12 (P2 GA breadth) → E13/E14 (P3 Moat & estate mgmt) → E15 (write-path, "Ponytail")
```

**Gates are hard.** To pass a gate, run the gate's demo end-to-end on real infrastructure (not mocks), record the evidence in STATE.md, and confirm every feature the gate names is `done`. **Every gate that touches GitHub-facing surface additionally requires G-gate certification** — the GitHub-integration quality bar (contract fixtures, live sandbox suite, webhook robustness, failure drills, least-privilege proof) defined in `docs/execution-plan.md` §2:

- **M0**: real GCP error → deduplicated GitHub ticket with deploy correlation. Features: ONB-1..8, ING-1..4, 8, 11, 13, DET-1..6, 9, TIK-1..5.
- **M1**: that ticket gains a classified RCA with confidence and evidence. Features: RCA-1..7.
- **M2**: RCA above floor produces a repro-first draft PR; post-merge verdict works. Features: FIX-1..10, VER-1..4.
- **M3**: security review signed off (WF-09 checklist), metrics live, `wakey doctor` green on a clean install, OSS launch checklist (README, LICENSE, CONTRIBUTING, SECURITY.md, examples, eval bench v0) complete.

E10/E11 are **continuous**: security and operability requirements apply from the first PR of E2 onward, not as an M3 cleanup.

## 6. Engineering rules (summary — full version in `docs/engineering-standards.md`)

- **Senior craft** (founder directive: work as a 10-year-experienced engineer; no messy code, everything well structured): write for the reader who maintains this in year three — small single-purpose modules, explicit names, boring solutions over clever ones, no dead code or dangling stubs, self-review before anything enters `review` status. If a file feels too big or a function needs a paragraph to explain, split it. Structure is defined, not improvised: canonical layout in `docs/engineering-standards.md` §2, doc map in §2 here.
- Language: **Python 3.12+**; async-first; no blocking calls in request paths.
- Dependencies: minimal, vetted, pinned; every new dependency justified in the PR description.
- Every module: typed, docstring explaining *why* where non-obvious; no dead code merges.
- Tests: unit for logic, contract for adapters/clients, e2e for gates, eval bench for agent quality. No skipping flaky tests — fix or delete the flake.
- **Continuous quality, zero known bugs**: `make check` on every commit, full suite at every wave merge, nightly benchmark/eval/live-sandbox; every bug ships with its regression test and reopens its task; **no gate or phase exits with an open bug**; coverage floors (≥90% overall, ≥95% critical modules) are CI-enforced.
- **Host-safe testing** (founder requirement: testing must never crash the PC): all tests/benchmarks run resource-capped by default — per-test timeouts, ≤4 parallel workers, containerized benchmarks with memory/CPU caps, rate-limited load generators. The three never-rules (never uncapped fork, never unbounded allocation, never metal load tests) are merge-blocking review criteria for test code. Every phase gate is runnable locally via `make gate-*`. Full rules: `docs/engineering-standards.md` §3.
- Observability: structured logs (JSON, request IDs), Prometheus metrics for every workflow's key latencies/counts, audit log for autonomous actions.
- Config: everything tunable lives in `wakey.yml` or env; no magic constants in code.
- **Forge-neutral core** (founder requirement: connect with any GitLab / other VCS easily): tickets, fix proposals, commands, and webhooks flow through the **ForgePort** abstraction (WF-15) with GitHub as the first adapter; no module outside a forge adapter imports a forge SDK or calls a forge API — enforced by an import-linter architecture test. The G-gate generalizes to a per-forge gate.
- **Resource discipline** (founder requirement: least possible resources): budgets in NFR-6..8 are CI-enforced — event-driven (no busy loops), aggregate-don't-retain storage, lazy heavy imports, lean dependencies (install size *and* import cost justified), batched I/O, server-rendered frontend, LLM frugality (dedup-before-LLM, cheap-tier routing, cached responses). Any PR that regresses the nightly resource benchmark >20% does not merge. Full rules: `docs/engineering-standards.md` §8.
- **AI is vendor-neutral by construction** (founder requirement: users can configure any AI tool): every LLM call flows through the model-router abstraction with named, user-defined profiles (WF-14 §B); no component may call a vendor SDK directly or hard-code a provider. The same openness applies to plugins (WF-14 §A): core must stay upgradeable under arbitrary plugin load.
- Failure handling: every external call has a timeout, retry policy, and a defined degraded mode. The pipeline never dies because one component did (NFR-4).

## 7. How to make decisions when specs conflict or run out

1. Search the workflow spec → feature inventory → improvements doc → mvp plan, in that order; newest behavioral spec wins.
2. If genuinely undefined: choose the option that (a) preserves the invariants in §1, (b) serves the **legacy-codebase, self-hosted, trust-first** positioning, (c) is simplest to undo. Write the decision + reasoning into STATE.md's decision log and, if behavioral, into the workflow spec.
3. Scope changes require a feature-inventory edit — code never silently drifts from the inventory.

## 8. What you must never do

- **Never push to any remote or add one (`git remote add …`)** — version control is local-only by founder directive (2026-09-15). Commits are allowed and expected; pushes are not, until the founder explicitly changes this.
- Never commit secrets, tokens, or real customer log data — fixtures only, synthetic data generated by `tests/fixtures/` generators. Never commit `.env`.
- **Never extract credentials from another tool's storage** (Claude Code/other agents' key stores, IDE sync, shell history) — even when offered. Keys enter Wakey only via the founder pasting them into `.env` (gitignored) or a local-model endpoint; the two-service pipeline demo runs with a silent fake model tier and needs no keys at all.
- **Repository allowlist (founder directive, 2026-09-16)**: live GitHub operations are permitted **only** against `krakujs/linux-clipboard-manager` (or a dedicated private test repo the founder approves). Never create tickets, PRs, branches, or any writes on any other repository — even if a token's scope would allow it. All other repos are read-nothing, write-nothing. Token stays in `.env` only, never in code, docs, or logs.
- Never weaken a security control (redaction, caps, signature verification) to make a test pass or a demo easier.
- Never merge or delete another agent's/work-stream's in-progress state in STATE.md — coordinate via the decisions log.
- Never let docs and code diverge: if you change behavior, the spec changes in the same PR.
- Never optimize latency/quality metrics by raising autonomy beyond the configured autonomy level.
- **Never run uncapped tests or load on the host**: no fork/spawn without caps, no unbounded allocations, no metal load tests without container limits — a runaway benchmark must always die at its cap, never on the PC (eng-standards §3, host guardrails).

## 9. Planned direction: write-path monitoring (the "Ponytail" plugin)

Founder direction (2026-09-15): after the runtime loop ships, Wakey extends **upstream** — a plugin, codename **Ponytail** (name to be confirmed), that monitors **code as it is written** inside AI coding agents and IDEs, feeding the same fingerprint/risk spine that powers production response. Full spec: feature inventory §15 (WPM-1..4), epic E15 (P3).

Binding consequences for every agent working on this repo **now**, even though the epic is future:

- **Core interfaces stay source-plural.** Ingest (WF-02), the event model (E2-T1), and fingerprinting (WF-03) must never assume "runtime logs only." Code-writing events (diffs, commits, agent-session summaries) are a planned first-class source entering through the generic-webhook contract (ING-3). Any design that hard-codes log-ness into the spine is a defect.
- **Attribution metadata is reserved.** Fingerprints already carry deploy correlation (DET-9); keep commit-level and session-level metadata first-class in the event model so write→runtime attribution (WPM-3) needs no schema break.
- **Don't build ahead of the epic; don't architect around it either.** E15 starts after GA, but reviews of WF-02 / E2-T1 / E5-T2 must check this section.

## 10. Parallel agents & worktrees (summary)

Multiple agents work simultaneously via **lanes** (L-P platform/ops · L-G GitHub/interaction · L-D data/detection) with disjoint module ownership, scheduled in **waves** that end with an integrator merge. Each agent works in its own **git worktree** (`git worktree add ../wakey-wt/<name> -b agent/<name>/<task-id>-<slug> main`), one task per worktree, tracking progress via commit trailers (`Task:` / `Lane:` / `Status:`) instead of editing shared docs. Shared files (STATE.md, TASKS.md, pyproject) are updated only by the **integrator** at wave merges, which keep `main` green. Full rules: `docs/execution-plan.md` §3–4. Violations that matter: two agents in one worktree, editing shared docs in a lane, merging your own branch past CI.
