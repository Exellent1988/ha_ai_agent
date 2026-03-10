"""Google Gemini API client."""

import json
import logging
from urllib.parse import quote

import aiohttp

from ..const import DEFAULT_REQUEST_TIMEOUT
from .base import BaseAIClient

_LOGGER = logging.getLogger(__name__)


class GeminiClient(BaseAIClient):
    """Client for Google Gemini API."""

    def __init__(self, token, model="gemini-2.5-flash"):
        self.token = (token or "").strip()
        self.model = model
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    async def get_response(self, messages, **kwargs):
        _LOGGER.debug("Making request to Gemini API with model: %s", self.model)
        if not self.token:
            raise Exception("Missing Gemini API key")
        headers = {"Content-Type": "application/json"}
        gemini_contents = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                if not gemini_contents:
                    gemini_contents.append({"role": "user", "parts": [{"text": f"System: {content}"}]})
                else:
                    gemini_contents.append({"role": "user", "parts": [{"text": f"System: {content}"}]})
            elif role == "user":
                gemini_contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                gemini_contents.append({"role": "model", "parts": [{"text": content}]})
        payload = {
            "contents": gemini_contents,
            "generationConfig": {"temperature": 0.7, "topP": 0.9},
        }
        url_with_key = f"{self.api_url}?key={quote(self.token)}"
        _LOGGER.debug("Gemini request payload: %s", json.dumps(payload, indent=2))
        timeout_sec = kwargs.get("timeout", DEFAULT_REQUEST_TIMEOUT)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url_with_key,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_sec, connect=30),
            ) as resp:
                response_text = await resp.text()
                _LOGGER.debug("Gemini API response status: %d", resp.status)
                if resp.status != 200:
                    _LOGGER.error("Gemini API error %d: %s", resp.status, response_text)
                    raise Exception(f"Gemini API error {resp.status}: {response_text}")
                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError as e:
                    _LOGGER.error("Failed to parse Gemini response: %s", str(e))
                    raise Exception(f"Invalid JSON response from Gemini: {response_text[:200]}")
                usage_metadata = data.get("usageMetadata", {})
                if usage_metadata:
                    _LOGGER.debug(
                        "Gemini token usage - prompt: %d, total: %d, thoughts: %d",
                        usage_metadata.get("promptTokenCount", 0),
                        usage_metadata.get("totalTokenCount", 0),
                        usage_metadata.get("thoughtsTokenCount", 0),
                    )
                candidates = data.get("candidates", [])
                if candidates and "content" in candidates[0]:
                    finish_reason = candidates[0].get("finishReason", "")
                    if finish_reason == "MAX_TOKENS":
                        _LOGGER.warning(
                            "Gemini response truncated due to MAX_TOKENS. Thoughts: %d",
                            usage_metadata.get("thoughtsTokenCount", 0),
                        )
                    parts = candidates[0]["content"].get("parts", [])
                    if parts:
                        content = parts[0].get("text", "")
                        if not content:
                            _LOGGER.warning("Gemini returned empty text content")
                        return content
                    _LOGGER.warning("Gemini response missing parts")
                else:
                    _LOGGER.warning("Gemini response missing expected structure")
                return str(data)
