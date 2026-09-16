# SPDX-License-Identifier: Apache-2.0
"""Configuration system (E2-T2): environment settings + per-repo ``wakey.yml``.

Two distinct layers, both failing loud with actionable errors (WF-01 §6):

- :class:`Settings` — server-level, from environment variables (``WAKEY_*``).
- :class:`WakeyYaml` / :func:`load_wakey_yaml` — per-repo service config,
  parsed and validated wherever it is fetched from (forge, disk, dashboard).

Everything autonomous is tunable here; no magic constants live in modules.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_REGEX_LENGTH = 500  # crude ReDoS surface bound; real guard lands with E10-T1

_SUPPORTED_DATABASE_SCHEMES = ("sqlite", "postgresql", "postgres", "")


class ConfigError(Exception):
    """Raised when configuration is invalid; ``problems`` lists every issue."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


class Autonomy(StrEnum):
    """The trust dial per repo (WF-07 §6): what Wakey may do without asking."""

    OBSERVE = "observe"
    TRIAGE = "triage"
    DIGEST = "digest"
    FIX = "fix"


class Settings(BaseModel):
    """Server-level settings, sourced from ``WAKEY_*`` environment variables."""

    port: int = Field(default=8477, ge=1, le=65535)
    data_dir: Path = Field(default=Path("wakey-data"))
    base_url: str = "http://localhost:8477"
    master_key: str = ""  # empty = development mode; production boot requires it
    webhook_secret: str = ""
    database_url: str = ""  # empty → SQLite at <data_dir>/wakey.db
    log_level: str = "INFO"
    llm_base_url: str = ""  # Anthropic-compatible endpoint; empty = no live tier
    llm_api_key: str = ""
    llm_model: str = "glm-4.5-air"
    github_token: str = ""  # empty → ConsoleForge development sink (R-01)
    github_repo: str = ""  # owner/name tickets are created in
    github_api_base: str = "https://api.github.com"
    allowed_repos: str = ""  # comma-separated write allowlist; empty = unrestricted
    worker_count: int = Field(default=2, ge=1, le=16)
    rca_max_per_hour: int = Field(default=30, ge=1)
    retention_days: int = Field(default=30, ge=1)  # event/delivery retention (R-14)
    # Deny-by-default @wakey command authorization (WF-07 §3) until
    # installation-permission checks land (G-gate):
    authorized_users: str = ""
    self_watch: bool = False  # OPS-4: ingest wakey's own errors
    # GCP Pub/Sub push OIDC verification (E4-T4): when the audience is set,
    # /ingest/gcp/<key> verifies the push's Bearer token before accepting.
    oidc_audience: str = ""
    oidc_service_account: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str]) -> Settings:
        """Build from an env mapping (os.environ at runtime; dict in tests)."""
        problems: list[str] = []
        parsed: dict[str, Any] = {}

        simple_fields = {
            "WAKEY_BASE_URL": "base_url",
            "WAKEY_DATA_DIR": "data_dir",
            "WAKEY_MASTER_KEY": "master_key",
            "WAKEY_WEBHOOK_SECRET": "webhook_secret",
            "WAKEY_DATABASE_URL": "database_url",
            "WAKEY_LLM_BASE_URL": "llm_base_url",
            "WAKEY_LLM_API_KEY": "llm_api_key",
            "WAKEY_LLM_MODEL": "llm_model",
            "WAKEY_GITHUB_TOKEN": "github_token",
            "WAKEY_GITHUB_REPO": "github_repo",
            "WAKEY_GITHUB_API_BASE": "github_api_base",
            "WAKEY_ALLOWED_REPOS": "allowed_repos",
            "WAKEY_AUTHORIZED_USERS": "authorized_users",
            "WAKEY_OIDC_AUDIENCE": "oidc_audience",
            "WAKEY_OIDC_SERVICE_ACCOUNT": "oidc_service_account",
        }
        for env_key, field_name in simple_fields.items():
            if value := env.get(env_key):
                parsed[field_name] = Path(value) if field_name == "data_dir" else value

        if env.get("WAKEY_SELF_WATCH", "").strip().lower() in {"1", "true", "yes"}:
            parsed["self_watch"] = True

        for env_key, field_name in (
            ("WAKEY_WORKER_COUNT", "worker_count"),
            ("WAKEY_RCA_MAX_PER_HOUR", "rca_max_per_hour"),
            ("WAKEY_RETENTION_DAYS", "retention_days"),
            ("WAKEY_PORT", "port"),
        ):
            if raw := env.get(env_key):
                try:
                    parsed[field_name] = int(raw)
                except ValueError:
                    problems.append(f"{env_key} must be an integer, got {raw!r}")

        if raw_level := env.get("WAKEY_LOG_LEVEL"):
            if raw_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
                problems.append(
                    f"WAKEY_LOG_LEVEL must be DEBUG/INFO/WARNING/ERROR, got {raw_level!r}"
                )
            parsed["log_level"] = raw_level.upper()

        if (raw_db := env.get("WAKEY_DATABASE_URL", "")).strip():
            scheme = raw_db.split("://", 1)[0].lower()
            if scheme not in _SUPPORTED_DATABASE_SCHEMES:
                problems.append(
                    f"WAKEY_DATABASE_URL scheme {scheme!r} is not supported yet "
                    f"(supported: sqlite) — Postgres lands with OPS-5"
                )

        if parsed.get("github_token") and not parsed.get("github_repo"):
            problems.append("WAKEY_GITHUB_TOKEN requires WAKEY_GITHUB_REPO (owner/name)")

        if problems:
            raise ConfigError(problems)
        return cls(**parsed)

    @property
    def allowlist(self) -> tuple[str, ...] | None:
        """Parsed repo allowlist; ``None`` means unrestricted (E10 will mandate it)."""
        repos = tuple(r.strip() for r in self.allowed_repos.split(",") if r.strip())
        return repos or None

    @property
    def live_forge(self) -> bool:
        """True when a real GitHub adapter will be composed (vs ConsoleForge)."""
        return bool(self.github_token and self.github_repo)


def _parse_int_env(
    env: dict[str, str], env_key: str, parsed: dict[str, Any], problems: list[str]
) -> None:
    """Parse one integer env var into ``parsed`` or record a named problem."""
    raw = env.get(env_key)
    if not raw:
        return
    try:
        parsed[env_key.split("_", 1)[1].lower()] = int(raw)
    except ValueError:
        problems.append(f"{env_key} must be an integer, got {raw!r}")


class ServiceYaml(BaseModel):
    """One service block inside a repo's ``wakey.yml`` (WF-01 §A3).

    Strict parsing: unknown keys are rejected with a named problem, not
    silently ignored — a typo like ``autnomy:`` must never demote a service
    to default autonomy (R-12).
    """

    model_config = ConfigDict(extra="forbid")

    path: str = "."
    autonomy: Autonomy = Autonomy.TRIAGE
    rate_per_min: int = Field(default=5, ge=1)
    immediate_count: int = Field(default=3, ge=1)
    grace_minutes: int = Field(default=30, ge=1)
    max_open_prs: int = Field(default=3, ge=1)
    max_tickets_per_hour: int = Field(default=10, ge=1)
    confidence_floor: float = Field(default=0.75, ge=0.0, le=1.0)
    allow_without_tests: bool = False
    test_command: str | None = None
    redact: list[str] = Field(default_factory=list)

    def validate_redact(self) -> list[str]:
        """Compile redaction patterns; return one problem string per bad pattern."""
        problems: list[str] = []
        for pattern in self.redact:
            if len(pattern) > MAX_REGEX_LENGTH:
                problems.append(f"redact pattern too long ({len(pattern)} > {MAX_REGEX_LENGTH})")
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(f"invalid redact regex {pattern!r}: {exc}")
        return problems


class WakeyYaml(BaseModel):
    """A parsed ``wakey.yml``: services keyed by service name."""

    model_config = ConfigDict(extra="forbid")

    services: dict[str, ServiceYaml] = Field(default_factory=dict, min_length=1)


def load_wakey_yaml(text: str) -> WakeyYaml:
    """Parse and validate ``wakey.yml`` content, raising :class:`ConfigError`.

    Every problem is collected and reported together, with source line numbers
    where the YAML parser provides them.
    """
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f" (line {mark.line + 1})" if mark is not None else ""
        raise ConfigError([f"invalid YAML{line}: {exc}"]) from exc

    if not isinstance(raw, dict):
        raise ConfigError(["wakey.yml must be a mapping with a 'services' key"])

    problems: list[str] = []
    compiled: dict[str, ServiceYaml] = {}
    try:
        parsed = WakeyYaml.model_validate(raw)
    except ValidationError as exc:
        problems.extend(
            f"wakey.yml: {error['msg']} at {'.'.join(str(p) for p in error['loc'])}"
            for error in exc.errors()
        )
        raise ConfigError(problems) from exc

    for name, service in parsed.services.items():
        for problem in service.validate_redact():
            problems.append(f"services.{name}: {problem}")
        compiled[name] = service

    if problems:
        raise ConfigError(problems)
    return WakeyYaml(services=compiled)
