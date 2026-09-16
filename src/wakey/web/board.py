# SPDX-License-Identifier: Apache-2.0
"""Server-rendered live board (WF-12 page 3, NFR-9).

One standalone HTML page, re-rendered per request: no JavaScript framework,
no SSE, no external assets (WF-12 air-gap rule — nothing phones home). The
operator on call at 2am opens ``/board`` and reads a table. Styling follows
docs/design.md: night canvas #0b0d12, charcoal card #161920, body text
#a3a8b3, Signal Amber reserved for the states where the system is acting.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from wakey.web.style import STYLE

# State -> presentation (WF-12 page 3). Amber = wakey is acting on it;
# green = a verified fix; every other state stays quiet body gray.
_STATE_COLORS: dict[str, str] = {
    "investigating": "#ffb020",
    "fixing": "#ffb020",
    "queued-rca": "#ffb020",
    "verifying": "#ffb020",
    "awaiting-human": "#e6a23c",
    "verified-closed": "#2fd67b",
}

# Palette tokens inlined as a plain (non-f-string) block so the CSS braces
# need no escaping; values mirror docs/design.md and must never load remotely.
_STYLE = STYLE

_COLUMNS = (
    "fingerprint",
    "service",
    "occurrences",
    "severity",
    "state",
    "last seen",
)


def _text(value: Any) -> str:
    """Cell text for a loosely-typed field: enum-like values, datetimes, plain."""
    if isinstance(value, datetime):
        return value.isoformat()
    plain = getattr(value, "value", value)
    return plain if isinstance(plain, str) else str(plain)


def _row(fp: Any) -> str:
    """One ``<tr>`` for a fingerprint; the hash links to the detail page."""
    state = _text(fp.state)
    color = _STATE_COLORS.get(state)
    state_cell = f'<span style="color:{color}">{escape(state)}</span>' if color else escape(state)
    if getattr(fp, "chronic", False):
        state_cell += ' <span class="pill" title="autonomous fixes blocked">chronic</span>'
    hash_text = escape(_text(fp.fp_hash))
    cells = (
        f'<td class="mono"><a href="/fingerprints/{hash_text}">{hash_text}</a></td>',
        f"<td>{escape(_text(fp.service))}</td>",
        f"<td>{escape(str(fp.occurrences))}</td>",
        f"<td>{escape(_text(fp.severity))}</td>",
        f"<td>{state_cell}</td>",
        f'<td class="mono">{escape(_text(fp.last_seen))}</td>',
    )
    return "<tr>" + "".join(cells) + "</tr>"


def render_board_html(fingerprints: list[Any], generated_at: str) -> str:
    """Render the full standalone board page for ``fingerprints``.

    ``generated_at`` is a pre-formatted timestamp string shown as the page's
    freshness line. The active count leads in the ``h1`` ("wakey board — N
    active"); the table columns are hash, service, occurrences, severity,
    state, last seen.
    """
    head = "".join(f"<th>{escape(name)}</th>" for name in _COLUMNS)
    rows = "".join(_row(fp) for fp in fingerprints)
    if not rows:
        rows = (
            '<tr><td class="empty" colspan="6">'
            "no active fingerprints — the night is quiet</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wakey — board</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>wakey board — {len(fingerprints)} active</h1>
<p class="meta">generated at {escape(generated_at)} · refresh to update</p>
<div class="card">
<table>
<thead><tr>{head}</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
</main>
</body>
</html>
"""
