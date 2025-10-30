"""ESP HA Low Power & Sleep platform."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, get_device_info
from .esp_iot import (
    get_sleep_icon,
    get_sleep_state_text,
    get_wake_reason_text,
    get_wake_window_text,
)

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

        # Initialize entity state with instance variables
        self._attr_native_value = "awake"
        self._sleep_state = "awake"
        self._wake_reason = "unknown"
        self._wake_window_status = "available"
        self._last_wake_time = time.time()
        self._sleep_duration = 0
        self._wake_count = 0
        self._last_update = time.time()

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

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return get_sleep_icon(self._sleep_state)

    @callback
    async def _handle_low_power_sleep_update(self, event) -> None:
        """Handle low power & sleep update event."""
        try:
            event_data = event.data
            event_node_id = str(event_data["node_id"]).replace(":", "").lower()
            if event_node_id != self._node_id:
                return  # Not for this device

            sleep_data = event_data["sleep_data"]

            # ESP32 sends parameters one by one, so we need to update only the fields that are present
            # 因为 ESP32 逐个发送参数，所以我们只更新存在的字段，保留其他字段的当前值
            
            # Track if state changed
            state_changed = False
            old_sleep_state = self._sleep_state
            old_wake_count = self._wake_count

            # Update sleep state
            if "sleep_state" in sleep_data and sleep_data["sleep_state"] is not None:
                new_sleep_state = sleep_data["sleep_state"]
                if new_sleep_state != self._sleep_state:
                    self._sleep_state = new_sleep_state
                    self._attr_native_value = new_sleep_state
                    state_changed = True
                    _LOGGER.info("Updated sleep state: %s -> %s", old_sleep_state, new_sleep_state)

            # Update wake reason
            if "wake_reason" in sleep_data and sleep_data["wake_reason"] is not None:
                self._wake_reason = sleep_data["wake_reason"]
                state_changed = True
                _LOGGER.info("Updated wake reason: %s", self._wake_reason)

            # Update wake window status
            if "wake_window_status" in sleep_data and sleep_data["wake_window_status"] is not None:
                self._wake_window_status = sleep_data["wake_window_status"]
                state_changed = True
                _LOGGER.info("Updated wake window status: %s", self._wake_window_status)

            # Update sleep duration
            if "sleep_duration" in sleep_data and sleep_data["sleep_duration"] is not None:
                self._sleep_duration = int(sleep_data["sleep_duration"])
                state_changed = True
                _LOGGER.debug("Updated sleep duration: %d seconds", self._sleep_duration)

            # Update wake count
            if "wake_count" in sleep_data and sleep_data["wake_count"] is not None:
                new_wake_count = int(sleep_data["wake_count"])
                if new_wake_count != self._wake_count:
                    # Wake count increased - update last wake time
                    if new_wake_count > self._wake_count:
                        self._last_wake_time = time.time()
                        _LOGGER.info("Wake count increased: %d -> %d", old_wake_count, new_wake_count)
                    self._wake_count = new_wake_count
                    state_changed = True
            # Calculate wake count locally on state transition (awake)
            elif old_sleep_state != "awake" and self._sleep_state == "awake":
                self._wake_count += 1
                self._last_wake_time = time.time()
                state_changed = True
                _LOGGER.info("State transition to awake, wake count: %d", self._wake_count)

            # Update timestamp
            if state_changed:
                self._last_update = time.time()
                self.async_write_ha_state()
                _LOGGER.debug(
                    "Low power/sleep state updated: %s (wake_count=%d)",
                    self._sleep_state,
                    self._wake_count,
                )

        except Exception:
            _LOGGER.exception("Failed to process low power/sleep update")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        # Show ESP32 parameters with user-friendly text versions
        return {
            # Core ESP32 data
            "sleep_state": self._sleep_state,
            "wake_reason": self._wake_reason,
            "wake_window_status": self._wake_window_status,
            "sleep_duration": self._sleep_duration,
            "wake_count": self._wake_count,
            # User-friendly text representations
            "sleep_state_text": get_sleep_state_text(self._sleep_state),
            "wake_reason_text": get_wake_reason_text(self._wake_reason),
            "wake_window_status_text": get_wake_window_text(self._wake_window_status),
        }
