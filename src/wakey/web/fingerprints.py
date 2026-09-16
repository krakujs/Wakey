# SPDX-License-Identifier: Apache-2.0
"""Fingerprint detail page (WF-12 page 3b, founder directive 2026-09-16).

One standalone server-rendered page per fingerprint: the evidence trail a
2am operator needs before deciding anything — what the error family looks
like (template, frames), the raw redacted log events behind it, the RCA
verdict with confidence, and the full audit line for this fingerprint.
Same air-gap rules as the board: no JavaScript framework, no external
assets, refresh to update.
"""

from __future__ import annotations

from html import escape
from typing import Any

from wakey.web.style import STYLE

_CLASS_COLORS = {
    "code-fix": "#ffb020",
    "infra": "#7fb3ff",
    "config": "#7fb3ff",
    "needs-human": "#e6a23c",
}


def _text(value: Any) -> str:
    plain = getattr(value, "value", value)
    return plain if isinstance(plain, str) else str(plain)


def _first_line(template: str, width: int = 96) -> str:
    line = template.splitlines()[0] if template else ""
    return line[:width] + ("…" if len(line) > width else "")


def _summary_card(fp: Any, audits: list[Any]) -> str:
    state = escape(_text(fp.state))
    if getattr(fp, "chronic", False):
        state += ' <span class="pill">chronic — autonomous fixes blocked</span>'
    ticket = ""
    if fp.ticket_issue_id:
        label = escape(fp.ticket_issue_id)
        if fp.ticket_url:
            ticket = f'<a href="{escape(fp.ticket_url)}">{label}</a>'
        else:
            ticket = f'<span class="mono">{label}</span>'
    else:
        ticket = '<span class="empty">no ticket yet</span>'
    rows = (
        ("fingerprint", f'<span class="mono">{escape(fp.fp_hash)}</span>'),
        ("service", f"{escape(fp.service)} / {escape(fp.environment)}"),
        ("severity", escape(_text(fp.severity))),
        ("state", state),
        ("occurrences", escape(str(fp.occurrences))),
        ("first seen", f'<span class="mono">{escape(_text(fp.first_seen))}</span>'),
        ("last seen", f'<span class="mono">{escape(_text(fp.last_seen))}</span>'),
        ("ticket", ticket),
    )
    cells = "".join(f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in rows)
    return f'<div class="card"><table>{cells}</table></div>'


def _rca_section(audits: list[Any]) -> str:
    rca = next((a for a in audits if a.action == "rca.classify"), None)
    if rca is None:
        return (
            '<div class="card"><p class="empty">not analyzed yet — '
            "RCA runs after the policy gate wakes this fingerprint</p></div>"
        )
    rca_class = rca.details.get("class", "needs-human")
    color = _CLASS_COLORS.get(rca_class, "#a3a8b3")
    pill = (
        f'<span class="pill" style="color:{color};border-color:{color}">{escape(rca_class)}</span>'
    )
    confidence = escape(rca.details.get("confidence", "?"))
    summary = escape(rca.details.get("summary", ""))
    body = f"<p style='margin:10px 14px'>{pill} · confidence {confidence}</p>"
    if summary:
        body += f"<pre>{summary}</pre>"
    return f'<div class="card">{body}</div>'


def _events_section(events: list[Any]) -> str:
    if not events:
        empty = "no stored events (retention may have pruned them)"
        return f'<div class="card"><p class="empty">{empty}</p></div>'
    blocks = []
    for event in events:
        head = f"{_text(event.ts)} · {escape(_text(event.severity))} · {escape(event.source)}"
        blocks.append(
            f"<div style='border-bottom:1px solid #191c23'>"
            f"<div class='mono' style='padding:8px 14px 0;color:#7d828d'>{head}</div>"
            f"<pre>{escape(event.message)}</pre></div>"
        )
    return f'<div class="card">{"".join(blocks)}</div>'


def _audit_section(audits: list[Any]) -> str:
    if not audits:
        return '<div class="card"><p class="empty">no audit lines yet</p></div>'
    rows = []
    for event in audits:
        details = ", ".join(f"{k}={v}" for k, v in event.details.items())
        rows.append(
            "<tr>"
            f'<td class="mono">{escape(_text(event.ts))}</td>'
            f"<td>{escape(event.actor)}</td>"
            f"<td class='mono'>{escape(event.action)}</td>"
            f"<td>{escape(details)}</td>"
            "</tr>"
        )
    head = "".join(f"<th>{name}</th>" for name in ("time", "actor", "action", "details"))
    body = "".join(rows)
    return (
        f'<div class="card"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _fix_section(fp_hash: str, flash: str | None, flash_ok: bool) -> str:
    flash_html = ""
    if flash:
        css = "flash ok" if flash_ok else "flash"
        flash_html = f'<p class="{css}">{escape(flash)}</p>'
    return (
        "<h2>fix</h2>"
        f"{flash_html}"
        f'<form method="post" action="/fingerprints/{escape(fp_hash)}/fix">'
        '<button class="btn" type="submit">Request fix attempt</button>'
        "</form>"
        '<p class="note">Runs the same FIX-1 eligibility gate the autonomous loop '
        "uses (class, confidence floor, autonomy dial, PR cap, chronic guard). "
        "Patch execution itself lands with the fix runtime (E7-T1/E8); every "
        "request is audited either way.</p>"
    )


def render_fingerprint_html(
    fingerprint: Any,
    events: list[Any],
    audits: list[Any],
    *,
    flash: str | None = None,
    flash_ok: bool = False,
    generated_at: str = "",
) -> str:
    """Render the standalone detail page for one fingerprint (WF-12 page 3b)."""
    fp = fingerprint
    title = escape(_first_line(fp.template))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wakey — {title}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<p class="meta" style="margin-bottom:8px"><a href="/board">← board</a></p>
<h1>{title}</h1>
<p class="meta">fingerprint detail · generated at {escape(generated_at)} · refresh to update</p>
{_summary_card(fp, audits)}
{_fix_section(fp.fp_hash, flash, flash_ok)}
<h2>root cause analysis</h2>
{_rca_section(audits)}
<h2>recent log events <span class="mono" style="font-size:12px">({escape(fp.service)})</span></h2>
{_events_section(events)}
<h2>audit trail</h2>
{_audit_section(audits)}
</main>
</body>
</html>
"""
