"""Llama API client."""

import json
import logging

import aiohttp

from ..const import DEFAULT_REQUEST_TIMEOUT
from .base import BaseAIClient

_LOGGER = logging.getLogger(__name__)


class LlamaClient(BaseAIClient):
    """Client for Llama API."""

    def __init__(self, token, model="Llama-4-Maverick-17B-128E-Instruct-FP8"):
        self.token = token
        self.model = model
        self.api_url = "https://api.llama.com/v1/chat/completions"

    async def get_response(self, messages, **kwargs):
        _LOGGER.debug("Making request to Llama API with model: %s", self.model)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.9,
        }
        _LOGGER.debug("Llama request payload: %s", json.dumps(payload, indent=2))
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
                    _LOGGER.error("Llama API error %d: %s", resp.status, error_text)
                    raise Exception(f"Llama API error {resp.status}")
                data = await resp.json()
                completion = data.get("completion_message", {})
                content = completion.get("content", {})
                return content.get("text", str(data))
