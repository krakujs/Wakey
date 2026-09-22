# SPDX-License-Identifier: Apache-2.0
"""Outbound-only Connect client and immediately activated single-repo adapter."""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from wakey.core.models import Delivery, Fingerprint
from wakey.core.storage import Storage
from wakey.forge.github import ForgeError, GitHubAdapter, GitHubConfig
from wakey.forge.port import TicketRef
from wakey.security.secretbox import SecretBox

CONNECTION_KEY = "connect:binding"


class ConnectClient:
    def __init__(
        self, url: str, storage: Storage, box: SecretBox, http: httpx.Client | None = None
    ) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Connect endpoint must be an operator-configured HTTPS URL")
        self.url = url.rstrip("/")
        self.storage, self.box = storage, box
        self.http = http or httpx.Client(timeout=15, follow_redirects=False)
        self.lock = threading.RLock()
        self._cached: tuple[str, str, float] = ("", "", 0)

    def state(self) -> dict[str, Any]:
        raw = self.storage.get_secret(CONNECTION_KEY)
        return json.loads(self.box.decrypt(raw)) if raw else {}

    def save(self, data: dict[str, Any]) -> None:
        self.storage.set_secret(CONNECTION_KEY, self.box.encrypt(json.dumps(data)))

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        headers = {}
        if authenticated:
            credential = self.state().get("credential")
            if not credential:
                raise ForgeError("Connect is not paired; open Setup → Connect GitHub")
            headers["Authorization"] = f"Bearer {credential}"
        try:
            response = self.http.request(method, self.url + path, json=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ForgeError(
                "Connect unavailable or permission revoked; check the connection"
            ) from exc
        result: dict[str, Any] = response.json()
        return result

    def begin(self) -> dict[str, Any]:
        with self.lock:
            if self.state().get("state") == "connected":
                raise ForgeError("disconnect before pairing again")
            data = self.request("POST", "/v1/pair", authenticated=False)
            self.save({**data, "state": "pending"})
            return data

    def poll(self) -> dict[str, Any]:
        with self.lock:
            data = self.request("POST", "/v1/poll")
            state = {**self.state(), **data}
            self.save(state)
            return state

    def select(self, repository_id: int) -> dict[str, Any]:
        with self.lock:
            options = self.state().get("repositories", [])
            choice = next((r for r in options if r["id"] == repository_id), None)
            if choice is None:
                raise ForgeError("select an authorized repository")
            if any(s.repo != choice["name"] for s in self.storage.list_services()):
                raise ForgeError(
                    "existing services belong to a different repo; use a separate deployment"
                )
            data = self.request("POST", "/v1/select", {"repository_id": repository_id})
            self.save({**self.state(), **data})
            return data

    def repository(self) -> str:
        state = self.state()
        name = str((state.get("repository") or {}).get("name", ""))
        if state.get("state") != "connected" or not re.fullmatch(r"[\w.-]+/[\w.-]+", name):
            raise ForgeError("Connect is not ready; finish GitHub setup")
        return name

    def token(self) -> str:
        with self.lock:
            self.repository()
            credential = str(self.state()["credential"])
            owner, token, expiry = self._cached
            if owner == credential and expiry > time.time() + 60:
                return token
            data = self.request("POST", "/v1/token")
            expires = datetime.fromisoformat(
                str(data["expires_at"]).replace("Z", "+00:00")
            ).timestamp()
            self._cached = (credential, str(data["token"]), expires)
            return str(data["token"])

    def receive(self) -> None:
        if self.state().get("state") != "connected":
            return
        for event in self.request("GET", "/v1/events")["events"]:
            # Persist locally before broker acknowledgement; a crash simply replays
            # the same local delivery id. The existing worker performs the action.
            self.storage.save_delivery(
                Delivery(
                    service="__webhook__",
                    delivery_id="connect:" + event["id"],
                    payload=json.dumps({"event": event["event"], "kind": event["kind"]}),
                )
            )
            self.request("POST", "/v1/ack", {"delivery_id": event["id"]})

    def disconnect(self) -> None:
        with self.lock:
            self.request("POST", "/v1/disconnect")
            self.storage.delete_secret(CONNECTION_KEY)
            self._cached = ("", "", 0)


class ConnectForge(GitHubAdapter):
    """Stable adapter object: pairing changes its credentials without restart.

    One repository only. No mutable per-request repo selection and no console
    fallback: unpaired or revoked requests fail visibly and remain retryable.
    """

    def __init__(self, connect: ConnectClient, client: httpx.Client | None = None) -> None:
        self.connect = connect
        super().__init__(GitHubConfig("", "_connect_"), client, token_provider=connect.token)

    def _request(
        self, method: str, path: str, json_body: object, accept: str = "application/vnd.github+json"
    ) -> httpx.Response:
        path = path.replace("/repos/_connect_", f"/repos/{self.connect.repository()}", 1)
        return super()._request(method, path, json_body, accept)

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef:
        service = self.connect.storage.get_service(fingerprint.service)
        if service is None or service.repo != self.connect.repository():
            raise ForgeError("service does not belong to the connected repository")
        return super().create_ticket(fingerprint, title, body)
