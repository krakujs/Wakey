# Wakey — making it way better (design improvements, prioritized)

Ranked by how much they move the product, not by effort. ✅ = folded into the MVP build (M0–M3), 🔭 = v1.x once the loop exists.

## A. Fix quality & trust — the make-or-break dimension

**A1. ✅ Repro-first fixes.** The fix agent must first write a **failing reproduction test** (from the fingerprint's log evidence), watch it fail, then patch until it passes. A PR that contains "repro test + minimal patch + suite green" proves the agent understood the bug instead of pattern-matching. This one rule is the biggest lever on fix quality and reviewer trust — and it gives teams a test they keep forever.

**A2. ✅ Approval-queue ("digest") mode.** Between `triage` and full `fix` autonomy: once a day (or on demand), Wakey opens **one** digest issue: "3 candidate fixes ready — react ✅ to open the PRs." Removes the trust cliff for cautious teams, still fully autonomous for the bold. Autonomy becomes `observe → triage → digest → fix`.

**A3. ✅ Confidence calibration, measured publicly.** Ship an eval bench (fixture repos with seeded bugs) in the OSS repo; CI runs the agent against it. Publish real numbers: "fixes land green 78% of the time; our 0.8 confidence is right 71% of the time." Calibrated honesty converts skeptics better than marketing.

**A4. ✅ Spot-check verifier.** A second, cheap model reviews every proposed diff against one question: "did log content (attacker-controlled) leak into this change, or does the diff do anything the RCA didn't justify?" Blocks the weird cases before a human ever sees them.

## B. Better root cause, not faster guesses

**B1. ✅ Deploys as first-class events.** Ingest deploy markers (Cloud Run revision labels, GitHub Actions `deployment` webhooks, k8s rollout annotations) alongside logs. Every fingerprint gets "last-good → first-bad" correlation automatically, tickets name the PR that caused it, and Wakey can say the underrated sentence: *"error rate +340% since deploy 00047; last good revision 00046; consider rollback while you review the fix."*

**B2. 🔭 Cross-service causality.** Legacy estates fail in chains (checkout breaks because payments times out). Correlate shared trace/request IDs across services into a lightweight service graph; RCA walks upstream before blaming the service that screamed loudest. The per-repo tools (Sentry, Copilot) structurally can't do this — it's the enterprise moat.

**B3. 🔭 Org memory (past-incident retrieval).** Every resolved ticket + merged PR becomes local embeddings; new fingerprints retrieve "this looks like incident #77 — same retry-without-backoff pattern." Converts years of tribal knowledge about a legacy system into an on-call teammate. Stays self-hosted (local embedding model).

**B4. ✅ Multi-language traceback parsing.** Python/Java/JS/PHP/logfmt/JSON logs out of the box, tolerant of old formats (Django 1.x, Java 8, PHP 5). This is unglamorous moat work: it's exactly why the tool works on *legacy* stacks where the pretty tools give up.

## C. Economics — make the CFO as happy as the SRE

**C1. ✅ Tiered model routing.** Cheap/fast model for classification and dedup questions; strong model only for RCA and fixes; **local model** (Ollama/vLLM endpoint) for PII-heavy services. Visible cost meter per investigation ("this ticket cost $0.42 in 4 agent runs") — cost transparency is a feature.

**C2. ✅ Dedup-before-LLM discipline.** Agents never run on raw volume: policy gate admits one investigation per fingerprint. The fingerprint engine isn't just anti-spam, it's the cost control.

## D. Wider reach — more things Wakey watches

**D1. ✅ Vulnerability events through the same pipe.** `osv-scanner`/dependabot alert webhooks become just another event source → same ticket → same fix agent → PR that bumps the dependency + release-note check. Covers the "new vulnerability" half of the product promise for near-zero extra plumbing.

**D2. 🔭 Upstream-issue mode.** When RCA concludes the bug is in a *dependency* (the legacy special: your code is fine, the library is broken), Wakey drafts a minimal-repro issue for the upstream repo and files it with the team's GitHub identity, linking it back to the internal ticket.

**D3. 🔭 On-call handoff.** PagerDuty/Slack escalation per `notify:` policy with quiet hours; Wakey pages a human only when policy says so — and the page *carries the RCA*, so the human wakes up to an answer, not a question.

## E. Operability & OSS growth

**E1. ✅ Metrics + self-monitoring.** Prometheus `/metrics`: ingest rates, fingerprints/hour, time-to-ticket, time-to-RCA, time-to-PR, agent spend. Wakey watches itself with its own pipeline (dogfooding = free QA + great demo).

**E2. ✅ Adapter SDK.** `BaseAdapter` + one-page recipe template so the community adds platforms (Fly.io, Render, Heroku, Nomad…). The connector list becomes the community's growth loop, not our backlog.

**E3. ✅ Hardened ingest.** Idempotency keys (log platforms retry), backpressure with 429+retry-after, HMAC/OIDC verification, per-service keys. Boring, but this is production trust.

**E4. ✅ Hot-reload config.** `wakey.yml` edits apply without restart (mtime-watch), so tightening thresholds during an incident is instant.

## Not doing (deliberately)

- **Auto-merge, ever.** It would kill the trust the product runs on.
- **A chat UI for the loop.** GitHub issues/PRs *are* the UI; a chat surface splits attention and re-implements what GitHub already nails.
- **Agent framework of the month.** A lean, inspectable edit→test loop beats a heavy agent stack for auditability and cost.
