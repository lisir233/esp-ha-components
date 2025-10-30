"""ESP HA Interactive Input platform."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback

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
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the Interactive Input entity."""
        SensorEntity.__init__(self)
        self._hass = hass
        self._node_id = str(node_id).replace(":", "").lower()
        self._device_name = device_name
        self._device_info = device_info

        # Entity attributes
        self._attr_has_entity_name = True
        self._attr_name = "Interactive Input"
        self._attr_unique_id = f"{DOMAIN}_{self._node_id}_interactive_input"
        self._attr_should_poll = False
        self._attr_device_class = SensorDeviceClass.ENUM  # Use enum type
        self._attr_state_class = None  # Enum type doesn't need state class
        self._attr_native_unit_of_measurement = None  # Enum type doesn't need unit

        # Initialize state with instance variables
        self._attr_native_value = "none"
        self._current_state = {
            "input_type": "button",
            "last_event": "none",
            "current_value": 0.0,
            "input_config": {},
            "input_mapping": {},
            "sensitivity": 50,
            "node_id": self._node_id,
            "device_name": self._device_name,
            "last_update": time.time(),
        }

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
            ]
        )

        _LOGGER.info("Interactive input entity added to HA: %s", self._attr_name)

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        input_type = self._current_state.get("input_type", "button")
        last_event = self._current_state.get("last_event", "none")
        return get_input_icon(input_type, last_event)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        # Unsubscribe from HA bus event listeners
        for unsubscribe_func in self._unsub_listeners:
            if callable(unsubscribe_func):
                unsubscribe_func()

        await super().async_will_remove_from_hass()

    @callback
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
                updates["last_update"] = time.time()
                self._current_state.update(updates)

                # Update entity's native value
                self._attr_native_value = updates.get(
                    "last_event", self._current_state.get("last_event", "none")
                )

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
                updates["last_update"] = time.time()
                self._current_state.update(updates)

                # Update entity's native value
                self._attr_native_value = updates.get(
                    "last_event", self._current_state.get("last_event", "none")
                )

                _LOGGER.debug(
                    "Interactive input updated from direct event: %s", updates
                )

        except Exception:  # Broad exception acceptable for callback event handling
            _LOGGER.exception("Error processing direct interactive input update")

    def _update_internal_state(self, state_data: dict) -> None:
        """Update internal entity state from state data."""
        self._current_state.update(state_data)
        self._attr_native_value = state_data.get("last_event", "none")
        self._attr_extra_state_attributes = {
            "input_type": state_data.get("input_type", "button"),
            "last_event": state_data.get("last_event", "none"),
            "current_value": state_data.get("current_value", 0.0),
            "input_config": state_data.get("input_config", {}),
            "input_mapping": state_data.get("input_mapping", {}),
            "sensitivity": state_data.get("sensitivity", 50),
            "node_id": self._node_id,
            "device_name": self._device_name,
            "last_update": state_data.get("last_update", time.time()),
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes - only ESP32 reported parameters."""
        input_type = self._current_state.get("input_type", "button")

        # Show all ESP32 parameters exactly as reported
        attrs = {
            # Core parameters from ESP32
            "input_type": input_type,
            "input_events": self._current_state.get("last_event", "none"),  # ESP32 param name
            "input_value": self._current_state.get("current_value", 0.0),  # ESP32 param name
            "last_event": self._current_state.get("last_event", "none"),
            "sensitivity": self._current_state.get("sensitivity", 50),
        }

        # Add config if it has meaningful content
        input_config = self._current_state.get("input_config", {})
        if input_config and input_config not in ({}, "{}"):
            attrs["input_config"] = input_config

        # Add mapping if it has meaningful content
        input_mapping = self._current_state.get("input_mapping", {})
        if input_mapping and input_mapping not in ({}, "{}"):
            attrs["input_mapping"] = input_mapping

        return attrs

    async def async_set_input_type(self, input_type: str) -> None:
        """Set input type (button, rotary, touch)."""
        if not validate_input_type(input_type):
            _LOGGER.error("Unsupported input type: %s", input_type)
            return

        _LOGGER.info("Setting input type: %s -> %s", self._attr_name, input_type)

        # Update state
        self._current_state["input_type"] = input_type
        self._current_state["last_update"] = time.time()

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
        self._current_state["sensitivity"] = sensitivity
        self._current_state["last_update"] = time.time()

        # Send config to device through event bus
        self.hass.bus.async_fire(
            f"{DOMAIN}_send_input_config",
            {"device_node_id": self._node_id, "sensitivity": sensitivity},
        )

    async def async_set_input_mapping(self, mapping: dict[str, Any]) -> None:
        """Set input event mapping configuration."""
        _LOGGER.info("Setting input mapping: %s -> %s", self._attr_name, mapping)

        # Update state
        self._current_state["input_mapping"] = mapping
        self._current_state["last_update"] = time.time()

        # Send config to device through event bus
        self.hass.bus.async_fire(
            f"{DOMAIN}_send_input_config",
            {"device_node_id": self._node_id, "input_mapping": json.dumps(mapping)},
        )

    async def async_trigger_event(
        self, event_type: str, event_value: float | None = None
    ) -> None:
        """Manually trigger an input event (for testing)."""
        input_type = self._current_state.get("input_type", "button")

        if event_type not in INPUT_TYPE_EVENTS.get(input_type, []):
            _LOGGER.error(
                "Unsupported input event: %s (current type: %s)", event_type, input_type
            )
            return

        _LOGGER.info(
            "Manually triggering input event: %s -> %s (value: %s)",
            self._attr_name,
            event_type,
            event_value,
        )

        # Update state
        self._current_state.update(
            {
                "last_event": event_type,
                "current_value": event_value if event_value is not None else 0.0,
                "last_update": time.time(),
            }
        )

        # Send event through event bus
        self.hass.bus.async_fire(
            f"{DOMAIN}_trigger_input_event",
            {
                "device_node_id": self._node_id,
                "event_type": event_type,
                "event_value": event_value,
            },
        )
