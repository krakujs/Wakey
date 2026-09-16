# SPDX-License-Identifier: Apache-2.0
"""Config hot-reload tests (E3-T6): wakey.yml edits reach stored service config."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from wakey.core.composition import build_config_reload, compose
from wakey.core.config import Autonomy, ServiceYaml, Settings
from wakey.core.models import Service
from wakey.core.storage import SQLiteStorage
from wakey.forge.github import GitHubAdapter, GitHubConfig
from wakey.forge.simulator import GitHubSimulator, serve_in_background

WAKE_YML_V1 = """\
services:
  payments-api:
    autonomy: observe
    immediate_count: 7
"""
WAKE_YML_V2 = """\
services:
  payments-api:
    autonomy: fix
    immediate_count: 2
    confidence_floor: 0.9
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def forge_env(tmp_path: Path):
    sim = GitHubSimulator(token="sim-token")
    port = _free_port()
    server = serve_in_background(sim.create_app(), port)
    adapter = GitHubAdapter(
        GitHubConfig(token="sim-token", repo="acme/payments", api_base=f"http://127.0.0.1:{port}")
    )
    sim.set_file("acme/payments", "wakey.yml", WAKE_YML_V1)

    settings = Settings(data_dir=tmp_path)
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        Service(name="payments-api", repo="acme/payments", ingest_key_hash="0123456789abcdef")
    )
    components = compose(settings, forge=adapter)
    components.storage.close()  # close compose's own handle; reuse the seeded one
    components.storage = storage
    components.config_reload = build_config_reload(storage, adapter, components.metrics)
    yield components, sim, storage
    storage.close()
    server.should_exit = True


def test_wakey_yaml_edit_reaches_stored_config(forge_env) -> None:
    components, sim, storage = forge_env
    components.config_reload()

    stored = storage.get_service("payments-api")
    assert stored is not None
    assert '"autonomy":"observe"' in stored.config_json.replace(" ", "")

    # the operator edits wakey.yml on the forge
    sim.set_file("acme/payments", "wakey.yml", WAKE_YML_V2)
    components.config_reload()

    stored = storage.get_service("payments-api")
    assert stored is not None
    assert '"autonomy":"fix"' in stored.config_json.replace(" ", "")
    assert '"confidence_floor":0.9' in stored.config_json.replace(" ", "")


def test_stored_config_drives_policy_immediately(forge_env) -> None:
    components, _, storage = forge_env
    components.config_reload()
    stored = storage.get_service("payments-api")
    assert stored is not None
    config = ServiceYaml.model_validate_json(stored.config_json)
    assert config.autonomy is Autonomy.OBSERVE
    assert config.immediate_count == 7


def test_invalid_wakey_yaml_keeps_last_good_config(forge_env) -> None:
    components, sim, storage = forge_env
    components.config_reload()
    first = storage.get_service("payments-api")
    assert first is not None

    sim.set_file("acme/payments", "wakey.yml", "services: {payments-api: {autnomy: fix}}")
    components.config_reload()  # must not raise, must not corrupt config

    second = storage.get_service("payments-api")
    assert second is not None
    assert second.config_json == first.config_json, "last-good config preserved"
