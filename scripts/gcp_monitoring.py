# SPDX-License-Identifier: Apache-2.0
"""Launch monitoring for the managed connector (readiness #7): idempotent.

Creates, if absent: one email notification channel, an uptime check on the
public inbox health endpoint, 5xx alert policies for the connector services,
and a billing budget with threshold alerts. Values are printed; nothing is
deleted. Run as `python -m scripts.gcp_monitoring --email OWNER@example.com`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.gcp_connect import Cloud

MON = "https://monitoring.googleapis.com/v3"
SERVICES = ("wakey-connect", "wakey-connect-inbox")


def ensure_notification_channel(cloud: Cloud, email: str) -> str:
    existing = cloud.call("GET", f"{MON}/projects/{cloud.project}/notificationChannels")
    for channel in existing.get("notificationChannels", []):
        if (
            channel.get("type") == "email"
            and channel.get("labels", {}).get("email_address") == email
        ):
            print("Notification channel exists:", channel["name"])
            return str(channel["name"])
    channel = cloud.call(
        "POST",
        f"{MON}/projects/{cloud.project}/notificationChannels",
        {
            "type": "email",
            "labels": {"email_address": email},
            "displayName": "wakey-launch-alerts",
        },
    )
    print("Notification channel created:", channel["name"])
    return str(channel["name"])


def ensure_uptime_check(cloud: Cloud) -> None:
    existing = cloud.call("GET", f"{MON}/projects/{cloud.project}/uptimeCheckConfigs")
    inbox = cloud.call(
        "GET",
        f"https://run.googleapis.com/v2/projects/{cloud.project}/locations/{cfg_region(cloud)}/services/wakey-connect-inbox",
    )
    host = urlparse(str(inbox["uri"])).hostname
    desired = {
        "displayName": "wakey-connect-inbox-healthz",
        # Cloud Run currently returns an edge 404 for /healthz on the public
        # inbox hostname. The root route is the same information-free liveness
        # handler and avoids that provider-path discrepancy.
        "httpCheck": {"path": "/", "port": 443, "useSsl": True, "validateSsl": True},
        "monitoredResource": {
            "type": "uptime_url",
            "labels": {"host": str(host), "project_id": cloud.project},
        },
        "period": "300s",
        "timeout": "10s",
        "contentMatchers": [{"content": "ok", "matcher": "CONTAINS_STRING"}],
        "selectedRegions": ["USA", "EUROPE", "ASIA_PACIFIC"],
    }
    current = next(
        (
            check
            for check in existing.get("uptimeCheckConfigs", [])
            if check.get("displayName") == desired["displayName"]
        ),
        None,
    )
    if current is not None:
        if (
            current.get("httpCheck", {}).get("path") == "/"
            and current.get("monitoredResource", {}).get("labels", {}).get("host") == host
        ):
            print("Uptime check exists:", current["name"])
            return
        desired["name"] = current["name"]
        cloud.call(
            "PATCH",
            # Field masks cannot address a key inside monitoredResource.labels;
            # update that typed resource as a whole while patching the path.
            f"{MON}/{current['name']}?updateMask=httpCheck.path,monitoredResource",
            desired,
        )
        print("Uptime check updated for", host)
        return
    cloud.call("POST", f"{MON}/projects/{cloud.project}/uptimeCheckConfigs", desired)
    print("Uptime check created for", host)


def cfg_region(cloud: Cloud) -> str:
    return str(cloud.cfg["region"])


def ensure_service_alert(cloud: Cloud, service: str, channel: str) -> None:
    existing = cloud.call("GET", f"{MON}/projects/{cloud.project}/alertPolicies")
    for policy in existing.get("alertPolicies", []):
        if policy.get("displayName") == f"wakey-{service}-5xx":
            print("Alert policy exists:", policy["name"])
            return
    flt = (
        'metric.type="run.googleapis.com/request_count" '
        'AND metric.label.response_code_class="500" '
        'AND resource.type="cloud_run_revision" '
        f'AND resource.label.service_name="{service}"'
    )
    policy: dict[str, Any] = {
        "displayName": f"wakey-{service}-5xx",
        "documentation": {
            "content": f"5xx responses on the {service} Cloud Run service. Investigate logs.",
            "mimeType": "text/markdown",
        },
        "combiner": "OR",
        "conditions": [
            {
                "displayName": "5xx rate above 1/5min",
                "conditionThreshold": {
                    "filter": flt,
                    "comparison": "COMPARISON_GT",
                    "thresholdValue": 1,
                    "duration": {"seconds": 0},
                    "trigger": {"count": 1},
                    "aggregations": [
                        {
                            "alignmentPeriod": {"seconds": 300},
                            "perSeriesAligner": "ALIGN_RATE",
                            "crossSeriesReducer": "REDUCE_SUM",
                        }
                    ],
                },
            }
        ],
        "notificationChannels": [channel],
        "userLabels": {"app": "wakey-connect"},
    }
    created = cloud.call("POST", f"{MON}/projects/{cloud.project}/alertPolicies", policy)
    print("Alert policy created:", created["name"])


def ensure_budget(cloud: Cloud) -> None:
    info = cloud.call(
        "GET", f"https://cloudbilling.googleapis.com/v1/projects/{cloud.project}/billingInfo"
    )
    account = str(info.get("billingAccountName", "")).rsplit("/", 1)[-1]
    if not account:
        print("No billing account linked; budget skipped (link billing first)")
        return
    budgets = cloud.call(
        "GET", f"https://billingbudgets.googleapis.com/v1/billingAccounts/{account}/budgets"
    )
    for budget in budgets.get("budgets", []):
        if budget.get("displayName") == "wakey-ai-monthly-budget":
            print("Budget exists:", budget["name"])
            return
    cloud.call(
        "POST",
        f"https://billingbudgets.googleapis.com/v1/billingAccounts/{account}/budgets",
        {
            "budget": {
                "displayName": "wakey-ai-monthly-budget",
                "budgetFilter": {
                    "projects": [f"billingProjects/{cloud.project}"],
                    "calendarPeriod": "MONTH",
                },
                "amount": {"specifiedAmount": {"currencyCode": "USD", "units": "100"}},
                "thresholdRules": [
                    {"thresholdPercent": 0.5},
                    {"thresholdPercent": 0.9},
                    {"thresholdPercent": 1.0},
                ],
            }
        },
    )
    print("Budget created ($100/month with 50/90/100% alerts) on account", account)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="alert recipient (operator on-call)")
    parser.add_argument("--config", type=Path, default=Path("deploy/connect-gcp.json"))
    parser.add_argument(
        "--key-file", default=str(Path.home() / ".config/wakey/gcp-service-account.json")
    )
    args = parser.parse_args()
    cloud = Cloud(json.loads(args.config.read_text()), args.key_file)
    try:
        channel = ensure_notification_channel(cloud, args.email)
        ensure_uptime_check(cloud)
        for service in SERVICES:
            ensure_service_alert(cloud, service, channel)
        ensure_budget(cloud)
    finally:
        cloud.http.close()


if __name__ == "__main__":
    main()
