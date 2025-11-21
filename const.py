"""Constants for the esp_ha integration.

This module provides constants, helper functions, and classes for the ESP HA
integration, including device discovery, configuration management, and event handling.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.helpers.device_registry import DeviceInfo

_LOGGER = logging.getLogger(__name__)

# Domain
DOMAIN: Final = "esp_ha"

# Configuration keys
CONF_NODE_ID: Final = "node_id"
CONF_CUSTOM_POP: Final = "custom_pop"
CONF_SECURITY_MODE: Final = "security_mode"

# Network defaults
DEFAULT_PORT: Final = 8080

# Device information
DEVICE_MANUFACTURER: Final = "Espressif"
DEVICE_MODEL: Final = "ESPHome Device"
DEVICE_HW_VERSION: Final = "1.0"
DEVICE_SW_VERSION: Final = "1.0.0"


def get_device_info(
    node_id: str,
    device_name: str | None = None,
    ip_address: str | None = None,
    hw_version: str = DEVICE_HW_VERSION,
    sw_version: str = DEVICE_SW_VERSION,
    device_type: str | None = None,
) -> DeviceInfo:
    """Get standardized device info for registry.

    Creates a DeviceInfo object with standardized formatting for use
    in the Home Assistant device registry.

    Args:
        node_id: Unique identifier for the device. Required.
        device_name: Optional device name. If not provided, generates
            name as "ESP-{node_id}".
        ip_address: Optional IP address. If provided, adds configuration
            URL to device info.
        hw_version: Hardware version string. Defaults to DEVICE_HW_VERSION.
        sw_version: Software version string. Defaults to DEVICE_SW_VERSION.
        device_type: Optional device type. If provided, appends to model name.

    Returns:
        DeviceInfo object with standardized device information.

    Raises:
        ValueError: If node_id is empty or None.

    Example:
        >>> info = get_device_info("abc123", device_name="Living Room Light")
        >>> info["name"]
        'Living Room Light'
        >>> info["identifiers"]
        {('esp_ha', 'abc123')}
    """
    # Validate node_id
    if not node_id:
        raise ValueError("node_id is required and cannot be empty")

    # Clean and normalize node_id
    node_id_clean = str(node_id).replace(":", "").lower()

    # Create device name if not provided - use complete node_id
    name = device_name or f"ESP-{node_id_clean}"

    # Build device info with required fields
    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, node_id_clean)},
        "name": name,
        "manufacturer": DEVICE_MANUFACTURER,
        "model": (f"{DEVICE_MODEL} ({device_type})" if device_type else DEVICE_MODEL),
        "hw_version": hw_version,
        "sw_version": sw_version,
    }

    # Add optional configuration URL
    if ip_address:
        info["configuration_url"] = f"http://{ip_address}"

    return DeviceInfo(**info)
