"""Config flow for the esp_ha integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import selector

from .const import (
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
    DEFAULT_PORT,
    DOMAIN,
)
from .esp_iot.esp_local_ctrl_lib import esp_local_ctrl
from .esp_iot.esp_iot_client import ESPSecurityManager
from .esp_iot.esp_iot_network import ESPDeviceListener, async_discover_devices

_LOGGER = logging.getLogger(__name__)


class ESPHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ESP HA integration.

    Supports automatic device discovery via mDNS/Zeroconf,
    security detection, and PoP authentication for secure devices.
    """

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._security_detected: dict[str, Any] | None = None
        self._device_info: dict[str, Any] | None = None
        self._discovered_devices: list[dict[str, Any]] = []
        self._available_devices: list[dict[str, Any]] = []
        self._selected_device: dict[str, Any] | None = None
        self._security_manager = ESPSecurityManager(esp_local_ctrl)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user.

        Automatically discovers ESP devices on the network via mDNS/Zeroconf.

        Args:
            user_input: Not used, flow starts automatically

        Returns:
            Flow result directing to device setup or abort if no devices found
        """
        self._security_manager.clear_cache()

        discovered_devices = await async_discover_devices(self.hass, ESPDeviceListener)
        if not discovered_devices:
            return self.async_abort(reason="no_devices_found")

        self._discovered_devices = discovered_devices

        existing_entries = self.hass.config_entries.async_entries(DOMAIN)
        existing_node_ids = {
            entry.unique_id for entry in existing_entries if entry.unique_id
        }

        # Process discovered devices and filter out already configured ones
        self._available_devices = []
        for device in discovered_devices:
            if device["node_id"] in existing_node_ids:
                continue

            # Use device_name if available, otherwise use "ESP-{node_id}" format
            if device.get("device_name"):
                name = device["device_name"]
            else:
                name = f"ESP-{device['node_id']}"
            
            self._available_devices.append({
                "ip": device["ip"],
                "node_id": device["node_id"],
                "port": device.get("port", DEFAULT_PORT),
                "name": name,
                "display_name": f"{name} ({device['ip']})",
            })

        if not self._available_devices:
            return self.async_abort(reason="no_new_devices_found")
        return await self.async_step_device_setup()

    async def async_step_device_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle device selection."""
        if user_input is not None:
            selected_node_id = user_input.get("selected_device")
            selected_device = None

            for device in self._available_devices:
                if device["node_id"] == selected_node_id:
                    selected_device = device.copy()
                    break

            if not selected_device:
                return self.async_show_form(
                    step_id="device_setup",
                    data_schema=self._get_device_selection_schema(),
                    errors={"base": "invalid_device_selection"},
                )

            self._selected_device = selected_device
            self._device_info = selected_device

            security_info = await self._security_manager.detect_device_security(
                selected_device["ip"], selected_device["port"], DEFAULT_PORT
            )

            self._security_detected = security_info

            pop_required = security_info.get("pop_required", False)

            if pop_required:
                return await self.async_step_pop_input()

            return await self._create_entry_from_device()

        return self.async_show_form(
            step_id="device_setup",
            data_schema=self._get_device_selection_schema(),
        )

    def _get_device_selection_schema(self) -> vol.Schema:
        """Return schema for device selection."""
        devices_dict = {
            device["node_id"]: device["display_name"]
            for device in self._available_devices
        }

        return vol.Schema(
            {
                vol.Required("selected_device"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"label": label, "value": node_id}
                            for node_id, label in devices_dict.items()
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def async_step_pop_input(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle PoP input step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            pop_value = user_input.get(CONF_CUSTOM_POP, "")

            if not pop_value:
                errors["base"] = "pop_required"
            else:
                device_ip = self._selected_device["ip"]
                device_port = self._selected_device["port"]

                pop_valid = await self._security_manager.test_pop_connection(
                    device_ip, pop_value, device_port
                )

                if pop_valid:
                    self._selected_device[CONF_CUSTOM_POP] = pop_value
                    return await self._create_entry_from_device()

                errors["base"] = "invalid_pop"

        return self.async_show_form(
            step_id="pop_input",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CUSTOM_POP): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "device_name": self._selected_device.get("name", "ESP Device"),
                "device_ip": self._selected_device["ip"],
            },
        )

    async def _create_entry_from_device(self) -> ConfigFlowResult:
        """Create a config entry from selected device."""
        if not self._selected_device:
            return self.async_abort(reason="no_device_selected")

        node_id = self._selected_device["node_id"]

        await self.async_set_unique_id(node_id)
        self._abort_if_unique_id_configured()

        title = self._selected_device.get("name", f"ESP-{node_id}")

        config_data = {
            CONF_HOST: self._selected_device["ip"],
            CONF_PORT: self._selected_device.get("port", DEFAULT_PORT),
            CONF_NODE_ID: node_id,
            "security_version": self._security_detected.get("security_version", 1),
        }

        if CONF_CUSTOM_POP in self._selected_device:
            config_data[CONF_CUSTOM_POP] = self._selected_device[CONF_CUSTOM_POP]
        return self.async_create_entry(
            title=title,
            data=config_data,
        )
