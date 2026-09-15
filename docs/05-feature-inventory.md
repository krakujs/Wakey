# Wakey — Complete Feature Inventory (v1 planning baseline)

**This is the canonical feature list.** Every roadmap item, ticket, and design doc should reference these IDs. Phases:

- **P1 — MVP ("the loop")**: one service, one repo, one cloud — error in → deduplicated ticket → RCA → suggested-fix PR → verified. Small but real, end to end.
- **P2 — GA ("trust & breadth")**: enough connectors, notifications, controls, and polish that a random team can adopt it without talking to us.
- **P3 — Moat ("the differentiators")**: the things per-repo and per-vendor tools structurally can't do.

**Non-negotiable invariants across all phases:** Wakey never merges; logs are data not instructions; redact before store/prompt; every autonomous action is capped and audited.

---

## 1. Setup & onboarding

| ID | Feature | What it does | Phase |
|---|---|---|---|
| ONB-1 | One-command self-host install | `docker compose up` (SQLite mode) with a production Postgres path; single-binary later | P1 |
| ONB-2 | Setup wizard | Web dashboard walks: host → GitHub → services → logs → verify | P1 |
| ONB-3 | GitHub login (self-owned App) | User creates their own GitHub App from a pre-filled manifest; wakeyd stores only the install credential, encrypted. Per-repo consent, revocable | P1 |
| ONB-4 | PAT fallback | Fine-grained PAT for early dev/air-gapped setups; documented as non-preferred | P1 |
| ONB-5 | `wakey.yml` config-as-code | Per-repo config: service paths, test command, autonomy, thresholds, redactions, notify — with JSON schema + editor validation | P1 |
| ONB-6 | Config validation & drift report | Startup + wizard validation; dashboard shows config errors per repo | P1 |
| ONB-7 | `wakey doctor` | Synthetic error through the real pipeline → auto-created and auto-closed test ticket; checks GitHub perms, ingest health, LLM reachability | P1 |
| ONB-8 | Monorepo support | One repo → many services via path mapping; fingerprints and PRs scoped to service path | P1 |
| ONB-9 | Multi-repo batch onboarding | Install on N repos at once; sensible defaults, zero-config start at `triage` | P2 |
| ONB-10 | Demo sandbox | One-click throwaway repo seeded with broken services to watch the loop work before pointing at real code | P2 |
| ONB-11 | Multi-tenant wakeyd | One deployment, multiple GitHub orgs/accounts isolated from each other | P3 |

## 2. Ingestion & connectors

| ID | Feature | What it does | Phase |
|---|---|---|---|
| ING-1 | Ingest API with per-service keys | `POST /ingest/<service-key>`; key rotation; per-key rate limits | P1 |
| ING-2 | GCP Cloud Logging adapter | Logs Router sink → Pub/Sub push subscription → ingest; OIDC token verification | P1 |
| ING-3 | Generic webhook adapter | Any JSON emitter (Sentry, Datadog, Loki, custom) with HMAC signature verification; format auto-detection | P1 |
| ING-4 | Docker/journald sidecar | `wakey-agent` container tails local containers for self-hosted services | P1 |
| ING-5 | AWS CloudWatch adapter | Subscription filter → Firehose HTTPS → ingest | P2 |
| ING-6 | Azure Monitor adapter | Diagnostic setting → Event Hub → forwarder | P2 |
| ING-7 | Kubernetes events adapter | Cluster events (crashloops, OOMKills, probe failures) as first-class events | P2 |
| ING-8 | Deploy/release events | Cloud Run revisions, GitHub deployment statuses, k8s rollout annotations — the correlation backbone (see DET-9) | P1 |
| ING-9 | Vulnerability event sources | Dependabot alerts, `osv-scanner`, CodeQL alert webhooks into the same pipeline | P2 |
| ING-10 | Adapter SDK | `BaseAdapter` + recipe template so the community ships connectors (Fly, Render, Heroku, Nomad…) | P2 |
| ING-11 | Hardened transport | Idempotency keys (platforms retry), backpressure with 429 + Retry-After, payload size limits | P1 |
| ING-12 | Raw event retention & replay | Configurable retention of stored (redacted) events; replay into pipeline for debugging | P2 |
| ING-13 | Trace/request-ID extraction | Pull trace/span/request IDs where present — powers cross-service causality later | P1 (extract) / P3 (use) |

## 3. Detection & noise control (fingerprint engine)

| ID | Feature | What it does | Phase |
|---|---|---|---|
| DET-1 | Multi-format log parsing | JSON lines, logfmt, plain text; tolerant of mixed output from legacy services | P1 |
| DET-2 | Multi-language traceback parser | Python, Java, JS/TS, PHP (+ Ruby, Go, C# in P2); old formats included (Java 8, PHP 5, Django 1.x) | P1 (Py/Java/JS/PHP), P2 (rest) |
| DET-3 | Error fingerprinting | Stable ID from normalized message template + top stack frames; survives restarts, redeploys, log rotation | P1 |
| DET-4 | Burst collapsing & occurrence counting | 500 identical errors in 60s = one fingerprint with a counter, never 500 tickets | P1 |
| DET-5 | Severity classification | Panic/OOM/unhandled-exception/5xx-spike/dependency-down tiers | P1 |
| DET-6 | Threshold rules | Wake only on severity × rate (configurable per service); everything else recorded | P1 |
| DET-7 | Suppression list | Known-noisy fingerprints silenced with expiry + reason (auto-expires so silences rot) | P1 |
| DET-8 | Maintenance windows | Scheduled no-wake periods for migrations/deploys | P2 |
| DET-9 | Deploy correlation | New fingerprint → "last-good → first-bad" commit/PR automatically in the ticket | P1 |
| DET-10 | Baseline learning | Per-service normal error rates; policy adapts to what "normal" means for a noisy legacy system | P2 |
| DET-11 | Anomaly detection | Novel-message spikes (not just known error shapes) raise flags | P2 |
| DET-12 | Environment scoping | Fingerprints scoped per environment (staging vs prod); staging-only rollout mode for adopting teams | P2 |
| DET-13 | Runbook links | Attach runbooks/docs to fingerprints; RCA references them; suppression entries can point at "accepted known error" runbooks | P2 |

## 4. Ticketing (GitHub issues)

| ID | Feature | What it does | Phase |
|---|---|---|---|
| TIK-1 | Auto-ticket creation | Rich issue: fingerprint, counts/timeline, redacted log excerpt, deploy correlation, dashboard link | P1 |
| TIK-2 | Label taxonomy | `wakey`, `wakey/sev-*`, `wakey/class-*`, `wakey/fp:<hash>` — filterable, scriptable | P1 |
| TIK-3 | Live occurrence updates | Throttled "+1, still happening ×142" comments on open tickets | P1 |
| TIK-4 | Auto-close on silence | Ticket closes when fingerprint is quiet past grace window, with evidence comment | P1 |
| TIK-5 | Regression reopen | Fingerprint returns after close → reopen + likely reintroducing commit range, never a fresh duplicate ticket | P1 |
| TIK-6 | Customizable templates | Issue/PR/comment templates overridable per org or repo | P2 |
| TIK-7 | External tracker sync | Mirror tickets to Jira/Linear (read state back) | P3 |

## 5. RCA (root-cause agent)

| ID | Feature | What it does | Phase |
|---|---|---|---|
| RCA-1 | Repo investigation | Shallow clone via the GitHub install; locate failing code path from stack frames + search (framework-agnostic — works on legacy stacks) | P1 |
| RCA-2 | Deploy-diff reasoning | Reads the last-good→first-bad diff as primary evidence | P1 |
| RCA-3 | Classification + confidence | `code-fix` / `config` / `infra` / `known-noise` / `needs-human`, each with confidence and cited evidence | P1 |
| RCA-4 | RCA report comment | Structured issue comment: root cause, evidence chain, suggested direction, what was ruled out | P1 |
| RCA-5 | `@wakey` commands | `fix` / `explain <line>` / `retry` / `drop` via issue and PR comments | P1 |
| RCA-6 | Config & infra routing | `config` → checklist comment (env/flags); `infra` → evidence-backed routing (quota, dependency down) | P1 |
| RCA-7 | Tiered model routing | Cheap model triages/classifies; strong model only for RCA/fix; per-service local-model option | P1 |
| RCA-8 | Cross-service causality | Trace-ID graph walking upstream before blaming the loudest service | P3 |
| RCA-9 | Org memory / similar incidents | Local-embedding retrieval of past resolved tickets & merged PRs ("looks like #77 — same retry pattern") | P3 |
| RCA-10 | Upstream dependency detection | Root cause in a dependency → prepares minimal repro for the upstream repo | P3 |

## 6. Fix agent (suggested fixes)

| ID | Feature | What it does | Phase |
|---|---|---|---|
| FIX-1 | Eligibility gate | `code-fix` ∧ confidence ≥ floor ∧ autonomy allows ∧ open PR cap not hit — else stops with reasons | P1 |
| FIX-2 | Repro-first fix | Writes a failing reproduction test from log evidence **before** patching; PR contains repro + minimal patch | P1 |
| FIX-3 | Minimal, style-following patches | Edits scoped to the service's path; matches repo conventions; no drive-by refactors | P1 |
| FIX-4 | Test execution | Runs the repo's own `test_command`; reports results in the PR | P1 |
| FIX-5 | Draft PR with full context | RCA link, what/why, test results, confidence, rollback note; **draft until a human touches it** | P1 |
| FIX-6 | Diff spot-check verifier | Second cheap model answers "does the diff do anything the RCA didn't justify / did log content leak in?" before a human sees it | P1 |
| FIX-7 | No-test-suite handling | Legacy repo without tests → no PR by default; opt-in lower-confidence PR with explicit flag | P1 |
| FIX-8 | Review feedback loop | `@wakey retry` with reviewer comments as context; regenerate on a new commit | P1 |
| FIX-9 | Approval-queue (digest) mode | Batched human approval: one daily digest issue, react ✅ to release candidate PRs — the middle autonomy step | P1 |
| FIX-10 | Scope & network isolation | Agent worktree isolation; test runs with no/limited network; resource limits | P1 |
| FIX-11 | PR hygiene | Auto-rebase when base moves; stale-PR reaper; never force-pushes | P2 |
| FIX-12 | Dependency-bump fixes | For vulnerability events (ING-9): PR upgrading the dep + changelog/CVE notes | P2 |

## 7. Verification & closing the loop

| ID | Feature | What it does | Phase |
|---|---|---|---|
| VER-1 | Post-merge fingerprint watch | Keeps watching the fingerprint after the fix ships | P1 |
| VER-2 | Success / insufficient verdicts | Silent past grace window → success comment + close; still firing → reopen "fix insufficient" + evidence | P1 |
| VER-3 | Rollback suggestion | Deploy-aware: "error rate +340% since 00047; last good 00046 — consider rollback while reviewing" | P1 |
| VER-4 | One-shot-per-fingerprint guard | Never silently re-fixes the same fingerprint after an insufficient verdict without human ack | P1 |
| VER-5 | Canary verification | Fix verified against a canary/staging environment before the PR is marked ready | P3 |

## 8. Notifications & human routing

| ID | Feature | What it does | Phase |
|---|---|---|---|
| NTF-1 | Notification policy | Per-service rules: which events notify (ticket, RCA, PR) and where | P2 |
| NTF-2 | Slack / Discord / email | First-class targets; messages carry the RCA, not just the alert | P2 |
| NTF-3 | Quiet hours & severity routing | Pages humans only when policy says so; rest waits in GitHub | P2 |
| NTF-4 | On-call escalation | PagerDuty/Opsgenie handoff — page carries the RCA so the human wakes to an answer | P3 |
| NTF-5 | Daily digest | "Wakey opened 2 tickets, has 3 candidate fixes, closed 1" — summary for low-touch teams | P2 |

## 9. Security & privacy

| ID | Feature | What it does | Phase |
|---|---|---|---|
| SEC-1 | Redaction engine | Built-in secret/PII patterns + custom regexes; applied at ingest, before storage, and again pre-LLM; redaction events audited | P1 |
| SEC-2 | Logs-are-data enforcement | Prompt-injection defenses: log content quoted as data, per-stage tool allowlists, no shell from log text, PR scope limited to service path | P1 |
| SEC-3 | Local LLM support | Ollama/vLLM endpoints as first-class providers — data can stay fully in-house | P1 |
| SEC-4 | Encrypted credential store | GitHub keys, service keys, LLM keys encrypted at rest; env-reference support | P1 |
| SEC-5 | Audit log | One JSON line per autonomous action (who/what/model/cost/decision); exportable | P1 |
| SEC-6 | Dashboard RBAC | Admin vs viewer; ties into GitHub identity | P2 |
| SEC-7 | Cost & rate budgets | Per-service and global caps: tickets/hour, concurrent investigations, open PRs, monthly LLM spend; graceful degradation to issues-only when exhausted | P1 |
| SEC-8 | Log retention & purge controls | Retention windows, hard-delete (GDPR-style) for stored events and fingerprints | P2 |

## 10. Administration & operations

| ID | Feature | What it does | Phase |
|---|---|---|---|
| OPS-1 | Web dashboard | Services, fingerprints, open tickets/PRs, agent runs, spend, config health; setup lives here too | P1 |
| OPS-2 | Config hot reload | `wakey.yml` and dashboard changes apply without restart | P1 |
| OPS-3 | Prometheus metrics | Ingest rates, fingerprints/h, time-to-ticket/RCA/PR, agent spend, error rates — `/metrics` | P1 |
| OPS-4 | Health endpoints & self-watch | `/healthz`; Wakey ingests its own errors (dogfooding) | P1 |
| OPS-5 | Storage choice | SQLite (single box) ↔ Postgres (production) behind one interface | P1 |
| OPS-6 | Backups & restore | Documented + tooling for state backup/restore | P2 |
| OPS-7 | REST API | Everything the dashboard does, as an API | P2 |
| OPS-8 | CLI | `wakey` CLI: doctor, service add, fingerprint list, replay, config validate | P2 |
| OPS-9 | Helm chart / k8s manifests | For teams running wakeyd on the same k8s as their services | P3 |
| OPS-10 | High-availability mode | Multi-replica ingest with shared queue for large estates | P3 |

## 11. Analytics & reporting

| ID | Feature | What it does | Phase |
|---|---|---|---|
| ANA-1 | Cost transparency | Per-investigation cost meter on tickets ("$0.42, 4 agent runs"); per-service spend rollups | P1 |
| ANA-2 | Impact report | Merged Wakey PRs, auto-closed tickets, time-to-fix trends, estimated hours saved — the "why we keep this" report | P2 |
| ANA-3 | Noise report | Top fingerprints by volume; suppression candidates surfaced automatically | P2 |
| ANA-4 | Confidence calibration report | Published eval-bench numbers: precision of fixes at each confidence band | P2 |

## 12. Extensibility & ecosystem

| ID | Feature | What it does | Phase |
|---|---|---|---|
| EXT-1 | Adapter SDK (see ING-10) | Community connectors with a stable contract + conformance tests | P2 |
| EXT-2 | GitHub Action | `wakey-action` for repos: run triage/fix on demand from CI | P2 |
| EXT-3 | MCP server | Expose fingerprints, RCAs, and repo state to any MCP client (Claude, Cursor) — Wakey as the production-context source for the team's other agents | P3 |
| EXT-4 | Outbound webhooks | Wakey events (ticket, RCA, PR, verdict) to any consumer | P2 |
| EXT-5 | Eval bench (open) | Fixture repos with seeded bugs + harness in the public repo; CI gate for agent quality; feeds ANA-4 | P1 |

## 13. Non-functional targets

| ID | Target | Phase |
|---|---|---|
| NFR-1 | Error-to-ticket latency: p95 < 60s from ingest | P1 |
| NFR-2 | Single wakeyd: 25 services / 50 repos / 5M events/day; Postgres path beyond | P1 (SQLite limits) / P2 |
| NFR-3 | At-least-once ingest with dedup; no ticket loss on restart | P1 |
| NFR-4 | Graceful degradation: LLM down/budget out → issues-only monitoring continues | P1 |
| NFR-5 | Works with zero SDK instrumentation in the target service | P1 (identity) |

---

## Phase summaries

- **P1 MVP — the loop works**: ONB 1–8 · ING 1–4, 8, 11, 13 · DET 1–6, 9 · TIK 1–5 · RCA 1–7 · FIX 1–10 · VER 1–4 · SEC 1–5, 7 · OPS 1–5 · ANA 1 · EXT 5. Roughly 45 features; demo-able as "2am error → 9am human reads the answer, merges the fix."
- **P2 GA — strangers can adopt it**: the remaining connectors, notifications, environments/baselines, RBAC, API/CLI, templates, digest, impact reports, eval-published calibration.
- **P3 Moat — why enterprises pick us and stay**: cross-service causality, org memory, upstream-issue mode, on-call handoff with RCA, canary verification, multi-tenant, MCP server.

## Decisions this list needs from you

1. **P1 connector scope**: GCP + generic webhook + docker sidecar confirmed as the MVP trio? (ING-5/6 wait for P2.)
2. **Language priority for tracebacks**: I assumed Python/Java/JS/PHP for P1 — swap in your legacy estate's real mix (e.g. Ruby/Go earlier?).
3. **Digest mode in P1 or P2?** I placed it in P1 (cheap, big trust win) — confirm.
4. **Local-LLM in P1**: first-class from day one, or follow right after? Affects SEC-3/RCA-7 sequencing.
