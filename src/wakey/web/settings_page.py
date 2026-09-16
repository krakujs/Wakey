# SPDX-License-Identifier: Apache-2.0
"""Settings page (WF-12 §7): server-rendered, secrets-free configuration view."""

from __future__ import annotations

from typing import Any


def render_settings_html(summary: dict[str, Any]) -> str:
    """Render the settings summary (already masked — no secrets accepted)."""
    rows = [
        ("Port", str(summary.get("port", ""))),
        ("Base URL", str(summary.get("base_url", ""))),
        ("Database", str(summary.get("database", "sqlite"))),
        ("LLM tier", "configured" if summary.get("llm_configured") else "not configured"),
        ("LLM model", str(summary.get("llm_model", "—"))),
        ("Log level", str(summary.get("log_level", "INFO"))),
    ]
    cells = "".join(f'<tr><td>{label}</td><td class="v">{value}</td></tr>' for label, value in rows)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>wakey — settings</title><style>"
        "body{background:#0b0d12;color:#a3a8b3;font-family:Inter,ui-sans-serif,system-ui,sans-serif;"
        "padding:32px}h1{color:#fff;font-size:24px}"
        "table{border-collapse:collapse;background:#161920;border-radius:12px;padding:8px}"
        "td{padding:10px 14px;border-bottom:1px solid #23262e}"
        ".v{color:#fff;font-family:'JetBrains Mono',monospace}"
        "</style></head><body><h1>Settings</h1><table>" + cells + "</table></body></html>"
    )
