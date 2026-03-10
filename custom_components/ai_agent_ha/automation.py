"""Automation creation and sanitization."""

import asyncio
import logging
import time
from typing import Any, Dict, List

import yaml  # type: ignore[import-untyped]

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _remove_none_from_automation_data(obj: Any) -> Any:
    """Recursively remove keys with None values for HA schema validation."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {
            k: _remove_none_from_automation_data(v)
            for k, v in obj.items()
            if v is not None
        }
    if isinstance(obj, list):
        return [_remove_none_from_automation_data(item) for item in obj]
    return obj


def sanitize_automation_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize automation configuration to prevent injection attacks."""
    sanitized: Dict[str, Any] = {}
    for key, value in config.items():
        if key in ["alias", "description"]:
            sanitized[key] = str(value).strip()[:100]
        elif key in ["trigger", "condition", "action"]:
            if isinstance(value, list):
                sanitized[key] = _remove_none_from_automation_data(value)
        elif key == "mode":
            if value in ["single", "restart", "queued", "parallel"]:
                sanitized[key] = value
    return sanitized


async def create_automation(
    hass: HomeAssistant,
    automation_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a new automation with validation and sanitization."""
    import json

    try:
        _LOGGER.debug(
            "Creating automation with config: %s", json.dumps(automation_config)
        )

        if not all(
            key in automation_config for key in ["alias", "trigger", "action"]
        ):
            return {"error": "Missing required fields in automation configuration"}

        sanitized_config = sanitize_automation_config(automation_config)

        if "trigger" not in sanitized_config or not sanitized_config["trigger"]:
            return {"error": "Automation must have at least one trigger"}
        if "action" not in sanitized_config or not sanitized_config["action"]:
            return {"error": "Automation must have at least one action"}

        automation_id = f"ai_agent_auto_{int(time.time() * 1000)}"
        automation_entry = {
            "id": automation_id,
            "alias": sanitized_config["alias"],
            "description": sanitized_config.get("description", ""),
            "trigger": sanitized_config["trigger"],
            "condition": sanitized_config.get("condition", []),
            "action": sanitized_config["action"],
            "mode": sanitized_config.get("mode", "single"),
        }

        automations_path = hass.config.path("automations.yaml")

        def _read_automations() -> List[Dict[str, Any]]:
            with open(automations_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or []

        try:
            current_automations = await hass.async_add_executor_job(_read_automations)
        except yaml.YAMLError as e:
            _LOGGER.warning("automations.yaml YAML error: %s", e)
            return {"error": f"automations.yaml ist beschädigt: {e}"}
        except FileNotFoundError:
            current_automations = []

        if any(
            auto.get("alias") == automation_entry["alias"]
            for auto in current_automations
        ):
            return {
                "error": f"An automation with the name '{automation_entry['alias']}' already exists"
            }

        current_automations.append(automation_entry)

        def _write_automations() -> None:
            with open(automations_path, "w", encoding="utf-8") as f:
                yaml.dump(current_automations, f, default_flow_style=False)

        await hass.async_add_executor_job(_write_automations)

        try:
            await hass.services.async_call("automation", "reload")
        except Exception as reload_err:
            _LOGGER.warning("Automation reload failed: %s", reload_err)
            return {
                "error": (
                    f"Die Automation wurde angelegt, aber die Konfiguration enthält "
                    f"Fehler. Bitte in Einstellungen > Automatisierungen prüfen. "
                    f"Fehler: {str(reload_err)}"
                ),
                "message": (
                    f"Die Automation '{automation_entry['alias']}' wurde angelegt, "
                    f"ist aber wegen Konfigurationsfehlern nicht aktiv. "
                    f"Bitte in Einstellungen > Automatisierungen die Fehler beheben. "
                    f"Fehler: {str(reload_err)}"
                ),
            }

        await asyncio.sleep(1)
        entity_id = f"automation.{automation_id}"
        state = hass.states.get(entity_id)
        if state and state.state == "unavailable":
            error_attr = state.attributes.get("error") or state.attributes.get(
                "message", ""
            )
            error_msg = error_attr or "Unbekannter Konfigurationsfehler"
            return {
                "error": (
                    f"Die Automation '{automation_entry['alias']}' "
                    f"({entity_id}) ist nicht aktiv, da die Konfiguration "
                    f"Fehler aufweist. {error_msg}"
                ),
                "message": (
                    f"Die Automation '{automation_entry['alias']}' wurde angelegt, "
                    f"ist aber wegen Konfigurationsfehlern nicht aktiv. "
                    f"Bitte in Einstellungen > Automatisierungen die Fehler beheben. "
                    f"Fehler: {error_msg}"
                ),
            }

        return {
            "success": True,
            "message": f"Automation '{automation_entry['alias']}' created successfully",
        }

    except Exception as e:
        _LOGGER.exception("Error creating automation: %s", str(e))
        return {"error": f"Error creating automation: {str(e)}"}


async def update_automation(
    hass: HomeAssistant,
    automation_id_or_alias: str,
    automation_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Update an existing automation by id (e.g. ai_agent_auto_123) or alias."""
    import json

    if not automation_id_or_alias or not isinstance(automation_id_or_alias, str):
        return {"error": "automation_id_or_alias is required (id or alias)"}

    try:
        _LOGGER.debug(
            "Updating automation %s with config: %s",
            automation_id_or_alias,
            json.dumps(automation_config),
        )

        if not all(
            key in automation_config for key in ["alias", "trigger", "action"]
        ):
            return {"error": "Missing required fields in automation configuration"}

        sanitized_config = sanitize_automation_config(automation_config)
        if "trigger" not in sanitized_config or not sanitized_config["trigger"]:
            return {"error": "Automation must have at least one trigger"}
        if "action" not in sanitized_config or not sanitized_config["action"]:
            return {"error": "Automation must have at least one action"}

        # Normalize: allow entity_id (automation.xxx) -> use xxx
        lookup = automation_id_or_alias.strip()
        if lookup.startswith("automation."):
            lookup = lookup.replace("automation.", "", 1)

        automations_path = hass.config.path("automations.yaml")

        def _read_automations() -> List[Dict[str, Any]]:
            with open(automations_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or []

        try:
            current_automations = await hass.async_add_executor_job(_read_automations)
        except yaml.YAMLError as e:
            _LOGGER.warning("automations.yaml YAML error: %s", e)
            return {"error": f"automations.yaml ist beschädigt: {e}"}
        except FileNotFoundError:
            return {"error": "automations.yaml nicht gefunden"}

        idx = None
        existing_id = None
        for i, auto in enumerate(current_automations):
            aid = auto.get("id") or ""
            alias = (auto.get("alias") or "").strip()
            if aid == lookup or alias == lookup:
                idx = i
                existing_id = aid
                break

        if idx is None:
            return {
                "error": (
                    f"Automation mit Id oder Alias '{automation_id_or_alias}' nicht gefunden. "
                    "Nutze get_automations um vorhandene Automations-Ids/Aliase zu sehen."
                )
            }

        automation_entry = {
            "id": existing_id,
            "alias": sanitized_config["alias"],
            "description": sanitized_config.get("description", ""),
            "trigger": sanitized_config["trigger"],
            "condition": sanitized_config.get("condition", []),
            "action": sanitized_config["action"],
            "mode": sanitized_config.get("mode", "single"),
        }
        current_automations[idx] = automation_entry

        def _write_automations() -> None:
            with open(automations_path, "w", encoding="utf-8") as f:
                yaml.dump(current_automations, f, default_flow_style=False)

        await hass.async_add_executor_job(_write_automations)

        try:
            await hass.services.async_call("automation", "reload")
        except Exception as reload_err:
            _LOGGER.warning("Automation reload failed: %s", reload_err)
            return {
                "error": (
                    f"Automation wurde aktualisiert, Reload schlug fehl: {reload_err}"
                ),
                "message": (
                    f"Automation '{automation_entry['alias']}' wurde in automations.yaml "
                    f"aktualisiert. Bitte Einstellungen > Automatisierungen prüfen."
                ),
            }

        await asyncio.sleep(1)
        entity_id = f"automation.{existing_id}"
        state = hass.states.get(entity_id)
        if state and state.state == "unavailable":
            error_attr = state.attributes.get("error") or state.attributes.get(
                "message", ""
            )
            return {
                "error": (
                    f"Automation '{automation_entry['alias']}' ({entity_id}) "
                    f"hat Konfigurationsfehler: {error_attr or 'Unbekannt'}"
                ),
                "message": (
                    f"Automation wurde aktualisiert, bitte Konfiguration prüfen."
                ),
            }

        return {
            "success": True,
            "message": (
                f"Automation '{automation_entry['alias']}' ({entity_id}) "
                "updated successfully"
            ),
        }

    except Exception as e:
        _LOGGER.exception("Error updating automation: %s", str(e))
        return {"error": f"Error updating automation: {str(e)}"}
