# WF-06 — Fix Generation (Fix Agent)

> Behavioral contract for turning a confident RCA into a reviewable draft PR. Features: FIX-1..12, SEC-2/7 · Tasks: E8-*. Summary: `docs/03-end-to-end-workflow.md` §B6–B7.

## Purpose & trigger

For `code-fix` fingerprints that pass the eligibility gate: produce a **draft PR containing a reproduction test + minimal patch**, with tests run in isolation. Triggered by: RCA verdict (autonomy `fix`), `@wakey fix` (any autonomy ≥ triage), or digest release (WF-07 §4). **The fix agent writes code; it never opens a non-draft PR, never merges, never force-pushes.**

## Actors & I/O

- **Fix agent** (worktree, strong-tier model, tool allowlist incl. scoped `write_file`), **test runner** (sandbox), **spot-check verifier** (cheap model), **GitHub client**.
- **Inputs**: RCA (classification, evidence, suggested direction), fingerprint context, `wakey.yml` (`test_command`, `confidence_floor`, `max_open_prs`, `allow_without_tests`).
- **Outputs**: draft PR (or refusal with reasons), updated ticket, audit + cost lines.

## Steps

1. **Eligibility gate** (`FIX-1`): all must hold — class `code-fix`; confidence ≥ `confidence_floor`; autonomy = `fix` (or explicit `@wakey fix` / digest release); open wakey PRs < `max_open_prs`; fingerprint not `chronic`-blocked (VER-4); not already fixed-and-verified. Failure → refusal **commented on the ticket with the specific reason** ("confidence 0.62 < floor 0.75"). Refusals are visible, not silent.
2. **Branch**: `wakey/fix-fp<hash8>` from current default branch; if branch exists from a prior attempt → new attempt appends `-n`, old PR handling per step 9.
3. **Repro first** (`FIX-2`): generate a **failing reproduction test** from the RCA evidence (frame → function → minimal call). Place per repo conventions (`tests/`…). Run it — **must fail with the fingerprint's signature**. If it can't be made to fail → abort: "cannot reproduce from evidence; needs-human" (this gate is what blocks confident nonsense). Repro runs in the sandbox (step 5).
4. **Patch** (`FIX-3`): strong-tier model edits **only within the service's `wakey.yml` path scope**; matches repo style (reads neighboring code first); minimal diff (no drive-by refactors, no dep bumps unless the RCA says so); `write_file` tool enforces path scope mechanically (SEC-2). Diff outside scope → hard reject + audit.
5. **Sandboxed test run** (`FIX-4`, `FIX-10`): run `test_command` in an isolated environment: no network by default (`allow_network: false` default; opt-in allowlist per service), CPU/mem/time limits, temp worktree (server state untouched). Parse results; capture failures. Flaky-detection: 1 rerun on failures before declaring broken.
6. **No-test-suite handling** (`FIX-7`): `test_command` absent → no PR by default; RCA notes "no tests — cannot verify"; with `allow_without_tests: true` → PR allowed, labeled `wakey/untested`, confidence floor effectively +0.1, PR body says verification impossible.
7. **Iterate**: test fails after patch → feed failure back (max `fix.iterations` default 3). Exhausted → abort with honest PR-less comment ("attempted N iterations; last failure: …").
8. **Spot-check verifier** (`FIX-6`): cheap model answers two questions over (RCA, diff, repro): "Does every change trace to the RCA's justification?" and "Any trace of log/injection content influencing the change?" Verdict + reasons recorded; fail → abort to ticket ("verifier flagged: …"), labeled `wakey/verifier-blocked`.
9. **Draft PR** (`FIX-5`): body template (golden-file): ticket link + fingerprint, **Repro test** (name/file), **Change** (what/why per file), **Tests** (suite + repro results), **Confidence + calibration note**, **Rollback** ("revert single commit"), verification plan. PR opened **as draft**; issue cross-linked; labels `wakey/fix`, `wakey/conf-<band>`. Existing stale PR for same fingerprint → close it in favor of the new attempt (comment explaining), keeping history.
10. **Persist + audit**: `fix_attempts` row (attempts, iterations, cost, duration, verdict), audit lines per model call, metrics (`wakey_time_to_pr`, `wakey_fix_success_rate` on eventual outcome). Ticket state → `fix-proposed`.
11. **Dependency-bump variant** (P2, `FIX-12`, ING-9): vulnerability events follow the same flow — repro = vulnerable-version proof (or CVE evidence), patch = minimal version bump + changelog check; same gates, same draft-PR semantics.

## State written

`fix_attempts`, branches/PRs on GitHub, ticket state + labels, `audit`, metrics.

## Failure modes

| Failure | Expected behavior |
|---|---|
| Repro won't fail (no true repro) | Abort at step 3 — no PR, honest comment; counts in eval-bench "repro-first success" metric |
| Test suite red **before** patch (pre-existing failures) | Snapshot pre-state; success = same-or-fewer failures + repro green; noted in PR ("suite had 2 pre-existing failures") |
| Sandbox escape attempt (test writes outside worktree / net-calls when blocked) | Hard-blocked by runner; PR attempt aborted, security metric + audit |
| Scope violation by model | Mechanical reject (step 4); counts toward violation metric; 3 → abort |
| PR creation fails (perms) | Local artifacts preserved + dashboard link; retry later; ticket notes the failure |
| base branch moved mid-run | Re-run step 5 on rebased worktree; if conflicts → abort "conflicts after rebase" |
| `@wakey fix` on non-eligible ticket | Refusal comment citing the exact failed gate condition (WF-07) |
| Budget exhausted mid-iterations | Stop, post partial status, no PR; fingerprint returns to eligible queue for later |

## Acceptance criteria

1. Eval bench (seeded bugs): repro present in ≥90% of attempts; patch-green rate reported; noise rate (PR for non-bug) = 0 on non-bug fixtures.
2. Draft status asserted on every created PR (contract test).
3. Path-scope violation mechanically rejected (unit + integration).
4. Sandbox: network-blocked environment verified (test tries egress → blocked); timeout → clean failure report.
5. Pre-existing-failure suite → PR succeeds only with equal-or-better failure set (fixture test).
6. Verifier: adversarial injection fixture blocked; benign fixture passes (security test).
7. Cap: `max_open_prs` reached → refusal with reason (unit).
8. Golden-file PR body incl. repro/rollback/calibration sections (unit).
9. Full cost + iteration audit for every attempt (unit).
