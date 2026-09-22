# SPDX-License-Identifier: Apache-2.0
"""Application composition (R-01): one path for CLI, container, and tests.

Demos previously assembled dependencies directly while ``serve`` built a
forge-less app — an apparently healthy installation that could not ingest.
Every entrypoint now composes through :func:`compose`: real SQLite
storage, a forge (GitHub when credentials exist, ConsoleForge otherwise),
the redaction engine, the durable delivery pipeline and worker pool, the
RCA dispatcher with its budget, the verification watcher loop, the ticket
lifecycle pass, and the dashboard auth/command-bus surface. Startup
recovery runs here: stale ``processing`` deliveries return to ``pending``
before anything serves.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from wakey.agents.digest import DigestManager
from wakey.agents.fix import TestOutcome
from wakey.agents.fix_executor import FixExecutionOutcome, FixExecutor
from wakey.agents.llm import AnthropicCompatibleModel
from wakey.agents.rca import ModelRca, RcaBudget, RcaDispatcher
from wakey.agents.sandbox import DockerTestRunner, RemoteTestRunner
from wakey.core.config import Autonomy, ConfigError, ServiceYaml, Settings, load_wakey_yaml
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import AuditEvent, DeployEvent, Fingerprint, WorkState, utcnow
from wakey.core.storage import SQLiteStorage, Storage
from wakey.forge.connect_client import ConnectClient, ConnectForge
from wakey.forge.github import GitHubAdapter, GitHubConfig
from wakey.forge.github_app import InstallationTokenSource
from wakey.forge.port import ConsoleForge, ForgePort, TicketRef
from wakey.ingest.pipeline import IngestPipeline
from wakey.ingest.worker import DeliveryWorker, DeliveryWorkerPool
from wakey.policy.verification import WatchInputs
from wakey.policy.watcher import VerificationWatcher
from wakey.security.bootstrap import persistent_master_key
from wakey.security.redaction import RedactionEngine
from wakey.security.secretbox import SecretBox
from wakey.storage.postgres import PostgresStorage
from wakey.tickets import lifecycle as ticket_lifecycle
from wakey.web.auth import AuthManager
from wakey.web.commands import CommandDispatcher
from wakey.web.github_setup import SECRET_CLIENT_ID_SLUG, SECRET_PRIVATE_KEY_SLUG

logger = logging.getLogger(__name__)


@dataclass
class Components:
    """Everything the running service owns; the caller stops ``runtime``."""

    settings: Settings
    storage: Storage
    metrics: MetricsRegistry
    forge: ForgePort
    redactor: RedactionEngine
    pipeline: IngestPipeline
    worker: DeliveryWorker
    pool: DeliveryWorkerPool
    rca: RcaDispatcher
    watcher: VerificationWatcher
    auth: AuthManager
    commands: CommandDispatcher
    fix_executor: FixExecutor | None
    digest: DigestManager | None
    lifecycle: Callable[[], None]
    config_reload: Callable[[], None]
    secret_box: SecretBox | None
    runtime: Runtime


@dataclass
class Runtime:
    """Background task ownership for the composed app (start/stop)."""

    pool: DeliveryWorkerPool
    rca: RcaDispatcher | None
    watcher: VerificationWatcher | None
    maintenance: Callable[[], None] | None
    lifecycle: Callable[[], None] | None
    config_reload: Callable[[], None] | None
    digest: Callable[[], object] | None = None
    rca_interval_seconds: float = 30.0
    watcher_interval_seconds: float = 60.0
    maintenance_interval_seconds: float = 3600.0
    lifecycle_interval_seconds: float = 300.0
    config_reload_interval_seconds: float = 30.0
    connect_poll: Callable[[], None] | None = None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def start(self) -> None:
        await self.pool.start()
        if self._tasks:
            return
        if self.connect_poll is not None:
            self._tasks.append(asyncio.ensure_future(self._loop(10, self.connect_poll)))
        if self.maintenance is not None:
            self.maintenance()  # prune once at boot, then hourly
            self._tasks.append(
                asyncio.ensure_future(
                    self._loop(self.maintenance_interval_seconds, self.maintenance)
                )
            )
        if self.rca is not None:
            self._tasks.append(
                asyncio.ensure_future(self._loop(self.rca_interval_seconds, self.rca.run_once))
            )
        if self.watcher is not None:
            self._tasks.append(
                asyncio.ensure_future(
                    self._loop(self.watcher_interval_seconds, self.watcher.run_once)
                )
            )
        if self.config_reload is not None:
            self._tasks.append(
                asyncio.ensure_future(
                    self._loop(self.config_reload_interval_seconds, self.config_reload)
                )
            )
        if self.lifecycle is not None:
            self._tasks.append(
                asyncio.ensure_future(self._loop(self.lifecycle_interval_seconds, self.lifecycle))
            )
        if self.digest is not None:
            self._tasks.append(asyncio.ensure_future(self._loop(3600, self.digest)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        await self.pool.stop()

    async def _loop(self, interval: float, step: Callable[[], object]) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(step)
            except asyncio.CancelledError:
                raise
            except Exception:
                # background passes must never take the pipeline down (NFR-4)
                logger.exception("background pass failed")


def build_forge(
    settings: Settings,
    storage: Storage | None = None,
    secret_box: SecretBox | None = None,
) -> ForgePort:
    """GitHub adapter when credentials exist; ConsoleForge is the dev sink.

    Auth resolves GitHub App installation first, PAT second: when the
    setup flow stored App credentials (encrypted at rest, SEC-4), the
    adapter authenticates with freshly minted installation tokens and a
    PAT is not required — App-only onboarding completes the operational
    connection.
    """
    if settings.connect_url:
        if storage is None or secret_box is None:
            raise ValueError("Connect requires encrypted persistent credential storage")
        return ConnectForge(ConnectClient(settings.connect_url, storage, secret_box))
    provider = _app_token_provider(settings, storage, secret_box)
    if not settings.github_repo or (not settings.live_forge and provider is None):
        return ConsoleForge()
    config = GitHubConfig(
        token="" if provider is not None else settings.github_token,
        repo=settings.github_repo,
        api_base=settings.github_api_base,
        allowed_repos=settings.allowlist,
    )
    return GitHubAdapter(config, token_provider=provider)


def _app_token_provider(
    settings: Settings,
    storage: Storage | None,
    secret_box: SecretBox | None,
) -> Callable[[], str] | None:
    """Installation-token source from stored App credentials, or None."""
    if storage is None or secret_box is None:
        return None
    stored_key = storage.get_secret(SECRET_PRIVATE_KEY_SLUG)
    client_id = storage.get_secret(SECRET_CLIENT_ID_SLUG)
    if not stored_key or not client_id:
        return None
    try:
        private_key_pem = secret_box.decrypt(stored_key)
    except Exception:  # noqa: BLE001 — unusable credentials fall back to the PAT
        logger.warning("GitHub App private key undecryptable — falling back to PAT auth")
        return None
    source = InstallationTokenSource(
        private_key_pem,
        client_id,
        api_base=settings.github_api_base,
    )
    return source.token


def build_model_rca(settings: Settings, redactor: RedactionEngine) -> ModelRca | None:
    """Live model tier only when both endpoint and key are configured."""
    if not (settings.llm_base_url and settings.llm_api_key):
        return None
    model = AnthropicCompatibleModel(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, max_tokens=512
    )
    return ModelRca(model, redactor)


def _stored_config(storage: Storage, service: str) -> ServiceYaml:
    stored = storage.get_service(service)
    if stored is None:
        return ServiceYaml()
    try:
        return ServiceYaml.model_validate_json(stored.config_json)
    except Exception:  # noqa: BLE001 — invalid stored config falls back to defaults
        return ServiceYaml()


def build_config_reload(
    storage: Storage,
    forge: ForgePort,
    metrics: MetricsRegistry,
) -> Callable[[], None]:
    """Build the E3-T6 pass: fetch ``wakey.yml``; keep last-good on errors."""

    def reload_once() -> None:
        try:
            raw = forge.fetch_file("wakey.yml")
        except Exception:  # noqa: BLE001 — forge outage must not kill the loop
            logger.warning("config refetch failed (forge unreachable)")
            return
        if raw is None:
            return  # repo has no wakey.yml — keep current configs
        try:
            parsed = load_wakey_yaml(raw)
        except ConfigError as exc:
            logger.warning("wakey.yml invalid — keeping last-good config: %s", exc)
            metrics.inc("wakey_config_rejects_total", "rejected wakey.yml loads")
            return
        for name, service_config in parsed.services.items():
            stored = storage.get_service(name)
            if stored is None:
                continue  # config only applies to registered services
            storage.save_service(
                stored.model_copy(update={"config_json": service_config.model_dump_json()})
            )
        metrics.inc("wakey_config_reloads_total", "wakey.yml reloads applied")

    return reload_once


def _build_runner(settings: Settings) -> Callable[[Path, list[str]], TestOutcome]:
    """Test runner for the fix executor: remote isolated executor when wired.

    Production topology (readiness #5): a host without a container runtime
    (Cloud Run) delegates sandboxed test execution to a dedicated executor
    service; local deployments keep the in-process Docker runner. Fail
    closed: an executor URL without a token is a configuration error (the
    endpoint must never be called unauthenticated).
    """
    if settings.sandbox_executor_url:
        return RemoteTestRunner(settings.sandbox_executor_url, settings.sandbox_executor_token)
    return DockerTestRunner(settings.sandbox_image)


def build_fix_executor(
    settings: Settings,
    storage: Storage,
    forge: ForgePort,
    secret_box: SecretBox | None = None,
) -> FixExecutor | None:
    """The live fix executor: present when forge + model credentials exist."""
    provider = _app_token_provider(settings, storage, secret_box)
    if not (settings.llm_base_url and settings.llm_api_key):
        return None
    if not (settings.live_forge or provider is not None or isinstance(forge, ConnectForge)):
        return None
    fix_model = AnthropicCompatibleModel(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, max_tokens=2048
    )
    verifier_model = AnthropicCompatibleModel(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, max_tokens=512
    )
    return FixExecutor(
        storage,
        forge,
        github_token="" if provider is not None else settings.github_token,
        github_api_base=settings.github_api_base,
        allowed_repos=settings.allowlist,
        model=fix_model,
        verifier_model=verifier_model,
        work_root=settings.data_dir / "workspaces",
        runner=_build_runner(settings),
        github_token_provider=forge.connect.token if isinstance(forge, ConnectForge) else provider,
    )


def build_fix_trigger(storage: Storage, fix_executor: FixExecutor) -> Callable[[Fingerprint], None]:
    """autonomy=fix dial (WF-06 §2): unattended fix dispatch after RCA.

    The RCA dispatcher calls this for confident code-fix verdicts; the
    executor re-runs the full FIX-1 gate (chronic, PR cap, floor) so a race
    or config flip between the two checks stays safe. Services on any lower
    autonomy dial never see a fix attempt.
    """

    def trigger(fingerprint: Fingerprint) -> None:
        config = _stored_config(storage, fingerprint.service)
        if config.autonomy is not Autonomy.FIX:
            return
        fix_executor.execute(fingerprint, config)

    return trigger


def _parse_authorized(settings: Settings) -> frozenset[str]:
    return frozenset(u.strip() for u in settings.authorized_users.split(",") if u.strip())


def build_commands(
    storage: Storage,
    *,
    forge: ForgePort,
    rca: RcaDispatcher,
    metrics: MetricsRegistry,
    authorized: frozenset[str],
    fix_executor: FixExecutor | None,
    digest: DigestManager | None,
) -> CommandDispatcher:
    """The command bus, with fix execution wired when available."""

    def run_fix(fp: Fingerprint) -> FixExecutionOutcome:
        assert fix_executor is not None  # guarded at the dispatch site
        return fix_executor.execute(fp, _stored_config(storage, fp.service), human_requested=True)

    def run_retry(fp: Fingerprint, feedback: str) -> FixExecutionOutcome:
        assert fix_executor is not None  # guarded at the dispatch site
        return fix_executor.retry(fp, _stored_config(storage, fp.service), feedback)

    def build_digest(fp: Fingerprint) -> str:
        assert digest is not None
        service = storage.get_service(fp.service)
        if service is None:
            return "Service repository binding is unavailable."
        outcomes = digest.run_once(service.repo)
        if not outcomes:
            return "No eligible digest candidates are waiting for this repository."
        return outcomes[0].message

    return CommandDispatcher(
        storage,
        forge,
        rca,
        authorized_users=authorized,
        metrics=metrics,
        fix_executor=run_fix if fix_executor is not None else None,
        retry_executor=run_retry if fix_executor is not None else None,
        digest_builder=build_digest if digest is not None else None,
        digest_approver=digest.approve if digest is not None else None,
    )


def build_grace_inputs(storage: Storage) -> Callable[[Fingerprint], WatchInputs]:
    """Deploy-aware, deployment-relative watch inputs (WF-08 §1-2).

    Environment binding: a staging deploy never counts for a prod
    fingerprint. Commit binding: when the merged fix commit is known, the
    deploy sha must match it (short-shas compare by prefix). The window
    opens when the fix actually lands in the fingerprint's environment —
    not at merge time — so occurrences from before the deploy can never
    count against the patch, and deploys of anything else keep the
    verdict inconclusive.
    """

    def matching_deploys(fingerprint: Fingerprint) -> list[DeployEvent]:
        """Deploys of this fingerprint's environment that carry the fix."""
        if fingerprint.verification_started_at is None:
            return []
        fix_sha = fingerprint.fix_commit_sha or ""
        return [
            d
            for d in storage.list_recent_deploys(fingerprint.service, limit=25)
            if d.environment == fingerprint.environment
            and d.deployed_at >= fingerprint.verification_started_at
            and (
                not fix_sha
                or d.sha.startswith(fix_sha)
                or (len(d.sha) <= len(fix_sha) and fix_sha.startswith(d.sha))
            )
        ]

    def grace_inputs(fingerprint: Fingerprint) -> WatchInputs:
        config = _stored_config(storage, fingerprint.service)
        window = max(3 * config.grace_minutes, 60)  # WF-08: extended watch window
        started = fingerprint.verification_started_at
        if started is None:
            return WatchInputs(
                occurrences_after_fix=0,
                grace_window_elapsed=False,
                fix_deployed_to_environment=False,
            )
        deploys = storage.list_recent_deploys(fingerprint.service, limit=25)
        if not deploys:  # deploy-blind: judge from the merge-time anchor
            effective_start = started
        else:
            matching = matching_deploys(fingerprint)
            if not matching:
                # deploys exist but none carried the fix to this
                # environment — the window cannot fairly start yet
                return WatchInputs(
                    occurrences_after_fix=0,
                    grace_window_elapsed=False,
                    fix_deployed_to_environment=False,
                )
            effective_start = max(started, min(d.deployed_at for d in matching))
        now = utcnow()
        elapsed = (now - effective_start).total_seconds() / 60.0 >= window
        occurrences = storage.count_fingerprint_events(fingerprint, effective_start, now)
        if occurrences is None:
            return WatchInputs(0, False, False)  # legacy events: never invent a success verdict
        return WatchInputs(
            occurrences_after_fix=occurrences,
            grace_window_elapsed=elapsed,
            fix_deployed_to_environment=True,
        )

    return grace_inputs


def compose(settings: Settings, forge: ForgePort | None = None) -> Components:
    """Assemble the full running system (R-01 closure path)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.auto_master_key and not settings.master_key:
        settings = settings.model_copy(
            update={"master_key": persistent_master_key(settings.data_dir)}
        )
    # SEC-4/E10-T3: reversible credentials get AES-GCM at rest when a master
    # key is configured; configuring a key without the crypto package is a
    # hard error (fail closed) rather than silently-plaintext storage.
    secret_box: SecretBox | None = None
    if settings.master_key.strip():
        secret_box = SecretBox(settings.master_key)
    if settings.connect_url and secret_box is None and forge is None:
        raise ValueError("Wakey Connect requires encrypted storage: configure a master key")
    storage = open_storage(settings, secret_box=secret_box)  # SQLite or Postgres (OPS-5)
    storage.recover_stale_deliveries()  # crash recovery before serving (R-02)

    metrics = MetricsRegistry()
    redactor = RedactionEngine()
    forge = (
        forge
        if forge is not None
        else build_forge(settings, storage=storage, secret_box=secret_box)
    )
    pipeline = IngestPipeline(storage, forge)
    worker = DeliveryWorker(storage, pipeline, redactor, metrics=metrics)
    pool = DeliveryWorkerPool(worker, count=settings.worker_count)

    fix_executor = build_fix_executor(settings, storage, forge, secret_box=secret_box)
    model_rca = build_model_rca(settings, redactor)
    rca = RcaDispatcher(
        storage,
        forge,
        model_rca=model_rca,
        budget=RcaBudget(
            max_per_hour=settings.rca_max_per_hour,
            max_tokens_per_hour=settings.rca_max_tokens_per_hour,
        ),
        redactor=redactor,
        auto_fix=build_fix_trigger(storage, fix_executor) if fix_executor else None,
    )

    digest = (
        DigestManager(
            storage,
            forge,
            lambda service: _stored_config(storage, service),
            lambda fp, config: fix_executor.execute(fp, config, human_requested=True),
        )
        if fix_executor is not None
        else None
    )

    watcher = VerificationWatcher(storage, forge, grace_inputs=build_grace_inputs(storage))

    def run_lifecycle() -> None:
        """Auto-close silent tickets / keep human decisions sticky (WF-04)."""
        now = utcnow()
        for fingerprint in storage.list_active_fingerprints():
            if fingerprint.ticket_url is None:
                continue
            config = _stored_config(storage, fingerprint.service)
            decision = ticket_lifecycle.decide(fingerprint, now, grace_minutes=config.grace_minutes)
            if decision.action != "close":
                continue
            forge.close_ticket(
                TicketRef(
                    issue_id=fingerprint.ticket_issue_id or fingerprint.fp_hash,
                    url=fingerprint.ticket_url,
                ),
                f"auto-closed: {decision.detail} (wakey)",
            )
            storage.save_fingerprint(
                fingerprint.model_copy(update={"state": WorkState.CLOSED_AUTO})
            )
            storage.record_audit(
                AuditEvent(
                    actor="system",
                    action="ticket.auto_close",
                    subject=f"fp:{fingerprint.fp_hash}",
                    details={"detail": decision.detail},
                )
            )
            metrics.inc("wakey_lifecycle_closes_total", "tickets auto-closed on silence")

    auth = AuthManager(storage)
    authorized = _parse_authorized(settings)
    commands = build_commands(
        storage,
        forge=forge,
        rca=rca,
        metrics=metrics,
        authorized=authorized,
        fix_executor=fix_executor,
        digest=digest,
    )

    def run_retention() -> None:
        """Bounded retention (R-14): drop settled rows past the configured window."""
        cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
        storage.prune_events(cutoff)
        storage.prune_deliveries(cutoff)

    config_reload = build_config_reload(storage, forge, metrics)
    runtime = Runtime(
        pool=pool,
        rca=rca,
        watcher=watcher,
        maintenance=run_retention,
        lifecycle=run_lifecycle,
        config_reload=config_reload,
        digest=digest.run_once if digest is not None else None,
        connect_poll=forge.connect.receive if isinstance(forge, ConnectForge) else None,
    )
    return Components(
        settings=settings,
        storage=storage,
        metrics=metrics,
        forge=forge,
        redactor=redactor,
        pipeline=pipeline,
        worker=worker,
        pool=pool,
        rca=rca,
        watcher=watcher,
        auth=auth,
        commands=commands,
        fix_executor=fix_executor,
        digest=digest,
        lifecycle=run_lifecycle,
        config_reload=config_reload,
        secret_box=secret_box,
        runtime=runtime,
    )


def open_storage(
    settings: Settings, secret_box: SecretBox | None = None
) -> SQLiteStorage | PostgresStorage:
    """Open the configured backend (OPS-5): SQLite default, Postgres via DSN."""
    if settings.database_url.startswith(("postgresql://", "postgres://")):
        return PostgresStorage(settings.database_url, secret_box=secret_box)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = settings.database_url or str(settings.data_dir / "wakey.db")
    return SQLiteStorage(database.removeprefix("sqlite://"), secret_box=secret_box)
