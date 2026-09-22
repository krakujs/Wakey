# SPDX-License-Identifier: Apache-2.0
"""Operator entrypoint for the hosted connector, not the customer deployment.

Secret files should be mounted from the host's secret manager. This entrypoint
never discovers or reads another tool's credentials and never logs secret values.
"""

import argparse
import os
from pathlib import Path

import uvicorn

from wakey.forge.connect_github import ConnectGitHub
from wakey.forge.connect_server import create_connect_app
from wakey.forge.connect_store import ConnectStore
from wakey.security.secretbox import SecretBox


def main() -> None:
    parser = argparse.ArgumentParser(description="Wakey-owned GitHub App connection service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8480)
    parser.add_argument("--data-dir", type=Path, default=Path("wakey-connect-data"))
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    def secret(name: str) -> str:
        path = os.environ.get(name + "_FILE", "")
        if not path:
            parser.error(f"{name}_FILE must point to a mounted secret")
        return Path(path).read_text().strip()

    box = SecretBox(secret("WAKEY_CONNECT_MASTER_KEY"))
    database_file = os.environ.get("WAKEY_CONNECT_DATABASE_URL_FILE", "")
    database_url = Path(database_file).read_text().strip() if database_file else ""
    if os.environ.get("K_SERVICE") and not database_url:
        parser.error("Cloud Run requires WAKEY_CONNECT_DATABASE_URL_FILE; SQLite is not durable")
    store = ConnectStore(args.data_dir / "connect.db", box, database_url=database_url)
    github = ConnectGitHub(
        os.environ["WAKEY_CONNECT_CLIENT_ID"], secret("WAKEY_CONNECT_PRIVATE_KEY")
    )
    app = create_connect_app(
        store,
        github,
        app_slug=os.environ["WAKEY_CONNECT_APP_SLUG"],
        webhook_secret=secret("WAKEY_CONNECT_WEBHOOK_SECRET"),
    )
    try:
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    finally:
        store.close()
        github.http.close()


if __name__ == "__main__":
    main()
