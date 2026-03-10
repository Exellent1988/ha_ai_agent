"""Anthropic Claude API client."""

import json
import logging

import aiohttp

from ..const import DEFAULT_REQUEST_TIMEOUT
from .base import BaseAIClient

_LOGGER = logging.getLogger(__name__)


class AnthropicClient(BaseAIClient):
    """Client for Anthropic Claude API."""

    def __init__(self, token, model="claude-sonnet-4-5-20250929"):
        self.token = token
        self.model = model
        self.api_url = "https://api.anthropic.com/v1/messages"

    async def get_response(self, messages, **kwargs):
        _LOGGER.debug("Making request to Anthropic API with model: %s", self.model)
        headers = {
            "x-api-key": self.token,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        system_message = None
        anthropic_messages = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                system_message = content
            elif role == "user":
                anthropic_messages.append({"role": "user", "content": content})
            elif role == "assistant":
                anthropic_messages.append({"role": "assistant", "content": content})
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0.7,
            "messages": anthropic_messages,
        }
        if system_message:
            payload["system"] = system_message
        _LOGGER.debug("Anthropic request payload: %s", json.dumps(payload, indent=2))
        timeout_sec = kwargs.get("timeout", DEFAULT_REQUEST_TIMEOUT)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_sec, connect=30),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    _LOGGER.error("Anthropic API error %d: %s", resp.status, error_text)
                    raise Exception(f"Anthropic API error {resp.status}")
                data = await resp.json()
                content_blocks = data.get("content", [])
                if content_blocks and isinstance(content_blocks, list):
                    for block in content_blocks:
                        if block.get("type") == "text":
                            return block.get("text", str(data))
                return str(data)
