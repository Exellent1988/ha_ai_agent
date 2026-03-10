"""The AI Agent implementation with multiple provider support.

Example config:
ai_agent_ha:
  ai_provider: openai  # or 'llama', 'gemini', 'openrouter', 'anthropic', 'alter', 'zai', 'ollama'
  llama_token: "..."
  openai_token: "..."
  gemini_token: "..."
  openrouter_token: "..."
  anthropic_token: "..."
  alter_token: "..."
  zai_token: "..."
  zai_endpoint: "general"  # or 'coding' for z.ai (3× usage, 1/7 cost)
  local_url: "http://localhost:11434/api/generate"  # Required for local models
  ollama_url: "http://localhost:11434"  # For Ollama (local)
  # Model configuration (optional, defaults will be used if not specified)
  models:
    openai: "gpt-3.5-turbo"  # or "gpt-4", "gpt-4-turbo", etc.
    llama: "Llama-4-Maverick-17B-128E-Instruct-FP8"
    gemini: "gemini-2.5-flash"  # or "gemini-2.5-pro", "gemini-2.0-flash", etc.
    openrouter: "openai/gpt-4o"  # or any model available on OpenRouter
    anthropic: "claude-sonnet-4-5-20250929"  # or "claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229", etc.
    alter: "your-model-name"  # model name for Alter API
    zai: "glm-4.7"  # model name for z.ai API (glm-4.7, glm-4.6, glm-4.5, etc.)
    local: "llama3.2"  # model name for local API (optional if your API doesn't require it)
    ollama: "llama3.2"  # model name for Ollama
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ALLOWED_SERVICE_DOMAINS,
    CONF_LANGUAGE,
    CONF_REQUEST_TIMEOUT,
    CONF_SYSTEM_PROMPT,
    CONF_WEATHER_ENTITY,
    DEFAULT_LANGUAGE,
    DEFAULT_REQUEST_TIMEOUT,
    DOMAIN,
    MAX_PROMPT_CHARS,
    normalize_url,
)

_LOGGER = logging.getLogger(__name__)

MAX_STORED_MESSAGES = 100  # Cap conversation history length per user

from .util import sanitize_for_logging
from .clients import (
    AlterClient,
    AnthropicClient,
    BaseAIClient,
    GeminiClient,
    LlamaClient,
    LocalClient,
    OllamaClient,
    OpenAIClient,
    OpenRouterClient,
    ZaiClient,
)
from . import data_requests
from . import automation
from . import dashboard


# (Client classes live in .clients)


# === Main Agent ===
class AiAgentHaAgent:
    """Agent for handling queries with dynamic data requests and multiple AI providers."""

    SYSTEM_PROMPT = {
        "role": "system",
        "content": (
            "You are an AI assistant integrated with Home Assistant.\n"
            "You can request specific data by using only these commands:\n"
            "- get_entity_state(entity_id): Get state of a specific entity\n"
            "- get_entities_by_domain(domain): Get all entities in a domain\n"
            "- get_entities_by_device_class(device_class, domain?): Get entities with specific device_class (e.g., 'temperature', 'humidity', 'motion')\n"
            "- get_climate_related_entities(): Get all climate-related entities (climate.* entities + temperature/humidity sensors)\n"
            "- get_entities_by_area(area_id): Get all entities in a specific area\n"
            "- get_entities(area_id or area_ids): Get entities by area(s) - supports single area_id or list of area_ids\n"
            "  Use as: get_entities(area_ids=['area1', 'area2']) for multiple areas or get_entities(area_id='single_area')\n"
            "- get_calendar_events(entity_id?): Get calendar events\n"
            "- get_automations(): Get all automations\n"
            "- get_weather_data(): Get current weather and forecast data\n"
            "- get_entity_registry(): Get entity registry entries (now includes device_class, state_class, unit_of_measurement)\n"
            "- get_device_registry(): Get device registry entries\n"
            "- get_area_registry(): Get room/area information\n"
            "- get_history(entity_id, hours): Get historical state changes\n"
            "- get_person_data(): Get person tracking information\n"
            "- get_statistics(entity_id): Get sensor statistics\n"
            "- get_scenes(): Get scene configurations\n"
            "- get_dashboards(): Get list of all dashboards\n"
            "- get_dashboard_config(dashboard_url): Get configuration of a specific dashboard\n"
            "- set_entity_state(entity_id, state, attributes?): Set state of an entity (e.g., turn on/off lights, open/close covers)\n"
            "- call_service(domain, service, target?, service_data?): Call any Home Assistant service directly\n"
            "- create_automation(automation): Create a new automation with the provided configuration\n"
            "- update_automation(automation_id_or_alias, automation): Update an existing automation (id e.g. automation.xxx, or alias from get_automations)\n"
            "- create_dashboard(dashboard_config): Create a new dashboard with the provided configuration\n"
            "- update_dashboard(dashboard_url, dashboard_config): Update an existing dashboard configuration\n\n"
            "IMPORTANT DEVICE_CLASS AND DOMAIN GUIDANCE:\n"
            "- Many sensors have a 'device_class' attribute (temperature, humidity, motion, etc.)\n"
            "- For robot vacuums (Staubsaugerroboter, vacuum cleaners): use get_entities_by_domain('vacuum')\n"
            "- Use get_climate_related_entities() for climate dashboards (includes climate.* entities and temperature/humidity sensors)\n"
            "- Use get_entities_by_device_class(device_class) to filter by device_class (e.g., 'temperature', 'humidity', 'motion')\n"
            "- When the user says entities are wrong or 'nicht meine Entitäten': REQUEST the correct entities with data_request. For vacuums use domain 'vacuum'. NEVER put a data_request inside final_response - always use data_request directly.\n"
            "- For climate dashboards, use history-graph and gauge cards for temperature/humidity sensors\n\n"
            "DASHBOARD CREATION:\n"
            "When a user asks to create a dashboard:\n"
            "1. Gather entities using get_climate_related_entities() or other get_* commands\n"
            "2. Respond with JSON using request_type: 'dashboard_suggestion' (NEVER use 'final_response'!)\n"
            "3. Use Lovelace JSON format (NOT YAML!)\n"
            "4. Example response structure:\n"
            '{"request_type": "dashboard_suggestion", "message": "Dashboard created", "dashboard": {"title": "...", "views": [...]}}\n'
            "5. Do NOT include YAML, markdown, or code blocks - only pure JSON\n\n"
            "IMPORTANT AREA/FLOOR GUIDANCE:\n"
            "- When users ask for entities from a specific floor, use get_area_registry() first\n"
            "- Areas have both 'area_id' and 'floor_id' - these are different concepts\n"
            "- Filter areas by their floor_id to find all areas on a specific floor\n"
            "- Use get_entities() with area_ids parameter to get entities from multiple areas efficiently\n"
            "- Example: get_entities(area_ids=['area1', 'area2', 'area3']) for multiple areas at once\n"
            "- This is more efficient than calling get_entities_by_area() multiple times\n\n"
            "AUTOMATION CREATION:\n"
            "When creating automations, request entities first to know the entity IDs.\n"
            "For days, use: ['fri', 'mon', 'sat', 'sun', 'thu', 'tue', 'wed']\n"
            "To update an existing automation (e.g. user says 'add error state' or 'also when error'), use get_automations to find it, then use update_automation(automation_id_or_alias, automation_config) with the full updated config.\n\n"
            "RESPONSE FORMATS - You must ALWAYS respond with valid JSON:\n\n"
            "For automations:\n"
            "{\n"
            '  "request_type": "automation_suggestion",\n'
            '  "message": "I\'ve created an automation that might help you. Would you like me to create it?",\n'
            '  "automation": {\n'
            '    "alias": "Name of the automation",\n'
            '    "description": "Description of what the automation does",\n'
            '    "trigger": [...],  // Array of trigger conditions\n'
            '    "condition": [...], // Optional array of conditions\n'
            '    "action": [...]     // Array of actions to perform\n'
            "  }\n"
            "}\n\n"
            "For dashboards (WHEN USER ASKS TO CREATE A DASHBOARD):\n"
            "{\n"
            '  "request_type": "dashboard_suggestion",\n'
            '  "message": "Description of the dashboard you created",\n'
            '  "dashboard": {\n'
            '    "title": "Dashboard Title",\n'
            '    "url_path": "url-path",\n'
            '    "icon": "mdi:icon-name",\n'
            '    "show_in_sidebar": true,\n'
            '    "views": [{\n'
            '      "title": "View Title",\n'
            '      "cards": [...]\n'
            "    }]\n"
            "  }\n"
            "}\n\n"
            "For data requests, use this exact JSON format:\n"
            "{\n"
            '  "request_type": "data_request",\n'
            '  "request": "command_name",\n'
            '  "parameters": {...}\n'
            "}\n"
            'For get_entities with multiple areas: {"request_type": "get_entities", "parameters": {"area_ids": ["area1", "area2"]}}\n'
            'For get_entities with single area: {"request_type": "get_entities", "parameters": {"area_id": "single_area"}}\n\n'
            "For service calls, use this exact JSON format:\n"
            "{\n"
            '  "request_type": "call_service",\n'
            '  "domain": "light",\n'
            '  "service": "turn_on",\n'
            '  "target": {"entity_id": ["entity1", "entity2"]},\n'
            '  "service_data": {"brightness": 255}\n'
            "}\n\n"
            "For answering questions (NOT creating dashboards/automations):\n"
            "{\n"
            '  "request_type": "final_response",\n'
            '  "response": "your answer to the user"\n'
            "}\n\n"
            "IMPORTANT: Use 'dashboard_suggestion' when creating dashboards, NOT 'final_response'!\n\n"
            "CRITICAL FORMATTING RULES:\n"
            "- You must ALWAYS respond with ONLY a valid JSON object\n"
            "- DO NOT include any text before the JSON\n"
            "- DO NOT include any text after the JSON\n"
            "- DO NOT include explanations or descriptions outside the JSON\n"
            "- Your entire response must be parseable as JSON\n"
            "- Use the 'message' field inside the JSON for user-facing text\n"
            "- NEVER mix regular text with JSON in your response\n\n"
            "WRONG: 'I'll create this for you. {\"request_type\": ...}'\n"
            'CORRECT: \'{"request_type": "dashboard_suggestion", "message": "I\'ll create this for you.", ...}\''
        ),
    }

    SYSTEM_PROMPT_LOCAL = {
        "role": "system",
        "content": (
            "You are an AI assistant integrated with Home Assistant.\n"
            "You can request specific data by using only these commands:\n"
            "- get_entity_state(entity_id): Get state of a specific entity\n"
            "- get_entities_by_domain(domain): Get all entities in a domain\n"
            "- get_entities_by_device_class(device_class, domain?): Get entities with specific device_class (e.g., 'temperature', 'humidity', 'motion')\n"
            "- get_climate_related_entities(): Get all climate-related entities (climate.* entities + temperature/humidity sensors)\n"
            "- get_entities_by_area(area_id): Get all entities in a specific area\n"
            "- get_entities(area_id or area_ids): Get entities by area(s) - supports single area_id or list of area_ids\n"
            "  Use as: get_entities(area_ids=['area1', 'area2']) for multiple areas or get_entities(area_id='single_area')\n"
            "- get_calendar_events(entity_id?): Get calendar events\n"
            "- get_automations(): Get all automations\n"
            "- get_weather_data(): Get current weather and forecast data\n"
            "- get_entity_registry(): Get entity registry entries (now includes device_class, state_class, unit_of_measurement)\n"
            "- get_device_registry(): Get device registry entries\n"
            "- get_area_registry(): Get room/area information\n"
            "- get_history(entity_id, hours): Get historical state changes\n"
            "- get_person_data(): Get person tracking information\n"
            "- get_statistics(entity_id): Get sensor statistics\n"
            "- get_scenes(): Get scene configurations\n"
            "- get_dashboards(): Get list of all dashboards\n"
            "- get_dashboard_config(dashboard_url): Get configuration of a specific dashboard\n"
            "- set_entity_state(entity_id, state, attributes?): Set state of an entity (e.g., turn on/off lights, open/close covers)\n"
            "- call_service(domain, service, target?, service_data?): Call any Home Assistant service directly\n"
            "- create_automation(automation): Create a new automation with the provided configuration\n"
            "- update_automation(automation_id_or_alias, automation): Update an existing automation (id e.g. automation.xxx, or alias from get_automations)\n"
            "- create_dashboard(dashboard_config): Create a new dashboard with the provided configuration\n"
            "- update_dashboard(dashboard_url, dashboard_config): Update an existing dashboard configuration\n\n"
            "IMPORTANT DEVICE_CLASS AND DOMAIN GUIDANCE:\n"
            "- Many sensors have a 'device_class' attribute (temperature, humidity, motion, etc.)\n"
            "- For robot vacuums (Staubsaugerroboter, vacuum cleaners): use get_entities_by_domain('vacuum')\n"
            "- Use get_climate_related_entities() for climate dashboards (includes climate.* entities and temperature/humidity sensors)\n"
            "- Use get_entities_by_device_class(device_class) to filter by device_class (e.g., 'temperature', 'humidity', 'motion')\n"
            "- When the user says entities are wrong or 'nicht meine Entitäten': REQUEST the correct entities with data_request. For vacuums use domain 'vacuum'. NEVER put a data_request inside final_response - always use data_request directly.\n"
            "- For climate dashboards, use history-graph and gauge cards for temperature/humidity sensors\n\n"
            "DASHBOARD CREATION:\n"
            "When a user asks to create a dashboard:\n"
            "1. Gather entities using get_climate_related_entities() or other get_* commands\n"
            "2. Respond with JSON using request_type: 'dashboard_suggestion' (NEVER use 'final_response'!)\n"
            "3. Use Lovelace JSON format (NOT YAML!)\n"
            "4. Example response structure:\n"
            '{"request_type": "dashboard_suggestion", "message": "Dashboard created", "dashboard": {"title": "...", "views": [...]}}\n'
            "5. Do NOT include YAML, markdown, or code blocks - only pure JSON\n\n"
            "IMPORTANT AREA/FLOOR GUIDANCE:\n"
            "- When users ask for entities from a specific floor, use get_area_registry() first\n"
            "- Areas have both 'area_id' and 'floor_id' - these are different concepts\n"
            "- Filter areas by their floor_id to find all areas on a specific floor\n"
            "- Use get_entities() with area_ids parameter to get entities from multiple areas efficiently\n"
            "- Example: get_entities(area_ids=['area1', 'area2', 'area3']) for multiple areas at once\n"
            "- This is more efficient than calling get_entities_by_area() multiple times\n\n"
            "AUTOMATION CREATION:\n"
            "When creating automations, request entities first to know the entity IDs.\n"
            "For days, use: ['fri', 'mon', 'sat', 'sun', 'thu', 'tue', 'wed']\n"
            "To update an existing automation (e.g. user says 'add error state' or 'also when error'), use get_automations to find it, then use update_automation(automation_id_or_alias, automation_config) with the full updated config.\n\n"
            "RESPONSE FORMATS - You must ALWAYS respond with valid JSON:\n\n"
            "For automations:\n"
            "{\n"
            '  "request_type": "automation_suggestion",\n'
            '  "message": "I\'ve created an automation that might help you. Would you like me to create it?",\n'
            '  "automation": {\n'
            '    "alias": "Name of the automation",\n'
            '    "description": "Description of what the automation does",\n'
            '    "trigger": [...],  // Array of trigger conditions\n'
            '    "condition": [...], // Optional array of conditions\n'
            '    "action": [...]     // Array of actions to perform\n'
            "  }\n"
            "}\n\n"
            "For dashboards (WHEN USER ASKS TO CREATE A DASHBOARD):\n"
            "{\n"
            '  "request_type": "dashboard_suggestion",\n'
            '  "message": "Description of the dashboard you created",\n'
            '  "dashboard": {\n'
            '    "title": "Dashboard Title",\n'
            '    "url_path": "url-path",\n'
            '    "icon": "mdi:icon-name",\n'
            '    "show_in_sidebar": true,\n'
            '    "views": [{\n'
            '      "title": "View Title",\n'
            '      "cards": [...]\n'
            "    }]\n"
            "  }\n"
            "}\n\n"
            "For data requests, use this exact JSON format:\n"
            "{\n"
            '  "request_type": "data_request",\n'
            '  "request": "command_name",\n'
            '  "parameters": {...}\n'
            "}\n"
            'For get_entities with multiple areas: {"request_type": "get_entities", "parameters": {"area_ids": ["area1", "area2"]}}\n'
            'For get_entities with single area: {"request_type": "get_entities", "parameters": {"area_id": "single_area"}}\n\n'
            "For service calls, use this exact JSON format:\n"
            "{\n"
            '  "request_type": "call_service",\n'
            '  "domain": "light",\n'
            '  "service": "turn_on",\n'
            '  "target": {"entity_id": ["entity1", "entity2"]},\n'
            '  "service_data": {"brightness": 255}\n'
            "}\n\n"
            "For answering questions (NOT creating dashboards/automations):\n"
            "{\n"
            '  "request_type": "final_response",\n'
            '  "response": "your answer to the user"\n'
            "}\n\n"
            "IMPORTANT: Use 'dashboard_suggestion' when creating dashboards, NOT 'final_response'!\n\n"
            "CRITICAL FORMATTING RULES:\n"
            "- You must ALWAYS respond with ONLY a valid JSON object\n"
            "- DO NOT include any text before the JSON\n"
            "- DO NOT include any text after the JSON\n"
            "- DO NOT include explanations or descriptions outside the JSON\n"
            "- Your entire response must be parseable as JSON\n"
            "- Use the 'message' field inside the JSON for user-facing text\n"
            "- NEVER mix regular text with JSON in your response\n\n"
            "WRONG: 'I'll create this for you. {\"request_type\": ...}'\n"
            'CORRECT: \'{"request_type": "dashboard_suggestion", "message": "I\'ll create this for you.", ...}\''
        ),
    }

    # Shorter system prompt for local/Ollama to reduce token usage and stay under context limits
    SYSTEM_PROMPT_LOCAL_SHORT = {
        "role": "system",
        "content": (
            "You are a Home Assistant AI. Reply ONLY with valid JSON, no text before or after.\n\n"
            "Commands: get_entity_state(entity_id), get_entities_by_domain(domain), get_entities_by_device_class(device_class, domain?), "
            "get_climate_related_entities(), get_entities_by_area(area_id), get_entities(area_id='x' or area_ids=['a','b']), "
            "get_automations(), get_weather_data(), get_entity_registry(), get_device_registry(), get_area_registry(), get_history(entity_id, hours), "
            "get_person_data(), get_scenes(), get_dashboards(), get_dashboard_config(url), set_entity_state(...), call_service(domain, service, target?, service_data?), "
            "create_automation(automation), update_automation(id_or_alias, automation), create_dashboard(dashboard_config), update_dashboard(url, config).\n"
            "Vacuums: get_entities_by_domain('vacuum'). Wrong entities: reply with data_request to fetch correct ones.\n"
            "Days: ['fri','mon','sat','sun','thu','tue','wed']. Automations: request entities first for real entity_ids.\n\n"
            "JSON formats:\n"
            "Data: {\"request_type\":\"data_request\",\"request\":\"cmd\",\"parameters\":{...}}\n"
            "Automation: {\"request_type\":\"automation_suggestion\",\"message\":\"...\",\"automation\":{\"alias\",\"description\",\"trigger\",\"condition\",\"action\"}}\n"
            "Dashboard: {\"request_type\":\"dashboard_suggestion\",\"message\":\"...\",\"dashboard\":{\"title\",\"url_path\",\"views\":[...]}}\n"
            "Answer: {\"request_type\":\"final_response\",\"response\":\"...\"}\n"
            "Service: {\"request_type\":\"call_service\",\"domain\",\"service\",\"target\",\"service_data\"}\n\n"
            "Rules: One JSON object only. User-facing text in \"message\" or \"response\". Dashboards → dashboard_suggestion, not final_response."
        ),
    }

    def __init__(
        self,
        hass: HomeAssistant,
        config: Dict[str, Any],
        entry_id: Optional[str] = None,
    ):
        """Initialize the agent with provider selection."""
        self.hass = hass
        self.config = config
        self._entry_id = entry_id or "default"
        self._user_conversations: Dict[str, List[Dict[str, Any]]] = {}
        self._user_locks: Dict[str, asyncio.Lock] = {}
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._max_cache_size = 200
        self.ai_client: BaseAIClient
        self._cache_timeout = 300  # 5 minutes
        self._max_retries = 5
        self._retry_delay = 1  # seconds (base for exponential backoff)
        self._rate_limit = 60  # requests per minute
        self._last_request_time = 0
        self._request_count = 0
        self._request_window_start = time.time()
        self._session: Optional[aiohttp.ClientSession] = None

        provider = config.get("ai_provider", "openai")
        models_config = config.get("models", {})

        _LOGGER.debug("Initializing AiAgentHaAgent with provider: %s", provider)
        _LOGGER.debug("Models config loaded: %s", models_config)

        # Set the appropriate system prompt based on provider (can be overridden by custom prompt from Store)
        if provider == "ollama":
            self._default_system_prompt = self.SYSTEM_PROMPT_LOCAL_SHORT
            _LOGGER.debug("Using short local system prompt (saves context for Ollama)")
        else:
            self._default_system_prompt = self.SYSTEM_PROMPT
            _LOGGER.debug("Using standard system prompt")
        self.system_prompt = self._default_system_prompt
        self._custom_system_prompt: Optional[str] = None
        self._language: Optional[str] = None
        self._settings_loaded = False

        # Load system prompt and language from config (backend model configuration)
        config_lang = config.get(CONF_LANGUAGE)
        config_prompt = config.get(CONF_SYSTEM_PROMPT)
        if config_lang is not None:
            self._language = str(config_lang).strip() or DEFAULT_LANGUAGE
        if config_prompt is not None:
            prompt_str = str(config_prompt).strip()
            self._custom_system_prompt = prompt_str if prompt_str else None
        self._apply_system_prompt()

        # Initialize the appropriate AI client with model selection
        if provider == "openai":
            model = models_config.get("openai", "gpt-3.5-turbo")
            self.ai_client = OpenAIClient(config.get("openai_token"), model)
        elif provider == "gemini":
            model = models_config.get("gemini", "gemini-2.5-flash")
            self.ai_client = GeminiClient(config.get("gemini_token"), model)
        elif provider == "openrouter":
            model = models_config.get("openrouter", "openai/gpt-4o")
            self.ai_client = OpenRouterClient(config.get("openrouter_token"), model)
        elif provider == "anthropic":
            model = models_config.get("anthropic", "claude-sonnet-4-5-20250929")
            self.ai_client = AnthropicClient(config.get("anthropic_token"), model)
        elif provider == "alter":
            model = models_config.get("alter", "")
            self.ai_client = AlterClient(config.get("alter_token"), model)
        elif provider == "zai":
            model = models_config.get("zai", "glm-4.7")
            endpoint_type = config.get("zai_endpoint", "general")
            self.ai_client = ZaiClient(config.get("zai_token"), model, endpoint_type)
        elif provider == "ollama":
            model = models_config.get("ollama", "llama3.2")
            base_url = normalize_url(config.get("ollama_url"))
            if not base_url:
                _LOGGER.error("Missing ollama_url for Ollama provider")
                raise Exception("Missing ollama_url configuration for Ollama provider")
            self.ai_client = OllamaClient(base_url, model)
        else:  # default to llama if somehow specified
            model = models_config.get("llama", "Llama-4-Maverick-17B-128E-Instruct-FP8")
            self.ai_client = LlamaClient(config.get("llama_token"), model)

        _LOGGER.debug(
            "AiAgentHaAgent initialized successfully with provider: %s, model: %s",
            provider,
            model,
        )

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        """Return the asyncio lock for the given user (creates if needed)."""
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    def _get_conversation(self, user_id: str) -> List[Dict[str, Any]]:
        """Return the conversation history list for the given user (creates if needed)."""
        return self._user_conversations.setdefault(user_id, [])

    @staticmethod
    def _build_provider_config(models_config: Dict[str, Any]) -> Dict[str, Any]:
        """Single source of truth for provider config (token keys, defaults, client classes)."""
        return {
            "openai": {
                "token_key": "openai_token",
                "model": models_config.get("openai", "gpt-3.5-turbo"),
                "client_class": OpenAIClient,
            },
            "gemini": {
                "token_key": "gemini_token",
                "model": models_config.get("gemini", "gemini-1.5-flash"),
                "client_class": GeminiClient,
            },
            "openrouter": {
                "token_key": "openrouter_token",
                "model": models_config.get("openrouter", "openai/gpt-4o"),
                "client_class": OpenRouterClient,
            },
            "llama": {
                "token_key": "llama_token",
                "model": models_config.get(
                    "llama", "Llama-4-Maverick-17B-128E-Instruct-FP8"
                ),
                "client_class": LlamaClient,
            },
            "anthropic": {
                "token_key": "anthropic_token",
                "model": models_config.get(
                    "anthropic", "claude-sonnet-4-5-20250929"
                ),
                "client_class": AnthropicClient,
            },
            "alter": {
                "token_key": "alter_token",
                "model": models_config.get("alter", ""),
                "client_class": AlterClient,
            },
            "zai": {
                "token_key": "zai_token",
                "model": models_config.get("zai", ""),
                "client_class": ZaiClient,
            },
            "ollama": {
                "token_key": "ollama_url",
                "model": models_config.get("ollama", "llama3.2"),
                "client_class": OllamaClient,
            },
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return shared aiohttp ClientSession (connection pooling); create if needed."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=5)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def async_close(self) -> None:
        """Close shared ClientSession (call on entry unload)."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _validate_api_key(self) -> bool:
        """Validate the API key format."""
        provider = self.config.get("ai_provider", "openai")

        if provider == "openai":
            token = self.config.get("openai_token")
        elif provider == "gemini":
            token = self.config.get("gemini_token")
        elif provider == "openrouter":
            token = self.config.get("openrouter_token")
        elif provider == "anthropic":
            token = self.config.get("anthropic_token")
        elif provider == "alter":
            token = self.config.get("alter_token")
        elif provider == "zai":
            token = self.config.get("zai_token")
        elif provider == "ollama":
            token = self.config.get("ollama_url")
        else:
            token = self.config.get("llama_token")

        if not token or not isinstance(token, str):
            return False

        # For local and ollama providers, validate URL format
        if provider == "ollama":
            return bool(token.startswith(("http://", "https://")))

        # Add more specific validation based on your API key format
        return len(token) >= 32

    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits."""
        current_time = time.time()
        if current_time - self._request_window_start >= 60:
            self._request_count = 0
            self._request_window_start = current_time

        if self._request_count >= self._rate_limit:
            return False

        self._request_count += 1
        return True

    def _get_cached_data(self, key: str) -> Optional[Any]:
        """Get data from cache if it's still valid (LRU: move to end on access)."""
        if key not in self._cache:
            return None
        timestamp, data = self._cache.pop(key)
        if time.time() - timestamp < self._cache_timeout:
            self._cache[key] = (timestamp, data)
            return data
        return None

    def _set_cached_data(self, key: str, data: Any) -> None:
        """Store data in cache with timestamp; evict oldest if over MAX_CACHE_SIZE."""
        self._cache[key] = (time.time(), data)
        while len(self._cache) > self._max_cache_size:
            self._cache.popitem(last=False)

    async def _load_settings(self) -> None:
        """Load system prompt and language from config or Store (migration)."""
        if self._settings_loaded:
            return
        # Config (from backend model configuration) takes precedence
        config_lang = self.config.get(CONF_LANGUAGE)
        config_prompt = self.config.get(CONF_SYSTEM_PROMPT)
        if config_lang is not None or config_prompt is not None:
            if config_lang is not None:
                self._language = str(config_lang).strip() or DEFAULT_LANGUAGE
            if config_prompt is not None:
                prompt_str = str(config_prompt).strip()
                self._custom_system_prompt = prompt_str if prompt_str else None
            self._apply_system_prompt()
        else:
            # Migration: load from Store for old configs; fall back to config for language
            provider = self.config.get("ai_provider", "openai")
            store: Store = Store(self.hass, 1, f"ai_agent_ha_settings_{provider}")
            try:
                data = await store.async_load()
                if data:
                    self._custom_system_prompt = data.get(CONF_SYSTEM_PROMPT)
                    self._language = (
                        data.get(CONF_LANGUAGE)
                        or self.config.get(CONF_LANGUAGE)
                        or DEFAULT_LANGUAGE
                    )
                    if self._language:
                        self._language = str(self._language).strip() or DEFAULT_LANGUAGE
                    self._apply_system_prompt()
                else:
                    self._language = (
                        str(self.config.get(CONF_LANGUAGE) or DEFAULT_LANGUAGE).strip()
                        or DEFAULT_LANGUAGE
                    )
                    self._apply_system_prompt()
            except Exception as e:
                _LOGGER.debug("Could not load settings from Store: %s", e)
                self._language = (
                    str(self.config.get(CONF_LANGUAGE) or DEFAULT_LANGUAGE).strip()
                    or DEFAULT_LANGUAGE
                )
                self._apply_system_prompt()
        self._settings_loaded = True

    def _apply_system_prompt(self) -> None:
        """Apply custom system prompt and language to self.system_prompt."""
        base_content: str
        if self._custom_system_prompt and self._custom_system_prompt.strip():
            base_content = self._custom_system_prompt.strip()
        else:
            base_content = self._default_system_prompt.get("content", "")
        lang = (self._language or "").strip()
        if lang:
            lang_instruction = (
                f"LANGUAGE: Always respond in {lang}. "
                "All user-facing text (messages, responses, explanations) must be in this language.\n\n"
            )
            base_content = lang_instruction + base_content
            base_content = base_content.rstrip() + f"\n\nRespond only in {lang}."
        self.system_prompt = {"role": "system", "content": base_content}

    async def _execute_data_request(
        self, request_type: str, parameters: Dict[str, Any]
    ) -> Optional[Union[Dict[str, Any], List[Dict[str, Any]]]]:
        """Execute a data request and return the result."""
        try:
            if request_type == "get_entity_state":
                return await data_requests.get_entity_state(
                    self.hass, parameters.get("entity_id")
                )
            if request_type == "get_entities_by_domain":
                return await data_requests.get_entities_by_domain(
                    self.hass, parameters.get("domain")
                )
            if request_type == "get_entities_by_area":
                return await data_requests.get_entities_by_area(
                    self.hass, parameters.get("area_id")
                )
            if request_type == "get_entities":
                return await data_requests.get_entities(
                    self.hass,
                    area_id=parameters.get("area_id"),
                    area_ids=parameters.get("area_ids"),
                )
            if request_type == "get_entities_by_device_class":
                return await data_requests.get_entities_by_device_class(
                    self.hass,
                    parameters.get("device_class"),
                    parameters.get("domain"),
                )
            if request_type == "get_climate_related_entities":
                return await data_requests.get_climate_related_entities(self.hass)
            if request_type == "get_calendar_events":
                return await data_requests.get_calendar_events(
                    self.hass, parameters.get("entity_id")
                )
            if request_type == "get_automations":
                return await data_requests.get_automations(self.hass)
            if request_type == "get_entity_registry":
                return await data_requests.get_entity_registry(self.hass)
            if request_type == "get_device_registry":
                return await data_requests.get_device_registry(self.hass)
            if request_type == "get_weather_data":
                return await data_requests.get_weather_data(self.hass)
            if request_type == "get_area_registry":
                return await data_requests.get_area_registry(self.hass)
            if request_type == "get_history":
                return await data_requests.get_history(
                    self.hass,
                    parameters.get("entity_id"),
                    parameters.get("hours", 24),
                )
            if request_type == "get_person_data":
                return await data_requests.get_person_data(self.hass)
            if request_type == "get_statistics":
                return await data_requests.get_statistics(
                    self.hass, parameters.get("entity_id")
                )
            if request_type == "get_scenes":
                return await data_requests.get_scenes(self.hass)
            if request_type == "get_dashboards":
                return await data_requests.get_dashboards(self.hass)
            if request_type == "get_dashboard_config":
                return await data_requests.get_dashboard_config(
                    self.hass, parameters.get("dashboard_url")
                )
            _LOGGER.warning("Unknown request type: %s", request_type)
            return {"error": f"Unknown request type: {request_type}"}
        except Exception as e:
            _LOGGER.exception("Error executing data request: %s", str(e))
            return {"error": str(e)}

    async def get_entity_state(self, entity_id: str) -> Dict[str, Any]:
        """Get the state of a specific entity."""
        return await data_requests.get_entity_state(self.hass, entity_id)

    async def get_entities_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        """Get all entities for a specific domain."""
        return await data_requests.get_entities_by_domain(self.hass, domain)

    async def get_entities_by_device_class(
        self, device_class: str, domain: str = None
    ) -> List[Dict[str, Any]]:
        """Get all entities with a specific device_class."""
        return await data_requests.get_entities_by_device_class(
            self.hass, device_class, domain
        )

    async def get_climate_related_entities(self) -> List[Dict[str, Any]]:
        """Get all climate-related entities (climate.* + temp/humidity sensors)."""
        return await data_requests.get_climate_related_entities(self.hass)

    async def get_entities_by_area(self, area_id: str) -> List[Dict[str, Any]]:
        """Get all entities for a specific area."""
        return await data_requests.get_entities_by_area(self.hass, area_id)

    async def get_entities(self, area_id=None, area_ids=None) -> List[Dict[str, Any]]:
        """Get entities by area(s)."""
        return await data_requests.get_entities(
            self.hass, area_id=area_id, area_ids=area_ids
        )

    async def get_calendar_events(
        self, entity_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get calendar events, optionally filtered by entity_id."""
        return await data_requests.get_calendar_events(self.hass, entity_id)

    async def get_automations(self) -> List[Dict[str, Any]]:
        """Get all automations."""
        return await data_requests.get_automations(self.hass)

    async def get_entity_registry(self) -> List[Dict]:
        """Get entity registry entries with device_class and other metadata."""
        return await data_requests.get_entity_registry(self.hass)

    async def get_device_registry(self) -> List[Dict]:
        """Get device registry entries."""
        return await data_requests.get_device_registry(self.hass)

    async def get_history(self, entity_id: str, hours: int = 24) -> List[Dict]:
        """Get historical state changes for an entity."""
        return await data_requests.get_history(self.hass, entity_id, hours)

    async def get_area_registry(self) -> Dict[str, Any]:
        """Get area registry information."""
        return await data_requests.get_area_registry(self.hass)

    async def get_person_data(self) -> List[Dict]:
        """Get person tracking information."""
        return await data_requests.get_person_data(self.hass)

    async def get_statistics(self, entity_id: str) -> Dict:
        """Get statistics for an entity."""
        return await data_requests.get_statistics(self.hass, entity_id)

    async def get_scenes(self) -> List[Dict]:
        """Get scene configurations."""
        return await data_requests.get_scenes(self.hass)

    async def get_weather_data(self) -> Dict[str, Any]:
        """Get weather data from any available weather entity."""
        return await data_requests.get_weather_data(self.hass)

    async def create_automation(
        self, automation_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new automation with validation and sanitization."""
        result = await automation.create_automation(self.hass, automation_config)
        if result.get("success"):
            self._cache.clear()
        return result

    async def update_automation(
        self,
        automation_id_or_alias: str,
        automation_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update an existing automation by id (e.g. automation.xxx or xxx) or alias."""
        result = await automation.update_automation(
            self.hass, automation_id_or_alias, automation_config
        )
        if result.get("success"):
            self._cache.clear()
        return result

    async def get_dashboards(self) -> List[Dict[str, Any]]:
        """Get list of all dashboards."""
        return await data_requests.get_dashboards(self.hass)

    async def get_dashboard_config(
        self, dashboard_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get configuration of a specific dashboard."""
        return await data_requests.get_dashboard_config(self.hass, dashboard_url)

    async def create_dashboard(
        self, dashboard_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new dashboard using Home Assistant's Lovelace configuration."""
        return await dashboard.create_dashboard(self.hass, dashboard_config)


    async def update_dashboard(
        self, dashboard_url: str, dashboard_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update an existing dashboard using Home Assistant's Lovelace configuration."""
        return await dashboard.update_dashboard(
            self.hass, dashboard_url, dashboard_config
        )

    async def process_query(
        self,
        user_query: str,
        provider: Optional[str] = None,
        debug: bool = False,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process a user query with input validation and rate limiting."""
        uid = user_id or "default"
        if not user_query or not isinstance(user_query, str):
            return {"success": False, "error": "Invalid query format"}

        async with self._get_user_lock(uid):
            try:
                await self._load_settings()
                await self.load_chat_history(uid)

                # Get the correct configuration for the requested provider
                # 'provider' can be an entry_id (from UI) or a provider type (ollama, llama, …)
                if provider and provider in self.hass.data[DOMAIN]["configs"]:
                    config = self.hass.data[DOMAIN]["configs"][provider]
                    selected_provider = config.get("ai_provider", "llama")
                else:
                    config = self.config
                    selected_provider = provider or config.get("ai_provider", "llama")

                _LOGGER.debug(
                    "Processing query with provider/entry: %s (ai_provider: %s)",
                    provider,
                    selected_provider,
                )
                # Log sanitized config (masks all tokens/keys for security)
                _LOGGER.debug(
                    f"Using config: {json.dumps(sanitize_for_logging(config), default=str)}"
                )
                models_config = config.get("models", {})
                provider_config = self._build_provider_config(models_config)

                # Validate provider and get configuration
                if selected_provider not in provider_config:
                    _LOGGER.warning(
                        f"Invalid provider {selected_provider}, falling back to llama"
                    )
                    selected_provider = "llama"

                provider_settings = provider_config[selected_provider]
                token = config.get(provider_settings["token_key"])

                def _with_debug(result: Dict[str, Any]) -> Dict[str, Any]:
                    """Attach a sanitized trace when UI requests debug info."""
                    if debug and "debug" not in result:
                        result["debug"] = self._build_debug_trace(
                            uid,
                            selected_provider,
                            provider_settings,
                            config.get("zai_endpoint", "general"),
                        )
                    return result

                # Validate token/URL
                if not token:
                    error_msg = f"No {'URL' if selected_provider == 'ollama' else 'token'} configured for provider {selected_provider}"
                    _LOGGER.error(error_msg)
                    return _with_debug({"success": False, "error": error_msg})

                # Initialize client
                try:
                    if selected_provider == "zai":
                        # ZaiClient takes (token, model, endpoint_type)
                        endpoint_type = config.get("zai_endpoint", "general")
                        self.ai_client = provider_settings["client_class"](
                            token=token,
                            model=provider_settings["model"],
                            endpoint_type=endpoint_type,
                        )
                        _LOGGER.debug(
                            f"Initialized {selected_provider} client with model {provider_settings['model']}, endpoint_type {endpoint_type}"
                        )
                    elif selected_provider == "ollama":
                        # OllamaClient takes (base_url, model)
                        self.ai_client = provider_settings["client_class"](
                            base_url=token, model=provider_settings["model"]
                        )
                        _LOGGER.debug(
                            f"Initialized {selected_provider} client with model {provider_settings['model']}"
                        )
                    else:
                        # Other clients take (token, model)
                        self.ai_client = provider_settings["client_class"](
                            token=token, model=provider_settings["model"]
                        )
                        _LOGGER.debug(
                            f"Initialized {selected_provider} client with model {provider_settings['model']}"
                        )
                except Exception as e:
                    error_msg = f"Error initializing {selected_provider} client: {str(e)}"
                    _LOGGER.error(error_msg)
                    return _with_debug({"success": False, "error": error_msg})

                # Process the query with rate limiting and retries
                if not self._check_rate_limit():
                    return _with_debug(
                        {
                            "success": False,
                            "error": "Rate limit exceeded. Please wait before trying again.",
                        }
                    )

                # Sanitize user input
                user_query = user_query.strip()[:1000]  # Limit length and trim whitespace

                _LOGGER.debug("Processing new query: %s", user_query)

                # Check cache for identical query
                cache_key = f"query_{hash(user_query)}_{provider}_{debug}"
                cached_result = self._get_cached_data(cache_key)
                if cached_result:
                    return (
                        dict(cached_result)
                        if isinstance(cached_result, dict)
                        else {"error": "Invalid cached result"}
                    )

                # Add system message to conversation if it's the first message
                conv = self._get_conversation(uid)
                if not conv:
                    _LOGGER.debug("Adding system message to new conversation")
                    conv.append(self.system_prompt)

                # Add user query to conversation
                conv.append({"role": "user", "content": user_query})
                _LOGGER.debug("Added user query to conversation history")

                max_iterations = 5  # Prevent infinite loops
                iteration = 0

                while iteration < max_iterations:
                    iteration += 1
                    _LOGGER.debug(f"Processing iteration {iteration} of {max_iterations}")

                    try:
                        _LOGGER.debug("Requesting response from AI provider")
                        response = await self._get_ai_response(uid, request_id)
                        _LOGGER.debug("Received response from AI provider: %s", response)

                        # Try to parse the response as JSON with simplified approach
                        response_clean = response.strip()

                        # Remove potential BOM and other invisible characters
                        import codecs

                        if response_clean.startswith(codecs.BOM_UTF8.decode("utf-8")):
                            response_clean = response_clean[1:]

                        # Remove other common invisible characters
                        invisible_chars = [
                            "\ufeff",
                            "\u200b",
                            "\u200c",
                            "\u200d",
                            "\u2060",
                        ]
                        for char in invisible_chars:
                            response_clean = response_clean.replace(char, "")

                        _LOGGER.debug(
                            "Cleaned response length: %d", len(response_clean)
                        )
                        _LOGGER.debug(
                            "Cleaned response first 100 chars: %s", response_clean[:100]
                        )
                        _LOGGER.debug(
                            "Cleaned response last 100 chars: %s", response_clean[-100:]
                        )

                        # Simple strategy: try to parse the cleaned response directly
                        response_data = None
                        try:
                            _LOGGER.debug("Attempting basic JSON parse...")
                            response_data = json.loads(response_clean)
                            _LOGGER.debug("Basic JSON parse succeeded!")
                        except json.JSONDecodeError as e:
                            _LOGGER.warning("Basic JSON parse failed: %s", str(e))
                            _LOGGER.debug("JSON error position: %d", e.pos)
                            if e.pos < len(response_clean):
                                _LOGGER.debug(
                                    "Character at error position: %s (ord: %d)",
                                    repr(response_clean[e.pos]),
                                    ord(response_clean[e.pos]),
                                )
                                _LOGGER.debug(
                                    "Context around error: %s",
                                    repr(
                                        response_clean[max(0, e.pos - 10) : e.pos + 10]
                                    ),
                                )

                            # Fallback: try to extract JSON by finding the first { and last }
                            json_start = response_clean.find("{")
                            json_end = response_clean.rfind("}")

                            if (
                                json_start != -1
                                and json_end != -1
                                and json_end > json_start
                            ):
                                json_part = response_clean[json_start : json_end + 1]
                                _LOGGER.debug(
                                    "Trying fallback extraction from pos %d to %d",
                                    json_start,
                                    json_end,
                                )
                                _LOGGER.debug("Extracted JSON: %s", json_part[:200])

                                try:
                                    response_data = json.loads(json_part)
                                    _LOGGER.debug("Fallback JSON extraction succeeded!")
                                except json.JSONDecodeError as e2:
                                    _LOGGER.warning(
                                        "Fallback JSON extraction also failed: %s",
                                        str(e2),
                                    )
                                    raise e  # Re-raise the original error
                            else:
                                _LOGGER.warning(
                                    "Could not find JSON boundaries in response"
                                )
                                raise e  # Re-raise the original error

                        if response_data is None:
                            raise json.JSONDecodeError(
                                "All parsing strategies failed", response_clean, 0
                            )

                        _LOGGER.debug("Successfully parsed JSON response")
                        _LOGGER.debug(
                            "Parsed response type: %s",
                            response_data.get("request_type", "unknown"),
                        )

                        # Check if this is a data request (either format)
                        data_request_types = [
                            "get_entity_state",
                            "get_entities_by_domain",
                            "get_entities_by_device_class",
                            "get_climate_related_entities",
                            "get_entities_by_area",
                            "get_entities",
                            "get_calendar_events",
                            "get_automations",
                            "get_entity_registry",
                            "get_device_registry",
                            "get_weather_data",
                            "get_area_registry",
                            "get_history",
                            "get_person_data",
                            "get_statistics",
                            "get_scenes",
                            "get_dashboards",
                            "get_dashboard_config",
                            "set_entity_state",
                            "create_automation",
                            "update_automation",
                            "create_dashboard",
                            "update_dashboard",
                        ]

                        if (
                            response_data.get("request_type") == "data_request"
                            or response_data.get("request_type") in data_request_types
                        ):
                            # Handle data request (both standard format and direct request type)
                            if response_data.get("request_type") == "data_request":
                                request_type = response_data.get("request")
                            else:
                                request_type = response_data.get("request_type")
                            parameters = response_data.get("parameters", {})
                            _LOGGER.debug(
                                "Processing data request: %s with parameters: %s",
                                request_type,
                                json.dumps(parameters),
                            )

                            # Add AI's response to conversation history
                            conv.append(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        response_data
                                    ),  # Store clean JSON
                                }
                            )

                            # Get requested data
                            data: Union[Dict[str, Any], List[Dict[str, Any]]]
                            if request_type == "get_entity_state":
                                data = await self.get_entity_state(
                                    parameters.get("entity_id")
                                )
                            elif request_type == "get_entities_by_domain":
                                data = await self.get_entities_by_domain(
                                    parameters.get("domain")
                                )
                            elif request_type == "get_entities_by_area":
                                data = await self.get_entities_by_area(
                                    parameters.get("area_id")
                                )
                            elif request_type == "get_entities":
                                data = await self.get_entities(
                                    area_id=parameters.get("area_id"),
                                    area_ids=parameters.get("area_ids"),
                                )
                            elif request_type == "get_entities_by_device_class":
                                data = await self.get_entities_by_device_class(
                                    parameters.get("device_class"),
                                    parameters.get("domain"),
                                )
                            elif request_type == "get_climate_related_entities":
                                data = await self.get_climate_related_entities()
                            elif request_type == "get_calendar_events":
                                data = await self.get_calendar_events(
                                    parameters.get("entity_id")
                                )
                            elif request_type == "get_automations":
                                data = await self.get_automations()
                            elif request_type == "get_entity_registry":
                                data = await self.get_entity_registry()
                            elif request_type == "get_device_registry":
                                data = await self.get_device_registry()
                            elif request_type == "get_weather_data":
                                data = await self.get_weather_data()
                            elif request_type == "get_area_registry":
                                data = await self.get_area_registry()
                            elif request_type == "get_history":
                                data = await self.get_history(
                                    parameters.get("entity_id"),
                                    parameters.get("hours", 24),
                                )
                            elif request_type == "get_person_data":
                                data = await self.get_person_data()
                            elif request_type == "get_statistics":
                                data = await self.get_statistics(
                                    parameters.get("entity_id")
                                )
                            elif request_type == "get_scenes":
                                data = await self.get_scenes()
                            elif request_type == "get_dashboards":
                                data = await self.get_dashboards()
                            elif request_type == "get_dashboard_config":
                                data = await self.get_dashboard_config(
                                    parameters.get("dashboard_url")
                                )
                            elif request_type == "set_entity_state":
                                data = await self.set_entity_state(
                                    parameters.get("entity_id"),
                                    parameters.get("state"),
                                    parameters.get("attributes"),
                                )
                            elif request_type == "create_automation":
                                data = await self.create_automation(
                                    parameters.get("automation")
                                )
                            elif request_type == "update_automation":
                                data = await self.update_automation(
                                    parameters.get("automation_id_or_alias", ""),
                                    parameters.get("automation", {}),
                                )
                            elif request_type == "create_dashboard":
                                data = await self.create_dashboard(
                                    parameters.get("dashboard_config")
                                )
                            elif request_type == "update_dashboard":
                                data = await self.update_dashboard(
                                    parameters.get("dashboard_url"),
                                    parameters.get("dashboard_config"),
                                )
                            else:
                                data = {
                                    "error": f"Unknown request type: {request_type}"
                                }
                                _LOGGER.warning(
                                    "Unknown request type: %s", request_type
                                )

                            # Check if any data request resulted in an error
                            if isinstance(data, dict) and "error" in data:
                                return _with_debug(
                                    {"success": False, "error": data["error"]}
                                )
                            elif isinstance(data, list) and any(
                                "error" in item
                                for item in data
                                if isinstance(item, dict)
                            ):
                                errors = [
                                    item["error"]
                                    for item in data
                                    if isinstance(item, dict) and "error" in item
                                ]
                                return _with_debug(
                                    {"success": False, "error": "; ".join(errors)}
                                )

                            _LOGGER.debug(
                                "Retrieved data for request: %s",
                                json.dumps(data, default=str),
                            )

                            # Add data to conversation as a user message (not system to avoid overwriting system prompt in Anthropic API)
                            conv.append(
                                {
                                    "role": "user",
                                    "content": json.dumps({"data": data}, default=str),
                                }
                            )
                            continue

                        elif response_data.get("request_type") == "final_response":
                            # Check if AI mistakenly put a data_request inside response (parse and process)
                            response_text = response_data.get("response", "")
                            nested_processed = False
                            if (
                                response_text
                                and response_text.strip().startswith("{")
                                and "request_type" in response_text
                            ):
                                try:
                                    nested = json.loads(response_text)
                                    if nested.get("request_type") == "data_request":
                                        _LOGGER.debug(
                                            "Extracting nested data_request from final_response"
                                        )
                                        request_type = nested.get("request")
                                        parameters = nested.get("parameters", {})
                                        conv.append(
                                            {
                                                "role": "assistant",
                                                "content": json.dumps(nested),
                                            }
                                        )
                                        data = await self._execute_data_request(
                                            request_type, parameters
                                        )
                                        conv.append(
                                            {
                                                "role": "user",
                                                "content": json.dumps(
                                                    {"data": data}, default=str
                                                ),
                                            }
                                        )
                                        nested_processed = True
                                        continue
                                    if nested.get("request_type") == "automation_suggestion":
                                        _LOGGER.debug(
                                            "Extracting nested automation_suggestion from final_response"
                                        )
                                        conv.append(
                                            {
                                                "role": "assistant",
                                                "content": json.dumps(nested),
                                            }
                                        )
                                        result = {
                                            "success": True,
                                            "answer": json.dumps(nested),
                                        }
                                        result = _with_debug(result)
                                        self._set_cached_data(cache_key, result)
                                        return result
                                    if nested.get("request_type") == "dashboard_suggestion":
                                        _LOGGER.debug(
                                            "Extracting nested dashboard_suggestion from final_response"
                                        )
                                        conv.append(
                                            {
                                                "role": "assistant",
                                                "content": json.dumps(nested),
                                            }
                                        )
                                        result = {
                                            "success": True,
                                            "answer": json.dumps(nested),
                                        }
                                        result = _with_debug(result)
                                        self._set_cached_data(cache_key, result)
                                        return result
                                except (json.JSONDecodeError, TypeError):
                                    pass

                            if nested_processed:
                                continue

                            # Add final response to conversation history
                            conv.append(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        response_data
                                    ),  # Store clean JSON
                                }
                            )

                            # Return final response
                            _LOGGER.debug(
                                "Received final response: %s",
                                response_data.get("response"),
                            )
                            result = {
                                "success": True,
                                "answer": response_data.get("response", ""),
                            }
                            result = _with_debug(result)
                            self._set_cached_data(cache_key, result)
                            return result
                        elif (
                            response_data.get("request_type") == "automation_suggestion"
                        ):
                            # Add automation suggestion to conversation history
                            conv.append(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        response_data
                                    ),  # Store clean JSON
                                }
                            )

                            # Return automation suggestion
                            _LOGGER.debug(
                                "Received automation suggestion: %s",
                                json.dumps(response_data.get("automation")),
                            )
                            result = {
                                "success": True,
                                "answer": json.dumps(response_data),
                            }
                            result = _with_debug(result)
                            self._set_cached_data(cache_key, result)
                            return result
                        elif (
                            response_data.get("request_type") == "dashboard_suggestion"
                        ):
                            # Add dashboard suggestion to conversation history
                            conv.append(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        response_data
                                    ),  # Store clean JSON
                                }
                            )

                            # Return dashboard suggestion
                            _LOGGER.debug(
                                "Received dashboard suggestion: %s",
                                json.dumps(response_data.get("dashboard")),
                            )
                            result = {
                                "success": True,
                                "answer": json.dumps(response_data),
                            }
                            result = _with_debug(result)
                            self._set_cached_data(cache_key, result)
                            return result
                        elif response_data.get("request_type") in [
                            "get_entities",
                            "get_entities_by_area",
                        ]:
                            # Handle direct get_entities request (for backward compatibility)
                            parameters = response_data.get("parameters", {})
                            _LOGGER.debug(
                                "Processing direct get_entities request with parameters: %s",
                                json.dumps(parameters),
                            )

                            # Add AI's response to conversation history
                            conv.append(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        response_data
                                    ),  # Store clean JSON
                                }
                            )

                            # Get entities data
                            if response_data.get("request_type") == "get_entities":
                                data = await self.get_entities(
                                    area_id=parameters.get("area_id"),
                                    area_ids=parameters.get("area_ids"),
                                )
                            else:  # get_entities_by_area
                                data = await self.get_entities_by_area(
                                    parameters.get("area_id")
                                )

                            _LOGGER.debug(
                                "Retrieved %d entities",
                                len(data) if isinstance(data, list) else 1,
                            )

                            # Add data to conversation as a user message (not system to avoid overwriting system prompt in Anthropic API)
                            conv.append(
                                {
                                    "role": "user",
                                    "content": json.dumps({"data": data}, default=str),
                                }
                            )
                            continue
                        elif response_data.get("request_type") == "call_service":
                            # Handle service call request
                            domain = response_data.get("domain")
                            service = response_data.get("service")
                            target = response_data.get("target", {})
                            service_data = response_data.get("service_data", {})

                            # Resolve nested requests in target
                            if target and "entity_id" in target:
                                entity_id_value = target["entity_id"]
                                if (
                                    isinstance(entity_id_value, dict)
                                    and "request_type" in entity_id_value
                                ):
                                    # This is a nested request, resolve it
                                    nested_request_type = entity_id_value.get(
                                        "request_type"
                                    )
                                    nested_parameters = entity_id_value.get(
                                        "parameters", {}
                                    )

                                    _LOGGER.debug(
                                        "Resolving nested request: %s with parameters: %s",
                                        nested_request_type,
                                        json.dumps(nested_parameters),
                                    )

                                    # Resolve the nested request
                                    if nested_request_type == "get_entities":
                                        entities_data = await self.get_entities(
                                            area_id=nested_parameters.get("area_id"),
                                            area_ids=nested_parameters.get("area_ids"),
                                        )
                                    elif nested_request_type == "get_entities_by_area":
                                        entities_data = await self.get_entities_by_area(
                                            nested_parameters.get("area_id")
                                        )
                                    elif (
                                        nested_request_type == "get_entities_by_domain"
                                    ):
                                        entities_data = (
                                            await self.get_entities_by_domain(
                                                nested_parameters.get("domain")
                                            )
                                        )
                                    else:
                                        _LOGGER.error(
                                            "Unsupported nested request type: %s",
                                            nested_request_type,
                                        )
                                        return {
                                            "success": False,
                                            "error": f"Unsupported nested request type: {nested_request_type}",
                                        }

                                    # Extract entity IDs from the resolved data
                                    if isinstance(entities_data, list):
                                        entity_ids = [
                                            entity.get("entity_id")
                                            for entity in entities_data
                                            if entity.get("entity_id")
                                        ]
                                        target["entity_id"] = entity_ids
                                        _LOGGER.debug(
                                            "Resolved nested request to entity IDs: %s",
                                            entity_ids,
                                        )
                                    else:
                                        _LOGGER.error(
                                            "Nested request returned unexpected data format"
                                        )
                                        return _with_debug(
                                            {
                                                "success": False,
                                                "error": "Nested request returned unexpected data format",
                                            }
                                        )

                            # Handle backward compatibility with old format
                            if not domain or not service:
                                request = response_data.get("request")
                                parameters = response_data.get("parameters", {})

                                if request and "entity_id" in parameters:
                                    entity_id = parameters["entity_id"]
                                    # Infer domain from entity_id
                                    if "." in entity_id:
                                        domain = entity_id.split(".")[0]
                                        service = request
                                        target = {"entity_id": entity_id}
                                        # Remove entity_id from parameters to avoid duplication
                                        service_data = {
                                            k: v
                                            for k, v in parameters.items()
                                            if k != "entity_id"
                                        }
                                        _LOGGER.debug(
                                            "Converted old format: domain=%s, service=%s",
                                            domain,
                                            service,
                                        )

                            _LOGGER.debug(
                                "Processing service call: %s.%s with target: %s and data: %s",
                                domain,
                                service,
                                json.dumps(target),
                                json.dumps(service_data),
                            )

                            # Add AI's response to conversation history
                            conv.append(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        response_data
                                    ),  # Store clean JSON
                                }
                            )

                            # Call the service
                            data = await self.call_service(
                                domain, service, target, service_data
                            )

                            # Check if service call resulted in an error
                            if isinstance(data, dict) and "error" in data:
                                return _with_debug(
                                    {"success": False, "error": data["error"]}
                                )

                            _LOGGER.debug(
                                "Service call completed: %s",
                                json.dumps(data, default=str),
                            )

                            # Add data to conversation as a user message (not system to avoid overwriting system prompt in Anthropic API)
                            conv.append(
                                {
                                    "role": "user",
                                    "content": json.dumps({"data": data}, default=str),
                                }
                            )
                            # Go to next iteration to continue the loop
                            continue

                        # Unknown request type
                        _LOGGER.warning(
                            "Unknown response type: %s",
                            response_data.get("request_type"),
                        )
                        return _with_debug(
                            {
                                "success": False,
                                "error": f"Unknown response type: {response_data.get('request_type')}",
                            }
                        )

                    except json.JSONDecodeError as e:
                        # Check if this is a local/ollama provider that might have already wrapped the response
                        if selected_provider == "ollama":
                            _LOGGER.debug(
                                "Local provider returned non-JSON response (this is normal and handled): %s",
                                response[:200],
                            )
                        else:
                            # Log more of the response to help with debugging for non-local providers
                            response_preview = (
                                response[:1000] if len(response) > 1000 else response
                            )
                            _LOGGER.warning(
                                "Failed to parse response as JSON: %s. Response length: %d. Response preview: %s",
                                str(e),
                                len(response),
                                response_preview,
                            )

                            # Log additional debugging information
                            _LOGGER.debug(
                                "First 50 characters as bytes: %s",
                                response[:50].encode("utf-8") if response else b"",
                            )
                            _LOGGER.debug(
                                "Response starts with: %s",
                                repr(response[:10]) if response else "None",
                            )

                        # Also log the response to a separate debug file for detailed analysis (non-local providers only)
                        if selected_provider != "ollama":
                            try:
                                import os

                                debug_dir = "/config/ai_agent_ha_debug"

                                def write_debug_file():
                                    if not os.path.exists(debug_dir):
                                        os.makedirs(debug_dir)

                                    import datetime

                                    timestamp = datetime.datetime.now().strftime(
                                        "%Y%m%d_%H%M%S"
                                    )
                                    debug_file = os.path.join(
                                        debug_dir, f"failed_response_{timestamp}.txt"
                                    )

                                    with open(debug_file, "w", encoding="utf-8") as f:
                                        f.write(f"Timestamp: {timestamp}\n")
                                        f.write(f"Provider: {selected_provider}\n")
                                        f.write(f"Error: {str(e)}\n")
                                        f.write(f"Response length: {len(response)}\n")
                                        f.write(
                                            f"Response bytes: {response.encode('utf-8') if response else b''}\n"
                                        )
                                        f.write(f"Response repr: {repr(response)}\n")
                                        f.write(f"Full response:\n{response}\n")

                                    return debug_file

                                # Run file operations in executor to avoid blocking
                                debug_file = await self.hass.async_add_executor_job(
                                    write_debug_file
                                )
                                _LOGGER.info(
                                    "Failed response saved to debug file: %s",
                                    debug_file,
                                )
                            except Exception as debug_error:
                                _LOGGER.debug(
                                    "Could not save debug file: %s", str(debug_error)
                                )

                        # Check if this looks like a corrupted automation suggestion
                        if (
                            response.strip().startswith(
                                '{"request_type": "automation_suggestion'
                            )
                            and len(response) > 10000
                            and response.count("for its use in various fields") > 50
                        ):
                            _LOGGER.warning(
                                "Detected corrupted automation suggestion response with repetitive text"
                            )
                            result = _with_debug(
                                {
                                    "success": False,
                                    "error": "AI generated corrupted automation response. Please try again with a more specific automation request.",
                                }
                            )
                            self._set_cached_data(cache_key, result)
                            return result

                        # If response is not valid JSON, try to wrap it as a final response
                        try:
                            # Truncate extremely long responses to prevent memory issues
                            response_to_wrap = response
                            if len(response) > 50000:
                                response_to_wrap = (
                                    response[:5000]
                                    + "... [Response truncated due to excessive length]"
                                )
                                _LOGGER.warning(
                                    "Truncated extremely long response from %d to 5000 characters",
                                    len(response),
                                )

                            wrapped_response = {
                                "request_type": "final_response",
                                "response": response_to_wrap,
                            }
                            result = {
                                "success": True,
                                "answer": json.dumps(wrapped_response),
                            }
                            _LOGGER.debug("Wrapped non-JSON response as final_response")
                        except Exception as wrap_error:
                            _LOGGER.error(
                                "Failed to wrap response: %s", str(wrap_error)
                            )
                            result = {
                                "success": False,
                                "error": f"Invalid response format: {str(e)}",
                            }

                        result = _with_debug(result)
                        self._set_cached_data(cache_key, result)
                        return result

                    except Exception as e:
                        _LOGGER.exception("Error processing AI response: %s", str(e))
                        return _with_debug(
                            {
                                "success": False,
                                "error": f"Error processing AI response: {str(e)}",
                            }
                        )

                # If we've reached max iterations without a final response
                _LOGGER.warning("Reached maximum iterations without final response")
                result = {
                    "success": False,
                    "error": "Maximum iterations reached without final response",
                }
                result = _with_debug(result)
                self._set_cached_data(cache_key, result)
                return result

            except Exception as e:
                _LOGGER.exception("Error in process_query: %s", str(e))
                return _with_debug(
                    {"success": False, "error": f"Error in process_query: {str(e)}"}
                )
            finally:
                if uid:
                    try:
                        conv = self._get_conversation(uid)
                        if len(conv) > MAX_STORED_MESSAGES:
                            self._user_conversations[uid] = conv[-MAX_STORED_MESSAGES:]
                            conv = self._user_conversations[uid]
                        await self.save_chat_history(
                            uid, self._conversation_to_messages(conv)
                        )
                    except Exception as save_err:
                        _LOGGER.debug("Could not save chat history: %s", save_err)

    def _build_debug_trace(
        self,
        user_id: str,
        provider: Optional[str],
        provider_settings: Optional[Dict[str, Any]],
        endpoint_type: Optional[str],
    ) -> Dict[str, Any]:
        """Return a sanitized snapshot of the HA↔AI conversation for UI display."""
        conv = self._get_conversation(user_id)
        history_tail = conv[-20:] if conv else []
        return {
            "provider": provider,
            "model": provider_settings.get("model") if provider_settings else None,
            "endpoint_type": endpoint_type,
            "conversation": history_tail,
        }

    def _trim_messages_to_char_limit(
        self, messages: List[Dict[str, Any]], max_chars: int = MAX_PROMPT_CHARS
    ) -> List[Dict[str, Any]]:
        """Trim message list so total content length stays under max_chars (Ollama limit 65536)."""
        total = 0
        result: List[Dict[str, Any]] = []
        for msg in reversed(messages):
            content = (msg.get("content") or "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", str(p)) for p in content if isinstance(p, dict)
                )
            content_len = len(str(content))
            if total + content_len > max_chars and result:
                break
            result.insert(0, msg)
            total += content_len
        return result

    async def _get_ai_response(
        self, user_id: str, request_id: Optional[str] = None
    ) -> str:
        """Get response from the selected AI provider with retries and rate limiting."""
        if not self._check_rate_limit():
            raise Exception("Rate limit exceeded. Please try again later.")
        retry_count = 0
        last_error = None
        conv = self._get_conversation(user_id)
        # Limit conversation history to last 10 messages to prevent token overflow
        recent_messages = (
            conv[-10:] if len(conv) > 10 else conv
        )
        # Ensure system prompt is always the first message
        if not recent_messages or recent_messages[0].get("role") != "system":
            recent_messages = [self.system_prompt] + recent_messages
        # Trim to stay under Ollama/local model prompt limit (65536 chars)
        orig_len = len(recent_messages)
        recent_messages = self._trim_messages_to_char_limit(recent_messages)
        if len(recent_messages) < orig_len:
            _LOGGER.warning(
                "Trimmed conversation from %d to %d messages to stay under %d chars (Ollama limit 65536)",
                orig_len,
                len(recent_messages),
                MAX_PROMPT_CHARS,
            )

        _LOGGER.debug("Sending %d messages to AI provider", len(recent_messages))
        _LOGGER.debug("AI provider: %s", self.config.get("ai_provider", "unknown"))

        while retry_count < self._max_retries:
            try:
                _LOGGER.debug(
                    "Attempt %d/%d: Calling AI client",
                    retry_count + 1,
                    self._max_retries,
                )
                request_timeout = self.config.get(
                    CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT
                )
                session = await self._get_session()
                provider = self.config.get("ai_provider", "")
                use_stream = (
                    provider == "ollama"
                    and hasattr(self.ai_client, "get_response_stream")
                    and request_id is not None
                )
                if use_stream:
                    self.ai_client._last_streamed_response = None
                    response = None
                    try:
                        async for chunk in self.ai_client.get_response_stream(
                            recent_messages,
                            timeout=request_timeout,
                            session=session,
                        ):
                            self.hass.bus.async_fire(
                                "ai_agent_ha_response_chunk",
                                {
                                    "entry_id": self._entry_id,
                                    "user_id": user_id,
                                    "request_id": request_id or "",
                                    "chunk": chunk,
                                },
                            )
                        response = getattr(
                            self.ai_client,
                            "_last_streamed_response",
                            None,
                        )
                    except Exception as stream_err:
                        _LOGGER.warning(
                            "Ollama streaming failed, falling back to non-streaming: %s",
                            stream_err,
                        )
                        use_stream = False
                if not use_stream or not response:
                    response = await self.ai_client.get_response(
                        recent_messages, timeout=request_timeout, session=session
                    )
                _LOGGER.debug(
                    "AI client returned response of length: %d", len(response or "")
                )
                _LOGGER.debug("AI response preview: %s", (response or "")[:200])

                # Check for extremely long responses that might indicate model issues
                if response and len(response) > 50000:
                    _LOGGER.warning(
                        "AI returned extremely long response (%d characters), this may indicate a model issue",
                        len(response),
                    )
                    # Check for repetitive patterns that indicate a corrupted response
                    if response.count("for its use in various fields") > 50:
                        _LOGGER.error(
                            "Detected corrupted repetitive response, aborting this iteration"
                        )
                        raise Exception(
                            "AI generated corrupted response with repetitive text. Please try again with a clearer request."
                        )

                # Check if response is empty
                if not response or response.strip() == "":
                    _LOGGER.warning(
                        "AI client returned empty response on attempt %d",
                        retry_count + 1,
                    )
                    if retry_count + 1 >= self._max_retries:
                        raise Exception(
                            "AI provider returned empty response after all retries"
                        )
                    else:
                        retry_count += 1
                        await asyncio.sleep(min(2 ** retry_count, 30))
                        continue

                return str(response)
            except Exception as e:
                err_msg = str(e).strip() or getattr(e, "message", repr(e))
                # Do not retry on client/authorization errors (401, 403, 404)
                status = getattr(e, "status", None)
                if status in (401, 403, 404) or "401" in err_msg or "403" in err_msg or "404" in err_msg:
                    _LOGGER.error("Non-retriable error: %s - %s", type(e).__name__, err_msg)
                    raise
                _LOGGER.error(
                    "AI client error on attempt %d: %s - %s",
                    retry_count + 1,
                    type(e).__name__,
                    err_msg,
                )
                last_error = e
                retry_count += 1
                if retry_count < self._max_retries:
                    await asyncio.sleep(min(2 ** retry_count, 30))
                continue
        last_msg = str(last_error).strip() if last_error else "unknown"
        if not last_msg:
            last_msg = f"{type(last_error).__name__} (no message)"
        hint = ""
        if last_error and (
            "disconnect" in last_msg.lower()
            or "connection" in last_msg.lower()
            or "reset" in last_msg.lower()
        ):
            hint = " Verlauf leeren und erneut versuchen oder Request-Timeout in den Integrations-Optionen erhöhen."
        raise Exception(
            f"Failed after {retry_count} retries. Last error: {last_msg}.{hint}"
        )

    def clear_conversation_history(self, user_id: str) -> None:
        """Clear the conversation history for the given user and the cache."""
        self._user_conversations[user_id] = []
        self._cache.clear()
        _LOGGER.debug("Conversation history and cache cleared for user %s", user_id)

    async def set_entity_state(
        self, entity_id: str, state: str, attributes: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Set the state of an entity."""
        try:
            _LOGGER.debug(
                "Setting state for entity %s to %s with attributes: %s",
                entity_id,
                state,
                json.dumps(attributes or {}),
            )

            # Validate entity exists
            if not self.hass.states.get(entity_id):
                return {"error": f"Entity {entity_id} not found"}

            # Call the appropriate service based on the domain
            domain = entity_id.split(".")[0]

            if domain == "light":
                service = (
                    "turn_on" if state.lower() in ["on", "true", "1"] else "turn_off"
                )
                service_data = {"entity_id": entity_id}
                if attributes and service == "turn_on":
                    service_data.update(attributes)
                await self.hass.services.async_call("light", service, service_data)

            elif domain == "switch":
                service = (
                    "turn_on" if state.lower() in ["on", "true", "1"] else "turn_off"
                )
                await self.hass.services.async_call(
                    "switch", service, {"entity_id": entity_id}
                )

            elif domain == "cover":
                if state.lower() in ["open", "up"]:
                    service = "open_cover"
                elif state.lower() in ["close", "down"]:
                    service = "close_cover"
                elif state.lower() == "stop":
                    service = "stop_cover"
                else:
                    return {"error": f"Invalid state {state} for cover entity"}
                await self.hass.services.async_call(
                    "cover", service, {"entity_id": entity_id}
                )

            elif domain == "climate":
                service_data = {"entity_id": entity_id}
                if state.lower() in ["on", "true", "1"]:
                    service = "turn_on"
                elif state.lower() in ["off", "false", "0"]:
                    service = "turn_off"
                elif state.lower() in ["heat", "cool", "dry", "fan_only", "auto"]:
                    service = "set_hvac_mode"
                    service_data["hvac_mode"] = state.lower()
                else:
                    return {"error": f"Invalid state {state} for climate entity"}
                await self.hass.services.async_call("climate", service, service_data)

            elif domain == "fan":
                service = (
                    "turn_on" if state.lower() in ["on", "true", "1"] else "turn_off"
                )
                service_data = {"entity_id": entity_id}
                if attributes and service == "turn_on":
                    service_data.update(attributes)
                await self.hass.services.async_call("fan", service, service_data)

            else:
                # For other domains, try to set the state directly
                self.hass.states.async_set(entity_id, state, attributes or {})

            # Get the new state to confirm the change
            new_state = self.hass.states.get(entity_id)
            return {
                "success": True,
                "entity_id": entity_id,
                "new_state": new_state.state,
                "new_attributes": new_state.attributes,
            }

        except Exception as e:
            _LOGGER.exception("Error setting entity state: %s", str(e))
            return {"error": f"Error setting entity state: {str(e)}"}

    async def call_service(
        self,
        domain: str,
        service: str,
        target: Optional[Dict[str, Any]] = None,
        service_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call a Home Assistant service (only allowed domains)."""
        try:
            if domain not in ALLOWED_SERVICE_DOMAINS:
                _LOGGER.warning("Blocked service call: domain %s not in allowlist", domain)
                return {"error": f"Domain '{domain}' is not allowed for AI service calls"}
            _LOGGER.debug(
                "Calling service %s.%s with target: %s and data: %s",
                domain,
                service,
                json.dumps(target or {}),
                json.dumps(service_data or {}),
            )

            # Prepare the service call data
            call_data = {}

            # Add target entities if provided
            if target:
                if "entity_id" in target:
                    entity_ids = target["entity_id"]
                    if isinstance(entity_ids, list):
                        call_data["entity_id"] = entity_ids
                    else:
                        call_data["entity_id"] = [entity_ids]

                # Add other target properties
                for key, value in target.items():
                    if key != "entity_id":
                        call_data[key] = value

            # Add service data if provided
            if service_data:
                call_data.update(service_data)

            _LOGGER.debug("Final service call data: %s", json.dumps(call_data))

            # Call the service
            await self.hass.services.async_call(domain, service, call_data)

            # Get the updated states of affected entities
            result_entities = []
            if "entity_id" in call_data:
                for entity_id in call_data["entity_id"]:
                    state = self.hass.states.get(entity_id)
                    if state:
                        result_entities.append(
                            {
                                "entity_id": entity_id,
                                "state": state.state,
                                "attributes": dict(state.attributes),
                            }
                        )

            return {
                "success": True,
                "service": f"{domain}.{service}",
                "entities_affected": result_entities,
                "message": f"Successfully called {domain}.{service}",
            }

        except Exception as e:
            _LOGGER.exception(
                "Error calling service %s.%s: %s", domain, service, str(e)
            )
            return {"error": f"Error calling service {domain}.{service}: {str(e)}"}

    async def save_user_prompt_history(
        self, user_id: str, history: List[str]
    ) -> Dict[str, Any]:
        """Save user's prompt history to HA storage."""
        try:
            store: Store = Store(
                self.hass, 1, f"ai_agent_ha_history_{self._entry_id}_{user_id}"
            )
            await store.async_save({"history": history})
            return {"success": True}
        except Exception as e:
            _LOGGER.exception("Error saving prompt history: %s", str(e))
            return {"error": f"Error saving prompt history: {str(e)}"}

    async def load_user_prompt_history(self, user_id: str) -> Dict[str, Any]:
        """Load user's prompt history from HA storage.

        Note: Store files (e.g. ai_agent_ha_history_*) persist in .storage even after
        uninstalling the integration; use Clear in the UI to reset prompt history.
        """
        try:
            store: Store = Store(
                self.hass, 1, f"ai_agent_ha_history_{self._entry_id}_{user_id}"
            )
            data = await store.async_load()
            history = data.get("history", []) if data else []
            return {"success": True, "history": history}
        except Exception as e:
            _LOGGER.exception("Error loading prompt history: %s", str(e))
            return {"error": f"Error loading prompt history: {str(e)}", "history": []}

    async def save_system_prompt_settings(
        self, system_prompt: Optional[str] = None, language: Optional[str] = None
    ) -> Dict[str, Any]:
        """Save system prompt and/or language (updates agent state; config entry updated by service handler)."""
        try:
            if system_prompt is not None:
                prompt_str = str(system_prompt).strip()
                self._custom_system_prompt = prompt_str if prompt_str else None
            if language is not None:
                self._language = str(language).strip() or DEFAULT_LANGUAGE
            self._apply_system_prompt()
            return {"success": True}
        except Exception as e:
            _LOGGER.exception("Error saving system prompt settings: %s", str(e))
            return {"error": str(e)}

    async def load_system_prompt_settings(self) -> Dict[str, Any]:
        """Load custom system prompt and language from Store."""
        try:
            await self._load_settings()
            return {
                "success": True,
                CONF_SYSTEM_PROMPT: self._custom_system_prompt or "",
                CONF_LANGUAGE: self._language or "",
            }
        except Exception as e:
            _LOGGER.exception("Error loading system prompt settings: %s", str(e))
            return {"error": str(e), CONF_SYSTEM_PROMPT: "", CONF_LANGUAGE: ""}

    async def save_chat_history(
        self, user_id: str, messages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Save conversation history to Store."""
        try:
            store: Store = Store(
                self.hass, 1, f"ai_agent_ha_chat_{self._entry_id}_{user_id}"
            )
            await store.async_save({"messages": messages})
            if not messages:
                self.clear_conversation_history(user_id)
            return {"success": True}
        except Exception as e:
            _LOGGER.exception("Error saving chat history: %s", str(e))
            return {"error": str(e)}

    async def load_chat_history(self, user_id: str) -> Dict[str, Any]:
        """Load conversation history from Store and restore to agent."""
        try:
            store: Store = Store(
                self.hass, 1, f"ai_agent_ha_chat_{self._entry_id}_{user_id}"
            )
            data = await store.async_load()
            messages = data.get("messages", []) if data else []
            conv = self._messages_to_conversation(messages)
            if len(conv) > MAX_STORED_MESSAGES:
                conv = conv[-MAX_STORED_MESSAGES:]
            self._user_conversations[user_id] = conv
            return {"success": True, "messages": messages}
        except Exception as e:
            _LOGGER.exception("Error loading chat history: %s", str(e))
            return {"error": str(e), "messages": []}

    def _messages_to_conversation(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert UI message format to conversation history format."""
        result: List[Dict[str, Any]] = []
        for msg in messages:
            role = "user" if msg.get("type") == "user" else "assistant"
            text = msg.get("text", "")
            result.append({"role": role, "content": text})
        return result

    def _conversation_to_messages(
        self, history: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert conversation history to UI message format (skip system role)."""
        result: List[Dict[str, Any]] = []
        for entry in history:
            role = entry.get("role", "user")
            if role == "system":
                continue
            content = entry.get("content", "")
            result.append(
                {
                    "type": "user" if role == "user" else "assistant",
                    "text": content,
                }
            )
        return result


def get_default_system_prompt_content_for_provider(provider: str) -> str:
    """Return the default system prompt content for local/ollama provider (short version), else empty string."""
    if provider == "ollama":
        return AiAgentHaAgent.SYSTEM_PROMPT_LOCAL_SHORT.get("content", "")
    return ""
