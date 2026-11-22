"""Tests for the LLM client service.

All network access is mocked. The HTTP transport is replaced with an
in-memory HTTPX mock transport, so these tests never contact a real website.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx2
import pytest

from app.core.config import settings
from app.services.llm import (
    LLMClient,
    LLMError,
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMNotConfiguredError,
    LLMTimeoutError,
    LLMInvalidResponseError,
    LLMNetworkError,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_client(**kwargs) -> LLMClient:
    defaults = {
        "api_key": "test-key-123",
        "base_url": "https://example.com/v1",
        "model": "test-model",
        "timeout_seconds": 5.0,
    }
    defaults.update(kwargs)
    return LLMClient(**defaults)


def _chat_params() -> list[Mapping[str, str]]:
    return [{"role": "user", "content": "Hello, world!"}]


def _json_response(status_code: int, payload: object) -> httpx2.Response:
    return httpx2.Response(
        status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )


def _text_response(status_code: int, text: str) -> httpx2.Response:
    return httpx2.Response(
        status_code,
        headers={"content-type": "text/plain"},
        content=text.encode("utf-8"),
    )


def _make_transport(handler) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)


def test_not_configured_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", None)
    client = LLMClient(api_key=None, base_url="https://example.com/v1")
    assert not client.is_configured


def test_blank_api_key_treated_as_unconfigured() -> None:
    client = LLMClient(api_key="   ", base_url="https://example.com/v1")
    assert not client.is_configured


def test_configured_with_api_key() -> None:
    client = _make_client()
    assert client.is_configured


def test_chat_without_api_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_api_key", None)
    client = LLMClient(api_key=None, base_url="https://example.com/v1")
    with pytest.raises(LLMNotConfiguredError):
        _run(client.chat(_chat_params()))


def test_successful_chat_returns_content() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return _json_response(
            200,
            {"choices": [{"message": {"role": "assistant", "content": "Hello back!"}}]},
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    result = _run(client.chat(_chat_params()))
    assert result == "Hello back!"
    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer test-key-123"
    assert captured["json"]["model"] == "test-model"
    assert captured["json"]["stream"] is False


def test_chat_sends_correct_request_shape() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    _run(
        client.chat(
            [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hi"},
            ],
            temperature=0.2,
            max_tokens=100,
        )
    )

    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer test-key-123"
    assert captured["json"]["model"] == "test-model"
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Hi"},
    ]
    assert captured["json"]["temperature"] == 0.2
    assert captured["json"]["max_tokens"] == 100
    assert captured["json"]["stream"] is False


def test_chat_json_returns_parsed_object() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {"choices": [{"message": {"role": "assistant", "content": '{"answer": 42}'}}]},
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    result = _run(client.chat_json(_chat_params()))
    assert result == {"answer": 42}


def test_chat_json_invalid_json_raises() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {"choices": [{"message": {"content": "not json"}}]},
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="not valid JSON"):
        _run(client.chat_json(_chat_params()))


def test_authentication_error_raises() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _text_response(401, "Unauthorized")

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMAuthenticationError):
        _run(client.chat(_chat_params()))


def test_forbidden_error_raises_authentication() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _text_response(403, "Forbidden")

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMAuthenticationError):
        _run(client.chat(_chat_params()))


def test_rate_limit_error_raises() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _text_response(429, "Too Many Requests")

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMRateLimitError):
        _run(client.chat(_chat_params()))


def test_server_error_raises_invalid_response() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _text_response(500, "Internal Server Error")

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError):
        _run(client.chat(_chat_params()))


def test_timeout_error_raises() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("read timeout")

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMTimeoutError):
        _run(client.chat(_chat_params()))


def test_network_error_raises() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.NetworkError("connection refused")

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMNetworkError):
        _run(client.chat(_chat_params()))


def test_invalid_json_response_raises() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _text_response(200, "not json at all")

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError):
        _run(client.chat(_chat_params()))


def test_missing_choices_raises() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(200, {"foo": "bar"})

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError):
        _run(client.chat(_chat_params()))


def test_empty_choices_raises() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(200, {"choices": []})

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError):
        _run(client.chat(_chat_params()))


def test_chat_json_content_block_list() -> None:
    """Content as a list of text blocks is concatenated and parsed."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": '{"answer": '},
                                {"type": "text", "text": '42}'},
                            ],
                        }
                    }
                ]
            },
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    result = _run(client.chat_json(_chat_params()))
    assert result == {"answer": 42}


def test_chat_content_null_raises() -> None:
    """Null content raises LLMInvalidResponseError."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200, {"choices": [{"message": {"role": "assistant", "content": None}}]}
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="content is empty"):
        _run(client.chat(_chat_params()))


def test_chat_content_empty_list_raises() -> None:
    """Empty content list raises LLMInvalidResponseError."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200, {"choices": [{"message": {"role": "assistant", "content": []}}]}
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="content is empty"):
        _run(client.chat(_chat_params()))


def test_chat_content_list_no_text_blocks_raises() -> None:
    """Content list without valid text blocks raises LLMInvalidResponseError."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "image_url", "image_url": {"url": "..."}},
                                {"type": "text"},
                            ],
                        }
                    }
                ],
            }
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="content is empty"):
        _run(client.chat(_chat_params()))


def test_chat_content_unexpected_type_raises() -> None:
    """Content that is a number or dict raises LLMInvalidResponseError."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200, {"choices": [{"message": {"role": "assistant", "content": 123}}]}
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="content is not a string"):
        _run(client.chat(_chat_params()))


def test_chat_content_tool_call_only_raises() -> None:
    """Content null with tool_calls present raises LLMInvalidResponseError."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "foo", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="tool calls but no text"):
        _run(client.chat(_chat_params()))



def test_finish_reason_read_from_choice_object() -> None:
    """finish_reason is read from choice object, not message."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": None,
                        },
                    }
                ]
            },
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="content is empty"):
        _run(client.chat(_chat_params()))


def test_empty_content_with_finish_reason_length_raises() -> None:
    """Empty content with finish_reason='length' raises with diagnostic metadata."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": None,
                        },
                    }
                ],
                "usage": {
                    "completion_tokens": 800,
                    "prompt_tokens": 500,
                },
            },
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="content is empty"):
        _run(client.chat_json(_chat_params(), max_tokens=800))


def test_empty_content_with_finish_reason_stop_raises() -> None:
    """Empty content with finish_reason='stop' raises with diagnostic metadata."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": None,
                        },
                    }
                ],
                "usage": {
                    "completion_tokens": 100,
                    "prompt_tokens": 300,
                },
            },
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="content is empty"):
        _run(client.chat(_chat_params()))


def test_empty_content_with_missing_finish_reason_raises() -> None:
    """Empty content with missing finish_reason raises with diagnostic metadata."""
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _json_response(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                        },
                    }
                ],
            },
        )

    transport = _make_transport(handler)
    client = _make_client(transport=transport)
    with pytest.raises(LLMInvalidResponseError, match="content is empty"):
        _run(client.chat(_chat_params()))


def test_all_exception_classes_inherit_from_llm_error() -> None:
    for exc in (
        LLMNotConfiguredError,
        LLMAuthenticationError,
        LLMRateLimitError,
        LLMTimeoutError,
        LLMNetworkError,
        LLMInvalidResponseError,
    ):
        assert issubclass(exc, LLMError)
