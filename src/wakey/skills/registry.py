# SPDX-License-Identifier: Apache-2.0
"""Skill registry (WF-14 addendum, founder directive): user-authoritative skill
loading, bound to model profiles.

Rules:
1. Skills are **disabled by default** — nothing loads until the user names it
   under ``skills.enabled`` in wakey.yml (complete user authority).
2. Each skill ships a manifest (``skill.yml``): name, version, entrypoint,
   permissions, description. Manifests are validated at load; a bad manifest
   is an error, not a warning.
3. Enabled skills may be bound to model profiles; a profile can only use
   skills that are both enabled AND bound. Nothing is implicit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from wakey.core.config import ConfigError

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
REQUIRED_MANIFEST_KEYS = ("name", "version", "entrypoint")


@dataclass(frozen=True)
class SkillManifest:
    """A validated skill declaration from ``skill.yml``."""

    name: str
    version: str
    entrypoint: str
    description: str = ""
    permissions: frozenset[str] = frozenset()

    @classmethod
    def load(cls, path: Path) -> SkillManifest:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError([f"{path}: invalid YAML: {exc}"]) from exc
        if not isinstance(data, dict):
            raise ConfigError([f"{path}: manifest must be a mapping"])
        missing = [key for key in REQUIRED_MANIFEST_KEYS if not data.get(key)]
        if missing:
            raise ConfigError([f"{path}: missing required keys: {', '.join(missing)}"])
        name = str(data["name"])
        if not NAME_PATTERN.match(name):
            raise ConfigError([f"{path}: invalid skill name {name!r}"])
        permissions = frozenset(str(p) for p in data.get("permissions", []))
        return cls(
            name=name,
            version=str(data["version"]),
            entrypoint=str(data["entrypoint"]),
            description=str(data.get("description", "")),
            permissions=permissions,
        )


@dataclass(frozen=True)
class BoundSkill:
    """An enabled skill bound to a profile, with its effective permissions."""

    manifest: SkillManifest
    entrypoint: Path


class SkillRegistry:
    """Discovers, validates, and (only on explicit user instruction) loads skills."""

    def __init__(self, skills_dir: Path, enabled: list[str] | None = None) -> None:
        self._skills_dir = skills_dir
        self._enabled = set(enabled or [])
        self._manifests: dict[str, SkillManifest] = {}
        self._load_manifests()

    def _load_manifests(self) -> None:
        if not self._skills_dir.exists():
            return
        for manifest_path in sorted(self._skills_dir.glob("*/skill.yml")):
            self._manifests[SkillManifest.load(manifest_path).name] = SkillManifest.load(
                manifest_path
            )

    def available(self) -> list[str]:
        """All discovered skill names (enabled or not)."""
        return sorted(self._manifests)

    def enabled(self) -> list[str]:
        """Skill names the user has explicitly enabled AND that exist."""
        return sorted(name for name in self._enabled if name in self._manifests)

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled and name in self._manifests

    def manifest(self, name: str) -> SkillManifest:
        if name not in self._manifests:
            raise ConfigError([f"unknown skill: {name!r}"])
        return self._manifests[name]

    def bind_to_profile(self, profile: str, skills: list[str]) -> list[BoundSkill]:
        """Resolve a profile's skill bindings; everything must be enabled.

        User authority: binding an unknown or disabled skill is a hard error —
        the registry never silently loads anything.
        """
        bound: list[BoundSkill] = []
        problems: list[str] = []
        for name in skills:
            manifest = self._manifests.get(name)
            if manifest is None:
                problems.append(f"profile {profile}: unknown skill {name!r}")
                continue
            if not self.is_enabled(name):
                problems.append(f"profile {profile}: skill {name!r} exists but is not enabled")
                continue
            bound.append(
                BoundSkill(
                    manifest=manifest,
                    entrypoint=self._skills_dir / name / manifest.entrypoint,
                )
            )
        if problems:
            raise ConfigError(problems)
        return bound
