"""Module-level async functions for entity/registry/history data requests.

All functions take hass: HomeAssistant as first argument. Used by the agent
and other components to fetch state, registries, history, etc.
"""

import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


async def get_entity_state(hass: HomeAssistant, entity_id: str) -> Dict[str, Any]:
    """Get the state of a specific entity."""
    try:
        _LOGGER.debug("Requesting entity state for: %s", entity_id)
        state = hass.states.get(entity_id)
        if not state:
            _LOGGER.warning("Entity not found: %s", entity_id)
            return {"error": f"Entity {entity_id} not found"}

        # Get area information from entity/device registry
        area_id = None
        area_name = None

        try:
            from homeassistant.helpers import area_registry as ar
            from homeassistant.helpers import device_registry as dr
            from homeassistant.helpers import entity_registry as er

            entity_registry = er.async_get(hass)
            device_registry = dr.async_get(hass)
            area_registry = ar.async_get(hass)

            if entity_registry and hasattr(entity_registry, "async_get"):
                entity_entry = entity_registry.async_get(entity_id)
                if entity_entry:
                    _LOGGER.debug("Entity %s found in registry", entity_id)
                    if hasattr(entity_entry, "area_id") and entity_entry.area_id:
                        area_id = entity_entry.area_id
                        _LOGGER.debug(
                            "Entity %s has direct area assignment: %s",
                            entity_id,
                            area_id,
                        )
                    elif (
                        hasattr(entity_entry, "device_id")
                        and entity_entry.device_id
                        and device_registry
                        and hasattr(device_registry, "async_get")
                    ):
                        _LOGGER.debug(
                            "Entity %s has device_id: %s, checking device area",
                            entity_id,
                            entity_entry.device_id,
                        )
                        device_entry = device_registry.async_get(
                            entity_entry.device_id
                        )
                        if device_entry:
                            if (
                                hasattr(device_entry, "area_id")
                                and device_entry.area_id
                            ):
                                area_id = device_entry.area_id
                                _LOGGER.debug(
                                    "Device %s has area: %s",
                                    entity_entry.device_id,
                                    area_id,
                                )
                            else:
                                _LOGGER.debug(
                                    "Device %s has no area assigned",
                                    entity_entry.device_id,
                                )
                        else:
                            _LOGGER.debug(
                                "Device %s not found in registry",
                                entity_entry.device_id,
                            )
                    else:
                        _LOGGER.debug(
                            "Entity %s has no area_id and no device_id", entity_id
                        )
                else:
                    _LOGGER.debug(
                        "Entity %s not found in entity registry", entity_id
                    )
            else:
                _LOGGER.debug("Entity registry not available for %s", entity_id)

            if (
                area_id
                and area_registry
                and hasattr(area_registry, "async_get_area")
            ):
                area_entry = area_registry.async_get_area(area_id)
                if area_entry and hasattr(area_entry, "name"):
                    area_name = area_entry.name
                    _LOGGER.debug(
                        "Resolved area_id %s to area_name: %s", area_id, area_name
                    )
                else:
                    _LOGGER.debug("Could not resolve area_id %s to name", area_id)
            elif area_id:
                _LOGGER.debug(
                    "Have area_id %s but area_registry not available", area_id
                )
        except Exception as e:
            _LOGGER.warning(
                "Exception retrieving area information for %s: %s",
                entity_id,
                str(e),
            )

        result = {
            "entity_id": state.entity_id,
            "state": state.state,
            "last_changed": (
                state.last_changed.isoformat() if state.last_changed else None
            ),
            "friendly_name": state.attributes.get("friendly_name"),
            "area_id": area_id,
            "area_name": area_name,
            "attributes": {
                k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in state.attributes.items()
            },
        }
        _LOGGER.debug(
            "Retrieved entity state for %s: area_id=%s, area_name=%s",
            entity_id,
            area_id,
            area_name,
        )
        return result
    except Exception as e:
        _LOGGER.exception("Error getting entity state: %s", str(e))
        return {"error": f"Error getting entity state: {str(e)}"}


async def get_entities_by_domain(
    hass: HomeAssistant, domain: str
) -> List[Dict[str, Any]]:
    """Get all entities for a specific domain."""
    try:
        _LOGGER.debug("Requesting all entities for domain: %s", domain)
        states = [
            state
            for state in hass.states.async_all()
            if state.entity_id.startswith(f"{domain}.")
        ]
        _LOGGER.debug("Found %d entities in domain %s", len(states), domain)
        return [await get_entity_state(hass, state.entity_id) for state in states]
    except Exception as e:
        _LOGGER.exception("Error getting entities by domain: %s", str(e))
        return [{"error": f"Error getting entities for domain {domain}: {str(e)}"}]


async def get_entities_by_device_class(
    hass: HomeAssistant, device_class: str, domain: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get all entities with a specific device_class.

    Args:
        hass: Home Assistant instance.
        device_class: The device class to filter by (e.g., 'temperature', 'humidity', 'motion')
        domain: Optional domain to restrict search (e.g., 'sensor', 'binary_sensor')

    Returns:
        List of entity state dictionaries that match the device_class
    """
    try:
        _LOGGER.debug(
            "Requesting all entities with device_class: %s (domain: %s)",
            device_class,
            domain or "all",
        )
        matching_entities = []

        for state in hass.states.async_all():
            if domain and not state.entity_id.startswith(f"{domain}."):
                continue

            entity_device_class = state.attributes.get("device_class")
            if entity_device_class == device_class:
                matching_entities.append(state.entity_id)

        _LOGGER.debug(
            "Found %d entities with device_class %s",
            len(matching_entities),
            device_class,
        )

        return [
            await get_entity_state(hass, entity_id)
            for entity_id in matching_entities
        ]

    except Exception as e:
        _LOGGER.exception("Error getting entities by device_class: %s", str(e))
        return [
            {
                "error": f"Error getting entities with device_class {device_class}: {str(e)}"
            }
        ]


async def get_climate_related_entities(
    hass: HomeAssistant,
) -> List[Dict[str, Any]]:
    """Get all climate-related entities including climate domain and temperature/humidity sensors."""
    try:
        _LOGGER.debug("Requesting all climate-related entities")
        climate_entities = []

        climate_domain = await get_entities_by_domain(hass, "climate")
        climate_entities.extend(climate_domain)

        temp_sensors = await get_entities_by_device_class(
            hass, "temperature", "sensor"
        )
        climate_entities.extend(temp_sensors)

        humidity_sensors = await get_entities_by_device_class(
            hass, "humidity", "sensor"
        )
        climate_entities.extend(humidity_sensors)

        seen_entity_ids = set()
        unique_entities = []
        for entity in climate_entities:
            entity_id = entity.get("entity_id")
            if entity_id and entity_id not in seen_entity_ids:
                seen_entity_ids.add(entity_id)
                unique_entities.append(entity)

        _LOGGER.debug(
            "Found %d total climate-related entities (deduplicated from %d)",
            len(unique_entities),
            len(climate_entities),
        )
        return unique_entities

    except Exception as e:
        _LOGGER.exception("Error getting climate-related entities: %s", str(e))
        return [{"error": f"Error getting climate-related entities: {str(e)}"}]


async def get_entities_by_area(
    hass: HomeAssistant, area_id: str
) -> List[Dict[str, Any]]:
    """Get all entities for a specific area."""
    try:
        _LOGGER.debug("Requesting all entities for area: %s", area_id)

        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)

        entities_in_area = []

        for entity in entity_registry.entities.values():
            if entity.area_id == area_id:
                entities_in_area.append(entity.entity_id)
            elif entity.device_id:
                device = device_registry.devices.get(entity.device_id)
                if device and device.area_id == area_id:
                    entities_in_area.append(entity.entity_id)

        _LOGGER.debug(
            "Found %d entities in area %s", len(entities_in_area), area_id
        )

        result = []
        for entity_id in entities_in_area:
            state_info = await get_entity_state(hass, entity_id)
            if not state_info.get("error"):
                result.append(state_info)

        return result

    except Exception as e:
        _LOGGER.exception("Error getting entities by area: %s", str(e))
        return [{"error": f"Error getting entities for area {area_id}: {str(e)}"}]


async def get_entities(
    hass: HomeAssistant,
    area_id: Optional[str] = None,
    area_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Get entities by area(s) - flexible method that supports single area or multiple areas."""
    try:
        areas_to_process = []

        if area_ids:
            if isinstance(area_ids, list):
                areas_to_process = area_ids
            else:
                areas_to_process = [area_ids]
        elif area_id:
            if isinstance(area_id, list):
                areas_to_process = area_id
            else:
                areas_to_process = [area_id]
        else:
            return [{"error": "No area_id or area_ids provided"}]

        _LOGGER.debug("Requesting entities for areas: %s", areas_to_process)

        all_entities = []
        for area in areas_to_process:
            entities_in_area = await get_entities_by_area(hass, area)
            all_entities.extend(entities_in_area)

        seen_entities = set()
        unique_entities = []
        for entity in all_entities:
            if isinstance(entity, dict) and "entity_id" in entity:
                if entity["entity_id"] not in seen_entities:
                    seen_entities.add(entity["entity_id"])
                    unique_entities.append(entity)
            else:
                unique_entities.append(entity)

        _LOGGER.debug(
            "Found %d unique entities across %d areas",
            len(unique_entities),
            len(areas_to_process),
        )
        return unique_entities

    except Exception as e:
        _LOGGER.exception("Error getting entities: %s", str(e))
        return [{"error": f"Error getting entities: {str(e)}"}]


async def get_calendar_events(
    hass: HomeAssistant, entity_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get calendar events, optionally filtered by entity_id."""
    try:
        if entity_id:
            _LOGGER.debug(
                "Requesting calendar events for specific entity: %s", entity_id
            )
            return [await get_entity_state(hass, entity_id)]

        _LOGGER.debug("Requesting all calendar events")
        return await get_entities_by_domain(hass, "calendar")
    except Exception as e:
        _LOGGER.exception("Error getting calendar events: %s", str(e))
        return [{"error": f"Error getting calendar events: {str(e)}"}]


async def get_automations(hass: HomeAssistant) -> List[Dict[str, Any]]:
    """Get all automations."""
    try:
        _LOGGER.debug("Requesting all automations")
        return await get_entities_by_domain(hass, "automation")
    except Exception as e:
        _LOGGER.exception("Error getting automations: %s", str(e))
        return [{"error": f"Error getting automations: {str(e)}"}]


async def get_entity_registry(hass: HomeAssistant) -> List[Dict]:
    """Get entity registry entries with device_class and other metadata."""
    _LOGGER.debug("Requesting all entity registry entries")
    try:
        from homeassistant.helpers import area_registry as ar
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        entity_registry = er.async_get(hass)
        if not entity_registry:
            return []

        device_registry = dr.async_get(hass)
        area_registry = ar.async_get(hass)

        result = []
        for entry in entity_registry.entities.values():
            state = hass.states.get(entry.entity_id)
            device_class = state.attributes.get("device_class") if state else None
            state_class = state.attributes.get("state_class") if state else None
            unit_of_measurement = (
                state.attributes.get("unit_of_measurement") if state else None
            )

            area_id = entry.area_id
            area_name = None

            if not area_id and entry.device_id and device_registry:
                device_entry = device_registry.async_get(entry.device_id)
                if device_entry and hasattr(device_entry, "area_id"):
                    area_id = device_entry.area_id

            if area_id and area_registry:
                area_entry = area_registry.async_get_area(area_id)
                if area_entry and hasattr(area_entry, "name"):
                    area_name = area_entry.name

            result.append(
                {
                    "entity_id": entry.entity_id,
                    "device_id": entry.device_id,
                    "platform": entry.platform,
                    "disabled": entry.disabled,
                    "area_id": area_id,
                    "area_name": area_name,
                    "original_name": entry.original_name,
                    "unique_id": entry.unique_id,
                    "device_class": device_class,
                    "state_class": state_class,
                    "unit_of_measurement": unit_of_measurement,
                }
            )

        return result
    except Exception as e:
        _LOGGER.exception("Error getting entity registry entries: %s", str(e))
        return [{"error": f"Error getting entity registry entries: {str(e)}"}]


async def get_device_registry(hass: HomeAssistant) -> List[Dict]:
    """Get device registry entries."""
    _LOGGER.debug("Requesting all device registry entries")
    try:
        from homeassistant.helpers import device_registry as dr

        registry = dr.async_get(hass)
        if not registry:
            return []
        return [
            {
                "id": device.id,
                "name": device.name,
                "model": device.model,
                "manufacturer": device.manufacturer,
                "sw_version": device.sw_version,
                "hw_version": device.hw_version,
                "connections": (
                    list(device.connections) if device.connections else []
                ),
                "identifiers": (
                    list(device.identifiers) if device.identifiers else []
                ),
                "area_id": device.area_id,
                "disabled": device.disabled_by is not None,
                "entry_type": (
                    device.entry_type.value if device.entry_type else None
                ),
                "name_by_user": device.name_by_user,
            }
            for device in registry.devices.values()
        ]
    except Exception as e:
        _LOGGER.exception("Error getting device registry entries: %s", str(e))
        return [{"error": f"Error getting device registry entries: {str(e)}"}]


async def get_history(
    hass: HomeAssistant, entity_id: str, hours: int = 24
) -> List[Dict]:
    """Get historical state changes for an entity."""
    _LOGGER.debug("Requesting historical state changes for entity: %s", entity_id)
    try:
        from homeassistant.components.recorder.history import get_significant_states

        now = dt_util.utcnow()
        start = now - timedelta(hours=hours)

        history_data = await hass.async_add_executor_job(
            get_significant_states,
            hass,
            start,
            now,
            [entity_id],
        )

        result = []
        for entity_id_key, states in history_data.items():
            for state in states:
                if isinstance(state, dict):
                    continue
                result.append(
                    {
                        "entity_id": state.entity_id,
                        "state": state.state,
                        "last_changed": state.last_changed.isoformat(),
                        "last_updated": state.last_updated.isoformat(),
                        "attributes": dict(state.attributes),
                    }
                )
        return result
    except Exception as e:
        _LOGGER.exception("Error getting history: %s", str(e))
        return [{"error": f"Error getting history: {str(e)}"}]


async def get_area_registry(hass: HomeAssistant) -> Dict[str, Any]:
    """Get area registry information."""
    _LOGGER.debug("Get area registry information")
    try:
        from homeassistant.helpers import area_registry as ar

        registry = ar.async_get(hass)
        if not registry:
            return {}

        result = {}
        for area in registry.areas.values():
            result[area.id] = {
                "name": area.name,
                "normalized_name": area.normalized_name,
                "picture": area.picture,
                "icon": area.icon,
                "floor_id": area.floor_id,
                "labels": list(area.labels) if area.labels else [],
            }
        return result
    except Exception as e:
        _LOGGER.exception("Error getting area registry: %s", str(e))
        return {"error": f"Error getting area registry: {str(e)}"}


async def get_person_data(hass: HomeAssistant) -> List[Dict]:
    """Get person tracking information."""
    _LOGGER.debug("Requesting person tracking information")
    try:
        result = []
        for state in hass.states.async_all("person"):
            result.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.attributes.get("friendly_name", state.entity_id),
                    "state": state.state,
                    "latitude": state.attributes.get("latitude"),
                    "longitude": state.attributes.get("longitude"),
                    "source": state.attributes.get("source"),
                    "gps_accuracy": state.attributes.get("gps_accuracy"),
                    "last_changed": (
                        state.last_changed.isoformat()
                        if state.last_changed
                        else None
                    ),
                }
            )
        return result
    except Exception as e:
        _LOGGER.exception("Error getting person tracking information: %s", str(e))
        return [{"error": f"Error getting person tracking information: {str(e)}"}]


async def get_statistics(hass: HomeAssistant, entity_id: str) -> Dict:
    """Get statistics for an entity."""
    _LOGGER.debug("Requesting statistics for entity: %s", entity_id)
    try:
        from homeassistant.components import recorder

        if not hass.data.get(recorder.DATA_INSTANCE):
            return {"error": "Recorder component is not available"}

        import homeassistant.components.recorder.statistics as stats_module

        stats = await hass.async_add_executor_job(
            stats_module.get_last_short_term_statistics,
            hass,
            1,
            entity_id,
            True,
            set(),
        )

        if entity_id in stats:
            stat_data = stats[entity_id][0] if stats[entity_id] else {}
            return {
                "entity_id": entity_id,
                "start": stat_data.get("start"),
                "mean": stat_data.get("mean"),
                "min": stat_data.get("min"),
                "max": stat_data.get("max"),
                "last_reset": stat_data.get("last_reset"),
                "state": stat_data.get("state"),
                "sum": stat_data.get("sum"),
            }
        else:
            return {"error": f"No statistics available for entity {entity_id}"}
    except Exception as e:
        _LOGGER.exception("Error getting statistics: %s", str(e))
        return {"error": f"Error getting statistics: {str(e)}"}


async def get_scenes(hass: HomeAssistant) -> List[Dict]:
    """Get scene configurations."""
    _LOGGER.debug("Requesting scene configurations")
    try:
        result = []
        for state in hass.states.async_all("scene"):
            result.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.attributes.get("friendly_name", state.entity_id),
                    "last_activated": state.attributes.get("last_activated"),
                    "icon": state.attributes.get("icon"),
                    "last_changed": (
                        state.last_changed.isoformat()
                        if state.last_changed
                        else None
                    ),
                }
            )
        return result
    except Exception as e:
        _LOGGER.exception("Error getting scene configurations: %s", str(e))
        return [{"error": f"Error getting scene configurations: {str(e)}"}]


async def get_weather_data(hass: HomeAssistant) -> Dict[str, Any]:
    """Get weather data from any available weather entity in the system."""
    try:
        weather_entities = [
            state
            for state in hass.states.async_all()
            if state.domain == "weather"
        ]

        if not weather_entities:
            return {
                "error": "No weather entities found in the system. Please add a weather integration."
            }

        state = weather_entities[0]
        _LOGGER.debug("Using weather entity: %s", state.entity_id)

        all_attributes = state.attributes
        _LOGGER.debug(
            "Available weather attributes: %s", json.dumps(all_attributes)
        )

        forecast = all_attributes.get("forecast", [])

        processed_forecast = []
        for day in forecast:
            forecast_entry = {
                "datetime": day.get("datetime"),
                "temperature": day.get("temperature"),
                "condition": day.get("condition"),
                "precipitation": day.get("precipitation"),
                "precipitation_probability": day.get("precipitation_probability"),
                "humidity": day.get("humidity"),
                "wind_speed": day.get("wind_speed"),
                "wind_bearing": day.get("wind_bearing"),
            }
            if any(v is not None for v in forecast_entry.values()):
                processed_forecast.append(forecast_entry)

        current = {
            "entity_id": state.entity_id,
            "temperature": all_attributes.get("temperature"),
            "humidity": all_attributes.get("humidity"),
            "pressure": all_attributes.get("pressure"),
            "wind_speed": all_attributes.get("wind_speed"),
            "wind_bearing": all_attributes.get("wind_bearing"),
            "condition": state.state,
            "forecast_available": len(processed_forecast) > 0,
        }

        _LOGGER.debug(
            "Processed weather data: %s",
            json.dumps(
                {"current": current, "forecast_count": len(processed_forecast)}
            ),
        )

        return {"current": current, "forecast": processed_forecast}
    except Exception as e:
        _LOGGER.exception("Error getting weather data: %s", str(e))
        return {"error": f"Error getting weather data: {str(e)}"}


async def get_dashboards(hass: HomeAssistant) -> List[Dict[str, Any]]:
    """Get list of all dashboards."""
    try:
        _LOGGER.debug("Requesting all dashboards")

        ws_api = hass.data.get("websocket_api")
        if not ws_api:
            return [{"error": "WebSocket API not available"}]

        try:
            from homeassistant.components.lovelace import DOMAIN as LOVELACE_DOMAIN

            lovelace_data = hass.data.get(LOVELACE_DOMAIN)
            if lovelace_data is None:
                return [{"error": "Lovelace not available"}]

            if not hasattr(lovelace_data, "dashboards"):
                return [{"error": "Lovelace dashboards not available"}]

            dashboards = lovelace_data.dashboards
            yaml_configs = getattr(lovelace_data, "yaml_dashboards", {}) or {}

            dashboard_list = []

            for url_path, dashboard_obj in dashboards.items():
                yaml_config = yaml_configs.get(url_path, {}) or {}

                title = yaml_config.get("title")
                if not title:
                    title = (
                        "Overview"
                        if url_path is None
                        else (url_path or "Dashboard")
                    )

                icon = yaml_config.get("icon")
                if not icon:
                    icon = "mdi:home" if url_path is None else "mdi:view-dashboard"

                show_in_sidebar = yaml_config.get("show_in_sidebar", True)
                require_admin = yaml_config.get("require_admin", False)

                dashboard_list.append(
                    {
                        "url_path": url_path,
                        "title": title,
                        "icon": icon,
                        "show_in_sidebar": show_in_sidebar,
                        "require_admin": require_admin,
                    }
                )

            _LOGGER.debug("Found %d dashboards", len(dashboard_list))
            return dashboard_list

        except Exception as e:
            _LOGGER.warning("Could not get dashboards via lovelace: %s", str(e))
            return [{"error": f"Could not retrieve dashboards: {str(e)}"}]

    except Exception as e:
        _LOGGER.exception("Error getting dashboards: %s", str(e))
        return [{"error": f"Error getting dashboards: {str(e)}"}]


async def get_dashboard_config(
    hass: HomeAssistant, dashboard_url: Optional[str] = None
) -> Dict[str, Any]:
    """Get configuration of a specific dashboard."""
    try:
        _LOGGER.debug(
            "Requesting dashboard config for: %s", dashboard_url or "default"
        )

        try:
            from homeassistant.components.lovelace import DOMAIN as LOVELACE_DOMAIN

            lovelace_data = hass.data.get(LOVELACE_DOMAIN)
            if lovelace_data is None:
                return {"error": "Lovelace not available"}

            if not hasattr(lovelace_data, "dashboards"):
                return {"error": "Lovelace dashboards not available"}

            dashboards = lovelace_data.dashboards
            dashboard_key = None if dashboard_url is None else dashboard_url
            if dashboard_key in dashboards:
                dashboard = dashboards[dashboard_key]
                config = await dashboard.async_get_info()
                return dict(config) if config else {"error": "No dashboard config"}
            else:
                if dashboard_url is None:
                    return {"error": "Default dashboard not found"}
                else:
                    return {"error": f"Dashboard '{dashboard_url}' not found"}

        except Exception as e:
            _LOGGER.warning("Could not get dashboard config: %s", str(e))
            return {"error": f"Could not retrieve dashboard config: {str(e)}"}

    except Exception as e:
        _LOGGER.exception("Error getting dashboard config: %s", str(e))
        return {"error": f"Error getting dashboard config: {str(e)}"}
