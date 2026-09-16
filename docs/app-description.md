# SPDX-License-Identifier: Apache-2.0
# 🌙 Wakey — the AI teammate that wakes up when your errors do

> [!TIP]
> **For the GitHub App's Description field, paste this one-liner:**
>
> ```text
> Wakey — self-hosted AI SRE. Watches my services' logs, opens tickets for recurring errors, investigates root cause, and opens draft fix PRs for human review. Never merges anything.
> ```

---

## The problem it solves

Companies run more services than anyone has time to watch. When
something breaks at 2am, triage starts with *"whose code is this?"* and
ends hours later. Meanwhile AI coding agents ship more code than ever —
and production errors climb with them. The gap isn't writing code
anymore. It's **watching and repairing what's already running**.

## What Wakey does

Wakey is a **self-hosted AI SRE**. It sits next to your services, reads
their logs, and acts like the teammate you wish were on call:

| Step | What happens |
|:---:|---|
| 👁️ **Watches** | Ingests logs from GCP, webhooks, or plain text — no SDK changes to your apps |
| 🔎 **Remembers** | Deduplicates noise into error *fingerprints*: one family, one trackable incident |
| 🎫 **Wakes you up properly** | Opens a GitHub ticket with redacted context, the likely code-level cause, and a live occurrence counter |
| 🧠 **Investigates** | Classifies the root cause — *code-fix, config, infra, needs-human* — with confidence, and posts the analysis to the ticket |
| 🛠️ **Proposes the fix** | Writes a failing reproduction test first, patches until your own suite is green, and publishes the result as a **draft PR** — tested tree committed, not a diff pasted into a comment |

```
logs ──▶ redact ──▶ fingerprint ──▶ policy ──▶ ticket
                                              │
                                 RCA ◀────────┘
                                  │
                       repro-first fix ──▶ draft PR ──▶ human review
                                              │
                                     post-merge verification ──▶ confirmed ✔
```

## Built on trust, not hope

- 🔒 **Self-hosted, open source.** Runs on your hardware, Apache-2.0.
  Your logs and code never leave your infrastructure — the optional AI
  tier talks only to the model endpoint *you* configure.
- 🙅 **Never merges. Never force-pushes.** Every fix is a draft PR for
  human review. Autonomy is a dial you control: `observe → triage → fix`.
- 🧪 **Repro-first or nothing.** A patch is only proposed after a failing
  test proved the bug — and that repro is published with the fix.
- 🕵️ **Redacts before it remembers.** 18 secret families (cloud keys,
  tokens, JWTs, cards) are scrubbed before storage and again before any
  model prompt.
- 📜 **Tamper-evident audit trail.** Tickets, comments, dispatches, and
  verdicts land in a hash-chained audit log — every action is provable.
- 🪶 **Runs on a $5 VPS.** Lean runtime, bounded memory budgets, capped
  fix execution inside a network-less container, enforced in CI.

## Permissions it asks for — and why

| Permission | Level | Why |
|---|:---:|---|
| Issues | read/write | Open tickets, post RCA and verdicts, close/reopen on lifecycle rules |
| Contents | read/write | Read `wakey.yml` (your per-repo config); publish fix branches |
| Pull requests | read/write | Open **draft** fix PRs for human review |
| Metadata | read-only | Resolve the default branch and repo identity |

That's the whole surface. No org administration, no secrets scanning, no
webhook management beyond its own.

## Get started

```bash
git clone https://github.com/wakey-ai/wakey && cd wakey
cp .env.example .env    # add WAKEY_MASTER_KEY + your GitHub token
make install && wakey serve
```

Open `http://localhost:8477`, paste the setup token from the console,
register a service, point your logs at the ingest URL — and let the
night watch take over.

- 📖 Setup guide: [recipes/github-app-setup.md](recipes/github-app-setup.md)
- 🔐 Security posture: [threat-model.md](threat-model.md)
- 🛠️ Operations runbook: [operations.md](operations.md)

---

<sub>Wakey is Apache-2.0 licensed. Suggest-don't-merge is a product
invariant, not a setting.</sub>
