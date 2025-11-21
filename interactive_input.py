"""ESP HA Interactive Input platform."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant

from .const import DOMAIN, get_device_info
from .esp_iot import (
    INPUT_TYPE_EVENTS,
    get_input_icon,
    parse_direct_input_update,
    parse_esp32_input_update,
    validate_input_type,
    validate_sensitivity,
)

_LOGGER = logging.getLogger(__name__)


class ESPHomeInteractiveInput(SensorEntity):
    """Representation of an ESP HA Interactive Input entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        node_id: str,
        device_name: str,
    ) -> None:
        """Initialize the Interactive Input entity."""
        SensorEntity.__init__(self)
        self._hass = hass
        self._node_id = str(node_id).replace(":", "").lower()
        self._device_name = device_name

        # Entity attributes
        self._attr_has_entity_name = True
        self._attr_name = "Interactive Input"
        self._attr_unique_id = f"{DOMAIN}_{self._node_id}_interactive_input"
        self._attr_should_poll = False
        self._attr_device_class = SensorDeviceClass.ENUM  # Use enum type
        self._attr_state_class = None  # Enum type doesn't need state class
        self._attr_native_unit_of_measurement = None

        # Core attributes
        self._attr_native_value = "none"
        self._input_type = "button"
        self._input_events = "none"
        self._last_update = time.time()

        # Device information
        self._attr_device_info = get_device_info(self._node_id, device_name)

        _LOGGER.debug(
            "Interactive input entity initialized: %s (node_id: %s)",
            self._attr_name,
            self._node_id,
        )

        # Store unsubscribe callbacks for cleanup
        self._unsub_listeners = []

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # Subscribe to Home Assistant bus events for ESP32 updates
        self._unsub_listeners.extend(
            [
                self.hass.bus.async_listen(
                    f"{DOMAIN}_interactive_input_update", self._handle_ha_bus_event
                ),
                self.hass.bus.async_listen(
                    f"{DOMAIN}_interactive_input_controller_update",
                    self._handle_ha_bus_event,
                ),
                # Subscribe to device availability changes
                self.hass.bus.async_listen(
                    f"{DOMAIN}_device_availability_changed",
                    self._handle_device_availability_change,
                ),
            ]
        )

        _LOGGER.info("Interactive input entity added to HA: %s", self._attr_name)

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return get_input_icon(self._input_type, self._input_events)

    @property
    def available(self) -> bool:
        """Return True if entity is available (device is connected)."""
        api = self._hass.data.get(DOMAIN, {}).get("shared_api")
        if api:
            return api.is_device_available(self._node_id)
        return False

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        # Unsubscribe from HA bus event listeners
        for unsubscribe_func in self._unsub_listeners:
            if callable(unsubscribe_func):
                unsubscribe_func()

        await super().async_will_remove_from_hass()

    async def _handle_device_availability_change(self, event) -> None:
        """Handle device availability change (offline/online) - update entity state."""
        try:
            event_node_id = str(event.data.get("node_id", "")).replace(":", "").lower()

            if event_node_id == self._node_id:
                available = event.data.get("available", False)
                _LOGGER.debug(
                    "Device %s availability changed to %s, updating interactive input entity %s",
                    event_node_id,
                    "available" if available else "unavailable",
                    self._attr_unique_id,
                )
                self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error(
                "Error handling device availability change for %s: %s",
                self._attr_unique_id,
                err,
            )

    async def _handle_ha_bus_event(self, event) -> None:
        """Handle Home Assistant bus events for ESP32 updates."""
        event_data = event.data

        # Filter by node_id (normalize for comparison)
        event_node_id = str(event_data.get("node_id", "")).replace(":", "").lower()
        if event_node_id != self._node_id:
            return

        _LOGGER.debug(
            "Interactive input entity received event: %s, data: %s",
            event.event_type,
            event_data,
        )

        try:
            # Handle ESP32 interactive input updates
            if event.event_type == f"{DOMAIN}_interactive_input_controller_update":
                await self._handle_esp32_update(event_data)
            elif event.event_type == f"{DOMAIN}_interactive_input_update":
                await self._handle_esp32_direct_update(event_data)

            # Update Home Assistant entity state
            self.async_write_ha_state()

        except Exception:  # Broad exception acceptable for callback event handling
            _LOGGER.exception(
                "Error handling HA bus event %s",
                event.event_type,
            )

    async def _handle_esp32_update(self, event_data: dict) -> None:
        """Handle ESP32 data updates from f"{DOMAIN}_interactive_input_controller_update" events."""
        try:
            # Extract parameters from ESP32 update
            params = event_data.get("params", [])
            _LOGGER.debug(
                "Processing interactive input ESP32 update with %d parameters",
                len(params),
            )

            # Parse parameter updates using utility function
            updates = parse_esp32_input_update(params)

            # Apply updates to state
            if updates:
                self._last_update = time.time()

                # Update core attributes (必选)
                if "input_type" in updates:
                    self._input_type = updates["input_type"]
                    _LOGGER.info("Updated input_type: %s", self._input_type)

                if "last_event" in updates:
                    self._input_events = updates["last_event"]
                    self._attr_native_value = updates["last_event"]
                    _LOGGER.info("Updated input_events: %s", self._input_events)

                # Update extended attributes (可选)
                if "current_value" in updates:
                    self._input_value = updates["current_value"]
                    _LOGGER.debug("Updated input_value: %.2f", self._input_value)

                if "sensitivity" in updates:
                    self._sensitivity = updates["sensitivity"]
                    _LOGGER.info("Updated sensitivity: %d", self._sensitivity)

                if "input_config" in updates:
                    self._input_config = updates["input_config"]
                    _LOGGER.debug("Updated input_config: %s", self._input_config)

                if "input_mapping" in updates:
                    self._input_mapping = updates["input_mapping"]
                    _LOGGER.debug("Updated input_mapping: %s", self._input_mapping)

                _LOGGER.info("Interactive input state updated from ESP32: %s", updates)

        except Exception:  # Broad exception acceptable for callback event handling
            _LOGGER.exception("Error processing ESP32 interactive input update")

    async def _handle_esp32_direct_update(self, event_data: dict) -> None:
        """Handle direct input updates from f"{DOMAIN}_interactive_input_update" events."""
        try:
            input_data = event_data.get("input_data", {})

            # Parse direct input updates using utility function
            updates = parse_direct_input_update(input_data)

            # Apply updates
            if updates:
                self._last_update = time.time()

                # Update core attributes (必选)
                if "input_type" in updates:
                    self._input_type = updates["input_type"]

                if "last_event" in updates:
                    self._input_events = updates["last_event"]
                    self._attr_native_value = updates["last_event"]

                # Update extended attributes (可选)
                if "current_value" in updates:
                    self._input_value = updates["current_value"]

                if "sensitivity" in updates:
                    self._sensitivity = updates["sensitivity"]

                if "input_config" in updates:
                    self._input_config = updates["input_config"]

                if "input_mapping" in updates:
                    self._input_mapping = updates["input_mapping"]

                _LOGGER.debug(
                    "Interactive input updated from direct event: %s", updates
                )

        except Exception:  # Broad exception acceptable for callback event handling
            _LOGGER.exception("Error processing direct interactive input update")

    def _update_internal_state(self, state_data: dict) -> None:
        """Update internal entity state from state data."""
        # Update core attributes
        if "input_type" in state_data:
            self._input_type = state_data["input_type"]
        if "last_event" in state_data:
            self._input_events = state_data["last_event"]
            self._attr_native_value = state_data["last_event"]

        # Update extended attributes
        if "current_value" in state_data:
            self._input_value = state_data["current_value"]
        if "sensitivity" in state_data:
            self._sensitivity = state_data["sensitivity"]
        if "input_config" in state_data:
            self._input_config = state_data["input_config"]
        if "input_mapping" in state_data:
            self._input_mapping = state_data["input_mapping"]

        self._last_update = state_data.get("last_update", time.time())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "input_type": self._input_type,
            "input_events": self._input_events,
        }

        if hasattr(self, "_input_value"):
            attrs["input_value"] = self._input_value

        if hasattr(self, "_sensitivity"):
            attrs["sensitivity"] = self._sensitivity

        if hasattr(self, "_input_config"):
            if self._input_config and self._input_config not in ({}, "{}"):
                attrs["input_config"] = self._input_config

        if hasattr(self, "_input_mapping"):
            if self._input_mapping and self._input_mapping not in ({}, "{}"):
                attrs["input_mapping"] = self._input_mapping

        return attrs

    async def async_set_input_type(self, input_type: str) -> None:
        """Set input type (button, rotary, touch)."""
        if not validate_input_type(input_type):
            _LOGGER.error("Unsupported input type: %s", input_type)
            return

        _LOGGER.info("Setting input type: %s -> %s", self._attr_name, input_type)

        # Update state
        self._input_type = input_type
        self._last_update = time.time()

        # Send config to device through event bus
        self.hass.bus.async_fire(
            f"{DOMAIN}_send_input_config",
            {"device_node_id": self._node_id, "input_type": input_type},
        )

    async def async_set_sensitivity(self, sensitivity: int) -> None:
        """Set input sensitivity (0-100)."""
        if not validate_sensitivity(sensitivity):
            _LOGGER.error(
                "Sensitivity value out of range: %d (should be 0-100)", sensitivity
            )
            return

        _LOGGER.info("Setting sensitivity: %s -> %d", self._attr_name, sensitivity)

        # Update state
        self._sensitivity = sensitivity
        self._last_update = time.time()

        # Send config to device through event bus
        self.hass.bus.async_fire(
            f"{DOMAIN}_send_input_config",
            {"device_node_id": self._node_id, "sensitivity": sensitivity},
        )

    async def async_set_input_mapping(self, mapping: dict[str, Any]) -> None:
        """Set input event mapping configuration."""
        _LOGGER.info("Setting input mapping: %s -> %s", self._attr_name, mapping)

        # Update state
        self._input_mapping = mapping
        self._last_update = time.time()

        # Send config to device through event bus
        self.hass.bus.async_fire(
            f"{DOMAIN}_send_input_config",
            {"device_node_id": self._node_id, "input_mapping": json.dumps(mapping)},
        )

    async def async_trigger_event(
        self, event_type: str, event_value: float | None = None
    ) -> None:
        """Manually trigger an input event (for testing)."""
        if event_type not in INPUT_TYPE_EVENTS.get(self._input_type, []):
            _LOGGER.error(
                "Unsupported input event: %s (current type: %s)",
                event_type,
                self._input_type,
            )
            return

        _LOGGER.info(
            "Manually triggering input event: %s -> %s (value: %s)",
            self._attr_name,
            event_type,
            event_value,
        )

        # Update state
        self._input_events = event_type
        if event_value is not None:
            self._input_value = event_value
        self._last_update = time.time()

        # Send event through event bus
        self.hass.bus.async_fire(
            f"{DOMAIN}_trigger_input_event",
            {
                "device_node_id": self._node_id,
                "event_type": event_type,
                "event_value": event_value,
            },
        )
