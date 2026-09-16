# SPDX-License-Identifier: Apache-2.0
"""Entry point for ``python -m wakey`` (and the ``wakey`` console script).

Subcommands:
    serve      start wakeyd: HTTP server + durable delivery workers (R-01)
    status     one-glance local instance summary
    board      live incident board (WorkState rows)
    doctor     staged end-to-end pipeline check, reporting what it tested

The container ``CMD`` uses ``serve``; it composes through
:mod:`wakey.core.composition` — the same path integration tests use — so
a fresh install ingests, tickets, and dispatches RCA without test-only
wiring.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
from collections.abc import Callable
from datetime import UTC, datetime

import uvicorn

from wakey import __version__
from wakey.agents.llm import AnthropicCompatibleModel
from wakey.core.composition import compose, open_storage
from wakey.core.config import Settings
from wakey.core.logging import setup_logging
from wakey.core.models import LogEvent, Severity
from wakey.core.server import create_app
from wakey.forge.port import ConsoleForge
from wakey.ingest.pipeline import IngestPipeline
from wakey.web.auth import AuthManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wakey",
        description="Wakey — the self-hosted AI SRE that watches your services.",
    )
    parser.add_argument("--version", action="version", version=f"wakey {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="start the wakeyd HTTP server")
    # Loopback default: the dashboard is unauthenticated until WF-01/WF-12
    # first-run token auth lands; never expose it beyond localhost by accident.
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")

    board = subparsers.add_parser("board", help="live incident board (WorkState rows)")
    board.add_argument("--service", default=None)

    subparsers.add_parser("status", help="one-glance instance summary (local storage view)")

    subparsers.add_parser("doctor", help="staged end-to-end pipeline check")

    token = subparsers.add_parser(
        "setup-token", help="print a fresh single-use dashboard setup token"
    )
    token.add_argument("--force", action="store_true", help="invalidate any active token")

    backup = subparsers.add_parser("backup", help="consistent online backup (OPS-6)")
    backup.add_argument("--out", default=None, help="destination file (default data-dir)")

    restore = subparsers.add_parser("restore", help="restore a backup (server must be stopped)")
    restore.add_argument("file")
    return parser


def _backup_target(settings: Settings, out: str | None) -> pathlib.Path:
    if out:
        return pathlib.Path(out)
    return settings.data_dir / f"wakey-backup-{datetime.now(UTC):%Y%m%dT%H%M%S}.db"


def _restore_backup(settings: Settings, file: str) -> int:
    """Restore drill (OPS-6): copy, clear WAL, verify the store answers."""
    source = pathlib.Path(file)
    if not source.is_file():
        print(f"restore failed: {source} is not a file")
        return 1
    target = settings.data_dir / "wakey.db"
    print("stop the server before restoring; restoring now:")
    print(f"  {source} -> {target}")
    shutil.copyfile(source, target)
    for suffix in ("-wal", "-shm"):
        stale = pathlib.Path(str(target) + suffix)
        if stale.exists():
            stale.unlink()
    storage = open_storage(settings)
    if not storage.check_ready():
        print("restore failed: restored database does not answer")
        return 1
    storage.close()
    print("restore complete (verify with: wakey doctor)")
    return 0


def _run_setup_token(force: bool) -> int:
    settings = Settings.from_env(dict(os.environ))
    auth = AuthManager(open_storage(settings))
    token, _ = auth.ensure_setup_token(force=force)
    print(token)
    return 0


def _run_backup(settings: Settings, out: str | None) -> int:
    storage = open_storage(settings)
    if type(storage).__module__.startswith("psycopg") or not hasattr(storage, "backup"):
        storage.close()
        print("backup: Postgres backends are backed up with pg_dump (docs/operations.md)")
        return 1
    destination = _backup_target(settings, out)
    storage.backup(destination)
    storage.close()
    print(f"backup written: {destination}")
    return 0


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (R-12): the documented quickstart actually works.

    Never overrides variables already set in the environment; ignores
    malformed lines; values may be quoted.
    """
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    env = dict(os.environ)
    handlers: dict[str, Callable[[], int]] = {
        "status": _status,
        "board": lambda: _board(getattr(args, "service", None)),
        "doctor": _doctor,
        "setup-token": lambda: _run_setup_token(force=bool(getattr(args, "force", False))),
        "backup": lambda: _run_backup(Settings.from_env(env), getattr(args, "out", None)),
        "restore": lambda: _restore_backup(Settings.from_env(env), getattr(args, "file", "")),
        "serve": lambda: _serve(args),
    }
    handler = handlers.get(args.command)
    if handler is None:
        print(f"wakey {__version__} — use 'wakey serve' to start the server")
        return 0
    result: int = handler()
    return result


def _serve(args: argparse.Namespace) -> int:
    settings = Settings.from_env(dict(os.environ))
    setup_logging(settings.log_level)
    settings = Settings.from_env(dict(os.environ))
    setup_logging(settings.log_level)
    # Single composition path (R-01): real storage, forge, pipeline, workers.
    components = compose(settings)
    app = create_app(
        settings,
        components.storage,
        forge=components.forge,
        redactor=components.redactor,
        runtime=components.runtime,
        secret_box=components.secret_box,
        commands=components.commands,
    )
    configured = "configured" if components.storage.has_services() else "no services registered"
    sink = "github" if settings.live_forge else "console"
    print(
        f"wakey {__version__} listening on {args.host}:{settings.port} "
        f"— ingest enabled, forge: {sink}, services: {configured}"
    )
    token, created = components.auth.ensure_setup_token()
    if created:
        print(f"first-run setup token (single use, 30 min): {token}", flush=True)
    else:
        print("dashboard locked — request a fresh token with: wakey setup-token", flush=True)
    try:
        uvicorn.run(app, host=args.host, port=settings.port, log_config=None)
    finally:
        components.storage.close()
    return 0


def _status() -> int:
    settings = Settings.from_env(dict(os.environ))
    storage = open_storage(settings)
    fps = storage.list_active_fingerprints()
    mode = "live github" if settings.live_forge else "console forge (dev)"
    print(
        f"wakey {__version__} | active fingerprints: {len(fps)} | data: {settings.data_dir} "
        f"| forge: {mode}"
    )
    for fp in fps:
        print(f"  fp:{fp.fp_hash} {fp.service} x{fp.occurrences} {fp.state.value}")
    return 0


def _board(service: str | None) -> int:
    settings = Settings.from_env(dict(os.environ))
    storage = open_storage(settings)
    for fp in storage.list_active_fingerprints(service):
        print(f"fp:{fp.fp_hash}  {fp.service:<20} x{fp.occurrences:<6} {fp.state.value}")
    return 0


def _doctor() -> int:
    """Staged check: reports exactly which stages ran and passed (R-12)."""
    setup_logging("ERROR")
    settings = Settings.from_env(dict(os.environ))
    storage = open_storage(settings)
    pipeline = IngestPipeline(storage, ConsoleForge())
    event = LogEvent(
        id="evt-doctor0000ff",
        ts=datetime.now(UTC),
        service="doctor",
        environment="prod",
        severity=Severity.ERROR,
        message="wakey doctor synthetic error: connection refused to db:0000",
        source="doctor",
    )
    result = pipeline.handle_event(event)
    stages_ok = result.outcome.value in {"ticketed", "recorded", "absorbed"}
    print(f"doctor: local pipeline -> {result.outcome.value} ({result.detail})")

    llm_ok = True
    if settings.llm_base_url and settings.llm_api_key:
        model = AnthropicCompatibleModel(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model, max_tokens=512
        )
        reply = model.complete(
            "You are an SRE. Reply with CLASS:"
            " <code-fix|config|infra|needs-human> then one sentence.",
            f"Classify this production error:\n{event.message}",
        )
        llm_ok = not reply.data.get("error") and bool(reply.text.strip())
        preview = reply.text.strip()[:200]
        print(f"doctor: live model -> {preview}")
    else:
        print("doctor: live model -> skipped (WAKEY_LLM_* not configured)")

    print(
        "doctor: real HTTP server / live forge NOT exercised by this command "
        "(use 'wakey serve' + an ingest POST)"
    )
    return 0 if (stages_ok and llm_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
