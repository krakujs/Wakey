# SPDX-License-Identifier: Apache-2.0
# Recipe: GCP Cloud Logging → wakey (Pub/Sub push, E4-T4)

Goal: errors from a GCP-run service appear as wakey fingerprints/tickets.

## 1. Register the service in wakey

Dashboard `/setup` (or `/api/services`) → note the ingest key `wk_...`.

## 2. Export errors to Pub/Sub

```bash
gcloud logging sinks create wakey-sink \
  pubsub.googleapis.com/projects/PROJECT/topics/wakey-errors \
  --log-filter='severity>=ERROR'
gcloud pubsub topics create wakey-errors   # (if not auto-created)
gcloud pubsub subscriptions create wakey-push-sub --topic=wakey-errors \
  --push-endpoint=https://wakey.example.com/ingest/gcp/wk_YOUR_KEY \
  --push-auth-service-account=wakey-push@PROJECT.iam.gserviceaccount.com \
  --push-auth-audience=https://wakey.example.com
```

## 3. Configure wakey (optional but recommended)

```bash
WAKEY_OIDC_AUDIENCE=https://wakey.example.com
WAKEY_OIDC_SERVICE_ACCOUNT=wakey-push@PROJECT.iam.gserviceaccount.com
```

With the audience set, every push's OIDC token is verified (issuer,
audience, expiry, service account) before acceptance; the Pub/Sub
`messageId` is used as the delivery id so provider retries deduplicate.

## 4. Verify

```bash
gcloud pubsub topics publish wakey-errors --message='{"level":"error","msg":"connection refused to db"}'
# repeat 3x (threshold) → ticket appears in the configured repo / console
curl :8477/readyz    # pending_deliveries returns to 0
```

Payload formats: JSON object, JSON lines, logfmt, or plain text — the
same normalizers as `/ingest`. If `severity` is carried in the JSON
payload it maps to wakey severity; plain-text errors are detected by
content.
