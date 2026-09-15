# Local Two-Service Pipeline Test — plan (pre-development)

> Founder request (2026-09-15): "run a test with a second dummy service… test locally." Status: **planned, not executed** — the project is under the planning-only lock and nothing is built yet (no wakeyd exists; there is no "first" service yet either). This doc defines exactly what will run the moment development starts; it becomes the `make demo-two-services` target and seeds E1-T5 fixtures + the M0 gate script.

## Scope — what this test proves

A thin vertical slice of the pipeline, **two services side by side**, no GitHub, no LLM:

```
dummy-service-a ──┐
                  ├──▶ POST /ingest/<key> ─▶ redact ─▶ fingerprint ─▶ dedup ─▶ policy ─▶ TICKET SINK (fake: console/JSONL)
dummy-service-b ──┘
```

Proven by assertions (each maps to an existing AC):

| # | Scenario | Expected | AC source |
|---|---|---|---|
| 1 | service-a bursts 50 identical errors | 1 fingerprint, count=50, 1 ticket | DET-4, WF-03 AC3 |
| 2 | same error class in service-b | **different** fingerprint (service scoping), own ticket | WF-03 §3 |
| 3 | same error in service-a with churned ids/uuids/ips | same fingerprint (templating) | DET-3 AC1 |
| 4 | log line contains AWS key + JWT | `[REDACTED:*]` in stored event & ticket draft; zero raw matches | SEC-1 AC1 |
| 5 | service-b in "staging" env label | separate fingerprint from prod-labeled service-a (env scoping) | DET-12 AC7 |
| 6 | 2 services × 100 mixed events incl. unknown-format lines | good lines processed, bad lines dead-lettered with reason, no crash | WF-02 §8 |
| 7 | idle after run | process RSS <120MB, CPU ≈0 (host-safe caps active) | NFR-6, eng-standards §3 |

**Explicitly out of scope**: GitHub tickets (FakeSink writes JSONL + pretty console card instead), LLM/RCA (fake silent tier — nothing here needs any API key), fix proposals.

## The two dummy services (become E1-T5 fixtures)

- **`demo-service-a` ("payments-api")** — Python, **plain-text** logs with Python tracebacks; emits `Connection refused to payments-db:{port}` and a `TypeError` traceback; ids/ports churn per line (scenario 3); injects secret-bearing lines (scenario 4).
- **`demo-service-b` ("checkout-web")** — **JSON-lines** logs (different format proves DET-1 multi-format), emits the *same* `Connection refused` message class (scenario 2) plus unique checkout errors; labeled `env=staging` for scenario 5.

Both are tiny scripts with a rate-limited emitter (token-bucket — host-safe per eng-standards §3) reading a scenario number.

## Procedure (once E2-T1..T6, E4-T1..T3, E5-T2..T3 land — i.e., early Wave 3/4)

```bash
git checkout main && docker compose up -d          # wakeyd only, SQLite mode
./demo/emit.sh service-a scenario1-4 &             # ~50 events, capped rate
./demo/emit.sh service-b scenario2,5               # same-class error, staging env
# then the assertion runner:
make test-demo-two-services                        # asserts table rows 1–7, exits 0
```

Ticket Sink (fake) prints per ticket: fingerprint hash, service, count, template, redacted excerpt — machine-checked from the JSONL file.

## AI / keys stance (recorded as policy)

- This test **needs no LLM**: the model router runs its `silent` fake tier; RCA/fix simply don't fire.
- **Wakey will never extract credentials from another tool's storage** (Claude Code, IDE sync, shell history). If/when an LLM tier is tested, the founder pastes a key into `.env` (gitignored) — or points at a local Ollama endpoint. Adding this to SKILL.md §8 as a never-rule.
- When RCA testing starts (M1), the same two-service demo is reused with a live model profile; cost metering visible per run.

## Exit criteria

All 7 assertions green in one `make` run; no host-safety violations; FakeSink output matches golden files. This demo then doubles as the M0 gate rehearsal (swap FakeSink → GitHub adapter).
