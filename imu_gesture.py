"""ESP HA IMU Gesture platform."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback

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
        self._attr_has_entity_name = True
        self._attr_name = "IMU Gesture"
        self._attr_icon = "mdi:gesture-tap"
        self._attr_unique_id = f"{DOMAIN}_{self._node_id}_imu_gesture"
        self._attr_device_info = get_device_info(node_id, device_name)

        # State
        self._gesture = "idle"
        self._confidence = 0
        self._orientation = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._orientation_change = "normal"
        self._power = True

        # Event states (boolean flags for different gesture events)
        self._push_event = False
        self._shake_event = False
        self._circle_event = False
        self._flip_event = False
        self._toss_event = False
        self._rotation_event = False

        # Configuration
        self._sensitivity = 50

        # Picture support
        # 图片支持 - 检查图片文件夹是否存在
        component_path = Path(__file__).parent
        self._use_pictures = check_gesture_pictures_available(component_path)
        # Don't set entity_picture - only use small icons by default
        # Pictures are accessed via extra_state_attributes for dashboard use only
        # 不设置entity_picture - 默认只使用小图标
        # 图片通过extra_state_attributes访问，仅用于Dashboard
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

    @callback
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
            
            # Update gesture type
            if "gesture_type" in sensor_data and sensor_data["gesture_type"] is not None:
                gesture_type = sensor_data["gesture_type"]
                if gesture_type in ["idle", "shake", "push", "circle", "flip", "toss", "rotation"]:
                    self._gesture = gesture_type
                    _LOGGER.info("Updated gesture type: %s", self._gesture)
            
            # Update confidence
            if "gesture_confidence" in sensor_data and sensor_data["gesture_confidence"] is not None:
                old_confidence = self._confidence
                self._confidence = int(sensor_data["gesture_confidence"])
                _LOGGER.info("Updated confidence: %d -> %d", old_confidence, self._confidence)
            
            # Update orientation (individual axes)
            if "x_orientation" in sensor_data and sensor_data["x_orientation"] is not None:
                self._orientation["x"] = float(sensor_data["x_orientation"])
                _LOGGER.debug("Updated X orientation: %.2f", self._orientation["x"])
            
            if "y_orientation" in sensor_data and sensor_data["y_orientation"] is not None:
                self._orientation["y"] = float(sensor_data["y_orientation"])
                _LOGGER.debug("Updated Y orientation: %.2f", self._orientation["y"])
            
            if "z_orientation" in sensor_data and sensor_data["z_orientation"] is not None:
                self._orientation["z"] = float(sensor_data["z_orientation"])
                _LOGGER.debug("Updated Z orientation: %.2f", self._orientation["z"])
            
            # Update orientation change
            if "orientation_change" in sensor_data and sensor_data["orientation_change"] is not None:
                self._orientation_change = sensor_data["orientation_change"]
                _LOGGER.info("Updated orientation change: %s", self._orientation_change)
            
            # Update power state
            if "power" in sensor_data and sensor_data["power"] is not None:
                self._power = bool(sensor_data["power"])
                _LOGGER.info("Updated power: %s", self._power)
            
            # Update event flags
            if "shake_event" in sensor_data and sensor_data["shake_event"] is not None:
                self._shake_event = bool(sensor_data["shake_event"])
                if self._shake_event:
                    self._gesture = "shake"
                _LOGGER.info("Updated shake event: %s", self._shake_event)
            
            if "push_event" in sensor_data and sensor_data["push_event"] is not None:
                self._push_event = bool(sensor_data["push_event"])
                if self._push_event:
                    self._gesture = "push"
                _LOGGER.info("Updated push event: %s", self._push_event)
            
            if "circle_event" in sensor_data and sensor_data["circle_event"] is not None:
                self._circle_event = bool(sensor_data["circle_event"])
                if self._circle_event:
                    self._gesture = "circle"
                _LOGGER.info("Updated circle event: %s", self._circle_event)
            
            if "flip_event" in sensor_data and sensor_data["flip_event"] is not None:
                self._flip_event = bool(sensor_data["flip_event"])
                if self._flip_event:
                    self._gesture = "flip"
                _LOGGER.info("Updated flip event: %s", self._flip_event)
            
            if "toss_event" in sensor_data and sensor_data["toss_event"] is not None:
                self._toss_event = bool(sensor_data["toss_event"])
                if self._toss_event:
                    self._gesture = "toss"
                _LOGGER.info("Updated toss event: %s", self._toss_event)
            
            if "rotation_event" in sensor_data and sensor_data["rotation_event"] is not None:
                self._rotation_event = bool(sensor_data["rotation_event"])
                if self._rotation_event:
                    self._gesture = "rotation"
                _LOGGER.info("Updated rotation event: %s", self._rotation_event)
            
            # Update sensitivity
            if "sensitivity" in sensor_data and sensor_data["sensitivity"] is not None:
                self._sensitivity = int(sensor_data["sensitivity"])
                _LOGGER.info("Updated sensitivity: %d", self._sensitivity)

            # Don't update entity_picture - keep small icon for consistency with other entities
            # Picture URL is available via extra_state_attributes for dashboard use
            # 不更新entity_picture - 保持小图标以与其他实体一致
            # 图片URL通过extra_state_attributes提供给Dashboard使用

            self.async_write_ha_state()
            _LOGGER.info(
                "IMU Gesture state updated: gesture=%s, confidence=%d%%",
                self._gesture,
                self._confidence,
            )

        except Exception:  # Broad exception acceptable for callback event handling
            _LOGGER.exception("Failed to process gesture update")

    @property
    def native_value(self) -> str:
        """Return current gesture state."""
        return get_gesture_display_name(self._gesture, self._power)

    @property
    def icon(self) -> str:
        """Return icon based on current gesture."""
        return get_gesture_icon(self._gesture, self._power)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        # Show ESP32 parameters only
        return {
            # Core gesture data from ESP32
            "gesture": self._gesture,
            "confidence": self._confidence,
            "orientation_x": self._orientation["x"],
            "orientation_y": self._orientation["y"],
            "orientation_z": self._orientation["z"],
            "orientation_change": self._orientation_change,
            # Event flags from ESP32
            "push_event": self._push_event,
            "shake_event": self._shake_event,
            "circle_event": self._circle_event,
            "flip_event": self._flip_event,
            "toss_event": self._toss_event,
            "rotation_event": self._rotation_event,
            # Configuration from ESP32
            "sensitivity": self._sensitivity,
        }
