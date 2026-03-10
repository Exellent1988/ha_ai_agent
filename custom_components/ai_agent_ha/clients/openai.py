"""OpenAI API client."""

import json
import logging

import aiohttp

from ..const import DEFAULT_REQUEST_TIMEOUT
from .base import BaseAIClient

_LOGGER = logging.getLogger(__name__)


class OpenAIClient(BaseAIClient):
    """Client for OpenAI API."""

    def __init__(self, token, model="gpt-3.5-turbo"):
        self.token = token
        self.model = model
        self.api_url = "https://api.openai.com/v1/chat/completions"

    def _is_restricted_model(self):
        restricted = ["o3-mini", "o3", "o1-mini", "o1-preview", "o1", "gpt-5"]
        return any(m in self.model.lower() for m in restricted)

    async def get_response(self, messages, **kwargs):
        _LOGGER.debug("Making request to OpenAI API with model: %s", self.model)
        if not self.token or not self.token.startswith("sk-"):
            raise Exception("Invalid OpenAI API key format")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        is_restricted = self._is_restricted_model()
        payload = {"model": self.model, "messages": messages}
        if not is_restricted:
            payload.update({"temperature": 0.7, "top_p": 0.9})
        _LOGGER.debug("OpenAI request payload: %s", json.dumps(payload, indent=2))
        timeout_sec = kwargs.get("timeout", DEFAULT_REQUEST_TIMEOUT)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_sec, connect=30),
            ) as resp:
                response_text = await resp.text()
                _LOGGER.debug("OpenAI API response status: %d", resp.status)
                if resp.status != 200:
                    _LOGGER.error("OpenAI API error %d: %s", resp.status, response_text)
                    raise Exception(f"OpenAI API error {resp.status}: {response_text}")
                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError as e:
                    _LOGGER.error("Failed to parse OpenAI response: %s", str(e))
                    raise Exception(f"Invalid JSON response from OpenAI: {response_text[:200]}")
                choices = data.get("choices", [])
                if choices and "message" in choices[0]:
                    content = choices[0]["message"].get("content", "")
                    if not content:
                        _LOGGER.warning("OpenAI returned empty content in message")
                    return content
                _LOGGER.warning("OpenAI response missing expected structure")
                return str(data)
