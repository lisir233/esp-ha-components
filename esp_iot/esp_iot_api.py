"""ESP Home API - Main coordinator for ESP device management.

This module provides the ESPHomeAPI class which manages ESP device connections,
property fetching, state updates, and command sending.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

import aiohttp
from zeroconf import ServiceBrowser

from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .esp_local_ctrl_lib import esp_local_ctrl
from .esp_iot_discovery import create_basic_device_config_from_properties
from .esp_iot_local_ctrl import ESPLocalCtrlClient
from .esp_iot_network import ESPDeviceListener
from .esp_iot_parser import ESPDeviceParser
from .esp_iot_payload import ESPDeviceController
from .esp_iot_spec import CONFIG_PROPERTY_NAMES, DEVICE_TYPES
from .esp_iot_utils import fire_property_events

_LOGGER = logging.getLogger(__name__)

# Connection and timing constants
RATE_LIMIT_INTERVAL = 10  # seconds between property calls per device


class ESPHomeAPI:
    """API coordinator for ESP HA devices.

    This class manages:
    - Device discovery and registration via mDNS/Zeroconf
    - ESP Local Control client connections with PoP authentication
    - Property fetching and device state updates
    - Entity discovery and platform setup through event bus
    - Background monitoring and connection maintenance

    The coordinator uses a shared instance pattern where one API instance
    manages multiple devices, each with their own ESP Local Control client.

    Attributes:
        hass: Home Assistant instance
        domain: Integration domain name
        coordinator: Device coordinator for entity discovery
        devices: Dictionary of registered devices by node_id
        _local_ctrl_clients: Active ESP Local Control client connections
        _connection_locks: Locks to prevent concurrent connection attempts
        _property_fetch_locks: Locks to serialize property fetches per device
    """

    def __init__(self, hass: HomeAssistant, domain: str, default_port: int = 8080) -> None:
        """Initialize the ESPHome API coordinator.

        Sets up data structures for device management, connection handling,
        and entity discovery. Does not establish any connections yet.

        Args:
            hass: Home Assistant instance
            domain: Integration domain name
            default_port: Default port for ESP devices
        """
        self.hass = hass
        self.domain = domain
        self.default_port = default_port

        # Device registry and state
        self.devices: dict[str, dict[str, Any]] = {}
        self._session: aiohttp.ClientSession | None = None

        # ESP Local Control clients
        self._local_ctrl_clients: dict[str, ESPLocalCtrlClient] = {}

        # Concurrency control
        self._connection_locks: dict[str, asyncio.Lock] = {}
        self._property_fetch_locks: dict[str, asyncio.Lock] = {}

        # Device parser
        self._device_parser = ESPDeviceParser()

        # Discovery tracking
        self._device_config_entries: dict[str, str] = {}  # node_id -> entry_id
        self._discovered_platforms: set[str] = set()
        self._discovery_completed: set[str] = set()

        # Rate limiting
        self._rate_limit_tracker: dict[str, float] = {}

        # Monitoring task
        self._monitoring_task: asyncio.Task | None = None
        self._zc = None
        self._browser = None

    def register_config_entry(self, node_id: str, entry_id: str) -> None:
        """Register a config entry mapping for a device.

        Args:
            node_id: Device node ID
            entry_id: Config entry ID to associate with this device
        """
        self._device_config_entries[node_id.lower()] = entry_id

    def is_discovery_completed(self, node_id: str) -> bool:
        """Check if entity discovery has been completed for a device.

        Args:
            node_id: Device node ID

        Returns:
            True if discovery completed, False otherwise
        """
        return node_id in self._discovery_completed

    def mark_discovery_completed(self, node_id: str) -> None:
        """Mark entity discovery as completed for a device.

        Args:
            node_id: Device node ID
        """
        self._discovery_completed.add(node_id)

    def should_rate_limit(self, node_id: str) -> bool:
        """Check if device should be rate limited.

        Args:
            node_id: Device node ID

        Returns:
            True if device should be rate limited, False otherwise
        """
        current_time = time.time()
        last_call_key = f"last_property_call_{node_id}"
        last_call_time = self._rate_limit_tracker.get(last_call_key, 0)

        if current_time - last_call_time < RATE_LIMIT_INTERVAL:
            return True

        self._rate_limit_tracker[last_call_key] = current_time
        return False

    def _extract_current_values(self, properties: list) -> dict:
        """Extract current device values from properties.

        Args:
            properties: List of property dictionaries from device

        Returns:
            Dictionary of current values organized by device type
        """
        current_values = {}

        for prop in properties:
            if prop.get("name") == "config":
                continue

            prop_value = prop.get("value")
            params_data = None

            if isinstance(prop_value, dict):
                params_data = prop_value
            elif isinstance(prop_value, bytes):
                try:
                    value_str = prop_value.decode("latin-1")
                    if value_str.startswith("{"):
                        params_data = json.loads(value_str)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

            if params_data:
                # Extract all device types
                for device_type in DEVICE_TYPES:
                    if device_type in params_data:
                        current_values[device_type] = params_data[device_type]

                # Handle special cases
                if "State" in params_data:
                    current_values["Binary Sensor"] = {
                        "Binary Sensor": params_data["State"]
                    }

                if current_values:
                    break

        return current_values

    async def _trigger_platform_discovery(
        self, node_id: str, device_config: dict, current_values: dict, device_info: dict
    ):
        """Trigger platform discovery events for all platforms.

        Args:
            node_id: Device node ID
            device_config: Parsed device configuration
            current_values: Current device property values
            device_info: Device information dictionary
        """
        discovered_platforms = device_config.get("platforms", {})

        # Always trigger number platform for threshold controls
        if "number" not in self._discovered_platforms:
            self.hass.bus.async_fire(
                f"{self.domain}_platform_discovered",
                {
                    "domain": "number",
                    "entry_id": "all",
                    "node_id": node_id,
                    "device_info": device_info,
                },
            )
            self._discovered_platforms.add("number")

        # Trigger discovery for each platform
        for platform_type, entities in discovered_platforms.items():
            if platform_type not in self._discovered_platforms:
                self.hass.bus.async_fire(
                    f"{self.domain}_platform_discovered",
                    {
                        "domain": platform_type,
                        "entry_id": "all",
                        "node_id": node_id,
                        "device_info": device_info,
                    },
                )
                self._discovered_platforms.add(platform_type)

            # Trigger entity discovery for each entity
            for entity_info in entities:
                await self._trigger_entity_discovery(
                    node_id, platform_type, entity_info, current_values, device_info
                )

    async def _trigger_entity_discovery(
        self,
        node_id: str,
        platform_type: str,
        entity_info: dict,
        current_values: dict,
        device_info: dict,
    ):
        """Trigger discovery for a specific entity.

        Args:
            node_id: Device node ID
            platform_type: Platform type (light, sensor, etc.)
            entity_info: Entity information dictionary
            current_values: Current device property values
            device_info: Device information dictionary
        """
        if platform_type == "light":
            self._trigger_light_discovery(
                node_id, entity_info, current_values, device_info
            )
        elif platform_type in ("binary_sensor", "contact_sensor"):
            self._trigger_binary_sensor_discovery(
                node_id, entity_info, current_values, device_info
            )
        elif platform_type == "sensor":
            self._trigger_sensor_discovery(
                node_id, entity_info, current_values, device_info
            )
        elif platform_type in [
            "imu_gesture",
            "interactive_input",
            "battery_energy",
            "low_power_sleep",
        ]:
            self._trigger_controller_discovery(
                node_id, platform_type, entity_info, device_info
            )

    def _trigger_light_discovery(
        self, node_id: str, entity_info: dict, current_values: dict, device_info: dict
    ):
        """Trigger light entity discovery.

        Args:
            node_id: Device node ID
            entity_info: Entity information dictionary
            current_values: Current device property values
            device_info: Device information dictionary
        """
        light_params = current_values.copy()
        light_params.update(entity_info.get("initial_values", {}))

        self.hass.bus.async_fire(
            f"{self.domain}_light_discovered",
            {
                "node_id": node_id,
                "device_name": entity_info["device_name"],
                "light_params": light_params,
                "capabilities": entity_info.get("capabilities", {}),
                "device_info": device_info,
            },
        )

    def _trigger_binary_sensor_discovery(
        self, node_id: str, entity_info: dict, current_values: dict, device_info: dict
    ):
        """Trigger binary sensor entity discovery.

        Args:
            node_id: Device node ID
            entity_info: Entity information dictionary
            current_values: Current device property values
            device_info: Device information dictionary
        """
        device_config = entity_info.get("config", {})
        initial_values = entity_info.get("initial_values", {})
        device_name = entity_info.get("device_name", "Binary Sensor")

        # Get device class
        device_class = (
            device_config.get("device_class")
            or initial_values.get("device_class")
            or entity_info.get("device_class")
            or "door"
        )

        # Get current state
        current_state = None
        if current_values and "Binary Sensor" in current_values:
            bs_data = current_values["Binary Sensor"]
            if isinstance(bs_data, dict):
                current_state = next(iter(bs_data.values()), None)

        if current_state is None:
            current_state = initial_values.get("state", False)

        discovery_params = {
            "state": bool(current_state),
            "device_class": device_class,
            "debounce_time": initial_values.get("debounce_time", 100),
            "report_interval": initial_values.get("report_interval", 1000),
            "timestamp": time.time(),
            "source": "params" if current_values else "discovery",
        }

        self.hass.bus.async_fire(
            f"{self.domain}_binary_sensor_discovered",
            {
                "node_id": node_id,
                "device_name": device_name,
                "params": discovery_params,
                "config": device_config,
                "device_info": device_info,
            },
        )

    def _trigger_sensor_discovery(
        self, node_id: str, entity_info: dict, current_values: dict, device_info: dict
    ):
        """Trigger sensor entity discovery.

        Args:
            node_id: Device node ID
            entity_info: Entity information dictionary
            current_values: Current device property values
            device_info: Device information dictionary
        """
        if "param" in entity_info:
            current_value = None
            if current_values and "Temperature Sensor" in current_values:
                sensor_data = current_values["Temperature Sensor"]
                param_name = entity_info["param"]["name"]
                current_value = sensor_data.get(param_name)

            self.hass.bus.async_fire(
                f"{self.domain}_sensor_discovered",
                {
                    "node_id": node_id,
                    "sensor_name": entity_info.get(
                        "sensor_name",
                        f"{entity_info['device_name']}_{entity_info['sensor_type']}",
                    ),
                    "sensor_type": entity_info["sensor_type"],
                    "param": entity_info["param"],
                    "current_value": current_value,
                    "device_info": device_info,
                    "unit_of_measurement": entity_info.get("unit_of_measurement", ""),
                    "device_class": entity_info.get("device_class"),
                },
            )

    def _trigger_controller_discovery(
        self, node_id: str, platform_type: str, entity_info: dict, device_info: dict
    ):
        """Trigger controller entity discovery.

        Args:
            node_id: Device node ID
            platform_type: Platform type (imu_gesture, interactive_input, etc.)
            entity_info: Entity information dictionary
            device_info: Device information dictionary
        """
        self.hass.bus.async_fire(
            f"{self.domain}_{platform_type}_discovered",
            {
                "node_id": node_id,
                "device_name": entity_info["device_name"],
                "entity_name": entity_info.get(
                    "entity_name", f"{platform_type}_controller"
                ),
                "capabilities": entity_info.get("capabilities", {}),
                "params": entity_info.get("params", []),
                "device_info": device_info,
            },
        )

    async def parse_and_discover_entities(
        self, node_id: str, properties: list, preferred_device_name: str | None = None
    ):
        """Parse device configuration and trigger entity discovery.

        Args:
            node_id: Device node ID
            properties: List of device properties
            preferred_device_name: Optional preferred device name from config entry
        """
        try:
            config_property = None
            for config_name in CONFIG_PROPERTY_NAMES:
                for prop in properties:
                    if prop.get("name") == config_name and isinstance(
                        prop.get("value"), (bytes, str, dict)
                    ):
                        config_property = prop
                        break
                if config_property:
                    break

            # Get preferred device name from config entry if not provided
            if preferred_device_name is None:
                node_id_lower = node_id.lower()
                if node_id_lower in self._device_config_entries:
                    entry_id = self._device_config_entries[node_id_lower]
                    config_entry = self.hass.config_entries.async_get_entry(entry_id)
                    if config_entry and config_entry.title:
                        preferred_device_name = config_entry.title

            if not config_property:
                _LOGGER.warning(
                    "Device %s has no configuration property, attempting basic discovery",
                    node_id,
                )
                device_config = create_basic_device_config_from_properties(properties)
            else:
                config_value = config_property["value"]
                if isinstance(config_value, dict):
                    config_str = json.dumps(config_value)
                    device_config = self._device_parser.parse_device_config(
                        config_str, preferred_device_name
                    )
                elif isinstance(config_value, str):
                    device_config = self._device_parser.parse_device_config(
                        config_value, preferred_device_name
                    )
                else:
                    device_config = self._device_parser.parse_device_config(
                        config_value, preferred_device_name
                    )

            if not device_config:
                _LOGGER.warning("Device %s configuration parsing failed", node_id)
                return

            current_values = self._extract_current_values(properties)
            device_info = device_config.get("device_info", {})

            if node_id in self.devices:
                self.devices[node_id].update(
                    {
                        "device_info": device_info,
                        "parsed_config": device_config,
                        "current_values": current_values,
                    }
                )

            await self._trigger_platform_discovery(
                node_id, device_config, current_values, device_info
            )

        except Exception:
            _LOGGER.exception("Failed to parse device configuration for %s", node_id)

    def process_property_update(self, node_id: str, params_data: dict) -> None:
        """Process property update and fire events.

        Args:
            node_id: Device node ID
            params_data: Property data from ESP device
        """
        fire_property_events(self.hass, self.domain, node_id, params_data)

    async def _check_and_reconnect_devices(self) -> None:
        """Check device connections and reconnect if disconnected.

        This method:
        1. Checks if each device's socket is still connected
        2. If disconnected (e.g., ESP32 restarted), attempts to reconnect
        3. After successful reconnection, fetches properties to resync state

        Called periodically by the monitoring loop every 60 seconds.
        """
        for node_id, device_info in list(self.devices.items()):
            if not device_info.get("registered", False):
                continue

            # Check if we have a client for this device
            if node_id not in self._local_ctrl_clients:
                _LOGGER.warning(
                    "Device %s is registered but has no client (possible state inconsistency), "
                    "attempting to establish connection",
                    node_id,
                )
                # Try to establish connection to recover from inconsistent state
                ip = device_info.get("ip")
                port = device_info.get("port", self.default_port)
                if ip and await self.establish_local_ctrl_session(node_id, ip, port):
                    _LOGGER.info("Successfully connected to device %s", node_id)
                    # Fetch properties to resync state
                    try:
                        await self.get_local_ctrl_properties(node_id, skip_discovery=True)
                    except Exception as err:
                        _LOGGER.debug("Error fetching properties after reconnect: %s", err)
                continue

            # Check if existing client is still connected
            client = self._local_ctrl_clients[node_id]
            if not isinstance(client, ESPLocalCtrlClient):
                continue

            try:
                if not await client.is_connected():
                    _LOGGER.warning(
                        "Device %s disconnected (socket closed), attempting reconnect",
                        node_id,
                    )
                    # Clean up old client
                    await client.disconnect()
                    del self._local_ctrl_clients[node_id]
                    device_info["registered"] = False

                    # Attempt to reconnect
                    ip = device_info.get("ip")
                    port = device_info.get("port", self.default_port)
                    if ip and await self.establish_local_ctrl_session(node_id, ip, port):
                        device_info["registered"] = True
                        _LOGGER.info(
                            "Successfully reconnected to device %s after disconnect",
                            node_id,
                        )
                        # Fetch properties to resync state
                        try:
                            await self.get_local_ctrl_properties(node_id, skip_discovery=True)
                            _LOGGER.debug(
                                "Property resync completed for device %s after reconnect",
                                node_id,
                            )
                        except Exception as err:
                            _LOGGER.debug(
                                "Error fetching properties after reconnect: %s", err
                            )
                    else:
                        _LOGGER.warning(
                            "Failed to reconnect to device %s, will retry later",
                            node_id,
                        )

            except Exception as err:
                _LOGGER.warning(
                    "Error checking connection for device %s: %s",
                    node_id,
                    err,
                )

    async def cleanup(self) -> None:
        """Clean up all connections and resources."""
        if hasattr(self, "_monitoring_task") and self._monitoring_task:
            if not self._monitoring_task.done():
                self._monitoring_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._monitoring_task

        for node_id, client in list(self._local_ctrl_clients.items()):
            if isinstance(client, ESPLocalCtrlClient):
                try:
                    await client.disconnect()
                except Exception as err:
                    _LOGGER.warning("Error disconnecting device %s: %s", node_id, err)

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        # Note: cleanup() is now a method of this class, no separate coordinator

    async def establish_local_ctrl_session(
        self, node_id: str, ip: str, port: int | None = None
    ) -> bool:
        """Establish ESP Local Control session with the device.

        Creates or reuses an ESP Local Control client connection to the device.
        Uses locks to prevent concurrent connection attempts to the same device.
        Retrieves and applies configured PoP (Proof of Possession) if available.

        Args:
            node_id: Unique device identifier
            ip: Device IP address
            port: Device port (default: from self.default_port)

        Returns:
            True if connection established successfully, False otherwise
        """
        if port is None:
            port = self.default_port

        # Ensure we have a lock for this device
        if node_id not in self._connection_locks:
            self._connection_locks[node_id] = asyncio.Lock()

        async with self._connection_locks[node_id]:
            if node_id in self._local_ctrl_clients:
                existing_client = self._local_ctrl_clients[node_id]
                if await existing_client.is_connected():
                    return True
                await existing_client.disconnect()
                del self._local_ctrl_clients[node_id]

            try:
                pop_value = self._get_configured_pop(node_id)
                security_version = self._get_configured_security_version(node_id)

                client = ESPLocalCtrlClient(
                    node_id,
                    ip,
                    port=port,
                    pop=pop_value,
                    security_mode=security_version,
                )

                client.add_message_callback(self._create_message_handler(node_id))

                if await client.connect():
                    self._local_ctrl_clients[node_id] = client
                    return True

                _LOGGER.warning(
                    "Failed to connect to device %s at %s:%s", node_id, ip, port
                )
                await client.disconnect()
                return False

            except ConnectionError as err:
                _LOGGER.error(
                    "Connection error for device %s at %s:%s: %s",
                    node_id,
                    ip,
                    port,
                    err,
                )
                return False
            except Exception:
                _LOGGER.exception(
                    "Unexpected error connecting to device %s at %s:%s",
                    node_id,
                    ip,
                    port,
                )
                return False

    def _get_configured_pop(self, node_id: str) -> str:
        """Get the configured PoP value for a device."""
        for entry in self.hass.config_entries.async_entries(self.domain):
            entry_node_id = entry.data.get("node_id", "")
            if entry_node_id == node_id:
                custom_pop = entry.data.get("pop", "") or entry.data.get(
                    "custom_pop", ""
                )
                return custom_pop if custom_pop else ""

        return ""

    def _get_configured_security_version(self, node_id: str) -> int:
        """Get the configured security version for a device.

        Returns:
            Security version (0 = no security, 1 = with security).
            Defaults to 1 if not found in config.
        """
        for entry in self.hass.config_entries.async_entries(self.domain):
            entry_node_id = entry.data.get("node_id", "")
            if entry_node_id == node_id:
                return entry.data.get("security_version", 1)

        # Default to security 1 (secure) if not found
        return 1

    async def get_local_ctrl_properties(
        self, node_id: str, skip_discovery: bool = False
    ) -> list:
        """Get all properties from ESP32 device using ESP Local Control client."""
        if node_id not in self._property_fetch_locks:
            self._property_fetch_locks[node_id] = asyncio.Lock()

        async with self._property_fetch_locks[node_id]:
            if node_id not in self._local_ctrl_clients:
                if node_id in self.devices:
                    device_port = self.devices[node_id].get("port", self.default_port)
                    if not await self.establish_local_ctrl_session(
                        node_id, self.devices[node_id]["ip"], device_port
                    ):
                        return []
                else:
                    return []

            try:
                client_info = self._local_ctrl_clients[node_id]

                if isinstance(client_info, ESPLocalCtrlClient):
                    properties = await client_info.get_property_values()
                    if properties:
                        if (
                            not skip_discovery
                            and not self.is_discovery_completed(node_id)
                        ):
                            await self.parse_and_discover_entities(
                                node_id, properties
                            )
                            self.mark_discovery_completed(node_id)

                        return [
                            {
                                "device": "Light",
                                "name": prop["name"],
                                "value": prop["value"],
                                "type": "bool"
                                if prop["type"] == 1  # PROP_TYPE_BOOLEAN
                                else "int",
                            }
                            for prop in properties
                        ]

                return []

            except Exception:
                _LOGGER.exception("Error getting local ctrl properties for %s", node_id)
                return []

    async def set_local_ctrl_property(
        self, node_id: str, prop_name: str, value
    ) -> bool:
        """Set ESP32 device property value using ESP Device Controller."""
        # Ensure we have a property-setting lock for this device to prevent concurrent modifications
        if node_id not in self._property_fetch_locks:
            self._property_fetch_locks[node_id] = asyncio.Lock()

        # Use the same lock as property fetch to serialize all operations
        async with self._property_fetch_locks[node_id]:
            if node_id not in self._local_ctrl_clients:
                if node_id in self.devices:
                    device_port = self.devices[node_id].get("port", self.default_port)
                    if not await self.establish_local_ctrl_session(
                        node_id, self.devices[node_id]["ip"], device_port
                    ):
                        return False
                else:
                    return False

            try:
                client_info = self._local_ctrl_clients[node_id]

                if isinstance(client_info, ESPLocalCtrlClient):
                    if not await client_info.is_connected():
                        _LOGGER.debug(
                            "Device %s not connected, attempting to connect",
                            node_id
                        )
                        if not await client_info.connect():
                            return False

                    device_controller = ESPDeviceController(client_info)

                    # Try to set property with timeout to detect connection failures faster
                    try:
                        # Use shorter timeout (2s) to detect connection problems quickly
                        success = await asyncio.wait_for(
                            device_controller.set_device_property(prop_name, value),
                            timeout=2.0
                        )
                        if success:
                            # Success: socket is working, ESP32 confirmed receipt
                            if "properties" not in self.devices[node_id]:
                                self.devices[node_id]["properties"] = {}
                            self.devices[node_id]["properties"][prop_name] = value
                            return True
                    except TimeoutError:
                        _LOGGER.warning(
                            "Property set timeout for device %s",
                            node_id,
                        )

                    # Failed or timeout: check if connection is still alive
                    connection_alive = await client_info.is_connected()
                    # Connection is DEAD - need to reconnect
                    if not connection_alive:
                        _LOGGER.info(
                            "Connection lost for device %s, attempting reconnect",
                            node_id,
                        )
                        # Clean up broken connection (will clear session)
                        await client_info.disconnect()

                        # Reconnect and re-establish session
                        if await client_info.connect():
                            # Create new device controller with fresh session
                            device_controller = ESPDeviceController(client_info)

                            # Retry once after reconnection
                            if await device_controller.set_device_property(prop_name, value):
                                _LOGGER.info(
                                    "Property set succeeded after reconnect for device %s",
                                    node_id
                                )
                                if "properties" not in self.devices[node_id]:
                                    self.devices[node_id]["properties"] = {}
                                self.devices[node_id]["properties"][prop_name] = value
                                return True
                            _LOGGER.warning(
                                "Property set failed after reconnect for device %s",
                                node_id,
                            )
                        else:
                            _LOGGER.error(
                                "Failed to reconnect to device %s",
                                node_id,
                            )

                    return False

            except Exception:
                return False

    async def register_device(self, node_id: str, ip: str, port: int | None = None) -> bool:
        """Register device."""
        if port is None:
            port = self.default_port

        node_id = node_id.lower()

        if node_id not in self.devices:
            self.devices[node_id] = {
                "ip": ip,
                "port": port,
                "node_id": node_id,
                "registered": False,
            }

        device_info = self.devices[node_id]

        if await self.establish_local_ctrl_session(node_id, ip, port):
            device_info["registered"] = True
            device_info["last_success"] = asyncio.get_event_loop().time()
            return True

        return False

    async def unregister_device(self, node_id: str) -> None:
        """Unregister and clean up a device.

        This method:
        1. Removes device from devices dict (stored with lowercase node_id)
        2. Cleans up all tracking data
        3. Disconnects local control client
        4. Clears discovery cache (for both original and lowercase variants)

        Args:
            node_id: Device node ID to unregister (as stored in config entry)
        """
        node_id_lower = node_id.lower()

        # Remove device from devices dict (uses lowercase internally)
        self.devices.pop(node_id, None)
        self.devices.pop(node_id_lower, None)

        # Clean up device tracking data
        self._property_fetch_locks.pop(node_id_lower, None)
        self._connection_locks.pop(node_id, None)
        self._connection_locks.pop(node_id_lower, None)

        # Clean up cached discovery data from hass.data
        cache_keys = [
            f"discovered_sensors_{node_id}",
            f"discovered_sensors_{node_id_lower}",
            f"discovered_numbers_{node_id}",
            f"discovered_numbers_{node_id_lower}",
            f"discovered_lights_{node_id}",
            f"discovered_lights_{node_id_lower}",
            f"discovered_binary_sensors_{node_id}",
            f"discovered_binary_sensors_{node_id_lower}",
        ]
        for cache_key in cache_keys:
            self.hass.data.get(self.domain, {}).pop(cache_key, None)

        # Disconnect local control client (both variants)
        for key in [node_id, node_id_lower]:
            if key in self._local_ctrl_clients:
                try:
                    client = self._local_ctrl_clients[key]
                    await client.disconnect()
                except Exception as err:
                    _LOGGER.debug("Error disconnecting client for %s: %s", key, err)
                finally:
                    self._local_ctrl_clients.pop(key, None)

        _LOGGER.debug("Unregistered device %s", node_id)

    async def send_command(self, cmd: str, node_id: str) -> bool:
        """Send control command to device."""
        return await self.set_local_ctrl_property(node_id, "Power", cmd == "on")

    async def send_light_command(self, command_data: dict, node_id: str) -> bool:
        """Send light control command to device."""
        if "command" in command_data:
            cmd = command_data["command"]
            success = True

            if "power" in cmd:
                success &= await self.set_local_ctrl_property(
                    node_id, "Power", cmd["power"]
                )
            if "brightness" in cmd:
                success &= await self.set_local_ctrl_property(
                    node_id, "Brightness", cmd["brightness"]
                )
            if "hue" in cmd:
                success &= await self.set_local_ctrl_property(
                    node_id, "Hue", cmd["hue"]
                )
            if "saturation" in cmd:
                success &= await self.set_local_ctrl_property(
                    node_id, "Saturation", cmd["saturation"]
                )
            if "color_temp" in cmd:
                success &= await self.set_local_ctrl_property(
                    node_id, "CCT", cmd["color_temp"]
                )

            return success
        return False

    async def set_binary_sensor_config(self, node_id: str, config: dict) -> bool:
        """Set binary sensor configuration."""
        try:
            success = True
            if "device_class" in config:
                success &= await self.set_local_ctrl_property(
                    node_id, "device_class", config["device_class"]
                )
            if "debounce_time" in config:
                success &= await self.set_local_ctrl_property(
                    node_id, "debounce_time", config["debounce_time"]
                )
            if "report_interval" in config:
                success &= await self.set_local_ctrl_property(
                    node_id, "report_interval", config["report_interval"]
                )
            return success
        except Exception as err:
            _LOGGER.error("Failed to set binary sensor configuration: %s", err)
            return False

    async def send_device_command(
        self, device_node_id: str, command_type: str, parameters: dict
    ) -> bool:
        """Send device command for threshold configuration or other operations."""
        try:
            device_node_id = device_node_id.replace(":", "").lower()

            if command_type == "set_thresholds":
                success_count = 0
                for param_name, param_value in parameters.items():
                    if await self.set_local_ctrl_property(
                        device_node_id, param_name, param_value
                    ):
                        success_count += 1

                return success_count > (len(parameters) * 0.5)

            if command_type == "set_battery_low_threshold":
                try:
                    low_th = int(parameters.get("low_threshold"))
                except Exception:
                    return False
                return await self.set_local_ctrl_property(
                    device_node_id, "Battery Low Threshold", low_th
                )

            if command_type == "set_sampling_interval":
                source = parameters.get("source", "")
                param_name = (
                    "Battery Sampling Interval"
                    if source == "battery_energy"
                    else parameters.get("param_name", "Report Interval")
                )

                try:
                    interval = int(parameters.get("interval_sec", 30))
                except Exception:
                    return False
                return await self.set_local_ctrl_property(
                    device_node_id, param_name, interval
                )

            return False

        except Exception:
            return False

    async def start_services(self, enable_discovery: bool = True) -> None:
        """Start essential background services."""
        if enable_discovery and not self.devices:
            self._zc = await zeroconf.async_get_instance(self.hass)
            self._browser = ServiceBrowser(
                self._zc,
                "_esp_local_ctrl._tcp.local.",
                ESPDeviceListener(self.hass, self),
            )

        self._monitoring_task = self.hass.async_create_task(
            self._combined_monitoring_loop()
        )

    def _create_message_handler(self, node_id: str):
        """Create a message handler callback for a specific device.

        This factory function creates a closure that captures the node_id,
        allowing the async callback to properly handle messages for this device.

        Args:
            node_id: Device node_id

        Returns:
            Async callback function that handles (msg_source, data) tuples
        """

        async def on_esp32_message(msg_source, data):
            """Handle incoming message from ESP32 device.

            Args:
                msg_source: Message source (ACTIVE_REPORT or QUERY_RESPONSE)
                data: Parsed message data containing properties
            """
            try:
                MessageSource = esp_local_ctrl.MessageSource

                if msg_source == MessageSource.ACTIVE_REPORT:
                    _LOGGER.debug(
                        "Active report from device %s: %s",
                        node_id,
                        data,
                    )
                    # Process active report data same as query response
                    if isinstance(data, dict) and "properties" in data:
                        properties = data.get("properties", [])
                        await self._process_active_report_values(node_id, properties)

                elif msg_source == MessageSource.QUERY_RESPONSE:
                    # Query responses are routed via futures in the HTTP listener
                    # This branch should not normally be reached
                    _LOGGER.debug(
                        "QUERY_RESPONSE routed to callback for device %s - "
                        "should have been handled via future",
                        node_id,
                    )

            except Exception:
                _LOGGER.exception("Error handling message from device %s", node_id)

        return on_esp32_message

    async def _process_active_report_values(
        self, node_id: str, properties: list
    ) -> None:
        """Process active report property values and fire update events."""
        try:
            for prop in properties:
                if not isinstance(prop, dict):
                    continue

                prop_name = prop.get("name", "")
                if prop_name and prop_name != "params":
                    continue

                prop_value = prop.get("value")
                if not prop_value:
                    continue

                # Decode the value (handles bytes/string/dict formats)
                params_data = None
                if isinstance(prop_value, bytes):
                    try:
                        prop_value_str = prop_value.decode("utf-8")
                        if prop_value_str.startswith("{"):
                            params_data = json.loads(prop_value_str)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        try:
                            prop_value_str = prop_value.decode("latin-1")
                            if prop_value_str.startswith("{"):
                                params_data = json.loads(prop_value_str)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            _LOGGER.debug(
                                "Failed to parse active report value for %s", node_id
                            )
                            continue
                elif isinstance(prop_value, str):
                    try:
                        if prop_value.startswith("{"):
                            params_data = json.loads(prop_value)
                    except json.JSONDecodeError:
                        _LOGGER.debug(
                            "Failed to parse active report JSON string for %s", node_id
                        )
                        continue
                elif isinstance(prop_value, dict):
                    params_data = prop_value

                if not params_data:
                    continue

                _LOGGER.debug(
                    "Processing active report data for %s: %s", node_id, params_data
                )

                # Process property update and fire all property update events
                self.process_property_update(node_id, params_data)
                break  # Process first valid property only

        except Exception:
            _LOGGER.exception("Error processing active report for %s", node_id)

    async def _combined_monitoring_loop(self) -> None:
        """Combined monitoring loop handling all device management tasks.

        This loop:
        1. ALWAYS performs initial discovery for each device on startup
        2. Optionally continues periodic polling (if ENABLE_QUERY_POLLING=True)
        3. Checks connections every 60 seconds (12 cycles * 5 seconds)
        4. Detects disconnections and reconnects automatically
        5. Continues until cancelled

        Runs as a background task started by start_services().
        """
        # ✅ TEMPORARY TEST FLAG: Control polling behavior AFTER initial discovery
        # - True = continue periodic queries after discovery
        # - False = stop querying after initial discovery, rely on active reports only
        ENABLE_QUERY_POLLING = False  # ← Change to True to re-enable continuous polling

        # Track initial discovery per device
        discovery_completed_devices = set()
        discovery_logged = False

        await asyncio.sleep(5)  # MONITORING_LOOP_INTERVAL

        connection_check_counter = 0

        while True:
            try:
                # ✅ PHASE 1: Always perform initial discovery for new/reconnected devices
                # This serves as a BACKUP in case the immediate query in update_device() failed.
                # Reasons a device might need backup discovery:
                # 1. Device wasn't fully ready when Zeroconf triggered update_device()
                # 2. Network timeout during immediate query
                # 3. Device manually added via UI (no Zeroconf trigger)
                # 4. Home Assistant restarted while device was offline
                for node_id, device_info in list(self.devices.items()):
                    if not device_info.get("registered", False):
                        continue

                    # Initial discovery for devices that haven't been discovered yet
                    if node_id not in discovery_completed_devices:
                        try:
                            await self._update_device_states(node_id)
                            discovery_completed_devices.add(node_id)
                        except Exception as err:
                            _LOGGER.warning(
                                "Error during initial discovery for device %s: %s",
                                node_id,
                                err,
                            )
                
                # ✅ PHASE 2: Periodic polling (only if enabled AND after initial discovery)
                if ENABLE_QUERY_POLLING:
                    # Continue periodic queries for already-discovered devices
                    for node_id, device_info in list(self.devices.items()):
                        if device_info.get("registered", False) and node_id in discovery_completed_devices:
                            try:
                                await self._update_device_states(node_id)
                            except Exception as err:
                                _LOGGER.debug(
                                    "Error updating device %s state: %s",
                                    node_id,
                                    err,
                                )

                # Log completion message once all devices are discovered
                if (
                    not discovery_logged
                    and discovery_completed_devices
                    and len(discovery_completed_devices)
                    == len(
                        [
                            d
                            for d in self.devices.values()
                            if d.get("registered", False)
                        ]
                    )
                ):
                    _LOGGER.info(
                        "Entity discovery complete for %d device(s)",
                        len(discovery_completed_devices),
                    )
                    discovery_logged = True

                # Periodically check and maintain connections
                connection_check_counter += 1
                if connection_check_counter >= 12:  # CONNECTION_CHECK_CYCLES (60 seconds)
                    connection_check_counter = 0
                    await self._check_and_reconnect_devices()

                await asyncio.sleep(5)  # MONITORING_LOOP_INTERVAL

            except asyncio.CancelledError:
                _LOGGER.debug("Monitoring loop cancelled")
                break
            except Exception as err:
                _LOGGER.error("Error in monitoring loop: %s", err)
                await asyncio.sleep(5)  # MONITORING_LOOP_INTERVAL

    async def update_device(
        self,
        node_id: str,
        ip: str,
        port: int | None = None,
        security_info: dict | None = None,
    ):
        """Update or add device to device list and trigger entity discovery."""
        if port is None:
            port = self.default_port

        node_id = str(node_id).replace(":", "").lower()

        existing_entries = self.hass.config_entries.async_entries(self.domain)
        for entry in existing_entries:
            if entry.unique_id == node_id:
                config_port = entry.data.get(CONF_PORT)
                config_host = entry.data.get(CONF_HOST)
                if config_port != port or config_host != ip:
                    new_data = {**entry.data}
                    new_data[CONF_HOST] = ip
                    new_data[CONF_PORT] = port
                    self.hass.config_entries.async_update_entry(entry, data=new_data)
                break

        device_data = {"ip": ip, "port": port, "node_id": node_id, "registered": False}
        if security_info:
            device_data["security_info"] = security_info

        self.devices[node_id] = device_data

        with contextlib.suppress(Exception):
            await self.register_device(node_id, ip, port)

        # Immediate property query is critical to prevent race condition:
        # ESP32 sends active reports immediately after session establishment.
        # If these arrive before we know the device structure, decryption fails.
        # This query must happen as soon as possible after register_device().
        with contextlib.suppress(Exception):
            await self.get_local_ctrl_properties(node_id)

    async def get_state(self, node_id: str) -> bool:
        """Get device state."""
        if node_id not in self.devices:
            return False

        try:
            properties = await self.get_local_ctrl_properties(
                node_id, skip_discovery=True
            )

            for prop in properties:
                if prop.get("device") == "Light" and prop.get("name") == "Power":
                    return prop.get("value", False)

            return False

        except Exception:
            return False

    async def _update_device_states(self, node_id: str):
        """Update device states - including lights, sensors and binary sensors."""
        try:
            # Check rate limiting
            if self.should_rate_limit(node_id):
                return

            skip_discovery = self.is_discovery_completed(node_id)
            properties = await self.get_local_ctrl_properties(
                node_id, skip_discovery=skip_discovery
            )

            if not properties:
                return

            for prop in properties:
                if prop.get("name") != "params":
                    continue

                try:
                    if isinstance(prop.get("value"), bytes):
                        params_str = prop["value"].decode("latin-1")
                        if not params_str.startswith("{"):
                            continue
                        params_data = json.loads(params_str)
                    elif isinstance(prop.get("value"), str):
                        params_str = prop["value"]
                        if not params_str.startswith("{"):
                            continue
                        params_data = json.loads(params_str)
                    elif isinstance(prop.get("value"), dict):
                        params_data = prop["value"]
                    else:
                        continue

                    # Use the coordinator's shared event firing function
                    fire_property_events(self.hass, self.domain, node_id, params_data)
                    break

                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

        except Exception:
            pass
