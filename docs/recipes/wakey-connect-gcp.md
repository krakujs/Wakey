# Managed Wakey Connect on Google Cloud

This runbook covers the separate connector, not the existing `wakey` service.
Provisioning was authorized on 2026-09-18. Public rollout is gated on the live
acceptance checklist in [Wakey Connect](wakey-connect.md); a successful deploy is
not product certification. These resources incur ongoing Google Cloud charges.

## Cutover checkpoint — 2026-09-18 (webhook routing live, permissions granted)

### 2026-09-22 reviewed update

- Broker revision `wakey-connect-00003-25l` and inbox revision
  `wakey-connect-inbox-00003-qg2` run immutable image
  `sha256:9384366345f21e91fa6575caf61467b949a0c6a56da9ae4a197e615c0d98c4ea`.
- The image passed the same Python dependency audit as CI (no known
  vulnerabilities; the local Wakey package is outside PyPI advisory matching).
- Public inbox `/` returns `{"status":"ok"}` and the Monitoring uptime check
  was reconciled from the provider-404 `/healthz` path to `/`.
- The post-deploy private probe passed database readiness, forged credential and
  signature rejection, signed webhook relay, live device-flow start, selection
  guard and credential revocation. It did not perform repository writes,
  multi-account isolation or database restore.

- The GitHub App `wakey-ai` webhook now targets the **public inbox**
  `https://wakey-connect-inbox-4vm7wrj2uq-uc.a.run.app/webhooks/github`
  (applied via `scripts/app_webhook_cutover.py`; `rollback` mode restores the
  previous `wakey` service target and its stored secret verbatim).
- The inbox is the only internet-facing surface: it verifies the GitHub HMAC
  signature (fail-closed) and relays to the IAM-restricted broker with its
  runtime identity. `/v1/*` operator routes remain Cloud Run IAM-gated.
- Verified live end-to-end: a real GitHub `issues` delivery was accepted 202 at
  the inbox, relayed 202 at the broker, and found queued (persisted before
  acknowledgement) for the connected staging deployment
  (`krakujs/linux-clipboard-manager`). Unsigned traffic 401s at the inbox and
  never reaches the broker. Verification object: issue #12 (created and closed).
- **Deployments integration is fully enabled**: the App subscribes to
  `deployment_status` and grants `deployments` (read); the installation accepted
  the permission update; a scoped installation token minted with
  `deployments: read` was verified against the live deployments API. The
  `/webhooks/deploy`-equivalent path now works through GitHub's own events.
- **Broker runs the hardened image**: revision `wakey-connect-00002-xn2`,
  `@sha256:a94b7e77cdd297f78893184e5476f5ac81d73cf7591ef2f651ac2508f1500068`
  (tag `ready20260918`) — external-write reconciliation, `deployments: read`
  token scoping, `deployment_status` handling. Inbox on the same digest. Both
  services keep **one warm instance** after a cold-start 500 on a GitHub retry;
  a forced redelivery of that delivery was absorbed with 202 (queue dedups by
  delivery id).
- Monitoring is configured: email notification channel, uptime check on the
  inbox `/healthz`, and 5xx alert policies for both Cloud Run services
  (`scripts/gcp_monitoring.py`, idempotent). Billing budget creation needs
  billing-account permission the runtime service account deliberately lacks.
- Full local gate after the cutover work: 469 passed, 9 skipped; security
  package coverage 98% (new ≥95% CI floor); sandbox gate 8/8.

## Verified checkpoint — 2026-09-18 (staging)


- Staging URI: `https://wakey-connect-4vm7wrj2uq-uc.a.run.app` — IAM authentication
  required. Anonymous readiness requests were rejected; do not set this as the
  ordinary customer default yet.
- Deployed image:
  `us-central1-docker.pkg.dev/wakey-ai/wakey/wakey-connect@sha256:a049ff1f3105c540afe6fe6201e14abc9d3b58f4c78e3a4efde4677cf8e7ffd1`.
- Cloud SQL reported RUNNABLE, REGIONAL, backups/PITR/deletion protection enabled
  and zero authorized public networks. Runtime startup/readiness confirmed the
  application can actually use its database and mounted secrets.
- `scripts.probe_connect` passed all its documented live checks. No repository
  issues, comments, branches or PRs were created. Existing Wakey and the App's
  webhook URL were unchanged.
- Full local gate: 424 passed, 9 skipped, 91.92% coverage. Rebuilt image Python
  dependency audit: no known vulnerabilities found; custom Wakey package is not
  on PyPI and is explicitly outside that advisory audit.
- Human authorization and selection of `krakujs/linux-clipboard-manager` succeeded.
  A subsequent live check confirmed GitHub accepts the installation token and
  lists exactly that repository in its scope. This does not certify webhook
  delivery or the full incident-to-PR loop. See the
  [full readiness check](../readiness-check-2026-09-18.md).

For the human portion, use `python -m scripts.pair_connect_staging begin`, have
the owner authorize the displayed code, then run `status` and `select`. The helper
refuses to select anything except `krakujs/linux-clipboard-manager`; its local
credential is encrypted in an owner-only Wakey configuration directory. Use
`disconnect` to revoke the test connection when finished. Codes are short-lived
and must not be saved in documentation.

## Infrastructure contract

Configuration: [`deploy/connect-gcp.json`](../../deploy/connect-gcp.json).

- Project `wakey-ai`, region `us-central1`, connector service `wakey-connect`.
- Dedicated keyless runtime identity `wakey-connect-runtime`; no service-account
  JSON key is embedded in the image or runtime. Cloud SQL Client is granted at
  project scope; Secret Accessor is granted only on four connector secrets.
- Dedicated PostgreSQL 16 instance `wakey-connect-db`, Enterprise, regional HA,
  1 vCPU / 3.75 GiB per instance, 10 GiB initial SSD, autoresize capped at 50 GiB.
  Backups, seven-day PITR logs and deletion protection are enabled. No authorized
  public networks; Cloud Run connects through the authenticated Cloud SQL socket.
- Separate database/user `wakey_connect`. Keep the broker separate from incident
  databases. Replicas share bounded write transactions via an advisory lock.
  This is a small control-plane design, not an unlimited-throughput guarantee.
- Cloud Run: 0–2 instances, 1 CPU / 512 MiB, concurrency 8, 60-second request
  timeout. `/readyz` checks the database; `/healthz` checks process liveness.
- Secret Manager: App PEM, master encryption key, webhook secret, database URL
  and database password. Only the first four are accessible by the runtime.
  Deployments pin secret version `1` and an image digest. Rotation is explicit,
  not an unnoticed `latest` change beneath a running revision.

## Repeatable preparation and deployment

Run from the repository root with the project virtual environment. The existing
operator service-account file defaults to `~/.config/wakey/gcp-service-account.json`.
Never pass secret values as command-line arguments; `--app-key` is a file path.

```bash
.venv/bin/python -m scripts.gcp_connect prepare --app-key /protected/wakey-app.pem
make check
docker build -t wakey:connect-reviewed .
.venv/bin/python -m scripts.publish_connect_image \
  --source wakey:connect-reviewed --tag YOUR-REVIEWED-TAG
.venv/bin/python -m scripts.gcp_connect deploy --image REGISTRY-PATH@sha256:DIGEST
.venv/bin/python -m scripts.probe_connect
```

`prepare` retains existing keys and resources; it does not replace mismatched
App keys. An interrupted Cloud SQL creation should be allowed to finish before
resuming. It retains policy bindings and etags. New service-account permissions
can take time to propagate; inspect a failure before retrying.

`deploy` creates an **IAM-restricted prelaunch service**, not a public service.
It refuses to overwrite an existing service, preventing accidental destructive
replacement of its configuration. Revision updates require review of the current
template and traffic configuration first. The image publisher uses a temporary
Docker credential directory and removes it on exit; its output is only the digest.

`probe_connect` checks private ingress, database readiness, forged credentials,
invalid HMAC, a signed synthetic ping, a real GitHub device-code request,
unauthorized selection and pairing revocation. Tokens stay in memory; one pending
pairing is revoked afterward. It never writes to repositories or changes the App.

## Public cutover and acceptance — not automatic

1. Confirm the owner has enabled Deployments read permission and the Deployment
   status event. App identity, public access and device flow have been verified
   against GitHub's live API; do not rely on a cached public App page.
2. Complete a real human-authorized pairing to an approved test repository, then
   confirm scoped tokens and log → issue → webhook routing. Repeat with a second
   independent account to validate live tenant isolation.
3. Preserve or migrate existing Wakey deployment state before any upgrade. The
   App's current webhook targets the old `wakey` service. Do not change that URL
   until the existing consumer is accounted for and rollback is prepared.
4. Add ingress abuse controls, uptime/error alerts with a real notification
   destination, billing alerts and a tested incident/rollback owner. Service max
   instances and global pairing admission limits are not complete abuse controls.
5. Perform a backup restore drill and revision/restart replay test. Regional HA
   and configured backups alone are not proof of recovery.
6. Only after these checks: authorize public invocation of the connector, update
   the App webhook URL/secret securely, and verify delivery from GitHub. Then
   publish the reviewed release and make its connector URL the default.

## Security, recovery and remaining release work

- The first candidate image was **not deployed** after the Python dependency
  audit found advisories affecting the old `cryptography<47` constraint. The
  dependency is now pinned to `50.0.1`; the rebuilt image must pass the audit.
  CI runs the same capped audit against the image's Python packages. This does
  not audit the custom Wakey code or every OS package; independent review and
  OS-level image scanning remain release requirements.
- Rotate the Google key exposed in chat after identifying/updating its operator
  and CI consumers. The deployed runtime uses its own keyless identity, so it
  does not require that long-lived operator key. Never revoke blindly and break CI.
- Back up the master encryption secret with appropriate access controls; loss
  makes stored connection data unreadable even if the database is restored.
- Before a revision update, record the current service template and digest
  without logging environment secret values. Roll back traffic only to a known
  compatible revision. A schema change may require a restored database and its
  matching encryption key; test the procedure in isolation before public launch.
- Deleting resources is not part of these scripts. Removing Cloud SQL requires
  explicit removal of deletion protection and a verified final backup. Stopping
  Cloud Run does not stop database/storage charges.

References: [Cloud Run database connections](https://docs.cloud.google.com/sql/docs/postgres/connect-run),
[Cloud SQL high availability](https://docs.cloud.google.com/sql/docs/postgres/configure-ha),
[Cloud Run authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).
