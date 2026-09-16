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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "serve":
        print(f"wakey {__version__} — use 'wakey serve' to start the server")
        return 0

    settings = Settings.from_env(dict(os.environ))
    setup_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    database = settings.database_url or str(settings.data_dir / "wakey.db")
    # SQLite at data_dir by default; the Postgres URL path arrives with OPS-5
    storage = SQLiteStorage(database.removeprefix("sqlite://"))

    app = create_app(settings, storage)
    logging.getLogger(__name__).info("wakey listening on port %s", settings.port)
    uvicorn.run(app, host=args.host, port=settings.port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
