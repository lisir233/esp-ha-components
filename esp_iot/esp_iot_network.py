"""Network discovery and device detection for ESP devices."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import socket
import time
from typing import TYPE_CHECKING, Any

from zeroconf import ServiceBrowser

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from zeroconf import ServiceInfo, Zeroconf
else:
    try:
        from zeroconf import ServiceInfo
    except ImportError:
        ServiceInfo = None

_LOGGER = logging.getLogger(__name__)

# Import DOMAIN from const module
try:
    from custom_components.esp_ha.const import DOMAIN
except ImportError:
    # Fallback for development/testing
    DOMAIN = "esp_ha"

# Service discovery constants
DEVICE_SERVICE_TYPE = "_esp_local_ctrl"
PROTO = "_tcp"

# Discovery timeouts (in seconds)
DISCOVERY_TIMEOUT = 20.0
DISCOVERY_SETTLE_TIME = 3.0

# Discovery throttling (seconds)
DISCOVERY_THROTTLE_INTERVAL = 5


async def async_discover_devices(
    hass: HomeAssistant, listener_class: type
) -> list[dict[str, Any]]:
    """Discover ESP devices via mDNS/Zeroconf.

    Args:
        hass: Home Assistant instance
        listener_class: ESPDeviceListener class from const

    Returns:
        List of discovered device dictionaries
    """
    try:
        zc = await zeroconf.async_get_instance(hass)
        listener = listener_class(hass)
        listener.reset()

        browser = ServiceBrowser(zc, f"{DEVICE_SERVICE_TYPE}.{PROTO}.local.", listener)

        try:
            await asyncio.wait_for(
                listener.discovered_event.wait(), timeout=DISCOVERY_TIMEOUT
            )
            await asyncio.sleep(DISCOVERY_SETTLE_TIME)
            return listener.devices.copy()

        except TimeoutError:
            return []
        finally:
            if browser and hasattr(browser, "cancel"):
                with contextlib.suppress(Exception):
                    browser.cancel()
            listener.reset()

    except OSError as err:
        _LOGGER.error("Network error during device discovery: %s", err)
        return []
    except Exception:
        _LOGGER.exception("Unexpected error during device discovery")
        return []


class ESPDeviceListener:
    """Listener for ESP device discovery via zeroconf.

    This class handles device discovery, updates, and removals through
    mDNS/Zeroconf. It maintains a list of discovered devices and throttles
    rapid discovery events to prevent flooding.

    Attributes:
        hass: Home Assistant instance.
        api: Optional API instance for device management.
        devices: List of discovered device information dictionaries.
        seen_node_ids: Set of node IDs that have been discovered.
        discovered_event: Event that is set when a new device is discovered.
    """

    def __init__(self, hass: HomeAssistant, api: Any | None = None) -> None:
        """Initialize the device listener.

        Args:
            hass: Home Assistant instance.
            api: Optional API instance for device management. If None,
                operates in config flow mode (discovers all devices).
                If provided, only processes devices with existing config entries.
        """
        self.hass = hass
        self.api = api
        self.devices: list[dict[str, Any]] = []
        self.seen_node_ids: set[str] = set()
        self.discovered_event: asyncio.Event = asyncio.Event()
        self._last_discovery_time: float = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    def reset(self) -> None:
        """Reset the listener state.

        Clears all discovered devices, seen node IDs, and resets
        the discovery event and timing.
        """
        self.devices.clear()
        self.seen_node_ids.clear()
        self.discovered_event.clear()
        self._last_discovery_time = 0
        _LOGGER.debug("Listener state reset")

    def add_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        """Handle addition of a new service.

        Called by Zeroconf when a new service is discovered on the network.

        Args:
            zeroconf: Zeroconf instance.
            type_: Service type string (e.g., "_http._tcp.local.").
            name: Service name string.
        """
        self._handle_service_change(zeroconf, type_, name, "added")

    def remove_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        """Handle removal of a service.

        Called by Zeroconf when a service is removed from the network.

        Args:
            zeroconf: Zeroconf instance.
            type_: Service type string.
            name: Service name string.
        """
        self._handle_service_change(zeroconf, type_, name, "removed")

    def update_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        """Handle update of a service.

        Called by Zeroconf when a service's information is updated.

        Args:
            zeroconf: Zeroconf instance.
            type_: Service type string.
            name: Service name string.
        """
        self._handle_service_change(zeroconf, type_, name, "updated")

    def _handle_service_change(
        self, zeroconf: Zeroconf, type_: str, name: str, change_type: str
    ) -> None:
        """Common handler for all service changes.

        Processes service additions, updates, and removals from Zeroconf.
        Extracts device information and routes to appropriate handlers.

        Args:
            zeroconf: Zeroconf instance.
            type_: Service type string.
            name: Service name string.
            change_type: Type of change ("added", "updated", or "removed").
        """
        try:
            _LOGGER.info(
                "📡 mDNS event received: type=%s, name=%s, change_type=%s",
                type_,
                name,
                change_type,
            )

            info = zeroconf.get_service_info(type_, name)
            if not info or not info.addresses:
                _LOGGER.warning("⚠️ No service info or addresses for %s", name)
                return

            _LOGGER.debug("Service %s: %s", change_type, name)

            # Extract node ID and device name from service properties
            node_id, device_name = self._extract_device_info(name, info)
            if not node_id:
                _LOGGER.error("Discovered device without node ID: %s", name)
                return

            ip = socket.inet_ntoa(info.addresses[0])
            port = info.port  # Extract port from zeroconf service info

            _LOGGER.info(
                "📝 Extracted device info: node_id=%s, device_name=%s, ip=%s, port=%s",
                node_id,
                device_name,
                ip,
                port,
            )

            if change_type == "removed":
                self._process_device_removal(node_id)
            else:
                self._process_device_discovery(node_id, ip, change_type, port, device_name)

        except Exception:
            _LOGGER.exception("Error handling service %s", change_type)

    def _process_device_discovery(
        self, node_id: str, ip: str, change_type: str, port: int | None = None, device_name: str | None = None
    ) -> None:
        """Process device discovery or update.

        Handles device discovery differently based on whether an API instance
        is present (service mode) or not (config flow mode).

        Args:
            node_id: Device node ID.
            ip: Device IP address.
            change_type: Type of change ("added" or "updated").
            port: Device port. Defaults to 8080 if None.
            device_name: Custom device name from mDNS TXT. Defaults to None.
        """
        if port is None:
            port = 8080  # Default port

        _LOGGER.info(
            "🔍 _process_device_discovery called: node_id=%s, device_name=%s, ip=%s, port=%s, change_type=%s, api=%s",
            node_id,
            device_name,
            ip,
            port,
            change_type,
            "present" if self.api is not None else "None",
        )

        if self.api is None:
            # Config flow discovery - allow all devices to be discovered
            _LOGGER.debug(
                "Discovered device (config flow): Node ID=%s Device Name=%s IP=%s Port=%s Change=%s",
                node_id,
                device_name,
                ip,
                port,
                change_type,
            )
            self.hass.loop.call_soon_threadsafe(
                self.hass.async_create_task,
                self._async_handle_discovery(node_id, ip, port, device_name),
            )
        else:
            # Service discovery - only process devices with existing config entries
            existing_entries = self.hass.config_entries.async_entries(DOMAIN)
            existing_node_ids = {
                entry.unique_id for entry in existing_entries if entry.unique_id
            }

            _LOGGER.info(
                "📋 Checking if node_id %s is in existing entries. Existing: %s",
                node_id,
                existing_node_ids,
            )

            if node_id in existing_node_ids:
                _LOGGER.info(
                    "✅ Discovered device (service) - WILL CALL update_device: Node ID=%s IP=%s Port=%s Change=%s",
                    node_id,
                    ip,
                    port,
                    change_type,
                )
                
                # Global listener for initial discovery and IP address updates
                # Individual reconnection attempts use dedicated ServiceBrowser
                self.hass.loop.call_soon_threadsafe(
                    self.hass.async_create_task,
                    self.api.update_device(node_id, ip, port),
                )
            else:
                _LOGGER.warning(
                    "❌ Ignoring unconfigured device discovery: Node ID=%s (not in %s)",
                    node_id,
                    existing_node_ids,
                )

    def _process_device_removal(self, node_id: str) -> None:
        """Process device removal.

        Handles device removal for both config flow and service modes.

        Args:
            node_id: Device node ID that was removed.
        """
        if self.api is None:
            _LOGGER.debug("Device removed (config flow): Node ID=%s", node_id)
            self.hass.loop.call_soon_threadsafe(
                self.hass.async_create_task,
                self._async_handle_removal(node_id),
            )
        else:
            _LOGGER.debug("Device removed (service): Node ID=%s", node_id)
            self.hass.loop.call_soon_threadsafe(
                self.hass.async_create_task,
                self._async_handle_removal(node_id),
            )

    async def _async_handle_discovery(
        self, node_id: str, ip: str, port: int | None = None, device_name: str | None = None
    ) -> None:
        """Handle new device discovery.

        Throttles rapid discoveries and maintains list of discovered devices.

        Args:
            node_id: Device node ID.
            ip: Device IP address.
            port: Device port. Defaults to 8080 if None.
            device_name: Custom device name from mDNS TXT. Defaults to None.
        """
        if port is None:
            port = 8080  # Default port

        async with self._lock:
            current_time = time.time()

            # Throttle rapid discoveries using constant
            if (
                node_id in self.seen_node_ids
                and current_time - self._last_discovery_time
                < DISCOVERY_THROTTLE_INTERVAL
            ):
                return

            self._last_discovery_time = current_time

            if node_id not in self.seen_node_ids:
                self.seen_node_ids.add(node_id)
                device_info: dict[str, Any] = {
                    "node_id": node_id,
                    "ip": ip,
                    "port": port,
                    "device_name": device_name,
                }
                self.devices.append(device_info)
                _LOGGER.debug("New device discovered: Node ID=%s Device Name=%s IP=%s", node_id, device_name, ip)

            if not self.discovered_event.is_set():
                self.discovered_event.set()

    async def _async_handle_removal(self, node_id: str) -> None:
        """Handle device removal.

        Removes device from seen list and devices list.

        Args:
            node_id: Device node ID to remove.
        """
        async with self._lock:
            if node_id in self.seen_node_ids:
                self.seen_node_ids.remove(node_id)
                self.devices = [d for d in self.devices if d["node_id"] != node_id]
                _LOGGER.debug("Device removed: Node ID=%s", node_id)

    def _extract_device_info(
        self, service_name: str, service_info: ServiceInfo | None
    ) -> tuple[str | None, str | None]:
        """Extract node ID and device name from service properties.

        Tries multiple methods to extract device information:
        1. Check service properties for node_id (required for communication)
        2. Check service properties for device_name (optional, for display)
        3. Extract node_id from service name
        4. Extract node_id from server name

        Args:
            service_name: Zeroconf service name.
            service_info: Zeroconf service info object.

        Returns:
            Tuple of (node_id, device_name). device_name may be None.
        """
        node_id = None
        device_name = None
        
        # Extract device_name if present (for display only)
        if service_info and service_info.properties:
            if b"device_name" in service_info.properties:
                device_name = service_info.properties[b"device_name"].decode("utf-8")
                _LOGGER.info(
                    "Found device_name in mDNS TXT: %s (will be used for display)",
                    device_name,
                )
        
        # Method 1: Check service properties for node ID (required for communication)
        if service_info and service_info.properties:
            # Get node_id from properties
            id_keys = ["node_id", "device_id", "id"]
            for key in id_keys:
                if key.encode() in service_info.properties:
                    id_value = service_info.properties[key.encode()].decode("utf-8")
                    # Validate and format node_id
                    if self._is_valid_node_id(id_value):
                        node_id = self._format_node_id(id_value)
                        break
        
        # If node_id found, return it with device_name
        if node_id:
            return (node_id, device_name)

        # Method 2: Extract from service name
        if service_name:
            # Remove service type suffix
            extracted_name = service_name.split(".")[0]
            # Validate if it's a valid node_id
            if self._is_valid_node_id(extracted_name):
                return (self._format_node_id(extracted_name), device_name)

        # Method 3: Extract from server name
        if service_info and hasattr(service_info, "server"):
            server_name = service_info.server
            if server_name:
                # Try to extract valid node_id from server name
                parts = server_name.split(".")
                if parts and self._is_valid_node_id(parts[0]):
                    return (self._format_node_id(parts[0]), device_name)

        return (None, None)

    def _is_valid_node_id(self, node_id_str: str) -> bool:
        """Check if string is a valid node ID format.

        Validates that the node ID contains only alphanumeric characters,
        underscores, and hyphens, and is between 1-64 characters long.

        Args:
            node_id_str: String to validate as node ID.

        Returns:
            True if valid node ID format, False otherwise.
        """
        # Validate node_id is a valid 64-character alphanumeric string
        id_pattern = r"^[0-9A-Za-z_-]{1,64}$"
        return bool(re.match(id_pattern, node_id_str))

    def _format_node_id(self, node_id_str: str) -> str:
        """Format node ID to standard format.

        Converts node ID to lowercase and replaces invalid characters
        with underscores. Valid characters are alphanumeric, underscore,
        and hyphen.

        Args:
            node_id_str: Raw node ID string.

        Returns:
            Formatted node ID (lowercase, alphanumeric with underscore and hyphen).
        """
        # Keep letters, numbers, underscores and hyphens; replace others with underscore
        clean_id = re.sub(r"[^0-9A-Za-z_-]", "_", node_id_str)
        return clean_id.lower()
