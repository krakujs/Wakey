<!-- SPDX-License-Identifier: Apache-2.0 -->

# Wakey development review and recovery plan

Date: 2026-09-16  
Reviewed baseline: local development tree, including the settings page subsequently committed by another session; HEAD at report creation: `b2c02f9`.  
Status: findings recorded; remediation has not been implemented by this review.

## 1. Assessment

Wakey has a useful foundation, but it is currently a collection of partially connected components rather than a complete monitoring and repair service. The primary development problem is that passing component tests and successful demos have been treated as evidence of product completion.

The architecture is worth retaining: small Python modules, typed domain models, a storage interface, a forge interface, deterministic policy functions, SQLite persistence, and a lightweight web surface. The immediate priority is to connect and harden one real operational flow, then prove recovery from failures.

Do not use the existing percentage-complete estimates to plan a release. Measure completion against workflow acceptance criteria, production startup behavior, and recorded milestone evidence.

This report supplements [TASKS.md](TASKS.md), [STATE.md](STATE.md), and the [workflow specifications](workflows/). It does not replace their scope or mark any milestone complete. The standing rule remains: finish P1 and its gates before P2/P3 work.

## 2. Scope and verification

Reviewed application modules, tests, README and contribution instructions, packaging, Docker/Compose, CI, planning documents, security review, and WF-01 through WF-15. External competitive-market claims were not revalidated. No live GitHub writes, cloud deployment, paid model calls, or real credential inspection were performed.

| Check | Result |
|---|---|
| Ruff formatting and lint | Passed |
| Strict mypy | Passed on the reviewed source tree |
| SPDX header check | Passed |
| Existing test suite | 133 passed |
| Measured line coverage | 87%; below the documented 90% overall floor |
| Startup/CLI coverage | `src/wakey/__main__.py`: 0% |
| Live model client coverage | `src/wakey/agents/llm.py`: 0% |
| Additional isolated defect reproductions | 10 passed, confirming the undesirable behavior described below |

The first sandboxed test run encountered local simulator/test-client restrictions and was interrupted. A subsequent unrestricted local run passed; that initial failure is not attributed to the project.

Temporary reproduction tests are at `/tmp/test_wakey_review.py` on the review machine. They assert the **current defective behavior**, so a passing result confirms a defect rather than correctness. They are not a permanent regression suite and `/tmp` is not durable storage. Convert each relevant case into a repository test asserting the desired behavior before fixing it. The reproduction descriptions below preserve the essential evidence if that temporary file disappears.

Coverage also exposed unclosed SQLite connections; test fixtures and application shutdown need explicit resource cleanup. Dependency deprecation warnings appeared in the test-client stack.

Concurrent work was present during review. At report creation, an untracked `src/wakey/tickets/lifecycle.py` existed; it was not part of the verified baseline. Recheck affected findings against that work before implementing overlapping changes. Its existence alone does not prove lifecycle integration is complete.

## 3. How the project currently works

The intended workflow is:

```text
Logs -> authenticate/redact -> fingerprint -> policy -> ticket
     -> RCA -> draft fix PR -> human merge -> post-merge verification
```

| Area | Implemented behavior | Missing connection or capability |
|---|---|---|
| Core | FastAPI, SQLite, models, health endpoints | Full startup composition and shutdown ownership |
| Ingestion | Basic key/HMAC checks, parsing, message/attribute redaction | Enabled production route, durable handoff, limits, complete redaction |
| Detection | Templates, traceback parsers, counters, simple policy | Error filtering, configured policy, rate-window integration |
| GitHub | Create ticket, comment, request draft PR | App onboarding, branch publication, complete ticket lifecycle and webhook handling |
| RCA | Heuristic classification and comment helper; separate model client | Automatic dispatch, code investigation, model routing, budgets and safety controls |
| Fixing | Repro/patch iteration with scripted proposers | Containment, isolated runner, trustworthy repro/diff, live proposer and actual code delivery |
| Verification | Pure verdicts and local fingerprint updates | Merge/deploy events, persistent windows, forge effects, repeat-fix guard |
| Dashboard | Read-only board and settings | Authentication, onboarding, service management and operational views |
| CLI | `python -m wakey` with serve/status/board/doctor | Installed executable, documented management commands and full API parity |

The demos assemble dependencies directly. Normal `serve` startup does not assemble the same system, which explains why demo success does not establish that a fresh installation works.

## 4. Findings

Priority meanings: **High** = correctness/security blocker for the affected operational flow; **Medium** = material reliability or development defect. Findings in disconnected fix components become operational security risks when those components are enabled; this review did not establish a remotely reachable exploit through the current default server.

### R-01 — High: normal startup does not enable ingestion

- Evidence: [startup](../src/wakey/__main__.py) calls `create_app(settings, storage)` without a forge. [Server assembly](../src/wakey/core/server.py) registers ingestion only when a forge is provided.
- Reproduction: the default app returns HTTP 200 from `/readyz` and HTTP 404 from `/ingest/anything`.
- Impact: an apparently healthy installation cannot perform the product's basic job. Queue, RCA, fix and verification workers are also absent from startup.
- Required change: create a single application composition path used by the CLI, container and integration tests. Define explicit configured/unconfigured readiness states and own worker/client/storage lifetimes there.
- Closure test: start the packaged application with temporary configuration and prove a registered service can ingest through its real HTTP endpoint without test-only dependency injection.
- Existing tasks: E2-T4/T5, E3-T5, E4-T1, E11-T7.

### R-02 — High: failed deliveries can permanently lose events

- Evidence: [ingest handler](../src/wakey/core/server.py) records a delivery before parsing/persisting the batch and before synchronous forge calls complete.
- Reproduction: submit five matching errors with a fixed delivery ID and a forge that fails creating the ticket. The request fails on event three. Retry returns `{"deduplicated": true}`; only three event rows exist.
- Additional defect: a malformed field such as `"level": []` produces HTTP 500 instead of isolating that line and processing valid siblings.
- Required change: durable ingest transaction plus recoverable work/outbox records; explicit pending/completed delivery state; event-level idempotency; bounded retries for external effects. Namespace delivery identity by source/service. A retry must never mean “already done” merely because processing began.
- Closure tests: failure before persistence, mid-batch failure, forge outage, process restart, duplicate delivery and two services sharing a delivery ID. No lost events or duplicate tickets. Treat remote success followed by local failure as an explicit reconciliation case.
- Existing tasks: E4-T7/T9, E2-T5, E6-T5, E11 operations.

### R-03 — High: fix execution has no effective sandbox or path containment

- Evidence: [fixer](../src/wakey/agents/fix.py) writes proposer-supplied paths directly and executes tests with `subprocess.run` on the host. Timeout is the only execution bound.
- Reproduction: a proposer returning `../outside.py` writes outside the workspace before the loop aborts.
- Impact: repository tests or generated code can access the runner's filesystem, environment and network permissions; output capture is not bounded while the subprocess runs.
- Required change: resolve and enforce workspace **and service-path** containment, reject absolute/traversal/symlink escapes, isolate execution with network disabled by default, use a minimal environment, cap CPU/memory/processes/output/time, and terminate the full process tree on timeout. Keep live autonomous fixing disabled until verified.
- Closure tests: traversal and symlink attempts, secret-environment access, network egress, resource limits and child-process cleanup. Run resource/escape tests inside disposable capped containers.
- Existing tasks: E7-T1, E8-T3/T4, E10-T2.

### R-04 — High: redaction is incomplete at persistence boundaries

- Evidence: [ingest handler](../src/wakey/core/server.py) redacts message and attribute values but leaves separate IDs untouched. [Normalizer](../src/wakey/ingest/normalizers.py) splits plain text into lines before the private-key block pattern runs.
- Reproductions: synthetic GitHub-token strings survive in stored `trace_id` and `request_id`; a synthetic multiline PEM-shaped block survives line-by-line ingestion even though the redactor catches it as one string.
- Direct callers of [IngestPipeline](../src/wakey/ingest/pipeline.py) also rely on callers having already redacted input.
- Required change: define a complete sanitization contract for every externally supplied string, including metadata, identifiers and attribute keys; use safe internal identities where needed. Preserve/reassemble multiline data for redaction. Apply a second pass to assembled model prompts and redact error/audit paths.
- Closure test: ingest a synthetic secret corpus in every accepted field and format, then scan temporary storage and captured outbound requests for leakage.
- Existing tasks: E4-T3/T8, E10-T1/T2.

### R-05 — High: ordinary logs can create tickets and comment floods

- Evidence: [pipeline](../src/wakey/ingest/pipeline.py) does not call the existing error filter, uses `ServiceYaml()` defaults for every service and comments on every further eligible occurrence. Sliding-window counters are not connected.
- Reproduction: six identical INFO messages produce one ticket and three comments.
- Required change: apply the configured severity/window policy, persist effective service configuration, throttle comments, enforce transactional ticket/concurrency caps and reserve dispatch ownership before external actions.
- Resolve a specification conflict first: WF-03 suppresses all wake signals in observe mode, while WF-07 defines observe as tickets without agents. Record one explicit contract and test it consistently.
- Closure tests: INFO-only stream creates no incident; thresholds respect windows and service config; bursts create one ticket with bounded comments; concurrent dispatch does not duplicate work; autonomy is enforced.
- Existing tasks: E3-T6, E5-T3/T4/T5, E6-T2/T5, E10-T5.

### R-06 — High: draft delivery does not publish the patch

- Evidence: [delivery](../src/wakey/agents/fix_delivery.py) puts a diff in the PR body and calls the forge. [GitHub adapter](../src/wakey/forge/github.py) expects an existing branch and hardcodes base `main`. There is no branch/commit publication in that flow.
- Impact: the console simulator accepts a “delivered” proposal without proving that the target branch contains the tested code.
- Required change: resolve the actual default branch/base SHA; create an isolated branch; commit and publish the tested patch and reproduction; open a draft; persist its fingerprint/issue/commit binding. Implement retry reconciliation and branch-name collision handling. Never merge or force-push.
- Closure test: inspect the resulting branch/tree and PR diff through a realistic forge contract test, then run the authorized live gate. A PR description containing code is not proof of code delivery.
- Existing tasks: E8-T5, E3-T2, E17-T1.

### R-07 — High: dashboard endpoints are unauthenticated

- Evidence: [server routes](../src/wakey/core/server.py) serve board/settings without auth; [CLI](../src/wakey/__main__.py) binds to `0.0.0.0`; [Compose](../docker-compose.yml) publishes the port on all host interfaces.
- Reproduction: anonymous requests to `/board` and `/api/settings` receive HTTP 200.
- Required change: implement first-run token and admin-session behavior from WF-01/WF-12. Until then, use loopback-only development exposure. Apply authentication to operational APIs and define separately which health/ingest endpoints are intentionally accessible.
- Closure tests: no anonymous operational data access; setup token expires and is single-use; session/logout behavior works; mutation endpoints have CSRF protection; container exposure matches documentation.
- Existing tasks: E3-T5, E11-T1, E10 hardening.

### R-08 — Medium: repeated fix attempts can report an incorrect diff

- Evidence: [fix loop](../src/wakey/agents/fix.py) replaces its original `files` baseline with `updated` after a failed attempt.
- Reproduction: after one failing patch attempt and a successful second attempt, the returned diff can omit the application change already made on attempt one.
- Other gaps: any baseline failure, including timeout/unrelated failures, counts as reproduction; there is no fingerprint-signature check. Only `*.py` files are snapshotted, and test failure output is not supplied to the proposer by its current interface.
- Required change: keep an immutable original baseline, maintain complete current workspace state separately, prove the specific repro failure, preserve existing-test results and generate the final diff against the original revision. Handle supported file types explicitly.
- Closure tests: success on attempts two/three, unchanged files, new files, non-Python files, unrelated pre-existing failure, timeout and patch attempts that weaken the repro instead of fixing the bug.
- Existing tasks: E8-T2/T3/T4/T6/T7.

### R-09 — Medium: verification does not close the external lifecycle

- Evidence: [watcher](../src/wakey/policy/watcher.py) accepts a forge but does not use it. It updates local state and audit rows only.
- Missing: merge/deploy event binding, persistent verification timestamps, issue close/reopen, PR labels and persisted repeat-fix inhibition after an insufficient verdict.
- Required change: persist a lifecycle state machine driven by authenticated forge/deploy events; start verification after the fix reaches the relevant environment; make outbound effects retryable and idempotent; enforce the human-ack guard from stored truth.
- Closure tests: merge/deploy/silence closes and labels correctly; recurrence reopens and blocks another autonomous fix; restart resumes the same window; revert/never-deployed cases do not get false success credit.
- Existing tasks: E6-T3/T4/T5, E9-T1/T2/T3/T4.

### R-10 — High: security documentation asserts controls that do not exist

- Evidence: [security-review.md](security-review.md) checks off injection canaries, tool allowlists, ticket/LLM budgets and graceful degradation without corresponding implementation/test evidence.
- Regex length and syntax validation do not establish bounded matching time. `WAKEY_MASTER_KEY` is unused and service webhook secrets are stored as plaintext SQLite values. Git-ignoring `.env` is not encrypted credential storage. The GitHub allowlist is optional at adapter construction.
- Required change: correct the checklist immediately; link each verified claim to an executable test and its date/result. Implement injection controls, bounded regex handling, budgets, credential protection and audit integrity under their existing tasks. Verify enforcement through public application paths.
- Closure criterion: no unchecked implementation is described as verified, and each claimed security invariant has a meaningful negative test.
- Existing tasks: E10-T1 through E10-T6.

### R-11 — Medium: status and build enforcement disagree with reality

- [README.md](../README.md) says development has not started; [STATE.md](STATE.md) mixes early foundation status with late completion estimates; [TASKS.md](TASKS.md) contains stale statuses and partially completed tasks marked done.
- [CI](../.github/workflows/ci.yml) runs lint, typing, tests and image build, but does not enforce the documented coverage floors or run scheduled resource benchmarks, security audits, agent evals or release gates. Local `make gate-*` targets are absent.
- Required change: reconcile task acceptance criteria with evidence; use “component implemented / integrated / verified” distinctions. Enforce the existing coverage requirements and add realistic startup/failure-path tests. CI definitions can be improved locally without adding a remote or pushing.
- Closure criterion: a clean installation test catches R-01; coverage below the agreed floor fails; each documented gate command exists and records evidence; percentages are removed or computed from an explicit verified denominator.
- Existing tasks: E1-T2/T4/T6, E2-T6, E3-T8.

### R-12 — Medium: installation and configuration contracts are incomplete

- [pyproject.toml](../pyproject.toml) declares no `wakey` console script; the reviewed virtualenv contains no such executable.
- [.env.example](../.env.example) names AI variables the runtime does not consume; Compose forwards only master key and port. The application does not itself load `.env`.
- Unknown YAML fields are silently ignored: `autnomy: observe` is accepted and falls back to `triage`.
- `WAKEY_DATABASE_URL` is passed to SQLite even for a Postgres URL; unsupported backends need a clear rejection until implemented. Dependency ranges have no lock/constraints artifact for reproducible application builds.
- `doctor` exercises a local ConsoleForge pipeline rather than proving the running server/real forge route works; `status` reads/creates local storage instead of reporting daemon health.
- Required change: align CLI packaging, environment handling and Compose; reject unknown config; fail clearly on unsupported backends; add reproducible dependency resolution and a doctor that accurately reports the stages it tested.
- Closure test: follow only the documented instructions in a clean environment; verify executable, settings propagation, typo rejection, supported storage selection and real-server diagnosis.
- Existing tasks: E2-T2/T3, E3-T7, E11-T5/T7, E1 packaging.

### R-13 — Medium: runtime SQLite data is tracked in Git

- Evidence: `git ls-files wakey-data` includes `wakey-data/wakey.db`; [.gitignore](../.gitignore) does not exclude the runtime data directory.
- Impact: subsequent local operations can place logs, metadata or credentials into future commits. Database contents were not inspected in this review; this finding does not assert an actual secret leak.
- Required change: preserve the local database, stop tracking runtime state, ignore its DB/WAL/SHM files, and use generated synthetic fixtures instead. Assess historical exposure separately without printing data. Do not delete local state or rewrite history as part of routine cleanup.
- Closure criterion: runtime data remains locally available but cannot enter a normal Git add/commit; fixture creation is reproducible.
- Existing tasks: E1-T1/T5, E10 credential/data controls.

### R-14 — Medium: resource evidence does not substantiate the stated budgets

- Evidence: [benchmark](../scripts/bench_resources.py) claims 50 error families, but the families differ by numbers that fingerprint normalization removes. It ignores HTTP response statuses and calculates throughput from attempted rather than verified accepted events.
- The script measures peak RSS and short-run throughput only; it is not the documented idle/lean/sustained profile suite, lacks container resource enforcement in its Make target, and is not called by CI despite the session-log claim.
- [Storage](../src/wakey/core/storage.py) retains event/delivery rows without scheduled cleanup; active-board queries fetch all fingerprints before filtering in Python. These paths need bounded retention/query behavior before the disk and scale targets are credible.
- Required change: test semantically distinct families; assert accepted/stored counts and response status; run inside capped containers; measure CPU, disk, startup and steady-state latency as specified; record a baseline and implement the regression gate.
- Closure criterion: induced errors and resource regressions fail the harness; measured evidence covers each claimed budget and retention/recovery behavior.
- Existing tasks: E1-T6, E2-T6, E4-T10, E11 operations.

## 5. What to do next, in order

These are remediation work packages within the existing P1 plan, not new product phases. Complete and verify each package before declaring its dependent work ready. Record changes in TASKS/STATE and the affected workflow specs. Finish all remaining P1 acceptance criteria as well as these findings before M3; this report is not a replacement feature backlog.

| Order | Work package | Findings | Required exit evidence |
|---|---|---|---|
| A | Establish an honest baseline and contain exposure | R-07, R-10, R-11, R-13 | Accurate status/security claims, runtime data excluded safely, local exposure controlled, regression backlog mapped |
| B | Make ingestion safe and durable | R-02, R-04, R-05, R-12 | Negative tests pass for redaction, malformed batches, retry/restart, config, filtering and caps |
| C | Complete startup and M0 | R-01 plus B integration | Clean install -> register -> ingest -> deduplicated ticket; GCP/deploy correlation and required forge gate evidenced |
| D | Complete evidence-based RCA and M1 | R-10; existing E7 scope | Ticket automatically gains validated RCA with code evidence, budget checks, redacted prompts and outage recovery |
| E | Complete isolated fixes and M2 | R-03, R-06, R-08, R-09 | Repro -> tested patch -> actual draft branch/PR -> authorized merge/deploy event -> persisted verification verdict |
| F | Complete operations and M3 | R-11, R-12, R-14; remaining E10/E11 scope | Coverage/gates enforced, safe benchmarks, backup/restore, retention, onboarding, security evidence and clean-install drill |

### Package A: immediate next implementation session

1. Read this report, SKILL.md, STATE.md and TASKS.md. Inspect the current worktree and coordinate with ongoing lifecycle work before editing overlapping files.
2. Reproduce the reviewed baseline with `make check` and a coverage run. Do not interpret old test counts as current evidence.
3. Correct README/status/security claims. Keep historical session entries intact; add corrections and map R-01 through R-14 to existing task IDs. Reopen incomplete tasks instead of marking their partial implementation done.
4. Preserve the runtime database while removing it from future version control. Exclude runtime artifacts; do not delete the database or rewrite Git history.
5. Restrict development exposure to localhost until dashboard auth is complete, and keep the live fix path disabled until sandbox tests pass.
6. Turn R-02 and R-04 reproductions into permanent tests asserting correct behavior. These begin package B; do not weaken assertions to make current behavior pass.

Deliverable: a reviewed, focused baseline correction and the first regression tests. No new P2/P3 features, production deployment, remote push or live forge write is needed for this package.

### Package B: implementation sequence

1. Define canonical service configuration and reject unknown keys. Resolve observe-mode semantics explicitly.
2. Validate envelopes/fields per event and preserve multiline content needed for safe redaction and traceback parsing.
3. Sanitize all accepted external fields before any durable storage or external call.
4. Persist accepted events and pending work atomically. Recover pending work after restart; scope deduplication correctly.
5. Consume pending work through bounded workers; move blocking external requests off the async ingestion request path. Add payload limits and explicit backpressure.
6. Enforce error filtering, time-window thresholds, ticket caps, comment throttling and dispatch ownership.
7. Exercise partial failure, restart and forge-outage recovery before connecting the complete path to startup.

### Packages C through F: integration rules

- Use the same dependency composition in the actual entrypoint and integration tests. A demo-specific assembly is supplementary evidence.
- Do not accept `/healthz` or `/readyz` alone as proof of an operational monitoring loop.
- Complete GitHub App/PAT onboarding, service registration, GCP envelope/OIDC support and deploy correlation under the existing P1 scope. Missing credentials do not block local contract/failure-path work; live gates remain explicitly pending.
- Establish the RCA runtime, provider abstraction, validated outputs, cost accounting and security boundary before exposing live model-generated changes.
- For draft PRs, verify the committed tree is the tested tree. For verification, assert both local state and external issue/PR state, including restart and repeat-fix inhibition.
- Implement operational cleanup, retention, backup/restore and shutdown ownership. Verify resource targets with controlled evidence rather than extrapolating a short synthetic run.
- Preserve the no-merge invariant. Live checks must respect the repository allowlist and current authorization; this document does not itself authorize external writes.

## 6. Verification commands and completion rules

Existing local checks from the repository root:

```bash
make check
make test
.venv/bin/pytest --cov=wakey --cov-report=term-missing --cov-fail-under=90
```

The coverage command is expected to fail on the reviewed baseline because measured coverage is 87%. This is a diagnostic, not a reason to lower the threshold. Configure critical-module floors separately as required by the engineering standards. Keep test timeouts and worker caps; run load/sandbox tests only in capped disposable containers.

Do not use `make bench` as release evidence until R-14 is fixed. Do not invoke `make gate-m0` through `make gate-m3` until those targets are implemented; their absence is part of R-11.

For each finding, closure requires:

1. A regression test asserting the desired behavior, shown to catch the original defect.
2. A focused implementation with no unrelated changes.
3. Relevant unit/contract/integration tests and `make check` passing.
4. Application-entrypoint evidence when the finding concerns runtime integration.
5. Updated workflow/task/state documentation, including limitations and any pending live gate.
6. A reviewer checking the acceptance criteria against behavior, not just test count or line coverage.

Track progress with this shape in the existing task/state records:

```text
Finding: R-02
Tasks: E4-T7, E4-T9, E2-T5
Status: open | in-progress | verified
Regression tests: <paths and test names>
Runtime evidence: <command, date, result>
Remaining limitations: <explicit list or none>
```

## 7. Copyable instruction for the next development session

> Read SKILL.md, docs/STATE.md, docs/TASKS.md and docs/development-review-2026-09-16.md. Start with work package A, then package B. Recheck the current worktree and preserve other sessions' changes, particularly ticket lifecycle work. Correct unsupported completion/security claims, preserve but untrack runtime data, constrain unauthenticated development exposure, and turn the delivery-loss and redaction reproductions into permanent regression tests before fixing them. Work within existing P1 tasks and specs. Verify each change with focused tests and make check; record concrete evidence in TASKS/STATE. Do not claim milestone completion from simulator-only tests. Do not add a remote, push, deploy, use real credentials, or perform live forge writes as part of this local remediation. Do not commit unless explicitly requested. Keep live autonomous fixing disabled until path containment and isolated-runner controls pass their negative tests.

## 8. Review limitations

This is a development review, not an external security certification. Passing 133 tests establishes only the behavior those tests cover. The ten additional reproductions prove selected defects; other findings are based on code/configuration inspection. No production deployment, live model quality evaluation, sustained load test or historical secret audit was performed. Revalidate findings when concurrent implementation changes land.
