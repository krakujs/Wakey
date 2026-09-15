# Wakey — MVP architecture & roadmap

Companion to [`01-competitive-landscape.md`](01-competitive-landscape.md). Positioning: **open-source, deployment-agnostic "watch → ticket → RCA → suggested fix" agent for GitHub-hosted codebases, tuned for legacy services.**

## Product principles

1. **Reacting fast is the product; fixes are suggestions.** Wakey's promise is "someone is always watching." Every autonomous action degrades gracefully: worst case is a well-triaged ticket, never a broken repo, never an auto-merge.
2. **Setup in minutes, no code changes.** GitHub App install + one log connector + one `wakey.yml`. If a legacy service can emit logs to its platform, Wakey can watch it.
3. **Self-hostable from day one.** Same binary powers OSS users and (later) the hosted cloud offering.

## Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │                  wakeyd (core)              │
 Log sources            │                                             │
 ───────────────        │  ┌──────────┐   ┌───────────┐   ┌────────┐  │
 GCP Cloud Logging ───▶ │  │ Ingest   │──▶│ Fingerprint│──▶│ Triage │  │
 AWS CloudWatch    ───▶ │  │ adapters │   │ + dedup +  │   │ policy │  │
 Azure Monitor     ───▶ │  │ (pull/   │   │ severity/  │   │ (rate  │  │
 Generic webhook   ───▶ │   push)   │   │ throttling │   │ limits)│  │
 docker/journald   ───▶ │  └──────────┘   └───────────┘   └───┬────┘  │
                        │                                     │       │
                        │   ┌──────────┐   ┌──────────────┐   ▼       │
                        │   │ Fix agent│◀──│ RCA agent    │ issue?   │
                        │   │ (draft PR│   │ (clone, map, │ create   │
                        │   │  + tests)│   │ diff, analyze│ ticket   │
                        │   └────┬─────┘   │ classify)    │ (GitHub) │
                        │        │         └──────────────┘          │
                        └────────┼──────────────────────┼────────────┘
                                 ▼                      ▼
                        Draft PR (human review)   Issue `wakey/triaged`
```

Components (all in one deployable for OSS; separable later for the cloud):

1. **Ingest adapters** — uniform `LogEvent` stream from: GCP Cloud Logging (Pub/Sub push → webhook, MVP), generic webhook (accepts Sentry/Datadog/anything JSON), `docker logs`/journald tail for self-hosted. Later: CloudWatch subscription filters, Azure Monitor, Loki, Elastic.
2. **Fingerprint & dedup engine** — error signature = normalized message template + top stack frames (the Sentry-fingerprint idea, log-flavored). Handles bursts (one incident, not 400 tickets), repeat-offenders, and severity/rate rules that decide "wake up" vs "record only". **This is the hardest and most important component.**
3. **Ticket creator** — GitHub issue labeled `wakey`, containing: fingerprint, occurrence counts/timeline, raw log excerpt (redacted), affected service, recent deploys/commits (via GitHub API), initial hypothesis.
4. **RCA agent** — shallow-clones the repo, maps the failing code path (search + framework-aware entry points), correlates with the deploy diff, then classifies: `code-fix` / `config` / `infra` / `known-noise` / `needs-human`, each with a confidence score and evidence links. Posts the RCA as an issue comment.
5. **Fix agent** — only for `code-fix` above the confidence floor: new branch, minimal patch, runs the repo's own tests (`wakey.yml` declares test command), opens a **draft PR** linked to the issue with: RCA summary, what changed and why, test results, confidence, rollback note. Never merges. Rebased/re-run on demand via PR comment (`@wakey retry`).
6. **Orchestrator** — work queue, per-repo concurrency, LLM cost budgets, kill switch, audit log of every autonomous action.
7. **Config-as-code** — `wakey.yml`: services → repo paths, test command, log redaction patterns, autonomy level (`observe | triage | fix`), severity thresholds, notification routing.

## Autonomy levels (the trust dial)

| Level | Behavior |
|---|---|
| `observe` | Fingerprint + summary issues only, no agent investigation |
| `triage` | + RCA comment + classification on each new fingerprint |
| `fix` | + draft PR when confidence ≥ threshold and tests pass |

Default on install: `triage`. `fix` is opt-in per repo. This directly encodes "not all errors need fixes — suggest, monitor, react."

## Legacy-codebase posture

- **No SDK required** — log-only ingestion is the wedge; Sentry needs instrumentation, we don't.
- **Language-agnostic** — the agent works from stack traces + repo search, not framework plugins.
- **Deploy-diff correlation** — for old systems, "what changed recently" is the highest-value RCA signal; we invest there.
- **Conservative patches** — style-following edits, minimal blast radius, always with tests or an explicit "no test suite found, lower confidence" flag.

## Tech stack (recommendation)

- **Python 3.12 + FastAPI** for the control plane (webhook ingest, GitHub App); richest ecosystem for log parsing + LLM tooling (LiteLLM for provider-agnostic models).
- **Worker**: simple asyncio queue → Postgres (SQLite mode for self-host single-box).
- **GitHub**: GitHub App (install UX, granular perms) with PAT fallback for early dev.
- **Fix engine**: own lean edit→test loop first (controllable, cheap); evaluate integrating OpenHands/SWE-agent as an alternative backend behind an interface once the loop is proven.
- Repo layout (monorepo): `core/` (ingest, fingerprint, orchestrator) · `agent/` (RCA + fix) · `connectors/` · `docs/`.

## Milestones

**M0 — Wake up (wks 1–2): "error in, ticket out"**
Repo skeleton, CI, GitHub App auth; GCP Cloud Logging adapter + generic webhook; fingerprint/dedup v1; auto-created `wakey` issue with log context and deploy diff. *Demo: break a demo service on Cloud Run → issue appears with the culprit commit.*

**M1 — Understand (wks 3–5): "ticket in, RCA out"**
RCA agent: clone → locate → analyze → classify with confidence; RCA comment on the issue; `wakey.yml` with autonomy levels; redaction pipeline. *Demo: issue gets a root-cause comment naming file, line, change, classification.*

**M2 — Suggest (wks 6–8): "RCA in, PR out"**
Fix agent → draft PR with tests; `@wakey retry`; confidence gating; **eval harness**: a bench of seeded bugs in fixture repos (SWE-bench-style, plus intentionally broken demo services) with pass/fail + noise-rate metrics. *Demo: real error → draft PR a human merges.*

**M3 — Ship it (wks 9–10): OSS launch**
Rate limits, cost caps, audit log, security review (secrets/PII, prompt-injection resistance — logs are attacker-controlled input!), docs + quickstart, LICENSE (Apache-2.0), GitHub Actions CI, launch post (Show HN, r/devops, Hacker Newsletter). Watch Keep/HolmesGPT communities for integration interest (Keep workflow action → Wakey, etc.).

## Key risks & mitigations

| Risk | Mitigation |
|---|---|
| Alert/PR spam destroys trust | Fingerprint quality is M0's main deliverable; hard per-repo PR caps; default `triage` mode; confidence floor |
| Logs contain secrets/PII sent to LLMs | Redaction before storage & prompts; self-host = data stays in-bounds; document exactly what leaves |
| Prompt injection via log content | Treat logs as untrusted input; no shell from raw log text; tool-allowlist per stage |
| LLM cost runaway | Per-repo budgets, dedup before agent runs, cheap-model triage → strong-model fix only |
| Incumbents converge (GitHub, Sentry, Datadog) | Win on openness, provider-agnosticism, and 5-minute setup; integrate with them (webhook in) rather than replace them |

## Decision needed before M0

1. Repo/license: confirm Apache-2.0 + monorepo layout above.
2. First connector priority: GCP Cloud Logging confirmed as MVP per your stack — is CloudWatch needed for the first design partners?
3. Branding: "Wakey" (working name) — check trademark/GitHub name availability before launch.
