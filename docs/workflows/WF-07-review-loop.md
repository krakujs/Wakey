# WF-07 — Review Loop, `@wakey` Commands & Autonomy Modes

> Behavioral contract for human–agent interaction surface. Features: RCA-5, FIX-8/9, TIK-6 · Tasks: E7-T7, E8-T8/9, E3-T3. Summary: `docs/03-end-to-end-workflow.md` §B8.
> **Forge note:** commands arrive via any supported forge's comment/reaction webhooks (WF-15); parsing, authorization, and semantics are adapter-independent.

## Purpose & trigger

Humans direct the loop from GitHub only: comments on tickets/PRs. This workflow parses, authorizes, and executes `@wakey` commands, and implements the autonomy modes (esp. digest/approval-queue). Triggered by GitHub webhook comment events and schedule (digest).

## Actors & I/O

- **GitHub webhooks** → command bus; **authorized responders** (users with write access to the repo — checked via installation permissions, cached); **wakeyd workflows** (WF-05/06/08).
- **Inputs**: issue/PR comment events, reactions, schedule tick (digest).
- **Outputs**: workflow dispatches, replies, state changes.

## Steps

1. **Receive & verify**: webhook HMAC check (reject 401 otherwise); dedup by delivery id (WF-02 semantics). Bot's own comments are ignored (loop prevention, hard rule).
2. **Parse commands** (`RCA-5`): `@wakey fix`, `@wakey explain <target>`, `@wakey retry [feedback]`, `@wakey drop`, `@wakey reopen`, `@wakey status`, (P2) `@wakey suppress <duration> <reason>`. Unknown command → helpful reply listing commands (once per thread to avoid noise). Non-commands ignored.
3. **Authorize**: responder must have write access to the repo in the installation; else reply "only repo collaborators can direct wakey" + security metric. Every command → audit line (user, command, target).
4. **Dispatch**:
   - `fix` on ticket → WF-06 eligibility re-check (report exact refusal reason if it fails) → fix attempt.
   - `explain <file:line|claim>` → cheap-tier targeted explanation posted as reply (bounded, no code changes).
   - `retry` on PR (WF-06 step feedback loop, `FIX-8`): reviewer comments since last attempt become structured feedback; new commit on **same PR**; max 2 auto-retries, then "needs human edit" reply.
   - `drop` → fingerprint suppressed (default 7d) + ticket closed as `wakey/dropped`; reversible via dashboard.
   - `reopen` → overrides human-closed state (WF-04 step 10).
   - `status` → current pipeline stage, costs so far, caps state.
5. **Digest mode** (`FIX-9`, autonomy level between triage and fix): instead of auto-starting WF-06, eligible fixes accumulate; daily (or on `@wakey digest`) a **single digest issue per repo**: one line per candidate (ticket, RCA summary, confidence) + instructions. Admin reacts ✅ → WF-06 starts for that candidate; ❌ → refusal recorded (learned-preference input for P2). Digest is idempotent (one open digest issue per repo/day).
6. **Autonomy mode semantics** (the dial, enforced centrally in policy — not scattered):
   - `observe`: tickets created, no RCA, no fixes. Footer tells humans what commands can enable.
   - `triage`: + RCA + classification + routing. Fixes only via explicit `@wakey fix`.
   - `digest`: + candidates batched for approval (step 5).
   - `fix`: + autonomous PR creation within caps.
   Mode changes via `wakey.yml` commit (hot-reloaded) or dashboard (writes back to config source per repo settings).

## State written

`commands` (log incl. authorization result), digest issues, suppression entries (via `drop`), config-derived autonomy state, `audit`.

## Failure modes

| Failure | Expected behavior |
|---|---|
| Webhook replay/duplicate | Dedup by delivery id → single execution |
| Comment edited after posting | Parse the edit event too (GitHub sends edits); re-dispatch only if command valid & not yet executed |
| Command on a non-wakey issue | Ignore silently (metric only) — wakey doesn't talk on issues it doesn't own |
| GitHub API down when replying | Command executed anyway where safe (dispatch first, reply queued); replies retried |
| Two conflicting commands concurrently (`fix` + `drop`) | Deterministic order (event timestamps); drop wins if later; both audited |
| Digest reaction by unauthorized user | Ignored + metric (step 3 rules apply to reactions) |
| `retry` loop abuse | Hard cap (2 auto-retries) + per-user command rate cap (default 10/hour) |

## Acceptance criteria

1. Forged webhook → 401, nothing executed (contract).
2. Bot's own comments never trigger commands (unit w/ loop fixture).
3. Unauthorized commenter → refusal reply + audit + metric (unit).
4. Every command type has an e2e test with fake GitHub asserting the downstream dispatch (fix→WF-06 attempt; drop→suppression+close; etc.).
5. Digest: exactly one open digest issue per repo/day; ✅ releases exactly the marked candidates (e2e).
6. Autonomy enforcement: `observe` → zero agent dispatches under storm; `triage` → RCA but zero fix attempts unless `@wakey fix` (e2e, fake-LLM counters).
7. Retry cap: 3rd retry request → refusal reply (unit).
