"""Device specification definitions for ESP-RainMaker devices.

This module contains all device type mappings, parameter type mappings,
and sensor definitions used to map ESP-RainMaker configurations to
Home Assistant platforms and entities.
"""

from __future__ import annotations

import json
from typing import Any, Final

# Default sensor values
DEFAULT_DEBOUNCE_TIME: Final = 100
DEFAULT_REPORT_INTERVAL: Final = 1000
DEFAULT_BRIGHTNESS: Final = 100
DEFAULT_HUE: Final = 0
DEFAULT_SATURATION: Final = 0
DEFAULT_COLOR_TEMP: Final = 4000
DEFAULT_INTENSITY: Final = 25
DEFAULT_LIGHT_MODE: Final = 0

# Device types for property extraction
DEVICE_TYPES: Final[list[str]] = [
    "Time",
    "Light",
    "Temperature Sensor",
    "Binary Sensor",
    "Interactive Input",
    "IMU Gesture Sensor",
    "Battery & Energy",
    "Low Power & Sleep",
]

# Configuration property names to try
CONFIG_PROPERTY_NAMES: Final[list[str]] = [
    "config",
    "device_config",
    "configuration",
    "setup",
    "info",
]

# Light parameter name mappings for ESP-RainMaker format
LIGHT_PARAM_MAP: Final[dict[str, str]] = {
    "power": "Power",
    "brightness": "Brightness",
    "hue": "Hue",
    "saturation": "Saturation",
    "cct": "CCT",
    "intensity": "Intensity",
    "light mode": "Mode",
    "color temp": "CCT",
    "colortemp": "CCT",
}

# Device type detection keywords for payload creation
LIGHT_PARAM_KEYWORDS: Final[list[str]] = [
    "power",
    "brightness",
    "hue",
    "saturation",
    "cct",
    "intensity",
    "light mode",
    "color temp",
    "colortemp",
]

SENSOR_PARAM_KEYWORDS: Final[list[str]] = [
    "temperature",
    "threshold",
    "sensor",
]

BATTERY_PARAM_KEYWORDS: Final[list[str]] = [
    "battery",
    "voltage",
    "charging",
]

# ESP Local Control protocol constants
# Property types enum (from ESP-IDF)
PROP_TYPE_TIMESTAMP: Final = 0
PROP_TYPE_INT32: Final = 1
PROP_TYPE_BOOLEAN: Final = 2
PROP_TYPE_STRING: Final = 3

# Property flags enum (from ESP-IDF)
PROP_FLAG_READONLY: Final = 1 << 0

# ESP Local Control connection defaults
DEFAULT_PORT: Final = 8080
DEFAULT_SECURITY_MODE: Final = 1

# ESP-RainMaker property indices
PARAMS_PROPERTY_INDEX: Final = 1
CONFIG_PROPERTY_INDEX: Final = 0

# Battery state mappings
BATTERY_STATES: Final[dict[str, str]] = {
    "charging": "Charging",
    "discharging": "Discharging",
    "full": "Fully Charged",
    "not_charging": "Not Charging",
    "unknown": "Unknown",
}

# Battery alert levels mapping
BATTERY_ALERT_LEVELS: Final[dict[str, str]] = {
    "normal": "Normal",
    "low": "Low Battery",
    "critical": "Critical Battery",
}

# Binary sensor device class mappings
DEFAULT_BINARY_SENSOR_DEVICE_CLASS: Final = "door"

# Mapping of string identifiers to BinarySensorDeviceClass values
# Note: This dictionary maps lowercase strings to class name strings
# The actual BinarySensorDeviceClass enum mapping happens in utility functions
BINARY_SENSOR_DEVICE_CLASS_MAP: Final[dict[str, str]] = {
    # Door and window sensors
    "door": "door",
    "window": "window",
    "garage_door": "garage_door",
    "opening": "opening",
    # Power and connectivity sensors
    "plug": "plug",
    "power": "power",
    # Motion and presence sensors
    "motion": "motion",
    "occupancy": "occupancy",
    "presence": "presence",
    # Safety and environmental sensors
    "safety": "safety",
    "smoke": "smoke",
    "gas": "gas",
    "co": "co",
    "moisture": "moisture",
    # Security sensors
    "vibration": "vibration",
    "tamper": "tamper",
    # Other sensor types
    "light": "light",
    "cold": "cold",
    "heat": "heat",
    "sound": "sound",
    "running": "running",
    "connectivity": "connectivity",
    "battery": "battery",
    "problem": "problem",
    "update": "update",
    # Compatibility mappings
    "contact": "door",
    "movement": "motion",
    "human": "motion",
    "touch": "occupancy",
    "switch": "plug",
}

# Sensor type definitions with units, device classes, icons, and name templates
SENSOR_DEFINITIONS: Final[dict[str, dict[str, str]]] = {
    "ambient_temperature": {
        "unit": "°C",
        "device_class": "temperature",
        "icon": "mdi:thermometer",
        "name_template": "{} Temperature",
    },
    "temperature": {
        "unit": "°C",
        "device_class": "temperature",
        "icon": "mdi:thermometer",
        "name_template": "{} Temperature",
    },
    "ambient_humidity": {
        "unit": "%",
        "device_class": "humidity",
        "icon": "mdi:water-percent",
        "name_template": "{} Humidity",
    },
    "humidity": {
        "unit": "%",
        "device_class": "humidity",
        "icon": "mdi:water-percent",
        "name_template": "{} Humidity",
    },
    "illuminance": {
        "unit": "lx",
        "device_class": "illuminance",
        "icon": "mdi:brightness-5",
        "name_template": "{} Light Level",
    },
    "pressure": {
        "unit": "hPa",
        "device_class": "pressure",
        "icon": "mdi:gauge",
        "name_template": "{} Pressure",
    },
    "co2": {
        "unit": "ppm",
        "device_class": "carbon_dioxide",
        "icon": "mdi:molecule-co2",
        "name_template": "{} CO2",
    },
    "voc": {
        "unit": "μg/m³",
        "device_class": "volatile_organic_compounds",
        "icon": "mdi:air-filter",
        "name_template": "{} VOC",
    },
    "pm25": {
        "unit": "μg/m³",
        "device_class": "pm25",
        "icon": "mdi:air-filter",
        "name_template": "{} PM2.5",
    },
}


def get_device_type_mapping() -> dict[str, str | None]:
    """Create mapping of ESP-RainMaker device types to HA platforms.

    Returns:
        Mapping from ESP device types to Home Assistant platforms.
    """
    return {
        "esp.device.lightbulb": "light",
        "esp.device.switch": "switch",
        "esp.device.fan": "fan",
        "esp.device.sensor": "sensor",
        "esp.device.temperature-sensor": "sensor",
        "esp.device.humidity-sensor": "sensor",
        "esp.device.binary-sensor": "binary_sensor",
        "esp.device.imu-gesture": "imu_gesture",
        "esp.device.interactive-input": "interactive_input",
        "esp.device.battery-energy": "battery_energy",
        "esp.device.low-power-sleep": "low_power_sleep",
        "esp.service.time": "sensor",
        "esp.service.schedule": None,
        "esp.service.scenes": None,
        "esp.service.system": None,
    }


def get_param_type_mapping() -> dict[str, str | None]:
    """Create mapping of ESP-RainMaker parameter types to HA entity types.

    Returns:
        Mapping from ESP parameter types to Home Assistant entity types.
    """
    mapping: dict[str, str | None] = {}

    # Basic device parameters
    mapping.update(_get_basic_param_mappings())

    # IMU gesture parameters
    mapping.update(_get_imu_gesture_param_mappings())

    # Interactive input parameters
    mapping.update(_get_interactive_input_param_mappings())

    # Battery & energy parameters
    mapping.update(_get_battery_energy_param_mappings())

    # Low power/sleep parameters
    mapping.update(_get_low_power_sleep_param_mappings())

    return mapping


def _get_basic_param_mappings() -> dict[str, str | None]:
    """Get basic parameter type mappings.

    Returns:
        Basic parameter mappings.
    """
    return {
        "esp.param.power": "switch",
        "esp.param.brightness": "light_brightness",
        "esp.param.hue": "light_hue",
        "esp.param.saturation": "light_saturation",
        "esp.param.state": "binary_sensor",
        "esp.param.device_class": "binary_sensor",
        "esp.param.debounce_time": "binary_sensor",
        "esp.param.report_interval": "binary_sensor",
        "esp.param.temperature": "sensor",
        "esp.param.humidity": "sensor",
        "esp.param.co2": "sensor",
        "esp.param.pm25": "sensor",
        "esp.param.pressure": "sensor",
        "esp.param.voc": "sensor",
        "esp.param.custom.illuminance": "sensor",
        "esp.param.speed": "fan_speed",
        "esp.param.tz": "sensor",
        "esp.param.status": "sensor",
        "esp.param.name": None,
    }


def _get_imu_gesture_param_mappings() -> dict[str, str]:
    """Get IMU gesture parameter type mappings.

    Returns:
        IMU gesture parameter mappings.
    """
    return {
        # Core gesture parameters
        "esp.param.gesture-type": "imu_gesture",
        "esp.param.gesture-confidence": "imu_gesture",
        # Orientation parameters
        "esp.param.orientation-x": "imu_gesture",
        "esp.param.orientation-y": "imu_gesture",
        "esp.param.orientation-z": "imu_gesture",
        # Gesture event parameters
        "esp.param.gesture-toss": "imu_gesture",
        "esp.param.gesture-flip": "imu_gesture",
        "esp.param.gesture-shake": "imu_gesture",
        "esp.param.gesture-rotation": "imu_gesture",
        "esp.param.gesture-push": "imu_gesture",
        "esp.param.gesture-circle": "imu_gesture",
        "esp.param.gesture-clap-single": "imu_gesture",
        "esp.param.gesture-clap-double": "imu_gesture",
        "esp.param.gesture-clap-triple": "imu_gesture",
        "esp.param.orientation-change": "imu_gesture",
        # Legacy format compatibility
        "esp.param.gesture-event": "imu_gesture",
        "esp.param.gesture-count": "imu_gesture",
        # Custom IMU gesture parameters
        "imu_gesture_detection": "imu_gesture",
        "imu_gesture_type": "imu_gesture",
        "imu_gesture_confidence": "imu_gesture",
        "imu_gesture_timestamp": "imu_gesture",
        "imu_gesture_duration": "imu_gesture",
        "imu_gesture_x_axis": "imu_gesture",
        "imu_gesture_y_axis": "imu_gesture",
        "imu_gesture_z_axis": "imu_gesture",
        "imu_gesture_accelerometer_x": "imu_gesture",
        "imu_gesture_accelerometer_y": "imu_gesture",
        "imu_gesture_accelerometer_z": "imu_gesture",
        "imu_gesture_gyroscope_x": "imu_gesture",
        "imu_gesture_gyroscope_y": "imu_gesture",
        "imu_gesture_gyroscope_z": "imu_gesture",
        "imu_gesture_magnetometer_x": "imu_gesture",
        "imu_gesture_magnetometer_y": "imu_gesture",
        "imu_gesture_magnetometer_z": "imu_gesture",
        "imu_gesture_orientation": "imu_gesture",
        "imu_gesture_activity": "imu_gesture",
        "imu_gesture_steps": "imu_gesture",
    }


def _get_interactive_input_param_mappings() -> dict[str, str]:
    """Get interactive input parameter type mappings.

    Returns:
        Interactive input parameter mappings.
    """
    return {
        "esp.param.input_type": "interactive_input",
        "esp.param.input_events": "interactive_input",
        "esp.param.input_value": "interactive_input",
        "esp.param.input_config": "interactive_input",
        "esp.param.input_mapping": "interactive_input",
        "esp.param.last_event": "interactive_input",
    }


def _get_battery_energy_param_mappings() -> dict[str, str]:
    """Get battery & energy parameter type mappings.

    Returns:
        Battery & energy parameter mappings.
    """
    return {
        "esp.param.battery_level": "battery_energy",
        "esp.param.voltage": "battery_energy",
        "esp.param.temperature": "battery_energy",
        "esp.param.charging_status": "battery_energy",
        "esp.param.alert_level": "battery_energy",
        "esp.param.battery_health": "battery_energy",
        "esp.param.charge_current": "battery_energy",
        "esp.param.discharge_current": "battery_energy",
        # Battery configuration parameters mapped to number platform
        "esp.param.battery_low_threshold": "number",
        "esp.param.battery_sampling_interval": "number",
    }


def _get_low_power_sleep_param_mappings() -> dict[str, str]:
    """Get low power/sleep parameter type mappings.

    Returns:
        Low power/sleep parameter mappings.
    """
    return {
        "esp.param.sleep_state": "low_power_sleep",
        "esp.param.wake_reason": "low_power_sleep",
        "esp.param.wake_window_status": "low_power_sleep",
        "esp.param.sleep_duration": "low_power_sleep",
        "esp.param.wake_count": "low_power_sleep",
        "esp.param.power_mode": "low_power_sleep",
    }


def get_sensor_definition(sensor_type: str) -> dict[str, str] | None:
    """Get sensor definition for a specific sensor type.

    Args:
        sensor_type: The sensor type identifier (e.g., "temperature", "humidity").

    Returns:
        Sensor definition dictionary with unit, device_class, icon, and name_template,
        or None if sensor type is not defined.
    """
    return SENSOR_DEFINITIONS.get(sensor_type)


def get_threshold_patterns() -> list[str]:
    """Get list of threshold parameter patterns to filter.

    These are configuration parameters that should not be treated as sensor entities.

    Returns:
        List of threshold parameter name patterns.
    """
    return [
        "_min_threshold",
        "_max_threshold",
        "_threshold_alarm",
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
    ]


def get_config_param_names() -> list[str]:
    """Get list of configuration parameter names to filter.

    These are configuration parameters that should not be treated as sensor entities.

    Returns:
        List of configuration parameter names.
    """
    return [
        "update_interval",
        "sampling_interval",
        "reporting_interval",
    ]


def is_light_property(prop_name: str) -> bool:
    """Check if property name indicates light capability.

    Args:
        prop_name: Property name to check.

    Returns:
        bool: True if property is light-related.
    """
    light_keywords = ["power", "brightness", "light", "led", "hue", "saturation"]
    return any(keyword in prop_name for keyword in light_keywords)


def is_sensor_property(prop_name: str) -> bool:
    """Check if property name indicates sensor capability.

    Args:
        prop_name: Property name to check.

    Returns:
        bool: True if property is sensor-related.
    """
    sensor_keywords = [
        "temperature",
        "humidity",
        "pressure",
        "co2",
        "voc",
        "pm25",
        "illuminance",
    ]
    return any(keyword in prop_name for keyword in sensor_keywords)


def is_binary_sensor_property(prop_name: str) -> bool:
    """Check if property name indicates binary sensor capability.

    Args:
        prop_name: Property name to check.

    Returns:
        bool: True if property is binary sensor-related.
    """
    binary_keywords = ["state", "door", "window", "motion", "binary"]
    return any(keyword in prop_name for keyword in binary_keywords)


def analyze_params_data(prop_value, capabilities: dict) -> None:
    """Analyze params property for nested capability data.

    Args:
        prop_value: Property value (bytes or dict).
        capabilities: Capabilities dict to update.
    """
    if not isinstance(prop_value, (bytes, dict)):
        return

    params_data = None
    if isinstance(prop_value, dict):
        params_data = prop_value
    elif isinstance(prop_value, bytes):
        try:
            params_str = prop_value.decode("latin-1")
            if params_str.startswith("{"):
                params_data = json.loads(params_str)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    if params_data:
        if "Light" in params_data:
            capabilities["has_light"] = True
        if "Temperature Sensor" in params_data:
            capabilities["has_sensor"] = True
        if any(key in params_data for key in ["State", "Binary Sensor"]):
            capabilities["has_binary_sensor"] = True


def add_light_platform(device_config: dict) -> None:
    """Add light platform configuration.

    Args:
        device_config: Device configuration to update.
    """
    device_config["platforms"]["light"] = [
        {
            "device_name": "ESP Light",
            "capabilities": {"brightness": True, "color": True},
            "initial_values": {},
        }
    ]


def add_sensor_platform(device_config: dict) -> None:
    """Add sensor platform configuration with common sensor types.

    Args:
        device_config: Device configuration to update.
    """
    device_config["platforms"]["sensor"] = []

    sensor_types = [
        ("temperature", "Temperature", "°C", "temperature"),
        ("humidity", "Humidity", "%", "humidity"),
        ("pressure", "Pressure", "hPa", "pressure"),
        ("co2", "CO2", "ppm", "carbon_dioxide"),
        ("voc", "VOC", "ppb", None),
        ("pm25", "PM2.5", "µg/m³", "pm25"),
        ("illuminance", "Illuminance", "lx", "illuminance"),
    ]

    for sensor_key, name, unit, device_class in sensor_types:
        sensor_config = {
            "device_name": f"ESP-{name}",
            "sensor_type": sensor_key,
            "sensor_name": f"esp_{sensor_key}",
            "param": {"name": name, "type": "float"},
            "unit_of_measurement": unit,
        }
        if device_class:
            sensor_config["device_class"] = device_class
        device_config["platforms"]["sensor"].append(sensor_config)


def add_binary_sensor_platform(device_config: dict) -> None:
    """Add binary sensor platform configuration.

    Args:
        device_config: Device configuration to update.
    """
    device_config["platforms"]["binary_sensor"] = [
        {
            "device_name": "ESP Binary Sensor",
            "device_class": "door",
            "initial_values": {
                "state": False,
                "debounce_time": 100,
                "report_interval": 1000,
            },
        }
    ]


def add_platform_configs(device_config: dict, capabilities: dict) -> None:
    """Add platform configurations based on detected capabilities.

    Args:
        device_config: Device configuration to update.
        capabilities: Detected device capabilities.
    """
    if capabilities["has_light"]:
        add_light_platform(device_config)

    if capabilities["has_sensor"]:
        add_sensor_platform(device_config)

    if capabilities["has_binary_sensor"]:
        add_binary_sensor_platform(device_config)

    # Always add number platform for threshold controls
    device_config["platforms"]["number"] = []


# IMU Gesture Recognition Constants
# IMU手势识别常量

# Gesture state display name mappings
GESTURE_STATES: Final[dict[str, str]] = {
    "idle": "Idle",
    "toss": "Toss",
    "flip": "Flip",
    "shake": "Shake",
    "rotation": "Rotation",
    "push": "Push",
    "circle": "Circle",
    "clap_single": "Clap Single",
    "clap_double": "Clap Double",
    "clap_triple": "Clap Triple",
    "orientation_change": "Orientation Change",
    "tap": "Tap",
    "double_tap": "Double Tap",
    "tilt_left": "Tilt Left",
    "tilt_right": "Tilt Right",
    "face_up": "Face Up",
    "face_down": "Face Down",
}

# Gesture to Material Design icon mapping
GESTURE_ICONS: Final[dict[str, str]] = {
    "idle": "mdi:gesture-tap",
    "toss": "mdi:arrow-up-bold",
    "flip": "mdi:flip-horizontal",
    "shake": "mdi:vibrate",
    "rotation": "mdi:rotate-3d-variant",
    "push": "mdi:gesture-swipe-right",
    "circle": "mdi:circle-outline",
    "clap_single": "mdi:hand-clap",
    "clap_double": "mdi:hand-clap",
    "clap_triple": "mdi:hand-clap",
    "orientation_change": "mdi:compass",
    "tap": "mdi:gesture-tap",
    "double_tap": "mdi:gesture-double-tap",
    "tilt_left": "mdi:rotate-left",
    "tilt_right": "mdi:rotate-right",
    "face_up": "mdi:arrow-up",
    "face_down": "mdi:arrow-down",
}

# Gesture picture file names (without extension)
# 手势图片文件名映射（不含扩展名）
GESTURE_PICTURES: Final[dict[str, str]] = {
    "idle": "idle",
    "toss": "toss",
    "flip": "flip",
    "shake": "shake",
    "rotation": "rotation",
    "push": "push",
    "circle": "circle",
    "clap_single": "clap_single",
    "clap_double": "clap_double",
    "clap_triple": "clap_triple",
    "orientation_change": "orientation_change",
    "tap": "tap",
    "double_tap": "double_tap",
    "tilt_left": "tilt_left",
    "tilt_right": "tilt_right",
    "face_up": "face_up",
    "face_down": "face_down",
}

# Picture folder path within ESP_HA component
# 图片文件夹路径（在ESP_HA组件内）
GESTURE_PICTURES_FOLDER: Final = "gesture_images"

# Default orientation structure
DEFAULT_ORIENTATION: Final[dict[str, float]] = {"x": 0.0, "y": 0.0, "z": 0.0}


# Interactive Input Constants
# 交互式输入常量

# Supported input types and their associated events
INPUT_TYPE_EVENTS: Final[dict[str, list[str]]] = {
    "button": ["click", "double_click", "long_press"],
    "rotary": ["angle", "increment", "speed"],
    "touch": ["tap", "double_tap", "long_press", "swipe"],
}

# Event value mappings for different input types
EVENT_VALUE_MAPPING: Final[dict[str, dict[str, int | str]]] = {
    "button": {
        "click": 1,
        "double_click": 2,
        "long_press": 3,
    },
    "rotary": {
        "angle": "angle_value",
        "increment": "increment_value",
        "speed": "speed_value",
    },
    "touch": {
        "tap": 1,
        "double_tap": 2,
        "long_press": 3,
        "swipe": 4,
    },
}

# Icon mappings for interactive input types and events
INPUT_ICON_MAPPING: Final[dict[str, dict[str, str]]] = {
    # Button event icons
    "button": {
        "click": "mdi:gesture-tap",
        "double_click": "mdi:gesture-double-tap",
        "long_press": "mdi:gesture-tap-hold",
        "none": "mdi:gesture-tap",
    },
    # Rotary event icons
    "rotary": {
        "angle": "mdi:rotate-3d",
        "increment": "mdi:rotate-right",
        "speed": "mdi:rotate-3d-variant",
        "none": "mdi:rotate-3d",
    },
    # Touch event icons
    "touch": {
        "tap": "mdi:gesture-tap",
        "double_tap": "mdi:gesture-double-tap",
        "long_press": "mdi:gesture-tap-hold",
        "swipe": "mdi:gesture-swipe",
        "none": "mdi:gesture-tap",
    },
}

# Default interactive input state structure
DEFAULT_INPUT_STATE: Final[dict[str, Any]] = {
    "input_type": "button",
    "last_event": "none",
    "current_value": 0.0,
    "input_config": {},
    "input_mapping": {},
    "sensitivity": 50,
}

# ============================================================================
# Light Configuration Constants
# ============================================================================

# Light color temperature range (Kelvin)
LIGHT_COLOR_TEMP_MIN_KELVIN: Final[int] = 2000
LIGHT_COLOR_TEMP_MAX_KELVIN: Final[int] = 6500
LIGHT_COLOR_TEMP_RANGE_KELVIN: Final[int] = 4500  # 6500 - 2000

# Light effect modes (light mode selections)
LIGHT_EFFECT_MODES: Final[list[str]] = [
    "Mode 0",
    "Mode 1",
    "Mode 2",
    "Mode 3",
    "Mode 4",
    "Mode 5",
]

# Light brightness conversion factors
LIGHT_BRIGHTNESS_HA_MAX: Final[int] = 255  # Home Assistant brightness range
LIGHT_BRIGHTNESS_ESP_MAX: Final[int] = 100  # ESP device brightness range

# ============================================================================
# Low Power & Sleep Configuration Constants
# ============================================================================

# Wake reason mapping - reasons why device woke from sleep
WAKE_REASONS: Final[dict[str, str]] = {
    "button": "Button Wake",
    "timer": "Timer Wake",
    "sensor": "Sensor Wake",
    "uart": "UART Wake",
    "gpio": "GPIO Wake",
    "wifi": "WiFi Wake",
    "ble": "Bluetooth Wake",
    "touch": "Touch Wake",
    "motion": "Motion Wake",
    "power_on": "Power On",
    "unknown": "Unknown Reason",
}

# Sleep state mapping - different power states
SLEEP_STATES: Final[dict[str, str]] = {
    "awake": "Awake",
    "light_sleep": "Light Sleep",
    "deep_sleep": "Deep Sleep",
    "hibernation": "Hibernation",
    "shutdown": "Shutdown",
}

# Wake window states - device availability during wake periods
WAKE_WINDOW_STATES: Final[dict[str, str]] = {
    "available": "Available",
    "busy": "Busy",
    "processing": "Processing",
    "unavailable": "Unavailable",
}

# Sleep state icon mapping
SLEEP_ICON_MAPPING: Final[dict[str, str]] = {
    "awake": "mdi:eye",
    "light_sleep": "mdi:sleep",
    "deep_sleep": "mdi:power-sleep",
    "hibernation": "mdi:snowflake",
    "shutdown": "mdi:power-off",
}

# Default sleep state values
DEFAULT_SLEEP_STATE: Final[str] = "awake"
DEFAULT_WAKE_REASON: Final[str] = "unknown"
DEFAULT_WAKE_WINDOW_STATUS: Final[str] = "available"

# ============================================================================
# Number Entity Configuration
# ============================================================================

# Sensor types that support threshold configuration
THRESHOLD_SENSOR_TYPES: Final[list[str]] = [
    "temperature",
    "ambient_temperature",
    "humidity",
    "ambient_humidity",
    "pressure",
    "illuminance",
    "co2",
    "voc",
    "pm25",
]

# Sensor display names for number entities (English)
NUMBER_SENSOR_DISPLAY_NAMES: Final[dict[str, str]] = {
    "temperature": "Temperature",
    "ambient_temperature": "Ambient Temperature",
    "humidity": "Humidity",
    "ambient_humidity": "Ambient Humidity",
    "pressure": "Pressure",
    "illuminance": "Illuminance",
    "co2": "CO2",
    "voc": "VOC",
    "pm25": "PM2.5",
}

# Number entity range configurations
# Format: {sensor_type: {threshold_type: {min, max, step, default}}}
NUMBER_RANGE_CONFIGS: Final[dict[str, dict[str, dict[str, float]]]] = {
    "temperature": {
        "min": {"min": -40, "max": 30, "step": 0.5, "default": 18},
        "max": {"min": 20, "max": 80, "step": 0.5, "default": 35},
    },
    "ambient_temperature": {
        "min": {"min": -40, "max": 30, "step": 0.5, "default": 18},
        "max": {"min": 20, "max": 80, "step": 0.5, "default": 35},
    },
    "humidity": {
        "min": {"min": 0, "max": 40, "step": 1, "default": 30},
        "max": {"min": 50, "max": 100, "step": 1, "default": 80},
    },
    "ambient_humidity": {
        "min": {"min": 0, "max": 40, "step": 1, "default": 30},
        "max": {"min": 50, "max": 100, "step": 1, "default": 80},
    },
    "pressure": {
        "min": {"min": 900, "max": 1000, "step": 1, "default": 980},
        "max": {"min": 1000, "max": 1100, "step": 1, "default": 1030},
    },
    "illuminance": {
        "min": {"min": 0, "max": 500, "step": 10, "default": 100},
        "max": {"min": 100, "max": 50000, "step": 100, "default": 10000},
    },
    "co2": {
        "min": {"min": 300, "max": 800, "step": 25, "default": 400},
        "max": {"min": 600, "max": 5000, "step": 50, "default": 1200},
    },
    "voc": {
        "min": {"min": 0, "max": 200, "step": 10, "default": 50},
        "max": {"min": 100, "max": 2000, "step": 25, "default": 500},
    },
    "pm25": {
        "min": {"min": 0, "max": 50, "step": 1, "default": 25},
        "max": {"min": 25, "max": 500, "step": 5, "default": 75},
    },
}

# Threshold icon mappings
THRESHOLD_ICON_MAPPING: Final[dict[str, dict[str, str]]] = {
    "temperature": {
        "min": "mdi:thermometer-minus",
        "max": "mdi:thermometer-plus",
    },
    "ambient_temperature": {
        "min": "mdi:thermometer-minus",
        "max": "mdi:thermometer-plus",
    },
    "humidity": {"min": "mdi:water-minus", "max": "mdi:water-plus"},
    "ambient_humidity": {"min": "mdi:water-minus", "max": "mdi:water-plus"},
    "pressure": {"min": "mdi:gauge-low", "max": "mdi:gauge-full"},
    "illuminance": {"min": "mdi:brightness-5", "max": "mdi:brightness-7"},
    "co2": {"min": "mdi:molecule-co2", "max": "mdi:molecule-co2"},
    "voc": {"min": "mdi:air-filter", "max": "mdi:air-filter"},
    "pm25": {"min": "mdi:blur", "max": "mdi:blur"},
}

# ESP32 sensor name mapping for threshold parameters
ESP32_SENSOR_NAME_MAPPING: Final[dict[str, str]] = {
    "temperature": "temp",
    "ambient_temperature": "temp",
    "humidity": "humidity",
    "ambient_humidity": "humidity",
    "pressure": "pressure",
    "illuminance": "illuminance",
    "co2": "co2",
    "voc": "voc",
    "pm25": "pm25",
}

# Default threshold icon
DEFAULT_THRESHOLD_ICON: Final[str] = "mdi:tune"

# ============================================================================
# Panel Configuration
# ============================================================================

# Panel URL patterns
PANEL_GESTURE_IMAGE_URL_PATTERN: Final[str] = "/api/esp_ha/gesture_images/{filename}"
PANEL_GESTURE_VIEW_URL_PATTERN: Final[str] = "/api/panel/esp_imu_gesture/{device_id}"

# Panel configuration
PANEL_AUTO_REFRESH_INTERVAL: Final[int] = 2  # seconds
PANEL_GESTURE_ICON: Final[str] = "mdi:gesture-tap"
PANEL_IMAGE_SIZE: Final[int] = 400  # pixels (width and height)

# Default panel values
DEFAULT_GESTURE_STATE: Final[str] = "idle"
DEFAULT_DEVICE_NAME: Final[str] = "Unknown"

# Panel HTML CSS styling
PANEL_HTML_STYLES: Final[str] = """body{margin:0;padding:20px;font-family:Roboto,sans-serif;background:#fafafa;
display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh}
.container{background:white;border-radius:8px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,0.1);
text-align:center;max-width:600px}img{width:400px;height:400px;margin:20px 0;object-fit:contain}
.name{font-size:32px;font-weight:500;margin:16px 0;color:#333}
.info{font-size:14px;color:#666;margin-top:16px}"""

# ============================================================================
# Sensor Platform Configuration
# ============================================================================

# Sensor history and statistics configuration
DEFAULT_MAX_HISTORY_ITEMS: Final[int] = 30  # Default cached history entries (~10 minutes at 20s interval)
MAX_HISTORY_LIMIT: Final[int] = 10000  # Absolute maximum to prevent memory issues (safety limit)
MAX_LATEST_VALUES_DURATION: Final[int] = 3600  # Maximum 1 hour (3600 seconds) for latest values duration
DEFAULT_HISTORY_MINUTES: Final[int] = 5  # Default history duration for queries
DEFAULT_STATISTICS_MINUTES: Final[int] = 10  # Default statistics duration
TREND_CHANGE_THRESHOLD: Final[float] = 0.1  # 10% change threshold for trend detection
MIN_TREND_DATA_POINTS: Final[int] = 3  # Minimum data points needed for trend calculation

# Sensor display names mapping
SENSOR_DISPLAY_NAMES: Final[dict[str, str]] = {
    "ambient_temperature": "Temperature",
    "temperature": "Temperature",
    "ambient_humidity": "Humidity",
    "humidity": "Humidity",
    "pressure": "Pressure",
    "illuminance": "Illuminance",
    "co2": "CO2",
    "voc": "VOC",
    "pm25": "PM2.5",
}

# Sensor type to Number entity mapping (for threshold management)
SENSOR_TYPE_TO_NUMBER_MAPPING: Final[dict[str, str]] = {
    "ambient_temperature": "temperature",
    "ambient_humidity": "humidity",
    "temperature": "temperature",
    "humidity": "humidity",
    "pressure": "pressure",
    "illuminance": "illuminance",
    "co2": "co2",
    "voc": "voc",
    "pm25": "pm25",
    # Pinyin mappings for ESP32 compatibility
    "huan_jing_wen_du": "temperature",
    "huan_jing_shi_du": "humidity",
    "qi_ya": "pressure",
}

# Sensor unit display mapping
SENSOR_UNIT_DISPLAY: Final[dict[str, str]] = {
    "temperature": "°C",
    "ambient_temperature": "°C",
    "humidity": "%",
    "ambient_humidity": "%",
    "pressure": "hPa",
    "illuminance": "lux",
    "co2": "ppm",
    "voc": "μg/m³",
    "pm25": "μg/m³",
}

# Threshold pattern filters (for excluding threshold entities from sensor discovery)
THRESHOLD_PATTERNS: Final[list[str]] = [
    "_min_threshold",
    "_max_threshold",
    "_threshold_alarm",
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
]

# Sensor threshold icon mapping
SENSOR_THRESHOLD_ICONS: Final[dict[str, dict[str, str]]] = {
    "temperature": {
        "min": "mdi:thermometer-minus",
        "max": "mdi:thermometer-plus",
    },
    "humidity": {
        "min": "mdi:water-minus",
        "max": "mdi:water-plus",
    },
    "pressure": {
        "min": "mdi:gauge-low",
        "max": "mdi:gauge-full",
    },
    "illuminance": {
        "min": "mdi:brightness-5",
        "max": "mdi:brightness-7",
    },
    "co2": {
        "min": "mdi:molecule-co2",
        "max": "mdi:molecule-co2",
    },
    "voc": {
        "min": "mdi:air-filter",
        "max": "mdi:air-filter",
    },
    "pm25": {
        "min": "mdi:blur",
        "max": "mdi:blur",
    },
}

# Default threshold icon for unknown sensor types
DEFAULT_SENSOR_THRESHOLD_ICON: Final[str] = "mdi:tune"

# Sensor threshold configuration (for creating threshold helpers)
SENSOR_THRESHOLD_CONFIGS: Final[dict[str, dict[str, dict[str, any]]]] = {
    "temperature": {
        "min": {
            "min": -20,
            "max": 40,
            "step": 0.5,
            "unit": "°C",
            "default": 20,
        },
        "max": {
            "min": 0,
            "max": 60,
            "step": 0.5,
            "unit": "°C",
            "default": 30,
        },
    },
    "humidity": {
        "min": {
            "min": 0,
            "max": 60,
            "step": 1,
            "unit": "%",
            "default": 40,
        },
        "max": {
            "min": 40,
            "max": 100,
            "step": 1,
            "unit": "%",
            "default": 70,
        },
    },
    "pressure": {
        "min": {
            "min": 900,
            "max": 1000,
            "step": 1,
            "unit": "hPa",
            "default": 980,
        },
        "max": {
            "min": 1000,
            "max": 1100,
            "step": 1,
            "unit": "hPa",
            "default": 1030,
        },
    },
    "illuminance": {
        "min": {
            "min": 0,
            "max": 500,
            "step": 10,
            "unit": "lx",
            "default": 100,
        },
        "max": {
            "min": 100,
            "max": 10000,
            "step": 50,
            "unit": "lx",
            "default": 1000,
        },
    },
    "co2": {
        "min": {
            "min": 300,
            "max": 800,
            "step": 25,
            "unit": "ppm",
            "default": 400,
        },
        "max": {
            "min": 600,
            "max": 3000,
            "step": 50,
            "unit": "ppm",
            "default": 1000,
        },
    },
    "voc": {
        "min": {
            "min": 0,
            "max": 200,
            "step": 10,
            "unit": "μg/m³",
            "default": 50,
        },
        "max": {
            "min": 100,
            "max": 1000,
            "step": 25,
            "unit": "μg/m³",
            "default": 300,
        },
    },
    "pm25": {
        "min": {
            "min": 0,
            "max": 50,
            "step": 1,
            "unit": "μg/m³",
            "default": 25,
        },
        "max": {
            "min": 25,
            "max": 300,
            "step": 5,
            "unit": "μg/m³",
            "default": 75,
        },
    },
}
