# SPDX-License-Identifier: Apache-2.0
"""Composition root tests (R-01): one path assembles the running system."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wakey.core.composition import build_forge, compose
from wakey.core.config import ConfigError, Settings
from wakey.core.models import Delivery
from wakey.forge.port import ConsoleForge


def test_compose_builds_full_graph_with_console_forge(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    components = compose(settings)
    try:
        assert isinstance(components.forge, ConsoleForge)
        assert components.pipeline is not None
        assert components.worker is not None
        assert components.rca is not None
        assert components.runtime is not None
        assert components.storage.check_ready()
        assert (tmp_path / "wakey.db").exists(), "storage opened at the configured data dir"
    finally:
        components.storage.close()


def test_compose_recovers_stale_processing_at_startup(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    components = compose(settings)
    # simulate a crash mid-delivery, then re-compose (fresh boot)
    components.storage.save_delivery(
        Delivery(service="acme", delivery_id="d-crash", payload="ERROR: boom")
    )
    claimed = components.storage.claim_next_delivery()
    assert claimed is not None
    components.storage.close()

    second = compose(settings)
    try:
        assert second.storage.pending_delivery_count() == 1, "stale processing reset to pending"
    finally:
        second.storage.close()


def test_runtime_start_stop_runs_workers(tmp_path: Path) -> None:
    ran: list[int] = []

    class Maintenance:
        def __call__(self) -> None:
            ran.append(1)

    settings = Settings(data_dir=tmp_path)
    components = compose(settings)
    components.runtime.maintenance = Maintenance()

    async def lifecycle() -> None:
        await components.runtime.start()
        await components.runtime.stop()

    asyncio.run(lifecycle())
    assert ran == [1], "retention pass runs once at startup"


def test_build_forge_requires_repo_with_token() -> None:
    assert isinstance(build_forge(Settings()), ConsoleForge)
    # token without repo is rejected at Settings level
    with pytest.raises(ConfigError):
        Settings.from_env({"WAKEY_GITHUB_TOKEN": "tok"})
