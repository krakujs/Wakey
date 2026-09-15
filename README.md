# Wakey

> The AI teammate that wakes up when your errors do. It watches your services' logs, and the moment something breaks it opens a ticket, finds the root cause, and opens a pull request with a suggested fix — before your on-call engineer has finished reading the alert.

## The problem

Companies run more and more services, many of them legacy codebases built years ago that the business depends on and keeps building on top of. Nobody has time to watch all of them. When something breaks at 2am, triage starts with "whose code is this?" and ends hours later.

AI coding agents are shipping more code than ever — and production errors are jumping accordingly. The gap isn't writing code; it's **watching and repairing** what's already running.

## What Wakey does

1. **Connects to your GitHub repository** (GitHub App — install, select repos, done).
2. **Ingests logs from wherever the service runs** — GCP Cloud Logging, AWS CloudWatch, Azure Monitor, a generic webhook (Sentry, Datadog, Loki…), or bare `docker logs`. No SDK changes to your legacy code.
3. **Wakes up on errors and vulnerabilities**: deduplicates them into fingerprints, and for anything significant **creates a ticket on its own** with the log context and the likely code-level cause.
4. **Investigates root cause**: clones the repo, maps the failing code path, checks what changed recently (deploys, commits, PRs), and classifies the issue: *needs a code fix / config problem / infra problem / known noise*.
5. **Opens a pull request with a suggested fix** when — and only when — the agent is confident the fix is a code change. Every PR is a **suggestion for human review**; Wakey never merges anything.

Monitoring and reacting fast is the product; the fix PR is the bonus.

## Status

🚧 **Planning complete — build not started.** Open source and self-hosted first: you run `wakeyd` on your infrastructure, log in with GitHub (self-owned GitHub App), and point it at your logs.

**For AI agents working on this repo: start with [`SKILL.md`](SKILL.md), then [`docs/STATE.md`](docs/STATE.md) — it tells you exactly how to operate.**

Document map:

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | Operating manual for AI agents: invariants, session protocol, build order, phase gates |
| [`docs/STATE.md`](docs/STATE.md) | Living project state: epic board, gate evidence, decision log, open decisions |
| [`docs/TASKS.md`](docs/TASKS.md) | Complete task backlog: E1–E13, 80+ tasks with acceptance criteria, dependencies, sizes |
| [`docs/engineering-standards.md`](docs/engineering-standards.md) | The "production grade" bar: definition of done, testing, CI/CD, release, security checklist |
| [`docs/workflows/`](docs/workflows/) | **11 behavioral specs** (WF-01…WF-11): exactly how each workflow must work, with failure modes and acceptance criteria |
| [`docs/design.md`](docs/design.md) | Wakey design system: the "night watch" brand — tokens, components, motion, site blueprint, dashboard application |
| [`docs/01-competitive-landscape.md`](docs/01-competitive-landscape.md) | Market research (Sep 2026) and the gap Wakey targets |
| [`docs/02-mvp-plan.md`](docs/02-mvp-plan.md) | Architecture, tech stack, milestone overview |
| [`docs/03-end-to-end-workflow.md`](docs/03-end-to-end-workflow.md) | Big-picture behavioral spec: setup journey + runtime loop narrative |
| [`docs/04-improvements.md`](docs/04-improvements.md) | Design decisions that differentiate Wakey (repro-first fixes, approval-queue mode, …) |
| [`docs/05-feature-inventory.md`](docs/05-feature-inventory.md) | Canonical feature list: ~80 features across MVP / GA / Moat phases (incl. the legacy estate-management layer), with referenceable IDs |

## License

Open source from day one (planned: Apache-2.0).
