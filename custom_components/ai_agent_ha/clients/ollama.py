"""Ollama API client using /api/chat endpoint."""

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

import aiohttp

from ..const import DEFAULT_REQUEST_TIMEOUT
from .base import BaseAIClient

_LOGGER = logging.getLogger(__name__)


class OllamaClient(BaseAIClient):
    """Client for local Ollama API using /api/chat endpoint."""

    def __init__(self, base_url: str, model: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model or "llama3.2"
        self.chat_url = f"{self.base_url}/api/chat"
        self._last_streamed_response: Optional[str] = None

    async def get_response(self, messages, **kwargs):
        _LOGGER.debug(
            "Making request to Ollama /api/chat with model '%s' at %s",
            self.model,
            self.chat_url,
        )
        headers = {"Content-Type": "application/json"}

        ollama_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("system", "user", "assistant"):
                ollama_messages.append({"role": role, "content": content})

        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "keep_alive": "15m",
        }

        timeout_sec = kwargs.get("timeout", DEFAULT_REQUEST_TIMEOUT)
        session = kwargs.get("session")
        own_session = None
        if session is None:
            own_session = aiohttp.ClientSession()
            session = own_session
        try:
            async with session.post(
                self.chat_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_sec, connect=30),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    _LOGGER.error("Ollama API error %d: %s", resp.status, error_text)
                    if resp.status == 404:
                        raise Exception(
                            f"Model '{self.model}' nicht gefunden. "
                            f"Installiere es mit: ollama pull {self.model}"
                        )
                    raise Exception(f"Ollama API Fehler {resp.status}: {error_text}")

                data = await resp.json()
                msg = data.get("message", {})
                content = msg.get("content", "")

                if not content or not content.strip():
                    if data.get("done_reason") == "load":
                        return json.dumps(
                            {
                                "request_type": "final_response",
                                "response": "Das Modell wird noch geladen. Bitte kurz warten.",
                            }
                        )
                    return json.dumps(
                        {
                            "request_type": "final_response",
                            "response": "Leere Antwort vom Modell. Bitte erneut versuchen.",
                        }
                    )

                content = content.strip()
                if content.startswith("{") and content.endswith("}"):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict) and "request_type" in parsed:
                            return content
                    except json.JSONDecodeError:
                        pass

                return json.dumps(
                    {"request_type": "final_response", "response": content}
                )
        finally:
            if own_session:
                await own_session.close()

    async def get_response_stream(
        self, messages: List[Dict[str, Any]], **kwargs
    ) -> AsyncGenerator[str, None]:
        """Stream Ollama response chunks. Sets self._last_streamed_response when done."""
        headers = {"Content-Type": "application/json"}
        ollama_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("system", "user", "assistant"):
                ollama_messages.append({"role": role, "content": content})
        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": True,
            "keep_alive": "15m",
        }
        timeout_sec = kwargs.get("timeout", DEFAULT_REQUEST_TIMEOUT)
        session = kwargs.get("session")
        own_session = None
        if session is None:
            own_session = aiohttp.ClientSession()
            session = own_session
        full_content: List[str] = []
        try:
            async with session.post(
                self.chat_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_sec, connect=30),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    _LOGGER.error("Ollama API error %d: %s", resp.status, error_text)
                    if resp.status == 404:
                        raise Exception(
                            f"Model '{self.model}' nicht gefunden. "
                            f"Installiere es mit: ollama pull {self.model}"
                        )
                    raise Exception(f"Ollama API Fehler {resp.status}: {error_text}")
                while True:
                    line = await resp.content.readline()
                    if not line:
                        break
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue
                    try:
                        data = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue
                    msg = data.get("message", {})
                    chunk = msg.get("content", "")
                    if chunk:
                        full_content.append(chunk)
                        yield chunk
            content = "".join(full_content).strip()
            if not content:
                self._last_streamed_response = json.dumps(
                    {
                        "request_type": "final_response",
                        "response": "Leere Antwort vom Modell. Bitte erneut versuchen.",
                    }
                )
            else:
                self._last_streamed_response = json.dumps(
                    {"request_type": "final_response", "response": content}
                )
        finally:
            if own_session:
                await own_session.close()
