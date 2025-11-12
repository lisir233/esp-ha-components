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

    # Track discovered binary sensors to prevent duplicates
    discovered_key = f"discovered_binary_sensors_{node_id}"
    if discovered_key not in hass.data[DOMAIN]:
        hass.data[DOMAIN][discovered_key] = set()

    # Store reference for discovery event handler
    entry_key = f"{node_id}_{config_entry.entry_id}"
    hass.data[DOMAIN][f"add_binary_entities_{entry_key}"] = async_add_entities

    # Event handler for binary sensor discovery
    async def handle_binary_sensor_discovered(event) -> None:
        """Handle binary sensor discovery events."""
        try:
            event_node_id = str(event.data.get("node_id", "")).replace(":", "").lower()

            # Only process events for this device
            if event_node_id != node_id.lower():
                return

            # Check if binary sensor already discovered to prevent duplicates
            discovered_key = f"discovered_binary_sensors_{node_id}"
            discovered_sensors = hass.data[DOMAIN].get(discovered_key, set())
            
            # Use unique key to track this binary sensor (one per device)
            sensor_key = f"{event_node_id}_binary_sensor"
            
            if sensor_key in discovered_sensors:
                _LOGGER.debug(
                    "Binary sensor already exists for device %s, skipping duplicate creation",
                    event_node_id,
                )
                return
            
            # Mark as discovered
            discovered_sensors.add(sensor_key)
            hass.data[DOMAIN][discovered_key] = discovered_sensors

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
            _LOGGER.debug("Added binary sensor: %s (ID: %s)", sensor_name, sensor_key)

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

        # Generate unique ID (one binary sensor per device)
        device_node_id = str(device_info.get("node_id", "")).replace(":", "").lower()
        self._attr_unique_id = f"{DOMAIN}_{device_node_id}_binary_sensor"

        # Entity attributes
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

        # Core attributes
        self._binary_sensor_type = device_class_normalized
        self._attr_extra_state_attributes = {}

        # Store node_id for availability check
        self._node_id = device_node_id

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

        # Subscribe to device availability changes (offline/online)
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_device_availability_changed",
                self._handle_device_availability_change,
            )
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available (device is connected)."""
        api = self.hass.data.get(DOMAIN, {}).get("shared_api")
        if api:
            return api.is_device_available(self._node_id)
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs = {
            "binary_sensor_type": self._binary_sensor_type,
        }
        if "debounce_time" in self._attr_extra_state_attributes:
            attrs["debounce_time"] = self._attr_extra_state_attributes["debounce_time"]

        if "report_interval" in self._attr_extra_state_attributes:
            attrs["report_interval"] = self._attr_extra_state_attributes["report_interval"]

        return attrs

    async def _handle_device_availability_change(self, event) -> None:
        """Handle device availability change - update entity state."""
        try:
            # Normalize node_id from event
            event_node_id = str(event.data.get("node_id", "")).replace(":", "").lower()

            # Extract node_id from device_info
            device_node_id = extract_node_id_from_device_info(self._attr_device_info)

            # Only update if this event is for our device
            if event_node_id == device_node_id:
                available = event.data.get("available", False)
                _LOGGER.debug(
                    "Device %s availability changed to %s, updating binary sensor %s",
                    device_node_id,
                    "available" if available else "unavailable",
                    self._attr_unique_id,
                )
                # Trigger state update - the available property will return current state
                self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error(
                "Error handling device availability change for %s: %s",
                self._attr_unique_id,
                err,
            )

    @callback
    def _handle_state_update(self, event) -> None:
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

            # Track what changed for debug logging
            state_changed = False
            device_class_updated = False
            config_updated = False
            
            # Update state from sensor_value
            new_state = state_data.get("sensor_value")
            if new_state is not None:
                old_state = self._attr_is_on
                new_state = bool(new_state)
                self._attr_is_on = new_state
                state_changed = (old_state != new_state)
                if state_changed:
                    _LOGGER.debug(
                        "Updated binary sensor %s: %s -> %s",
                        self._attr_name,
                        "ON" if old_state else "OFF",
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

                # Update binary_sensor_type and device_class if changed
                if self._binary_sensor_type != device_class_normalized:
                    old_type = self._binary_sensor_type
                    self._binary_sensor_type = device_class_normalized

                    # Also update _attr_device_class to change the icon
                    new_device_class = getattr(
                        BinarySensorDeviceClass, device_class_normalized.upper()
                    )
                    self._attr_device_class = new_device_class

                    device_class_updated = True
                    _LOGGER.info(
                        "Binary sensor %s type changed: %s -> %s (icon will update to match)",
                        self._attr_name,
                        old_type,
                        device_class_normalized,
                    )

            # Update extended attributes only if ESP32 provides them
            # These are optional and detected dynamically from params
            debounce_time = params.get("debounce_time")
            if debounce_time is not None:
                new_debounce = f"{debounce_time}ms"
                current_debounce = self._attr_extra_state_attributes.get("debounce_time")
                if current_debounce != new_debounce:
                    self._attr_extra_state_attributes["debounce_time"] = new_debounce
                    config_updated = True
                    if current_debounce is None:
                        _LOGGER.info(
                            "Detected extended attribute debounce_time for %s: %s",
                            self._attr_name,
                            new_debounce,
                        )
                    else:
                        _LOGGER.debug(
                            "Updated debounce_time for %s: %s -> %s",
                            self._attr_name,
                            current_debounce,
                            new_debounce,
                        )

            report_interval = params.get("report_interval")
            if report_interval is not None:
                new_interval = f"{report_interval}ms"
                current_interval = self._attr_extra_state_attributes.get("report_interval")
                if current_interval != new_interval:
                    self._attr_extra_state_attributes["report_interval"] = new_interval
                    config_updated = True
                    if current_interval is None:
                        _LOGGER.info(
                            "Detected extended attribute report_interval for %s: %s",
                            self._attr_name,
                            new_interval,
                        )
                    else:
                        _LOGGER.debug(
                            "Updated report_interval for %s: %s -> %s",
                            self._attr_name,
                            current_interval,
                            new_interval,
                        )

            # Only update HA state if something actually changed
            # This prevents unnecessary UI refreshes that cause page jumping
            if state_changed or device_class_updated or config_updated:
                self.async_write_ha_state()
                _LOGGER.debug(
                    "Binary sensor %s state written to HA: state_changed=%s, device_class_updated=%s, config_updated=%s",
                    self._attr_name,
                    state_changed,
                    device_class_updated,
                    config_updated,
                )
            else:
                _LOGGER.debug(
                    "Binary sensor %s: no changes detected, skipping state update to prevent UI refresh",
                    self._attr_name,
                )

        except Exception:
            _LOGGER.exception("Failed to process binary sensor update")
