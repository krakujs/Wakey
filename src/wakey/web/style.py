# SPDX-License-Identifier: Apache-2.0
"""Shared design tokens for the server-rendered pages (docs/design.md).

Plain (non-f-string) block so CSS braces need no escaping; values mirror
docs/design.md and must never load remotely (WF-12 air-gap rule).
"""

from __future__ import annotations

STYLE = """
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #0b0d12;
      color: #a3a8b3;
      font: 14px/1.5 'Inter', ui-sans-serif, system-ui, sans-serif;
    }
    main { max-width: 1200px; margin: 0 auto; padding: 32px 20px; }
    h1 {
      color: #ffffff;
      font-size: 24px;
      font-weight: 500;
      letter-spacing: -0.5px;
      margin: 0 0 4px;
    }
    h2 {
      color: #ffffff;
      font-size: 15px;
      font-weight: 600;
      letter-spacing: 0.3px;
      margin: 28px 0 10px;
    }
    .meta { color: #7d828d; font-size: 13px; margin: 0 0 24px; }
    .card {
      background: #161920;
      border-radius: 12px;
      overflow-x: auto;
    }
    table { width: 100%; border-collapse: collapse; }
    th {
      text-align: left;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.88px;
      text-transform: uppercase;
      color: #7d828d;
      padding: 10px 14px;
      border-bottom: 1px solid #23262e;
      white-space: nowrap;
    }
    td { padding: 10px 14px; border-bottom: 1px solid #191c23; vertical-align: top; }
    tr:last-child td { border-bottom: none; }
    .mono { font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px; }
    .empty { text-align: center; color: #7d828d; padding: 32px 14px; }
    a { color: #ffb020; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .pill {
      display: inline-block; padding: 1px 8px; border-radius: 999px;
      font-size: 12px; border: 1px solid #23262e;
    }
    .flash {
      border: 1px solid #23262e; border-left: 3px solid #ffb020;
      border-radius: 8px; padding: 12px 14px; margin: 0 0 20px;
      color: #e6e9ef; background: #161920;
    }
    .flash.ok { border-left-color: #2fd67b; }
    .btn {
      display: inline-block; background: #ffb020; color: #0b0d12;
      border: none; border-radius: 8px; padding: 9px 18px;
      font: 600 13px 'Inter', ui-sans-serif, system-ui, sans-serif;
      cursor: pointer; min-height: 44px;
    }
    .btn:hover { filter: brightness(1.08); }
    .note { color: #7d828d; font-size: 12px; margin: 8px 0 0; }
    pre {
      margin: 0; padding: 12px 14px; white-space: pre-wrap; word-break: break-word;
      font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px;
      color: #c9cdd6;
    }
"""
