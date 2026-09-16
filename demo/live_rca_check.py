#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Live RCA check (M1): real GLM model classifies a genuine production-style error.

Reads credentials from .env only. Prints the model's RCA as it would
appear on a wakey ticket.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wakey.agents.llm import AnthropicCompatibleModel  # noqa: E402

ERROR = (
    "Traceback (most recent call last):\n"
    '  File "app/billing.py", line 87, in charge\n'
    '    return gateway.refund(order.metadata["idempotency_key"])\n'
    "TypeError: 'NoneType' object is not subscriptable\n"
)

SYSTEM = (
    "You are a precise SRE engineer performing root-cause analysis. "
    "Reply in this exact format:\n"
    "CLASS: <code-fix|config|infra|needs-human>\n"
    "CONFIDENCE: <0.00-1.00>\n"
    "ROOT CAUSE: <one sentence>\n"
    "EVIDENCE: <one sentence>"
)


def main() -> int:
    env = {}
    for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()

    model = AnthropicCompatibleModel(
        base_url=env["WAKEY_LLM_BASE_URL"],
        api_key=env["WAKEY_LLM_API_KEY"],
        model=env.get("WAKEY_LLM_MODEL", "glm-4.5-air"),
        max_tokens=1024,
    )
    reply = model.complete(SYSTEM, f"Analyze this production error and fill the template:\n\n{ERROR}")
    if reply.data.get("error"):
        print(f"LLM error: {reply.data['error']}")
        return 1
    print("== live RCA (GLM via z.ai) ==")
    print(reply.text.strip()[:1200])
    ok = "CLASS:" in reply.text
    print("\nLIVE RCA CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
