"""Utility functions for ESP device management."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import socket
import time
from typing import Any

from homeassistant.components.number import NumberDeviceClass
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant

from .esp_iot_spec import (
    BINARY_SENSOR_DEVICE_CLASS_MAP,
    DEFAULT_ORIENTATION,
    DEFAULT_SLEEP_STATE,
    DEFAULT_THRESHOLD_ICON,
    DEFAULT_WAKE_REASON,
    DEFAULT_WAKE_WINDOW_STATUS,
    ESP32_SENSOR_NAME_MAPPING,
    GESTURE_ICONS,
    GESTURE_PICTURES,
    GESTURE_PICTURES_FOLDER,
    GESTURE_STATES,
    INPUT_ICON_MAPPING,
    INPUT_TYPE_EVENTS,
    LIGHT_BRIGHTNESS_ESP_MAX,
    LIGHT_BRIGHTNESS_HA_MAX,
    LIGHT_COLOR_TEMP_MAX_KELVIN,
    LIGHT_COLOR_TEMP_MIN_KELVIN,
    LIGHT_COLOR_TEMP_RANGE_KELVIN,
    NUMBER_RANGE_CONFIGS,
    NUMBER_SENSOR_DISPLAY_NAMES,
    SLEEP_ICON_MAPPING,
    SLEEP_STATES,
    THRESHOLD_ICON_MAPPING,
    WAKE_REASONS,
    WAKE_WINDOW_STATES,
)

_LOGGER = logging.getLogger(__name__)

# Default values for device properties
DEFAULT_BRIGHTNESS = 100
DEFAULT_HUE = 0
DEFAULT_SATURATION = 0


def get_self_ip() -> str:
    """Get the local IP address of this machine.

    Uses a UDP connection to Google's DNS server to determine the
    local IP address without actually sending data.

    Returns:
        Local IP address as a string.

    Raises:
        OSError: If unable to determine local IP address.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Connect to Google DNS (no data is actually sent)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError as err:
        _LOGGER.error("Failed to determine local IP address: %s", err)
        raise


def generate_entity_id(platform: str, node_id: str, entity_name: str, domain: str = "esp_ha") -> str:
    """Generate a standardized entity ID.

    Creates a unique entity ID by combining the domain, node ID,
    platform, and entity name with proper formatting.

    Args:
        platform: The platform type (sensor, binary_sensor, light, etc.).
        node_id: The device's node ID.
        entity_name: The entity name or identifier.
        domain: The integration domain. Defaults to "esp_ha".

    Returns:
        Standardized entity ID string in format:
        "{domain}_{node_id}_{platform}_{entity_name}"

    Example:
        >>> generate_entity_id("sensor", "abc123", "Temperature")
        'esp_ha_abc123_sensor_temperature'
    """
    # Clean and format the components
    node_id_clean = str(node_id).replace(":", "").lower()
    name_slug = entity_name.lower().replace(" ", "_")

    return f"{domain}_{node_id_clean}_{platform}_{name_slug}"


def extract_current_values(properties: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract current values from ESP32 property response.

    Parses property response data to extract current device states
    for lights, sensors, and services.

    Args:
        properties: List of property dictionaries from device response.

    Returns:
        Dictionary of current values organized by service/device name.
    """
    current_values: dict[str, Any] = {}

    for prop in properties:
        if prop.get("name") == "params" and isinstance(prop.get("value"), bytes):
            try:
                # Parse params JSON data
                params_str = prop["value"].decode("latin-1")
                params_data = json.loads(params_str)

                # Extract light values
                light_data = params_data.get("Light", {})
                if light_data:
                    current_values.update(
                        {
                            "power": light_data.get("Power", False),
                            "brightness": light_data.get("Brightness", DEFAULT_BRIGHTNESS),
                            "hue": light_data.get("Hue", DEFAULT_HUE),
                            "saturation": light_data.get("Saturation", DEFAULT_SATURATION),
                        }
                    )

                    _LOGGER.debug("Extracted current light state: %s", current_values)

                # Extract Time service values
                time_data = params_data.get("Time", {})
                if time_data:
                    current_values["Time"] = time_data
                    _LOGGER.debug("Extracted current time service state: %s", time_data)

                # Extract Temperature Sensor values
                sensor_data = params_data.get("Temperature Sensor", {})
                if sensor_data:
                    current_values["Temperature Sensor"] = sensor_data
                    _LOGGER.debug(
                        "Extracted current environment sensor state: %s",
                        sensor_data,
                    )

                # Extract other services if present
                for service_name, service_data in params_data.items():
                    if service_name not in [
                        "Light",
                        "Time",
                        "Temperature Sensor",
                    ] and isinstance(service_data, dict):
                        current_values[service_name] = service_data
                        _LOGGER.debug(
                            "Extracted current service state %s: %s",
                            service_name,
                            service_data,
                        )

            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                _LOGGER.warning("Failed to parse device current state: %s", str(e))

    return current_values


def fire_property_events(
    hass: HomeAssistant, domain: str, node_id: str, params_data: dict
) -> None:
    """Fire Home Assistant events for ESP32 property updates.

    This is the central function for firing all property update events.
    Used by both:
    - Active reports from ESP32 (active report handler)
    - Query responses (device state updates)

    Args:
        hass: Home Assistant instance
        domain: Integration domain name
        node_id: Device node ID (will be normalized)
        params_data: Dictionary containing property data from ESP32
    """
    normalized_node_id = str(node_id).replace(":", "").lower()

    # Battery & Energy
    if "Battery & Energy" in params_data:
        battery_data = params_data["Battery & Energy"]
        hass.bus.async_fire(
            f"{domain}_battery_energy_update",
            {
                "node_id": normalized_node_id,
                "battery_data": {
                    "battery_level": battery_data.get("Battery Level", None),
                    "voltage": battery_data.get("Voltage", None),
                    "temperature": battery_data.get("Temperature", None),
                    "charging_status": battery_data.get("Charging Status", None),
                    "alert_level": battery_data.get("Alert Level", None),
                },
                "timestamp": time.time(),
            },
        )
        _LOGGER.debug(
            "Fired battery_energy_update for %s: %s",
            normalized_node_id,
            battery_data,
        )

    # Interactive Input
    if "Interactive Input" in params_data:
        input_data = params_data["Interactive Input"]
        hass.bus.async_fire(
            f"{domain}_interactive_input_update",
            {
                "node_id": normalized_node_id,
                "input_data": {
                    "input_type": input_data.get("Input Type", None),
                    "last_event": input_data.get("Last Event", None),
                    "input_events": input_data.get("Input Events", None),
                    "current_value": input_data.get("Input Value", None),
                    "input_config": input_data.get("Input Config", None),
                    "input_mapping": input_data.get("Input Mapping", None),
                    "sensitivity": input_data.get("Sensitivity", None),
                },
                "timestamp": time.time(),
            },
        )
        _LOGGER.debug(
            "Fired interactive_input_update for %s: %s",
            normalized_node_id,
            input_data,
        )

    # IMU Gesture Sensor
    if "IMU Gesture Sensor" in params_data:
        gesture_data = params_data["IMU Gesture Sensor"]
        
        # Extract sensor data with detailed logging
        sensor_data_payload = {
            "gesture_type": gesture_data.get("Gesture Type", None),
            "gesture_confidence": gesture_data.get("Gesture Confidence", None),
            "x_orientation": gesture_data.get("X Orientation", None),
            "y_orientation": gesture_data.get("Y Orientation", None),
            "z_orientation": gesture_data.get("Z Orientation", None),
            "orientation_change": gesture_data.get("Orientation Change", None),
            "power": gesture_data.get("Power", None),
            "shake_event": gesture_data.get("Shake Event", None),
            "push_event": gesture_data.get("Push Event", None),
            "circle_event": gesture_data.get("Circle Event", None),
            "flip_event": gesture_data.get("Flip Event", None),
            "toss_event": gesture_data.get("Toss Event", None),
            "rotation_event": gesture_data.get("Rotation Event", None),
            "sensitivity": gesture_data.get("Sensitivity", None),
        }
        
        hass.bus.async_fire(
            f"{domain}_imu_gesture_update",
            {
                "node_id": normalized_node_id,
                "sensor_data": sensor_data_payload,
                "timestamp": time.time(),
            },
        )
        
        # Log with details about what was received
        _LOGGER.info(
            "Fired imu_gesture_update for %s: Raw data=%s, Confidence=%s",
            normalized_node_id,
            gesture_data,
            sensor_data_payload.get("gesture_confidence"),
        )

    # Low Power & Sleep
    if "Low Power & Sleep" in params_data:
        sleep_data = params_data["Low Power & Sleep"]
        hass.bus.async_fire(
            f"{domain}_low_power_sleep_update",
            {
                "node_id": normalized_node_id,
                "sleep_data": {
                    "sleep_state": sleep_data.get("Sleep State", None),
                    "wake_reason": sleep_data.get("Wake Reason", None),
                    "wake_window_status": sleep_data.get("Wake Window Status", None),
                    "sleep_duration": sleep_data.get("Sleep Duration", None),
                    "wake_count": sleep_data.get("Wake Count", None),
                },
                "timestamp": time.time(),
            },
        )
        _LOGGER.debug(
            "Fired low_power_sleep_update for %s: %s",
            normalized_node_id,
            sleep_data,
        )

    # Temperature Sensor
    if "Temperature Sensor" in params_data:
        env_data = params_data["Temperature Sensor"]
        threshold_keys = [
            "temp_min_threshold",
            "temp_max_threshold",
            "humidity_min_threshold",
            "humidity_max_threshold",
            "illuminance_min_threshold",
            "illuminance_max_threshold",
            "pressure_min_threshold",
            "pressure_max_threshold",
            "co2_min_threshold",
            "co2_max_threshold",
            "voc_min_threshold",
            "voc_max_threshold",
            "pm25_min_threshold",
            "pm25_max_threshold",
            "update_interval",
            "threshold_alarm_enabled",
        ]

        for sensor_name, sensor_value in env_data.items():
            if sensor_name in threshold_keys:
                continue
            if isinstance(sensor_value, (int, float)):
                sensor_type = sensor_name.lower().replace(" ", "_")
                hass.bus.async_fire(
                    f"{domain}_sensor_update",
                    {
                        "node_id": normalized_node_id,
                        "sensor_name": sensor_type,
                        "type": sensor_type,
                        "value": sensor_value,
                        "timestamp": time.time(),
                    },
                )

    # Binary Sensor
    bs_state = None
    device_class = None
    debounce_time = None
    report_interval = None
    has_binary_sensor_data = False

    if "State" in params_data:
        bs_state = params_data["State"]
        has_binary_sensor_data = True
    elif "Binary Sensor" in params_data:
        bs_data = params_data["Binary Sensor"]
        has_binary_sensor_data = True
        if isinstance(bs_data, dict):
            # Look for device_class
            if "device_class" in bs_data:
                device_class = bs_data["device_class"]

            # Look for configuration parameters
            if "debounce_time" in bs_data:
                debounce_time = bs_data["debounce_time"]
            if "report_interval" in bs_data:
                report_interval = bs_data["report_interval"]

            # Look for state (boolean values)
            for key, value in bs_data.items():
                if isinstance(value, bool) and key.lower() != "device_class":
                    bs_state = value
                    break
        # If bs_data is not a dict, check if it's a boolean
        elif isinstance(bs_data, bool):
            bs_state = bs_data

    # Fire event if we have ANY binary sensor data (state or device_class)
    if has_binary_sensor_data:
        event_data = {
            "node_id": normalized_node_id,
            "params": {},
            "timestamp": time.time(),
        }

        # Add state if available
        if bs_state is not None:
            event_data["sensor_value"] = bool(bs_state)

        # Add device_class if available
        if device_class is not None:
            event_data["params"]["device_class"] = device_class

        # Add configuration parameters if available
        if debounce_time is not None:
            event_data["params"]["debounce_time"] = debounce_time
        if report_interval is not None:
            event_data["params"]["report_interval"] = report_interval

        hass.bus.async_fire(f"{domain}_binary_sensor_update", event_data)

        _LOGGER.debug(
            "Fired binary_sensor_update for %s: state=%s, device_class=%s, debounce=%s, interval=%s",
            normalized_node_id,
            bool(bs_state) if bs_state is not None else "None",
            device_class if device_class is not None else "None",
            f"{debounce_time}ms" if debounce_time is not None else "None",
            f"{report_interval}ms" if report_interval is not None else "None",
        )

    # Light
    if "Light" in params_data:
        light_data = params_data["Light"]
        hass.bus.async_fire(
            f"{domain}_light_update",
            {
                "node_id": normalized_node_id,
                "light_data": {
                    "power": light_data.get("Power", None),
                    "brightness": light_data.get("Brightness", None),
                    "hue": light_data.get("Hue", None),
                    "saturation": light_data.get("Saturation", None),
                    "color_temp": light_data.get("cct", None),
                    "intensity": light_data.get("Intensity", None),
                    "light_mode": light_data.get("Light Mode", None),
                },
                "timestamp": time.time(),
            },
        )
        _LOGGER.debug(
            "Fired light_update for %s: power=%s, brightness=%s, hue=%s, sat=%s, cct=%s, intensity=%s, mode=%s",
            normalized_node_id,
            light_data.get("Power"),
            light_data.get("Brightness"),
            light_data.get("Hue"),
            light_data.get("Saturation"),
            light_data.get("cct"),
            light_data.get("Intensity"),
            light_data.get("Light Mode"),
        )


def get_battery_icon(
    battery_level: int, charging_status: str, alert_level: str
) -> str:
    """Return appropriate battery icon based on state.

    Selects the most appropriate Material Design Icon based on battery level,
    charging status, and alert level. Priority order:
    1. Critical/Low alert icons
    2. Charging icon (if charging)
    3. Level-based icons (battery, battery-50, battery-30, battery-10)

    Args:
        battery_level: Battery percentage (0-100)
        charging_status: Charging status ("charging", "discharging", etc.)
        alert_level: Alert level ("critical", "low", "normal")

    Returns:
        Material Design Icon string (e.g., "mdi:battery-charging")
    """
    if alert_level == "critical":
        return "mdi:battery-alert"
    if alert_level == "low":
        return "mdi:battery-low"
    if charging_status == "charging":
        return "mdi:battery-charging"
    if battery_level > 75:
        return "mdi:battery"
    if battery_level > 50:
        return "mdi:battery-50"
    if battery_level > 25:
        return "mdi:battery-30"
    return "mdi:battery-10"


def parse_battery_update(battery_data: dict[str, Any]) -> dict[str, Any]:
    """Parse and validate battery data from event.

    Extracts battery properties from raw event data, performing type conversion
    and None-checking. ESP32 devices send individual properties, so each field
    may be None.

    Args:
        battery_data: Raw battery data dictionary from event

    Returns:
        Dictionary with parsed battery properties:
        - battery_level: int or None
        - voltage: float or None
        - temperature: float or None
        - charging_status: str or None
        - alert_level: str or None
    """
    result: dict[str, Any] = {}

    # Parse battery level (percentage)
    if "battery_level" in battery_data and battery_data["battery_level"] is not None:
        result["battery_level"] = int(battery_data["battery_level"])

    # Parse voltage
    if "voltage" in battery_data and battery_data["voltage"] is not None:
        result["voltage"] = float(battery_data["voltage"])

    # Parse temperature
    if "temperature" in battery_data and battery_data["temperature"] is not None:
        result["temperature"] = float(battery_data["temperature"])

    # Parse charging status
    if (
        "charging_status" in battery_data
        and battery_data["charging_status"] is not None
    ):
        result["charging_status"] = str(battery_data["charging_status"])

    # Parse alert level
    if "alert_level" in battery_data and battery_data["alert_level"] is not None:
        result["alert_level"] = str(battery_data["alert_level"])

    return result


def get_binary_sensor_device_class(
    device_class_str: str, default: str = "door"
) -> str:
    """Get normalized binary sensor device class from string identifier.

    Args:
        device_class_str: Device class identifier string from device configuration
        default: Default device class to use if lookup fails (default: "door")

    Returns:
        Normalized device class string that matches BinarySensorDeviceClass enum values
    """
    if not device_class_str:
        return default

    # Normalize input
    normalized = device_class_str.lower().strip()

    # Look up in mapping
    return BINARY_SENSOR_DEVICE_CLASS_MAP.get(normalized, default)


def extract_node_id_from_device_info(device_info: dict) -> str:
    """Extract and normalize node ID from device info identifiers.

    Args:
        device_info: Device information dictionary containing identifiers set

    Returns:
        Normalized node ID string with colons removed and lowercased
    """
    identifiers = device_info.get("identifiers", set())
    if not identifiers:
        return ""

    # Get first identifier tuple and extract node ID (second element)
    first_identifier = next(iter(identifiers), None)
    if not first_identifier or len(first_identifier) < 2:
        return ""

    node_id = first_identifier[1]
    # Normalize: remove colons and lowercase
    return node_id.replace(":", "").lower()


def get_gesture_display_name(gesture: str, power: bool = True) -> str:
    """Get display name for gesture state.

    Args:
        gesture: Gesture identifier string
        power: Whether the gesture sensor is powered on

    Returns:
        Human-readable gesture display name
    """
    if not power:
        return "Disabled"
    return GESTURE_STATES.get(gesture, "Idle")


def get_gesture_icon(gesture: str, power: bool = True) -> str:
    """Get Material Design icon for gesture.

    Args:
        gesture: Gesture identifier string
        power: Whether the gesture sensor is powered on

    Returns:
        Material Design icon identifier
    """
    if not power:
        return "mdi:gesture-tap-hold"
    return GESTURE_ICONS.get(gesture, "mdi:gesture-tap")


def parse_gesture_update(sensor_data: dict, current_gesture: str) -> dict:
    """Parse gesture sensor data from update event.

    Args:
        sensor_data: Raw sensor data from ESP device
        current_gesture: Current gesture state (for event-based updates)

    Returns:
        Dictionary containing parsed gesture data with keys:
        - gesture: Detected gesture type
        - confidence: Gesture confidence level (0-100)
        - orientation: Dict with x, y, z orientation values
        - orientation_change: Orientation change state
        - power: Power state
        - push_event: Push event flag
        - shake_event: Shake event flag
        - circle_event: Circle event flag
        - flip_event: Flip event flag
        - toss_event: Toss event flag
        - rotation_event: Rotation event flag
        - sensitivity: Gesture sensitivity level (0-100)
    """
    result = {
        "gesture": current_gesture,
        "confidence": 0,
        "orientation": DEFAULT_ORIENTATION.copy(),
        "orientation_change": "normal",
        "power": True,
        "push_event": False,
        "shake_event": False,
        "circle_event": False,
        "flip_event": False,
        "toss_event": False,
        "rotation_event": False,
        "sensitivity": 50,
    }

    # Handle gesture type update
    if "gesture_type" in sensor_data and sensor_data["gesture_type"] is not None:
        gesture_type = sensor_data["gesture_type"]
        if gesture_type in GESTURE_STATES:
            result["gesture"] = gesture_type

    # Update confidence
    if (
        "gesture_confidence" in sensor_data
        and sensor_data["gesture_confidence"] is not None
    ):
        result["confidence"] = int(sensor_data["gesture_confidence"])

    # Update orientation
    if "x_orientation" in sensor_data and sensor_data["x_orientation"] is not None:
        result["orientation"]["x"] = float(sensor_data["x_orientation"])
    if "y_orientation" in sensor_data and sensor_data["y_orientation"] is not None:
        result["orientation"]["y"] = float(sensor_data["y_orientation"])
    if "z_orientation" in sensor_data and sensor_data["z_orientation"] is not None:
        result["orientation"]["z"] = float(sensor_data["z_orientation"])

    # Update orientation change
    if (
        "orientation_change" in sensor_data
        and sensor_data["orientation_change"] is not None
    ):
        result["orientation_change"] = sensor_data["orientation_change"]

    # Update power state
    if "power" in sensor_data and sensor_data["power"] is not None:
        result["power"] = bool(sensor_data["power"])

    # Update event flags and set gesture if event detected
    if "shake_event" in sensor_data and sensor_data["shake_event"] is not None:
        result["shake_event"] = bool(sensor_data["shake_event"])
        if result["shake_event"]:
            result["gesture"] = "shake"

    if "push_event" in sensor_data and sensor_data["push_event"] is not None:
        result["push_event"] = bool(sensor_data["push_event"])
        if result["push_event"]:
            result["gesture"] = "push"

    if "circle_event" in sensor_data and sensor_data["circle_event"] is not None:
        result["circle_event"] = bool(sensor_data["circle_event"])
        if result["circle_event"]:
            result["gesture"] = "circle"

    if "flip_event" in sensor_data and sensor_data["flip_event"] is not None:
        result["flip_event"] = bool(sensor_data["flip_event"])
        if result["flip_event"]:
            result["gesture"] = "flip"

    if "toss_event" in sensor_data and sensor_data["toss_event"] is not None:
        result["toss_event"] = bool(sensor_data["toss_event"])
        if result["toss_event"]:
            result["gesture"] = "toss"

    if "rotation_event" in sensor_data and sensor_data["rotation_event"] is not None:
        result["rotation_event"] = bool(sensor_data["rotation_event"])
        if result["rotation_event"]:
            result["gesture"] = "rotation"

    # Update sensitivity
    if "sensitivity" in sensor_data and sensor_data["sensitivity"] is not None:
        result["sensitivity"] = int(sensor_data["sensitivity"])

    return result


def check_gesture_pictures_available(component_path) -> bool:
    """Check if gesture pictures folder exists.

    Args:
        component_path: Path to the ESP_HA component directory

    Returns:
        True if pictures folder exists, False otherwise
    """
    pictures_path = Path(component_path) / GESTURE_PICTURES_FOLDER
    exists = pictures_path.is_dir()

    if not exists:
        _LOGGER.info(
            "Gesture pictures folder not found at: %s. "
            "To use custom gesture pictures, create this folder and add images. "
            "支持的格式: png, jpg, svg, gif",
            pictures_path,
        )
    else:
        _LOGGER.info("Gesture pictures folder found: %s", pictures_path)

    return exists


def get_gesture_picture_url(gesture: str, component_path, use_pictures: bool) -> str | None:
    """Get the picture URL for a specific gesture.

    Args:
        gesture: The gesture name (e.g., "shake", "flip")
        component_path: Path to the ESP_HA component directory
        use_pictures: Whether picture support is enabled

    Returns:
        The picture URL if available, None otherwise
    """
    if not use_pictures:
        return None

    # Get the picture filename for this gesture
    picture_name = GESTURE_PICTURES.get(gesture, "idle")

    # Try different image extensions
    extensions = [".png", ".jpg", ".jpeg", ".svg", ".gif"]

    pictures_path = Path(component_path) / GESTURE_PICTURES_FOLDER

    for ext in extensions:
        full_path = pictures_path / f"{picture_name}{ext}"
        if full_path.is_file():
            # Return relative file path - dashboard will use this
            return f"/api/esp_ha/gesture_images/{picture_name}{ext}"

    # If no picture found for this gesture, try idle as fallback
    if gesture != "idle":
        for ext in extensions:
            full_path = pictures_path / f"idle{ext}"
            if full_path.is_file():
                return f"/api/esp_ha/gesture_images/idle{ext}"

    _LOGGER.debug("No picture found for gesture: %s", gesture)
    return None


def get_input_icon(input_type: str, last_event: str) -> str:
    """Get icon for interactive input based on type and event.

    Args:
        input_type: Input type (button, rotary, touch)
        last_event: Last event that occurred

    Returns:
        Material Design icon identifier
    """
    input_type_icons = INPUT_ICON_MAPPING.get(input_type, {})
    return input_type_icons.get(last_event, "mdi:gesture-tap")


def validate_input_type(input_type: str) -> bool:
    """Validate if input type is supported.

    Args:
        input_type: Input type to validate

    Returns:
        True if input type is supported, False otherwise
    """
    return input_type in INPUT_TYPE_EVENTS


def validate_sensitivity(sensitivity: int) -> bool:
    """Validate if sensitivity value is in valid range.

    Args:
        sensitivity: Sensitivity value to validate (should be 0-100)

    Returns:
        True if sensitivity is valid, False otherwise
    """
    return 0 <= sensitivity <= 100


def parse_esp32_input_update(params: list[dict]) -> dict:
    """Parse ESP32 parameter updates for interactive input.

    Args:
        params: List of parameter dictionaries from ESP32

    Returns:
        Dictionary containing parsed input updates with keys:
        - input_type: Input device type
        - last_event: Last event that occurred
        - current_value: Current input value
        - input_config: Input configuration dict
        - input_mapping: Input event mapping dict
        - sensitivity: Input sensitivity level
    """
    updates = {}

    for param in params:
        param_type = param.get("type", "")
        param_value = param.get("value")

        if param_type == "esp.param.input_type":
            updates["input_type"] = param_value or "button"
        elif param_type == "esp.param.input_events":
            updates["last_event"] = param_value or "none"
        elif param_type == "esp.param.input_value":
            updates["current_value"] = param_value or 0.0
        elif param_type == "esp.param.input_config":
            try:
                updates["input_config"] = (
                    json.loads(param_value) if param_value else {}
                )
            except (json.JSONDecodeError, TypeError):
                updates["input_config"] = {}
        elif param_type == "esp.param.input_mapping":
            try:
                updates["input_mapping"] = (
                    json.loads(param_value) if param_value else {}
                )
            except (json.JSONDecodeError, TypeError):
                updates["input_mapping"] = {}
        elif param_type == "esp.param.last_event":
            updates["last_event"] = param_value or "none"
        elif param_type == "esp.param.sensitivity":
            updates["sensitivity"] = param_value or 50

    return updates


def parse_direct_input_update(input_data: dict) -> dict:
    """Parse direct input data updates.

    Args:
        input_data: Input data dictionary from direct update event

    Returns:
        Dictionary containing parsed input updates with keys:
        - input_type: Input device type
        - last_event: Last event that occurred
        - current_value: Current input value
        - input_config: Input configuration dict
        - input_mapping: Input event mapping dict
        - sensitivity: Input sensitivity level
    """
    updates = {}

    # Extract updates from input data (ESP32 sends individual properties, check for None)
    if "input_type" in input_data and input_data["input_type"] is not None:
        updates["input_type"] = input_data["input_type"]
    if "last_event" in input_data and input_data["last_event"] is not None:
        updates["last_event"] = input_data["last_event"]
    if "input_events" in input_data and input_data["input_events"] is not None:
        # input_events is the same as last_event in ESP32 reports
        updates["last_event"] = input_data["input_events"]
    if "current_value" in input_data and input_data["current_value"] is not None:
        updates["current_value"] = input_data["current_value"]
    if "input_config" in input_data and input_data["input_config"] is not None:
        updates["input_config"] = input_data["input_config"]
    if "input_mapping" in input_data and input_data["input_mapping"] is not None:
        updates["input_mapping"] = input_data["input_mapping"]
    if "sensitivity" in input_data and input_data["sensitivity"] is not None:
        updates["sensitivity"] = input_data["sensitivity"]

    return updates


# ============================================================================
# Light Utility Functions
# ============================================================================


def convert_brightness_to_ha(brightness_esp: float) -> int:
    """Convert ESP brightness (0-100) to Home Assistant brightness (0-255).

    Args:
        brightness_esp: Brightness value from ESP device (0-100 range)

    Returns:
        Brightness value for Home Assistant (0-255 range)
    """
    return int(brightness_esp * LIGHT_BRIGHTNESS_HA_MAX / LIGHT_BRIGHTNESS_ESP_MAX)


def convert_brightness_to_esp(brightness_ha: int) -> int:
    """Convert Home Assistant brightness (0-255) to ESP brightness (0-100).

    Args:
        brightness_ha: Brightness value from Home Assistant (0-255 range)

    Returns:
        Brightness value for ESP device (0-100 range)
    """
    return int(brightness_ha * LIGHT_BRIGHTNESS_ESP_MAX / LIGHT_BRIGHTNESS_HA_MAX)


def convert_temp_percent_to_kelvin(temp_percent: float) -> int:
    """Convert color temperature percentage to Kelvin.

    Args:
        temp_percent: Temperature percentage (0-100)

    Returns:
        Temperature in Kelvin (2000-6500)
    """
    return int(
        LIGHT_COLOR_TEMP_MIN_KELVIN
        + (temp_percent / 100) * LIGHT_COLOR_TEMP_RANGE_KELVIN
    )


def convert_temp_kelvin_to_percent(temp_kelvin: int) -> float:
    """Convert color temperature from Kelvin to percentage.

    Args:
        temp_kelvin: Temperature in Kelvin (2000-6500)

    Returns:
        Temperature as percentage (0-100), clamped to valid range
    """
    temp_percent = (
        (temp_kelvin - LIGHT_COLOR_TEMP_MIN_KELVIN) / LIGHT_COLOR_TEMP_RANGE_KELVIN
    ) * 100
    return max(0.0, min(100.0, temp_percent))


def parse_light_mode(effect: str) -> int | None:
    """Parse light mode number from effect string.

    Args:
        effect: Effect string in format "Mode N" where N is 0-5

    Returns:
        Mode number (0-5) if valid, None otherwise
    """
    try:
        mode_num = int(effect.split()[-1])
        if 0 <= mode_num <= 5:
            return mode_num
    except (ValueError, IndexError):
        pass
    return None


def parse_light_params(light_params: dict[str, Any]) -> dict[str, Any]:
    """Parse light parameters from discovery data.

    Args:
        light_params: Light parameters from device discovery

    Returns:
        Dictionary with parsed light state attributes:
        - is_on: Power state (bool)
        - brightness: HA brightness (0-255)
        - hs_color: Tuple of (hue, saturation)
        - intensity: Light intensity
        - light_mode: Current light mode
        - color_temp: Color temperature in Celsius
        - color_temp_kelvin: Color temperature in Kelvin
    """
    result = {}

    # Power state
    result["is_on"] = light_params.get("power", False)

    # Brightness conversion
    brightness_esp = light_params.get("brightness", 100)
    result["brightness"] = convert_brightness_to_ha(brightness_esp)

    # Color (hue/saturation)
    hue = light_params.get("hue", 0)
    saturation = light_params.get("saturation", 0)
    result["hs_color"] = (hue, saturation)

    # Custom parameters
    result["intensity"] = light_params.get("intensity", 25)
    result["light_mode"] = light_params.get("light_mode", 0)
    result["color_temp"] = light_params.get("color_temp", 25.4)

    # Color temperature in Kelvin
    if "color_temp" in light_params:
        temp_percent = light_params["color_temp"]
        result["color_temp_kelvin"] = convert_temp_percent_to_kelvin(temp_percent)

    return result


def parse_light_update(
    light_data: dict[str, Any], current_state: dict[str, Any]
) -> dict[str, Any]:
    """Parse light update data and return state changes.

    Args:
        light_data: Light update data from device
        current_state: Current light state with keys:
            - is_on, brightness, hs_color, intensity, light_mode,
              color_temp, color_temp_kelvin

    Returns:
        Dictionary with updated light state attributes
    """
    updates = {}

    # Power state
    if "power" in light_data:
        updates["is_on"] = light_data["power"]

    # Brightness
    if "brightness" in light_data:
        brightness = light_data["brightness"]
        if brightness is not None:
            updates["brightness"] = convert_brightness_to_ha(brightness)

    # Color (hue/saturation)
    if all(key in light_data for key in ["hue", "saturation"]):
        hue = light_data["hue"]
        saturation = light_data["saturation"]
        if hue is not None and saturation is not None:
            updates["hs_color"] = (hue, saturation)

    # Custom parameters - only update if not None
    if "intensity" in light_data and light_data["intensity"] is not None:
        updates["intensity"] = light_data["intensity"]
    if "light_mode" in light_data and light_data["light_mode"] is not None:
        updates["light_mode"] = light_data["light_mode"]
    if "color_temp" in light_data and light_data["color_temp"] is not None:
        updates["color_temp"] = light_data["color_temp"]
        # Color temperature in Kelvin - only update when color_temp is valid
        temp_percent = light_data["color_temp"]
        updates["color_temp_kelvin"] = convert_temp_percent_to_kelvin(temp_percent)

    return updates


# ============================================================================
# Low Power & Sleep Utility Functions
# ============================================================================


def get_sleep_icon(sleep_state: str) -> str:
    """Get icon for sleep state.

    Args:
        sleep_state: Current sleep state

    Returns:
        Material Design icon identifier
    """
    return SLEEP_ICON_MAPPING.get(sleep_state, "mdi:help-circle")


def get_sleep_state_text(sleep_state: str) -> str:
    """Get human-readable text for sleep state.

    Args:
        sleep_state: Sleep state key

    Returns:
        Human-readable sleep state description
    """
    return SLEEP_STATES.get(sleep_state, "Unknown")


def get_wake_reason_text(wake_reason: str) -> str:
    """Get human-readable text for wake reason.

    Args:
        wake_reason: Wake reason key

    Returns:
        Human-readable wake reason description
    """
    return WAKE_REASONS.get(wake_reason, "Unknown")


def get_wake_window_text(wake_window_status: str) -> str:
    """Get human-readable text for wake window status.

    Args:
        wake_window_status: Wake window status key

    Returns:
        Human-readable wake window status description
    """
    return WAKE_WINDOW_STATES.get(wake_window_status, "Unknown")


def parse_sleep_update(
    sleep_data: dict[str, Any],
    current_state: dict[str, Any],
) -> dict[str, Any]:
    """Parse sleep update data and return state changes.

    Args:
        sleep_data: Sleep update data from device
        current_state: Current sleep state with keys:
            - sleep_state, wake_reason, wake_window_status,
              sleep_duration, wake_count, last_wake_time

    Returns:
        Dictionary with updated sleep state attributes
    """
    state_update = {}
    old_sleep_state = current_state.get("sleep_state", DEFAULT_SLEEP_STATE)

    # Update sleep state
    if "sleep_state" in sleep_data and sleep_data["sleep_state"] is not None:
        state_update["sleep_state"] = sleep_data["sleep_state"]
    else:
        state_update["sleep_state"] = old_sleep_state

    # Update wake reason
    if "wake_reason" in sleep_data and sleep_data["wake_reason"] is not None:
        state_update["wake_reason"] = sleep_data["wake_reason"]
    else:
        state_update["wake_reason"] = current_state.get(
            "wake_reason", DEFAULT_WAKE_REASON
        )

    # Update wake window status
    if (
        "wake_window_status" in sleep_data
        and sleep_data["wake_window_status"] is not None
    ):
        state_update["wake_window_status"] = sleep_data["wake_window_status"]
    else:
        state_update["wake_window_status"] = current_state.get(
            "wake_window_status", DEFAULT_WAKE_WINDOW_STATUS
        )

    # Update sleep duration
    if "sleep_duration" in sleep_data and sleep_data["sleep_duration"] is not None:
        state_update["sleep_duration"] = int(sleep_data["sleep_duration"])
    else:
        state_update["sleep_duration"] = current_state.get("sleep_duration", 0)

    # Update wake count - use ESP32's value if provided, otherwise calculate locally
    current_wake_count = current_state.get("wake_count", 0)
    if "wake_count" in sleep_data and sleep_data["wake_count"] is not None:
        wake_count = int(sleep_data["wake_count"])
    else:
        # Calculate wake count locally on state transition
        wake_count = current_wake_count
        if old_sleep_state != "awake" and state_update["sleep_state"] == "awake":
            wake_count += 1

    state_update["wake_count"] = wake_count

    # Update last wake time if wake count increased
    if wake_count > current_wake_count:
        state_update["last_wake_time"] = time.time()
    else:
        state_update["last_wake_time"] = current_state.get("last_wake_time", time.time())

    state_update["last_update"] = time.time()

    return state_update


# ============================================================================
# Number Entity Utilities
# ============================================================================


def get_number_sensor_display_name(sensor_type: str) -> str:
    """Get sensor display name for number entities.

    Args:
        sensor_type: The sensor type identifier

    Returns:
        Human-readable sensor name in English
    """
    return NUMBER_SENSOR_DISPLAY_NAMES.get(
        sensor_type, sensor_type.replace("_", " ").title()
    )


def get_number_device_class_and_unit(sensor_type: str) -> tuple[str | None, str | None]:
    """Get device class and unit for number entity based on sensor type.

    Args:
        sensor_type: The sensor type identifier

    Returns:
        Tuple of (device_class, unit_of_measurement)
    """
    device_class_mapping = {
        "temperature": (NumberDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
        "ambient_temperature": (NumberDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
        "humidity": (NumberDeviceClass.HUMIDITY, PERCENTAGE),
        "ambient_humidity": (NumberDeviceClass.HUMIDITY, PERCENTAGE),
        "pressure": (NumberDeviceClass.PRESSURE, UnitOfPressure.HPA),
        "illuminance": (None, LIGHT_LUX),
        "co2": (None, CONCENTRATION_PARTS_PER_MILLION),
        "voc": (None, CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
        "pm25": (None, CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
    }

    return device_class_mapping.get(sensor_type, (None, None))


def get_number_range_config(sensor_type: str, threshold_type: str) -> dict[str, float]:
    """Get number entity range configuration.

    Args:
        sensor_type: The sensor type identifier
        threshold_type: Either "min" or "max"

    Returns:
        Dictionary with keys: min, max, step, default
    """
    # Default configuration if sensor type not found
    default_config = {
        "min": {"min": 0, "max": 50, "step": 1, "default": 10},
        "max": {"min": 10, "max": 100, "step": 1, "default": 90},
    }

    sensor_config = NUMBER_RANGE_CONFIGS.get(sensor_type, default_config)
    return sensor_config.get(threshold_type, {"min": 0, "max": 100, "step": 1, "default": 50})


def get_threshold_icon(sensor_type: str, threshold_type: str) -> str:
    """Get icon for threshold number entity.

    Args:
        sensor_type: The sensor type identifier
        threshold_type: Either "min" or "max"

    Returns:
        MDI icon string
    """
    sensor_icons = THRESHOLD_ICON_MAPPING.get(sensor_type, {})
    return sensor_icons.get(threshold_type, DEFAULT_THRESHOLD_ICON)


def get_esp32_threshold_param_name(sensor_type: str, threshold_type: str) -> str:
    """Get ESP32 parameter name for threshold.

    Args:
        sensor_type: The sensor type identifier
        threshold_type: Either "min" or "max"

    Returns:
        ESP32 parameter name (e.g., "temp_min_threshold")
    """
    sensor_prefix = ESP32_SENSOR_NAME_MAPPING.get(sensor_type, sensor_type)
    return f"{sensor_prefix}_{threshold_type}_threshold"


# ============================================================================
# Panel Utilities
# ============================================================================


def filter_gesture_sensors(entity_registry, domain: str) -> list:
    """Filter all gesture sensors from entity registry.
    
    Args:
        entity_registry: Home Assistant entity registry
        domain: Integration domain to filter by
        
    Returns:
        List of gesture sensor entity entries
    """
    return [
        entry
        for entry in entity_registry.entities.values()
        if entry.domain == "sensor"
        and "_imu_gesture" in entry.entity_id
        and entry.platform == domain
    ]


def get_gesture_sensors_for_device(entity_registry, device_id: str, domain: str) -> list:
    """Get gesture sensors for a specific device.
    
    Args:
        entity_registry: Home Assistant entity registry
        device_id: Device ID to filter by
        domain: Integration domain to filter by
        
    Returns:
        List of gesture sensor entity entries for the device
    """
    return [
        entry
        for entry in entity_registry.entities.values()
        if entry.domain == "sensor"
        and "_imu_gesture" in entry.entity_id
        and entry.platform == domain
        and entry.device_id == device_id
    ]


def get_device_node_id_from_identifiers(device_entry, domain: str) -> str | None:
    """Extract node_id from device identifiers.
    
    Args:
        device_entry: Device registry entry
        domain: Integration domain to match
        
    Returns:
        Node ID if found, None otherwise
    """
    if not device_entry:
        return None
    
    for identifier in device_entry.identifiers:
        if identifier[0] == domain:
            return identifier[1]
    
    return None


def generate_panel_url_path(node_id: str | None, device_id: str, device_name: str) -> str:
    """Generate panel URL path from device information.
    
    Args:
        node_id: Device node ID (preferred)
        device_id: Device ID (fallback)
        device_name: Device name (not used in URL)
        
    Returns:
        URL path string for panel
    """
    if node_id:
        return f"esp_imu_{node_id.replace('-', '_')}"
    return f"esp_imu_{device_id.replace('-', '_')}"


def generate_panel_title(node_id: str | None, device_name: str) -> str:
    """Generate panel title from device information.
    
    Args:
        node_id: Device node ID (preferred for title)
        device_name: Device name (fallback)
        
    Returns:
        Panel title string
    """
    if node_id:
        return f"IMU: {node_id}"
    return f"IMU: {device_name}"


def build_gesture_panel_html(
    device_name: str,
    gesture_key: str,
    gesture_display: str,
    image_url: str,
    refresh_interval: int,
    styles: str,
) -> str:
    """Build complete HTML for gesture panel.
    
    Args:
        device_name: Name of the device
        gesture_key: Current gesture key/state
        gesture_display: Human-readable gesture display name
        image_url: URL to gesture image
        refresh_interval: Auto-refresh interval in seconds
        styles: CSS styles string
        
    Returns:
        Complete HTML string for panel
    """
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="{refresh_interval}">
<title>IMU: {device_name}</title>
<style>{styles}</style></head>
<body><div class="container"><h1>IMU Gesture</h1>
<img src="{image_url}" alt="{gesture_display}">
<div class="name">{gesture_display}</div>
<div class="info">Device: {device_name}</div></div></body></html>"""


# ============================================================================
# Sensor Platform Utilities
# ============================================================================


def get_sensor_display_name(sensor_type: str) -> str:
    """Get display name for sensor type.
    
    Args:
        sensor_type: Raw sensor type identifier
        
    Returns:
        Human-readable sensor display name
    """
    from .esp_iot_spec import SENSOR_DISPLAY_NAMES
    
    return SENSOR_DISPLAY_NAMES.get(
        sensor_type, sensor_type.replace("_", " ").title()
    )


def get_sensor_mapped_type(sensor_type: str) -> str:
    """Get mapped sensor type for number entity naming.
    
    Args:
        sensor_type: Raw sensor type identifier
        
    Returns:
        Mapped sensor type for consistent naming
    """
    from .esp_iot_spec import SENSOR_TYPE_TO_NUMBER_MAPPING
    
    return SENSOR_TYPE_TO_NUMBER_MAPPING.get(
        sensor_type.lower(), sensor_type.lower()
    )


def get_sensor_unit_display(sensor_type: str) -> str:
    """Get display unit for sensor type.
    
    Args:
        sensor_type: Sensor type identifier
        
    Returns:
        Unit string for display (e.g., "°C", "%", "hPa")
    """
    from .esp_iot_spec import SENSOR_UNIT_DISPLAY
    
    return SENSOR_UNIT_DISPLAY.get(sensor_type, "")


def is_threshold_sensor(sensor_type: str) -> bool:
    """Check if sensor type is a threshold-related entity.
    
    Args:
        sensor_type: Sensor type identifier
        
    Returns:
        True if this is a threshold entity, False otherwise
    """
    from .esp_iot_spec import THRESHOLD_PATTERNS
    
    return any(pattern in sensor_type for pattern in THRESHOLD_PATTERNS)


def get_sensor_threshold_icon(sensor_type: str, threshold_type: str) -> str:
    """Get icon for sensor threshold helper.
    
    Args:
        sensor_type: Sensor type identifier
        threshold_type: Either "min" or "max"
        
    Returns:
        MDI icon string for the threshold type
    """
    from .esp_iot_spec import (
        SENSOR_THRESHOLD_ICONS,
        DEFAULT_SENSOR_THRESHOLD_ICON,
    )
    
    return SENSOR_THRESHOLD_ICONS.get(sensor_type, {}).get(
        threshold_type, DEFAULT_SENSOR_THRESHOLD_ICON
    )


def get_sensor_threshold_config(sensor_type: str) -> dict[str, dict[str, any]] | None:
    """Get threshold configuration for sensor type.
    
    Args:
        sensor_type: Sensor type identifier
        
    Returns:
        Dictionary with "min" and "max" threshold configs, or None if not found
    """
    from .esp_iot_spec import SENSOR_THRESHOLD_CONFIGS
    
    return SENSOR_THRESHOLD_CONFIGS.get(sensor_type)


def parse_threshold_param_name(param_name: str) -> tuple[str | None, str | None]:
    """Parse threshold parameter name to extract sensor type and threshold type.
    
    Args:
        param_name: Parameter name like "temperature_min_threshold" or "humidity_max_threshold"
        
    Returns:
        Tuple of (sensor_type, threshold_type) or (None, None) if parsing fails
        
    Examples:
        >>> parse_threshold_param_name("temperature_min_threshold")
        ("temperature", "min")
        >>> parse_threshold_param_name("humidity_max_threshold")
        ("humidity", "max")
    """
    try:
        if param_name.endswith("_min_threshold"):
            sensor_type = param_name.replace("_min_threshold", "")
            return (sensor_type, "min")
        elif param_name.endswith("_max_threshold"):
            sensor_type = param_name.replace("_max_threshold", "")
            return (sensor_type, "max")
        return (None, None)
    except Exception:
        return (None, None)


def calculate_sensor_trend(values: list[float], threshold: float = 0.1) -> str:
    """Calculate trend direction from sensor value history.
    
    Args:
        values: List of sensor values in chronological order
        threshold: Minimum percentage change to detect trend (default 0.1 = 10%)
        
    Returns:
        Trend direction: "increasing", "decreasing", or "stable"
    """
    from .esp_iot_spec import MIN_TREND_DATA_POINTS
    
    if len(values) < MIN_TREND_DATA_POINTS:
        return "stable"
    
    try:
        first_half = values[: len(values) // 2]
        second_half = values[len(values) // 2 :]
        
        if not first_half or not second_half:
            return "stable"
        
        first_avg = sum(first_half) / len(first_half)
        second_avg = sum(second_half) / len(second_half)
        
        # Avoid division by zero
        denominator = max(abs(first_avg), 0.1)
        diff_ratio = abs(second_avg - first_avg) / denominator
        
        if diff_ratio > threshold:
            return "increasing" if second_avg > first_avg else "decreasing"
        
        return "stable"
    except Exception:
        return "stable"


def build_threshold_alert_message(
    sensor_type: str,
    alert_type: str,
    current_value: float,
    threshold_value: float,
    unit: str,
) -> str:
    """Build threshold alert message for ESP32.
    
    Args:
        sensor_type: Sensor type identifier
        alert_type: Alert type ("high" or "low")
        current_value: Current sensor value
        threshold_value: Threshold value that was violated
        unit: Unit of measurement
        
    Returns:
        Formatted alert message string
    """
    return f"ALERT|{sensor_type}|{alert_type}|{current_value}|{threshold_value}|{unit}"


def build_threshold_clear_message(
    sensor_type: str, current_value: float, unit: str
) -> str:
    """Build threshold alert clear message for ESP32.
    
    Args:
        sensor_type: Sensor type identifier
        current_value: Current sensor value (back in normal range)
        unit: Unit of measurement
        
    Returns:
        Formatted clear message string
    """
    return f"CLEAR|{sensor_type}|{current_value}|{unit}"


def build_sensor_number_entity_id(
    domain: str, node_id: str, sensor_type: str, threshold_type: str
) -> str:
    """Build number entity ID for sensor threshold.
    
    Args:
        domain: Integration domain
        node_id: Device node ID
        sensor_type: Raw sensor type identifier
        threshold_type: Either "min" or "max"
        
    Returns:
        Full entity ID string (e.g., "number.esp_ha_39cc_temperature_min_threshold")
    """
    return f"number.{domain}_{node_id}_{sensor_type}_{threshold_type}_threshold"


def build_sensor_pinyin_entity_id(
    node_id: str, sensor_type: str, threshold_type: str
) -> str:
    """Build pinyin format number entity ID for sensor threshold.
    
    Args:
        node_id: Device node ID
        sensor_type: Raw sensor type identifier
        threshold_type: Either "min" (zui_xiao_yu_zhi) or "max" (zui_da_yu_zhi)
        
    Returns:
        Pinyin format entity ID (e.g., "number.esp_39cc_huan_jing_wen_du_zui_xiao_yu_zhi")
    """
    pinyin_suffix = "zui_xiao_yu_zhi" if threshold_type == "min" else "zui_da_yu_zhi"
    return f"number.esp_{node_id}_{sensor_type}_{pinyin_suffix}"


def get_sensor_statistics(
    values: list[float], minutes: int = 10
) -> dict[str, any]:
    """Calculate statistics for sensor value history.
    
    Args:
        values: List of numeric sensor values
        minutes: Duration of history in minutes
        
    Returns:
        Dictionary with min, max, average, variance, std_deviation, trend, etc.
    """
    from .esp_iot_spec import TREND_CHANGE_THRESHOLD
    
    if not values:
        return {"error": "No data available"}
    
    try:
        # Calculate trend
        trend = calculate_sensor_trend(values, TREND_CHANGE_THRESHOLD)
        
        statistics = {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "average": round(sum(values) / len(values), 2),
            "first_value": values[0],
            "last_value": values[-1],
            "range": max(values) - min(values),
            "trend": trend,
            "duration_minutes": minutes,
        }
        
        # Add variance if enough data points
        if len(values) >= 3:
            avg = statistics["average"]
            variance = sum((v - avg) ** 2 for v in values) / len(values)
            statistics["variance"] = round(variance, 2)
            statistics["std_deviation"] = round(variance**0.5, 2)
        
        return statistics
    except Exception as err:
        return {"error": str(err)}
