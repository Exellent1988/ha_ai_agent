"""Generic local API client (Ollama-compatible and other local endpoints)."""

import json
import logging

import aiohttp

from ..const import DEFAULT_REQUEST_TIMEOUT
from ..util import sanitize_for_logging
from .base import BaseAIClient

_LOGGER = logging.getLogger(__name__)


class LocalClient(BaseAIClient):
    """Client for generic local API (e.g. Ollama /api/generate, other local servers)."""

    def __init__(self, url, model=""):
        self.url = url
        self.model = model

    async def get_response(self, messages, **kwargs):
        _LOGGER.debug(
            "Making request to local API with model: '%s' at URL: %s",
            self.model or "[NO MODEL SPECIFIED]",
            self.url,
        )

        if not self.model:
            _LOGGER.warning(
                "No model specified for local API request. Some APIs (like Ollama) require a model name."
            )
        headers = {"Content-Type": "application/json"}

        prompt = ""
        for message in messages:
            role = message.get("role", "")
            content = message.get("content", "")
            if role == "system":
                prompt += f"System: {content}\n\n"
            elif role == "user":
                prompt += f"User: {content}\n\n"
            elif role == "assistant":
                prompt += f"Assistant: {content}\n\n"
        prompt += "Assistant: "

        payload = {
            "prompt": prompt,
            "stream": False,
        }
        if self.model:
            payload["model"] = self.model

        _LOGGER.debug("Local API request payload: %s", json.dumps(payload, indent=2))

        if "model" not in payload or not payload["model"]:
            _LOGGER.warning(
                "Missing 'model' field in request to local API. This may cause issues with Ollama."
            )
        elif self.url and "ollama" in self.url.lower():
            _LOGGER.debug(
                "Detected Ollama URL, ensuring model is specified: %s",
                payload.get("model"),
            )

        timeout_sec = kwargs.get("timeout", DEFAULT_REQUEST_TIMEOUT)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_sec, connect=30),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    _LOGGER.error("Local API error %d: %s", resp.status, error_text)
                    if resp.status == 404:
                        if "model" in payload and payload["model"]:
                            raise Exception(
                                f"Model '{payload['model']}' not found. Please ensure the model is installed in Ollama using: ollama pull {payload['model']}"
                            )
                        raise Exception(
                            "Local API endpoint not found. Please check the URL and ensure Ollama is running."
                        )
                    if resp.status == 400:
                        raise Exception(f"Bad request to local API. Error: {error_text}")
                    raise Exception(f"Local API error {resp.status}: {error_text}")

                response_text = await resp.text()
                _LOGGER.debug("Local API response (first 200 chars): %s", response_text[:200])
                _LOGGER.debug(
                    "Local API response headers: %s",
                    sanitize_for_logging(dict(resp.headers)),
                )

                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError:
                    response_text = response_text.strip()
                    if response_text.startswith("{") and response_text.endswith("}"):
                        try:
                            parsed = json.loads(response_text)
                            if isinstance(parsed, dict) and "request_type" in parsed:
                                return response_text
                        except json.JSONDecodeError:
                            pass
                    return json.dumps({
                        "request_type": "final_response",
                        "response": response_text,
                    })

                if "response" in data:
                    content = data["response"] or ""
                    if not content or not content.strip():
                        if data.get("done_reason") == "load":
                            return json.dumps({
                                "request_type": "final_response",
                                "response": "The AI model is still loading. Please wait a moment and try again.",
                            })
                        if data.get("done") is False:
                            return json.dumps({
                                "request_type": "final_response",
                                "response": "The AI is still processing your request. Please try again.",
                            })
                        return json.dumps({
                            "request_type": "final_response",
                            "response": "The AI returned an empty response. Please try rephrasing your question.",
                        })
                    content = content.strip()
                    if content.startswith("{") and content.endswith("}"):
                        try:
                            p = json.loads(content)
                            if isinstance(p, dict) and "request_type" in p:
                                return content
                        except json.JSONDecodeError:
                            pass
                    return json.dumps({"request_type": "final_response", "response": content})

                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    content = choice.get("message", {}).get("content") or choice.get("text") or str(data)
                    content = (content or "").strip()
                    if content.startswith("{") and content.endswith("}"):
                        try:
                            p = json.loads(content)
                            if isinstance(p, dict) and "request_type" in p:
                                return content
                        except json.JSONDecodeError:
                            pass
                    return json.dumps({"request_type": "final_response", "response": content})

                if "content" in data:
                    content = (data["content"] or "").strip()
                    if content.startswith("{") and content.endswith("}"):
                        try:
                            p = json.loads(content)
                            if isinstance(p, dict) and "request_type" in p:
                                return content
                        except json.JSONDecodeError:
                            pass
                    return json.dumps({"request_type": "final_response", "response": content})

                if "message" in data:
                    msg = data["message"]
                    content = (msg.get("content") if isinstance(msg, dict) else str(msg)) or ""
                    if not content or not content.strip():
                        if data.get("done_reason") == "load":
                            return json.dumps({
                                "request_type": "final_response",
                                "response": "The AI model is still loading. Please wait a moment and try again.",
                            })
                        return json.dumps({
                            "request_type": "final_response",
                            "response": "The AI returned an empty response. Please try rephrasing your question.",
                        })
                    content = content.strip()
                    if content.startswith("{") and content.endswith("}"):
                        try:
                            p = json.loads(content)
                            if isinstance(p, dict) and "request_type" in p:
                                return content
                        except json.JSONDecodeError:
                            pass
                    return json.dumps({"request_type": "final_response", "response": content})

                if data.get("done_reason") == "load":
                    return json.dumps({
                        "request_type": "final_response",
                        "response": "The AI model is still loading. Please wait a moment and try again.",
                    })
                if data.get("done") is False:
                    return json.dumps({
                        "request_type": "final_response",
                        "response": "The AI is still processing your request. Please try again.",
                    })

                return json.dumps({
                    "request_type": "final_response",
                    "response": f"Received unexpected response format from local API: {str(data)}",
                })
