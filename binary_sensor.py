"""ESP HA Binary Sensor platform."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_NODE_ID, DOMAIN, get_device_info
from .esp_iot import (
    DEFAULT_BINARY_SENSOR_DEVICE_CLASS,
    extract_node_id_from_device_info,
    get_binary_sensor_device_class,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ESP HA Binary Sensor platform from a config entry."""
    api = config_entry.runtime_data
    if not api:
        return

    node_id = config_entry.data.get(CONF_NODE_ID)
    if not node_id:
        return

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    # Store reference for discovery event handler
    entry_key = f"{node_id}_{config_entry.entry_id}"
    hass.data[DOMAIN][f"add_binary_entities_{entry_key}"] = async_add_entities

    # Event handler for binary sensor discovery
    @callback
    async def handle_binary_sensor_discovered(event) -> None:
        """Handle binary sensor discovery events."""
        try:
            event_node_id = str(event.data.get("node_id", "")).replace(":", "").lower()

            # Only process events for this device
            if event_node_id != node_id.lower():
                return

            device_info_data = event.data.get("device_info", {})
            if not device_info_data or "node_id" not in device_info_data:
                device_info_data = {
                    "node_id": event_node_id,
                    "name": event.data.get("device_name", f"ESP {event_node_id}"),
                }

            # Get sensor name from params or use generic name
            params = event.data.get("params", {})
            sensor_name = params.get("name", "Binary Sensor")
            if sensor_name == "Binary Sensor":
                # Try to get a better name from device_class or type
                device_class = params.get("device_class", "")
                if device_class:
                    sensor_name = device_class.replace("_", " ").title()
                else:
                    sensor_name = "Binary Sensor"

            # Create and add binary sensor
            binary_sensor = ESPHomeBinarySensor(
                hass=hass,
                device_info=device_info_data,
                sensor_name=sensor_name,
                sensor_params=event.data.get("params", {}),
            )

            async_add_entities([binary_sensor])
            _LOGGER.debug("Added binary sensor: %s", sensor_name)

        except Exception:
            _LOGGER.exception("Failed to process binary sensor discovery")

    # Register discovery event listener
    hass.bus.async_listen(
        f"{DOMAIN}_binary_sensor_discovered", handle_binary_sensor_discovered
    )


class ESPHomeBinarySensor(BinarySensorEntity):
    """Binary sensor entity for ESP HA devices."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        device_info: dict[str, Any],
        sensor_name: str,
        sensor_params: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the binary sensor entity."""
        self.hass = hass
        self._params = sensor_params or {}

        # Generate unique ID (use generic binary_sensor, not specific sensor type)
        device_node_id = str(device_info.get("node_id", "")).replace(":", "").lower()
        self._attr_unique_id = f"{DOMAIN}_{device_node_id}_binary_sensor"

        # Entity attributes
        self._attr_has_entity_name = True
        self._attr_name = "Binary Sensor"
        self._attr_is_on = self._params.get("state", False)

        # Device class - use utility function for lookup
        device_class_str = self._params.get(
            "device_class", DEFAULT_BINARY_SENSOR_DEVICE_CLASS
        )
        device_class_normalized = get_binary_sensor_device_class(
            device_class_str, DEFAULT_BINARY_SENSOR_DEVICE_CLASS
        )
        # Convert string to enum
        self._attr_device_class = getattr(
            BinarySensorDeviceClass, device_class_normalized.upper()
        )

        # Device info
        self._attr_device_info = get_device_info(
            device_info["node_id"], device_info["name"]
        )

        # Extra attributes - use normalized device class for consistency
        self._attr_extra_state_attributes = {
            "device_class": device_class_normalized,  # Use normalized value
            "debounce_time": None,
            "report_interval": None,
        }

        _LOGGER.debug(
            "Initialized binary sensor: %s (ID: %s, Type: %s)",
            sensor_name,
            self._attr_unique_id,
            self._attr_device_class,
        )

    async def async_added_to_hass(self) -> None:
        """Register update listener when entity is added to Home Assistant."""
        await super().async_added_to_hass()

        # Subscribe to state updates
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_binary_sensor_update",
                self._handle_state_update,
            )
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes - dynamically generated to always show current values."""
        # Always return current state and all configuration parameters
        attrs = {
            "state": "ON" if self._attr_is_on else "OFF",  # Current state as text
            "state_bool": self._attr_is_on,  # Boolean value
            "device_class": self._attr_extra_state_attributes.get("device_class", "generic"),
        }
        
        # Add configuration parameters if available
        if self._attr_extra_state_attributes.get("debounce_time") is not None:
            attrs["debounce_time"] = self._attr_extra_state_attributes["debounce_time"]
        
        if self._attr_extra_state_attributes.get("report_interval") is not None:
            attrs["report_interval"] = self._attr_extra_state_attributes["report_interval"]
        
        return attrs

    @callback
    async def _handle_state_update(self, event) -> None:
        """Handle state update events."""
        try:
            state_data = event.data if hasattr(event, "data") else event

            # Normalize node_id from event for comparison
            event_node_id = str(state_data.get("node_id", "")).replace(":", "").lower()

            # Extract node_id from device_info using utility function
            device_node_id = extract_node_id_from_device_info(self._attr_device_info)

            # Check if this update is for this device
            if event_node_id != device_node_id:
                return

            # Note: We don't filter by sensor_name because there's only one binary_sensor per device
            # The binary_sensor entity displays different states (door, motion, etc.) via device_class

            # Update state from sensor_value
            new_state = state_data.get("sensor_value")
            if new_state is not None:
                new_state = bool(new_state)
                if new_state != self._attr_is_on:
                    self._attr_is_on = new_state
                    # State will be shown via extra_state_attributes property
                    self.async_write_ha_state()
                    _LOGGER.debug(
                        "Updated binary sensor %s: %s",
                        self._attr_name,
                        "ON" if new_state else "OFF",
                    )

            # Update device class if provided
            params = state_data.get("params", {})
            device_class = params.get("device_class")
            if device_class:
                if isinstance(device_class, bytes):
                    device_class = device_class.decode("utf-8")

                # Use utility function for device class lookup
                device_class_normalized = get_binary_sensor_device_class(
                    str(device_class), DEFAULT_BINARY_SENSOR_DEVICE_CLASS
                )
                # Convert string to enum
                new_device_class = getattr(
                    BinarySensorDeviceClass, device_class_normalized.upper()
                )

                # Only update if device class actually changed
                if new_device_class != self._attr_device_class:
                    self._attr_device_class = new_device_class
                    # Also update extra_state_attributes to show the device class
                    self._attr_extra_state_attributes["device_class"] = device_class_normalized
                    self.async_write_ha_state()
                    _LOGGER.debug(
                        "Updated device class for %s: %s",
                        self._attr_name,
                        device_class_normalized,
                    )
                # Even if device class didn't change, ensure it's in attributes
                elif "device_class" not in self._attr_extra_state_attributes:
                    self._attr_extra_state_attributes["device_class"] = device_class_normalized

            # Update configuration parameters if provided
            config_updated = False
            debounce_time = params.get("debounce_time")
            if debounce_time is not None:
                self._attr_extra_state_attributes["debounce_time"] = f"{debounce_time}ms"
                config_updated = True
                _LOGGER.debug(
                    "Updated debounce_time for %s: %dms",
                    self._attr_name,
                    debounce_time,
                )

            report_interval = params.get("report_interval")
            if report_interval is not None:
                self._attr_extra_state_attributes["report_interval"] = f"{report_interval}ms"
                config_updated = True
                _LOGGER.debug(
                    "Updated report_interval for %s: %dms",
                    self._attr_name,
                    report_interval,
                )

            # Write state if config was updated
            if config_updated:
                self.async_write_ha_state()

        except Exception:
            _LOGGER.exception("Failed to process binary sensor update")
