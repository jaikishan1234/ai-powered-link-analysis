"""Asynchronous LLM client for the NVIDIA NIM Chat Completions API.

The client is independent of website extraction and future analysis logic.
It reads configuration from environment variables (via :mod:`app.core.config`)
and makes asynchronous requests to the NVIDIA NIM OpenAI-compatible Chat
Completions endpoint using :mod:`httpx2`, an existing project dependency.

No API keys are ever hardcoded; the key is read exclusively from the
``LLM_API_KEY`` environment variable (or ``.env`` file).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
import json
from typing import Any

import httpx2

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)

_CHAT_COMPLETIONS_PATH: str = "/chat/completions"
_JSON_CONTENT_TYPE: str = "application/json"


class LLMError(Exception):
    """Base class for LLM client failures."""


class LLMNotConfiguredError(LLMError):
    """No API key is configured; LLM calls are disabled."""


class LLMAuthenticationError(LLMError):
    """The API key was rejected (HTTP 401 or 403)."""


class LLMRateLimitError(LLMError):
    """The request was rate-limited (HTTP 429)."""


class LLMTimeoutError(LLMError):
    """The LLM request timed out."""


class LLMNetworkError(LLMError):
    """A network or transport failure occurred."""


class LLMInvalidResponseError(LLMError):
    """The LLM response was malformed or unexpected."""


def _resolve_api_key(configured: Settings) -> str | None:
    """Return the plaintext API key from settings, or ``None`` if unset."""
    if configured.llm_api_key is None:
        return None
    return configured.llm_api_key.get_secret_value()


class LLMClient:
    """Asynchronous client for the NVIDIA NIM Chat Completions API.

    Parameters default to the application settings when omitted. The client
    does not perform network I/O until :meth:`chat` or :meth:`chat_json` is
    called.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else _resolve_api_key(settings)
        )
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.llm_timeout_seconds
        )
        self._transport = transport

    @property
    def is_configured(self) -> bool:
        """Return whether a non-empty API key is configured."""
        return bool(self.api_key and self.api_key.strip())

    async def chat(
        self,
        messages: list[Mapping[str, str]],
        *,
        json_mode: bool = False,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant's text.

        ``messages`` is a list of mappings with ``role`` and ``content`` keys.
        When ``json_mode`` is true, the model is asked for JSON output; the
        raw content string is still returned (use :meth:`chat_json` for
        parsed JSON).

        Raises an :class:`LLMError` subclass for authentication, rate limit,
        timeout, network, and invalid-response failures.
        """
        if not self.is_configured:
            raise LLMNotConfiguredError(
                "LLM_API_KEY is not configured; set it in the environment."
            )

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        try:
            async with httpx2.AsyncClient(
                transport=self._transport,
                timeout=httpx2.Timeout(self.timeout_seconds),
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{self.base_url}{_CHAT_COMPLETIONS_PATH}",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": _JSON_CONTENT_TYPE,
                        "Accept": _JSON_CONTENT_TYPE,
                    },
                    json=body,
                )
        except httpx2.TimeoutException as exc:
            raise LLMTimeoutError("The LLM request timed out.") from exc
        except httpx2.NetworkError as exc:
            raise LLMNetworkError(f"Network error: {exc}") from exc
        except httpx2.HTTPError as exc:
            raise LLMNetworkError(f"HTTP error: {exc}") from exc
        except OSError as exc:
            raise LLMNetworkError(f"Network error: {exc}") from exc

        if response.status_code in (401, 403):
            raise LLMAuthenticationError("Invalid or missing API key.")
        if response.status_code == 429:
            raise LLMRateLimitError("LLM rate limit exceeded.")
        if response.status_code >= 400:
            raise LLMInvalidResponseError(
                f"LLM API returned HTTP {response.status_code}."
            )

        try:
            data: Any = response.json()
        except (ValueError, httpx2.DecodingError) as exc:
            raise LLMInvalidResponseError(
                f"LLM response is not valid JSON: {exc}"
            ) from exc

        try:
            choices = data["choices"]
            if not isinstance(choices, list) or not choices:
                raise LLMInvalidResponseError("LLM response has no choices.")
            choice = choices[0]
            message = choice.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            finish_reason = choice.get("finish_reason")
            tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
            is_tool_call_response = (
                finish_reason == "tool_calls"
                or (isinstance(tool_calls, list) and len(tool_calls) > 0)
            )

            if isinstance(content, str):
                text_content = content
            elif content is None:
                text_content = ""
            elif isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        block_text = block.get("text")
                        if isinstance(block_text, str):
                            parts.append(block_text)
                text_content = "".join(parts)
            else:
                raise LLMInvalidResponseError("LLM message content is not a string.")

            if not text_content:
                usage = data.get("usage") if isinstance(data, dict) else None
                completion_tokens = None
                if isinstance(usage, dict):
                    completion_tokens = usage.get("completion_tokens")
                refusal = message.get("refusal") if isinstance(message, dict) else None
                logger.error(
                    "LLM response empty content: finish_reason=%s, max_tokens=%s, "
                    "completion_tokens=%s, tool_calls_present=%s, refusal_present=%s",
                    finish_reason,
                    max_tokens,
                    completion_tokens,
                    tool_calls is not None and len(tool_calls) > 0,
                    refusal is not None,
                )
                if is_tool_call_response:
                    raise LLMInvalidResponseError(
                        "LLM response contains tool calls but no text content."
                    )
                raise LLMInvalidResponseError("LLM message content is empty.")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError(
                f"LLM response has unexpected structure: {exc}"
            ) from exc

        return text_content

    async def chat_json(
        self,
        messages: list[Mapping[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Any:
        """Send a chat completion request and return parsed JSON output.

        The model is asked to emit JSON via ``response_format``. The returned
        content is parsed with :func:`json.loads`; a parse failure raises
        :class:`LLMInvalidResponseError`.
        """
        content = await self.chat(
            messages,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError(
                f"LLM JSON-mode response is not valid JSON: {exc}"
            ) from exc
