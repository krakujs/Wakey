# SPDX-License-Identifier: Apache-2.0
# Recipe: Creating the GitHub App (WF-01 §3, E3-T1)

Field-by-field guide for `https://github.com/settings/apps/new`. Start
`wakey serve` **before** you begin, with `WAKEY_BASE_URL` set to the
address your browser will use to reach it — every URL in the manifest is
derived from it.

> You must be logged into the wakey dashboard before step 4: the redirect
> lands on `/setup/github/callback` and the exchange happens immediately.

## 1. Get the pre-filled values

Log into the dashboard, then open:

```
GET /setup/github/start
```

The response contains the manifest and a `creation_url` — open it. It is
`https://github.com/settings/apps/new?manifest=...` with the name,
webhook URL, callback URLs, and permissions already filled in. You only
author two fields:

| Field | Value |
|---|---|
| **GitHub App name** | pre-filled (`wakey`) — must be unique across GitHub; append your org if taken (e.g. `wakey-acme`) |
| **Description** | `Wakey — self-hosted AI SRE. Watches my services' logs, opens tickets for recurring errors, investigates root cause, and opens draft fix PRs for human review. Never merges anything.` |
| **Homepage URL** | your `WAKEY_BASE_URL` — `https://wakey.yourdomain.com`, or `http://localhost:8477` for a local install |

## 2. Leave these exactly as pre-filled

| Field | Value (from the manifest) |
|---|---|
| Webhook | **Active**, URL `{base}/webhooks/github` |
| Callback URL | `{base}/setup/github/callback` |
| Redirect URL | `{base}/setup/github/callback` |
| Permissions — Issues | Read and write |
| Permissions — Contents | Read and write |
| Permissions — Pull requests | Read and write |
| Permissions — Metadata | Read-only |
| Subscribed events | Issues, Issue comment, Pull request, Push |
| Where can this app be installed? | Any account (default) |

GitHub requires HTTPS for app URLs except `localhost` — a purely local
install may use `http://localhost:8477`; anything else should be a real
hostname behind TLS.

## 3. Create it

Click **Create GitHub App**. GitHub redirects immediately to
`{base}/setup/github/callback?code=...`. wakey exchanges the one-time
code for the app's credentials and stores them **encrypted** — this
requires `WAKEY_MASTER_KEY` to be set. Without it the callback refuses
with:

```
{"error": "WAKEY_MASTER_KEY must be configured before connecting a
GitHub App: the app private key must be stored encrypted, never in
plaintext"}
```

Fix: add the key to `.env`, restart, and walk through the flow again
(new code — old codes are single-use).

## 4. Verify

```text
GET /setup/github/callback response → {"connected": true, "slug": "wakey-on-..."}
```

Then finish the GitHub-side steps on the app's settings page:

1. **Install the app** on the repositories wakey should watch
   (or accept the installation prompt GitHub shows right after creation).
2. Optional but recommended: generate and download the private key
   (`Generate a private key`) — note that the manifest flow already
   delivered a key, stored encrypted; regenerate only if you prefer
   managing keys yourself.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Redirect lands on `localhost` but wakey is remote | `WAKEY_BASE_URL` mismatch — set it to the public URL, restart, re-run the flow |
| `{"error": "manifest exchange failed: HTTP 404"}` | the code was already used or expired — restart from `/setup/github/start` |
| `422 ... private key must be stored encrypted` | `WAKEY_MASTER_KEY` not set — set it, restart, retry |
| Nothing listens on the callback | wakey wasn't running or you're not logged into the dashboard — start it, log in, redo the flow |

## Alternative: filling the form by hand

If you open `https://github.com/settings/apps/new` directly (no
manifest), fill it exactly like this:

| Form field | Value |
|---|---|
| GitHub App name | `wakey` — globally unique on GitHub, so append your org if taken: `wakey-krakujs` |
| Description *(markdown)* | `Wakey — self-hosted AI SRE 🌙 Watches service logs, opens tickets for recurring errors, investigates root cause, and opens **draft fix PRs** for human review. **Never merges anything.**` |
| Homepage URL | your `WAKEY_BASE_URL` (`https://wakey.yourdomain.com` or `http://localhost:8477`) |
| Redirect URI | **leave empty** — wakey does not use the user-OAuth identity flow |
| Allow wildcard matching / Expire user authorization tokens / Request user authorization (OAuth) during installation / Enable Device Flow | all **unchecked** — none are used |
| Setup URL (optional) | `{base}/board` — lands the operator on the live board after installing |
| Redirect on update | unchecked (optional) |
| Webhook | **Active** ✅ |
| Webhook URL | `{base}/webhooks/github` |
| Webhook secret | the exact value of `WAKEY_WEBHOOK_SECRET` from wakey's `.env` — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` and put the same value in both places |
| Repository permissions | Issues: **Read and write** · Contents: **Read and write** · Pull requests: **Read and write** · Metadata: **Read-only** (auto) — everything else **No access** |
| Organization / Account / Enterprise permissions | all **No access** |
| Subscribe to events | **Issues** ✅ · **Issue comment** ✅ · **Pull request** ✅ (these appear after the permissions are set; leave Installation target, Meta, Security advisory unchecked) |
| Where can this app be installed? | **Only on this account** — wakey is a self-owned app, not a marketplace app |

Click **Create GitHub App** — GitHub redirects to
`{base}/setup/github/callback?code=...` and wakey stores the webhook
binding. Two follow-ups on the app's settings page after creation:

1. **Install the app** on the repositories wakey should watch.
2. Note that the manual flow does **not** deliver an API credential to
   wakey — keep `WAKEY_GITHUB_TOKEN` set for API calls, or prefer the
   manifest flow (`/setup/github/start`), which exchanges everything
   automatically and stores the private key encrypted.
