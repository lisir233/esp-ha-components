"""Device discovery configuration helpers for ESP-RainMaker devices.

This module provides utilities for creating basic device configurations
from raw device properties when no explicit configuration exists.
"""

from __future__ import annotations

import logging

from .esp_iot_spec import (
    add_platform_configs,
    analyze_params_data,
    is_binary_sensor_property,
    is_light_property,
    is_sensor_property,
)

_LOGGER = logging.getLogger(__name__)


def create_basic_device_config_from_properties(properties: list) -> dict:
    """Create basic device config from available properties when no explicit config exists.

    Args:
        properties: List of device properties

    Returns:
        dict: Basic device configuration for entity discovery
    """
    try:
        device_config = initialize_device_config()
        capabilities = analyze_device_capabilities(properties)
        add_platform_configs(device_config, capabilities)
    except Exception:
        _LOGGER.exception("Failed to create basic device config")
        return {
            "device_info": {"name": "ESP Device", "manufacturer": "Espressif"},
            "platforms": {},
        }
    else:
        return device_config


def initialize_device_config() -> dict:
    """Initialize empty device configuration structure.

    Returns:
        dict: Empty device configuration with basic info.
    """
    return {
        "device_info": {
            "name": "ESP Device",
            "manufacturer": "Espressif",
            "model": "ESP32",
        },
        "platforms": {},
    }


def analyze_device_capabilities(properties: list) -> dict:
    """Analyze properties to determine device capabilities.

    Args:
        properties: List of device properties.

    Returns:
        dict: Detected capabilities (has_light, has_sensor, has_binary_sensor).
    """
    capabilities = {
        "has_light": False,
        "has_sensor": False,
        "has_binary_sensor": False,
    }

    for prop in properties:
        prop_name = prop.get("name", "").lower()
        prop_value = prop.get("value")

        # Check for light-related properties
        if is_light_property(prop_name):
            capabilities["has_light"] = True

        # Check for sensor properties
        if is_sensor_property(prop_name):
            capabilities["has_sensor"] = True

        # Check for binary sensor properties
        if is_binary_sensor_property(prop_name):
            capabilities["has_binary_sensor"] = True

        # Check params property for nested data
        if prop_name == "params":
            analyze_params_data(prop_value, capabilities)

    return capabilities
