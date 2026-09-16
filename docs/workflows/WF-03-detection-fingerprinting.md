# WF-03 — Detection, Fingerprinting & the Wake-Up Decision

> Behavioral contract for the anti-noise heart. Features: DET-1..13, TIK-3 (updates) · Tasks: E5-*. Summary: `docs/03-end-to-end-workflow.md` §B1–B3.

## Purpose & trigger

Consume queued LogEvents → parse traces → fingerprint → dedup → decide **wake / record / suppress**. This workflow decides *whether anything downstream happens*; everything after it (WF-04+) is expensive and must be rare by design (C2 economics).

## Actors & I/O

- **Consumes**: queue of LogEvents.
- **Emits**: fingerprint updates; wake signals (to WF-04); metrics; suppression decisions.
- **Pure computation + DB**: no network, no LLM. Fast path only.

## Steps

1. **Trace extraction** (`DET-2`): detect stack traces in message(s) — Python (`Traceback (most recent call last): … File "…", line N, in fn`), Java (`at com.x.Y.method(File.java:123)`), JS/TS (` at fn (file:line:col)`), PHP (`#0 /path/file.php(22): Class->method()…`). Normalize frames to `(path, line, function)`; tolerate old formats (no line numbers, obfuscated/minified JS handled best-effort with sourcemap note). No trace → event is a plain message (spike logic still applies).
2. **Message templating** (`DET-3`): replace volatile tokens with slots: numbers, UUIDs, hex ids ≥8 chars, IPs, quoted strings (configurable aggressiveness per service), ISO timestamps, emails (also for privacy). Result: a stable template string.
3. **Fingerprint computation**: `fp = H(template, top-K frames (K=3), language, service, environment)` — environment-scoped so staging noise never opens prod tickets (`DET-12`). Persist fingerprint row on first sight with `first_seen`, `last_seen`, `state=new`.
4. **Occurrence accounting** (`DET-4`): every event bumps the fingerprint's counters (total, per-minute sliding window, per-hour). Burst rule: N identical within window = one fingerprint + counts. Counters live in DB → restart-safe (NFR-3).
5. **Severity classification** (`DET-5`): label each event: `panic` (process fatal), `unhandled-exception` (trace w/ top-level handler absent), `http-5xx`, `dependency-error` (client/network errors in known libs), `resource` (OOM/backpressure strings), `other`. Fingerprint severity = max seen in window.
6. **Suppression check** (`DET-7`): fingerprint on suppression list → record-only, no wake; entry expiry honored (expired → wake again). Suppression entries carry reason + expiry + author (audit).
7. **Threshold decision** (`DET-6`): wake iff any of:
   - severity ∈ {panic, unhandled-exception} and count ≥ `wake.immediate_count` (default 3 in 5 min);
   - http-5xx/other error-rate ≥ `wake.rate_per_min` (default 5) over sliding window;
   - resource/dependency events ≥ `wake.infra_count`;
   - **novel-template spike** (P2, `DET-11`): message template unseen in baseline period appearing ≥ N times/min.
   `observe` autonomy (contract resolved 2026-09-16, aligning this section with WF-07 §6): the decision logic runs unchanged and the wake signal **is emitted** so the ticket exists for humans; what observe suppresses is **agent dispatch** — no RCA, no fixes (assertable in tests via agent/LLM-call counter = 0, WF-03 §6/§54 unchanged). Tickets created under observe are never queued for RCA.
8. **Baseline bookkeeping** (P2, `DET-10`): per-service hourly error-rate baselines update continuously; thresholds may auto-widen for chronically noisy services (documented, capped, reversible).
9. **Emit wake signal** → WF-04 with payload: fingerprint, severity, counts, sample events (redacted), deploy correlation request. Maintenance windows (`DET-8`, P2): during window, wake signals queue instead of firing (post-window evaluation uses queued counts).

## State written

`fingerprints` (new/updated: counts, severity, state), `occurrence_windows`, `suppression_entries` (reads + expiry transitions), `audit` for suppression add/remove.

## Failure modes

| Failure | Expected behavior |
|---|---|
| Parser can't handle a trace shape | Fall back to template-only fingerprint (no frames) + `parse_degraded` metric; never crash the consumer |
| Ambiguous language detection | Prefer frames that parse; mark `language=unknown`; fingerprint still stable |
| Template too aggressive (everything collapses) | Aggressiveness config per service; dashboard "fingerprint too broad" hint when distinct templates merge (P2 heuristic); property tests keep false-merge rate ≤1% on corpus |
| Clock skew across events | Use event timestamps when sane, delivery time otherwise; windows tolerant to ±5m skew |
| Queue consumer crash | Messages unacked → redelivered; step 4 counters are idempotent per event id (jointly with WF-02 step 3) |
| DB unavailable | Consumer pauses with backoff (messages wait in queue); no in-memory-only state that would be lost |

## Acceptance criteria

1. Property test: 10k mutations of the same underlying error (ids/numbers/paths churn) → ≤2 distinct fingerprints.
2. Corpus test: distinct error classes → distinct fingerprints ≥99% (no false merges >1%).
3. 500 identical events in 60s → exactly 1 fingerprint, count=500, **zero** wake-signal duplicates.
4. Threshold boundary tests at exact counts (3rd event wakes at default `immediate_count`).
5. Suppression: active → no wake; expired → wake resumes (fake-clock unit).
6. `observe` autonomy → agent/LLM call counters remain zero across a full synthetic storm (e2e).
7. Environment scoping: identical error in staging and prod → 2 fingerprints (unit).
8. Language corpus: ≥95% frame extraction on the P1 corpus; degradation metric on the rest.
