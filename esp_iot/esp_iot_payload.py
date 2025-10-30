"""ESP-RainMaker payload formatting utilities.

This module provides functions for creating properly formatted ESP-RainMaker
device payloads for various device types including lights, sensors, battery
devices, and more. Also includes ESPDeviceController for high-level device control.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .esp_iot_spec import (
    BATTERY_PARAM_KEYWORDS,
    LIGHT_PARAM_KEYWORDS,
    LIGHT_PARAM_MAP,
    SENSOR_PARAM_KEYWORDS,
)

_LOGGER = logging.getLogger(__name__)


class ESPDeviceController:
    """High-level ESP device controller that formats device-specific payloads.

    This controller provides a simplified interface for controlling ESP devices
    by automatically formatting payloads according to ESP-RainMaker conventions
    for different device types.
    """

    def __init__(self, client: Any) -> None:
        """Initialize with an ESP Local Control client.

        Args:
            client: ESPLocalCtrlClient instance for low-level protocol communication.
        """
        self.client = client

    async def set_device_property(self, param_name: str, param_value: Any) -> bool:
        """Set a device property with ESP-RainMaker payload formatting.

        Automatically detects the device type based on the parameter name
        and formats the payload accordingly.

        Args:
            param_name: Parameter name (e.g., "Power", "Brightness").
            param_value: Parameter value (type depends on parameter).

        Returns:
            True if property was set successfully, False otherwise.
        """
        try:
            # Create ESP-RainMaker JSON payload based on parameter type
            json_payload = create_device_payload(param_name, param_value)

            if not json_payload:
                _LOGGER.error("Failed to create payload for parameter: %s", param_name)
                return False

            payload_str = json.dumps(json_payload)
            _LOGGER.debug("Setting device property JSON payload: %s", payload_str)

            # Use params property (index 1) for ESP-RainMaker devices
            success = await self.client.set_property_values([1], [payload_str])

        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to set device property %s: %s", param_name, err)
            return False
        else:
            if success:
                _LOGGER.debug(
                    "Property set successfully: %s=%s", param_name, param_value
                )
                return True

            _LOGGER.warning(
                "Failed to set property: %s=%s", param_name, param_value
            )
            return False


def create_light_payload(param_name: str, param_value: Any) -> dict[str, Any]:
    """Create Light device payload formatted for ESP-RainMaker.

    Args:
        param_name: Light parameter name (e.g., "power", "brightness").
        param_value: Light parameter value.

    Returns:
        Formatted payload: {"Light": {"Power": true}} etc.
    """
    param_key = LIGHT_PARAM_MAP.get(param_name.lower(), param_name)
    return {"Light": {param_key: param_value}}


def create_sensor_payload(param_name: str, param_value: Any) -> dict[str, Any]:
    """Create Temperature Sensor device payload.

    Args:
        param_name: Sensor parameter name.
        param_value: Sensor parameter value.

    Returns:
        Formatted payload: {"Temperature Sensor": {"Threshold": value}} etc.
    """
    return {"Temperature Sensor": {param_name: param_value}}


def create_battery_payload(param_name: str, param_value: Any) -> dict[str, Any]:
    """Create Battery & Energy device payload.

    Args:
        param_name: Battery parameter name.
        param_value: Battery parameter value.

    Returns:
        Formatted payload: {"Battery & Energy": {"Battery Level": value}} etc.
    """
    return {"Battery & Energy": {param_name: param_value}}


def create_device_payload(param_name: str, param_value: Any) -> dict[str, Any]:
    """Create ESP-RainMaker device payload based on parameter name.

    Automatically detects the device type based on the parameter name
    and routes to the appropriate payload creator. Falls back to light
    device format for unknown parameters.

    Args:
        param_name: Parameter name to determine device type.
        param_value: Parameter value to include in payload.

    Returns:
        ESP-RainMaker JSON payload dictionary formatted for the detected device type.
    """
    param_name_lower = str(param_name).lower()

    # Light device parameters
    if param_name_lower in LIGHT_PARAM_KEYWORDS:
        return create_light_payload(param_name, param_value)

    # Temperature Sensor parameters
    if any(keyword in param_name_lower for keyword in SENSOR_PARAM_KEYWORDS):
        return create_sensor_payload(param_name, param_value)

    # Battery & Energy device parameters
    if any(keyword in param_name_lower for keyword in BATTERY_PARAM_KEYWORDS):
        return create_battery_payload(param_name, param_value)

    # Default: Light device (for backward compatibility)
    _LOGGER.warning(
        "Unknown parameter type, defaulting to Light device: %s", param_name
    )
    return create_light_payload(param_name, param_value)
