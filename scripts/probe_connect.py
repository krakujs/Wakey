# SPDX-License-Identifier: Apache-2.0
"""Non-destructive live staging probes; tokens stay in process memory.

Creates then revokes one pending device pairing. Signed synthetic ping has no
installation or repository, so it cannot dispatch customer work. Does not change
the GitHub App webhook, issue tokens for repositories, or write to a repository.
"""

import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
from scripts.gcp_connect import Cloud


def main() -> None:
    key_file = str(Path.home() / ".config/wakey/gcp-service-account.json")
    cfg = json.loads(Path("deploy/connect-gcp.json").read_text())
    cloud = Cloud(cfg, key_file)
    try:
        service = cloud.call(
            "GET",
            f"https://run.googleapis.com/v2/projects/{cfg['project']}/locations/{cfg['region']}/services/{cfg['service']}",
        )
        url = service["uri"]
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token", "--audiences=" + url],
            env={**os.environ, "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": key_file},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Google ID-token acquisition failed; output suppressed")
        iam = {"X-Serverless-Authorization": "Bearer " + result.stdout.strip()}
        with httpx.Client(timeout=40, base_url=url) as client:
            assert client.get("/readyz").status_code in (401, 403), "staging must not be public"
            assert client.get("/readyz", headers=iam).json() == {"status": "ready"}
            assert (
                client.post(
                    "/v1/token", headers={**iam, "Authorization": "Bearer forged"}
                ).status_code
                == 403
            )
            assert client.post("/webhooks/github", headers=iam, json={}).status_code == 401
            secret = cloud.call(
                "GET",
                f"https://secretmanager.googleapis.com/v1/projects/{cfg['project']}/secrets/wakey-connect-webhook-secret/versions/1:access",
            )
            webhook_secret = base64.b64decode(secret["payload"]["data"])
            body = b'{"zen":"synthetic staging probe"}'
            headers = {
                **iam,
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": "staging-probe-" + str(time.time_ns()),
                "X-Hub-Signature-256": "sha256="
                + hmac.new(webhook_secret, body, hashlib.sha256).hexdigest(),
            }
            assert client.post("/webhooks/github", headers=headers, content=body).status_code == 202
            pairing = client.post("/v1/pair", headers=iam)
            assert pairing.status_code == 200, "device flow failed (response withheld)"
            credential = pairing.json()["credential"]
            bound = {**iam, "Authorization": "Bearer " + credential}
            try:
                assert client.post("/v1/poll", headers=bound).json()["state"] == "pending"
                assert client.post("/v1/token", headers=bound).status_code == 403
                assert (
                    client.post("/v1/select", headers=bound, json={"repository_id": 1}).status_code
                    == 403
                )
            finally:
                assert client.post("/v1/disconnect", headers=bound).status_code == 200
            assert client.post("/v1/poll", headers=bound).status_code == 403
        print(
            "PASS: private ingress, database readiness, forged credential/signature rejection, "
            "signed ping, live GitHub device start, selection guard and revocation."
        )
        print(
            "Not tested: human device authorization, repo token issuance, repository writes, "
            "multi-account isolation or backup restore."
        )
    finally:
        cloud.http.close()


if __name__ == "__main__":
    main()
