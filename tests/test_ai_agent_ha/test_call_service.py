"""Tests for call_service allowlist validation."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    from custom_components.ai_agent_ha.agent import AiAgentHaAgent
    from custom_components.ai_agent_ha.const import ALLOWED_SERVICE_DOMAINS
    HOMEASSISTANT_AVAILABLE = True
except ImportError:
    HOMEASSISTANT_AVAILABLE = False


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.services.async_call = AsyncMock(return_value=None)
    hass.states.get = MagicMock(return_value=None)
    return hass


@pytest.fixture
def mock_config():
    return {
        "ai_provider": "ollama",
        "ollama_url": "http://localhost:11434",
        "models": {"ollama": "llama3.2"},
    }


class TestCallServiceAllowlist:
    """Test that call_service respects ALLOWED_SERVICE_DOMAINS."""

    @pytest.mark.asyncio
    async def test_allowed_domain_succeeds(self, mock_hass, mock_config):
        if not HOMEASSISTANT_AVAILABLE:
            pytest.skip("Home Assistant not available")
        agent = AiAgentHaAgent(mock_hass, mock_config, entry_id="test")
        result = await agent.call_service("light", "turn_on", {"entity_id": "light.test"})
        assert result.get("success") is True
        assert result.get("service") == "light.turn_on"
        mock_hass.services.async_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_disallowed_domain_returns_error(self, mock_hass, mock_config):
        if not HOMEASSISTANT_AVAILABLE:
            pytest.skip("Home Assistant not available")
        agent = AiAgentHaAgent(mock_hass, mock_config, entry_id="test")
        result = await agent.call_service("homeassistant", "restart")
        assert result.get("error") is not None
        assert "not allowed" in result["error"].lower()
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_log_blocked(self, mock_hass, mock_config):
        if not HOMEASSISTANT_AVAILABLE:
            pytest.skip("Home Assistant not available")
        agent = AiAgentHaAgent(mock_hass, mock_config, entry_id="test")
        result = await agent.call_service("system_log", "clear")
        assert result.get("error") is not None
        mock_hass.services.async_call.assert_not_called()

    def test_allowed_domains_include_common(self):
        if not HOMEASSISTANT_AVAILABLE:
            pytest.skip("Home Assistant not available")
        assert "light" in ALLOWED_SERVICE_DOMAINS
        assert "switch" in ALLOWED_SERVICE_DOMAINS
        assert "notify" in ALLOWED_SERVICE_DOMAINS
        assert "homeassistant" not in ALLOWED_SERVICE_DOMAINS
        assert "system_log" not in ALLOWED_SERVICE_DOMAINS
