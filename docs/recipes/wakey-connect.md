# Wakey Connect: setup and launch checklist

Managed Google Cloud deployment work is now documented in the
[GCP runbook](wakey-connect-gcp.md). It supersedes the SQLite-only hosting
constraint below when `WAKEY_CONNECT_DATABASE_URL_FILE` configures PostgreSQL.
The broker refuses to start on Cloud Run without that durable store.

Status (2026-09-18): a managed PostgreSQL-backed connector is deployed and
smoke-tested in IAM-restricted staging. **Public onboarding and the complete
live repository workflow are not yet verified.** The App is `wakey-ai`; the
staging address is recorded in the GCP runbook, not enabled as a customer default.
The published 0.1.0 image does not contain these changes;
build the current source until a reviewed release is published.

### Earlier development-resource check — superseded by the GCP runbook

- Target project: `wakey-ai`; existing Wakey service-account file authenticated
  successfully. The interactive gcloud login currently requires reauthentication.
- Existing Cloud Run service: `wakey`, region `us-central1`. It was inspected,
  not modified or redeployed. No connector service has been deployed.
- Secret Manager API is now enabled; its first successful inventory contained
  zero secrets. No credential payloads or secret versions were uploaded.
- Compute Engine and Cloud SQL inventory APIs were disabled. They were not
  enabled and no new compute/database resources were provisioned.
- The supplied [wakey-ai App](https://github.com/apps/wakey-ai) currently reports
  **private** visibility. Its owner needs to open App settings → Advanced → Make
  public before other accounts can install it. Enable device flow in App settings
  and provide the **GitHub App client ID**, not the Google service-account client ID.
  See [GitHub's settings instructions](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration).
- Supply the GitHub App PEM through a protected local file or Secret Manager and
  provide only its path/resource name to the operator. Do not paste it in chat.
  The exposed Google service-account key should be rotated after checking its
  consumers; it was not copied into the repository or used from the chat message.
- Storage is still a deployment gate: the connector currently uses SQLite and
  cannot safely use Cloud Run's ephemeral filesystem. Choose persistent-disk
  hosting or implement a durable external store before deploying it there.
  The existing Wakey service also showed no `WAKEY_DATABASE_URL` environment
  setting or volumes; review its durability before treating it as production.
  This configuration observation is not evidence of actual data loss.

## What a customer does

1. Clone Wakey and set `WAKEY_CONNECT_URL` in `.env` to the HTTPS endpoint published
   by the Wakey operator. Do not use an untrusted connector: it issues repository
   credentials and receives GitHub webhook metadata.
2. Run `sh start.sh` (Docker and Compose required). It waits for health and prints
   a fresh login token. Open
   `http://localhost:8477` and sign in with the one-time setup token.
   Check the Compose port mapping if you have changed it.
3. Open **Connect GitHub**, install the Wakey App on selected repositories, then
   enter the displayed device code at GitHub and authorize the App. Return to
   Wakey and check the connection after the displayed polling interval.
4. Select a repository you administer. The running adapter activates immediately;
   pairing does not require restarting Wakey or sharing the App private key.
5. Follow **Set up log ingestion**, register a service for that same repository,
   save its one-time ingest key, and configure the application to send logs.
6. Send a synthetic error and confirm its GitHub issue, then test a webhook event.
   A green connection page alone is not proof of the full log-to-ticket loop.

The first slice supports **one repository per Wakey deployment**, with multiple
services in that repository. Use separate deployments for different repositories.
Repository-admin permission is required to pair. Installations with more than
100 repositories, or users with more than 100 accessible installations, are
explicitly rejected rather than partially discovered. GitHub comment commands
remain deny-by-default: configure `WAKEY_AUTHORIZED_USERS` separately.

Compose and `make serve` opt into generating a persistent owner-only `master.key`.
Back up that key securely with the data volume; the database alone cannot recover
encrypted connection credentials. Never delete the volume to reset onboarding.
For independently operated integrations, use the advanced BYO-App setup instead
of setting `WAKEY_CONNECT_URL`.

## What the Wakey operator must do before launch

1. Configure the Wakey-owned public GitHub App. Enable **device flow**. Grant
   repository **Contents**, **Issues**, and **Pull requests** read/write; Metadata
   read is implicit. Subscribe to issue comments, pull requests, and deployment
   status events used by Wakey. Ensure installation/repository access changes and
   App authorization revocation reach the webhook handler.
2. Choose a public HTTPS hostname and configure the App webhook URL as
   `https://YOUR_CONNECT_HOST/webhooks/github`. Configure a strong webhook secret.
   No callback to a customer's localhost/private network is necessary.
3. Mount secret-manager files and set these environment variables on the broker
   (not customer deployments):

   | Variable | Value |
   |---|---|
   | `WAKEY_CONNECT_CLIENT_ID` | The public App client ID |
   | `WAKEY_CONNECT_APP_SLUG` | The public App's GitHub URL slug |
   | `WAKEY_CONNECT_MASTER_KEY_FILE` | Mounted file containing a 64-character hex AES key |
   | `WAKEY_CONNECT_PRIVATE_KEY_FILE` | Mounted App PEM private-key file |
   | `WAKEY_CONNECT_WEBHOOK_SECRET_FILE` | Mounted webhook-secret file |

4. Install the current source with the crypto dependencies (see `make install`).
   Run `wakey-connect --host 127.0.0.1 --port 8480 --data-dir /var/lib/wakey-connect`
   behind a TLS reverse proxy. `/healthz` is the process health endpoint.
   Provision the data directory and permissions first. Do not expose the plaintext
   listener publicly or put secret values in command-line arguments.
5. Use **one broker process with one persistent SQLite volume**. This implementation
   is not a multi-replica/stateless Cloud Run deployment. Add ingress per-IP rate
   limits, request-size limits, alerts, backups, and an operational recovery plan.
   Internal admission is globally bounded to 60 pairing starts/minute and 1,000
   pending connections; this does not replace abuse protection at ingress.
6. Complete the live acceptance checklist below, then publish the approved endpoint
   and a reviewed release containing it/configuration instructions. Until then,
   the source default deliberately leaves `WAKEY_CONNECT_URL` empty.

## Security and operational boundaries

### Packaged operator startup

`deploy/compose.connect.yml` packages the broker separately from customer Wakey.
Set the five operator variables above in the deployment environment; secret-file
variables must point to existing secret-manager-mounted files readable by the
container's non-root user. Do not paste keys into chat or commit them.

```bash
docker compose -p wakey-connect -f deploy/compose.connect.yml config --quiet
docker compose -p wakey-connect -f deploy/compose.connect.yml up --build --wait --wait-timeout 180
```

This starts a capped, non-root broker with a read-only root filesystem and a
separate persistent data volume. It binds only localhost:8480. It does **not**
configure DNS, TLS, ingress rate limits, or the public App for you: those require
your chosen hosting target and App ownership. Compose waits for the health check
before reporting success ([Docker startup semantics](https://docs.docker.com/reference/cli/docker/compose/up/)).
For a reviewed production release, pin the built image to an immutable digest.
Stop with the same Compose prefix followed by `stop`; never use `down -v`.
Rollback requires the previous reviewed image and a compatible database snapshot;
restore the separately protected master key with that snapshot. Test this in
staging before launch.

### Data handling

- GitHub device authorization verifies identity; repository administration is
  checked during pairing. Installation IDs and repository IDs come from GitHub,
  not caller assertions. The user's GitHub access token is not persisted.
- The broker retains the App private key. It mints installation tokens limited to
  the selected repository and required permissions. Treat both the customer's
  deployment bearer credential and installation tokens as secrets.
- Deployment credentials are hashed at the broker and encrypted locally. Pending
  device secrets, connection metadata, and queued webhook payloads are encrypted
  at the broker. Routing IDs, timestamps, state, and audit actions are not all
  encrypted. The broker's action log is not a tamper-evident audit system.
- Production log ingestion stays at the customer deployment. GitHub metadata
  (including issue/comment/PR webhook content) passes through the broker, is
  redacted, then queued. Repository content is fetched directly from GitHub by
  the deployment. This is a disclosed trust boundary, not fully offline hosting.
- The local runtime polls outbound every 10 seconds, persists each webhook in its
  delivery queue **before** acknowledging it, and processes it through existing
  handlers. Broker queues retain events/deduplication tombstones for 24 hours,
  with at most 1,000 outstanding events per connection. Longer outages can lose
  events; monitor queue failures and use GitHub redelivery where available.
- Disconnect revokes future broker access and clears the local token cache.
  App uninstall/suspension, repository removal and authorization revocation also
  revoke matching connections when the signed event arrives. Already-issued
  GitHub installation tokens can remain usable until expiration (up to one hour).
  Pairing-time user admin authority is not continuously revalidated.
- Back up the SQLite database using a consistent snapshot and back up the master
  key separately in protected storage. Protect proxy/application logs: do not log
  Authorization headers or pairing/token response bodies. Keep access logging
  disabled for the broker as supplied. Test restore before public launch.

## Live acceptance checklist — still required

- [ ] Fresh checkout/build and fresh volume: login, device authorization, select
  a repository, register a service, ingest an error, observe the real GitHub issue.
- [ ] Repeat with a second independent account/organization and deployment;
  verify neither can select, obtain credentials for, or receive events for the other.
- [ ] Restart broker and deployment; connection survives and pending events replay
  without duplicate local dispatch. Exercise a response-loss/retry scenario.
- [ ] Verify issue-comment commands, PR merge, and deployment-status routing.
- [ ] Remove a repository, suspend/uninstall the App, revoke user authorization,
  and disconnect locally; verify renewal/relay refusal and document token expiry.
- [ ] Verify HTTPS, ingress abuse controls, queue/backlog alerts, backup/restore,
  key rotation procedure, and an independent security review.
- [ ] Publish the configured release and test the README as a first-time user.

Local regression evidence lives in `tests/unit/test_connect.py` and
`tests/contract/test_connect_github.py`; `make check` runs these with the existing
suite. They simulate GitHub and **do not replace** the live checklist.

First-run verification (2026-09-18): startup preflight/failure-path tests and
operator Compose configuration validation passed. A newly built image booted
in a disposable, network-disabled container capped at 512 MB / 1 CPU / 128 PIDs.
It generated a mode-0600 encryption key and passed login → setup → registration
→ HTTP ingest → incident board. This verified **console mode only**, not a real
GitHub connection. No production container or data volume was replaced.

## GitHub protocol references

- [User access tokens and device flow](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
- [User-accessible installations and repositories](https://docs.github.com/en/rest/apps/installations)
- [Scoped installation tokens and expiration](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
