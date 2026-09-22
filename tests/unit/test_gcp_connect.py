# SPDX-License-Identifier: Apache-2.0
"""Deployment templates must isolate identities and keep secrets out of manifests."""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gcp_connect import Cloud, service_spec  # noqa: E402
from scripts.gcp_monitoring import ensure_uptime_check  # noqa: E402


def test_cloud_run_spec_uses_scoped_secret_mounts_and_durable_database():
    cfg = json.loads(Path("deploy/connect-gcp.json").read_text())
    spec = service_spec(cfg, "synthetic@sha256:digest")
    template = spec["template"]
    assert cfg["service"] != "wakey"
    assert template["scaling"]["maxInstanceCount"] == 2
    assert "wakey-connect-runtime" in template["serviceAccount"]
    assert len(template["volumes"]) == 5
    container = template["containers"][0]
    assert container["resources"]["limits"]["memory"] == "512Mi"
    assert container["startupProbe"]["httpGet"]["path"] == "/readyz"
    assert any(e["name"] == "WAKEY_CONNECT_DATABASE_URL_FILE" for e in container["env"])
    assert "allUsers" not in json.dumps(spec)
    for volume in template["volumes"]:
        if "secret" in volume:
            assert volume["secret"]["items"][0]["version"] == "1"


def test_provider_errors_do_not_echo_secret_response():
    cloud = Cloud.__new__(Cloud)
    cloud.http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, json={"error": "synthetic-private-key-material"})
        )
    )
    with pytest.raises(RuntimeError, match="HTTP 403") as exc:
        cloud.call("POST", "https://example.test/secret", {"value": "sensitive"})
    assert "private-key" not in str(exc.value)
    cloud.http.close()


def test_iam_binding_preserves_existing_principals_and_etag():
    seen = []

    def wire(request):
        if request.url.path.endswith(":getIamPolicy"):
            assert json.loads(request.content)["options"]["requestedPolicyVersion"] == 3
            return httpx.Response(
                200,
                json={
                    "etag": "synthetic",
                    "bindings": [
                        {"role": "roles/viewer", "members": ["user:existing@example.test"]}
                    ],
                },
            )
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={})

    cloud = Cloud.__new__(Cloud)
    cloud.http = httpx.Client(transport=httpx.MockTransport(wire))
    cloud.bind("https://example.test/resource", "serviceAccount:synthetic", "roles/viewer")
    assert seen[0]["policy"]["etag"] == "synthetic"
    assert seen[0]["policy"]["bindings"][0]["members"] == [
        "user:existing@example.test",
        "serviceAccount:synthetic",
    ]
    cloud.http.close()


def test_uptime_check_reconciles_stale_health_path() -> None:
    calls: list[tuple[str, str, object]] = []

    class FakeCloud:
        project = "wakey-ai"
        cfg = {"region": "us-central1"}

        def call(self, method, url, body=None):
            calls.append((method, url, body))
            if url.endswith("/uptimeCheckConfigs") and method == "GET":
                return {
                    "uptimeCheckConfigs": [
                        {
                            "name": "projects/wakey-ai/uptimeCheckConfigs/1",
                            "displayName": "wakey-connect-inbox-healthz",
                            "httpCheck": {"path": "/healthz"},
                            "monitoredResource": {"labels": {"host": "old.example"}},
                        }
                    ]
                }
            if "/services/wakey-connect-inbox" in url:
                return {"uri": "https://inbox.example.test"}
            return {}

    ensure_uptime_check(FakeCloud())
    patch = next(call for call in calls if call[0] == "PATCH")
    assert "updateMask=httpCheck.path,monitoredResource" in patch[1]
    assert patch[2]["httpCheck"]["path"] == "/"
    assert patch[2]["monitoredResource"]["labels"]["host"] == "inbox.example.test"
