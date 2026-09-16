# SPDX-License-Identifier: Apache-2.0
"""Service registration (WF-01 §5, E3-T5): the unconfigured-to-working step.

Creates a service binding (repo, environment, ingest key) and returns the
generated key exactly once — only its SHA-256 hash is stored, so a leaked
database cannot leak working keys. Rotation invalidates the old key by
replacement. Every registration/rotation is audited.
"""

from __future__ import annotations

import hashlib
import re
import secrets

from wakey.core.models import AuditEvent, Service
from wakey.core.storage import Storage

KEY_PREFIX = "wk_"
NAME_PATTERN_RESTRICTION = "letters, digits, dash and underscore"


def generate_ingest_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(24)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class RegistrationError(Exception):
    """Validation failed; ``problems`` lists every issue."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def _validate(name: str, repo: str) -> list[str]:
    problems: list[str] = []
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        problems.append(f"service name must use {NAME_PATTERN_RESTRICTION}")
    if len(name) > 64:
        problems.append("service name too long (max 64)")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) is None:
        problems.append("repo must be owner/name")
    return problems


def register_service(
    storage: Storage,
    *,
    name: str,
    repo: str,
    environment: str = "prod",
    path: str = ".",
    webhook_secret: str | None = None,
    actor: str = "user:dashboard",
) -> tuple[Service, str]:
    """Create a service and return it with the one-time ingest key."""
    name, repo = name.strip(), repo.strip()
    problems = _validate(name, repo)
    if storage.get_service(name) is not None:
        problems.append(f"service {name!r} already exists")
    if problems:
        raise RegistrationError(problems)

    key = generate_ingest_key()
    service = Service(
        name=name,
        repo=repo,
        environment=environment.strip() or "prod",
        path=path.strip() or ".",
        ingest_key_hash=key_hash(key),
        webhook_secret=webhook_secret or None,
    )
    storage.save_service(service)
    storage.record_audit(
        AuditEvent(
            actor=actor,
            action="service.register",
            subject=name,
            details={"repo": repo, "environment": service.environment},
        )
    )
    return service, key


def rotate_ingest_key(storage: Storage, name: str) -> tuple[Service, str] | None:
    """Replace the ingest key; the old key stops working immediately."""
    service = storage.get_service(name)
    if service is None:
        return None
    key = generate_ingest_key()
    updated = service.model_copy(update={"ingest_key_hash": key_hash(key)})
    storage.save_service(updated)
    storage.record_audit(
        AuditEvent(actor="user:dashboard", action="service.rotate_key", subject=name, details={})
    )
    return updated, key
