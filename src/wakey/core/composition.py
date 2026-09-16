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

from wakey.agents.fix_executor import FixExecutionOutcome, FixExecutor
from wakey.agents.llm import AnthropicCompatibleModel
from wakey.agents.rca import ModelRca, RcaBudget, RcaDispatcher
from wakey.core.config import ConfigError, ServiceYaml, Settings, load_wakey_yaml
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import AuditEvent, Fingerprint, WorkState, utcnow
from wakey.core.storage import SQLiteStorage, Storage
from wakey.forge.github import GitHubAdapter, GitHubConfig
from wakey.forge.port import ConsoleForge, ForgePort, TicketRef
from wakey.ingest.pipeline import IngestPipeline
from wakey.ingest.worker import DeliveryWorker, DeliveryWorkerPool
from wakey.policy.verification import WatchInputs
from wakey.policy.watcher import VerificationWatcher, grace_inputs_from_storage
from wakey.security.redaction import RedactionEngine
from wakey.security.secretbox import SecretBox
from wakey.storage.postgres import PostgresStorage
from wakey.tickets import lifecycle as ticket_lifecycle
from wakey.web.auth import AuthManager
from wakey.web.commands import CommandDispatcher

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
    rca_interval_seconds: float = 30.0
    watcher_interval_seconds: float = 60.0
    maintenance_interval_seconds: float = 3600.0
    lifecycle_interval_seconds: float = 300.0
    config_reload_interval_seconds: float = 30.0
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def start(self) -> None:
        await self.pool.start()
        if self._tasks:
            return
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


def build_forge(settings: Settings) -> ForgePort:
    """GitHub adapter when credentials exist; ConsoleForge is the dev sink."""
    if not settings.live_forge:
        return ConsoleForge()
    config = GitHubConfig(
        token=settings.github_token,
        repo=settings.github_repo,
        api_base=settings.github_api_base,
        allowed_repos=settings.allowlist,
    )
    return GitHubAdapter(config)


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


def build_fix_executor(
    settings: Settings,
    storage: Storage,
    forge: ForgePort,
) -> FixExecutor | None:
    """The live fix executor: present when forge + model credentials exist."""
    if not (settings.live_forge and settings.llm_base_url and settings.llm_api_key):
        return None
    fix_model = AnthropicCompatibleModel(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, max_tokens=2048
    )
    return FixExecutor(
        storage,
        forge,
        github_token=settings.github_token,
        github_api_base=settings.github_api_base,
        allowed_repos=settings.allowlist,
        model=fix_model,
        work_root=settings.data_dir / "workspaces",
    )


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
) -> CommandDispatcher:
    """The command bus, with fix execution wired when available."""

    def run_fix(fp: Fingerprint) -> FixExecutionOutcome:
        assert fix_executor is not None  # guarded at the dispatch site
        return fix_executor.execute(fp, _stored_config(storage, fp.service))

    return CommandDispatcher(
        storage,
        forge,
        rca,
        authorized_users=authorized,
        metrics=metrics,
        fix_executor=run_fix if fix_executor is not None else None,
    )


def compose(settings: Settings, forge: ForgePort | None = None) -> Components:
    """Assemble the full running system (R-01 closure path)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # SEC-4/E10-T3: reversible credentials get AES-GCM at rest when a master
    # key is configured; configuring a key without the crypto package is a
    # hard error (fail closed) rather than silently-plaintext storage.
    secret_box: SecretBox | None = None
    if settings.master_key.strip():
        secret_box = SecretBox(settings.master_key)
    storage = open_storage(settings, secret_box=secret_box)  # SQLite or Postgres (OPS-5)
    storage.recover_stale_deliveries()  # crash recovery before serving (R-02)

    metrics = MetricsRegistry()
    redactor = RedactionEngine()
    forge = forge if forge is not None else build_forge(settings)
    pipeline = IngestPipeline(storage, forge)
    worker = DeliveryWorker(storage, pipeline, redactor, metrics=metrics)
    pool = DeliveryWorkerPool(worker, count=settings.worker_count)

    model_rca = build_model_rca(settings, redactor)
    rca = RcaDispatcher(
        storage,
        forge,
        model_rca=model_rca,
        budget=RcaBudget(max_per_hour=settings.rca_max_per_hour),
        redactor=redactor,
    )

    def deployed_checker(fingerprint: Fingerprint) -> bool:
        """Deploy-aware verification (WF-08 §1): the clock waits for the deploy."""
        if fingerprint.verification_started_at is None:
            return True
        deploys = storage.list_recent_deploys(fingerprint.service, limit=25)
        if not deploys:
            return True  # deploy-blind mode: no deploy source configured
        return any(d.deployed_at >= fingerprint.verification_started_at for d in deploys)

    def grace_inputs(fingerprint: Fingerprint) -> WatchInputs:
        config = _stored_config(storage, fingerprint.service)
        window = max(3 * config.grace_minutes, 60)  # WF-08: extended watch window
        return grace_inputs_from_storage(
            fingerprint,
            lambda: utcnow().timestamp(),
            grace_minutes=window,
            deployed=deployed_checker,
        )

    watcher = VerificationWatcher(storage, forge, grace_inputs=grace_inputs)

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
    fix_executor = build_fix_executor(settings, storage, forge)
    commands = build_commands(
        storage,
        forge=forge,
        rca=rca,
        metrics=metrics,
        authorized=authorized,
        fix_executor=fix_executor,
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
