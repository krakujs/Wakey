#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Repeatable GCP deployment for Wakey — no gcloud CLI required.

Authenticates with a service-account key (JSON) via a JWT-bearer
exchange, then drives the GCP REST APIs to prepare and update a
production Cloud Run deployment:

    scripts/gcp_deploy.py all     --database-url postgresql://...
    scripts/gcp_deploy.py image   --image-tag v0.1.0
    scripts/gcp_deploy.py service --database-url postgresql://...
    scripts/gcp_deploy.py pubsub  --ingest-key wk_...

Requirements:
- A service-account key JSON (default ~/.config/wakey/gcp-service-account.json)
  with Cloud Run / Artifact Registry / Service Usage / Pub/Sub admin on
  the project.
- Docker running locally for image build + push.
- The target Postgres (Cloud SQL or self-hosted) reachable via
  ``--database-url`` for durable storage (OPS-5).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key

REPO_ROOT = Path(__file__).resolve().parent.parent
REGION_DEFAULT = "us-central1"
AR_REPO = "wakey"
SERVICE_NAME = "wakey"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def load_key(path: str) -> dict[str, str]:
    key_data: dict[str, str] = json.loads(Path(path).read_text())
    return key_data


def access_token(sa: dict[str, str]) -> str:
    """JWT-bearer exchange — the SA private key is the credential."""
    private_key = load_pem_private_key(sa["private_key"].encode(), password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise SystemExit("service account key is not an RSA private key")

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    now = int(time.time())
    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64(
        json.dumps(
            {
                "iss": sa["client_email"],
                "scope": " ".join(SCOPES),
                "aud": sa["token_uri"],
                "iat": now,
                "exp": now + 3600,
            }
        ).encode()
    )
    signature = b64(
        private_key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    )
    assertion = f"{header}.{claims}.{signature}"
    data = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }
    ).encode()
    with urllib.request.urlopen(
        urllib.request.Request(str(sa["token_uri"]), data=data), timeout=15
    ) as response:
        return str(json.loads(response.read())["access_token"])


def api(
    token: str, method: str, url: str, body: dict[str, object] | None = None
) -> dict[str, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result: dict[str, object] = json.loads(response.read() or b"{}")
            return result
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        raise SystemExit(f"GCP API {method} {url} failed: HTTP {exc.code} — {detail}") from exc


def lro_wait(token: str, operation: dict[str, object], what: str) -> dict[str, object]:
    name = str(operation.get("name", ""))
    for _ in range(80):
        time.sleep(3)
        if not name:
            return operation
        op = api(token, "GET", name)
        if op.get("done"):
            if "error" in op:
                raise SystemExit(f"{what} failed: {op['error']}")
            response = op.get("response")
            return response if isinstance(response, dict) else operation
    raise SystemExit(f"{what} timed out")


def enable_apis(token: str, project: str, apis: list[str]) -> None:
    for api_name in apis:
        api(
            token,
            "POST",
            f"https://serviceusage.googleapis.com/v1/projects/{project}/services/{api_name}:enable",
            {},
        )
        print(f"  enabled/verified {api_name}")


def ensure_ar_repo(token: str, project: str, region: str, *, name: str) -> None:
    url = (
        f"https://artifactregistry.googleapis.com/v1/projects/{project}/"
        f"locations/{region}/repositories/{name}"
    )
    existing = api(token, "GET", url)
    if existing.get("name"):
        print(f"  artifact registry {region}/{name}: exists")
        return
    api(
        token,
        "POST",
        f"https://artifactregistry.googleapis.com/v1/projects/{project}/"
        f"locations/{region}/repositories?repositoryId={name}",
        {"format": "DOCKER"},
    )
    print(f"  artifact registry {region}/{name}: created")


def docker_push(*, project: str, region: str, repo: str, tag: str, token: str) -> str:
    image = f"{region}-docker.pkg.dev/{project}/{repo}/wakey:{tag}"
    build = subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", image, "."],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise SystemExit(f"docker build failed:\n{build.stderr[-500:]}")
    auth = base64.b64encode(f"oauth2accesstoken:{token}".encode()).decode()
    cfg_dir = Path("/tmp/wakey-docker-cfg")
    cfg_dir.mkdir(exist_ok=True)
    cfg = cfg_dir / "config.json"
    cfg.write_text(json.dumps({"auths": {f"{region}-docker.pkg.dev": {"auth": auth}}}))
    cfg.chmod(0o600)
    push = subprocess.run(
        ["docker", "--config", str(cfg_dir), "push", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if push.returncode != 0:
        raise SystemExit(f"docker push failed:\n{push.stderr[-500:]}")
    print(f"  pushed {image}")
    return image


def ensure_service(
    token: str,
    project: str,
    region: str,
    name: str,
    image: str,
    *,
    env_pairs: dict[str, str],
) -> str:
    # Cloud Run routes to containerPort 8080 and startup-probes it; the
    # app must bind that port or instances fail readiness and churn.
    env_pairs = {**env_pairs, "WAKEY_PORT": "8080"}
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/services/{name}"
    template = {
        "timeout": "300s",
        "scaling": {"minInstanceCount": 1},
        "containers": [
            {
                "image": image,
                "ports": [{"containerPort": 8080}],
                "env": [{"name": k, "value": v} for k, v in env_pairs.items()],
                "command": ["python", "-m", "wakey"],
                "args": ["serve", "--host", "0.0.0.0"],
            }
        ],
    }
    existing = api(token, "GET", url)
    if existing.get("name"):
        api(token, "PATCH", url, {"template": template})
    else:
        api(token, "POST", f"{url}?serviceId={name}", {"template": template})
    op = api(token, "GET", url)
    for _ in range(60):
        conditions = op.get("conditions")
        if isinstance(conditions, list):
            ready = next(
                (
                    str(c["status"])
                    for c in conditions
                    if isinstance(c, dict) and c.get("type") == "Ready"
                ),
                None,
            )
            if ready == "True":
                break
        time.sleep(4)
        op = api(token, "GET", url)
    api(
        token,
        "POST",
        f"{url}:setIamPolicy",
        {"policy": {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}},
    )
    print(f"  cloud run service {name}: ready at {op.get('uri')}")
    return str(op.get("uri") or "")


def ensure_pubsub_push(
    token: str,
    project: str,
    *,
    topic: str,
    subscription: str,
    endpoint: str,
    service_account: str,
    ingest_key: str,
) -> None:
    """GCP Cloud Logging → wakey push subscription (WF-02 §3, E4-T4)."""
    api(token, "PUT", f"https://pubsub.googleapis.com/v1/projects/{project}/topics/{topic}", {})
    api(
        token,
        "PUT",
        f"https://pubsub.googleapis.com/v1/projects/{project}/subscriptions/{subscription}",
        {
            "topic": f"projects/{project}/topics/{topic}",
            "pushConfig": {
                "pushEndpoint": f"{endpoint.rstrip('/')}/ingest/gcp/{ingest_key}",
                "oidcToken": {
                    "serviceAccountEmail": service_account,
                    "audience": endpoint.rstrip("/"),
                },
            },
        },
    )
    print(f"  pub/sub push subscription {subscription} → {endpoint}")


def load_env_pairs(
    path: Path, database_url: str, environ: Mapping[str, str] = os.environ
) -> dict[str, str]:
    # start from the process environment so CI-exported WAKEY_* secrets
    # flow through, then overlay the optional .env file
    pairs = {k: v for k, v in environ.items() if k.startswith("WAKEY_")}
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        pairs[key.strip()] = value.strip().strip('"')
    if database_url:
        pairs["WAKEY_DATABASE_URL"] = database_url
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy Wakey to GCP (REST-only)")
    parser.add_argument("command", choices=["all", "image", "service", "pubsub"])
    parser.add_argument("--project", default=os.environ.get("GOOGLE_PROJECT", "wakey-ai"))
    parser.add_argument("--region", default=REGION_DEFAULT)
    parser.add_argument(
        "--key-file",
        default=os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS",
            str(Path.home() / ".config/wakey/gcp-service-account.json"),
        ),
    )
    parser.add_argument("--image-tag", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument(
        "--ingest-key",
        default=os.environ.get("WAKEY_INGEST_KEY", ""),
        help="ingest key for the GCP push route",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("WAKEY_DATABASE_URL", ""),
        help="Postgres DSN for durable storage (OPS-5)",
    )
    args = parser.parse_args()

    sa = load_key(args.key_file)
    token = access_token(sa)
    print("authenticated as", sa["client_email"])

    if args.command in ("all", "image"):
        enable_apis(
            token,
            args.project,
            [
                "run.googleapis.com",
                "artifactregistry.googleapis.com",
                "serviceusage.googleapis.com",
            ],
        )
        ensure_ar_repo(token, args.project, args.region, name=AR_REPO)
        docker_push(
            project=args.project, region=args.region, repo=AR_REPO, tag=args.image_tag, token=token
        )
    if args.command in ("all", "service"):
        enable_apis(token, args.project, ["run.googleapis.com"])
        image = f"{args.region}-docker.pkg.dev/{args.project}/wakey/wakey:{args.image_tag}"
        token = access_token(sa)  # refresh
        url = ensure_service(
            token=token,
            project=args.project,
            region=args.region,
            name=SERVICE_NAME,
            image=image,
            env_pairs=load_env_pairs(REPO_ROOT / ".env", args.database_url),
        )
        print("service url:", url)
    if args.command == "pubsub":
        if not args.ingest_key:
            raise SystemExit("--ingest-key required for the GCP push route")
        enable_apis(token, args.project, ["pubsub.googleapis.com"])
        ensure_pubsub_push(
            token=token,
            project=args.project,
            topic="wakey-errors",
            subscription="wakey-errors-push",
            endpoint="https://wakey.example.com",
            service_account=str(sa["client_email"]),
            ingest_key=args.ingest_key,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
