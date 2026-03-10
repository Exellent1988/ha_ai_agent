"""Shared utilities for AI Agent HA."""

from __future__ import annotations

from typing import Any


def sanitize_for_logging(data: Any, mask: str = "***REDACTED***") -> Any:
    """Sanitize sensitive data for safe logging.

    Recursively masks sensitive fields like API keys, tokens, passwords, etc.
    """
    sensitive_patterns = {
        "token", "key", "password", "secret", "credential", "auth",
        "authorization", "api_key", "apikey", "llama_token", "openai_token",
        "gemini_token", "anthropic_token", "openrouter_token", "alter_token",
        "zai_token",
    }
    if isinstance(data, dict):
        sanitized = {}
        for key, value in data.items():
            key_lower = str(key).lower()
            is_sensitive = any(p in key_lower for p in sensitive_patterns)
            sanitized[key] = mask if is_sensitive else sanitize_for_logging(value, mask)
        return sanitized
    if isinstance(data, list):
        return [sanitize_for_logging(item, mask) for item in data]
    if isinstance(data, tuple):
        return tuple(sanitize_for_logging(item, mask) for item in data)
    return data
