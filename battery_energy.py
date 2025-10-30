"""ESP HA Battery & Energy platform."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, get_device_info
from .esp_iot.esp_iot_spec import BATTERY_ALERT_LEVELS, BATTERY_STATES
from .esp_iot.esp_iot_utils import get_battery_icon, parse_battery_update

_LOGGER = logging.getLogger(__name__)


class ESPHomeBatteryEnergy(SensorEntity):
    """Representation of an ESP HA Battery entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        node_id: str,
        device_name: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the Battery entity."""
        self.hass = hass
        self._node_id = str(node_id).replace(":", "").lower()
        self._device_name = device_name

        # Entity attributes
        self._attr_has_entity_name = True
        self._attr_name = "Battery Energy"
        self._attr_unique_id = f"{DOMAIN}_{self._node_id}_battery_energy"
        self._attr_should_poll = False
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_info = get_device_info(self._node_id, device_name)
        self._attr_native_value = 0

        # State data
        self._battery_level = 0
        self._voltage = 0.0
        self._temperature = 0.0
        self._charging_status = "unknown"
        self._alert_level = "normal"
        self._last_alert_level = "normal"

        _LOGGER.debug(
            "Battery entity initialized: %s (node_id: %s)", device_name, self._node_id
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # Subscribe to battery update events
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_battery_energy_update",
                self._handle_battery_energy_update,
            )
        )

        _LOGGER.debug("Battery entity added to Home Assistant: %s", self._attr_name)

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return get_battery_icon(
            self._battery_level, self._charging_status, self._alert_level
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        # Only show ESP32 reported data
        return {
            "battery_level": self._battery_level,
            "voltage": f"{self._voltage:.2f}V",
            "temperature": f"{self._temperature:.1f}°C",
            "charging_status": BATTERY_STATES.get(self._charging_status, "Unknown"),
            "alert_level": BATTERY_ALERT_LEVELS.get(self._alert_level, "Unknown"),
        }

    @callback
    def _handle_battery_energy_update(self, event) -> None:
        """Handle battery energy update events."""
        try:
            event_node_id = str(event.data.get("node_id", "")).replace(":", "").lower()

            # Only process updates for this device
            if event_node_id != self._node_id:
                return

            battery_data = event.data.get("battery_data", {})

            # Parse battery data using utility function
            parsed_data = parse_battery_update(battery_data)

            # Update battery state
            if "battery_level" in parsed_data:
                self._battery_level = parsed_data["battery_level"]
                self._attr_native_value = self._battery_level

            if "voltage" in parsed_data:
                self._voltage = parsed_data["voltage"]

            if "temperature" in parsed_data:
                self._temperature = parsed_data["temperature"]

            if "charging_status" in parsed_data:
                self._charging_status = parsed_data["charging_status"]

            if "alert_level" in parsed_data:
                self._alert_level = parsed_data["alert_level"]
                self._trigger_alert_notification()

            self.async_write_ha_state()

            _LOGGER.debug(
                "Battery update: %s -> %d%% (voltage: %.2fV, temp: %.1f°C)",
                self._attr_name,
                self._battery_level,
                self._voltage,
                self._temperature,
            )

        except Exception:  # Broad exception acceptable for callback event handling
            _LOGGER.exception("Failed to process battery update")

    @callback
    def _trigger_alert_notification(self) -> None:
        """Trigger alert notification if level changed."""
        if self._alert_level == self._last_alert_level:
            return

        self._last_alert_level = self._alert_level

        if self._alert_level == "critical":
            title = f"{self._device_name} - Critical Battery"
            message = f"Battery critical: {self._battery_level}% - Charge immediately!"
            notification_id = f"esp_battery_critical_{self._node_id}"
        elif self._alert_level == "low":
            title = f"{self._device_name} - Low Battery"
            message = f"Battery low: {self._battery_level}% - Please charge soon."
            notification_id = f"esp_battery_low_{self._node_id}"
        else:
            # Clear notifications when battery returns to normal
            for nid in (
                f"esp_battery_critical_{self._node_id}",
                f"esp_battery_low_{self._node_id}",
            ):
                self.hass.async_create_task(
                    self.hass.services.async_call(
                        "persistent_notification", "dismiss", {"notification_id": nid}
                    )
                )
            return

        # Create notification
        self.hass.async_create_task(
            self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": message,
                    "title": title,
                    "notification_id": notification_id,
                },
            )
        )

        _LOGGER.warning("Battery alert - %s: %d%%", title, self._battery_level)
