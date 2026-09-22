# SPDX-License-Identifier: Apache-2.0
"""Human-authorized staging check; local deployment credentials stay encrypted.

Only the explicitly approved test repository can be selected by this operator
helper. Ordinary customer onboarding remains the dashboard, not this script.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path

import httpx
from scripts.gcp_connect import Cloud

from wakey.core.storage import SQLiteStorage
from wakey.forge.connect_client import ConnectClient
from wakey.security.bootstrap import persistent_master_key
from wakey.security.secretbox import SecretBox

APPROVED_REPO = "krakujs/linux-clipboard-manager"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "status", "select", "disconnect"))
    args = parser.parse_args()
    key_file = str(Path.home() / ".config/wakey/gcp-service-account.json")
    cfg = json.loads(Path("deploy/connect-gcp.json").read_text())
    cloud = Cloud(cfg, key_file)
    try:
        service = cloud.call(
            "GET",
            f"https://run.googleapis.com/v2/projects/{cfg['project']}/locations/{cfg['region']}/services/{cfg['service']}",
        )
    finally:
        cloud.http.close()
    url = service["uri"]
    token = subprocess.run(
        ["gcloud", "auth", "print-identity-token", "--audiences=" + url],
        env={**os.environ, "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": key_file},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if token.returncode:
        raise SystemExit("Could not authenticate staging operator")
    directory = Path.home() / ".config/wakey/connect-staging-check"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    box = SecretBox(persistent_master_key(directory))
    storage = SQLiteStorage(directory / "state.db", secret_box=box)
    try:
        with httpx.Client(
            timeout=40, headers={"X-Serverless-Authorization": "Bearer " + token.stdout.strip()}
        ) as http:
            client = ConnectClient(url, storage, box, http=http)
            if args.action == "begin":
                result = client.begin()
                print("Authorize only this staging test at https://github.com/login/device")
                print("User code:", result["user_code"])
                print("Expires in seconds:", result["expires_in"])
                print("Install the App on the approved test repository first:", APPROVED_REPO)
            elif args.action == "status":
                state = client.poll()
                print("Pairing state:", state["state"])
                print(
                    "Approved repository available:",
                    any(r["name"] == APPROVED_REPO for r in state.get("repositories", [])),
                )
            elif args.action == "select":
                state = client.poll()
                selected = next(
                    (r for r in state.get("repositories", []) if r["name"] == APPROVED_REPO), None
                )
                if selected is None:
                    raise SystemExit("Approved repository not authorized; no selection performed")
                client.select(selected["id"])
                print("Selected approved test repository. Token issuance:", bool(client.token()))
            else:
                client.disconnect()
                print("Staging test connection revoked")
    finally:
        storage.close()


if __name__ == "__main__":
    main()
