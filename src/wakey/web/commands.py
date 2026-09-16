# SPDX-License-Identifier: Apache-2.0
"""``@wakey`` command bus (WF-07, E3-T3): parse, authorize, dispatch.

Webhook comments arrive here after HMAC verification and delivery
dedup. Parsing is typed: a comment either names a command or is ignored
(non-commands never generate noise). Authorization is deny-by-default:
until installation-permission checks land (G-gate), only logins listed
in ``WAKEY_AUTHORIZED_USERS`` may direct Wakey; everyone else gets the
spec's refusal reply and a security metric. Every handled command
leaves an audit line. The bot's own comments are ignored (loop
prevention, hard rule).

P1 dispatch scope: ``drop``/``reopen`` (lifecycle), ``explain`` (RCA
re-run), ``status`` (state report), ``fix``/``retry`` (named refusals —
on-host workspaces and the PR feedback loop are E7-T1/E8-T8).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from wakey.agents.fix_executor import FixExecutionOutcome
from wakey.agents.rca import RcaDispatcher
from wakey.core.config import ServiceYaml
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import AuditEvent, Fingerprint, WorkState, utcnow
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort, TicketRef

COMMAND_PATTERN = re.compile(r"(?:^|\s)@wakey\s+(\w+)\s*(.*)$", re.IGNORECASE | re.MULTILINE)
KNOWN_COMMANDS = ("fix", "explain", "retry", "drop", "reopen", "status")
UNAUTHORIZED_REPLY = (
    "Only repo collaborators can direct wakey. "
    "Ask an admin to add your login to `WAKEY_AUTHORIZED_USERS`."
)
UNKNOWN_REPLY = (
    "Unknown command. I understand: "
    + ", ".join(f"`@wakey {name}`" for name in KNOWN_COMMANDS)
    + "."
)
HELPFUL_NOTE = "Unknown command"


@dataclass(frozen=True)
class WakeyCommand:
    """One typed command parsed from a comment body."""

    name: str
    argument: str
    raw: str


def parse_command(body: str) -> WakeyCommand | None:
    """Extract the first ``@wakey <command>`` from a comment; None if not one."""
    match = COMMAND_PATTERN.search(body)
    if match is None:
        return None
    name = match.group(1).lower()
    return WakeyCommand(name=name, argument=match.group(2).strip(), raw=match.group(0).strip())


def _render_status(fingerprint: Fingerprint, config: ServiceYaml) -> str:
    return (
        f"**Status** — state `{fingerprint.state.value}`"
        f" · occurrences ×{fingerprint.occurrences}"
        f" · chronic: {'yes — autonomous fixes blocked' if fingerprint.chronic else 'no'}\n\n"
        f"Service caps: ≤{config.max_tickets_per_hour} tickets/h, autonomy"
        f" `{config.autonomy.value}`, comment floor at ×{config.immediate_count}."
    )


class CommandDispatcher:
    """Handles one webhook comment; returns the reply text (or None = silence)."""

    def __init__(
        self,
        storage: Storage,
        forge: ForgePort,
        rca: RcaDispatcher | None,
        *,
        authorized_users: frozenset[str] = frozenset(),
        metrics: MetricsRegistry | None = None,
        clock: Callable[[], datetime] = utcnow,
        bot_logins: frozenset[str] = frozenset({"wakey[bot]", "wakey-bot"}),
        fix_executor: Callable[[Fingerprint], FixExecutionOutcome] | None = None,
    ) -> None:
        self._storage = storage
        self._forge = forge
        self._rca = rca
        self._authorized = authorized_users
        self._fix_executor = fix_executor
        self._metrics = metrics if metrics is not None else MetricsRegistry()
        self._clock = clock
        self._bot_logins = bot_logins

    def handle_comment(self, login: str, issue_id: str, body: str) -> str | None:
        """Route one comment; replies are posted by the caller via the forge."""
        return self._route(login, issue_id, body)

    def _route(self, login: str, issue_id: str, body: str) -> str | None:
        if login in self._bot_logins:
            return None  # loop prevention: never react to our own comments
        command = parse_command(body)
        if command is None:
            return None  # non-commands are ignored entirely

        self._metrics.inc(
            "wakey_commands_total", "@wakey commands received", {"name": command.name}
        )
        fingerprint = self._storage.get_fingerprint_by_issue(issue_id)
        if fingerprint is None:
            return None  # not a wakey ticket — stay quiet on foreign issues

        if login not in self._authorized:
            self._metrics.inc("wakey_commands_unauthorized_total", "unauthorized command attempts")
            self._audit(login, command, fingerprint.fp_hash, "unauthorized")
            return UNAUTHORIZED_REPLY

        reply, action = self._dispatch(fingerprint, command, login)
        self._audit(login, command, fingerprint.fp_hash, action)
        return reply

    # --- dispatch -----------------------------------------------------------------

    def _dispatch(
        self, fingerprint: Fingerprint, command: WakeyCommand, login: str
    ) -> tuple[str, str]:
        handlers: dict[str, Callable[[Fingerprint, WakeyCommand, str], tuple[str, str]]] = {
            "drop": self._cmd_drop,
            "reopen": self._cmd_reopen,
            "explain": self._cmd_explain,
            "status": self._cmd_status,
            "fix": self._cmd_fix,
            "retry": self._cmd_retry,
        }
        handler = handlers.get(command.name)
        if handler is None:
            return UNKNOWN_REPLY, "unknown"
        return handler(fingerprint, command, login)

    def _cmd_drop(
        self, fingerprint: Fingerprint, command: WakeyCommand, login: str
    ) -> tuple[str, str]:
        updated = fingerprint.model_copy(update={"state": WorkState.DROPPED})
        self._storage.save_fingerprint(updated)
        self._forge.close_ticket(
            self._ticket(fingerprint),
            f"suppressed by @{login} via `@wakey drop` — reversible with `@wakey reopen`",
        )
        return f"Fingerprint `{fingerprint.fp_hash[:8]}` dropped by @{login}.", "drop"

    def _cmd_reopen(
        self, fingerprint: Fingerprint, command: WakeyCommand, login: str
    ) -> tuple[str, str]:
        closable = (WorkState.CLOSED_AUTO, WorkState.CLOSED_HUMAN, WorkState.DROPPED)
        if fingerprint.state not in closable:
            return (
                f"Ticket is `{fingerprint.state.value}` — reopen applies to "
                "closed/dropped tickets.",
                "reopen-noop",
            )
        updated = fingerprint.model_copy(update={"state": WorkState.REOPENED})
        self._storage.save_fingerprint(updated)
        self._forge.reopen_ticket(self._ticket(fingerprint))
        return "Reopened — wakey is watching this fingerprint again.", "reopen"

    def _cmd_explain(
        self, fingerprint: Fingerprint, command: WakeyCommand, login: str
    ) -> tuple[str, str]:
        if self._rca is None:
            return "RCA agent is not available on this instance.", "explain-refused"
        result = self._rca.investigate(fingerprint)
        return (
            f"**Re-analysis** — class `{result.classification}`"
            f" · confidence {result.confidence:.2f}\n\n{result.summary}",
            "explain",
        )

    def _cmd_status(
        self, fingerprint: Fingerprint, command: WakeyCommand, login: str
    ) -> tuple[str, str]:
        config = self._service_config(fingerprint.service)
        return _render_status(fingerprint, config), "status"

    def _cmd_fix(
        self, fingerprint: Fingerprint, command: WakeyCommand, login: str
    ) -> tuple[str, str]:
        if self._fix_executor is None:
            return (
                "Fix execution is not available on this instance (no workspace "
                "runtime configured). See `@wakey status`.",
                "fix-refused",
            )
        outcome = self._fix_executor(fingerprint)
        return outcome.message, f"fix-{outcome.stage}"

    def _cmd_retry(
        self, fingerprint: Fingerprint, command: WakeyCommand, login: str
    ) -> tuple[str, str]:
        return (
            "The PR review-feedback loop is not available yet (E8-T8). "
            "Push an update to the fix branch instead.",
            "retry-refused",
        )

    # --- helpers ------------------------------------------------------------------

    def _service_config(self, service: str) -> ServiceYaml:
        stored = self._storage.get_service(service)
        if stored is None:
            return ServiceYaml()
        try:
            return ServiceYaml.model_validate_json(stored.config_json)
        except ValidationError:
            return ServiceYaml()

    @staticmethod
    def _ticket(fingerprint: Fingerprint) -> TicketRef:
        return TicketRef(
            issue_id=fingerprint.ticket_issue_id or fingerprint.fp_hash,
            url=fingerprint.ticket_url or "",
        )

    def _audit(self, login: str, command: WakeyCommand, fp_hash: str, action: str) -> None:
        self._storage.record_audit(
            AuditEvent(
                actor=f"user:{login}",
                action=f"command.{command.name}",
                subject=f"fp:{fp_hash}",
                details={"result": action},
            )
        )
