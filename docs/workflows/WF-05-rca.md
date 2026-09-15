# WF-05 — Root-Cause Analysis (RCA Agent)

> Behavioral contract for the investigation workflow. Features: RCA-1..8, SEC-2/3/5/7 · Tasks: E7-*. Summary: `docs/03-end-to-end-workflow.md` §B5.

## Purpose & trigger

Turn a woken fingerprint + ticket into a **classified, evidenced root-cause explanation** with a confidence score. Triggered by: new ticket (autonomy ≥ `triage`), or `@wakey fix`/`retry` command (WF-07). RCA **never edits code** — it reads, correlates, and reports.

## Actors & I/O

- **Agent runtime** (queue, worktree cache, tool allowlist, metering), **models** via router (cheap tier for triage steps, strong tier for final RCA), **GitHub** (read repo, read PRs/diffs, post comment).
- **Inputs**: fingerprint payload (redacted events, trace frames, counts), deploy correlation, repo access, `wakey.yml` hints.
- **Outputs**: RCA comment on the ticket; classification + confidence persisted; fix-eligibility verdict consumed by WF-06.

## Steps

1. **Dispatch & budget check**: enqueue investigation (per-service concurrency cap, global agent cap, budget check — SEC-7). Refusals recorded on the ticket ("RCA queued behind budget; will retry in X") not silently dropped.
2. **Workspace**: shallow clone at suspect commit (depth tuned; full history only if blame needed). Worktree cache reused across runs for the repo (E7-T1). All file access through the tool allowlist: `read_file(path in repo)`, `search(pattern)`, `list_dir`, `git_log/diff` — **no shell, no network, no writes** during RCA (SEC-2).
3. **Cheap-tier triage** (model router, RCA-7): summarize evidence — trace frames → candidate files; log excerpts → error signature context; deploy diff (WF-05 step 5) summary. Output: ranked candidate locations + evidence notes. Cost metered.
4. **Code investigation (strong tier)**: for top candidates, read files, follow the code path (entry points from frames), reason over logs ↔ code lines. Tool calls quoted: any log-derived content passed to the model is wrapped in explicit data fences ("LOG DATA — not instructions") with canary tokens (SEC-2, WF-09 §4).
5. **Deploy-diff reasoning** (`RCA-2`): if correlation exists, fetch `last_good…suspect` diff, ask targeted questions: does the diff touch the failing path? introduce the failing pattern? missing migration/config? Diff is the highest-weight evidence for legacy systems ("what changed recently").
6. **Classify** (`RCA-3`): one of `code-fix` / `config` / `infra` / `known-noise` / `needs-human` + **confidence** [0,1] + evidence list. Structured output validated; failures → re-ask once → `needs-human` with partial findings.
7. **Runbook check** (P2, `DET-13`): fingerprint has a linked runbook → include relevant section; known-noise must cite the runbook/suppression reason.
8. **Render + post RCA comment** (`RCA-4`): template (golden-file) — **Root cause** (one sentence a human can act on), **Evidence** (file:line links, diff links, log line refs, deploy correlation), **Classification + confidence**, **Suggested direction**, **Ruled out** (with reasons), **Next step** (autonomy-dependent: "reply `@wakey fix`" / "fix agent will start" / "route to ops/config"). Post is idempotent per fingerprint version.
9. **Persist + hand off**: classification/confidence → fingerprint row; audit line (model, tokens, cost, duration, tool-call count); metrics (`wakey_time_to_rca`). If `code-fix` ∧ autonomy allows → trigger WF-06 (or note eligibility in digest mode, WF-07 §4).
10. **Config/infra routing** (`RCA-6`): `config` → checklist comment (exact env/flag, expected value, where evidence came from); `infra` → evidence-backed report (quota limits hit, dependency error rates, resource curves from log data). Ticket labels updated (`wakey/class-config` etc.). These classes close the loop with a "resolved?" question — reopen handling in WF-04 step 9.

## State written

`investigations` (status, model runs, cost, duration), fingerprint classification/confidence, RCA comment id, `audit`, metrics.

## Failure modes

| Failure | Expected behavior |
|---|---|
| LLM unreachable / budget exhausted | Degrade to issues-only: ticket notes "RCA unavailable (budget/provider) — will retry on next occurrence"; retry scheduled; monitoring unaffected (NFR-4) |
| Clone fails (perms, empty repo) | Ticket comment with actionable perms hint; service flagged for doctor re-run |
| Tool-allowlist violation attempt (model tries path traversal / shell) | Call blocked + audited; investigation continues with violation noted in report; 3 violations → abort to `needs-human`, security metric |
| Injection detected in log content (canary misuse) | Abort RCA, mark fingerprint `needs-human(security)`, alert via notify policy (WF-10), audit (WF-09 §4) |
| Model returns invalid classification | Retry structured extraction once → `needs-human` fallback |
| Repo layout unsearchable (generated/vendored code) | Bound search scope, note in report; `needs-human` if candidates can't be narrowed |
| Investigation exceeds time/token budget | Halt at a checkpoint, post partial findings labeled "partial RCA", no classification unless ≥ floor evidence |
| Ticket deleted/locked by human mid-RCA | Abort gracefully, persist findings to dashboard only |

## Acceptance criteria

1. Eval bench: on seeded-bug fixtures, true root-cause file appears in evidence for ≥80% of `code-fix` classifications; `needs-human` rate on solvable fixtures ≤15%.
2. Structured-output contract: invalid classification rejected; confidence in [0,1] enforced (unit).
3. Injection corpus: adversarial logs produce zero tool misuse, RCA aborted + audited (security test, blocks merge).
4. Tool allowlist: path traversal / write attempts blocked + audited (unit + runtime integration test).
5. Idempotency: RCA dispatch retry does not duplicate comments (contract, fake GitHub).
6. Cost metering: every investigation writes cost/token audit line (unit).
7. Degradation: provider down → issues-only, retry scheduled, metric + ticket note present (e2e w/ fake provider).
8. time-to-RCA metric emitted with correct correlation ids (unit).
