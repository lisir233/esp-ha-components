"""ESP HA IMU Gesture platform."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant

from .const import DOMAIN, get_device_info
from .esp_iot import (
    check_gesture_pictures_available,
    get_gesture_display_name,
    get_gesture_icon,
)

_LOGGER = logging.getLogger(__name__)


class ESPHomeIMUGesture(SensorEntity):
    """IMU gesture recognition sensor entity."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        api: Any,
        device_info: dict[str, Any],
        device_name: str,
        node_id: str,
    ) -> None:
        """Initialize the IMU gesture sensor."""
        self._hass = hass
        self._node_id = str(node_id).replace(":", "").lower()
        self._device_name = device_name

        # Entity attributes
        self._attr_name = "IMU Gesture"
        self._attr_unique_id = f"{DOMAIN}_{self._node_id}_imu_gesture"
        self._attr_device_info = get_device_info(node_id, device_name)

        # Core attributes
        self._gesture = "idle"
        self._confidence = 0
        # Event states (boolean flags for different gesture events)
        self._push_event = False
        self._shake_event = False
        self._circle_event = False
        self._flip_event = False
        self._toss_event = False
        self._rotation_event = False

        # Internal timer duration
        self._gesture_display_duration = 2.0
        self._reset_timer = None

        # Picture support
        component_path = Path(__file__).parent
        self._use_pictures = check_gesture_pictures_available(component_path)
        self._attr_entity_picture = None

        _LOGGER.debug(
            "Initialized IMU gesture sensor: %s (Pictures: %s)",
            device_name,
            "Enabled" if self._use_pictures else "Disabled",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to gesture events when added to HA."""
        await super().async_added_to_hass()

        # Subscribe to gesture updates
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_imu_gesture_update",
                self._handle_gesture_update,
            )
        )

        # Subscribe to device availability changes
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_device_availability_changed",
                self._handle_device_availability_change,
            )
        )

    def _cancel_reset_timer(self) -> None:
        """Cancel the existing reset timer if any."""
        if self._reset_timer is not None and not self._reset_timer.done():
            self._reset_timer.cancel()
            self._reset_timer = None
            _LOGGER.debug("Cancelled gesture reset timer")

    async def _auto_reset_to_idle(self) -> None:
        """Automatically reset gesture to idle after gesture_display_duration."""
        try:
            await asyncio.sleep(self._gesture_display_duration)
            _LOGGER.info(
                "Auto-resetting gesture to idle after %.1fs (was: %s)",
                self._gesture_display_duration,
                self._gesture,
            )
            self._gesture = "idle"
            self._confidence = 0
            self._push_event = False
            self._shake_event = False
            self._circle_event = False
            self._flip_event = False
            self._toss_event = False
            self._rotation_event = False
            
            self.async_write_ha_state()
            _LOGGER.debug(
                "Reset all event flags to False after %.1fs",
                self._gesture_display_duration,
            )
        except asyncio.CancelledError:
            _LOGGER.debug("Gesture reset timer cancelled")
        except Exception as err:  # Broad exception acceptable for background task
            _LOGGER.error("Error in auto-reset timer: %s", err)

    async def _handle_device_availability_change(self, event) -> None:
        """Handle device availability change (offline/online) - update entity state."""
        try:
            event_node_id = str(event.data.get("node_id", "")).replace(":", "").lower()

            if event_node_id == self._node_id:
                available = event.data.get("available", False)
                _LOGGER.debug(
                    "Device %s availability changed to %s, updating IMU gesture entity %s",
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

    async def _handle_gesture_update(self, event) -> None:
        """Handle gesture update events."""
        event_data = event.data

        # Filter by node_id (normalize for comparison)
        event_node_id = str(event_data.get("node_id", "")).replace(":", "").lower()
        
        _LOGGER.info(
            "IMU Gesture received update event: node_id=%s (match=%s), data=%s",
            event_node_id,
            event_node_id == self._node_id,
            event_data,
        )
        
        if event_node_id != self._node_id:
            _LOGGER.debug(
                "Skipping gesture update: node_id mismatch (expected=%s, got=%s)",
                self._node_id,
                event_node_id,
            )
            return

        try:
            # Extract sensor_data from event
            sensor_data = event_data.get("sensor_data", {})
            
            _LOGGER.info(
                "Processing gesture data: confidence=%s, gesture_type=%s, all_keys=%s",
                sensor_data.get("gesture_confidence"),
                sensor_data.get("gesture_type"),
                list(sensor_data.keys()),
            )

            # ESP32 sends parameters one by one, so we need to update only the fields that are present
            # 因为 ESP32 逐个发送参数，所以我们只更新存在的字段，保留其他字段的当前值
            
            # Check for gesture_display_duration parameter from ESP32
            # 检查 ESP32 的手势显示持续时间参数
            if "gesture_display_duration" in sensor_data and sensor_data["gesture_display_duration"] is not None:
                try:
                    self._gesture_display_duration = float(sensor_data["gesture_display_duration"])
                    _LOGGER.info(
                        "Updated gesture display duration: %.1fs",
                        self._gesture_display_duration,
                    )
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Invalid gesture_display_duration value: %s",
                        sensor_data["gesture_display_duration"],
                    )

            # Update gesture type
            if "gesture_type" in sensor_data and sensor_data["gesture_type"] is not None:
                gesture_type = sensor_data["gesture_type"]
                # Treat "none" as "idle" since they both represent no active gesture
                if gesture_type == "none":
                    gesture_type = "idle"
                if gesture_type in ["idle", "shake", "push", "circle", "flip", "toss", "rotation"]:
                    old_gesture = self._gesture
                    self._gesture = gesture_type
                    _LOGGER.info("Updated gesture type: %s -> %s", old_gesture, self._gesture)
                    
                    # Start auto-reset timer for non-idle gestures
                    # 为非 idle 手势启动自动重置定时器
                    if self._gesture != "idle":
                        self._cancel_reset_timer()  # Cancel any existing timer
                        self._reset_timer = asyncio.create_task(self._auto_reset_to_idle())
                        _LOGGER.info(
                            "Started auto-reset timer for gesture '%s' (%.1fs)",
                            self._gesture,
                            self._gesture_display_duration,
                        )
                    else:
                        # If gesture is idle, cancel any pending reset timer
                        self._cancel_reset_timer()
            
            # Update confidence
            if "gesture_confidence" in sensor_data and sensor_data["gesture_confidence"] is not None:
                old_confidence = self._confidence
                self._confidence = int(sensor_data["gesture_confidence"])
                _LOGGER.info("Updated confidence: %d -> %d", old_confidence, self._confidence)
            
            # Update orientation (individual axes) - Extended attributes
            if "x_orientation" in sensor_data and sensor_data["x_orientation"] is not None:
                self._orientation_x = float(sensor_data["x_orientation"])
                _LOGGER.debug("Updated X orientation: %.2f", self._orientation_x)

            if "y_orientation" in sensor_data and sensor_data["y_orientation"] is not None:
                self._orientation_y = float(sensor_data["y_orientation"])
                _LOGGER.debug("Updated Y orientation: %.2f", self._orientation_y)

            if "z_orientation" in sensor_data and sensor_data["z_orientation"] is not None:
                self._orientation_z = float(sensor_data["z_orientation"])
                _LOGGER.debug("Updated Z orientation: %.2f", self._orientation_z)

            # Update orientation change - Extended attribute
            if "orientation_change" in sensor_data and sensor_data["orientation_change"] is not None:
                self._orientation_change = sensor_data["orientation_change"]
                _LOGGER.info("Updated orientation change: %s", self._orientation_change)

            # Update power state - Extended attribute
            if "power" in sensor_data and sensor_data["power"] is not None:
                self._power = bool(sensor_data["power"])
                _LOGGER.info("Updated power: %s", self._power)
            
            # Update event flags
            # When an event is triggered, also start the auto-reset timer
            # IMPORTANT: Only set flags to True when ESP32 reports True
            # Ignore False values from ESP32 - let the auto-reset timer handle resetting to False
            # 重要：只在 ESP32 上报 True 时设置标志位为 True
            # 忽略 ESP32 发来的 False 值 - 让自动重置定时器负责将标志位重置为 False
            gesture_triggered = False
            
            if "shake_event" in sensor_data and sensor_data["shake_event"] is not None:
                event_value = bool(sensor_data["shake_event"])
                if event_value:  # Only process True events
                    self._shake_event = True
                    self._gesture = "shake"
                    gesture_triggered = True
                    _LOGGER.info("Shake event triggered")
            
            if "push_event" in sensor_data and sensor_data["push_event"] is not None:
                event_value = bool(sensor_data["push_event"])
                if event_value:  # Only process True events
                    self._push_event = True
                    self._gesture = "push"
                    gesture_triggered = True
                    _LOGGER.info("Push event triggered")
            
            if "circle_event" in sensor_data and sensor_data["circle_event"] is not None:
                event_value = bool(sensor_data["circle_event"])
                if event_value:  # Only process True events
                    self._circle_event = True
                    self._gesture = "circle"
                    gesture_triggered = True
                    _LOGGER.info("Circle event triggered")
            
            if "flip_event" in sensor_data and sensor_data["flip_event"] is not None:
                event_value = bool(sensor_data["flip_event"])
                if event_value:  # Only process True events
                    self._flip_event = True
                    self._gesture = "flip"
                    gesture_triggered = True
                    _LOGGER.info("Flip event triggered")
            
            if "toss_event" in sensor_data and sensor_data["toss_event"] is not None:
                event_value = bool(sensor_data["toss_event"])
                if event_value:  # Only process True events
                    self._toss_event = True
                    self._gesture = "toss"
                    gesture_triggered = True
                    _LOGGER.info("Toss event triggered")
            
            if "rotation_event" in sensor_data and sensor_data["rotation_event"] is not None:
                event_value = bool(sensor_data["rotation_event"])
                if event_value:  # Only process True events
                    self._rotation_event = True
                    self._gesture = "rotation"
                    gesture_triggered = True
                    _LOGGER.info("Rotation event triggered")
            
            # Start auto-reset timer if a gesture event was triggered
            if gesture_triggered:
                self._cancel_reset_timer()
                self._reset_timer = asyncio.create_task(self._auto_reset_to_idle())
                _LOGGER.info(
                    "Started auto-reset timer for event-triggered gesture '%s' (%.1fs)",
                    self._gesture,
                    self._gesture_display_duration,
                )
            
            # Update sensitivity - Extended attribute
            if "sensitivity" in sensor_data and sensor_data["sensitivity"] is not None:
                self._sensitivity = int(sensor_data["sensitivity"])
                _LOGGER.info("Updated sensitivity: %d", self._sensitivity)

            # Don't update entity_picture - keep small icon for consistency with other entities
            # Picture URL is available via extra_state_attributes for dashboard use
            # 不更新entity_picture - 保持小图标以与其他实体一致
            # 图片URL通过extra_state_attributes提供给Dashboard使用

            self.async_write_ha_state()

        except Exception:  # Broad exception acceptable for callback event handling
            _LOGGER.exception("Failed to process gesture update")

    @property
    def native_value(self) -> str:
        """Return current gesture state."""
        power = getattr(self, "_power", True)  # Default to True if not set
        return get_gesture_display_name(self._gesture, power)

    @property
    def available(self) -> bool:
        """Return True if entity is available (device is connected)."""
        api = self._hass.data.get(DOMAIN, {}).get("shared_api")
        if api:
            return api.is_device_available(self._node_id)
        return False

    @property
    def icon(self) -> str:
        """Return icon based on current gesture."""
        power = getattr(self, "_power", True)  # Default to True if not set
        return get_gesture_icon(self._gesture, power)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "gesture": self._gesture,
            "confidence": self._confidence,
            "push_event": self._push_event,
            "shake_event": self._shake_event,
            "circle_event": self._circle_event,
            "flip_event": self._flip_event,
            "toss_event": self._toss_event,
            "rotation_event": self._rotation_event,
        }

        if hasattr(self, "_orientation_x"):
            attrs["orientation_x"] = self._orientation_x

        if hasattr(self, "_orientation_y"):
            attrs["orientation_y"] = self._orientation_y

        if hasattr(self, "_orientation_z"):
            attrs["orientation_z"] = self._orientation_z

        if hasattr(self, "_orientation_change"):
            attrs["orientation_change"] = self._orientation_change

        if hasattr(self, "_sensitivity"):
            attrs["sensitivity"] = self._sensitivity

        if hasattr(self, "_gesture_display_duration") and self._gesture_display_duration != 2.0:
            attrs["gesture_display_duration"] = self._gesture_display_duration

        if hasattr(self, "_power"):
            attrs["power"] = self._power

        return attrs
