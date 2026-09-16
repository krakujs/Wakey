# SPDX-License-Identifier: Apache-2.0
"""LLM client tests (RCA-7): typed replies, transport failures never raise (NFR-4)."""

from __future__ import annotations

import httpx

from wakey.agents.llm import AnthropicCompatibleModel


def make_model(handler: httpx.Handler) -> AnthropicCompatibleModel:
    return AnthropicCompatibleModel(
        "http://llm.test",
        "key-123",
        "test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_successful_completion_extracts_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "key-123"
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "content": [
                    {"type": "text", "text": "CLASS: infra"},
                    {"type": "tool_use", "id": "t1"},
                    {"type": "text", "text": " CONFIDENCE: 0.8"},
                ],
            },
        )

    reply = make_model(handler).complete("system", "prompt")
    assert reply.text == "CLASS: infra CONFIDENCE: 0.8"
    assert reply.data["model"] == "test-model"


def test_http_error_returns_error_entry_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "overloaded"})

    reply = make_model(handler).complete("s", "p")
    assert reply.text == ""
    assert reply.data["error"] == "HTTP 503"


def test_transport_error_returns_error_entry_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    reply = make_model(handler).complete("s", "p")
    assert reply.data["error"].startswith("transport:")
