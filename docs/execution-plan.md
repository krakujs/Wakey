# Wakey — Execution Plan: Phases, Parallel Lanes & Worktree Protocol

> How the backlog (`docs/TASKS.md`) actually gets executed: in **phases** with hard entry/exit criteria, in **parallel lanes** that let multiple agents work simultaneously without collisions, and in **git worktrees** so no two agents ever share a checkout. Also defines the **G-gate** — the GitHub-integration quality bar that must be certified before any release.

---

## 1. Phases (with entry/exit criteria)

| Phase | Contents | Entry criteria | Exit criteria |
|---|---|---|---|
| **P0 — Foundation** | E1 (+ E2 start) | Repo exists | CI green end-to-end on PRs; packaging works; demo fixtures exist |
| **P1 — MVP "the loop"** | E2–E11 (69 tasks) | P0 exit | Gates **M0 → M1 → M2 → M3** passed in order, **each including G-gate certification** for its GitHub-facing surface (see §2); `wakey doctor` green on a clean machine |
| **P2 — GA** | E12 + E14 (LEG-1/2/3/5/6) + E16-T1..T3 | M3 passed | GA checklist (docs/02 §M3 list) + G-gate re-certified ≤14 days before tag |
| **P3 — Moat** | E13 + E14 (LEG-4/7/8) + E15 + E16-T4..T5 | GA shipped | Per-epic exit criteria written at epic start |

Rules: gates are hard (SKILL.md §5) — no gate, no next phase. Within a phase, lanes run in parallel (§3). A phase exits only when **all** its lanes are merged and CI is green on `main`.

## 2. Forge gates (G-gate) — VCS-integration release bar

GitHub (and later any forge — GitLab, Gitea, … per WF-15) is not "an integration"; it is half the product (tickets, fix proposals, review loop, commands). Nothing ships while a **supported forge** is under-tested. The G-gate is a checklist applied **per forge**: certified for GitHub before M0, and for every additional forge before it ships; **re-certified (full live suite) within 14 days before any release tag**:

1. **Contract suite** (every PR): recorded-fixture tests for *every* GitHub API call Wakey makes — app installation & token minting, issues (create/comment/label/close/reopen), draft PRs, webhook receive/verify. No endpoint without a fixture test; no fixture drift tolerated.
2. **Live sandbox suite** (nightly + pre-release): a dedicated test GitHub org + App installs the real flow end-to-end: install → service add → synthetic error → ticket → RCA comment → draft PR → `@wakey` commands → merge webhook → post-merge verdict. Authenticated via CI secret (`WAKEY_E2E_*`); rate-limit-aware (the suite must not trip secondary limits).
3. **Webhook robustness**: HMAC forgery rejected; redelivery/duplicate deduped; out-of-order events tolerated; ping handled; secret rotation path tested.
4. **Failure drills** (each has an automated test): token revoked mid-run; app uninstalled mid-run; permission narrowed; 403/404 on objects deleted by humans; rate-limit responses (primary + secondary) trigger correct backoff; network partition → queue + catch-up replay without duplicate tickets (WF-11 L3).
5. **Least privilege proof**: the App's declared permissions are enumerated in docs and a test asserts the client never calls outside them.
6. **Fresh-machine install**: the documented install → first ticket flow executes on a clean GitHub org by someone/something that hasn't done it before.

Task that builds the harness: **E3-T8** (lives in Epic 3 so everything after is tested against it, not bolted on later).

## 3. Parallel lanes (who can work at the same time)

Parallelism is safe because **lanes own disjoint modules**. An agent claims tasks only within its lane unless the integrator reassigns.

| Lane | Owns (modules) | Epics / typical tasks | Max agents |
|---|---|---|---|
| **L-P — Platform & Ops** | repo scaffolding, `config/`, `models/`, `storage/`, server, `ops/`, CLI, dashboard, packaging, CI | E1, E2, E11, E10, E16, docs/site | 1–2 |
| **L-G — Forge & Interaction** | `forge/` adapters (GitHub, GitLab, …), `tickets/`, review-loop, agents' interaction surface, eval bench | E3, E17-T1..T4, E6, E7-T7, E8-T5/T8/T9, E9, E12-T17/25 | 1–2 |
| **L-D — Data & Detection** | `ingest/`, `fingerprints/`, `policy/`, connectors, redaction engine, agent brains (RCA/fix loops) | E4, E5, E7-T1..T6/T8, E8-T1..T4/T6/T7 | 1–2 |
| **Integrator** | merges lanes into `main`, updates STATE.md/TASKS.md at wave boundaries, runs the wave CI | rotates; also the G-gate evidence recorder | 1 (can be a lane agent wearing the hat) |

**Cross-lane rule:** shared files — `docs/STATE.md`, `docs/TASKS.md`, `pyproject.toml`, OpenAPI/schema files — are edited **only by the integrator at wave merge** (see §4.5). Everything else is lane-owned.

**Realistic parallelism:** P1 runs comfortably with **3 agents** (one per lane) + integrator; P2 can widen to 4–5 by splitting L-D into detection vs agents and L-P into ops vs GUI.

### P1 wave plan (each wave ends with an integrator merge + CI on `main`)

| Wave | L-P | L-G | L-D | Exit |
|---|---|---|---|---|
| 0 | E1-T1..T5 | — | — | P0 exit |
| 1 | E2-T1..T5 | — | — | platform skeleton ready |
| 2 | E11-T3 metrics scaffold, E1 polish | E3-T1..T4, E3-T8 harness | E4-T1..T3, T7, T8 | GitHub harness live; ingest redacts+persists |
| 3 | E2-T5 queue hardening, E11-T2 | E3-T5..T7 (wizard, doctor) | E4-T4..T6, T9; E5-T1..T2 start | doctor green (M0 preview) |
| 4 | E11-T1 dashboard | E6-T1..T4 | E5-T2..T6 | **GATE M0** + G-gate certify |
| 5 | E11-T4/T5 | E7-T6..T8 | E7-T1..T5 (runtime first) | **GATE M1** |
| 6 | E10-T1..T3 | E8-T5..T9 | E8-T1..T4 | **GATE M2** + G-gate re-certify |
| 7 | E10-T4..T6, E11-T6 | E9-T1..T4, E12 previews | backlog burn-down, launch docs | **GATE M3** |

(Dependency source of truth is the `Deps:` column in TASKS.md; waves are the scheduling view. If a lane's next task is blocked, it pulls from its lane's backlog or helps the integrator.)

## 4. Worktree protocol (different agents, different checkouts)

### 4.1 Setup (per agent, per task)
```bash
git worktree add ../wakey-wt/agent-<name>-<taskid> -b agent/<name>/<taskid>-<slug> main
cd ../wakey-wt/agent-<name>-<taskid>
cp .env.example .env && pip install -e ".[dev]"   # isolated venv per worktree
```
- One **worktree + branch per task**, never two tasks in one checkout, never two agents in one worktree.
- Branch naming: `agent/<agent-name>/<task-id>-<short-slug>` (e.g. `agent/nyx/e5-t2-fingerprint-engine`).
- Worktree dirs live outside the repo (`../wakey-wt/`), gitignored by convention; removed after merge (`git worktree remove`).

### 4.2 Working rules
1. Commit small and often; every commit carries trailers: `Task: E5-T2`, `Lane: L-D`, `Status: in-progress|review|done` — this is how parallel progress is tracked without file collisions.
2. Tests + lint run locally before any merge request (`make check`); CI runs per branch in CI-capable setups, otherwise at wave merge.
3. Behavior changes update the relevant `docs/workflows/WF-*.md` **in the same branch** (SKILL.md rule) — doc conflicts are resolved at merge by the integrator favoring the code-bearing branch.
4. Docs with shared files (STATE.md, TASKS.md): **do not edit in lanes**. Record progress via commit trailers + the merge request description.

### 4.3 Merge requests (local-only flow)
Since the project is **local-only** (no remote, founder directive), the merge request is: the agent announces the branch is `Status: done` (commit trailer) and hands the branch name to the integrator. The integrator:
1. Reviews diff vs the task's AC + workflow spec,
2. Rebases onto `main` if needed, runs full `make check` + affected suites,
3. Merges with `--no-ff` (merge commit message = task id + AC summary),
4. Updates STATE.md/TASKS.md statuses + decisions,
5. Removes the worktree.

### 4.4 Rebase cadence & conflicts
- Lanes rebase onto `main` at **every wave boundary** (and mid-wave if `main` moved into their module).
- Lane module ownership (§3) makes code conflicts rare; when they happen, the two agents + integrator resolve in one pass — **spec files win over opinions** (SKILL.md §7).
- `main` must stay green at every wave merge: if a merged branch breaks CI, its lane fixes forward immediately; no revert without integrator sign-off.

### 4.5 Wave ritual (integrator, ~30 min)
Collect `Status: done` branches → merge in dependency order → run full CI → update TASKS.md statuses + STATE.md (epic board, decisions, next wave) → tag the wave in the STATE.md session log → announce next wave assignments.

## 5. Release definition (tying it together)

`v1.0.0` may be tagged only when:
- [ ] All four M-gates passed with evidence in STATE.md;
- [ ] **Forge-gate live suite certified ≤14 days before the tag for every shipped forge** (§2);
- [ ] P1 + GA-scope lanes fully merged, CI green on `main`, eval bench numbers published;
- [ ] Security review (E10-T6) closed with no open highs;
- [ ] Fresh-machine install drill (docs + doctor) executed;
- [ ] Docs complete: README quickstart, all WF specs current, CHANGELOG, LICENSE.

## 6. Continuous cadence & safe local testing

- **Continuous development**: waves roll back-to-back — the wave ritual (§4.5) ends with next-wave assignments, so lanes are never idle waiting for a phase to "officially" start. Quality work (bug board, flaky fixes, docs debt) is first-class wave work when feature tasks are blocked.
- **Continuous testing**: commit → `make check` in the worktree; wave merge → full suite on `main`; nightly → resource benchmark + eval bench + forge live sandbox + mutation testing (engineering-standards §3). Nothing waits for a phase boundary to be tested.
- **Every phase tested locally**: each M-gate has a script — `make gate-m0|m1|m2|m3` — that runs the gate's full verification on a developer machine (fake GitHub for daily runs; live forge sandbox when tokens are present). Phase exit = local gate script green + evidence recorded in STATE.md.
- **The PC-safety contract**: all testing is resource-capped by default (`make test` cannot harm the host) — caps, containerized benchmarks, rate-limited load generators, and the three never-rules are defined in engineering-standards §3 ("Safe local testing") and are merge-blocking review criteria for any test/bench code.
