# SPDX-License-Identifier: Apache-2.0
"""Entry point for ``python -m wakey``.

Subcommands:
    serve      start the wakeyd HTTP server (real SQLite storage + uvicorn)
    --version  report the running version

The container ``CMD`` uses ``serve``; the quickstart banner and first-run
token arrive with the dashboard (WF-12).
"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from wakey import __version__
from wakey.core.config import Settings
from wakey.core.logging import setup_logging
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wakey",
        description="Wakey — the self-hosted AI SRE that watches your services.",
    )
    parser.add_argument("--version", action="version", version=f"wakey {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="start the wakeyd HTTP server")
    serve.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")

    board = subparsers.add_parser("board", help="live incident board (WorkState rows)")
    board.add_argument("--service", default=None)

    subparsers.add_parser("status", help="one-glance instance summary")
    return parser


def open_storage(settings: Settings) -> SQLiteStorage:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = settings.database_url or str(settings.data_dir / "wakey.db")
    return SQLiteStorage(database.removeprefix("sqlite://"))  # Postgres path: OPS-5


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        settings = Settings.from_env(dict(os.environ))
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        storage = open_storage(settings)
        fps = storage.list_active_fingerprints()
        print(f"wakey {__version__} | active fingerprints: {len(fps)} | data: {settings.data_dir}")
        for fp in fps:
            print(f"  fp:{fp.fp_hash} {fp.service} x{fp.occurrences} {fp.state.value}")
        return 0
    if args.command == "board":
        settings = Settings.from_env(dict(os.environ))
        storage = open_storage(settings)
        for fp in storage.list_active_fingerprints(getattr(args, "service", None)):
            print(f"fp:{fp.fp_hash}  {fp.service:<20} x{fp.occurrences:<6} {fp.state.value}")
        return 0
    if args.command != "serve":
        print(f"wakey {__version__} — use 'wakey serve' to start the server")
        return 0

    settings = Settings.from_env(dict(os.environ))
    setup_logging(settings.log_level)
    storage = open_storage(settings)

    app = create_app(settings, storage)
    logging.getLogger(__name__).info("wakey listening on port %s", settings.port)
    uvicorn.run(app, host=args.host, port=settings.port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
