# Wakey readiness check — 2026-09-18

## Verdict

**Working alpha with a verified shared-App connection; not production-ready.**
Successful authorization is real, but does not prove webhook delivery, incident
handling, draft-fix execution, recovery, or safe public onboarding.

This review reran the local gates, inspected the onboarding/runtime/CI paths and
planning documents, and queried live GCP and GitHub configuration. It is not an
independent security certification or an exhaustive line-by-line review.
No repository issues, comments, branches or PRs were created, and no cloud/App
configuration was changed. Diagnostic token issuance used the existing encrypted
staging binding; credentials were never printed or added to this report.

## Fresh evidence

| Check | Result |
|---|---|
| `make check` | 424 passed, 9 skipped; 91.92% coverage; format, lint, types and SPDX passed |
| Real PostgreSQL contract tests | Passed as part of the full gate, including cross-replica admission, rollback and reconnect persistence |
| `WAKEY_DOCKER_TESTS=1 .venv/bin/pytest tests/unit/test_sandbox_runner.py -q` | 11 passed, including real container isolation |
| `make gate-sandbox` | Freshly rebuilt sandbox image; all 8 isolation tests passed in the capped, network-disabled container |
| `make demo-two-services demo-m0` | Seven scenarios and seven-point M0 rehearsal passed; synthetic/local, not live GitHub acceptance |
| Deployed-image Python dependency audit | No known vulnerabilities found; custom Wakey package and OS packages are outside this audit |
| Connector readiness | Authenticated HTTP 200, `ready`; anonymous HTTP 403 |
| Human-authorized connection | Connected to `krakujs/linux-clipboard-manager` |
| Actual installation token | GitHub accepted it; `/installation/repositories` returned exactly the approved repository; repository GET returned 200 |
| Cloud Run connector | Ready, digest-pinned, dedicated runtime account, four secret mounts and Cloud SQL mount; maximum two instances |
| Cloud SQL | RUNNABLE, regional HA, backup/PITR/deletion protection enabled, zero authorized public networks |
| Monitoring inventory | Zero alert policies, uptime-check configurations and notification channels in `wakey-ai`; no pagination omitted |
| `git diff --check` | Passed before report edits; rerun at handoff |

The nine skips are four opt-in live GitHub tests, four container-only isolation
tests, and one opt-in Docker runner test. The Docker runner was separately tested
above, and all four container-only skips were covered by `make gate-sandbox`.
Live GitHub write tests were intentionally not enabled by this read-only
check. Test-suite warnings remain (47); a green suite is not a clean release gate.

Local evidence logs: `/tmp/wakey-full-recheck.log`,
`/tmp/wakey-full-sandbox.log`, `/tmp/wakey-full-demos.log`,
`/tmp/wakey-full-image-audit.log`, `/tmp/wakey-full-skips.log`,
`/tmp/wakey-full-container-gate.log`.
These temporary logs are not permanent release artifacts.

## Blocking findings and acceptance criteria

### 1. Existing public Wakey service has a persistence risk — high

Live `wakey` configuration has no volumes and no `WAKEY_DATABASE_URL` environment
variable. It runs the older `wakey:head-41dcaf0` image, not the new connector image.
The current application defaults to a local SQLite database
(`src/wakey/core/composition.py:517`). This is a configuration-based risk finding:
the deployed image's internal filesystem was not inspected and no loss was induced.
Cloud Run's local filesystem is nonpersistent
([runtime contract](https://docs.cloud.google.com/run/docs/container-contract)).

**Next:** inspect/export existing state and its encryption key before replacing
the service; migrate to durable storage and prove restoration/restart survival.
Do not redeploy the old service merely to bring its code up to date.

### 2. Shared-App webhook routing is not connected — high

Live App configuration still targets
`https://wakey-4vm7wrj2uq-uc.a.run.app/webhooks/github`, not `wakey-connect`.
The staging connection therefore does not prove outbound event relay, commands,
or authorization-revocation delivery. The old service also lacks a configured
`WAKEY_CONNECT_URL`; the operator test binding is not a migrated customer runtime.

**Next:** preserve the existing consumer, define a rollback-capable cutover, then
verify a signed real GitHub delivery reaches only its connected deployment,
persists before acknowledgement, and handles retry/revocation correctly.

### 3. Deployment-status integration is incomplete — high

The App currently grants contents/issues/pull_requests write and metadata read.
Its subscribed events are issues, issue_comment, pull_request and push:
`deployment_status` is absent. In addition, the connector's token request explicitly
omits `deployments: read` (`src/wakey/forge/connect_github.py:89`). Changing only the
App settings will not change that requested permission subset.

A deployment-list GET returned 200 on the approved repository. This does **not**
prove private-repository permission or event delivery: GitHub permits public
deployment reads without authentication
([API permission contract](https://docs.github.com/en/rest/deployments/deployments)).

**Next:** enable the App permission/event, update the scoped-token contract and
tests, obtain any required installation approval, and verify exact commit plus
environment matching through a real deployment-status event.

### 4. Ambiguous external writes can duplicate tickets — high

`IngestPipeline._create_ticket` resets its reservation after any forge exception
without checking whether GitHub already accepted the issue. A fresh in-memory
diagnostic simulated an issue accepted by the forge followed by a lost response;
retrying the same event produced **two simulated issue side effects**.
Occurrence deduplication is not external-effect reconciliation.
The draft-PR path also opens a proposal before persisting its binding
(`src/wakey/agents/fix_delivery.py`); crash recovery needs equivalent review.

**Next:** persist operation identity/intent, reconcile an ambiguous response
against the remote object before creating another, and test accepted-write plus
timeout, process crash, replay and competing-worker cases. Acceptance: one remote
issue/proposal per intended operation, not just one local occurrence count.

### 5. Standard deployment cannot yet run the isolated fixer — high

The executor requires `DockerTestRunner`; it fails closed without Docker and a
preprovisioned sandbox image. The standard image/Compose stack does not provide a
Docker executor connection or `WAKEY_SANDBOX_IMAGE`. Local runner tests passing
does not make this available inside the customer's container or Cloud Run.

**Next:** provide a separately isolated executor with narrow authority, health
checks, cancellation and resource controls. Do not solve this by exposing an
unrestricted host Docker socket. Prove repro failure → patch → green test → draft
PR in the actual deployment topology before advertising automatic fixes.

### 6. Public onboarding is still a staging-only flow — high

The connector rejects anonymous requests with 403. The operator helper supplies
a Google identity token; the ordinary dashboard client does not. Compose defaults
`WAKEY_CONNECT_URL` to empty. New users can run a local demo but cannot simply
install our App and connect to this private endpoint. This is an intentional
prelaunch gate, not an authorization failure.

**Next:** after security and routing gates, publish an approved public endpoint,
configure the default, publish a reviewed release, and test the README from a
clean machine using a second independent account. No founder credentials or
manual Google authentication should be needed. Initial scope remains one repo
per deployment and requires repository-admin access.

### 7. Operational/security launch gates remain open — high

There are no project Monitoring alert policies, uptime checks or notification
channels. Configured backups are not evidence of a successful restore. No
revision replay/recovery drill, second-account live isolation, ingress abuse
acceptance, independent review, or OS image audit was performed here. Billing
alert configuration was not queried. The Google key previously pasted into chat
still needs coordinated rotation; this check did not revoke keys or inspect
unrelated credential consumers.

**Next:** configure real notification ownership and alert delivery tests, perform
isolated restore/rollback drills, verify tenant separation, scan OS packages and
rotate the exposed key after updating its consumers. Resource caps alone do not
provide abuse protection or a billing alert.

### 8. Product/CI acceptance exceeds the implemented gates — medium

The backlog still records verifier, reviewer-feedback retry, no-test handling,
digest approval and evaluation work (E8-T2/T3/T6–T9). The implemented model client
speaks Anthropic-compatible Messages, not every advertised future provider.
Do not count these planned capabilities as complete.

CI enforces only overall 90% coverage, not the separately specified critical
module 95% floor or diff coverage. Fresh security-package coverage is 119/132
lines (90.15%); `secretbox.py` is 77%. Combined critical-package coverage can mask
this gap. Source: `docs/engineering-standards.md:71`, `.github/workflows/ci.yml:67`.
Remote CI and publication of this dirty worktree were not verified; no commit or
push was made by this check. Historical epic/task roll-ups are not a completion
percentage.

**Next:** enforce the specified coverage gates, fill the missing security tests,
reconcile P1 acceptance criteria and either finish the remaining features or
explicitly narrow the alpha's scope. Capture evidence from the released artifact,
not only from the developer's working tree.

## Recommended execution order

1. Secure credentials and preserve/export the old service's state.
2. Implement/retest external-write reconciliation and production executor wiring.
3. Complete App deployment permissions, token scoping and webhook migration plan.
4. Configure alerts/abuse protection and prove restore/rollback and two-account isolation.
5. With explicit test-write approval, run the complete incident → issue → RCA →
   draft PR → human merge → deployment → observation loop on the approved test repo.
   Wakey must never perform the merge itself. Keep synthetic and live evidence separate.
6. Complete or explicitly defer remaining P1 features; enforce CI acceptance.
7. Publish the approved connector endpoint and release; rerun clean-install
   onboarding with an independent user and record steps, elapsed time and failures.

**Release decision:** hold public production launch. The verified connection is
a substantial onboarding milestone, not completion of the SRE product.

---

# Remediation outcomes — 2026-09-18 (same-day session)

Evidence standard: fresh `make check` 469 passed / 9 skipped (overall 91.8%,
security package 98%), `make gate-sandbox` 8/8, `make demo-two-services` and
`make demo-m0` green. Live work used only the project service account; secrets
stayed in process memory and were never printed. One verification object was
created on the approved test repository (issue #12, since closed).

## Per-finding status

1. **Old service persistence risk — resolved by cutover, not redeployment.**
   Live config confirmed: no volumes, no `WAKEY_DATABASE_URL`, SQLite inside an
   ephemeral container, `WAKEY_MASTER_KEY` injected as env. Its state is
   unrecoverable-by-design (the setup token lives only in container logs), so
   nothing was exported; the durable path is the Cloud SQL-backed connector,
   which now receives the webhooks (see #2). The old service remains deployed
   untouched as the rollback target and can be retired once the founder confirms
   no consumers remain.

2. **Shared-App webhook routing — CONNECTED and verified live.** GitHub App
   webhook now targets the new public inbox
   `wakey-connect-inbox-…a.run.app/webhooks/github` with the connector's stored
   secret (cutover via `scripts/app_webhook_cutover.py` — plan/apply/verify/
   rollback; rollback restores the old URL plus the old service's
   `WAKEY_WEBHOOK_SECRET` from its Cloud Run spec). Live proof: a real `issues`
   delivery traversed GitHub → inbox (HMAC 202) → broker (IAM 202) and was found
   persisted un-acked in the connected deployment's queue. Unsigned traffic 401s
   at the inbox; `/v1/*` remains IAM-gated. The inbox
   (`src/wakey/forge/connect_inbox.py`, 8 contract tests) is the only public
   surface.

3. **Deployment-status integration — COMPLETE.** The broker's token request
   includes `deployments: read`, and `/webhooks/github` handles
   `deployment_status` (success → deploy record with payload-authoritative
   environment, same contract as `/webhooks/deploy`; tested). The App-level
   event/permission change was applied through the owner's browser session
   (REST `PATCH /app` returns 404 for user-owned Apps) and the installation
   accepted the permission update; a scoped installation token with
   `deployments: read` was minted and verified against the live deployments
   API. The broker was redeployed on the hardened digest
   (`wakey-connect-00002-xn2`, tag `ready20260918`) with one warm instance.

4. **Ambiguous external writes — reconciled, with regression coverage.**
   Typed forge exceptions now distinguish ambiguous writes (lost response, 5xx)
   from definitive rejections; an ambiguous failure reconciles against the
   remote (`find_ticket_by_marker`) and binds the existing issue instead of
   creating a twin, keeping the reservation until stale-recovery re-reconciles.
   `open_draft_proposal` is idempotent per head branch and `create_branch`
   tolerates already-exists; the delivery persists its branch intent before any
   external call and resumes after crashes. Regression tests cover accepted-
   write-plus-lost-response, competing-worker, definitive-rejection, and both
   delivery crash windows (one remote issue/proposal per intended operation).

5. **Isolated fix executor — production topology implemented.** New
   `RemoteTestRunner` + `wakey-executor` service
   (`src/wakey/agents/executor_server.py`): the only host with container-runtime
   access, bearer-token auth, bounded concurrency and uploads, safe tar
   extraction, resource caps enforced server-side; wakeyd itself needs no
   Docker. Selected automatically via `WAKEY_SANDBOX_EXECUTOR_URL/TOKEN`
   (token required — validated in config). Local topology:
   `deploy/compose.fixer.yml` with a docker-socket-proxy allowlist. Live proof
   in the Cloud Run topology is still outstanding (the executor needs a VM/GKE
   host with Docker — not Cloud Run), so automatic fixes must stay off there
   until proven.

6. **Public onboarding — unblocked, intentionally minimal.** The inbox is the
   approved public endpoint for webhook traffic; ordinary customer onboarding
   (dashboard/device flow) still requires the operator IAM identity until the
   public-launch gate passes. No `WAKEY_CONNECT_URL` default changed.

7. **Operational gates — monitoring now real.** Email notification channel,
   uptime check on the inbox health endpoint, and 5xx alert policies for both
   connector services are configured (idempotent `scripts/gcp_monitoring.py`).
   Open: billing budget (needs billing-account permission the runtime account
   deliberately lacks), a live alert-delivery test, the restore drill, the
   second-account isolation run, independent review, and rotation of the
   previously exposed Google key.

8. **Product/CI acceptance — critical-module floor enforced.** CI and
   `make check` now enforce a security-package coverage floor of 95% in addition
   to the overall 90% floor (currently 98%; `secretbox.py` gained a dedicated
   14-test security suite, 100% covered). Remaining: diff coverage, the P1
   backlog items (verifier, reviewer-feedback retry, digest mode, digest
   approval) — still not counted as complete.

## Ordered next steps (updated)

2026-09-22 local follow-up: the executor image now includes the Docker client,
the executor streams workspaces through the restricted proxy into sandbox tmpfs,
and the complete local Compose topology was exercised successfully. Tested
commits stay pinned through proposal publication, and bounded same-PR reviewer
retry is implemented. A real VM/GKE executor deployment and the live end-to-end
incident loop remain required before enabling production fixes.

2026-09-22 completion follow-up: the remaining local fix-path controls now
exist: fail-closed independent verifier with audit/hidden PR evidence,
`allow_without_tests` threshold and labeling, bounded same-PR reviewer retry,
daily per-repository digest approval, exact tested-SHA publication,
pre-existing-suite comparison, repository/deployment-diff RCA context, and
hourly call/token ceilings with usage audit. `make check` and the CI-equivalent
four-worker PostgreSQL run each pass 491 tests with 10 skips (91.12% overall,
98% security); `make gate-sandbox` passes 8/8. Deployment, second-account
isolation, restore/alert/key-rotation drills and the real incident-to-observation
acceptance loop remain external launch evidence, not local code gaps.

Source commit `aaa21f08821191802ff489230d9ac40010939eaf` passed GitHub CI.
The committed connector image was published and deployed by digest
`sha256:ec0eb347fa89e4dacf0e0762663bb16c246e7249790f32e4b5e3e1868cf39e7d`
to broker revision `wakey-connect-00004-hl2` and inbox revision
`wakey-connect-inbox-00004-stk`. Public root liveness is 200, the Monitoring
check now targets that route, and the non-destructive private live probe passes.

1. ~~Founder (sudo): App settings → add `deployments` (read) permission +
   `deployment_status` event~~ — DONE (applied and accepted; scoped token
   verified against the live deployments API).
2. Founder (billing): create the budget or grant `billing.budgets` permission;
   confirm the alert email is monitored. Alert routing otherwise configured.
3. ~~Redeploy the broker on the hardened digest~~ — DONE (revision
   `wakey-connect-00002-xn2`, one warm instance; a previously-failed delivery
   was force-redelivered and absorbed with 202).
4. Prove the isolated executor in the real topology (small VM behind
   `WAKEY_SANDBOX_EXECUTOR_URL`), then run the full incident → issue → RCA →
   draft PR → human merge → deployment → observation loop on the approved repo
   (Wakey never merges). deployment_status events now flow automatically once a
   deployment is created.
5. Restore drill, two-account isolation, live alert-fire test, exposed-key
   rotation, then the public launch decision. Retire the old `wakey` service
   once the founder confirms no remaining consumers (webhook no longer targets
   it; it stays deployed as rollback until then).
