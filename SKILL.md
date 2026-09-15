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
| `docs/TASKS.md` | **Complete task backlog** — epics E1–E13, tasks with AC | Your work queue; update task statuses |
| `docs/engineering-standards.md` | Definition of done, testing, CI, release, security bar | Before claiming any task "done" |
| `docs/workflows/WF-01…WF-11.md` | **Exact behavioral specs per workflow** | Read before implementing anything in that area; update when behavior changes |

**Rule: no code without a spec.** If a workflow file or task doesn't describe the behavior you're about to implement, write the spec first, then code.

## 3. Session protocol (do this every session, in order)

0. **Check the mode in `docs/STATE.md`.** While it says **PLANNING-ONLY** (locked by the founder 2026-09-15): do **not** write, scaffold, or refactor production code — only plan, spec, review, and refine docs. Implementation begins only when the founder explicitly says to start development (and the mode line in STATE.md is updated in the same breath).
1. **Read `docs/STATE.md`** — know the current phase, what's done, what's in flight, and open decisions.
2. **Pick work from `docs/TASKS.md`** in dependency order (§5 here, and task `Deps:` columns). Never start a task whose dependencies aren't `done`.
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
                                     E12 (P2 GA breadth) → E13 (P3 Moat)
```

**Gates are hard.** To pass a gate, run the gate's demo end-to-end on real infrastructure (not mocks), record the evidence in STATE.md, and confirm every feature the gate names is `done`:

- **M0**: real GCP error → deduplicated GitHub ticket with deploy correlation. Features: ONB-1..8, ING-1..4, 8, 11, 13, DET-1..6, 9, TIK-1..5.
- **M1**: that ticket gains a classified RCA with confidence and evidence. Features: RCA-1..7.
- **M2**: RCA above floor produces a repro-first draft PR; post-merge verdict works. Features: FIX-1..10, VER-1..4.
- **M3**: security review signed off (WF-09 checklist), metrics live, `wakey doctor` green on a clean install, OSS launch checklist (README, LICENSE, CONTRIBUTING, SECURITY.md, examples, eval bench v0) complete.

E10/E11 are **continuous**: security and operability requirements apply from the first PR of E2 onward, not as an M3 cleanup.

## 6. Engineering rules (summary — full version in `docs/engineering-standards.md`)

- Language: **Python 3.12+**; async-first; no blocking calls in request paths.
- Dependencies: minimal, vetted, pinned; every new dependency justified in the PR description.
- Every module: typed, docstring explaining *why* where non-obvious; no dead code merges.
- Tests: unit for logic, contract for adapters/clients, e2e for gates, eval bench for agent quality. No skipping flaky tests — fix or delete the flake.
- Observability: structured logs (JSON, request IDs), Prometheus metrics for every workflow's key latencies/counts, audit log for autonomous actions.
- Config: everything tunable lives in `wakey.yml` or env; no magic constants in code.
- Failure handling: every external call has a timeout, retry policy, and a defined degraded mode. The pipeline never dies because one component did (NFR-4).

## 7. How to make decisions when specs conflict or run out

1. Search the workflow spec → feature inventory → improvements doc → mvp plan, in that order; newest behavioral spec wins.
2. If genuinely undefined: choose the option that (a) preserves the invariants in §1, (b) serves the **legacy-codebase, self-hosted, trust-first** positioning, (c) is simplest to undo. Write the decision + reasoning into STATE.md's decision log and, if behavioral, into the workflow spec.
3. Scope changes require a feature-inventory edit — code never silently drifts from the inventory.

## 8. What you must never do

- **Never push to any remote or add one (`git remote add …`)** — version control is local-only by founder directive (2026-09-15). Commits are allowed and expected; pushes are not, until the founder explicitly changes this.
- Never commit secrets, tokens, or real customer log data — fixtures only, synthetic data generated by `tests/fixtures/` generators. Never commit `.env`.
- Never weaken a security control (redaction, caps, signature verification) to make a test pass or a demo easier.
- Never merge or delete another agent's/work-stream's in-progress state in STATE.md — coordinate via the decisions log.
- Never let docs and code diverge: if you change behavior, the spec changes in the same PR.
- Never optimize latency/quality metrics by raising autonomy beyond the configured autonomy level.
