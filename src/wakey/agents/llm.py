# SPDX-License-Identifier: Apache-2.0
"""Live model tier: Anthropic-compatible endpoint client (RCA-7, WF-14 §B).

Talks to any Anthropic-Messages-compatible endpoint (api.anthropic.com,
z.ai GLM proxy, corporate gateways). Errors never raise — they return a
reply with an ``error`` entry so agents degrade gracefully (NFR-4). The
API key is read from Settings/env and is never logged or committed.
"""

from __future__ import annotations

import httpx

from wakey.agents.models import ModelReply


class AnthropicCompatibleModel:
    """ModelClient implementation against an Anthropic-Messages endpoint."""

    def __init__(  # noqa: PLR0913, PLR0917 — endpoint identity is genuinely multi-part
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 512,
        timeout_s: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout_s
        self._client = client or httpx.Client(timeout=timeout_s)

    def complete(self, system: str, prompt: str) -> ModelReply:
        headers = {
            "x-api-key": self._api_key,
            "Authorization": f"Bearer {self._api_key}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = self._client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            return ModelReply(text="", data={"error": f"transport: {exc}"})
        if response.status_code != 200:
            return ModelReply(text="", data={"error": f"HTTP {response.status_code}"})
        data = response.json()
        blocks = data.get("content", [])
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        return ModelReply(text=text, data={"model": data.get("model", self._model)})
