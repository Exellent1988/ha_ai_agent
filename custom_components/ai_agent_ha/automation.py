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


def _ensure_list(value: Any) -> List[Any]:
    """Ensure trigger/condition/action are lists (HA expects list of items)."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _fix_conditions(conditions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fix AI-generated condition quirks that HA doesn't accept.

    - ``negate: true`` → wrap in ``condition: not`` with nested ``conditions``.
    - Removes unknown keys that HA schema rejects.
    """
    fixed: List[Dict[str, Any]] = []
    for cond in conditions:
        if not isinstance(cond, dict):
            fixed.append(cond)
            continue
        cond = dict(cond)
        negate = cond.pop("negate", None)
        if negate:
            fixed.append({
                "condition": "not",
                "conditions": [cond],
            })
        else:
            fixed.append(cond)
    return fixed


def _fix_triggers(triggers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize AI-generated triggers for HA compatibility.

    - Renames ``type`` → ``platform`` when ``platform`` is missing (common AI mistake).
    - Ensures ``for`` durations are strings (e.g. ``"00:15:00"``) not dicts.
    """
    fixed: List[Dict[str, Any]] = []
    for trig in triggers:
        if not isinstance(trig, dict):
            fixed.append(trig)
            continue
        trig = dict(trig)
        if "platform" not in trig and "type" in trig:
            trig["platform"] = trig.pop("type")
        dur = trig.get("for")
        if isinstance(dur, dict):
            h = int(dur.get("hours", 0))
            m = int(dur.get("minutes", 0))
            s = int(dur.get("seconds", 0))
            trig["for"] = f"{h:02d}:{m:02d}:{s:02d}"
        fixed.append(trig)
    return fixed


def _fix_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize AI-generated actions for HA compatibility.

    - Renames ``service`` → ``action`` (HA 2024.x+ renamed service calls to actions).
    """
    fixed: List[Dict[str, Any]] = []
    for act in actions:
        if not isinstance(act, dict):
            fixed.append(act)
            continue
        act = dict(act)
        if "service" in act and "action" not in act:
            act["action"] = act.pop("service")
        fixed.append(act)
    return fixed


def sanitize_automation_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize automation configuration to prevent injection attacks.
    Normalizes trigger/condition/action to lists (HA format; AI may return single objects).
    Fixes common AI-generated schema issues (negate, service→action, type→platform).
    """
    sanitized: Dict[str, Any] = {}
    for key, value in config.items():
        if key in ["alias", "description"]:
            sanitized[key] = str(value).strip()[:100]
        elif key in ["trigger", "condition", "action"]:
            as_list = _ensure_list(value)
            if as_list:
                sanitized[key] = _remove_none_from_automation_data(as_list)
        elif key == "mode":
            if value in ["single", "restart", "queued", "parallel"]:
                sanitized[key] = value

    if "trigger" in sanitized:
        sanitized["trigger"] = _fix_triggers(sanitized["trigger"])
    if "condition" in sanitized:
        sanitized["condition"] = _fix_conditions(sanitized["condition"])
    if "action" in sanitized:
        sanitized["action"] = _fix_actions(sanitized["action"])

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
            _LOGGER.error("Missing required fields: %s", list(automation_config.keys()))
            return {"error": "Missing required fields in automation configuration"}

        sanitized_config = sanitize_automation_config(automation_config)
        _LOGGER.debug("Sanitized config keys: %s", list(sanitized_config.keys()))

        if "trigger" not in sanitized_config or not sanitized_config["trigger"]:
            _LOGGER.error("No trigger after sanitization (raw type: %s)", type(automation_config.get("trigger")))
            return {"error": "Automation must have at least one trigger"}
        if "action" not in sanitized_config or not sanitized_config["action"]:
            _LOGGER.error("No action after sanitization (raw type: %s)", type(automation_config.get("action")))
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
            _LOGGER.warning(
                "Automation '%s' already exists, skipping creation",
                automation_entry["alias"],
            )
            return {
                "error": f"An automation with the name '{automation_entry['alias']}' already exists"
            }

        current_automations.append(automation_entry)
        _LOGGER.info(
            "Writing %d automations to %s (adding '%s')",
            len(current_automations),
            automations_path,
            automation_entry["alias"],
        )

        def _write_automations() -> None:
            with open(automations_path, "w", encoding="utf-8") as f:
                yaml.dump(current_automations, f, default_flow_style=False)

        await hass.async_add_executor_job(_write_automations)
        _LOGGER.info("automations.yaml written successfully")

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

        _LOGGER.info(
            "Automation '%s' (%s) created successfully",
            automation_entry["alias"],
            entity_id,
        )
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
