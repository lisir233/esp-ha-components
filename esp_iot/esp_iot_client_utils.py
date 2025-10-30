"""ESP Local Control client utility functions.

This module provides utility functions for working with ESP Local Control clients,
including value conversion, response parsing, and property handling.
"""

from __future__ import annotations

import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def convert_values_to_esp_format(values: list[Any]) -> list[bytes]:
    """Convert Python values to ESP-IDF byte format.

    Converts various Python types to the byte format expected by ESP-IDF
    Local Control protocol:
    - Strings are encoded directly to bytes using latin-1
    - Other types (int, bool, dict, etc.) are first converted to JSON strings,
      then encoded to bytes

    Args:
        values: List of Python values to convert (strings, numbers, bools, dicts, etc.)

    Returns:
        List of byte strings suitable for ESP-IDF protocol transmission.

    Example:
        >>> convert_values_to_esp_format(["hello", 42, True, {"key": "value"}])
        [b'hello', b'42', b'true', b'{"key": "value"}']
    """
    esp_values: list[bytes] = []
    for val in values:
        if isinstance(val, str):
            # String values need to be encoded to bytes
            esp_values.append(val.encode("latin-1"))
        else:
            # Other types → JSON string → bytes
            json_str = json.dumps(val)
            esp_values.append(json_str.encode("latin-1"))
    return esp_values


def parse_property_count_response(parsed_data: Any) -> int:
    """Parse property count from ESP Local Control response.

    Handles both response formats:
    1. Standard format: {"count": 2}
    2. Fallback format: {"properties": [...]} - returns length of list

    Args:
        parsed_data: Parsed response data from ESP device (typically a dict)

    Returns:
        Number of properties found in the response, or 0 if invalid/empty.
    """
    if not isinstance(parsed_data, dict):
        _LOGGER.warning("Invalid property count response type: %s", type(parsed_data).__name__)
        return 0

    # Try standard 'count' field first
    if "count" in parsed_data:
        count = parsed_data["count"]
        if isinstance(count, int) and count > 0:
            return count
        if count == 0:
            _LOGGER.debug("Device reported 0 properties")
            return 0

    # Try 'properties' field as fallback
    if "properties" in parsed_data:
        properties = parsed_data["properties"]
        if isinstance(properties, list):
            count = len(properties)
            if count > 0:
                return count
            _LOGGER.warning("Device returned 0 properties in list")
            return 0

    _LOGGER.warning("Invalid property count response: %s", parsed_data)
    return 0


def parse_property_values_response(
    props_data: Any, log_details: bool = False
) -> list[dict[str, Any]]:
    """Parse property values from ESP Local Control response.

    Extracts the list of properties from the response dictionary.
    Expected format: {"properties": [{"name": "...", "value": ...}, ...]}

    Args:
        props_data: Parsed response data from ESP device
        log_details: Whether to log detailed property information (default: False)

    Returns:
        List of property dictionaries with 'name' and 'value' keys,
        or empty list if parsing fails.
    """
    if log_details:
        _LOGGER.debug(
            "parse_property_values_response: Received props_data type=%s",
            type(props_data).__name__,
        )

    if not isinstance(props_data, dict):
        _LOGGER.warning("Invalid property values response type: %s", type(props_data).__name__)
        return []

    if log_details:
        _LOGGER.debug("props_data keys=%s", list(props_data.keys()))
        for key, val in props_data.items():
            if isinstance(val, list):
                _LOGGER.debug("%s: list with %d items", key, len(val))
            else:
                _LOGGER.debug("%s: %s", key, val)

    # Extract properties from parsed data
    if "properties" in props_data:
        properties = props_data["properties"]
        if isinstance(properties, list):
            if properties and log_details:
                _LOGGER.debug("Extracted %d properties", len(properties))
                for prop in properties:
                    _LOGGER.debug(
                        "Property: name=%s, value=%s",
                        prop.get("name"),
                        prop.get("value"),
                    )
            return properties

    _LOGGER.warning("No properties in response")
    return []
