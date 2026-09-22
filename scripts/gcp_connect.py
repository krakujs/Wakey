# SPDX-License-Identifier: Apache-2.0
"""Additive Connect provisioning; secret values never enter argv, output or source.

Run as `python -m scripts.gcp_connect prepare --app-key /protected/app.pem`.
Then `... deploy --image IMAGE@sha256:DIGEST` creates an IAM-restricted service.
No existing Wakey service, key or policy binding is removed. No automatic public
cutover: live staging acceptance and webhook routing review must happen first.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from scripts.gcp_deploy import access_token, load_key

from wakey.forge.github_app import sign_app_jwt


class Cloud:
    def __init__(self, config: dict[str, Any], key_file: str) -> None:
        self.cfg = config
        self.project = config["project"]
        account = load_key(key_file)
        if account["project_id"] != self.project:
            raise ValueError("credential project differs from deployment target")
        self.http = httpx.Client(
            timeout=60, headers={"Authorization": "Bearer " + access_token(account)}
        )

    def call(self, method: str, url: str, body: Any = None, *, optional: bool = False) -> Any:
        response = self.http.request(method, url, json=body)
        if optional and response.status_code == 404:
            return None
        if response.is_error:
            # Never dump provider bodies: they can echo credential-bearing input.
            raise RuntimeError(
                f"{method} {url.split('?', maxsplit=1)[0]}: HTTP {response.status_code}"
            )
        return response.json() if response.content else {}

    def wait(self, operation: dict[str, Any], base: str) -> None:
        deadline = time.monotonic() + 1200
        while not operation.get("done") and operation.get("status") != "DONE":
            if time.monotonic() > deadline:
                raise RuntimeError("Cloud operation timed out; inspect before retrying")
            time.sleep(3)
            operation = self.call("GET", base + operation["name"])
        if operation.get("error"):
            raise RuntimeError("Cloud operation failed; inspect operation (payload suppressed)")

    def enable(self, service: str) -> None:
        operation = self.call(
            "POST",
            f"https://serviceusage.googleapis.com/v1/projects/{self.project}/services/{service}:enable",
            {},
        )
        self.wait(operation, "https://serviceusage.googleapis.com/v1/")
        print("API ready:", service, flush=True)

    def bind(self, url: str, member: str, role: str, *, get_method: str = "POST") -> None:
        # Version 1 can strip conditional bindings into unusable role aliases.
        if get_method == "GET":
            policy = self.call("GET", url + ":getIamPolicy?options.requestedPolicyVersion=3")
        else:
            policy = self.call(
                "POST", url + ":getIamPolicy", {"options": {"requestedPolicyVersion": 3}}
            )
        bindings = policy.setdefault("bindings", [])
        binding = next((b for b in bindings if b["role"] == role and not b.get("condition")), None)
        if binding is None:
            binding = {"role": role, "members": []}
            bindings.append(binding)
        if member not in binding["members"]:
            binding["members"].append(member)
            self.call("POST", url + ":setIamPolicy", {"policy": policy})

    def secret(self, name: str, supplier: Callable[[], str]) -> str:
        url = f"https://secretmanager.googleapis.com/v1/projects/{self.project}/secrets/{name}"
        if self.call("GET", url, optional=True) is None:
            self.call(
                "POST",
                url.rsplit("/", 1)[0] + "?secretId=" + name,
                {"replication": {"automatic": {}}, "labels": {"app": "wakey-connect"}},
            )
        versions = self.call("GET", url + "/versions?filter=state:ENABLED&pageSize=1")
        if not versions.get("versions"):
            self.call(
                "POST",
                url + ":addVersion",
                {"payload": {"data": base64.b64encode(supplier().encode()).decode()}},
            )
        payload = self.call("GET", url + "/versions/latest:access")
        return base64.b64decode(payload["payload"]["data"]).decode()

    def prepare(self, app_key: Path) -> None:
        cfg, project = self.cfg, self.project
        pem = app_key.read_text()
        jwt = sign_app_jwt(pem, cfg["client_id"])
        with httpx.Client(timeout=20) as github:
            response = github.get(
                "https://api.github.com/app", headers={"Authorization": "Bearer " + jwt}
            )
            if response.status_code != 200 or response.json().get("slug") != cfg["app_slug"]:
                raise ValueError("App key/client ID/slug verification failed")
        for service in (
            "cloudresourcemanager.googleapis.com",
            "iam.googleapis.com",
            "sqladmin.googleapis.com",
            "monitoring.googleapis.com",
        ):
            self.enable(service)
        email = f"{cfg['runtime_account']}@{project}.iam.gserviceaccount.com"
        iam = f"https://iam.googleapis.com/v1/projects/{project}/serviceAccounts"
        if self.call("GET", iam + "/" + email, optional=True) is None:
            self.call(
                "POST",
                iam,
                {
                    "accountId": cfg["runtime_account"],
                    "serviceAccount": {"displayName": "Wakey Connect runtime"},
                },
            )
        member = "serviceAccount:" + email
        self.bind(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}",
            member,
            "roles/cloudsql.client",
        )
        password = self.secret("wakey-connect-db-password", lambda: secrets.token_urlsafe(36))
        self.secret("wakey-connect-master-key", lambda: secrets.token_hex(32))
        saved_pem = self.secret("wakey-connect-app-key", lambda: pem)
        if saved_pem.strip() != pem.strip():
            raise ValueError("Existing App key differs; explicit rotation required")
        self.secret("wakey-connect-webhook-secret", lambda: secrets.token_urlsafe(48))
        sql_base = f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{project}"
        instance_url = sql_base + "/instances/" + cfg["database_instance"]
        instance = self.call("GET", instance_url, optional=True)
        if instance is None:
            operation = self.call(
                "POST",
                sql_base + "/instances",
                {
                    "name": cfg["database_instance"],
                    "region": cfg["region"],
                    "databaseVersion": "POSTGRES_16",
                    "settings": {
                        "tier": cfg["sql_tier"],
                        "edition": "ENTERPRISE",
                        "availabilityType": cfg["sql_availability"],
                        "dataDiskSizeGb": "10",
                        "dataDiskType": "PD_SSD",
                        "storageAutoResize": True,
                        "storageAutoResizeLimit": "50",
                        "deletionProtectionEnabled": True,
                        "backupConfiguration": {
                            "enabled": True,
                            "pointInTimeRecoveryEnabled": True,
                            "startTime": "03:00",
                            "transactionLogRetentionDays": 7,
                        },
                        "ipConfiguration": {
                            "ipv4Enabled": True,
                            "authorizedNetworks": [],
                            "requireSsl": True,
                        },
                        "userLabels": {"app": "wakey-connect"},
                    },
                },
            )
            print(
                "Creating protected regional PostgreSQL instance; this can take several minutes.",
                flush=True,
            )
            self.wait(operation, sql_base + "/operations/")
        elif instance.get("state") != "RUNNABLE":
            raise RuntimeError("Database exists but is not ready; wait before resuming")
        users = self.call("GET", instance_url + "/users").get("items", [])
        if not any(u["name"] == "wakey_connect" for u in users):
            self.wait(
                self.call(
                    "POST", instance_url + "/users", {"name": "wakey_connect", "password": password}
                ),
                sql_base + "/operations/",
            )
        if self.call("GET", instance_url + "/databases/" + cfg["database"], optional=True) is None:
            self.wait(
                self.call("POST", instance_url + "/databases", {"name": cfg["database"]}),
                sql_base + "/operations/",
            )
        socket = f"/cloudsql/{project}:{cfg['region']}:{cfg['database_instance']}"
        self.secret(
            "wakey-connect-database-url",
            lambda: (
                f"postgresql://wakey_connect:{quote(password, safe='')}@/{cfg['database']}"
                f"?host={quote(socket, safe='')}"
            ),
        )
        for name in ("master-key", "app-key", "webhook-secret", "database-url"):
            self.bind(
                f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/wakey-connect-{name}",
                member,
                "roles/secretmanager.secretAccessor",
                get_method="GET",
            )
        print(
            "Connector database, secrets and runtime identity ready; existing Wakey untouched.",
            flush=True,
        )

    def deploy(self, image: str) -> None:
        if "@sha256:" not in image or not image.startswith(
            f"{self.cfg['region']}-docker.pkg.dev/{self.project}/"
        ):
            raise ValueError(
                "Deploy requires a digest-pinned image in the target project's registry"
            )
        parent = f"https://run.googleapis.com/v2/projects/{self.project}/locations/{self.cfg['region']}/services"
        body = service_spec(self.cfg, image)
        existing = self.call("GET", parent + "/" + self.cfg["service"], optional=True)
        if existing is not None:
            raise ValueError("Service already exists; review the revision before updating")
        operation = self.call("POST", parent + "?serviceId=" + self.cfg["service"], body)
        self.wait(operation, "https://run.googleapis.com/v2/")
        service = self.call("GET", parent + "/" + self.cfg["service"])
        print("IAM-restricted connector deployed:", service.get("uri"), flush=True)


def service_spec(cfg: dict[str, Any], image: str) -> dict[str, Any]:
    volumes = [
        {
            "name": "cloudsql",
            "cloudSqlInstance": {
                "instances": [f"{cfg['project']}:{cfg['region']}:{cfg['database_instance']}"]
            },
        }
    ]
    mounts = [{"name": "cloudsql", "mountPath": "/cloudsql"}]
    env = [
        {"name": "WAKEY_CONNECT_CLIENT_ID", "value": cfg["client_id"]},
        {"name": "WAKEY_CONNECT_APP_SLUG", "value": cfg["app_slug"]},
    ]
    for variable, suffix in (
        ("MASTER_KEY", "master-key"),
        ("PRIVATE_KEY", "app-key"),
        ("WEBHOOK_SECRET", "webhook-secret"),
        ("DATABASE_URL", "database-url"),
    ):
        volumes.append(
            {
                "name": suffix,
                "secret": {
                    "secret": "wakey-connect-" + suffix,
                    "items": [{"version": "1", "path": "value"}],
                },
            }
        )
        mounts.append({"name": suffix, "mountPath": "/run/secrets/" + suffix})
        env.append(
            {
                "name": "WAKEY_CONNECT_" + variable + "_FILE",
                "value": "/run/secrets/" + suffix + "/value",
            }
        )
    return {
        "labels": {"app": "wakey-connect", "stage": "prelaunch"},
        "ingress": "INGRESS_TRAFFIC_ALL",
        "template": {
            "serviceAccount": f"{cfg['runtime_account']}@{cfg['project']}.iam.gserviceaccount.com",
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": cfg["max_instances"]},
            "maxInstanceRequestConcurrency": 8,
            "timeout": "60s",
            "volumes": volumes,
            "containers": [
                {
                    "image": image,
                    "command": ["wakey-connect"],
                    "args": ["--host", "0.0.0.0", "--port", "8080", "--data-dir", "/tmp/connect"],
                    "ports": [{"containerPort": 8080}],
                    "env": env,
                    "volumeMounts": mounts,
                    "resources": {"limits": {"cpu": "1", "memory": "512Mi"}},
                    "startupProbe": {
                        "httpGet": {"path": "/readyz", "port": 8080},
                        "periodSeconds": 10,
                        "failureThreshold": 12,
                    },
                    "livenessProbe": {
                        "httpGet": {"path": "/healthz", "port": 8080},
                        "periodSeconds": 30,
                    },
                }
            ],
        },
    }


def inbox_service_spec(cfg: dict[str, Any], image: str, broker_url: str) -> dict[str, Any]:
    return {
        "labels": {"app": "wakey-connect", "stage": "prelaunch", "role": "inbox"},
        "ingress": "INGRESS_TRAFFIC_ALL",
        "template": {
            "serviceAccount": f"{cfg['runtime_account']}@{cfg['project']}.iam.gserviceaccount.com",
            "scaling": {"minInstanceCount": 1, "maxInstanceCount": 3},
            "maxInstanceRequestConcurrency": 16,
            "timeout": "30s",
            "containers": [
                {
                    "image": image,
                    "command": ["wakey-connect-inbox"],
                    "args": ["--host", "0.0.0.0", "--port", "8080"],
                    "ports": [{"containerPort": 8080}],
                    "env": [
                        {"name": "WAKEY_CONNECT_BROKER_URL", "value": broker_url},
                        {
                            "name": "WAKEY_CONNECT_WEBHOOK_SECRET_FILE",
                            "value": "/run/secrets/webhook-secret/value",
                        },
                    ],
                    "volumeMounts": [
                        {"name": "webhook-secret", "mountPath": "/run/secrets/webhook-secret"}
                    ],
                    "resources": {"limits": {"cpu": "1", "memory": "512Mi"}},
                    "startupProbe": {
                        "httpGet": {"path": "/healthz", "port": 8080},
                        "periodSeconds": 10,
                        "failureThreshold": 12,
                    },
                    "livenessProbe": {
                        "httpGet": {"path": "/healthz", "port": 8080},
                        "periodSeconds": 30,
                    },
                }
            ],
            "volumes": [
                {
                    "name": "webhook-secret",
                    "secret": {
                        "secret": "wakey-connect-webhook-secret",
                        "items": [{"version": "1", "path": "value"}],
                    },
                }
            ],
        },
    }


def deploy_inbox(cloud: Cloud, image: str) -> str:
    """Deploy (or update) the public inbox; returns its URL."""
    cfg = cloud.cfg
    run_base = (
        f"https://run.googleapis.com/v2/projects/{cloud.project}/locations/{cfg['region']}/services"
    )
    if "@sha256:" not in image or not image.startswith(
        f"{cfg['region']}-docker.pkg.dev/{cloud.project}/"
    ):
        raise ValueError("Inbox deploy requires a digest-pinned image in the project registry")
    broker = cloud.call("GET", run_base + "/" + cfg["service"])
    spec = inbox_service_spec(cfg, image, str(broker["uri"]))
    existing = cloud.call("GET", run_base + "/wakey-connect-inbox", optional=True)
    if existing is None:
        operation = cloud.call("POST", run_base + "?serviceId=wakey-connect-inbox", spec)
        cloud.wait(operation, "https://run.googleapis.com/v2/")
    else:
        spec["template"]["containers"][0]["image"] = image
        operation = cloud.call(
            "PATCH",
            run_base + "/wakey-connect-inbox",
            spec,
        )
        cloud.wait(operation, "https://run.googleapis.com/v2/")
    # The inbox alone is public; GitHub cannot present Google credentials.
    policy = cloud.call(
        "GET",
        run_base + "/wakey-connect-inbox:getIamPolicy?options.requestedPolicyVersion=3",
    )
    bindings = policy.setdefault("bindings", [])
    if not any(
        b.get("role") == "roles/run.invoker" and "allUsers" in b.get("members", [])
        for b in bindings
    ):
        bindings.append({"role": "roles/run.invoker", "members": ["allUsers"]})
        cloud.call("POST", run_base + "/wakey-connect-inbox:setIamPolicy", {"policy": policy})
    # The inbox's runtime identity needs to invoke the IAM-restricted broker.
    cloud.bind(
        run_base + "/" + cfg["service"],
        f"serviceAccount:{cfg['runtime_account']}@{cfg['project']}.iam.gserviceaccount.com",
        "roles/run.invoker",
        get_method="GET",
    )
    service = cloud.call("GET", run_base + "/wakey-connect-inbox")
    return str(service["uri"])


def redeploy_broker(cloud: Cloud, image: str) -> None:
    """Update the existing broker to a digest-pinned image; one warm instance."""
    cfg = cloud.cfg
    run_base = (
        f"https://run.googleapis.com/v2/projects/{cloud.project}/locations/{cfg['region']}/services"
    )
    if "@sha256:" not in image or not image.startswith(
        f"{cfg['region']}-docker.pkg.dev/{cloud.project}/"
    ):
        raise ValueError("Redeploy requires a digest-pinned image in the project registry")
    spec = service_spec(cfg, image)
    spec["template"]["scaling"]["minInstanceCount"] = 1
    spec["template"]["containers"][0]["image"] = image
    operation = cloud.call("PATCH", run_base + "/" + cfg["service"], spec)
    cloud.wait(operation, "https://run.googleapis.com/v2/")
    service = cloud.call("GET", run_base + "/" + cfg["service"])
    print("Broker redeployed:", service.get("uri"), service.get("latestReadyRevision"), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "deploy", "inbox", "redeploy"])
    parser.add_argument("--config", type=Path, default=Path("deploy/connect-gcp.json"))
    parser.add_argument(
        "--key-file", default=str(Path.home() / ".config/wakey/gcp-service-account.json")
    )
    parser.add_argument("--app-key", type=Path)
    parser.add_argument("--image", default="")
    args = parser.parse_args()
    cloud = Cloud(json.loads(args.config.read_text()), args.key_file)
    try:
        if args.action == "prepare":
            if not args.app_key:
                parser.error("prepare requires --app-key")
            cloud.prepare(args.app_key)
        elif args.action == "inbox":
            print("Public inbox deployed:", deploy_inbox(cloud, args.image), flush=True)
        elif args.action == "redeploy":
            redeploy_broker(cloud, args.image)
        else:
            cloud.deploy(args.image)
    finally:
        cloud.http.close()


if __name__ == "__main__":
    main()
