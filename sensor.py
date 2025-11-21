"""ESP HA Sensor platform.

This module provides sensor entities for ESP HA devices, including:
- Temperature, humidity, pressure sensors
- Air quality sensors (CO2, VOC, PM2.5)
- Illuminance sensors
- IMU gesture sensors
- Threshold monitoring and alerting
- Historical data tracking and statistics
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .battery_energy import ESPHomeBatteryEnergy
from .const import CONF_NODE_ID, DOMAIN, get_device_info
from .esp_iot import (
    DEFAULT_HISTORY_MINUTES,
    DEFAULT_MAX_HISTORY_ITEMS,
    DEFAULT_STATISTICS_MINUTES,
    MAX_HISTORY_LIMIT,
    MAX_LATEST_VALUES_DURATION,
    SENSOR_DISPLAY_NAMES,
    SENSOR_THRESHOLD_CONFIGS,
    SENSOR_TYPE_TO_NUMBER_MAPPING,
    SENSOR_UNIT_DISPLAY,
    THRESHOLD_PATTERNS,
    TREND_CHANGE_THRESHOLD,
    build_sensor_number_entity_id,
    build_sensor_pinyin_entity_id,
    build_threshold_alert_message,
    build_threshold_clear_message,
    calculate_sensor_trend,
    get_sensor_display_name,
    get_sensor_mapped_type,
    get_sensor_statistics,
    get_sensor_threshold_config,
    get_sensor_threshold_icon,
    get_sensor_unit_display,
    is_threshold_sensor,
    parse_threshold_param_name,
)
from .imu_gesture import ESPHomeIMUGesture
from .interactive_input import ESPHomeInteractiveInput
from .low_power_sleep import ESPHomeLowPowerSleep

_LOGGER = logging.getLogger(__name__)



async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up sensor platform from config entry.

    This function initializes the sensor platform, registers event listeners for:
    - Sensor discovery events
    - IMU gesture updates
    - Threshold updates from number entities
    - ESP32 threshold reports

    Args:
        hass: Home Assistant instance.
        config_entry: Configuration entry for this integration.
        async_add_entities: Callback to add new entities to Home Assistant.

    Returns:
        True if setup was successful, False otherwise.
    """
    api = config_entry.runtime_data
    if not api:
        return None

    node_id = config_entry.data.get(CONF_NODE_ID)
    if not node_id:
        return None

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    entities = []

    cache_key = f"discovered_sensors_{node_id}"
    if cache_key not in hass.data[DOMAIN]:
        hass.data[DOMAIN][cache_key] = {}

    discovered_entities = hass.data[DOMAIN][cache_key]

    entry_key = f"{node_id}_{config_entry.entry_id}"
    hass.data[DOMAIN][f"add_entities_{entry_key}"] = async_add_entities

    # Store references for event-driven entity creation
    # Entities will be created when ESP32 reports them via discovery events
    hass.data[DOMAIN][f"entities_{entry_key}"] = entities
    
    # Event-driven entity creation handlers
    @callback
    def handle_battery_energy_discovered(event):
        """Handle battery energy discovery event from ESP32"""
        try:
            event_node_id = event.data.get("node_id", "").replace(":", "").lower()
            if event_node_id != node_id.lower():
                return
            
            # Check if already created
            if any(isinstance(e, ESPHomeBatteryEnergy) for e in entities):
                _LOGGER.debug("Battery energy entity already exists for %s", node_id)
                return
            
            battery_entity = ESPHomeBatteryEnergy(
                hass=hass,
                node_id=node_id,
                device_name=config_entry.title or f"ESP-{node_id}",
            )
            entities.append(battery_entity)
            async_add_entities([battery_entity])
            _LOGGER.info("Added battery energy entity for device %s (via discovery)", node_id)
        except Exception as err:
            _LOGGER.error("Failed to create battery energy entity: %s", err)
    
    @callback
    def handle_imu_gesture_discovered(event):
        """Handle IMU gesture discovery event from ESP32"""
        try:
            event_node_id = event.data.get("node_id", "").replace(":", "").lower()
            if event_node_id != node_id.lower():
                return
            
            # Check if already created
            if any(isinstance(e, ESPHomeIMUGesture) for e in entities):
                _LOGGER.debug("IMU gesture entity already exists for %s", node_id)
                return
            
            gesture_entity = ESPHomeIMUGesture(
                hass=hass,
                api=api,
                node_id=node_id,
                device_name=config_entry.title or f"ESP-{node_id}",
            )
            entities.append(gesture_entity)
            async_add_entities([gesture_entity])
            _LOGGER.info("Added IMU gesture entity for device %s (via discovery)", node_id)
        except Exception as err:
            _LOGGER.error("Failed to create IMU gesture entity: %s", err)
    
    @callback
    def handle_interactive_input_discovered(event):
        """Handle interactive input discovery event from ESP32"""
        try:
            event_node_id = event.data.get("node_id", "").replace(":", "").lower()
            if event_node_id != node_id.lower():
                return
            
            # Check if already created
            if any(isinstance(e, ESPHomeInteractiveInput) for e in entities):
                _LOGGER.debug("Interactive input entity already exists for %s", node_id)
                return
            
            input_entity = ESPHomeInteractiveInput(
                hass=hass,
                node_id=node_id,
                device_name=config_entry.title or f"ESP-{node_id}",
            )
            entities.append(input_entity)
            async_add_entities([input_entity])
            _LOGGER.info("Added interactive input entity for device %s (via discovery)", node_id)
        except Exception as err:
            _LOGGER.error("Failed to create interactive input entity: %s", err)
    
    @callback
    def handle_low_power_sleep_discovered(event):
        """Handle low power sleep discovery event from ESP32"""
        try:
            event_node_id = event.data.get("node_id", "").replace(":", "").lower()
            if event_node_id != node_id.lower():
                return
            
            # Check if already created
            if any(isinstance(e, ESPHomeLowPowerSleep) for e in entities):
                _LOGGER.debug("Low power sleep entity already exists for %s", node_id)
                return
            
            sleep_entity = ESPHomeLowPowerSleep(
                hass=hass,
                node_id=node_id,
                device_name=config_entry.title or f"ESP-{node_id}",
            )
            entities.append(sleep_entity)
            async_add_entities([sleep_entity])
            _LOGGER.info("Added low power sleep entity for device %s (via discovery)", node_id)
        except Exception as err:
            _LOGGER.error("Failed to create low power sleep entity: %s", err)
    
    # Register discovery event listeners
    hass.bus.async_listen(f"{DOMAIN}_battery_energy_discovered", handle_battery_energy_discovered)
    hass.bus.async_listen(f"{DOMAIN}_imu_gesture_discovered", handle_imu_gesture_discovered)
    hass.bus.async_listen(f"{DOMAIN}_interactive_input_discovered", handle_interactive_input_discovered)
    hass.bus.async_listen(f"{DOMAIN}_low_power_sleep_discovered", handle_low_power_sleep_discovered)

    if config_entry.data.get("device_type") == "sensor":
        async_add_entities(entities)
        return None

    # Listen for threshold reports from ESP32 and trigger number platform to create threshold entities
    async def handle_esp32_threshold_report_for_numbers(event):
        """Handle ESP32 threshold report events and trigger number platform entity creation.

        Args:
            event: Event containing threshold data from ESP32 device.
        """
        try:
            event_node_id = event.data.get("node_id", "").replace(":", "").lower()
            threshold_data = event.data.get("threshold_data", {})

            # Key filter: only process devices managed by current config entry
            if event_node_id != node_id.lower():
                return

            # Parse threshold data and trigger number platform discovery for each sensor type
            sensor_types_found = set()
            for param_name, value in threshold_data.items():
                try:
                    # Parse parameter name format: sensor_type_min_threshold or sensor_type_max_threshold
                    if param_name.endswith("_min_threshold"):
                        sensor_type = param_name.replace("_min_threshold", "")
                        sensor_types_found.add(sensor_type)
                    elif param_name.endswith("_max_threshold"):
                        sensor_type = param_name.replace("_max_threshold", "")
                        sensor_types_found.add(sensor_type)
                except Exception as err:
                    __LOGGER.error("Failed to parse threshold parameter name: %s", err)

            # Send number platform discovery events for each discovered sensor type
            for sensor_type in sensor_types_found:
                # Send event to number platform to create threshold number entities
                hass.bus.async_fire(
                    f"{DOMAIN}_sensor_discovered",
                    {
                        "node_id": node_id,
                        "sensor_type": sensor_type,
                        "sensor_name": f"{sensor_type.replace('_', ' ').title()} Sensor",
                        "device_info": {"node_id": node_id, "name": f"ESP-{node_id}"},
                        "threshold_data": {
                            k: v for k, v in threshold_data.items() if sensor_type in k
                        },
                        "source": "esp32_threshold_report",
                    },
                )

                # Send threshold data event if available
                if (
                    threshold_config["min_threshold"] is not None
                    or threshold_config["max_threshold"] is not None
                ):
                    hass.bus.async_fire(
                        f"{DOMAIN}_threshold_data_received",
                        {
                            "node_id": node_id,
                            "threshold_data": {
                                f"{sensor_type}_min_threshold": threshold_config[
                                    "min_threshold"
                                ],
                                f"{sensor_type}_max_threshold": threshold_config[
                                    "max_threshold"
                                ],
                            },
                            "timestamp": datetime.datetime.now().isoformat(),
                        },
                    )
        except Exception as err:
            _LOGGER.error(
                "Failed to handle ESP32 threshold report event: %s",
                str(err),
                exc_info=True,
            )

    # Register ESP32 threshold report event listener
    hass.bus.async_listen(
        f"{DOMAIN}_esp32_threshold_report", handle_esp32_threshold_report_for_numbers
    )

    # Listen for sensor discovery events
    async def handle_sensor_discovered(event):
        """Handle sensor discovery events"""
        try:
            # Add debug logging
            _LOGGER.debug("Received sensor discovery event: %s", event.event_type)
            _LOGGER.debug("Event data: %s", event.data)

            event_node_id = event.data.get("node_id", "").replace(":", "").lower()

            # Key filter: only process devices managed by current config entry
            if event_node_id != node_id.lower():
                return

            sensor_name = event.data.get("sensor_name", "") or event.data.get(
                "device_name", ""
            )  # Compatible with both field names
            sensor_type = event.data.get("sensor_type", "")
            param = event.data.get("param", {})
            device_info = event.data.get("device_info", {})
            current_value = event.data.get("current_value")
            unit_of_measurement = event.data.get("unit_of_measurement", "")
            device_class = event.data.get("device_class")
            device_ip = event.data.get("device_ip", "")  # Get device IP address

            # Filter out threshold entities - these should not be displayed as sensor entities
            if is_threshold_sensor(sensor_type):
                return

            # Generate unique entity key

            entity_key = f"{node_id}_{sensor_type}_discovered"

            # Debug: show information for all current config entries
            existing_entries = hass.config_entries.async_entries(DOMAIN)

            # Simplified check: only check if this sensor type has been processed in current session
            # Avoid duplicate creation, but allow rediscovery after HA restart
            if entity_key not in discovered_entities:
                # Map sensor types to device class and state class (fallback mapping)
                sensor_mapping = {
                    "ambient_temperature": (
                        SensorDeviceClass.TEMPERATURE,
                        SensorStateClass.MEASUREMENT,
                        UnitOfTemperature.CELSIUS,
                    ),
                    "temperature": (
                        SensorDeviceClass.TEMPERATURE,
                        SensorStateClass.MEASUREMENT,
                        UnitOfTemperature.CELSIUS,
                    ),
                    "ambient_humidity": (
                        SensorDeviceClass.HUMIDITY,
                        SensorStateClass.MEASUREMENT,
                        PERCENTAGE,
                    ),
                    "humidity": (
                        SensorDeviceClass.HUMIDITY,
                        SensorStateClass.MEASUREMENT,
                        PERCENTAGE,
                    ),
                    "pressure": (
                        SensorDeviceClass.PRESSURE,
                        SensorStateClass.MEASUREMENT,
                        UnitOfPressure.HPA,
                    ),
                    "illuminance": (
                        SensorDeviceClass.ILLUMINANCE,
                        SensorStateClass.MEASUREMENT,
                        LIGHT_LUX,
                    ),
                    "co2": (
                        "carbon_dioxide",
                        SensorStateClass.MEASUREMENT,
                        CONCENTRATION_PARTS_PER_MILLION,
                    ),
                    "voc": (
                        "volatile_organic_compounds",
                        SensorStateClass.MEASUREMENT,
                        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
                    ),
                    "pm25": (
                        "pm25",
                        SensorStateClass.MEASUREMENT,
                        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
                    ),
                    # Removed timezone sensors as they cause string value errors
                }

                # Use device_class from discovery event, fallback to mapping
                final_device_class = (
                    device_class
                    or sensor_mapping.get(sensor_type, (None, None, None))[0]
                )
                final_state_class = sensor_mapping.get(
                    sensor_type, (None, SensorStateClass.MEASUREMENT, None)
                )[1]

                # Force correct unit mapping, ignore potentially incorrect units from ESP32
                # Especially pressure must use hPa, temperature must use °C
                correct_unit_mapping = {
                    "pressure": "hPa",  # Force pressure to use hPa
                    "temperature": "°C",  # Force temperature to use °C
                    "ambient_temperature": "°C",  # Force ambient temperature to use °C
                    "humidity": "%",
                    "ambient_humidity": "%",
                    "illuminance": "lx",
                    "co2": "ppm",
                    "voc": "μg/m³",
                    "pm25": "μg/m³",
                }

                # Prioritize correct unit mapping to ensure unit consistency
                final_unit = (
                    correct_unit_mapping.get(sensor_type)
                    or sensor_mapping.get(sensor_type, (None, None, None))[2]
                    or unit_of_measurement
                    or ""
                )

                # Get device name from device_info (preferred) or event data or use default format
                device_name = (
                    device_info.get("name")  # Prefer from device_info
                    or event.data.get("device_name")  # Fallback to event data
                    or f"ESP-{node_id}"  # Last resort: default format
                )
                
                # Create discovered sensor entity - fix: pass all required parameters
                discovered_entity = ESPHomeSensor(
                    hass=hass,
                    api=api,  # API instance
                    node_id=str(event.data.get("node_id", "")).replace(":", "").lower(),
                    device_name=device_name,
                    sensor_type=sensor_type,
                    name=sensor_name,
                    unit=final_unit or "",
                    device_class=final_device_class,
                    state_class=final_state_class,
                    is_discovery=True,
                    param_info=param,  # Pass parameter information
                    current_value=current_value,  # Pass current value
                )

                # Do not manually set entity_id, let HA auto-generate
                # discovered_entity.entity_id = f"sensor.{DOMAIN}_{mac}_discovered_{sensor_type}"

                # Find the config entry that manages this device
                target_config_entry = None

                # Method 1: Check shared API device registration
                shared_api = hass.data.get(DOMAIN, {}).get("shared_api")
                if shared_api:
                    managing_config_entry_id = shared_api._device_config_entries.get(
                        node_id
                    )
                    if managing_config_entry_id:
                        # Find the actual config entry object
                        for entry in hass.config_entries.async_entries(DOMAIN):
                            if entry.entry_id == managing_config_entry_id:
                                target_config_entry = entry
                                break

                # Method 2: Fallback - find config entry by node_id match
                if not target_config_entry:
                    for entry in hass.config_entries.async_entries(DOMAIN):
                        entry_node_id = entry.data.get(CONF_NODE_ID) or entry.data.get(
                            "node_id"
                        )
                        if entry_node_id and entry_node_id.lower() == node_id.lower():
                            target_config_entry = entry
                            break

                if not target_config_entry:
                    _LOGGER.warning("No config entry found for device %s", node_id)

                if target_config_entry:
                    # Get the correct add_entities function for this device and config entry
                    entry_data = hass.data.get(DOMAIN, {})
                    entry_key_for_func = f"{node_id}_{target_config_entry.entry_id}"
                    add_entities_func = entry_data.get(
                        f"add_entities_{entry_key_for_func}"
                    )

                    if add_entities_func:
                        # Only mark as discovered after successfully adding entity
                        add_entities_func([discovered_entity])
                        discovered_entities[entity_key] = (
                            discovered_entity  # Mark as successfully added
                        )
                    else:
                        _LOGGER.warning(
                            "No add_entities function found for device %s, config entry %s",
                            node_id,
                            target_config_entry.entry_id[-8:],
                        )
                        # Don't mark as discovered since it wasn't added
                else:
                    _LOGGER.warning(
                        "No config entry found for device %s, skipping entity creation to avoid duplicates",
                        node_id,
                    )
                    # Don't mark as discovered since it wasn't added

                # Send event to notify number platform to create threshold number entities
                hass.bus.async_fire(
                    f"{DOMAIN}_sensor_discovered",
                    {
                        "node_id": node_id,
                        "sensor_type": sensor_type,
                        "sensor_name": sensor_name,
                        "device_info": device_info,
                        "current_value": current_value,
                        "unit_of_measurement": final_unit,
                        "device_class": final_device_class,
                    },
                )

                # Thresholds are managed by number platform
                # Send event to notify number platform to create threshold number entities
                hass.bus.async_fire(
                    f"{DOMAIN}_sensor_threshold_needed",
                    {
                        "node_id": node_id,
                        "sensor_type": sensor_type,
                        "sensor_name": sensor_name,
                        "entity_id": f"sensor.{DOMAIN}_{node_id}_discovered_{sensor_type}",
                        "current_value": current_value,
                        "unit": final_unit,
                        "device_class": final_device_class,
                    },
                )

                # Send sensor discovery confirmation event
                hass.bus.async_fire(
                    f"{DOMAIN}_sensor_discovered_confirmed",
                    {
                        "entity_id": f"sensor.{DOMAIN}_{node_id}_discovered_{sensor_type}",
                        "sensor_type": sensor_type,
                        "sensor_name": sensor_name,
                        "node_id": node_id,
                        "current_value": current_value,
                        "unit": final_unit,
                        "device_class": final_device_class,
                        "discovery_timestamp": datetime.datetime.now().isoformat(),
                    },
                )

        except Exception as err:
            _LOGGER.error(
                "Failed to handle sensor discovery event: %s", str(err), exc_info=True
            )

    # Register event listeners
    hass.bus.async_listen(f"{DOMAIN}_sensor_discovered", handle_sensor_discovered)

    # Register IMU gesture sensor discovery event listener
    hass.bus.async_listen(
        f"{DOMAIN}_sensor_imu_gesture_discovered", handle_sensor_discovered
    )

    # Listen for IMU gesture data update events
    async def handle_imu_gesture_update(event):
        """Handle IMU gesture data update events - optimized to prevent duplicate processing"""
        try:
            event_node_id = event.data.get("node_id", "").replace(":", "").lower()
            event_type = event.event_type

            # Key filter: only process devices managed by current config entry
            if event_node_id != node_id.lower():
                return

            # Only process main controller update events, avoid duplicate processing
            if (
                event_type == f"{DOMAIN}_imu_gesture_controller_update"
                and "param_data" in event.data
            ):
                param_data = event.data.get("param_data", {})

                # Find corresponding IMU gesture entity
                discovered_entities = hass.data[DOMAIN].get("discovered_sensors", {})
                entity_key = f"{event_node_id}_imu_gesture_sensor"

                if entity_key in discovered_entities:
                    # Get actual entity object from discovered_entities
                    entity_data = discovered_entities[entity_key]
                    if isinstance(entity_data, dict) and "entity" in entity_data:
                        imu_entity = entity_data["entity"]
                    else:
                        # Compatibility handling: if entity object is stored directly
                        imu_entity = entity_data

                    # Handle gesture trigger (highest priority, only process valid gestures)
                    gesture_type = param_data.get("gesture_type", "")
                    if gesture_type and gesture_type not in ["none", "", "stationary"]:
                        await imu_entity.async_gesture_triggered(
                            gesture_type,
                            {
                                "confidence": param_data.get("gesture_confidence", 0),
                                "source": "controller_update_optimized",
                            },
                        )

                    # Update other sensor data (batch update, exclude gesture_type to avoid duplicate processing)
                    other_params = {
                        k: v for k, v in param_data.items() if k != "gesture_type"
                    }
                    if other_params:
                        for sensor_type, value in other_params.items():
                            await imu_entity.async_update_sensor_data(
                                sensor_type, value
                            )
                        _LOGGER.debug(
                            "IMU other data updated: %s", list(other_params.keys())
                        )

                else:
                    _LOGGER.warning("IMU gesture entity not found: %s", entity_key)
            else:
                # Skip other types of IMU events to avoid duplicate processing
                _LOGGER.debug(
                    "Skipped duplicate or non-primary IMU event: %s", event_type
                )
                return

        except Exception as err:
            _LOGGER.error(
                "Failed to handle IMU gesture update event: %s", str(err), exc_info=True
            )

    # Register IMU gesture data update event listener - only listen to main events, avoid duplicates
    hass.bus.async_listen(
        f"{DOMAIN}_imu_gesture_controller_update", handle_imu_gesture_update
    )

    # Listen for threshold update events from number platform and send to ESP32
    async def handle_number_threshold_update(event):
        """Handle threshold update events from number platform and send to ESP32"""
        try:
            event_node_id = event.data.get("node_id", "").replace(":", "").lower()

            # Key filter: only process devices managed by current config entry
            if event_node_id != node_id.lower():
                return
            param_name = event.data.get("param_name", "")
            value = event.data.get("value")
            sensor_type = event.data.get("sensor_type", "")
            threshold_type = event.data.get("threshold_type", "")
            source = event.data.get("source", "")
            entity_id = event.data.get("entity_id", "")

            if source != "number_entity":
                _LOGGER.debug("Skip updates not from number_entity source: %s", source)
                return  # Only process updates from number entities

            # Find corresponding API instance and send to ESP32
            # Use the shared API instance
            api_instance = hass.data.get(DOMAIN, {}).get("shared_api")
            if not api_instance:
                _LOGGER.warning("Shared API instance not found")
                return

            if api_instance:
                # Use same successful communication method as Helper: set_local_ctrl_property

                try:
                    # Use same API method as helpers to ensure successful communication
                    success = await api_instance.set_local_ctrl_property(
                        node_id, param_name, value
                    )

                    if success:
                        # Successfully sent

                        # Update threshold attributes of corresponding sensor entity
                        await _update_sensor_threshold_attributes(
                            node_id, sensor_type, threshold_type, value
                        )
                    else:
                        _LOGGER.error(
                            "Failed to send threshold to ESP32: device=%s, param=%s, API returned failure",
                            node_id,
                            param_name,
                        )

                except Exception as api_error:
                    _LOGGER.error(
                        "API call exception: device=%s, param=%s, error=%s",
                        node_id,
                        param_name,
                        api_error,
                    )
                    _LOGGER.error(
                        "Failed to send threshold to ESP32: device=%s, param=%s",
                        node_id,
                        param_name,
                    )
            else:
                _LOGGER.warning(
                    "API instance not found for device %s, cannot send threshold",
                    node_id,
                )

        except Exception as err:
            _LOGGER.error(
                "Failed to handle number platform threshold update: %s",
                str(err),
                exc_info=True,
            )

    # Register number platform threshold update event listener
    hass.bus.async_listen(
        f"{DOMAIN}_threshold_update_to_esp32", handle_number_threshold_update
    )

    # Helper function: update threshold attributes of sensor entity
    async def _update_sensor_threshold_attributes(
        node_id: str, sensor_type: str, threshold_type: str, value: float
    ):
        """Update threshold attributes of sensor entity"""
        try:
            # Find corresponding sensor entity
            discovered_entities = hass.data[DOMAIN].get("discovered_sensors", {})
            sensor_entity_key = f"{node_id}_{sensor_type}_discovered"

            if sensor_entity_key in discovered_entities:
                sensor_entity = discovered_entities[sensor_entity_key]

                # Update threshold attributes of sensor entity (trigger state update)
                sensor_entity.async_write_ha_state()

                _LOGGER.debug(
                    "Updated sensor threshold attributes: %s %s = %s",
                    sensor_type,
                    threshold_type,
                    value,
                )
            else:
                _LOGGER.debug(
                    "Corresponding sensor entity not found: %s", sensor_entity_key
                )

        except Exception as err:
            _LOGGER.error("Failed to update sensor threshold attributes: %s", err)

    # Helper system removed, now only using Number entity system

    # Register services only once globally
    if not hass.services.has_service(DOMAIN, "get_sensor_history"):

        async def get_sensor_history(call):
            """Service: Get sensor short-term historical data"""
            try:
                entity_id = call.data.get("entity_id")
                duration_minutes = call.data.get("duration_minutes", 5)

                if not entity_id:
                    _LOGGER.error("Get history service missing entity_id parameter")
                    return {"error": "Missing entity_id"}

                # Get data from local sensor entity
                all_discovered = hass.data[DOMAIN].get("discovered_sensors", {})
                for entity_key, entity in all_discovered.items():
                    if entity.entity_id == entity_id:
                        history = entity.get_recent_history(duration_minutes)
                        statistics = entity.get_statistics(duration_minutes)

                        result = {
                            "entity_id": entity_id,
                            "duration_minutes": duration_minutes,
                            "history": history,
                            "count": len(history),
                            "statistics": statistics,
                        }

                        # Send historical data event
                        hass.bus.async_fire(f"{DOMAIN}_sensor_history_response", result)

                        _LOGGER.info(
                            "Returning sensor historical data: %s, %d records(%d minutes)",
                            entity_id,
                            len(history),
                            duration_minutes,
                        )
                        return result

                _LOGGER.warning("Sensor entity not found: %s", entity_id)
                return {"error": f"Entity not found: {entity_id}"}

            except Exception as err:
                _LOGGER.error(
                    "Failed to get sensor historical data: %s", str(err), exc_info=True
                )
                return {"error": str(err)}

        async def clear_sensor_history(call):
            """Service: Clear sensor history cache"""
            try:
                entity_id = call.data.get("entity_id")

                if entity_id:
                    # Clear history for specified sensor
                    all_discovered = hass.data[DOMAIN].get("discovered_sensors", {})
                    for entity_key, entity in all_discovered.items():
                        if entity.entity_id == entity_id:
                            entity.clear_history()
                            _LOGGER.info("Sensor history cache cleared: %s", entity_id)
                            return {"status": "success", "entity_id": entity_id}
                    _LOGGER.warning("Sensor entity not found: %s", entity_id)
                    return {"status": "not_found", "entity_id": entity_id}
                # Clear history for all sensors
                count = 0
                all_discovered = hass.data[DOMAIN].get("discovered_sensors", {})
                for entity in all_discovered.values():
                    entity.clear_history()
                    count += 1
                _LOGGER.info(
                    "Cleared all sensor history cache, total %d sensors", count
                )
                return {"status": "success", "cleared_count": count}

            except Exception as err:
                _LOGGER.error(
                    "Failed to clear sensor history cache: %s", str(err), exc_info=True
                )
                return {"status": "error", "message": str(err)}

        async def set_sensor_threshold(call):
            """Service: Set sensor threshold alarm - now managed through Number entities"""
            try:
                entity_id = call.data.get("entity_id")
                min_threshold = call.data.get("min_threshold")
                max_threshold = call.data.get("max_threshold")
                alarm_enabled = call.data.get("alarm_enabled", True)

                if not entity_id:
                    _LOGGER.error("Set threshold service missing entity_id parameter")
                    return {"status": "error", "message": "Missing entity_id"}

                # Extract sensor type from entity_id (e.g., sensor.esp32_temperature)
                if "esp32_" in entity_id:
                    sensor_type = entity_id.split("esp32_")[-1]
                elif f"{DOMAIN}_" in entity_id and (
                    "_ambient_temperature" in entity_id
                    or "_ambient_humidity" in entity_id
                    or "_illuminance" in entity_id
                    or "_pressure" in entity_id
                    or "_co2" in entity_id
                    or "_voc" in entity_id
                    or "_pm25" in entity_id
                ):
                    # Handle new discovery format: sensor.{DOMAIN}_686725e839cc_discovered_ambient_temperature
                    if "_ambient_temperature" in entity_id:
                        sensor_type = "temperature"
                    elif "_ambient_humidity" in entity_id:
                        sensor_type = "humidity"
                    elif "_illuminance" in entity_id:
                        sensor_type = "illuminance"
                    elif "_pressure" in entity_id:
                        sensor_type = "pressure"
                    elif "_co2" in entity_id:
                        sensor_type = "co2"
                    elif "_voc" in entity_id:
                        sensor_type = "voc"
                    elif "_pm25" in entity_id:
                        sensor_type = "pm25"

                    # Update corresponding Number entities
                    updates = []
                    # Extract node_id from entity_id
                    node_id = (
                        entity_id.split("_")[2]
                        if "_discovered_" in entity_id
                        else "39cc"
                    )

                    if min_threshold is not None:
                        min_number_id = (
                            f"number.{DOMAIN}_{node_id}_{sensor_type}_min_threshold"
                        )
                        try:
                            await hass.services.async_call(
                                "number",
                                "set_value",
                                {"entity_id": min_number_id, "value": min_threshold},
                            )
                            updates.append(f"min={min_threshold}")
                        except Exception as err:
                            _LOGGER.warning(
                                "Failed to update minimum threshold Number entity: %s",
                                err,
                            )

                    if max_threshold is not None:
                        max_number_id = (
                            f"number.{DOMAIN}_{node_id}_{sensor_type}_max_threshold"
                        )
                        try:
                            await hass.services.async_call(
                                "number",
                                "set_value",
                                {"entity_id": max_number_id, "value": max_threshold},
                            )
                            updates.append(f"max={max_threshold}")
                        except Exception as err:
                            _LOGGER.warning(
                                "Failed to update maximum threshold Number entity: %s",
                                err,
                            )

                    config = {
                        "entity_id": entity_id,
                        "sensor_type": sensor_type,
                        "updates": updates,
                        "status": "success",
                    }

                    # Send configuration confirmation event
                    hass.bus.async_fire(
                        f"{DOMAIN}_threshold_config_updated",
                        {"entity_id": entity_id, "config": config},
                    )

                    return config
                else:
                    _LOGGER.error("Unsupported entity_id format: %s", entity_id)
                    return {
                        "status": "error",
                        "message": "Unsupported entity_id format",
                    }

            except Exception as err:
                _LOGGER.error(
                    "Failed to set sensor threshold: %s", str(err), exc_info=True
                )
                return {"status": "error", "message": str(err)}

        async def get_sensor_threshold(call):
            """Service: Get sensor threshold configuration."""
            try:
                entity_id = call.data.get("entity_id")
                if not entity_id:
                    _LOGGER.error("Missing entity_id parameter")
                    return {"status": "error", "message": "Missing entity_id"}

                # Extract sensor type and node_id from entity_id
                if "esp32_" in entity_id:
                    node_id = entity_id.split("_")[2]
                    sensor_type = entity_id.split("esp32_")[-1]
                elif f"{DOMAIN}_" in entity_id:
                    parts = entity_id.split("_")
                    node_id = parts[2]

                    # Map sensor types
                    type_mapping = {
                        "ambient_temperature": "temperature",
                        "ambient_humidity": "humidity",
                        "illuminance": "illuminance",
                        "pressure": "pressure",
                        "co2": "co2",
                        "voc": "voc",
                        "pm25": "pm25",
                    }

                    for key, value in type_mapping.items():
                        if f"_{key}" in entity_id:
                            sensor_type = value
                            break
                    else:
                        _LOGGER.error("Unknown sensor type in entity_id: %s", entity_id)
                        return {"status": "error", "message": "Unknown sensor type"}
                else:
                    _LOGGER.error("Invalid entity_id format: %s", entity_id)
                    return {"status": "error", "message": "Invalid entity_id format"}

                # Get thresholds from Number entities
                # Get min threshold
                min_number_id = f"number.{DOMAIN}_{node_id}_{sensor_type}_min_threshold"
                min_state = hass.states.get(min_number_id)
                min_threshold = float(min_state.state) if min_state else None

                # Get max threshold
                max_number_id = f"number.{DOMAIN}_{node_id}_{sensor_type}_max_threshold"
                max_state = hass.states.get(max_number_id)
                max_threshold = float(max_state.state) if max_state else None

                threshold_config = {
                    "min_threshold": min_threshold,
                    "max_threshold": max_threshold,
                    "alarm_enabled": True,  # Always enabled for compatibility
                }
                threshold_config["alarm_enabled"] = True

                # Get current sensor value
                sensor_state = hass.states.get(entity_id)
                current_value = sensor_state.state if sensor_state else None

                config = {
                    "entity_id": entity_id,
                    "sensor_type": sensor_type,
                    "threshold_config": threshold_config,
                    "current_value": current_value,
                    "number_entities": {"min": min_number_id, "max": max_number_id},
                    "status": "success",
                }

                # Send threshold configuration event
                hass.bus.async_fire(f"{DOMAIN}_threshold_config_response", config)

                _LOGGER.info(
                    "Returning sensor threshold configuration (read from Number entities): %s",
                    entity_id,
                )
                return config

            except Exception as err:
                _LOGGER.error(
                    "Failed to get sensor threshold configuration: %s",
                    str(err),
                    exc_info=True,
                )
                return {"status": "error", "message": str(err)}

        # Register services (simplified version, no schema validation)
        hass.services.async_register(DOMAIN, "get_sensor_history", get_sensor_history)
        hass.services.async_register(
            DOMAIN, "clear_sensor_history", clear_sensor_history
        )
        hass.services.async_register(
            DOMAIN, "set_sensor_threshold", set_sensor_threshold
        )
        hass.services.async_register(
            DOMAIN, "get_sensor_threshold", get_sensor_threshold
        )

        # Helper system removed, threshold sync now handled directly by Number entities

        # New: Test ESP32 threshold report service

        async def process_esp32_threshold_report(call):
            """Process ESP32 threshold report service and automatically create/update number entities"""
            try:
                node_id = call.data.get("node_id", "").lower()
                threshold_data = call.data.get("threshold_data", {})

                _LOGGER.info(
                    "Processing ESP32 threshold report: node_id=%s, data=%s",
                    node_id,
                    threshold_data,
                )

                # Send threshold report event to number platform for processing
                hass.bus.async_fire(
                    f"{DOMAIN}_esp32_threshold_report",
                    {
                        "node_id": node_id,
                        "threshold_data": threshold_data,
                        "timestamp": datetime.datetime.now().isoformat(),
                    },
                )

                return {"status": "success", "processed_params": len(threshold_data)}

            except Exception as err:
                _LOGGER.error("Failed to process ESP32 threshold report: %s", err)
                return {"status": "error", "message": str(err)}

        async def process_esp32_alarm(call):
            """Process ESP32 alarm message service"""
            try:
                node_id = call.data.get("node_id")
                alarm_message = call.data.get("alarm_message")

                # Parse alarm message format: "ALARM|sensor_name|min|max|value|type|delay|current_value:xx"
                if alarm_message.startswith("ALARM|"):
                    parts = alarm_message[6:].split("|")
                    if len(parts) >= 6:
                        sensor_name = parts[0]
                        min_threshold = float(parts[1])
                        max_threshold = float(parts[2])
                        simulated_value = float(parts[3])
                        alarm_type = parts[4]
                        alarm_delay = int(parts[5])

                        # Parse current value
                        current_value = simulated_value
                        if len(parts) >= 7 and "current_value:" in parts[6]:
                            current_value = float(parts[6].split("current_value:")[1])

                        # Send alarm event
                        hass.bus.async_fire(
                            f"{DOMAIN}_esp32_alarm_received",
                            {
                                "node_id": node_id,
                                "sensor_name": sensor_name,
                                "min_threshold": min_threshold,
                                "max_threshold": max_threshold,
                                "simulated_value": simulated_value,
                                "current_value": current_value,
                                "alarm_type": alarm_type,
                                "alarm_delay": alarm_delay,
                                "timestamp": datetime.datetime.now().isoformat(),
                            },
                        )

                        _LOGGER.info(
                            "Processing ESP32 alarm: device=%s, sensor=%s, type=%s, value=%.2f",
                            node_id,
                            sensor_name,
                            alarm_type,
                            simulated_value,
                        )
                        return {
                            "node_id": node_id,
                            "sensor_name": sensor_name,
                            "alarm_type": alarm_type,
                            "status": "success",
                        }

                return {"status": "error", "message": "Invalid alarm message format"}
            except Exception as err:
                _LOGGER.error("Failed to process ESP32 alarm message: %s", err)
                return {"status": "error", "message": str(err)}

        async def sync_thresholds_to_esp32(call):
            """Sync thresholds to ESP32 device service - now reads from helpers"""
            try:
                node_id = call.data.get("node_id")

                # Collect threshold configuration for all sensors (read from helpers)
                threshold_config = {}
                synced_count = 0

                sensor_types = [
                    "temperature",
                    "humidity",
                    "pressure",
                    "illuminance",
                    "co2",
                    "voc",
                    "pm25",
                ]

                for sensor_type in sensor_types:
                    # Check if corresponding sensor exists
                    sensor_entity_id = f"sensor.esp32_{sensor_type}"
                    sensor_state = hass.states.get(sensor_entity_id)

                    if sensor_state:  # Sensor exists, read corresponding helper values
                        min_helper_id = f"input_number.esp32_{sensor_type}_min"
                        max_helper_id = f"input_number.esp32_{sensor_type}_max"
                        alarm_helper_id = f"input_boolean.esp32_{sensor_type}_alarm"

                        min_state = hass.states.get(min_helper_id)
                        max_state = hass.states.get(max_helper_id)
                        alarm_state = hass.states.get(alarm_helper_id)

                        if min_state or max_state or alarm_state:
                            threshold_config[sensor_type] = {
                                "min": float(min_state.state) if min_state else None,
                                "max": float(max_state.state) if max_state else None,
                                "enabled": alarm_state.state == "on"
                                if alarm_state
                                else False,
                                "helper_entities": {
                                    "min": min_helper_id,
                                    "max": max_helper_id,
                                    "alarm": alarm_helper_id,
                                },
                            }
                            synced_count += 1

                # Logic for actually sending to ESP32 can be added here
                # For example via MQTT, HTTP or other communication methods

                _LOGGER.info(
                    "Synced %d sensor thresholds to ESP32 device (read from helpers): %s",
                    synced_count,
                    node_id or "all devices",
                )

                return {
                    "node_id": node_id or "all",
                    "sensors_synced": synced_count,
                    "threshold_config": threshold_config,
                    "status": "success",
                }
            except Exception as err:
                _LOGGER.error("Failed to sync thresholds to ESP32: %s", err)
                return {"status": "error", "message": str(err)}

        async def update_sensor_threshold_attributes(call):
            """Service to update sensor threshold attributes - now managed through helpers"""
            try:
                entity_id = call.data.get("entity_id")
                if not entity_id:
                    return {"status": "error", "message": "Missing entity_id"}

                # Extract sensor type from entity_id
                if "esp32_" not in entity_id:
                    return {
                        "status": "error",
                        "message": "Unsupported entity_id format",
                    }

                sensor_type = entity_id.split("esp32_")[-1]

                # Extract threshold parameters from call data
                threshold_updates = {}
                updated_helpers = []

                for key, value in call.data.items():
                    if key.endswith("_min_threshold"):
                        helper_id = f"input_number.esp32_{sensor_type}_min"
                        try:
                            await hass.services.async_call(
                                "input_number",
                                "set_value",
                                {"entity_id": helper_id, "value": value},
                            )
                            threshold_updates[key] = value
                            updated_helpers.append(helper_id)
                        except Exception:
                            _LOGGER.warning(
                                "Failed to update minimum threshold helper: %s - %s",
                                helper_id,
                                e,
                            )

                    elif key.endswith("_max_threshold"):
                        helper_id = f"input_number.esp32_{sensor_type}_max"
                        try:
                            await hass.services.async_call(
                                "input_number",
                                "set_value",
                                {"entity_id": helper_id, "value": value},
                            )
                            threshold_updates[key] = value
                            updated_helpers.append(helper_id)
                        except Exception:
                            _LOGGER.warning(
                                "Failed to update maximum threshold helper: %s - %s",
                                helper_id,
                                e,
                            )

                    elif key.endswith("_threshold_alarm_enabled"):
                        helper_id = f"input_boolean.esp32_{sensor_type}_alarm"
                        try:
                            service_name = "turn_on" if value else "turn_off"
                            await hass.services.async_call(
                                "input_boolean", service_name, {"entity_id": helper_id}
                            )
                            threshold_updates[key] = value
                            updated_helpers.append(helper_id)
                        except Exception:
                            _LOGGER.warning(
                                "Failed to update alarm helper: %s - %s", helper_id, e
                            )

                if not threshold_updates:
                    return {
                        "status": "error",
                        "message": "No threshold parameters provided",
                    }

                # Send threshold updates to ESP32 through helpers
                hass.bus.async_fire(
                    f"{DOMAIN}_threshold_update_esp32",
                    {
                        "sensor_type": sensor_type,
                        "threshold_updates": threshold_updates,
                        "updated_helpers": updated_helpers,
                        "entity_id": entity_id,
                    },
                )

                _LOGGER.info(
                    "Sensor threshold attributes updated through helpers: %s, updated helpers: %s",
                    entity_id,
                    updated_helpers,
                )

                return {
                    "entity_id": entity_id,
                    "sensor_type": sensor_type,
                    "updated_attributes": list(threshold_updates.keys()),
                    "updated_helpers": updated_helpers,
                    "status": "success",
                }

            except Exception as err:
                _LOGGER.error("Failed to update sensor threshold attributes: %s", err)
                return {"status": "error", "message": str(err)}

        # Register new services
        hass.services.async_register(
            DOMAIN, "process_esp32_threshold_report", process_esp32_threshold_report
        )
        hass.services.async_register(DOMAIN, "process_esp32_alarm", process_esp32_alarm)
        hass.services.async_register(
            DOMAIN, "sync_thresholds_to_esp32", sync_thresholds_to_esp32
        )
        hass.services.async_register(
            DOMAIN,
            "update_sensor_threshold_attributes",
            update_sensor_threshold_attributes,
        )

        # Helper management services

        # Add a convenience service specifically for creating helpers

        # Add automatic helper management service

        # Add threshold communication test service

        _LOGGER.info(
            "ESP HA sensor services registered (including ESP32 threshold and alarm features)"
        )
    else:
        _LOGGER.info("ESP HA sensor services already exist, skipping registration")

    # Register a summary service to get all sensor information
    if not hass.services.has_service(DOMAIN, "get_sensors_summary"):

        async def get_sensors_summary(call):
            """Get summary of all sensors"""
            try:
                summary = {
                    "sensors": {},
                    "total_count": 0,
                    "timestamp": datetime.datetime.now().isoformat(),
                }

                # Add local sensor information
                all_discovered = hass.data[DOMAIN].get("discovered_sensors", {})
                for entity_id, entity in all_discovered.items():
                    summary["sensors"][entity_id] = {
                        "sensor_type": entity._sensor_type,
                        "node_id": entity._node_id,
                        "last_value": entity._attr_native_value,
                        "available": entity._attr_available,
                        "source": "local",
                    }

                summary["total_count"] = len(summary["sensors"])

                # Fire event for automation consumption
                hass.bus.async_fire(f"{DOMAIN}_sensors_summary_response", summary)

                return summary
            except Exception as err:
                _LOGGER.error(
                    "Failed to get sensor summary: %s", str(err), exc_info=True
                )
                return {"error": str(err)}

        hass.services.async_register(DOMAIN, "get_sensors_summary", get_sensors_summary)
        _LOGGER.info("Sensor summary service registered")

    async_add_entities(entities)
    _LOGGER.info("Sensor entities added, total %d entities", len(entities))

    # Return True to indicate successful platform setup
    # Store unsubscribe functions for cleanup
    unsubscribe_functions = []

    @callback
    def handle_threshold_update_from_number(event):
        """Handle threshold updates from Number entities."""
        try:
            data = event.data or {}
            node_id = str(data.get("node_id", "")).lower()
            param_name = data.get("param_name") or ""
            value = data.get("value")
            sensor_type = data.get("sensor_type") or ""
            if not node_id or value is None:
                return

            # Derive threshold type from param name
            threshold_type = None
            if param_name.endswith("_min_threshold"):
                threshold_type = "min"
            elif param_name.endswith("_max_threshold"):
                threshold_type = "max"
            if not threshold_type:
                return

            # Derive sensor_type if missing
            if not sensor_type:
                try:
                    st = param_name.replace("_min_threshold", "").replace(
                        "_max_threshold", ""
                    )
                    # Map back short prefixes used in number.py
                    reverse_map = {
                        "temp": "temperature",
                        "humidity": "humidity",
                        "pressure": "pressure",
                        "illuminance": "illuminance",
                        "co2": "co2",
                        "voc": "voc",
                        "pm25": "pm25",
                        # Add pinyin mappings
                        "huan_jing_wen_du": "temperature",
                        "huan_jing_shi_du": "humidity",
                        "qi_ya": "pressure",
                    }
                    sensor_type = reverse_map.get(st, st)
                except Exception:
                    pass

            # Update threshold cache
            cache = hass.data[DOMAIN].setdefault("sensor_thresholds", {})
            node_cache = cache.setdefault(node_id, {})
            st_cache = node_cache.setdefault(sensor_type, {})
            st_cache[threshold_type] = float(value)

        except Exception as err:
            _LOGGER.error("Failed to handle threshold update: %s", err)

    @callback
    def async_handle_threshold_alert(event):
        """Handle threshold alert events."""
        try:
            event_data = event.data
            sensor_type = event_data.get("sensor_type", "unknown")
            alert_type = event_data.get("alert_type", "unknown")
            current_value = event_data.get("current_value")
            threshold_value = event_data.get("threshold_value")
            unit = event_data.get("unit", "")
            device_name = event_data.get("device_name", "ESP32")

            # Create notification message
            if alert_type == "high":
                message = f"{sensor_type.title()} alert: Current {current_value}{unit} exceeds maximum threshold {threshold_value}{unit}"
                notification_id = f"esp32_{sensor_type}_high_alert"
            elif alert_type == "low":
                message = f"{sensor_type.title()} alert: Current {current_value}{unit} is below minimum threshold {threshold_value}{unit}"
                notification_id = f"esp32_{sensor_type}_low_alert"
            else:
                message = (
                    f"{sensor_type.title()} threshold alert: {current_value}{unit}"
                )
                notification_id = f"esp32_{sensor_type}_alert"

            # Create persistent notification
            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "message": message,
                        "title": f"{device_name} Sensor Alert",
                        "notification_id": notification_id,
                    },
                )
            )

            _LOGGER.info("Created threshold alert notification: %s", message)

        except Exception as err:
            _LOGGER.error("Failed to handle threshold alert: %s", err)

    @callback
    def async_handle_threshold_violation(event):
        """Handle threshold violation events."""
        try:
            event_data = event.data
            sensor_type = event_data.get("sensor_type", "unknown")
            alert_type = event_data.get("alert_type", "unknown")
            current_value = event_data.get("value") or event_data.get("current_value")
            threshold_value = event_data.get("threshold_value")
            unit = event_data.get("unit", "")
            device_name = event_data.get("device_name", "ESP32")

            if alert_type == "high":
                message = f"{sensor_type.title()} alert: Current {current_value}{unit} exceeds maximum threshold {threshold_value}{unit}"
                notification_id = f"esp32_{sensor_type}_high_alert"
            elif alert_type == "low":
                message = f"{sensor_type.title()} alert: Current {current_value}{unit} is below minimum threshold {threshold_value}{unit}"
                notification_id = f"esp32_{sensor_type}_low_alert"
            else:
                message = (
                    f"{sensor_type.title()} threshold alert: {current_value}{unit}"
                )
                notification_id = f"esp32_{sensor_type}_alert"

            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "message": message,
                        "title": f"{device_name} Sensor Alert",
                        "notification_id": notification_id,
                    },
                )
            )
            _LOGGER.info("Created threshold violation notification: %s", message)
        except Exception as err:
            _LOGGER.error("Failed to handle threshold violation: %s", err)

    # Register event handlers using Home Assistant bus
    unsubscribe_functions.extend(
        [
            hass.bus.async_listen(
                f"{DOMAIN}_threshold_update_to_esp32",
                handle_threshold_update_from_number,
            ),
            hass.bus.async_listen(
                f"{DOMAIN}_threshold_alert", async_handle_threshold_alert
            ),
            hass.bus.async_listen(
                f"{DOMAIN}_threshold_violation", async_handle_threshold_violation
            ),
        ]
    )

    # Store unsubscribe functions in hass.data for cleanup
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    if "unsubscribe_functions" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["unsubscribe_functions"] = {}
    hass.data[DOMAIN]["unsubscribe_functions"][config_entry.entry_id] = (
        unsubscribe_functions
    )

    return True


class ESPHomeSensor(SensorEntity):
    """ESPHome sensor entity using standard HA patterns."""

    _attr_should_poll = False  # No polling, event driven
    _attr_force_update = False  # Update only on value change

    def __init__(
        self,
        hass: HomeAssistant,
        api: Any,
        node_id: str,
        device_name: str,
        sensor_type: str,
        name: str,
        unit: str,
        device_class: str,
        state_class: str,
        is_discovery: bool = False,
        param_info: dict | None = None,
        current_value: Any = None,
    ) -> None:
        """Initialize the sensor entity.

        Args:
            hass: Home Assistant instance.
            api: API instance for device communication.
            node_id: Device node ID.
            device_name: Device name.
            sensor_type: Type of sensor (temperature, humidity, etc.).
            name: Display name for the sensor.
            unit: Unit of measurement.
            device_class: Home Assistant device class.
            state_class: Home Assistant state class.
            is_discovery: Whether this is a discovered sensor.
            param_info: Additional parameter information.
            current_value: Initial sensor value.
        """
        SensorEntity.__init__(self)
        self._hass = hass
        self._api = api
        self._node_id = str(node_id).replace(":", "").lower()
        self._device_name = device_name
        self._sensor_type = sensor_type
        self._is_discovery = is_discovery
        self._param_info = param_info or {}

        # Setup entity through extracted methods
        self._setup_entity_naming(sensor_type)
        self._setup_device_class_and_unit(sensor_type, unit, device_class, state_class)
        self._setup_unique_id(sensor_type, is_discovery)
        self._setup_device_info()
        self._initialize_state_tracking()
        self._set_initial_value(is_discovery, current_value, param_info)

        _LOGGER.debug(
            "Created sensor entity: %s, type=%s", self._attr_unique_id, sensor_type
        )

    def _setup_entity_naming(self, sensor_type: str) -> None:
        """Configure entity name based on sensor type.

        Args:
            sensor_type: Type of sensor.
        """
        # Use has_entity_name=True pattern: short names that HA combines with device name
        self._attr_name = get_sensor_display_name(sensor_type)
        self._attr_has_entity_name = True  # Let HA combine with device name


    def _setup_device_class_and_unit(
        self, sensor_type: str, unit: str, device_class: str, state_class: str
    ) -> None:
        """Configure device class, state class, and unit of measurement.

        Args:
            sensor_type: Type of sensor.
            unit: Unit of measurement.
            device_class: Home Assistant device class.
            state_class: Home Assistant state class.
        """
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

        # Force correct units for specific sensor types
        if sensor_type == "pressure":
            self._attr_native_unit_of_measurement = UnitOfPressure.HPA
            self._attr_device_class = SensorDeviceClass.PRESSURE
            _LOGGER.info(
                "Force correct pressure sensor unit: %s -> %s", unit, UnitOfPressure.HPA
            )
        elif sensor_type in ["temperature", "ambient_temperature"]:
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            _LOGGER.info(
                "Force correct temperature sensor unit: %s -> %s",
                unit,
                UnitOfTemperature.CELSIUS,
            )

    def _setup_unique_id(
        self, sensor_type: str, is_discovery: bool
    ) -> None:
        """Generate unique ID for the entity.

        Args:
            sensor_type: Type of sensor.
            is_discovery: Whether this is a discovered sensor.
        """
        if is_discovery:
            self._attr_unique_id = f"{DOMAIN}_{self._node_id}_{sensor_type}"
        else:
            self._attr_unique_id = f"{DOMAIN}_{self._node_id}_{sensor_type}_legacy"

    def _setup_device_info(self) -> None:
        """Setup device information for entity."""
        # Use get_device_info like other entities (binary_sensor, light, number)
        # This ensures consistent device registration across all entity types
        self._attr_device_info = get_device_info(self._node_id, self._device_name)

    def _initialize_state_tracking(self) -> None:
        """Initialize state tracking variables."""
        self._attr_available = True
        self._attr_extra_state_attributes = {}
        self._unsub_update = None
        self._last_update_time = None
        self._value_history = []
        # Internal: actual history buffer size (can be configured by ESP32)
        self._max_history_items = DEFAULT_MAX_HISTORY_ITEMS
        
        # Extended attributes: will be set if ESP32 provides them via get_property
        # When _max_history_count is detected, it will update _max_history_items
        # self._max_history_count = None  # ESP32 config: shown in UI, updates _max_history_items
        # self._latest_values_duration = None  # ESP32 config: affects statistics window (e.g., 60 seconds)

    def _set_initial_value(
        self, is_discovery: bool, current_value: Any, param_info: dict
    ) -> None:
        """Set initial sensor value if available.

        Args:
            is_discovery: Whether this is a discovered sensor.
            current_value: Initial sensor value.
            param_info: Additional parameter information.
        """
        if is_discovery and current_value is not None:
            self._attr_native_value = (
                float(current_value) if current_value is not None else None
            )
            self._last_update_time = datetime.datetime.now()
            self._add_to_history(self._attr_native_value)
        elif is_discovery and param_info:
            self._update_value_from_device()

    def _add_to_history(self, value: Any) -> None:
        """Add value to local history cache"""
        try:
            timestamp = datetime.datetime.now()

            # Convert value to appropriate type for storage
            if isinstance(value, (int, float)):
                numeric_value = float(value)
            else:
                try:
                    numeric_value = float(value)
                except (ValueError, TypeError):
                    numeric_value = 0.0

            history_item = {
                "timestamp": timestamp,
                "value": numeric_value,
                "state": str(value),
                "iso_timestamp": timestamp.isoformat(),
            }

            self._value_history.append(history_item)

            # Keep history records within limit
            if len(self._value_history) > self._max_history_items:
                self._value_history = self._value_history[-self._max_history_items :]

            _LOGGER.debug(
                "Added history record: %s = %s (total: %d)",
                self._sensor_type,
                numeric_value,
                len(self._value_history),
            )

        except Exception as err:
            _LOGGER.error("Failed to add history record: %s", str(err))

    def get_recent_history(self, minutes: int = 5) -> list:
        """Get historical data for recent N minutes"""
        try:
            cutoff_time = datetime.datetime.now() - datetime.timedelta(minutes=minutes)
            recent_history = [
                {
                    "timestamp": item[
                        "iso_timestamp"
                    ],  # Use ISO format for JSON serialization
                    "value": item["value"],
                    "state": item["state"],
                }
                for item in self._value_history
                if item["timestamp"] >= cutoff_time
            ]

            _LOGGER.debug(
                "Getting historical data: %s, %dtotal %d records within minutes",
                self._sensor_type,
                minutes,
                len(recent_history),
            )
            return recent_history
        except Exception as err:
            _LOGGER.error("Failed to get historical data: %s", str(err))
            return []

    @property
    def unit_of_measurement(self) -> str | None:
        """Return measurement unit, ensure unit is displayed correctly"""
        if self._sensor_type == "pressure":
            return "hPa"  # Force pressure to use hPa
        if self._sensor_type in ["temperature", "ambient_temperature"]:
            return "°C"  # Force temperature to use °C
        return self._attr_native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return extra state attributes with useful statistics and historical data."""
        try:
            # Use configured duration or default to 5 minutes
            stats_duration = getattr(self, "_latest_values_duration", 300) // 60  # Convert seconds to minutes
            recent_history = self.get_recent_history(stats_duration)
            
            # Core attributes (always present)
            attributes = {
                "history_count": len(self._value_history),
                f"history_{stats_duration}m_count": len(recent_history),
            }

            # Calculate statistics for recent history
            if recent_history:
                values = [
                    float(item["value"])
                    for item in recent_history
                    if isinstance(item["value"], (int, float))
                ]

                if values:
                    # Show all values in recent_history (up to stats_duration minutes)
                    # This respects the configured latest_values_duration (default 5 minutes)
                    attributes.update(
                        {
                            f"min_{stats_duration}m": min(values),
                            f"max_{stats_duration}m": max(values),
                            f"avg_{stats_duration}m": round(sum(values) / len(values), 2),
                            "latest_values": [item["value"] for item in recent_history],  # 显示时间窗口内的所有值
                        }
                    )
                else:
                    # 即使没有数据，也显示空值，保持属性完整性
                    attributes.update(
                        {
                            f"min_{stats_duration}m": None,
                            f"max_{stats_duration}m": None,
                            f"avg_{stats_duration}m": None,
                            "latest_values": [],
                        }
                    )
            else:
                # 没有历史数据时，也显示空值
                attributes.update(
                    {
                        f"min_{stats_duration}m": None,
                        f"max_{stats_duration}m": None,
                        f"avg_{stats_duration}m": None,
                        "latest_values": [],
                    }
                )

            # Extended attributes (only if configured by ESP32) - 扩展配置参数
            # Show max_history_count only if different from default
            if self._max_history_items != DEFAULT_MAX_HISTORY_ITEMS:
                attributes["max_history_count"] = self._max_history_items
            
            if hasattr(self, "_latest_values_duration"):
                attributes["latest_values_duration"] = f"{self._latest_values_duration}s"

            return attributes
        except Exception as err:
            _LOGGER.error("Failed to generate extra state attributes: %s", str(err))
            return {}

    def _get_threshold_attributes_from_numbers(
        self, sensor_type: str
    ) -> dict[str, any]:
        """Read threshold info from Number entities and return as attribute dictionary"""
        try:
            # Use mapped sensor type for number entity names
            mapped_sensor_type = get_sensor_mapped_type(sensor_type)

            threshold_attrs = {}

            # Number entity naming must be consistent with IDs created by number platform
            raw_for_number = self._sensor_type.lower()
            min_number_id = build_sensor_number_entity_id(
                DOMAIN, self._node_id, raw_for_number, "min"
            )
            max_number_id = build_sensor_number_entity_id(
                DOMAIN, self._node_id, raw_for_number, "max"
            )

            # Read minimum threshold
            min_state = self.hass.states.get(min_number_id)
            if min_state and min_state.state not in ("unknown", "unavailable"):
                try:
                    threshold_attrs[f"{mapped_sensor_type}_min_threshold"] = float(
                        min_state.state
                    )
                    threshold_attrs["threshold_min_entity"] = min_number_id
                except (ValueError, TypeError):
                    pass

            # Read maximum threshold
            max_state = self.hass.states.get(max_number_id)
            if max_state and max_state.state not in ("unknown", "unavailable"):
                try:
                    threshold_attrs[f"{mapped_sensor_type}_max_threshold"] = float(
                        max_state.state
                    )
                    threshold_attrs["threshold_max_entity"] = max_number_id
                except (ValueError, TypeError):
                    pass

            # Add threshold management information
            if threshold_attrs:
                threshold_attrs["threshold_managed_by"] = "number_entities"
                threshold_attrs["threshold_config_ui"] = (
                    "Settings → Devices & Services → ESP HA → Number Entities"
                )

                # Add comparison status between current value and thresholds
                current_value = self._attr_native_value
                if current_value is not None and isinstance(
                    current_value, (int, float)
                ):
                    min_val = threshold_attrs.get(f"{mapped_sensor_type}_min_threshold")
                    max_val = threshold_attrs.get(f"{mapped_sensor_type}_max_threshold")

                    if min_val is not None and max_val is not None:
                        if current_value < min_val:
                            threshold_attrs["threshold_status"] = "below_min"
                            threshold_attrs["threshold_alert"] = (
                                f"Current value {current_value} is below minimum {min_val}"
                            )
                        elif current_value > max_val:
                            threshold_attrs["threshold_status"] = "above_max"
                            threshold_attrs["threshold_alert"] = (
                                f"Current value {current_value} is above maximum {max_val}"
                            )
                        else:
                            threshold_attrs["threshold_status"] = "normal"
                            threshold_attrs["threshold_alert"] = "Within normal range"

            return threshold_attrs

        except Exception as err:
            _LOGGER.error("Failed to read Number threshold information: %s", err)
            return {
                "threshold_error": str(err),
                "threshold_managed_by": "number_entities",
                "threshold_config_ui": "Settings → Devices & Services → ESP HA → Number Entities",
            }

    async def create_threshold_helpers(self):
        """Create independent threshold Helper entities (not associated with ESP device)."""
        try:
            # Get mapped sensor type
            raw_sensor_type = self._sensor_type.lower()
            mapped_sensor_type = get_sensor_mapped_type(raw_sensor_type)

            # Get threshold configuration
            config = get_sensor_threshold_config(mapped_sensor_type)
            if not config:
                _LOGGER.warning(
                    "Unsupported sensor type, skipping helper creation: %s",
                    mapped_sensor_type,
                )
                return

            config = threshold_configs[mapped_sensor_type]

            # Helper entity naming:esp32_{mapped_sensor_type}_{min/max/alarm}
            min_helper_name = f"esp32_{mapped_sensor_type}_min"
            max_helper_name = f"esp32_{mapped_sensor_type}_max"
            alarm_helper_name = f"esp32_{mapped_sensor_type}_alarm"

            # Check if helpers already exist
            min_exists = (
                self.hass.states.get(f"input_number.{min_helper_name}") is not None
            )
            max_exists = (
                self.hass.states.get(f"input_number.{max_helper_name}") is not None
            )
            alarm_exists = (
                self.hass.states.get(f"input_boolean.{alarm_helper_name}") is not None
            )

            if min_exists and max_exists and alarm_exists:
                _LOGGER.info(
                    "Threshold helpers already exist, skipping creation: %s",
                    mapped_sensor_type,
                )
                return

            # Send helper creation request event (create independent Helpers, not associated with ESP device)
            helper_request = {
                "sensor_type": mapped_sensor_type,
                "sensor_entity_id": getattr(
                    self, "entity_id", f"sensor.{DOMAIN}_{mapped_sensor_type}"
                ),
                "create_independent_helpers": True,  # Mark as independent Helper
                "helpers_needed": {
                    "min_threshold": {
                        "entity_id": f"input_number.{min_helper_name}",
                        "name": f"ESP32 {mapped_sensor_type.replace('_', ' ').title()} Minimum Threshold",
                        "config": config["min"],
                        "exists": min_exists,
                    },
                    "max_threshold": {
                        "entity_id": f"input_number.{max_helper_name}",
                        "name": f"ESP32 {mapped_sensor_type.replace('_', ' ').title()} Maximum Threshold",
                        "config": config["max"],
                        "exists": max_exists,
                    },
                    "alarm_enabled": {
                        "entity_id": f"input_boolean.{alarm_helper_name}",
                        "name": f"ESP32 {mapped_sensor_type.replace('_', ' ').title()} Alarm Enabled",
                        "config": {"initial": True},
                        "exists": alarm_exists,
                    },
                },
            }

            # Send event notification that independent helpers need to be created
            self.hass.bus.async_fire(f"{DOMAIN}_helper_creation_needed", helper_request)

            __LOGGER.info(
                "Sent independent threshold helper creation request: %s (will appear in Helpers, not associated with ESP device)",
                mapped_sensor_type,
            )

        except Exception as err:
            _LOGGER.error("Failed to request threshold helper creation: %s", err)

    async def _sync_threshold_to_esp32(self, param_name: str, value: Any):
        """Sync threshold parameters to ESP32 device"""
        try:
            # Sync parameters to ESP32 via API
            if hasattr(self._api, "devices") and self._node_id in self._api.devices:
                # Update local parameters
                device_data = self._api.devices[self._node_id]
                if "params" not in device_data:
                    device_data["params"] = {}

                device_data["params"][param_name] = value

                # Send to ESP32 device
                update_data = {param_name: value}
                success = await self._api.send_device_command(
                    self._node_id, "set_thresholds", update_data
                )

                if success:
                    _LOGGER.info(
                        "Threshold parameter synced to ESP32: %s = %s",
                        param_name,
                        value,
                    )
                else:
                    _LOGGER.error(
                        "Failed to sync threshold parameter to ESP32: %s", param_name
                    )
            else:
                _LOGGER.warning("Device not found, cannot sync threshold: %s", self._node_id)

        except Exception as err:
            _LOGGER.error("Failed to sync thresholds to ESP32: %s", err)

    def _get_threshold_icon(self, sensor_type: str, threshold_type: str) -> str:
        """Get icon for threshold helper."""
        return get_sensor_threshold_icon(sensor_type, threshold_type)

    def clear_history(self) -> None:
        """Clear local history cache."""
        self._value_history.clear()
        _LOGGER.info("Sensor history cache cleared: %s", self._sensor_type)

    def get_statistics(self, minutes: int = 10) -> dict:
        """Get statistics for specified time range."""
        try:
            recent_history = self.get_recent_history(minutes)
            if not recent_history:
                return {"error": "No data available"}

            values = [
                float(item["value"])
                for item in recent_history
                if isinstance(item["value"], (int, float))
            ]

            if not values:
                return {"error": "No numeric values available"}

            # Use utility function to get statistics
            return get_sensor_statistics(values, minutes)
        except Exception as err:
            _LOGGER.error("Failed to calculate statistics: %s", str(err))
            return {"error": str(err)}

    async def async_added_to_hass(self) -> None:
        """Register event listeners"""
        await super().async_added_to_hass()

        self._unsub_update = self.hass.bus.async_listen(
            f"{DOMAIN}_sensor_update", self._handle_sensor_update
        )

        # Listen for device availability changes
        self._unsub_availability = self.hass.bus.async_listen(
            f"{DOMAIN}_device_availability_changed", self._handle_device_availability_change
        )

        _LOGGER.debug("Sensor entity added to HA: %s", self._attr_unique_id)

    @property
    def available(self) -> bool:
        """Return True if entity is available (device is connected)."""
        api = self._hass.data.get(DOMAIN, {}).get("shared_api")
        if api:
            return api.is_device_available(self._node_id)
        return False

    def _update_value_from_device(self):
        """Update sensor value from device data"""
        try:
            if not self._is_discovery or not self._param_info:
                return

            # Get current device value from API
            if hasattr(self._api, "devices") and self._node_id in self._api.devices:
                device_data = self._api.devices[self._node_id]
                current_values = device_data.get("current_values", {})

                # Get corresponding value based on sensor type
                param_name = self._param_info.get("name", "")

                # Find corresponding value
                value = None
                if "Time" in current_values and param_name in current_values["Time"]:
                    value = current_values["Time"][param_name]
                elif param_name in current_values:
                    value = current_values[param_name]

                if value is not None:
                    self._attr_native_value = str(value)
                    self._attr_available = True
                    self._last_update_time = datetime.datetime.now()
                    self._add_to_history(value)
                    _LOGGER.debug(
                        "Sensor initial value updated: %s = %s",
                        self._sensor_type,
                        value,
                    )
                else:
                    _LOGGER.debug("Sensor initial value not found: %s", param_name)
            else:
                _LOGGER.debug("Device data not found: %s", node_id)

        except Exception as err:
            _LOGGER.error(
                "Failed to update sensor initial value: %s", str(err), exc_info=True
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up resources"""
        if self._unsub_update:
            self._unsub_update()
        if self._unsub_availability:
            self._unsub_availability()
        _LOGGER.debug("Sensor entity removed from HA: %s", self._attr_unique_id)

    async def _handle_device_availability_change(self, event) -> None:
        """Handle device availability change - update entity state."""
        try:
            # Normalize node_id to lowercase for comparison
            event_node_id = str(event.data.get("node_id", "")).replace(":", "").lower()

            # Only update if this event is for our device
            if event_node_id == self._node_id:
                available = event.data.get("available", False)
                _LOGGER.debug(
                    "Device %s availability changed to %s, updating sensor entity %s",
                    device_node_id,
                    "available" if available else "unavailable",
                    self._attr_unique_id,
                )
                # Trigger state update - the available property will return current state
                self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error(
                "Error handling device availability change for %s: %s",
                self._attr_unique_id,
                err,
            )

    async def _handle_sensor_update(self, event) -> None:
        """Handle sensor update event"""
        try:
            # Normalize node_id to lowercase for comparison
            event_node_id = str(event.data["node_id"]).replace(":", "").lower()

            # Check if node_id matches
            if event_node_id != self._node_id:
                return

            # Check if sensor type matches
            event_type = event.data.get("type") or event.data.get("sensor_type", "")
            if event_type != self._sensor_type:
                return

            # Detect and update extended configuration parameters from ESP32
            params = event.data.get("params", {})

            # Detect max_history_count configuration with safety limit
            if "max_history_count" in params:
                requested_max = int(params["max_history_count"])
                # Enforce maximum limit for memory safety
                new_max = min(requested_max, MAX_HISTORY_LIMIT)
                if self._max_history_items != new_max:
                    self._max_history_items = new_max
                    if requested_max > MAX_HISTORY_LIMIT:
                        _LOGGER.warning(
                            "请求的历史记录数量 %d 超过最大限制 %d，已限制为 %d (传感器: %s)",
                            requested_max,
                            MAX_HISTORY_LIMIT,
                            new_max,
                            self._sensor_type,
                        )
                    else:
                        _LOGGER.info(
                            "检测到扩展配置 max_history_count: %d (传感器: %s)",
                            new_max,
                            self._sensor_type,
                        )

            # Detect latest_values_duration configuration with safety limit
            if "latest_values_duration" in params:
                requested_duration = int(params["latest_values_duration"])
                # Enforce maximum limit of 1 hour (3600 seconds)
                new_duration = min(requested_duration, MAX_LATEST_VALUES_DURATION)
                old_duration = getattr(self, "_latest_values_duration", None)
                if old_duration != new_duration:
                    self._latest_values_duration = new_duration
                    if requested_duration > MAX_LATEST_VALUES_DURATION:
                        _LOGGER.warning(
                            "请求的统计时长 %d 秒超过最大限制 %d 秒，已限制为 %d (传感器: %s)",
                            requested_duration,
                            MAX_LATEST_VALUES_DURATION,
                            new_duration,
                            self._sensor_type,
                        )
                    else:
                        _LOGGER.info(
                            "检测到扩展配置 latest_values_duration: %d 秒 (传感器: %s)",
                            new_duration,
                            self._sensor_type,
                        )

            # Update sensor value
            new_value_data = event.data.get("value") or event.data.get("current_value")
            if new_value_data is None:
                return
            new_value = float(new_value_data)
            old_value = self._attr_native_value

            # Record update time and history
            self._last_update_time = datetime.datetime.now()
            self._add_to_history(new_value)

            self._attr_native_value = new_value
            self._attr_available = True

            # Always update extra state attributes to show freshness
            self._attr_extra_state_attributes = self._attr_extra_state_attributes or {}
            self._attr_extra_state_attributes["last_esp32_update"] = (
                self._last_update_time.isoformat()
            )
            self._attr_extra_state_attributes["esp32_communication"] = "active"

            # OPTION 1: Always force update regardless of value change
            # This ensures UI always shows fresh data and timestamp
            self.async_write_ha_state()

            value_changed = old_value != new_value
            if value_changed:
                _LOGGER.info(
                    "Sensor state updated: %s=%.2f (old=%s, changed=True)",
                    self._sensor_type,
                    new_value,
                    old_value,
                )
            else:
                _LOGGER.debug(
                    "Sensor forced update: %s=%.2f (same value, timestamp updated)",
                    self._sensor_type,
                    new_value,
                )

        except ValueError as err:
            _LOGGER.error(
                "Failed to convert sensor value: %s, data=%s", str(err), event.data
            )
            self._attr_available = False
        except Exception as err:
            _LOGGER.error(
                "Failed to handle sensor update event: %s", str(err), exc_info=True
            )
            self._attr_available = False

    async def async_update(self) -> None:
        """Actively query sensor data (compatibility method)"""
        # ESP32 uses active reporting mode, no need for active querying
        # Maintain entity availability state
        self._attr_available = True
        # Remove async_write_ha_state() call to avoid duplicate state writes
        _LOGGER.debug("Sensor update called: %s", self._attr_unique_id)

    def async_write_ha_state(self) -> None:
        """Override to intercept all state changes and check thresholds."""
        old_value = None
        try:
            # Get the old value before calling the parent method
            current_state = self.hass.states.get(self.entity_id)
            if current_state and current_state.state not in ["unknown", "unavailable"]:
                try:
                    old_value = float(current_state.state)
                except (ValueError, TypeError):
                    old_value = None

            # Call the parent method to update the state
            super().async_write_ha_state()

            # Check thresholds if we have a numeric value
            if self._attr_native_value is not None:
                try:
                    current_value = float(self._attr_native_value)
                    _LOGGER.info(
                        "State change detected for %s: %s -> %s (entity_id=%s)",
                        self._sensor_type,
                        old_value,
                        current_value,
                        self.entity_id,
                    )

                    # Schedule async threshold checking
                    if self.hass:
                        self.hass.async_create_task(
                            self._check_threshold_violations(current_value, old_value)
                        )
                except (ValueError, TypeError) as err:
                    _LOGGER.debug(
                        "Cannot convert sensor value to float for threshold check: %s",
                        err,
                    )

        except Exception as err:
            _LOGGER.error("Error in async_write_ha_state for %s: %s", self.entity_id, err)
            # Still call parent method even if threshold checking fails
            try:
                super().async_write_ha_state()
            except Exception:
                pass

    async def _check_threshold_violations(
        self, current_value: float, old_value: float
    ) -> None:
        """Check for threshold violations and create notifications."""
        try:
            # Map sensor types to number entity naming convention
            sensor_type_mapping = {
                "temperature": "temperature",
                "ambient_temperature": "temperature",
                "humidity": "humidity",
                "ambient_humidity": "humidity",
                "pressure": "pressure",
                "illuminance": "illuminance",
                "co2": "co2",
                "voc": "voc",
                "pm25": "pm25",
                # Add pinyin mappings for number entity lookup
                "huan_jing_wen_du": "temperature",
                "huan_jing_shi_du": "humidity",
                "qi_ya": "pressure",
            }

            raw_sensor_type = self._sensor_type.lower()
            sensor_type = sensor_type_mapping.get(raw_sensor_type, raw_sensor_type)

            _LOGGER.debug(
                "Checking thresholds for sensor: %s=%s", sensor_type, current_value
            )

            # Get threshold values from number entities (not helpers)
            # Keep consistent with IDs created by number entities (number platform uses original sensor_type to generate entity_id)
            raw_for_number = self._sensor_type.lower()
            min_number_id = f"number.{DOMAIN}_{self._node_id}_{raw_for_number}_min_threshold"
            max_number_id = f"number.{DOMAIN}_{self._node_id}_{raw_for_number}_max_threshold"

            # Also try pinyin entity ID format (esp_39cc_huan_jing_wen_du_zui_da_yu_zhi)
            pinyin_min_id = f"number.esp_{self._node_id}_{raw_for_number}_zui_xiao_yu_zhi"
            pinyin_max_id = f"number.esp_{self._node_id}_{raw_for_number}_zui_da_yu_zhi"

            # Get threshold values from number entities - try multiple formats
            min_state = self.hass.states.get(min_number_id)
            max_state = self.hass.states.get(max_number_id)

            # If not found, try pinyin format
            if not min_state or not max_state:
                min_state = self.hass.states.get(pinyin_min_id)
                max_state = self.hass.states.get(pinyin_max_id)
                if min_state or max_state:
                    _LOGGER.info(
                        "Found pinyin format number entities: min=%s, max=%s",
                        pinyin_min_id,
                        pinyin_max_id,
                    )

            # Fix: if Number entities not found, log warning and skip alarm check
            # This ensures alarm judgment uses threshold values actually set by user in HA interface
            if not min_state or not max_state:
                _LOGGER.debug("No threshold entities found for %s", sensor_type)
                return

            if min_state and max_state:
                try:
                    min_threshold = float(min_state.state)
                    max_threshold = float(max_state.state)
                    _LOGGER.debug(
                        "Threshold check: sensor=%s, value=%s, min=%s, max=%s",
                        sensor_type,
                        current_value,
                        min_threshold,
                        max_threshold,
                    )
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Invalid threshold values for %s: min=%s, max=%s",
                        sensor_type,
                        min_state.state if min_state else None,
                        max_state.state if max_state else None,
                    )
                    return  # Invalid threshold values

            # Determine unit for display
            unit_map = {
                "temperature": "°C",
                "ambient_temperature": "°C",
                "humidity": "%",
                "ambient_humidity": "%",
                "pressure": " hPa",
                "illuminance": " lux",
                "co2": " ppm",
                "voc": " μg/m³",
                "pm25": " μg/m³",
            }
            unit = unit_map.get(sensor_type, "")

            # Check for threshold violations
            device_name = self._device_name
            alert_created = False

            # Check high threshold
            if current_value > max_threshold:
                _LOGGER.info(
                    "🚨 HIGH threshold violation detected: %s %s > %s%s",
                    sensor_type,
                    current_value,
                    max_threshold,
                    unit,
                )
                await self._create_threshold_notification(
                    sensor_type, "high", current_value, max_threshold, unit, device_name
                )
                await self._send_alert_to_esp32(
                    sensor_type, "high", current_value, max_threshold, unit
                )
                alert_created = True
                _LOGGER.info(
                    "High threshold alert created: %s %s > %s%s",
                    sensor_type,
                    current_value,
                    max_threshold,
                    unit,
                )

            elif current_value < min_threshold:
                _LOGGER.info(
                    "🚨 LOW threshold violation detected: %s %s < %s%s",
                    sensor_type,
                    current_value,
                    min_threshold,
                    unit,
                )
                await self._create_threshold_notification(
                    sensor_type, "low", current_value, min_threshold, unit, device_name
                )
                await self._send_alert_to_esp32(
                    sensor_type, "low", current_value, min_threshold, unit
                )
                alert_created = True
                _LOGGER.info(
                    "Low threshold alert created: %s %s < %s%s",
                    sensor_type,
                    current_value,
                    min_threshold,
                    unit,
                )

            elif old_value is not None and (
                old_value > max_threshold or old_value < min_threshold
            ):
                # Value returned to normal range
                await self._clear_threshold_notifications(sensor_type)
                await self._send_alert_clear_to_esp32(sensor_type, current_value, unit)
                _LOGGER.info(
                    "Threshold alert cleared: %s returned to normal (%s%s)",
                    sensor_type,
                    current_value,
                    unit,
                )

            if alert_created:
                # Fire event for automation triggers
                self.hass.bus.async_fire(
                    f"{DOMAIN}_threshold_violation",
                    {
                        "entity_id": self.entity_id,
                        "sensor_type": sensor_type,
                        "current_value": current_value,
                        "min_threshold": min_threshold,
                        "max_threshold": max_threshold,
                        "unit": unit,
                        "device_name": device_name,
                        "node_id": self._node_id,
                    },
                )

        except Exception as err:
            _LOGGER.error("Failed to check threshold violations: %s", err)

    async def _send_alert_to_esp32(
        self,
        sensor_type: str,
        alert_type: str,
        current_value: float,
        threshold_value: float,
        unit: str,
    ) -> None:
        """Send threshold alert to ESP32 device."""
        try:
            # Build alarm message using utility function
            alert_message = build_threshold_alert_message(
                sensor_type, alert_type, current_value, threshold_value, unit
            )

            # Get the shared API instance
            api_instance = self.hass.data.get(DOMAIN, {}).get("shared_api")
            if not api_instance:
                _LOGGER.warning("Shared API instance not found for alert")
                return

            await api_instance.send_device_command(
                node_id=self._node_id,
                command_type="threshold_alert",
                parameters={
                    "alert_message": alert_message,
                    "sensor_type": sensor_type,
                    "alert_type": alert_type,
                    "current_value": current_value,
                    "threshold_value": threshold_value,
                    "unit": unit,
                },
            )
            _LOGGER.info("Threshold alert sent to ESP32: %s", alert_message)

        except Exception as err:
            _LOGGER.error("Failed to send alert to ESP32: %s", err)

    async def _send_alert_clear_to_esp32(
        self, sensor_type: str, current_value: float, unit: str
    ) -> None:
        """Send threshold alert clear to ESP32 device."""
        try:
            # Build alarm clear message using utility function
            clear_message = build_threshold_clear_message(
                sensor_type, current_value, unit
            )

            # Get the shared API instance
            api_instance = self.hass.data.get(DOMAIN, {}).get("shared_api")
            if not api_instance:
                _LOGGER.warning("Shared API instance not found for alert clear")
                return

            await api_instance.send_device_command(
                node_id=self._node_id,
                command_type="threshold_alert_clear",
                parameters={
                    "clear_message": clear_message,
                    "sensor_type": sensor_type,
                    "current_value": current_value,
                    "unit": unit,
                },
            )
        except Exception as err:
            _LOGGER.error("Failed to send alert clear to ESP32: %s", err)
