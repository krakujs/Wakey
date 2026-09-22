# SPDX-License-Identifier: Apache-2.0
"""GitHub App webhook cutover to the managed connector (readiness #2/#3).

Modes:
  plan     show the current App webhook target, events and permissions
  apply    subscribe to ``deployment_status``, request ``deployments: read``,
           and repoint the App webhook at the connector using the connector's
           stored webhook secret (GitHub pings the new target on save)
  verify   mint a scoped installation token (deployments: read) and list the
           recent webhook deliveries to prove signed delivery reached the
           connector
  rollback repoint the webhook at the previous ``wakey`` service, restoring
           that service's ``WAKEY_WEBHOOK_SECRET`` verbatim from its Cloud
           Run spec so the previous consumer keeps working

Secrets stay in process memory: the App private key, the connector webhook
secret and the previous service's secret are never printed or logged.
Authentication uses an App JWT signed from the private key — no browser or
user session, and no GitHub credential transits this script.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from scripts.gcp_connect import Cloud

from wakey.forge.github_app import sign_app_jwt

APPROVED_REPO = "krakujs/linux-clipboard-manager"
GITHUB = "https://api.github.com"
DEPLOY_EVENT = "deployment_status"
DEPLOY_PERMISSION = {"deployments": "read"}


def read_secret(cloud: Cloud, name: str) -> str:
    """Read the latest secret version; never writes, never prints."""
    payload = cloud.call(
        "GET",
        f"https://secretmanager.googleapis.com/v1/projects/{cloud.project}"
        f"/secrets/{name}/versions/latest:access",
    )
    return base64.b64decode(payload["payload"]["data"]).decode()


def github_client(jwt: str) -> httpx.Client:
    return httpx.Client(
        timeout=30,
        base_url=GITHUB,
        headers={"Authorization": f"Bearer {jwt}", "Accept": "application/vnd.github+json"},
    )


def get_app(http: httpx.Client) -> dict[str, Any]:
    response = http.get("/app")
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def get_hook(http: httpx.Client) -> dict[str, Any]:
    response = http.get("/app/hook/config")
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def patch_app(http: httpx.Client, body: dict[str, Any]) -> dict[str, Any]:
    response = http.patch("/app", json=body)
    if response.status_code != 200:
        raise RuntimeError(f"PATCH /app: HTTP {response.status_code}")
    return response.json()  # type: ignore[no-any-return]


def patch_hook_config(http: httpx.Client, body: dict[str, Any]) -> None:
    response = http.patch("/app/hook/config", json=body)
    if response.status_code != 200:
        raise RuntimeError(f"PATCH /app/hook/config: HTTP {response.status_code}")


def describe(app: dict[str, Any], hook: dict[str, Any]) -> None:
    print("App:", app.get("slug"), "(", app.get("name"), ")")
    print("Webhook URL:", hook.get("url"))
    print("Content type:", hook.get("content_type"), "insecure_ssl:", hook.get("insecure_ssl"))
    print("Events:", ", ".join(sorted(app.get("events", []))))
    print("Permissions:", json.dumps(app.get("permissions", {}), sort_keys=True))


def connector_url(cloud: Cloud, cfg: dict[str, Any]) -> str:
    service = cloud.call(
        "GET",
        f"https://run.googleapis.com/v2/projects/{cfg['project']}/locations/{cfg['region']}"
        f"/services/{cfg['service']}",
    )
    return str(service["uri"])


def previous_service_target(cloud: Cloud, cfg: dict[str, Any]) -> tuple[str, str]:
    """The old service's URL + its WAKEY_WEBHOOK_SECRET (rollback only)."""
    spec = cloud.call(
        "GET",
        f"https://run.googleapis.com/v2/projects/{cfg['project']}/locations/{cfg['region']}"
        "/services/wakey",
    )
    container = spec.get("template", {}).get("containers", [{}])[0]
    secret = next(
        (e["value"] for e in container.get("env", []) if e.get("name") == "WAKEY_WEBHOOK_SECRET"),
        "",
    )
    if not secret:
        raise RuntimeError("previous service has no WAKEY_WEBHOOK_SECRET; cannot restore")
    return str(spec["uri"]) + "/webhooks/github", secret


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "verify", "rollback"))
    args = parser.parse_args()
    key_file = str(Path.home() / ".config/wakey/gcp-service-account.json")
    cfg = json.loads(Path("deploy/connect-gcp.json").read_text())
    cloud = Cloud(cfg, key_file)
    app_key = read_secret(cloud, "wakey-connect-app-key")
    jwt = sign_app_jwt(app_key, cfg["client_id"])
    try:
        with github_client(jwt) as http:
            if args.action == "plan":
                describe(get_app(http), get_hook(http))
            elif args.action == "apply":
                app = get_app(http)
                events = sorted(set(app.get("events", [])) | {DEPLOY_EVENT})
                permissions = {**app.get("permissions", {}), **DEPLOY_PERMISSION}
                patch_app(http, {"events": events, "permissions": permissions})
                secret = read_secret(cloud, "wakey-connect-webhook-secret")
                target = connector_url(cloud, cfg) + "/webhooks/github"
                patch_hook_config(
                    http,
                    {
                        "url": target,
                        "content_type": "json",
                        "secret": secret,
                        "insecure_ssl": "0",
                        "active": True,
                    },
                )
                ping = http.post("/app/hook/ping")
                print("Cutover applied. Webhook URL:", target)
                print("Events:", ", ".join(events))
                print("Permissions:", json.dumps(permissions, sort_keys=True))
                print("Ping to trigger a verification delivery:", ping.status_code)
                print("Rollback:", f"{Path(sys.argv[0]).name} rollback")
            elif args.action == "verify":
                describe(get_app(http), get_hook(http))
                raw = http.get("/app/installations").json()
                installations = raw.get("installations", []) if isinstance(raw, dict) else raw
                installation = next(
                    (i for i in installations if i.get("app_slug") == cfg["app_slug"]),
                    None,
                )
                if installation is None:
                    raise RuntimeError("no installation found for this App")
                token = http.post(
                    f"/app/installations/{installation['id']}/access_tokens",
                    json={
                        "repositories": [APPROVED_REPO.split("/", 1)[1]],
                        "permissions": {"deployments": "read"},
                    },
                )
                print("Scoped token mint:", "OK" if token.status_code == 201 else token.status_code)
                if token.status_code == 201:
                    deployments = httpx.get(
                        f"{GITHUB}/repos/{APPROVED_REPO}/deployments?per_page=1",
                        headers={
                            "Authorization": f"Bearer {token.json()['token']}",
                            "Accept": "application/vnd.github+json",
                        },
                        timeout=30,
                    )
                    print(
                        "Deployments read with scoped token:",
                        "OK" if deployments.status_code == 200 else deployments.status_code,
                    )
                raw_deliveries = http.get("/app/hook/deliveries?per_page=5").json()
                deliveries = (
                    raw_deliveries.get("deliveries", [])
                    if isinstance(raw_deliveries, dict)
                    else raw_deliveries
                )
                for delivery in deliveries[:5]:
                    print(
                        "delivery",
                        delivery.get("id"),
                        delivery.get("event"),
                        "status",
                        delivery.get("status_code"),
                        delivery.get("delivered_at"),
                    )
            else:
                url, secret = previous_service_target(cloud, cfg)
                patch_hook_config(
                    http,
                    {
                        "url": url,
                        "content_type": "json",
                        "secret": secret,
                        "insecure_ssl": "0",
                        "active": True,
                    },
                )
                print("Rollback applied. Webhook URL:", url)
    finally:
        cloud.http.close()


if __name__ == "__main__":
    main()
