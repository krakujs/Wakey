# WF-15 — Forge Abstraction & Multi-VCS (GitHub, GitLab, any forge)

> Behavioral contract for version-control-system support. Features: FORGE-1..6 · Tasks: E17-*. Requirement (founder, 2026-09-15): **Wakey must connect with any GitLab / other version-control system easily.** GitHub is the first *adapter*, never the core.

## Principles

1. **Forge-neutral core.** Tickets (WF-04), fix proposals (WF-06), review commands (WF-07), and verification (WF-08) operate on a **ForgePort** interface. No component outside a forge adapter imports a forge SDK or calls a forge API directly — enforced by an architecture test (imports of `github/`, `gitlab/`, … are legal only inside adapters).
2. **GitHub = reference dialect.** Docs say "issue/PR" because GitHub came first; conceptually these are **tickets** and **fix proposals (MRs)**. Each adapter maps to its forge's native objects.
3. **A forge is done when it passes the Forge-gate** — the G-gate checklist (execution-plan §2) executed *for that forge*: recorded fixtures for every API call, live sandbox suite, webhook robustness, failure drills, least-privilege proof, fresh-install drill.
4. **Community forges are plugins.** The plugin system (WF-14) grows a `forge` extension point, so new forges land without touching core — same contract, same conformance kit.

## ForgePort (the abstraction)

Capabilities the core uses, per service (config: `forge: github | gitlab | gitea | …` in `wakey.yml`):

| Capability | Meaning | Notes |
|---|---|---|
| `identity` | connect/auth (app install, token), actor identity for audit | least privilege per forge |
| `repos` | clone URLs, default branch, file access at ref, deploy/commit metadata | feeds DET-9 |
| `tickets` | create/comment/label/close/reopen + reactions (digest approvals) | WF-04 |
| `proposals` | branch, open **draft** proposal, update, merge-state, merge/closed events | WF-06 — draft semantics per forge |
| `events` | webhook receive → normalized `ForgeEvent` (comment-command, proposal-merged, push/deploy) | auth per forge (HMAC/token) |
| `commands` | comment-command parsing + authorized-responder check | WF-07 |

Core also defines the **normalized vocabulary**: ticket states (`new/investigating/open/closed-auto/closed-human/reopened`), proposal states (`draft/ready/merged/closed`), verdict labels — adapters translate.

## Adapter mappings (first-class)

| Concern | GitHub (reference, P1) | GitLab (P2) | Gitea/Forgejo (P3) |
|---|---|---|---|
| Auth | GitHub App via manifest (ONB-3); PAT fallback | Project/group **access token** (scope: api, read_repository, write_repository); self-managed = any base URL | token |
| Tickets | Issues + labels | Issues + labels | Issues + labels |
| Fix proposals | **Draft PR** | **Draft MR** (`Draft:` title prefix) | Draft PR (WIP:) |
| Digest approval | ✅ reaction | 👍 award emoji | reaction |
| Commands | issue/PR comments | issue/MR comments | comments |
| Webhooks | `issue_comment`, `pull_request`, `push`, `deployment` | `note`, `merge_request`, `push`, (deploy via webhook or CI) | `issue_comment`, `pull_request`, `push` |
| Rate limits | primary + secondary | per-token quotas (self-managed: config) | config |
| Clone | via app/token | token-embedded HTTPS | token |

## GitLab adapter specifics (FORGE-2)

- **gitlab.com and self-managed**: base URL is config; self-managed API version pinned + conformance-tested against supported versions (drift = adapter bug).
- Onboarding parity: wizard (WF-01) gains a forge step — GitHub App flow (existing) or GitLab token flow (paste token → validate scopes → pick repos). Group-level tokens supported.
- Webhooks: wakeyd registers per-project webhooks with a GitLab token; secret verification per GitLab's scheme; dedup identical to WF-02.
- Draft MRs: opened with `Draft:` prefix; readiness flips by title/command; **never merged by Wakey** (invariant §1.1 holds on every forge).
- Notifications/verdicts: same templates, forge-native rendering.

## Failure modes

| Failure | Expected behavior |
|---|---|
| Forge API outage | Same degradation ladder as GitHub (WF-11 L3): fingerprints accumulate, catch-up replay on recovery — per service's forge |
| Token expired/revoked, scopes narrowed | Clear per-service banner + `doctor` failure pinpointing the forge; queue keeps counting; no silent ticket loss |
| Self-managed GitLab upgraded, API shape changed | Version-pinned adapter warns on mismatch; conformance suite failure blocks that version from "supported" list |
| Draft semantics unsupported (forge without draft concept) | Proposal opens as normal MR/PR labeled `wakey/draft-equivalent` + "not ready" marker; core logic keyed on our normalized state, not the forge's |
| Webhook scheme differs (secret/verification) | Per-adapter verification module; forgery → 401; recorded-fixture tests per forge |
| Command loop via bot's own comments | Bot identity known per forge; self-comments ignored on every adapter (WF-07 §2.1 holds) |

## Acceptance criteria

1. **Architecture test**: no module outside `forge/<adapter>/` imports a forge SDK/client (CI-enforced import-linter) — proves FORGE-1.
2. **Forge-gate per adapter**: GitHub certified at M0 (existing G-gate); GitLab certified before its GA — same checklist, live sandbox on gitlab.com *and* one self-managed instance.
3. **Parity test**: a fixture repo mirrored on GitHub and GitLab, same synthetic error stream → equivalent tickets, RCA comments, draft proposals, verdict transitions (golden files per forge).
4. Command/response parity: `@wakey fix|explain|retry|drop|reopen|status` behave identically across adapters (contract tests).
5. Config switch: changing a service's `forge:` key (with mirrored repo) migrates cleanly; doctor validates the new forge end-to-end.
6. Failure drills from §2 executed per adapter (revoked token, narrowed scope, deleted objects, rate-limit backoff).
