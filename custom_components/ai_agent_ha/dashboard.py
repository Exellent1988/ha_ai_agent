"""Dashboard creation and update using Home Assistant's Lovelace configuration."""

import json
import logging
import os
from typing import Any, Dict

import yaml  # type: ignore[import-untyped]
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def create_dashboard(
    hass: HomeAssistant, dashboard_config: Dict[str, Any]
) -> Dict[str, Any]:
    """Create a new dashboard using Home Assistant's Lovelace WebSocket API."""
    try:
        _LOGGER.debug(
            "Creating dashboard with config: %s",
            json.dumps(dashboard_config, default=str),
        )

        # Validate required fields
        if not dashboard_config.get("title"):
            return {"error": "Dashboard title is required"}

        if not dashboard_config.get("url_path"):
            return {"error": "Dashboard URL path is required"}

        # Sanitize the URL path
        url_path = (
            dashboard_config["url_path"].lower().replace(" ", "-").replace("_", "-")
        )

        # Prepare dashboard configuration for Lovelace
        dashboard_data = {
            "title": dashboard_config["title"],
            "icon": dashboard_config.get("icon", "mdi:view-dashboard"),
            "show_in_sidebar": dashboard_config.get("show_in_sidebar", True),
            "require_admin": dashboard_config.get("require_admin", False),
            "views": dashboard_config.get("views", []),
        }

        try:
            # Create dashboard file directly - this is the most reliable method
            lovelace_config_file = hass.config.path(
                f"ui-lovelace-{url_path}.yaml"
            )

            def write_dashboard_file():
                with open(lovelace_config_file, "w") as f:
                    yaml.dump(
                        dashboard_data,
                        f,
                        default_flow_style=False,
                        allow_unicode=True,
                    )

            await hass.async_add_executor_job(write_dashboard_file)

            _LOGGER.info(
                "Successfully created dashboard file: %s", lovelace_config_file
            )

            # Now update configuration.yaml
            try:
                config_file = hass.config.path("configuration.yaml")
                dashboard_config_entry = {
                    url_path: {
                        "mode": "yaml",
                        "title": dashboard_config["title"],
                        "icon": dashboard_config.get("icon", "mdi:view-dashboard"),
                        "show_in_sidebar": dashboard_config.get(
                            "show_in_sidebar", True
                        ),
                        "filename": f"ui-lovelace-{url_path}.yaml",
                    }
                }

                def update_config_file():
                    try:
                        with open(config_file, "r") as f:
                            content = f.read()

                        dashboard_yaml = f"""    {url_path}:
      mode: yaml
      title: {dashboard_config['title']}
      icon: {dashboard_config.get('icon', 'mdi:view-dashboard')}
      show_in_sidebar: {str(dashboard_config.get('show_in_sidebar', True)).lower()}
      filename: ui-lovelace-{url_path}.yaml"""

                        if "lovelace:" not in content:
                            lovelace_section = f"""
# Lovelace dashboards configuration added by AI Agent
lovelace:
  dashboards:
{dashboard_yaml}
"""
                            with open(config_file, "a") as f:
                                f.write(lovelace_section)
                            return True

                        lines = content.split("\n")
                        new_lines = []
                        dashboard_added = False
                        in_lovelace = False
                        lovelace_indent = 0

                        for i, line in enumerate(lines):
                            new_lines.append(line)

                            if (
                                line.strip() == "lovelace:"
                                or line.strip().startswith("lovelace:")
                            ):
                                in_lovelace = True
                                lovelace_indent = len(line) - len(line.lstrip())
                                continue

                            if in_lovelace:
                                current_indent = (
                                    len(line) - len(line.lstrip())
                                    if line.strip()
                                    else 0
                                )

                                if (
                                    line.strip()
                                    and current_indent <= lovelace_indent
                                    and not line.startswith(" ")
                                ):
                                    if line.strip() != "lovelace:":
                                        in_lovelace = False

                                if in_lovelace and "dashboards:" in line:
                                    new_lines.append(dashboard_yaml)
                                    dashboard_added = True
                                    in_lovelace = False
                                    break

                        if not dashboard_added and "lovelace:" in content:
                            new_lines = []
                            for line in lines:
                                new_lines.append(line)
                                if (
                                    line.strip() == "lovelace:"
                                    or line.strip().startswith("lovelace:")
                                ):
                                    new_lines.append("  dashboards:")
                                    new_lines.append(dashboard_yaml)
                                    dashboard_added = True
                                    break

                        if dashboard_added:
                            with open(config_file, "w") as f:
                                f.write("\n".join(new_lines))
                            return True
                        else:
                            with open(config_file, "a") as f:
                                f.write(f"\n  dashboards:\n{dashboard_yaml}\n")
                            return True

                    except Exception as e:
                        _LOGGER.error(
                            "Failed to update configuration.yaml: %s", str(e)
                        )
                        try:
                            with open(config_file, "r") as f:
                                content = f.read()

                            if "lovelace:" not in content:
                                lovelace_config = f"""
# Lovelace dashboards
lovelace:
  dashboards:
    {url_path}:
      mode: yaml
      title: {dashboard_config['title']}
      icon: {dashboard_config.get('icon', 'mdi:view-dashboard')}
      show_in_sidebar: {str(dashboard_config.get('show_in_sidebar', True)).lower()}
      filename: ui-lovelace-{url_path}.yaml
"""
                                with open(config_file, "a") as f:
                                    f.write(lovelace_config)
                            else:
                                dashboard_entry = f"""    {url_path}:
      mode: yaml
      title: {dashboard_config['title']}
      icon: {dashboard_config.get('icon', 'mdi:view-dashboard')}
      show_in_sidebar: {str(dashboard_config.get('show_in_sidebar', True)).lower()}
      filename: ui-lovelace-{url_path}.yaml
"""
                                lines = content.split("\n")
                                new_lines = []
                                in_dashboards = False
                                dashboards_indented = False

                                for line in lines:
                                    new_lines.append(line)
                                    if (
                                        "dashboards:" in line
                                        and "lovelace"
                                        in content[: content.find(line)]
                                    ):
                                        in_dashboards = True
                                        new_lines.append(dashboard_entry.rstrip())
                                        in_dashboards = False

                                if not any("dashboards:" in line for line in lines):
                                    for i, line in enumerate(new_lines):
                                        if line.strip() == "lovelace:":
                                            new_lines.insert(i + 1, "  dashboards:")
                                            new_lines.insert(
                                                i + 2, dashboard_entry.rstrip()
                                            )
                                            break

                                with open(config_file, "w") as f:
                                    f.write("\n".join(new_lines))

                            return True
                        except Exception as fallback_error:
                            _LOGGER.error(
                                "Fallback config update also failed: %s",
                                str(fallback_error),
                            )
                            return False

                config_updated = await hass.async_add_executor_job(
                    update_config_file
                )

                if config_updated:
                    success_message = f"""Dashboard '{dashboard_config['title']}' created successfully!

✅ Dashboard file created: ui-lovelace-{url_path}.yaml
✅ Configuration.yaml updated automatically

🔄 Please restart Home Assistant to see your new dashboard in the sidebar."""

                    return {
                        "success": True,
                        "message": success_message,
                        "url_path": url_path,
                        "restart_required": True,
                    }
                else:
                    config_instructions = f"""Dashboard '{dashboard_config['title']}' created successfully!

✅ Dashboard file created: ui-lovelace-{url_path}.yaml
⚠️  Could not automatically update configuration.yaml

Please manually add this to your configuration.yaml:

lovelace:
  dashboards:
    {url_path}:
      mode: yaml
      title: {dashboard_config['title']}
      icon: {dashboard_config.get('icon', 'mdi:view-dashboard')}
      show_in_sidebar: {str(dashboard_config.get('show_in_sidebar', True)).lower()}
      filename: ui-lovelace-{url_path}.yaml

Then restart Home Assistant to see your new dashboard in the sidebar."""

                    return {
                        "success": True,
                        "message": config_instructions,
                        "url_path": url_path,
                        "restart_required": True,
                    }

            except Exception as config_error:
                _LOGGER.error(
                    "Error updating configuration.yaml: %s", str(config_error)
                )
                config_instructions = f"""Dashboard '{dashboard_config['title']}' created successfully!

✅ Dashboard file created: ui-lovelace-{url_path}.yaml
⚠️  Could not automatically update configuration.yaml

Please manually add this to your configuration.yaml:

lovelace:
  dashboards:
    {url_path}:
      mode: yaml
      title: {dashboard_config['title']}
      icon: {dashboard_config.get('icon', 'mdi:view-dashboard')}
      show_in_sidebar: {str(dashboard_config.get('show_in_sidebar', True)).lower()}
      filename: ui-lovelace-{url_path}.yaml

Then restart Home Assistant to see your new dashboard in the sidebar."""

                return {
                    "success": True,
                    "message": config_instructions,
                    "url_path": url_path,
                    "restart_required": True,
                }

        except Exception as e:
            _LOGGER.error("Failed to create dashboard file: %s", str(e))
            return {"error": f"Failed to create dashboard file: {str(e)}"}

    except Exception as e:
        _LOGGER.exception("Error creating dashboard: %s", str(e))
        return {"error": f"Error creating dashboard: {str(e)}"}


async def update_dashboard(
    hass: HomeAssistant,
    dashboard_url: str,
    dashboard_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Update an existing dashboard using Home Assistant's Lovelace WebSocket API."""
    try:
        _LOGGER.debug(
            "Updating dashboard %s with config: %s",
            dashboard_url,
            json.dumps(dashboard_config, default=str),
        )

        dashboard_data = {
            "title": dashboard_config.get("title", "Updated Dashboard"),
            "icon": dashboard_config.get("icon", "mdi:view-dashboard"),
            "show_in_sidebar": dashboard_config.get("show_in_sidebar", True),
            "require_admin": dashboard_config.get("require_admin", False),
            "views": dashboard_config.get("views", []),
        }

        try:
            dashboard_file = hass.config.path(
                f"ui-lovelace-{dashboard_url}.yaml"
            )

            def check_file_exists():
                return os.path.exists(dashboard_file)

            file_exists = await hass.async_add_executor_job(check_file_exists)

            if not file_exists:
                dashboard_file = hass.config.path(
                    f"dashboards/{dashboard_url}.yaml"
                )
                file_exists = await hass.async_add_executor_job(
                    lambda: os.path.exists(dashboard_file)
                )

            if file_exists:
                def update_dashboard_file():
                    with open(dashboard_file, "w") as f:
                        yaml.dump(
                            dashboard_data,
                            f,
                            default_flow_style=False,
                            allow_unicode=True,
                        )

                await hass.async_add_executor_job(update_dashboard_file)

                _LOGGER.info(
                    "Successfully updated dashboard file: %s", dashboard_file
                )
                return {
                    "success": True,
                    "message": f"Dashboard '{dashboard_url}' updated successfully!",
                }
            else:
                return {"error": f"Dashboard file for '{dashboard_url}' not found"}

        except Exception as e:
            _LOGGER.error("Failed to update dashboard file: %s", str(e))
            return {"error": f"Failed to update dashboard file: {str(e)}"}

    except Exception as e:
        _LOGGER.exception("Error updating dashboard: %s", str(e))
        return {"error": f"Error updating dashboard: {str(e)}"}
