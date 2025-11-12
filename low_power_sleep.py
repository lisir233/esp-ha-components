"""ESP HA Low Power & Sleep platform."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant

from .const import DOMAIN, get_device_info
from .esp_iot import get_sleep_icon

_LOGGER = logging.getLogger(__name__)

class ESPHomeLowPowerSleep(SensorEntity):
    """Representation of an ESP HA Low Power & Sleep entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        node_id: str,
        device_name: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the Low Power & Sleep entity."""
        SensorEntity.__init__(self)
        self._hass = hass
        self._node_id = str(node_id).replace(":", "").lower()
        self._device_name = device_name
        self._device_info = device_info

        # Entity attributes
        self._attr_has_entity_name = True
        self._attr_name = "Low Power Sleep"
        self._attr_unique_id = f"{DOMAIN}_{self._node_id}_low_power_sleep"
        self._attr_should_poll = False
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_state_class = None
        self._attr_native_unit_of_measurement = None

        # Core attributes
        self._attr_native_value = "awake"
        self._sleep_state = "awake"
        self._wake_reason = "unknown"
        self._last_wake_time = time.time()

        # Device information
        self._attr_device_info = get_device_info(self._node_id, device_name)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # Subscribe to state updates using standard HA patterns
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_low_power_sleep_update", self._handle_low_power_sleep_update
            )
        )

        # Subscribe to device availability changes
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_device_availability_changed",
                self._handle_device_availability_change,
            )
        )

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return get_sleep_icon(self._sleep_state)

    @property
    def available(self) -> bool:
        """Return True if entity is available (device is connected)."""
        api = self._hass.data.get(DOMAIN, {}).get("shared_api")
        if api:
            return api.is_device_available(self._node_id)
        return False

    async def _handle_device_availability_change(self, event) -> None:
        """Handle device availability change (offline/online) - update entity state."""
        try:
            event_node_id = str(event.data.get("node_id", "")).replace(":", "").lower()

            if event_node_id == self._node_id:
                available = event.data.get("available", False)
                _LOGGER.debug(
                    "Device %s availability changed to %s, updating sleep entity %s",
                    self._device_info.get("name"),
                    "available" if available else "unavailable",
                    self._attr_unique_id if hasattr(self, '_attr_unique_id') else 'unknown',
                )
                # Trigger state update - the available property will return current state
                self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Error handling device availability change: %s", err)

    async def _handle_low_power_sleep_update(self, event) -> None:
        """Handle low power & sleep update event."""
        try:
            event_data = event.data
            event_node_id = str(event_data["node_id"]).replace(":", "").lower()
            
            _LOGGER.debug(
                "Received low_power_sleep_update event for node %s (my node: %s)",
                event_node_id,
                self._node_id,
            )
            
            if event_node_id != self._node_id:
                return  # Not for this device

            sleep_data = event_data["sleep_data"]

            old_sleep_state = self._sleep_state

            # Update sleep state
            if "sleep_state" in sleep_data and sleep_data["sleep_state"] is not None:
                new_sleep_state = sleep_data["sleep_state"]
                if new_sleep_state != self._sleep_state:
                    self._sleep_state = new_sleep_state
                    self._attr_native_value = new_sleep_state
                    _LOGGER.info("Updated sleep state: %s -> %s", old_sleep_state, new_sleep_state)

            # Update wake reason
            if "wake_reason" in sleep_data and sleep_data["wake_reason"] is not None:
                self._wake_reason = sleep_data["wake_reason"]
                _LOGGER.info("Updated wake reason: %s", self._wake_reason)

            # Update wake window status
            if "wake_window_status" in sleep_data and sleep_data["wake_window_status"] is not None:
                self._wake_window_status = sleep_data["wake_window_status"]
                _LOGGER.info("Updated wake window status: %s", self._wake_window_status)

            # Update sleep duration
            if "sleep_duration" in sleep_data and sleep_data["sleep_duration"] is not None:
                self._sleep_duration = int(sleep_data["sleep_duration"])

            # Update wake count
            if "wake_count" in sleep_data and sleep_data["wake_count"] is not None:
                old_wake_count = getattr(self, "_wake_count", 0)
                new_wake_count = int(sleep_data["wake_count"])
                if new_wake_count != old_wake_count:
                    if new_wake_count > old_wake_count:
                        self._last_wake_time = time.time()
                    self._wake_count = new_wake_count
            elif old_sleep_state != "awake" and self._sleep_state == "awake" and not hasattr(self, "_wake_count"):
                self._wake_count = 1
                self._last_wake_time = time.time()

            self.async_write_ha_state()

        except Exception:
            _LOGGER.exception("Failed to process low power/sleep update")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs = {
            "sleep_state": self._sleep_state,
            "wake_reason": self._wake_reason,
        }

        if hasattr(self, "_wake_window_status"):
            attrs["wake_window_status"] = self._wake_window_status

        if hasattr(self, "_sleep_duration"):
            attrs["sleep_duration"] = self._sleep_duration

        if hasattr(self, "_wake_count"):
            attrs["wake_count"] = self._wake_count

        return attrs
