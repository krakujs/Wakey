# SPDX-License-Identifier: Apache-2.0
"""Skill registry tests: user authority, validation, profile binding."""

from __future__ import annotations

from pathlib import Path

import pytest

from wakey.core.config import ConfigError
from wakey.skills.registry import SkillRegistry

MANIFEST = """
name: runbook-lookup
version: 1.0.0
entrypoint: lookup.py
description: query internal runbooks
permissions:
  - events:read
  - network:runbooks.internal
"""


def make_registry(
    tmp_path: Path, enabled: list[str] | None = None, extra: str = ""
) -> SkillRegistry:
    skills = tmp_path / "skills"
    (skills / "runbook-lookup").mkdir(parents=True)
    (skills / "runbook-lookup" / "skill.yml").write_text(MANIFEST)
    if extra:
        (skills / "extra-impl").mkdir(parents=True)
        (skills / "extra-impl" / "skill.yml").write_text(extra)
    return SkillRegistry(skills, enabled=enabled)


def test_disabled_by_default(tmp_path: Path) -> None:
    registry = make_registry(tmp_path, enabled=["runbook-lookup"])
    assert registry.available() == ["runbook-lookup"]
    assert registry.enabled() == ["runbook-lookup"]


def test_nothing_loads_without_explicit_enable(tmp_path: Path) -> None:
    registry = make_registry(tmp_path, enabled=None)
    assert registry.available() == ["runbook-lookup"]
    assert registry.enabled() == []
    with pytest.raises(ConfigError):
        registry.bind_to_profile("strong", ["runbook-lookup"])


def test_unknown_skill_binding_rejected(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    with pytest.raises(ConfigError, match="unknown skill"):
        registry.bind_to_profile("strong", ["nope"])


def test_bad_manifest_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "skills" / "broken" / "skill.yml"
    bad.parent.mkdir(parents=True)
    bad.write_text("description: missing name/version/entrypoint\n")
    with pytest.raises(ConfigError, match="missing required keys"):
        SkillRegistry(tmp_path / "skills")


def test_binding_resolves_entrypoint_and_permissions(tmp_path: Path) -> None:
    registry = make_registry(tmp_path, enabled=["runbook-lookup"])
    bound = registry.bind_to_profile("strong", ["runbook-lookup"])
    assert bound[0].manifest.permissions == frozenset({"events:read", "network:runbooks.internal"})
    assert bound[0].entrypoint == tmp_path / "skills" / "runbook-lookup" / "lookup.py"
