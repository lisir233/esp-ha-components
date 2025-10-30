"""Device Configuration Parser for ESP-RainMaker Devices.

This module provides parsing functionality for ESP device configurations,
converting ESP-RainMaker device and service definitions into Home Assistant
entity specifications.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .esp_iot_spec import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_COLOR_TEMP,
    DEFAULT_DEBOUNCE_TIME,
    DEFAULT_HUE,
    DEFAULT_INTENSITY,
    DEFAULT_LIGHT_MODE,
    DEFAULT_REPORT_INTERVAL,
    DEFAULT_SATURATION,
    get_config_param_names,
    get_device_type_mapping,
    get_param_type_mapping,
    get_sensor_definition,
    get_threshold_patterns,
)

_LOGGER = logging.getLogger(__name__)
DOMAIN = "esp_ha"


class ESPDeviceParser:
    """Parse ESP-RainMaker device configuration and determine required entities.

    This parser converts ESP-RainMaker device configurations into Home Assistant
    entity specifications. It handles various device types including:
    - Lights (with brightness, color, temperature control)
    - Switches
    - Fans
    - Sensors (temperature, humidity, pressure, etc.)
    - Binary sensors
    - IMU gesture devices
    - Interactive input devices
    - Battery and energy monitoring devices
    - Low power and sleep management devices

    The parser maintains mappings between ESP-RainMaker device/parameter types
    and their corresponding Home Assistant platforms and entity types.
    """

    def __init__(self) -> None:
        """Initialize the ESP device parser.

        Sets up mappings between ESP-RainMaker device/parameter types
        and Home Assistant platforms and entity types.
        """
        self.device_type_mapping = get_device_type_mapping()
        self.param_type_mapping = get_param_type_mapping()

    def parse_device_config(
        self, config_data: bytes | str, preferred_name: str | None = None
    ) -> dict[str, Any]:
        """Parse ESP32 device configuration from protobuf response.

        Converts raw configuration data (bytes or string) into a structured
        dictionary containing device information and entity specifications
        for Home Assistant platforms.

        Args:
            config_data: Raw configuration data in bytes or string format.
                Expected to be JSON-formatted device configuration.
            preferred_name: Optional preferred device name (e.g., from config entry title).
                If provided, this will be used instead of the name from ESP32 config.

        Returns:
            Dictionary containing:
                - device_info: Device metadata (name, model, manufacturer, etc.)
                - platforms: Entity specifications grouped by platform type
                - entities: List of all entity specifications
            Returns empty dict if parsing fails.

        Raises:
            json.JSONDecodeError: If JSON parsing fails
            UnicodeDecodeError: If string decoding fails
        """
        if not config_data:
            _LOGGER.error("Empty configuration data received")
            return {}

        try:
            # Handle both bytes and string input
            config_str = (
                config_data.decode("utf-8", errors="replace")
                if isinstance(config_data, bytes)
                else str(config_data)
            )

            # Parse and validate JSON configuration
            device_config = json.loads(config_str)

            device_name = device_config.get("info", {}).get(
                "name"
            ) or device_config.get("node_id", "Unknown Device")

            _LOGGER.debug("Parsing device configuration: %s", device_name)

            return self._extract_entity_info(device_config, preferred_name)

        except json.JSONDecodeError as json_err:
            _LOGGER.error(
                "Failed to parse device configuration JSON: %s", str(json_err)
            )
            return {}

        except UnicodeDecodeError as decode_err:
            _LOGGER.error(
                "Failed to decode device configuration string: %s", str(decode_err)
            )
            return {}

        except Exception:
            _LOGGER.exception("Unexpected error processing device configuration")
            return {}

    def _extract_entity_info(
        self, config: dict[str, Any], preferred_name: str | None = None
    ) -> dict[str, Any]:
        """Extract entity information from device configuration.

        Processes the device configuration to extract device metadata
        and create entity specifications for all supported platforms.

        Args:
            config: Device configuration dictionary with 'info', 'devices',
                and 'services' sections.
            preferred_name: Optional preferred device name to use instead of
                the name from ESP32 config.

        Returns:
            Dictionary with device_info, platforms, and entities.
        """
        result: dict[str, Any] = {
            "device_info": self._extract_device_info(config, preferred_name),
            "platforms": {},  # Platform type -> entities
            "entities": [],  # All entities list
        }

        # Process devices section
        devices = config.get("devices", [])
        for device in devices:
            self._process_device(device, result)

        # Process services section
        services = config.get("services", [])
        for service in services:
            self._process_service(service, result)

        return result

    def _extract_device_info(
        self, config: dict[str, Any], preferred_name: str | None = None
    ) -> dict[str, Any]:
        """Extract device information with unified naming.

        Creates a standardized device info dictionary that ensures all
        entities for this device are grouped together in Home Assistant.

        Args:
            config: Device configuration dictionary.
            preferred_name: Optional preferred device name (e.g., from config entry title).
                If provided, this will override the name from ESP32 config.

        Returns:
            Device info dictionary with identifiers, name, manufacturer, etc.
        """
        info = config.get("info", {})
        node_id = config.get("node_id", "")

        # Use preferred name if provided, otherwise use ESP32's device name
        if preferred_name:
            device_name = preferred_name
            _LOGGER.debug(
                "Using preferred device name: %s (ESP32 name was: %s)",
                preferred_name,
                info.get("name", "Unknown"),
            )
        else:
            device_name = info.get(
                "name", f"ESP {node_id}" if node_id else "ESP Device"
            )

        # Create standardized device info to ensure all entities belong to same device
        return {
            "identifiers": {(DOMAIN, node_id)},
            "name": device_name,  # This is the main device name all entities will use
            "manufacturer": "Espressif",
            "model": info.get("model", "ESP32"),
            "hw_version": info.get("model", ""),
            "sw_version": info.get("fw_version", ""),
            "node_id": node_id,
            "type": info.get("type", ""),
            "platform": info.get("platform", ""),
            "project_name": info.get("project_name", ""),
        }

    def _process_device(self, device: dict[str, Any], result: dict[str, Any]) -> None:
        """Process a device and its parameters.

        Routes device processing to the appropriate handler based on
        the device type mapping.

        Args:
            device: Device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        device_type = device.get("type", "")
        ha_platform = self.device_type_mapping.get(device_type)

        if ha_platform == "light":
            self._process_light_device(device, result)
        elif ha_platform == "switch":
            self._process_switch_device(device, result)
        elif ha_platform == "fan":
            self._process_fan_device(device, result)
        elif ha_platform == "sensor":
            self._process_sensor_device(device, result)
        elif ha_platform == "binary_sensor":
            self._process_binary_sensor_device(device, result)
        elif ha_platform == "imu_gesture":
            self._process_imu_gesture_device(device, result)
        elif ha_platform == "interactive_input":
            self._process_interactive_input_device(device, result)
        elif ha_platform == "battery_energy":
            self._process_battery_energy_device(device, result)
        elif ha_platform == "low_power_sleep":
            self._process_low_power_sleep_device(device, result)

    def _process_light_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process light device and extract light parameters.

        Analyzes device parameters to determine light capabilities
        (power, brightness, color, etc.) and creates a light entity
        specification.

        Args:
            device: Light device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        main_device_name = result["device_info"]["name"]
        params = device.get("params", [])

        # Extract light capabilities
        light_capabilities: dict[str, bool | str] = {
            "power": False,
            "brightness": False,
            "hue": False,
            "saturation": False,
            "color_temp": False,
            "intensity": False,
            "light_mode": False,
            "name": main_device_name,
        }

        # Default parameter values using named constants
        light_values: dict[str, bool | int] = {
            "power": False,
            "brightness": DEFAULT_BRIGHTNESS,
            "hue": DEFAULT_HUE,
            "saturation": DEFAULT_SATURATION,
            "color_temp": DEFAULT_COLOR_TEMP,
            "intensity": DEFAULT_INTENSITY,
            "light_mode": DEFAULT_LIGHT_MODE,
        }

        for param in params:
            param_name = param.get("name", "")
            properties = param.get("properties", [])

            # Map ESP-RainMaker parameters to light capabilities
            if param_name.lower() == "power" and "write" in properties:
                light_capabilities["power"] = True
            elif param_name.lower() == "brightness" and "write" in properties:
                light_capabilities["brightness"] = True
            elif param_name.lower() == "hue" and "write" in properties:
                light_capabilities["hue"] = True
            elif param_name.lower() == "saturation" and "write" in properties:
                light_capabilities["saturation"] = True
            elif (
                param_name.lower() in ["cct", "color_temp", "color temperature"]
                and "write" in properties
            ):
                light_capabilities["color_temp"] = True
            elif param_name.lower() == "intensity" and "write" in properties:
                light_capabilities["intensity"] = True
            elif (
                param_name.lower() in ["light_mode", "light mode"]
                and "write" in properties
            ):
                light_capabilities["light_mode"] = True

        # Create light entity info
        entity_info = {
            "platform": "light",
            "device_name": main_device_name,
            "entity_type": "light",
            "capabilities": light_capabilities,
            "initial_values": light_values,
            "params": params,
        }

        if "light" not in result["platforms"]:
            result["platforms"]["light"] = []
        result["platforms"]["light"].append(entity_info)
        result["entities"].append(entity_info)

    def _process_switch_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process switch device.

        Creates a switch entity specification from the device configuration.

        Args:
            device: Switch device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        main_device_name = result["device_info"]["name"]

        entity_info: dict[str, Any] = {
            "platform": "switch",
            "device_name": main_device_name,
            "entity_type": "switch",
            "params": device.get("params", []),
        }

        if "switch" not in result["platforms"]:
            result["platforms"]["switch"] = []
        result["platforms"]["switch"].append(entity_info)
        result["entities"].append(entity_info)

    def _process_sensor_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process sensor device and create corresponding entities.

        Uses sensor definitions to standardize sensor configurations including
        units, device classes, icons, and naming templates. Only processes
        numeric sensors with read capability, filtering out configuration
        and threshold parameters.

        Args:
            device: Sensor device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        main_device_name = result["device_info"]["name"]
        params = device.get("params", [])

        # Get threshold patterns and config param names to filter
        threshold_patterns = get_threshold_patterns()
        config_params = get_config_param_names()

        for param in params:
            param_name = param.get("name", "").lower().replace(" ", "_")
            data_type = param.get("data_type", "")
            properties = param.get("properties", [])

            # Skip threshold parameters
            if any(pattern in param_name for pattern in threshold_patterns):
                continue

            # Skip configuration parameters (not sensors)
            if param_name in config_params:
                continue

            # Only process numeric sensors with read property
            if data_type not in ["int", "float"] or "read" not in properties:
                continue

            # Get sensor definition
            sensor_def = get_sensor_definition(param_name)
            if not sensor_def:
                # Skip unknown sensor types silently
                continue

            # Create entity info using sensor definition
            entity_info = {
                "platform": "sensor",
                "device_name": main_device_name,
                "entity_type": "sensor",
                "sensor_type": param_name.replace(" ", "_"),
                "sensor_name": sensor_def["name_template"].format(main_device_name),
                "unit_of_measurement": sensor_def["unit"],
                "device_class": sensor_def["device_class"],
                "icon": sensor_def["icon"],
                "param": param,
                "capabilities": {
                    "has_value_template": True,
                    "has_device_class": True,
                    "has_unit_of_measurement": True,
                },
            }

            # Add to platforms
            if "sensor" not in result["platforms"]:
                result["platforms"]["sensor"] = []
            result["platforms"]["sensor"].append(entity_info)
            result["entities"].append(entity_info)

            _LOGGER.info(
                "Found sensor: %s (%s, unit: %s, device class: %s)",
                entity_info["sensor_name"],
                param_name,
                sensor_def["unit"],
                sensor_def["device_class"],
            )

    def _process_fan_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process fan device.

        Creates a fan entity specification from the device configuration.

        Args:
            device: Fan device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        main_device_name = result["device_info"]["name"]

        entity_info: dict[str, Any] = {
            "platform": "fan",
            "device_name": main_device_name,
            "entity_type": "fan",
            "params": device.get("params", []),
        }

        if "fan" not in result["platforms"]:
            result["platforms"]["fan"] = []
        result["platforms"]["fan"].append(entity_info)
        result["entities"].append(entity_info)

    def _process_service(self, service: dict[str, Any], result: dict[str, Any]) -> None:
        """Process service and extract sensor parameters.

        Services can provide additional sensor data. Currently filters out
        Time service to avoid string value issues in Home Assistant.

        Args:
            service: Service configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        service_name = service.get("name", "")
        service_type = service.get("type", "")

        # Skip Time service string sensors as they cause issues with Home Assistant
        # Timezone information isn't very useful as sensor entities anyway
        if service_type in ["esp.service.time"]:
            _LOGGER.debug("Skipping Time service sensor to avoid string value issues")
            return

        # Only process services that provide numeric sensor data
        if service_type in []:  # Add other service types here if needed
            params = service.get("params", [])

            for param in params:
                param_name = param.get("name", "")
                data_type = param.get("data_type", "")

                if data_type in ["int", "float"] and "read" in param.get(
                    "properties", []
                ):
                    entity_info = {
                        "platform": "sensor",
                        "device_name": service_name,
                        "entity_type": "sensor",
                        "sensor_type": param_name.lower().replace(" ", "_"),
                        "param": param,
                    }

                    if "sensor" not in result["platforms"]:
                        result["platforms"]["sensor"] = []
                    result["platforms"]["sensor"].append(entity_info)
                    result["entities"].append(entity_info)

    def _process_binary_sensor_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process binary sensor device and extract parameters.

        Creates a binary sensor entity with configuration for state,
        device class, debounce time, and reporting interval.

        Args:
            device: Binary sensor device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        main_device_name = result["device_info"]["name"]
        params = device.get("params", [])

        # Extract binary sensor capabilities and config
        binary_sensor_config: dict[str, str] = {
            "name": main_device_name,
            "device_class": "door",  # Default to door sensor
            "state_topic": f"{DOMAIN}/{main_device_name}/state",
            "availability_topic": f"{DOMAIN}/{main_device_name}/available",
            "payload_on": "ON",
            "payload_off": "OFF",
        }

        # Extract current parameter values and configuration using constants
        binary_sensor_values: dict[str, bool | str | int] = {
            "state": False,  # Default state is off
            "device_class": "door",  # Default device class is door
            "debounce_time": DEFAULT_DEBOUNCE_TIME,
            "report_interval": DEFAULT_REPORT_INTERVAL,
        }

        # Process all parameters
        for param in params:
            param_type = param.get("type", "")
            param_value = param.get("value")

            if param_type == "esp.param.state":
                binary_sensor_values["state"] = (
                    bool(param_value) if param_value is not None else False
                )
            elif param_type == "esp.param.device_class":
                device_class = str(param_value).lower() if param_value else "door"
                binary_sensor_values["device_class"] = device_class
                binary_sensor_config["device_class"] = device_class
            elif param_type == "esp.param.debounce_time":
                binary_sensor_values["debounce_time"] = (
                    int(param_value)
                    if param_value is not None
                    else DEFAULT_DEBOUNCE_TIME
                )
            elif param_type == "esp.param.report_interval":
                binary_sensor_values["report_interval"] = (
                    int(param_value)
                    if param_value is not None
                    else DEFAULT_REPORT_INTERVAL
                )

        # Create binary sensor entity info with complete configuration
        entity_info: dict[str, Any] = {
            "platform": "binary_sensor",
            "device_name": main_device_name,
            "entity_type": "binary_sensor",
            "config": binary_sensor_config,
            "state": binary_sensor_values["state"],  # Current state
            "device_class": binary_sensor_values["device_class"],  # Device type
            "initial_values": binary_sensor_values,
            "params": params,
            "attributes": {  # Additional attributes
                "debounce_time": binary_sensor_values["debounce_time"],
                "report_interval": binary_sensor_values["report_interval"],
                "friendly_name": main_device_name,
            },
        }

        # Add to platforms
        if "binary_sensor" not in result["platforms"]:
            result["platforms"]["binary_sensor"] = []
        result["platforms"]["binary_sensor"].append(entity_info)
        result["entities"].append(entity_info)

    def _process_imu_gesture_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process IMU gesture device as a unified gesture controller.

        Creates a single gesture controller entity that handles all IMU
        gesture-related functionality including detection, orientation
        tracking, and sensitivity control.

        Args:
            device: IMU gesture device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        main_device_name = result["device_info"]["name"]
        params = device.get("params", [])

        # Create a single gesture controller entity info
        gesture_controller_info: dict[str, Any] = {
            "platform": "imu_gesture",
            "device_name": main_device_name,
            "entity_type": "imu_gesture_controller",
            "entity_name": "imu_gesture_controller",
            "params": params,
            "capabilities": {
                "power_control": True,
                "sensitivity_control": True,
                "gesture_detection": True,
                "orientation_tracking": True,
            },
        }

        # Add to platforms
        if "imu_gesture" not in result["platforms"]:
            result["platforms"]["imu_gesture"] = []
        result["platforms"]["imu_gesture"].append(gesture_controller_info)
        result["entities"].append(gesture_controller_info)

    def _process_interactive_input_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process Interactive Input device as a unified input controller.

        Creates a single interactive input controller entity that handles
        input type control, sensitivity, event detection, and mapping
        configuration.

        Args:
            device: Interactive input device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        # Use the main device name from device_info instead of creating separate device name
        main_device_name = result["device_info"]["name"]
        params = device.get("params", [])

        # Create a single interactive input controller entity info using main device name
        input_controller_info: dict[str, Any] = {
            "platform": "interactive_input",
            "device_name": main_device_name,
            "entity_type": "interactive_input_controller",
            "entity_name": "interactive_input_controller",
            "params": params,
            "capabilities": {
                "input_type_control": True,
                "sensitivity_control": True,
                "event_detection": True,
                "mapping_config": True,
            },
        }

        # Add to platforms
        if "interactive_input" not in result["platforms"]:
            result["platforms"]["interactive_input"] = []
        result["platforms"]["interactive_input"].append(input_controller_info)
        result["entities"].append(input_controller_info)

    def _process_battery_energy_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process Battery & Energy device as a unified battery controller.

        Separates battery status parameters (for sensor entities) from
        configuration parameters (for number entities) and creates
        appropriate entity specifications for each.

        Args:
            device: Battery & energy device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        # Use the main device name from device_info instead of creating separate device name
        main_device_name = result["device_info"]["name"]
        device_component_name = device.get("name", "Battery & Energy")
        params = device.get("params", [])

        # Separate battery status parameters and configuration parameters
        battery_status_params: list[dict[str, Any]] = []
        battery_config_params: list[dict[str, Any]] = []

        for param in params:
            param_type = param.get("type", "")

            # Battery status parameters (for sensor entities)
            if param_type in [
                "esp.param.battery_level",
                "esp.param.voltage",
                "esp.param.temperature",
                "esp.param.charging_status",
                "esp.param.alert_level",
                "esp.param.battery_health",
                "esp.param.charge_current",
                "esp.param.discharge_current",
            ]:
                battery_status_params.append(param)
            # Battery configuration parameters (for number entities)
            elif param_type in [
                "esp.param.battery_low_threshold",
                "esp.param.battery_sampling_interval",
            ]:
                battery_config_params.append(param)
                # Create number platform entity for configuration parameters
                self._process_battery_config_param(param, device, result)

        # Only create battery_energy platform entity for battery status parameters
        if battery_status_params:
            battery_controller_info: dict[str, Any] = {
                "platform": "battery_energy",
                "device_name": main_device_name,  # Use main device name to group under same device
                "component_name": device_component_name,  # Keep component name for reference
                "entity_type": "battery_energy_controller",
                "entity_name": "battery_energy_controller",
                "params": battery_status_params,
                "capabilities": {
                    "battery_monitoring": True,
                    "voltage_monitoring": True,
                    "temperature_monitoring": True,
                    "charging_status": True,
                    "low_battery_alert": True,
                    "power_management": True,
                },
            }

            if "battery_energy" not in result["platforms"]:
                result["platforms"]["battery_energy"] = []
            result["platforms"]["battery_energy"].append(battery_controller_info)
            result["entities"].append(battery_controller_info)

    def _process_battery_config_param(
        self, param: dict[str, Any], device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process battery configuration parameter as number entity.

        Creates a number entity for battery configuration parameters
        like low threshold and sampling interval.

        Args:
            param: Parameter configuration dictionary.
            device: Device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        param_name = param.get("name", "")
        param_type = param.get("type", "")

        # Create number platform entity information
        number_entity_info: dict[str, Any] = {
            "platform": "number",
            "device_name": result["device_info"]["name"],
            "entity_type": "battery_config",
            "entity_name": param_name.lower().replace(" ", "_"),
            "param_name": param_name,
            "param_type": param_type,
            "param_value": param.get("value", 0),
            "capabilities": {"configurable": True, "battery_management": True},
        }

        if "number" not in result["platforms"]:
            result["platforms"]["number"] = []
        result["platforms"]["number"].append(number_entity_info)
        result["entities"].append(number_entity_info)

    def _process_low_power_sleep_device(
        self, device: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Process Low Power & Sleep device as a unified power management controller.

        Creates a single low power & sleep controller entity that handles
        sleep management, wake reason detection, wake window management,
        and power mode control.

        Args:
            device: Low power & sleep device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        # Use the main device name from device_info instead of creating separate device name
        main_device_name = result["device_info"]["name"]
        device_component_name = device.get("name", "Low Power & Sleep")
        params = device.get("params", [])

        # Create a single low power & sleep controller entity info using main device name
        sleep_controller_info: dict[str, Any] = {
            "platform": "low_power_sleep",
            "device_name": main_device_name,  # Use main device name to group under same device
            "component_name": device_component_name,  # Keep component name for reference
            "entity_type": "low_power_sleep_controller",
            "entity_name": "low_power_sleep_controller",
            "params": params,
            "capabilities": {
                "sleep_management": True,
                "wake_reason_detection": True,
                "wake_window_management": True,
                "power_mode_control": True,
                "event_wake_handling": True,
                "command_queue_management": True,
            },
        }

        if "low_power_sleep" not in result["platforms"]:
            result["platforms"]["low_power_sleep"] = []
        result["platforms"]["low_power_sleep"].append(sleep_controller_info)
        result["entities"].append(sleep_controller_info)
