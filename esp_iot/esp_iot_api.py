"""ESP Home API - Main coordinator for ESP device management.

This module provides the ESPHomeAPI class which manages ESP device connections,
property fetching, state updates, and command sending.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
from typing import Any

import aiohttp
from zeroconf import ServiceBrowser

from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .esp_local_ctrl_lib import esp_local_ctrl
from .esp_iot_discovery import create_basic_device_config_from_properties
from .esp_iot_local_ctrl import ESPLocalCtrlClient
from .esp_iot_network import DEVICE_SERVICE_TYPE, ESPDeviceListener, PROTO
from .esp_iot_parser import ESPDeviceParser
from .esp_iot_payload import ESPDeviceController
from .esp_iot_spec import CONFIG_PROPERTY_NAMES
from .esp_iot_utils import fire_property_events

_LOGGER = logging.getLogger(__name__)

# Connection and timing constants
RATE_LIMIT_INTERVAL = 10  # seconds between property calls per device
MDNS_CHECK_TIMEOUT = 8.0  # seconds to wait for mDNS service detection (increased from 3.0 for reliability)


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
        _property_fetch_locks: Unified locks to prevent concurrent operations (connections + property fetches)
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

        # Concurrency control - unified lock for both connections and property operations
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

    def is_device_available(self, node_id: str) -> bool:
        """Check if device is available (registered and has active client).

        Args:
            node_id: Device node ID

        Returns:
            True if device is available, False otherwise
        """
        node_id_lower = node_id.lower()
        
        # Check if device is registered
        if node_id_lower in self.devices:
            device_info = self.devices[node_id_lower]
            if not device_info.get("registered", False):
                return False
            
            # Check if we have an active client
            return node_id_lower in self._local_ctrl_clients
        
        return False

    async def _check_tcp_port_ready(
        self, host: str, port: int, timeout: float = 2.0
    ) -> bool:
        """Check if TCP port is accepting connections.
        
        This verifies the HTTP server is actually ready, not just mDNS broadcasting.
        Prevents race condition where mDNS advertises availability before HTTP server starts.
        
        Args:
            host: IP address or hostname
            port: TCP port to check
            timeout: Connection timeout in seconds
            
        Returns:
            True if port accepts connections, False otherwise
        """
        try:
            # Attempt TCP connection to verify port is ready
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            # Connection successful - close it immediately
            writer.close()
            await writer.wait_closed()
            return True
        except (TimeoutError, ConnectionRefusedError, OSError):
            # Port not ready yet
            return False
        except Exception as err:
            _LOGGER.debug(
                "Unexpected error checking TCP port %s:%d: %s", host, port, err
            )
            return False

    async def check_device_mdns_available(
        self, node_id: str, expected_ip: str | None = None
    ) -> bool:
        """Check if device's mDNS service is broadcasting (device is online).

        Creates a dedicated ServiceBrowser to detect this specific device via mDNS.
        ESP32 devices broadcast mDNS on startup (2-3 times), then only respond to queries.
        ServiceBrowser automatically sends queries, triggering ESP32 to respond.

        Args:
            node_id: Device node ID to check
            expected_ip: Optional expected IP address to verify

        Returns:
            True if device mDNS detected and TCP ready, False otherwise
        """
        node_id = node_id.lower()
        service_name = f"{node_id.upper()}._esp_local_ctrl._tcp.local."
        
        _LOGGER.debug(
            "Creating dedicated mDNS listener for device %s (service: %s)",
            node_id,
            service_name,
        )

        detected = asyncio.Event()
        detected_ip = None

        class TempListener:
            """Temporary listener for this specific device."""
            
            def add_service(self, zc, type_, name):
                nonlocal detected_ip
                if name.lower() == service_name.lower():
                    _LOGGER.info("mDNS service added: %s", name)
                    info = zc.get_service_info(type_, name)
                    if info and info.addresses:
                        detected_ip = socket.inet_ntoa(info.addresses[0])
                        _LOGGER.info("Device %s detected via mDNS at %s", node_id, detected_ip)
                        detected.set()

            def update_service(self, zc, type_, name):
                nonlocal detected_ip
                if name.lower() == service_name.lower():
                    _LOGGER.debug("mDNS service updated: %s", name)
                    info = zc.get_service_info(type_, name)
                    if info and info.addresses:
                        detected_ip = socket.inet_ntoa(info.addresses[0])
                        _LOGGER.info("Device %s detected via mDNS update at %s", node_id, detected_ip)
                        detected.set()

            def remove_service(self, zc, type_, name):
                if name.lower() == service_name.lower():
                    _LOGGER.debug("mDNS service removed: %s", name)

        try:
            from homeassistant.components import zeroconf as ha_zeroconf
            
            zc = await ha_zeroconf.async_get_instance(self.hass)
            listener = TempListener()
            
            # Create ServiceBrowser - this will send mDNS query immediately
            browser = ServiceBrowser(zc, "_esp_local_ctrl._tcp.local.", listener)
            
            try:
                # Wait up to 3 seconds for mDNS detection
                # ESP32 will respond within 1-2 seconds if it's online
                # Short timeout for fast response, but keep browser alive for delayed responses
                await asyncio.wait_for(detected.wait(), timeout=3.0)
                
                _LOGGER.info(
                    "Device %s detected via mDNS at %s, verifying TCP connectivity",
                    node_id,
                    detected_ip,
                )
                
                # Verify detected IP matches expected (if provided)
                if expected_ip and detected_ip != expected_ip:
                    _LOGGER.warning(
                        "Device %s IP changed: expected %s, detected %s",
                        node_id,
                        expected_ip,
                        detected_ip,
                    )
                    # Update device registry with new IP
                    if node_id in self.devices:
                        self.devices[node_id]["host"] = detected_ip
                        self.devices[node_id]["ip"] = detected_ip
                
                # Use detected IP or fall back to registry
                device_ip = detected_ip
                if not device_ip:
                    device_info = self.devices.get(node_id)
                    if device_info:
                        device_ip = device_info.get("host")
                
                if not device_ip:
                    _LOGGER.warning("No IP address available for device %s", node_id)
                    browser.cancel()
                    return False
                
                # Verify TCP port is ready (mDNS may broadcast before HTTP server ready)
                # ESP32 needs time after restart to fully boot and start HTTP server
                # mDNS service starts quickly (~2s) but HTTP server needs ~30s
                # Retry with 2s intervals to allow sufficient ESP32 boot time
                max_retries = 15  # 15 retries × 2s = 30s max wait
                for retry_count in range(max_retries):
                    tcp_ready = await self._check_tcp_port_ready(device_ip, 8080, timeout=1.0)
                    if tcp_ready:
                        _LOGGER.info(
                            "Device %s TCP port ready after %.1f seconds, device is online",
                            node_id,
                            retry_count * 2.0,
                        )
                        browser.cancel()  # Cancel immediately when device is confirmed online
                        return True
                    
                    if retry_count < max_retries - 1:
                        await asyncio.sleep(2.0)
                
                # Port still not ready after retries
                _LOGGER.warning(
                    "Device %s mDNS detected but TCP port not ready after %.1f seconds",
                    node_id,
                    max_retries * 2.0,
                )
                browser.cancel()
                return False
                
            except TimeoutError:
                # CRITICAL: Don't cancel browser immediately!
                # ESP32 might be booting and will respond to our query in 10-30 seconds
                # Keep the browser alive so it can receive the delayed response
                _LOGGER.info(
                    "Device %s not detected within 3 seconds, but keeping mDNS listener active for delayed response",
                    node_id,
                )
                
                # Schedule browser cleanup after 30 seconds in the background
                # This allows ESP32 boot time while not blocking the caller
                async def delayed_cleanup():
                    await asyncio.sleep(30)
                    browser.cancel()
                    _LOGGER.debug("Cleaned up mDNS browser for device %s after 30s", node_id)
                
                self.hass.async_create_task(delayed_cleanup())
                
                # Return False immediately so caller can continue with fast retry cycle
                # If ESP32 responds within 30s, global listener will trigger update_device()
                return False
                
        except Exception as err:
            _LOGGER.warning("Error checking mDNS availability for device %s: %s", node_id, err)
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
                # ✅ OPTIMIZED: Extract ALL device types from params_data
                # Don't limit to DEVICE_TYPES list - ESP32 may send new device types
                for device_type, device_data in params_data.items():
                    # Skip non-dict values (metadata fields)
                    if not isinstance(device_data, dict):
                        # Handle special case: "State" field for binary sensors
                        if device_type == "State":
                            current_values["Binary Sensor"] = {
                                "Binary Sensor": device_data
                            }
                            _LOGGER.debug(
                                "Extracted Binary Sensor state from property: %s",
                                device_data,
                            )
                        continue
                    
                    # Store or merge device data
                    if device_type not in current_values:
                        current_values[device_type] = device_data
                    else:
                        # Merge if same device type appears in multiple properties
                        current_values[device_type].update(device_data)
                    
                    _LOGGER.debug(
                        "Extracted %s data from property: %s",
                        device_type,
                        device_data,
                    )

        # Return all collected values from ALL properties
        _LOGGER.debug(
            "Total extracted device types: %s",
            list(current_values.keys()),
        )
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
        _LOGGER.warning(
            "🔥 [PROPERTY_UPDATE] Starting to process property update for device %s with data types: %s",
            node_id,
            list(params_data.keys()),
        )
        fire_property_events(self.hass, self.domain, node_id, params_data)
        _LOGGER.warning(
            "🔥 [PROPERTY_UPDATE] Completed firing property events for device %s",
            node_id,
        )

    async def _check_and_reconnect_devices(self) -> None:
        """Check device connections and reconnect if disconnected.

        This method:
        1. Checks if each device's socket is still connected
        2. If disconnected (e.g., ESP32 restarted), attempts to reconnect
        3. After successful reconnection, fetches properties to resync state

        Called periodically by the monitoring loop every 60 seconds.
        """
        for node_id, device_info in list(self.devices.items()):
            # ✅ FIXED: Don't skip unregistered devices - they might be offline and need reconnection
            # Check if device is offline and needs reconnection attempt
            if not device_info.get("registered", False):
                # Device is marked as unregistered (offline)
                if device_info.get("offline_state", False):
                    _LOGGER.debug(
                        "Device %s is offline, attempting reconnection",
                        node_id,
                    )
                    ip = device_info.get("ip")
                    port = device_info.get("port", self.default_port)
                    
                    if not ip:
                        _LOGGER.warning("Device %s has no IP address, skipping reconnection", node_id)
                        continue
                    
                    mdns_available = await self.check_device_mdns_available(node_id, ip)
                    
                    if mdns_available:
                        _LOGGER.info(
                            "Device %s is back online, reconnecting",
                            node_id,
                        )
                        
                        # Ensure we have a lock for this device
                        if node_id not in self._property_fetch_locks:
                            self._property_fetch_locks[node_id] = asyncio.Lock()
                        
                        # Acquire lock before reconnection
                        async with self._property_fetch_locks[node_id]:
                            # Double-check: another operation might have already reconnected
                            if node_id in self._local_ctrl_clients:
                                check_client = self._local_ctrl_clients[node_id]
                                if await check_client.is_connected():
                                    _LOGGER.debug(
                                        "Device %s already reconnected by another operation during offline recovery",
                                        node_id,
                                    )
                                    device_info["registered"] = True
                                    device_info["offline_state"] = False
                                    continue
                            
                            device_info["registered"] = True
                            device_info["offline_state"] = False
                            
                            success, _ = await self._establish_and_sync_device(
                                node_id, ip, port, fire_online_event=True
                            )
                            if success:
                                _LOGGER.info("Successfully reconnected offline device %s", node_id)
                            else:
                                _LOGGER.warning("Failed to reconnect device %s, will retry", node_id)
                    else:
                        _LOGGER.debug("Device %s still offline (mDNS + TCP not available)", node_id)
                else:
                    # Device is unregistered but not marked offline (initial state)
                    _LOGGER.debug("Device %s is unregistered but not offline, skipping", node_id)
                continue

            # Check if we have a client for this device
            if node_id not in self._local_ctrl_clients:
                _LOGGER.warning(
                    "Device %s is registered but has no client (possible state inconsistency), "
                    "attempting to establish connection",
                    node_id,
                )
                
                # Ensure we have a lock for this device
                if node_id not in self._property_fetch_locks:
                    self._property_fetch_locks[node_id] = asyncio.Lock()
                
                # Acquire lock before establishing connection
                async with self._property_fetch_locks[node_id]:
                    # Double-check: another operation might have already created client
                    if node_id in self._local_ctrl_clients:
                        check_client = self._local_ctrl_clients[node_id]
                        if await check_client.is_connected():
                            _LOGGER.debug(
                                "Device %s client created by another operation, skipping",
                                node_id,
                            )
                            continue
                    
                    # Try to establish connection to recover from inconsistent state
                    ip = device_info.get("ip")
                    port = device_info.get("port", self.default_port)
                    success, _ = await self._establish_and_sync_device(node_id, ip, port)
                    if ip and success:
                        _LOGGER.info("Successfully connected and synced device %s", node_id)
                continue

            # Check if existing client is still connected
            client = self._local_ctrl_clients[node_id]
            if not isinstance(client, ESPLocalCtrlClient):
                continue

            try:
                # Check connection status outside any lock (quick check)
                if not await client.is_connected():
                    _LOGGER.info(
                        "Device %s disconnected (socket closed), attempting reconnect",
                        node_id,
                    )
                    
                    # Try to acquire property lock with timeout
                    # If manual operation is in progress, skip this cycle
                    if node_id not in self._property_fetch_locks:
                        self._property_fetch_locks[node_id] = asyncio.Lock()
                    
                    lock = self._property_fetch_locks[node_id]
                    
                    try:
                        # Try to acquire lock with 0.1s timeout
                        # Manual operations have priority - if they're running, skip
                        await asyncio.wait_for(
                            lock.acquire(),
                            timeout=0.1
                        )
                    except TimeoutError:
                        _LOGGER.warning(
                            "Device %s reconnection skipped - operation already in progress",
                            node_id,
                        )
                        continue
                    
                    try:
                        # Initialize variables for entity discovery after lock release
                        properties_for_discovery = None
                        needs_discovery = False
                        
                        # Double-check: another operation might have already reconnected
                        if node_id in self._local_ctrl_clients:
                            check_client = self._local_ctrl_clients[node_id]
                            if await check_client.is_connected():
                                _LOGGER.debug(
                                    "Device %s already reconnected by another operation",
                                    node_id,
                                )
                                device_info["registered"] = True
                                continue
                        
                        # Clean up old client
                        await client.disconnect()
                        # Wait for TCP FIN/RST to complete to avoid connection leaks
                        await asyncio.sleep(0.2)
                        if node_id in self._local_ctrl_clients:
                            del self._local_ctrl_clients[node_id]
                        
                        # ⚠️ DON'T set registered=False immediately!
                        # Keep it True during fast reconnection to prevent entities from becoming unavailable
                        # Only set to False if device is truly offline (mDNS + TCP not available)

                        # Clear discovery flag on reconnection to allow re-discovery
                        node_id_lower = node_id.lower()
                        self._discovery_completed.discard(node_id)
                        self._discovery_completed.discard(node_id_lower)

                        # Get device connection info
                        ip = device_info.get("ip")
                        port = device_info.get("port", self.default_port)
                        
                        # ✅ CORRECT LOGIC: Check mDNS + TCP availability first
                        # ESP32 socket disconnected scenarios:
                        # 1. ESP32 restarting (fast) → mDNS + TCP available → immediate reconnect
                        # 2. ESP32 powered off → mDNS + TCP not available → fire offline event
                        # 3. Network issue → mDNS + TCP might still be there → try reconnect
                        
                        _LOGGER.info(
                            "Device %s socket disconnected, checking mDNS + TCP availability",
                            node_id,
                        )
                        
                        # Check if device mDNS + TCP are still available
                        mdns_available = await self.check_device_mdns_available(node_id, ip)
                        
                        was_offline = device_info.get("offline_state", False)
                        
                        _LOGGER.debug(
                            "Device %s reconnection: mdns_available=%s, was_offline=%s",
                            node_id,
                            mdns_available,
                            was_offline,
                        )
                        
                        if mdns_available:
                            if was_offline:
                                _LOGGER.info(
                                    "Device %s was offline, now available, firing online event",
                                    node_id,
                                )
                                fire_online = True
                            else:
                                _LOGGER.debug(
                                    "Device %s fast reconnect, no online event",
                                    node_id,
                                )
                                fire_online = False
                            
                            if was_offline:
                                device_info["registered"] = True
                                device_info["offline_state"] = False
                            
                            success, properties_for_discovery = await self._establish_and_sync_device(
                                node_id, ip, port, fire_online_event=fire_online
                            )
                            
                            if ip and success:
                                if was_offline:
                                    _LOGGER.info(
                                        "Successfully reconnected device %s from offline state",
                                        node_id,
                                    )
                                else:
                                    _LOGGER.debug(
                                        "Successfully reconnected device %s",
                                        node_id,
                                    )
                                
                                # Check if discovery is needed
                                needs_discovery = (
                                    not self.is_discovery_completed(node_id)
                                    and properties_for_discovery is not None
                                )
                            else:
                                _LOGGER.warning(
                                    "Device %s reconnection failed despite mDNS + TCP available, will retry in 60s",
                                    node_id,
                                )
                                properties_for_discovery = None
                                needs_discovery = False
                        else:
                            if not was_offline:
                                _LOGGER.warning(
                                    "Device %s is offline",
                                    node_id,
                                )
                                
                                # Set registered=False ONLY when truly offline
                                device_info["registered"] = False
                                
                                # Fire device availability changed event to update all entities to unavailable
                                self.hass.bus.async_fire(
                                    f"{self.domain}_device_availability_changed",
                                    {
                                        "node_id": node_id,
                                        "available": False,
                                        "timestamp": asyncio.get_event_loop().time(),
                                    },
                                )
                                _LOGGER.info("Fired device_availability_changed event for %s (available=False, entities marked unavailable)", node_id)
                                
                                # Mark device as offline
                                device_info["offline_state"] = True
                            else:
                                _LOGGER.debug(
                                    "Device %s still offline (already in offline state, no duplicate event)",
                                    node_id,
                                )
                            
                            # Will retry in next 60s loop check cycle
                            # If mDNS + TCP come back, will reconnect and fire online event
                            properties_for_discovery = None
                            needs_discovery = False
                    finally:
                        # Always release the lock
                        lock.release()
                    
                    # Do entity discovery OUTSIDE the lock to avoid blocking
                    if needs_discovery and properties_for_discovery:
                        _LOGGER.debug("Starting entity discovery for %s", node_id)
                        try:
                            await self.parse_and_discover_entities(
                                node_id, properties_for_discovery
                            )
                            self.mark_discovery_completed(node_id)
                            _LOGGER.debug("Entity discovery completed for %s", node_id)
                        except Exception as err:
                            _LOGGER.warning(
                                "Error during entity discovery for %s: %s", node_id, err
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

    async def _establish_and_sync_device(
        self,
        node_id: str,
        ip: str,
        port: int,
        *,
        process_properties: bool = True,
        wait_after_sync: float = 0.0,
        fire_online_event: bool = False,
    ) -> tuple[bool, list | None]:
        """Establish connection and sync all device properties.
        
        This unified method handles device connection with property synchronization.
        Used across all connection scenarios to ensure consistent behavior.
        
        Args:
            node_id: Device node ID
            ip: Device IP address
            port: Device port
            process_properties: If True, process fetched properties (default: True)
                               Set to False if caller wants raw properties without processing
            wait_after_sync: Seconds to wait after sync (default: 0.0)
            fire_online_event: If True, fire device_availability_changed event (available=True) before sync (default: False)
                              Only set to True when recovering from offline state
        
        Returns:
            Tuple of (success: bool, properties: list | None)
            - success: True if connection and sync succeeded
            - properties: Fetched properties (None if fetch failed)
        """
        # Step 0: Check if already connected (prevent concurrent reconnections)
        if node_id in self._local_ctrl_clients:
            existing_client = self._local_ctrl_clients[node_id]
            if await existing_client.is_connected():
                _LOGGER.debug(
                    "Device %s already connected (concurrent reconnection avoided), skipping reconnection",
                    node_id
                )
        
        # Step 1: Establish connection
        if not await self.establish_local_ctrl_session(node_id, ip, port):
            return (False, None)
        
        # Step 2: Property synchronization
        # ✅ CRITICAL ORDER:
        # 1. Fire online event FIRST (if requested) → entities become available
        # 2. Then sync properties → entities update state while available
        # This ensures entities don't update state while still unavailable
        
        fetched_properties = None
        _LOGGER.debug(
            "Starting property sync for %s (fire_online_event=%s)",
            node_id,
            fire_online_event,
        )
        
        if fire_online_event:
            self.hass.bus.async_fire(
                f"{self.domain}_device_availability_changed",
                {
                    "node_id": node_id,
                    "available": True,
                    "timestamp": asyncio.get_event_loop().time(),
                },
            )
            _LOGGER.debug(
                "Fired device_availability_changed event for %s",
                node_id,
            )
        
        try:
            client = self._local_ctrl_clients.get(node_id)
            if client and isinstance(client, ESPLocalCtrlClient):
                properties = await client.get_property_values()
                if properties:
                    fetched_properties = properties
                    _LOGGER.debug(
                        "Fetched %d properties from device %s after connection",
                        len(properties),
                        node_id,
                    )
                    
                    # Only process if requested
                    if process_properties:
                        _LOGGER.debug(
                            "Processing properties for %s",
                            node_id,
                        )
                        # Extract and process property updates
                        current_values = self._extract_current_values(properties)
                        if current_values:
                            _LOGGER.warning(
                                "🔵 [SYNC] Extracted current values for %s: %s",
                                node_id,
                                list(current_values.keys()),
                            )
                            _LOGGER.warning(
                                "🔵 [SYNC] About to call process_property_update for %s...",
                                node_id,
                            )
                            self.process_property_update(node_id, current_values)
                            _LOGGER.warning(
                                "🔵 [SYNC] Completed process_property_update for %s - all entity states synced",
                                node_id,
                            )
                        else:
                            _LOGGER.warning(
                                "⚠️ [SYNC] Failed to extract property values for %s, but device is still online",
                                node_id,
                            )
                    else:
                        _LOGGER.warning(
                            "🔵 [SYNC] Skipping property processing for %s (process_properties=False)",
                            node_id,
                        )
                else:
                    _LOGGER.debug(
                        "No properties returned from device %s",
                        node_id,
                    )
            else:
                _LOGGER.debug(
                    "Client not found or invalid for device %s",
                    node_id,
                )
        except Exception as err:
            _LOGGER.warning(
                "Failed to sync properties after connection for %s: %s",
                node_id,
                err,
            )
        
        # Step 3: Optional wait for ESP32 to be ready
        if wait_after_sync > 0:
            await asyncio.sleep(wait_after_sync)
        
        return (True, fetched_properties)

    async def establish_local_ctrl_session(
        self, node_id: str, ip: str, port: int | None = None
    ) -> bool:
        """Establish ESP Local Control session with the device.

        Creates or reuses an ESP Local Control client connection to the device.
        
        ⚠️ IMPORTANT: This method does NOT acquire locks internally.
        Callers must hold _property_fetch_locks[node_id] before calling this method
        to prevent concurrent operations.

        Args:
            node_id: Unique device identifier
            ip: Device IP address
            port: Device port (default: from self.default_port)

        Returns:
            True if connection established successfully, False otherwise
        """
        if port is None:
            port = self.default_port

        # Check if already connected (caller should have already checked, but double-check)
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
        """Get all properties from ESP32 device using ESP Local Control client.
        
        This method ONLY fetches properties and prepares them for discovery.
        It does NOT fire events or process property updates.
        Caller is responsible for processing the returned properties.
        
        Args:
            node_id: Device node ID
            skip_discovery: If True, skip entity discovery even if not completed
        """
        if node_id not in self._property_fetch_locks:
            self._property_fetch_locks[node_id] = asyncio.Lock()
        
        # Store properties for discovery outside the lock
        properties_for_discovery = None
        needs_discovery = False
        
        async with self._property_fetch_locks[node_id]:
            properties = None
            
            # If client doesn't exist, establish connection and fetch properties
            if node_id not in self._local_ctrl_clients:
                if node_id not in self.devices:
                    return []
                    
                device_port = self.devices[node_id].get("port", self.default_port)
                ip = self.devices[node_id]["ip"]
                
                # ✅ OPTIMIZED: Let _establish_and_sync_device fetch properties
                # But skip processing - we only want the raw properties
                success, properties = await self._establish_and_sync_device(
                    node_id, ip, device_port,
                    process_properties=False,  # Fetch but don't process them
                    fire_online_event=False
                )
                if not success or not properties:
                    return []
            else:
                # Client exists, just fetch properties
                try:
                    client_info = self._local_ctrl_clients[node_id]

                    if not isinstance(client_info, ESPLocalCtrlClient):
                        return []
                        
                    properties = await client_info.get_property_values()
                    if not properties:
                        return []

                except Exception:
                    _LOGGER.exception("Error getting local ctrl properties for %s", node_id)
                    return []
            
            # Check if discovery is needed
            if (
                not skip_discovery
                and not self.is_discovery_completed(node_id)
            ):
                properties_for_discovery = properties
                needs_discovery = True

            # ✅ CLEAN: get_local_ctrl_properties ONLY fetches properties
            # It does NOT fire events or process updates
            # Caller (_update_device_states, etc.) is responsible for processing
        
        # Do entity discovery OUTSIDE the lock to avoid blocking other operations
        # Entity discovery can take 10-20 seconds as it creates many entities
        if needs_discovery and properties_for_discovery:
            _LOGGER.debug("Starting entity discovery for device %s", node_id)
            try:
                await self.parse_and_discover_entities(
                    node_id, properties_for_discovery
                )
                self.mark_discovery_completed(node_id)
                _LOGGER.debug("Entity discovery completed for device %s", node_id)
            except Exception:
                _LOGGER.exception("Error during entity discovery for %s", node_id)
        
        return properties if properties else []

    async def set_local_ctrl_property(
        self, node_id: str, prop_name: str, value
    ) -> bool:
        """Set ESP32 device property value using ESP Device Controller."""
        
        # Ensure we have a property-setting lock for this device to prevent concurrent modifications
        if node_id not in self._property_fetch_locks:
            self._property_fetch_locks[node_id] = asyncio.Lock()
        
        # Track if we need to handle reconnection after releasing lock
        need_reconnect = False

        # Use the same lock as property fetch to serialize all operations
        async with self._property_fetch_locks[node_id]:
            if node_id not in self._local_ctrl_clients:
                if node_id in self.devices:
                    device_port = self.devices[node_id].get("port", self.default_port)
                    ip = self.devices[node_id]["ip"]
                    # ✅ FIX: Use unified method to establish connection AND sync all properties
                    # This ensures all entities are updated, not just the one being set
                    success, _ = await self._establish_and_sync_device(node_id, ip, device_port)
                    if not success:
                        return False
                else:
                    return False

            try:
                client_info = self._local_ctrl_clients[node_id]

                if isinstance(client_info, ESPLocalCtrlClient):
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
                        else:
                            # Property set failed - check connection and reconnect
                            _LOGGER.warning(
                                "Property set failed for device %s, will attempt reconnect",
                                node_id,
                            )
                            need_reconnect = True
                    except TimeoutError:
                        # Timeout indicates connection problem - trigger immediate reconnect
                        _LOGGER.warning(
                            "Property set timeout for device %s, will attempt reconnect",
                            node_id,
                        )
                        need_reconnect = True

            except Exception:
                _LOGGER.exception("Exception in set_local_ctrl_property for node_id=%s", node_id)
        
        # Handle reconnection OUTSIDE the lock context to avoid deadlock
        # This allows _fast_reconnect_for_manual_operation to acquire the lock
        if need_reconnect:
            _LOGGER.debug("Calling fast reconnect for node_id=%s", node_id)
            return await self._fast_reconnect_for_manual_operation(
                node_id, prop_name, value
            )

        return False

    async def _fast_reconnect_for_manual_operation(
        self, node_id: str, prop_name: str, value: Any
    ) -> bool:
        """Fast reconnection path for manual operations (user-initiated).

        When timeout/failure occurs, this function:
        1. Checks if another operation already reconnected (passive check)
        2. If not, performs full reconnection: cleanup → mDNS check → reconnect
        3. Retries property set after successful reconnection

        Args:
            node_id: Device node ID
            prop_name: Property name to set after reconnection
            value: Property value to set

        Returns:
            True if reconnection and property set succeeded, False otherwise
        """
        if node_id not in self._property_fetch_locks:
            self._property_fetch_locks[node_id] = asyncio.Lock()

        # Try to acquire lock with timeout to avoid blocking user operations indefinitely
        # Manual operations should have higher priority than background checks
        _LOGGER.debug(
            "Manual operation acquiring lock for device %s reconnection",
            node_id,
        )
        
        lock = self._property_fetch_locks[node_id]
        
        try:
            # Try to acquire lock with 5s timeout
            # If background loop is reconnecting, we can wait a bit but not forever
            await asyncio.wait_for(lock.acquire(), timeout=5.0)
        except TimeoutError:
            _LOGGER.warning(
                "Manual operation timed out waiting for lock on device %s - another operation in progress",
                node_id,
            )
            return False
        
        try:
            _LOGGER.debug("Manual operation acquired lock for device %s", node_id)
            
            # Check if device already reconnected by another operation (e.g., loop check)
            # This is just an optimization to avoid unnecessary reconnection
            if node_id in self._local_ctrl_clients:
                check_client = self._local_ctrl_clients[node_id]
                if await check_client.is_connected():
                    _LOGGER.info(
                        "Device %s already reconnected by another operation, will retry property set",
                        node_id,
                    )
                    # Another operation already reconnected - just retry once
                    device_controller = ESPDeviceController(check_client)
                    
                    try:
                        if await asyncio.wait_for(
                            device_controller.set_device_property(prop_name, value),
                            timeout=2.0
                        ):
                            _LOGGER.info(
                                "Property set succeeded after another operation reconnected device %s",
                                node_id
                            )
                            if "properties" not in self.devices[node_id]:
                                self.devices[node_id]["properties"] = {}
                            self.devices[node_id]["properties"][prop_name] = value
                            return True
                    except (TimeoutError, OSError, ConnectionError) as ex:
                        _LOGGER.warning(
                            "Retry failed even after reconnection by another operation for device %s: %s",
                            node_id, type(ex).__name__
                        )
                        # Fall through to perform our own reconnection
            
            # Perform full reconnection ourselves
            _LOGGER.info("Manual operation performing full reconnection for device %s", node_id)

            # Get old client for cleanup
            old_client = self._local_ctrl_clients.get(node_id)
            
            # Complete cleanup: disconnect and remove old client
            if old_client:
                await old_client.disconnect()
                # Wait for TCP FIN/RST to complete to avoid connection leaks
                # This prevents old socket from retransmitting while new connection establishes
                await asyncio.sleep(0.2)
            if node_id in self._local_ctrl_clients:
                del self._local_ctrl_clients[node_id]

            # Clear discovery flag on reconnection to allow re-discovery
            node_id_lower = node_id.lower()
            self._discovery_completed.discard(node_id)
            self._discovery_completed.discard(node_id_lower)

            # Check mDNS before fast reconnect (manual operation)
            ip = self.devices[node_id].get("ip")
            port = self.devices[node_id].get("port", self.default_port)

            _LOGGER.debug("Checking device %s mDNS availability before manual reconnect", node_id)
            if not await self.check_device_mdns_available(node_id, ip):
                _LOGGER.warning(
                    "Device %s mDNS service not detected during manual operation, device may be offline",
                    node_id,
                )
                
                # Fire device availability changed event to update all entities to unavailable
                self.hass.bus.async_fire(
                    f"{self.domain}_device_availability_changed",
                    {
                        "node_id": node_id,
                        "available": False,
                        "timestamp": asyncio.get_event_loop().time(),
                    },
                )
                _LOGGER.debug("Fired device_availability_changed event for %s (available=False, manual path)", node_id)
                
                # Device offline - return False immediately
                return False

            # mDNS detected, proceed with fast reconnect
            # Use unified method with wait_after_sync=1.0 to prepare ESP32 for retry
            # Set fire_online_event=True because we're recovering from offline state
            success, _ = await self._establish_and_sync_device(
                node_id, ip, port, wait_after_sync=1.0, fire_online_event=True
            )
            if ip and success:
                _LOGGER.info(
                    "Successfully reconnected and synced device %s during manual operation",
                    node_id
                )
                
                # Get the newly created client for property retry
                new_client = self._local_ctrl_clients[node_id]
                device_controller = ESPDeviceController(new_client)
                
                # Single retry after new connection (ESP32 already waited 1s)
                try:
                    if await asyncio.wait_for(
                        device_controller.set_device_property(prop_name, value),
                        timeout=2.0
                    ):
                        _LOGGER.info(
                            "Property set succeeded after manual reconnect for device %s",
                            node_id
                        )
                        if "properties" not in self.devices[node_id]:
                            self.devices[node_id]["properties"] = {}
                        self.devices[node_id]["properties"][prop_name] = value
                        return True
                except (TimeoutError, OSError, ConnectionError) as ex:
                    _LOGGER.warning(
                        "Property set failed after manual reconnect for device %s: %s",
                        node_id, type(ex).__name__
                    )
                
                return False

            _LOGGER.error(
                "Failed to reconnect to device %s",
                node_id,
            )
            return False
        finally:
            # Always release the lock
            lock.release()

        # This should not be reached, but return False as fallback
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

        # Ensure we have a lock for this device
        if node_id not in self._property_fetch_locks:
            self._property_fetch_locks[node_id] = asyncio.Lock()

        # Acquire lock before establishing connection
        async with self._property_fetch_locks[node_id]:
            # Check if already connected (another operation might have connected)
            if node_id in self._local_ctrl_clients:
                existing_client = self._local_ctrl_clients[node_id]
                if await existing_client.is_connected():
                    _LOGGER.debug(
                        "Device %s already connected during registration",
                        node_id
                    )
                    device_info["registered"] = True
                    device_info["last_success"] = asyncio.get_event_loop().time()
                    return True

            # Check if device is recovering from offline state
            was_offline = device_info.get("offline_state", False)
            fire_online = was_offline  # Fire event if recovering from offline
            
            if was_offline:
                _LOGGER.info(
                    "Device %s is recovering from offline state via mDNS discovery, will fire online event",
                    node_id
                )
                # ✅ CRITICAL: Set registered=True BEFORE calling _establish_and_sync_device
                # This ensures is_device_available() returns True when device_availability_changed event fires
                device_info["registered"] = True
                device_info["offline_state"] = False

            # Use unified method to establish connection and sync properties
            success, _ = await self._establish_and_sync_device(
                node_id, ip, port, fire_online_event=fire_online
            )
            if success:
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
        5. Clears discovery completed flags
        6. Clears config entry mappings
        7. Clears rate limit tracking data

        Args:
            node_id: Device node ID to unregister (as stored in config entry)
        """
        node_id_lower = node_id.lower()

        # Remove device from devices dict (uses lowercase internally)
        self.devices.pop(node_id, None)
        self.devices.pop(node_id_lower, None)

        # Clean up device tracking data (unified lock)
        self._property_fetch_locks.pop(node_id, None)
        self._property_fetch_locks.pop(node_id_lower, None)

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

        # FIX 1: Clear discovery completed flags (for both variants)
        self._discovery_completed.discard(node_id)
        self._discovery_completed.discard(node_id_lower)

        # FIX 2: Clear config entry mappings (for both variants)
        self._device_config_entries.pop(node_id, None)
        self._device_config_entries.pop(node_id_lower, None)

        # FIX 3: Clear rate limit tracking data (for both variants)
        self._rate_limit_tracker.pop(f"last_property_call_{node_id}", None)
        self._rate_limit_tracker.pop(f"last_property_call_{node_id_lower}", None)

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
        # Always start mDNS discovery to detect device reconnection
        # Even if devices are already known, we need to detect when they come back online
        if enable_discovery:
            _LOGGER.info(
                "🚀 Starting mDNS ServiceBrowser for device discovery and reconnection (enable_discovery=%s, devices=%s)",
                enable_discovery,
                list(self.devices.keys()) if self.devices else "empty",
            )
            self._zc = await zeroconf.async_get_instance(self.hass)
            self._browser = ServiceBrowser(
                self._zc,
                "_esp_local_ctrl._tcp.local.",
                ESPDeviceListener(self.hass, self),
            )
            _LOGGER.info("✅ mDNS ServiceBrowser started successfully")
        else:
            _LOGGER.warning("⚠️ mDNS discovery DISABLED (enable_discovery=False)")

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
        # TEMPORARY TEST FLAG: Control polling behavior AFTER initial discovery
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
                # PHASE 1: Always perform initial discovery for new/reconnected devices
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
                
                # PHASE 2: Periodic polling (only if enabled AND after initial discovery)
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
                # Use different intervals for offline vs online devices:
                # - Offline devices: check every 3 cycles (15 seconds) for faster reconnection
                # - Online devices: check every 12 cycles (60 seconds) to reduce overhead
                connection_check_counter += 1
                
                # Check if we have any offline devices
                # We need to check this asynchronously, so collect offline devices first
                offline_devices = []
                for node_id in self.devices:
                    if not self.devices[node_id].get("registered", False):
                        continue
                    if node_id in self._local_ctrl_clients:
                        if not await self._local_ctrl_clients[node_id].is_connected():
                            offline_devices.append(node_id)
                
                has_offline = len(offline_devices) > 0
                
                # Use faster check interval if devices are offline
                check_threshold = 3 if has_offline else 12  # 15s vs 60s
                
                if connection_check_counter >= check_threshold:
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
        
        _LOGGER.info(
            "🔄 update_device called for %s (IP: %s, Port: %s) - triggered by mDNS discovery",
            node_id,
            ip,
            port,
        )

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
                    _LOGGER.debug(
                        "Updated config entry for %s: IP %s → %s, Port %s → %s",
                        node_id,
                        config_host,
                        ip,
                        config_port,
                        port,
                    )
                break

        # ✅ FIX: Preserve existing device state when updating
        # Don't overwrite the entire dict - preserve offline_state and other flags
        if node_id in self.devices:
            # Device already exists - update only IP/Port, preserve other state
            existing_device = self.devices[node_id]
            existing_device["ip"] = ip
            existing_device["port"] = port
            if security_info:
                existing_device["security_info"] = security_info
            _LOGGER.debug(
                "Device %s updated: IP=%s, Port=%s (preserved offline_state=%s, registered=%s)",
                node_id,
                ip,
                port,
                existing_device.get("offline_state", False),
                existing_device.get("registered", False),
            )
        else:
            # New device - create fresh entry
            device_data = {"ip": ip, "port": port, "node_id": node_id, "registered": False}
            if security_info:
                device_data["security_info"] = security_info
            self.devices[node_id] = device_data
            _LOGGER.debug("Device %s added to devices dict with registered=False", node_id)

        try:
            _LOGGER.debug("Calling register_device for %s...", node_id)
            await self.register_device(node_id, ip, port)
            _LOGGER.info("✅ Successfully registered device %s", node_id)
        except Exception:
            _LOGGER.exception("❌ Error registering device %s", node_id)

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

            # ✅ OPTIMIZED: Reuse _extract_current_values instead of duplicating JSON parsing
            current_values = self._extract_current_values(properties)
            
            if current_values:
                # Fire property events for all extracted device types
                fire_property_events(self.hass, self.domain, node_id, current_values)

        except Exception:
            pass
