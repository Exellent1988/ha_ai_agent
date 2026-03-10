"""The AI Agent HA integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .agent import AiAgentHaAgent
from .config_flow import PROVIDERS as PROVIDER_LABELS
from .const import (
    CONF_LANGUAGE,
    CONF_REQUEST_TIMEOUT,
    CONF_SYSTEM_PROMPT,
    DEFAULT_LANGUAGE,
    DEFAULT_REQUEST_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Config schema - this integration only supports config entries
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@websocket_api.websocket_command({vol.Required("type"): "ai_agent_ha/providers"})
@callback
def _ws_get_configured_providers(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """WebSocket handler: return list of configured providers for the panel."""
    providers = []
    if DOMAIN in hass.data and hass.data[DOMAIN].get("agents"):
        titles = hass.data[DOMAIN].get("entry_titles") or {}
        providers = [
            {
                "value": pid,
                "label": titles.get(pid) or PROVIDER_LABELS.get(pid, pid),
            }
            for pid in hass.data[DOMAIN]["agents"].keys()
        ]
    connection.send_result(msg["id"], {"providers": providers})


# Define service schema to accept a custom prompt
SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("prompt"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the AI Agent HA component."""
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to new version."""
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    if entry.version == 1:
        # Add system_prompt and language to config if missing (from Store migration)
        new_data = dict(entry.data)
        if CONF_LANGUAGE not in new_data:
            new_data[CONF_LANGUAGE] = DEFAULT_LANGUAGE
        if CONF_SYSTEM_PROMPT not in new_data:
            new_data[CONF_SYSTEM_PROMPT] = ""
        if CONF_REQUEST_TIMEOUT not in new_data:
            new_data[CONF_REQUEST_TIMEOUT] = DEFAULT_REQUEST_TIMEOUT
        if new_data != entry.data:
            hass.config_entries.async_update_entry(entry, data=new_data)
        return True

    _LOGGER.info("Migration to version %s successful", entry.version)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AI Agent HA from a config entry."""
    try:
        # Handle version compatibility
        if not hasattr(entry, "version") or entry.version != 1:
            _LOGGER.warning(
                "Config entry has version %s, expected 1. Attempting compatibility mode.",
                getattr(entry, "version", "unknown"),
            )

        # Convert ConfigEntry to dict and ensure all required keys exist
        config_data = dict(entry.data)

        # Ensure backward compatibility - check for required keys
        if "ai_provider" not in config_data:
            _LOGGER.error(
                "Config entry missing required 'ai_provider' key. Entry data: %s",
                config_data,
            )
            raise ConfigEntryNotReady("Config entry missing required 'ai_provider' key")

        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {"agents": {}, "configs": {}, "entry_titles": {}}

        provider = config_data["ai_provider"]
        # Migrate legacy "local" provider to "ollama"
        if provider == "local":
            from urllib.parse import urlparse
            local_url = (config_data.get("local_url") or "").strip().rstrip("/")
            if "/api/" in local_url:
                base = local_url.split("/api/")[0]
            elif "/v1/" in local_url:
                base = local_url.split("/v1/")[0]
            else:
                base = local_url or "http://localhost:11434"
            new_data = dict(config_data)
            new_data["ai_provider"] = "ollama"
            new_data["ollama_url"] = base
            new_data.setdefault("models", {})["ollama"] = (config_data.get("models") or {}).get("local", "llama3.2")
            hass.config_entries.async_update_entry(entry, data=new_data)
            config_data = new_data
            provider = "ollama"

        entry_id = entry.entry_id
        # Label for UI: "Provider - Modellname" (e.g. "Ollama - qwen3:4b-instruct")
        model = (config_data.get("models") or {}).get(provider, "").strip()
        label = (
            f"{PROVIDER_LABELS.get(provider, provider)} - {model}"
            if model
            else PROVIDER_LABELS.get(provider, provider)
        )
        hass.data[DOMAIN].setdefault("entry_titles", {})[entry_id] = label

        # Validate provider (local removed; use ollama)
        if provider not in [
            "llama",
            "openai",
            "gemini",
            "openrouter",
            "anthropic",
            "alter",
            "zai",
            "ollama",
        ]:
            _LOGGER.error("Unknown AI provider: %s", provider)
            raise ConfigEntryNotReady(f"Unknown AI provider: {provider}")

        # Store config and agent by entry_id (allows multiple entries per provider)
        hass.data[DOMAIN]["configs"][entry_id] = config_data

        # Create agent for this entry
        _LOGGER.debug(
            "Creating AI agent for entry %s (provider %s) with config: %s",
            entry_id,
            provider,
            {
                k: v
                for k, v in config_data.items()
                if k
                not in [
                    "llama_token",
                    "openai_token",
                    "gemini_token",
                    "openrouter_token",
                    "anthropic_token",
                    "zai_token",
                ]
            },
        )
        hass.data[DOMAIN]["agents"][entry_id] = AiAgentHaAgent(
            hass, config_data, entry_id=entry_id
        )

        _LOGGER.info("Successfully set up AI Agent HA for entry %s (provider: %s)", entry_id, provider)

    except KeyError as err:
        _LOGGER.error("Missing required configuration key: %s", err)
        raise ConfigEntryNotReady(f"Missing required configuration key: {err}")
    except Exception as err:
        _LOGGER.exception("Unexpected error setting up AI Agent HA")
        raise ConfigEntryNotReady(f"Error setting up AI Agent HA: {err}")

    # Modify the query service handler to use the correct provider
    async def async_handle_query(call):
        """Handle the query service call."""
        try:
            # Check if agents are available
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                _LOGGER.error(
                    "No AI agents available. Please configure the integration first."
                )
                result = {"error": "No AI agents configured"}
                hass.bus.async_fire("ai_agent_ha_response", result)
                return

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                # Fallback: first available entry
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    _LOGGER.error("No AI agents available")
                    result = {"error": "No AI agents configured"}
                    hass.bus.async_fire("ai_agent_ha_response", result)
                    return
                entry_id = available[0]
                _LOGGER.debug("Using fallback entry: %s", entry_id)

            agent = hass.data[DOMAIN]["agents"][entry_id]
            user_id = call.context.user_id if call.context.user_id else "default"
            request_id = call.data.get("request_id") or ""
            result = await agent.process_query(
                call.data.get("prompt", ""),
                provider=entry_id,
                debug=call.data.get("debug", False),
                user_id=user_id,
                request_id=request_id or None,
            )
            if isinstance(result, dict):
                result["request_id"] = request_id
            hass.bus.async_fire("ai_agent_ha_response", result)
        except Exception as e:
            _LOGGER.error(f"Error processing query: {e}")
            result = {"error": str(e)}
            hass.bus.async_fire("ai_agent_ha_response", result)

    async def async_handle_create_automation(call):
        """Handle the create_automation service call."""
        try:
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                _LOGGER.error("No AI agents available.")
                hass.bus.async_fire("ai_agent_ha_automation_result", {"error": "No AI agents configured"})
                return

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    _LOGGER.error("No AI agents available")
                    hass.bus.async_fire("ai_agent_ha_automation_result", {"error": "No AI agents configured"})
                    return
                entry_id = available[0]
                _LOGGER.debug("Using fallback entry: %s", entry_id)

            agent = hass.data[DOMAIN]["agents"][entry_id]
            automation_data = call.data.get("automation", {})
            items = automation_data if isinstance(automation_data, list) else [automation_data]
            succeeded = []
            failed = []
            for item in items:
                r = await agent.create_automation(item)
                _LOGGER.debug("create_automation result for '%s': %s", item.get("alias", "?"), r)
                if r.get("error"):
                    failed.append({"alias": item.get("alias", "?"), "error": r["error"]})
                else:
                    succeeded.append({"alias": item.get("alias", "?"), "message": r.get("message", "OK")})
            hass.bus.async_fire("ai_agent_ha_automation_result", {
                "succeeded": succeeded,
                "failed": failed,
            })
        except Exception as e:
            _LOGGER.error("Error creating automation: %s", e, exc_info=True)
            hass.bus.async_fire("ai_agent_ha_automation_result", {"error": str(e)})

    async def async_handle_update_automation(call):
        """Handle the update_automation service call."""
        try:
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                _LOGGER.error(
                    "No AI agents available. Please configure the integration first."
                )
                return {"error": "No AI agents configured"}

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    _LOGGER.error("No AI agents available")
                    return {"error": "No AI agents configured"}
                entry_id = available[0]
                _LOGGER.debug("Using fallback entry: %s", entry_id)

            agent = hass.data[DOMAIN]["agents"][entry_id]
            automation_id_or_alias = call.data.get("automation_id_or_alias", "")
            automation_config = call.data.get("automation", {})
            if not automation_id_or_alias:
                return {"error": "automation_id_or_alias is required (id or alias)"}
            result = await agent.update_automation(
                automation_id_or_alias, automation_config
            )
            if result.get("error") and result.get("message"):
                return {"error": result["error"], "message": result["message"]}
            return result
        except Exception as e:
            _LOGGER.error(f"Error updating automation: {e}")
            return {"error": str(e), "message": str(e)}

    async def async_handle_save_prompt_history(call):
        """Handle the save_prompt_history service call."""
        try:
            # Check if agents are available
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                _LOGGER.error(
                    "No AI agents available. Please configure the integration first."
                )
                return {"error": "No AI agents configured"}

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    _LOGGER.error("No AI agents available")
                    return {"error": "No AI agents configured"}
                entry_id = available[0]
                _LOGGER.debug("Using fallback entry: %s", entry_id)

            agent = hass.data[DOMAIN]["agents"][entry_id]
            user_id = call.context.user_id if call.context.user_id else "default"
            result = await agent.save_user_prompt_history(
                user_id, call.data.get("history", [])
            )
            return result
        except Exception as e:
            _LOGGER.error(f"Error saving prompt history: {e}")
            return {"error": str(e)}

    async def async_handle_load_prompt_history(call):
        """Handle the load_prompt_history service call."""
        try:
            # Check if agents are available
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                _LOGGER.error(
                    "No AI agents available. Please configure the integration first."
                )
                return {"error": "No AI agents configured"}

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    _LOGGER.error("No AI agents available")
                    return {"error": "No AI agents configured"}
                entry_id = available[0]
                _LOGGER.debug("Using fallback entry: %s", entry_id)

            agent = hass.data[DOMAIN]["agents"][entry_id]
            user_id = call.context.user_id if call.context.user_id else "default"
            result = await agent.load_user_prompt_history(user_id)
            _LOGGER.debug("Load prompt history result: %s", result)
            return result
        except Exception as e:
            _LOGGER.error(f"Error loading prompt history: {e}")
            return {"error": str(e)}

    async def async_handle_save_system_prompt_settings(call):
        """Handle the save_system_prompt_settings service call."""
        try:
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                return {"error": "No AI agents configured"}

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    return {"error": "No AI agents configured"}
                entry_id = available[0]

            system_prompt = call.data.get("system_prompt")
            language = call.data.get("language")

            # Update config entry for persistence (match by entry_id)
            entries = hass.config_entries.async_entries(DOMAIN)
            for config_entry in entries:
                if config_entry.entry_id == entry_id:
                    new_data = dict(config_entry.data)
                    new_data[CONF_LANGUAGE] = (
                        (language or "").strip() or DEFAULT_LANGUAGE
                    )
                    new_data[CONF_SYSTEM_PROMPT] = (system_prompt or "").strip()
                    hass.config_entries.async_update_entry(config_entry, data=new_data)
                    if "configs" in hass.data[DOMAIN]:
                        hass.data[DOMAIN]["configs"][entry_id] = new_data
                    break

            agent = hass.data[DOMAIN]["agents"][entry_id]
            result = await agent.save_system_prompt_settings(
                system_prompt=system_prompt,
                language=language,
            )
            return result
        except Exception as e:
            _LOGGER.error(f"Error saving system prompt settings: {e}")
            return {"error": str(e)}

    async def async_handle_load_system_prompt_settings(call):
        """Handle the load_system_prompt_settings service call."""
        try:
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                return {"error": "No AI agents configured"}

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    return {"error": "No AI agents configured"}
                entry_id = available[0]

            agent = hass.data[DOMAIN]["agents"][entry_id]
            return await agent.load_system_prompt_settings()
        except Exception as e:
            _LOGGER.error(f"Error loading system prompt settings: {e}")
            return {"error": str(e)}

    async def async_handle_save_chat_history(call):
        """Handle the save_chat_history service call."""
        try:
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                _LOGGER.warning("save_chat_history: No AI agents configured")
                return

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    return
                entry_id = available[0]

            agent = hass.data[DOMAIN]["agents"][entry_id]
            user_id = call.context.user_id if call.context.user_id else "default"
            await agent.save_chat_history(
                user_id, call.data.get("messages", [])
            )
            _LOGGER.debug("Chat history saved for user %s, provider %s", user_id, entry_id)
        except Exception as e:
            _LOGGER.error("Error saving chat history: %s", e, exc_info=True)

    async def async_handle_load_chat_history(call):
        """Handle the load_chat_history service call."""
        try:
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                hass.bus.async_fire("ai_agent_ha_chat_history", {"messages": []})
                return

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    hass.bus.async_fire("ai_agent_ha_chat_history", {"messages": []})
                    return
                entry_id = available[0]

            agent = hass.data[DOMAIN]["agents"][entry_id]
            user_id = call.context.user_id if call.context.user_id else "default"
            result = await agent.load_chat_history(user_id)
            messages = result.get("messages", []) if isinstance(result, dict) else []
            _LOGGER.debug(
                "Chat history loaded for user %s, provider %s: %d messages",
                user_id, entry_id, len(messages),
            )
            hass.bus.async_fire("ai_agent_ha_chat_history", {"messages": messages})
        except Exception as e:
            _LOGGER.error("Error loading chat history: %s", e, exc_info=True)
            hass.bus.async_fire("ai_agent_ha_chat_history", {"messages": [], "error": str(e)})

    async def async_handle_get_configured_providers(call):
        """Return list of configured providers for the frontend (value + label from entry title)."""
        try:
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                return {"providers": []}
            titles = hass.data[DOMAIN].get("entry_titles") or {}
            providers = [
                {
                    "value": provider_id,
                    "label": titles.get(provider_id) or PROVIDER_LABELS.get(provider_id, provider_id),
                }
                for provider_id in hass.data[DOMAIN]["agents"].keys()
            ]
            return {"providers": providers}
        except Exception as e:
            _LOGGER.error("Error getting configured providers: %s", e)
            return {"providers": []}

    async def async_handle_create_dashboard(call):
        """Handle the create_dashboard service call."""
        try:
            # Check if agents are available
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                _LOGGER.error(
                    "No AI agents available. Please configure the integration first."
                )
                return {"error": "No AI agents configured"}

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    _LOGGER.error("No AI agents available")
                    return {"error": "No AI agents configured"}
                entry_id = available[0]
                _LOGGER.debug("Using fallback entry: %s", entry_id)

            agent = hass.data[DOMAIN]["agents"][entry_id]

            # Parse dashboard config if it's a string
            dashboard_config = call.data.get("dashboard_config", {})
            if isinstance(dashboard_config, str):
                try:
                    import json

                    dashboard_config = json.loads(dashboard_config)
                except json.JSONDecodeError as e:
                    _LOGGER.error(f"Invalid JSON in dashboard_config: {e}")
                    return {"error": f"Invalid JSON in dashboard_config: {e}"}

            result = await agent.create_dashboard(dashboard_config)
            return result
        except Exception as e:
            _LOGGER.error(f"Error creating dashboard: {e}")
            return {"error": str(e)}

    async def async_handle_update_dashboard(call):
        """Handle the update_dashboard service call."""
        try:
            # Check if agents are available
            if DOMAIN not in hass.data or not hass.data[DOMAIN].get("agents"):
                _LOGGER.error(
                    "No AI agents available. Please configure the integration first."
                )
                return {"error": "No AI agents configured"}

            entry_id = call.data.get("provider")
            if entry_id not in hass.data[DOMAIN]["agents"]:
                available = list(hass.data[DOMAIN]["agents"].keys())
                if not available:
                    _LOGGER.error("No AI agents available")
                    return {"error": "No AI agents configured"}
                entry_id = available[0]
                _LOGGER.debug("Using fallback entry: %s", entry_id)

            agent = hass.data[DOMAIN]["agents"][entry_id]

            # Parse dashboard config if it's a string
            dashboard_config = call.data.get("dashboard_config", {})
            if isinstance(dashboard_config, str):
                try:
                    import json

                    dashboard_config = json.loads(dashboard_config)
                except json.JSONDecodeError as e:
                    _LOGGER.error(f"Invalid JSON in dashboard_config: {e}")
                    return {"error": f"Invalid JSON in dashboard_config: {e}"}

            dashboard_url = call.data.get("dashboard_url", "")
            if not dashboard_url:
                return {"error": "Dashboard URL is required"}

            result = await agent.update_dashboard(dashboard_url, dashboard_config)
            return result
        except Exception as e:
            _LOGGER.error(f"Error updating dashboard: {e}")
            return {"error": str(e)}

    # Register services only for the first entry (avoid duplicate registration with multiple entries)
    if len(hass.data[DOMAIN]["agents"]) == 1:
        hass.services.async_register(DOMAIN, "query", async_handle_query)
        hass.services.async_register(
            DOMAIN, "create_automation", async_handle_create_automation
        )
        hass.services.async_register(
            DOMAIN, "update_automation", async_handle_update_automation
        )
        hass.services.async_register(
            DOMAIN, "save_prompt_history", async_handle_save_prompt_history
        )
        hass.services.async_register(
            DOMAIN, "load_prompt_history", async_handle_load_prompt_history
        )
        hass.services.async_register(
            DOMAIN, "save_system_prompt_settings", async_handle_save_system_prompt_settings
        )
        hass.services.async_register(
            DOMAIN, "load_system_prompt_settings", async_handle_load_system_prompt_settings
        )
        hass.services.async_register(
            DOMAIN, "save_chat_history", async_handle_save_chat_history
        )
        hass.services.async_register(
            DOMAIN, "load_chat_history", async_handle_load_chat_history
        )
        hass.services.async_register(
            DOMAIN,
            "get_configured_providers",
            async_handle_get_configured_providers,
        )
        hass.services.async_register(
            DOMAIN, "create_dashboard", async_handle_create_dashboard
        )
        hass.services.async_register(
            DOMAIN, "update_dashboard", async_handle_update_dashboard
        )

        # Register WebSocket command for provider list (frontend uses this if service not found)
        try:
            websocket_api.async_register_command(hass, _ws_get_configured_providers)
        except ValueError:
            pass  # already registered (multiple config entries)
    else:
        _LOGGER.debug("Services already registered by first entry, skipping")

    # Register static path for frontend
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/frontend/ai_agent_ha",
                hass.config.path("custom_components/ai_agent_ha/frontend"),
                False,
            )
        ]
    )

    # Panel registration only once (first entry); avoid "Overwriting panel" when multiple entries
    panel_name = "ai_agent_ha"
    try:
        if len(hass.data[DOMAIN]["agents"]) > 1:
            _LOGGER.debug("Panel already registered by first entry, skipping")
        elif await _panel_exists(hass, panel_name):
            _LOGGER.debug("AI Agent HA panel already exists, skipping registration")
        else:
            _LOGGER.debug("Registering AI Agent HA panel")
            async_register_built_in_panel(
                hass,
                component_name="custom",
                sidebar_title="AI Agent HA",
                sidebar_icon="mdi:robot",
                frontend_url_path=panel_name,
                require_admin=True,
                config={
                    "_panel_custom": {
                        "name": "ai_agent_ha-panel",
                        "module_url": "/frontend/ai_agent_ha/ai_agent_ha-panel.js",
                        "embed_iframe": False,
                    }
                },
            )
            _LOGGER.debug("AI Agent HA panel registered successfully")
    except Exception as e:
        _LOGGER.warning("Panel registration error: %s", str(e))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry. Only remove this entry's agent; remove services/panel only when no entries remain."""
    if DOMAIN not in hass.data:
        return True

    entry_id = entry.entry_id
    agent = hass.data[DOMAIN]["agents"].pop(entry_id, None)
    if agent and hasattr(agent, "async_close"):
        try:
            await agent.async_close()
        except Exception as e:
            _LOGGER.debug("Error closing agent session: %s", e)
    hass.data[DOMAIN]["configs"].pop(entry_id, None)
    hass.data[DOMAIN].get("entry_titles", {}).pop(entry_id, None)
    _LOGGER.debug("Unloaded entry: %s", entry_id)

    # Only remove services and panel when no agents left (last entry unloaded)
    if not hass.data[DOMAIN].get("agents"):
        try:
            from homeassistant.components.frontend import async_remove_panel

            async_remove_panel(hass, "ai_agent_ha")
            _LOGGER.debug("AI Agent HA panel removed")
        except Exception as e:
            _LOGGER.debug("Error removing panel: %s", str(e))

        hass.services.async_remove(DOMAIN, "query")
        hass.services.async_remove(DOMAIN, "create_automation")
        hass.services.async_remove(DOMAIN, "update_automation")
        hass.services.async_remove(DOMAIN, "save_prompt_history")
        hass.services.async_remove(DOMAIN, "load_prompt_history")
        hass.services.async_remove(DOMAIN, "save_system_prompt_settings")
        hass.services.async_remove(DOMAIN, "load_system_prompt_settings")
        hass.services.async_remove(DOMAIN, "save_chat_history")
        hass.services.async_remove(DOMAIN, "load_chat_history")
        hass.services.async_remove(DOMAIN, "get_configured_providers")
        hass.services.async_remove(DOMAIN, "create_dashboard")
        hass.services.async_remove(DOMAIN, "update_dashboard")

        hass.data.pop(DOMAIN)

    return True


async def _panel_exists(hass: HomeAssistant, panel_name: str) -> bool:
    """Check if a panel already exists."""
    try:
        return hasattr(hass.data, "frontend_panels") and panel_name in hass.data.get(
            "frontend_panels", {}
        )
    except Exception as e:
        _LOGGER.debug("Error checking panel existence: %s", str(e))
        return False
