# SPDX-License-Identifier: Apache-2.0
# Recipe: deploy events → wakey (WF-08 deploy-aware verification, E4-T6)

Goal: verification windows only start counting after the fix actually
reaches the emitting environment, and (P2) deploy correlation names the
likely first-bad deploy.

## CI step (any CI system; HMAC-authenticated)

After each production deploy:

```bash
curl -X POST https://wakey.example.com/webhooks/deploy \
  -H 'Content-Type: application/json' \
  -H 'X-Wakey-Signature: sha256=<_>' \
  -d '{"service":"payments-api","sha":"'"$GIT_SHA"'"}'
```

`<_>` = `printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WAKEY_WEBHOOK_SECRET" | awk '{print $2}'`

GitHub Actions equivalent:

```yaml
- name: notify wakey
  run: |
    BODY='{"service":"payments-api","sha":"'"$GITHUB_SHA"'"}'
    SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WAKEY_WEBHOOK_SECRET" | awk '{print $2}')"
    curl -sf -X POST https://wakey.example.com/webhooks/deploy \
      -H "Content-Type: application/json" -H "X-Wakey-Signature: $SIG" -d "$BODY"
  env:
    WAKEY_WEBHOOK_SECRET: ${{ secrets.WAKEY_WEBHOOK_SECRET }}
```

## How wakey uses it

- Merge of a wakey fix PR (webhook) arms the verification window.
- The window clock starts only when a deploy of that service lands at or
  after the merge (deploy-aware grace, WF-08 §1).
- Without any deploy source wakey is deploy-blind and starts the window
  at merge time — acceptable for single-environment services.
