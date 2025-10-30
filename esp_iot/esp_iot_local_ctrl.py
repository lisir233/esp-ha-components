#!/usr/bin/env python3
"""ESP Local Control Client using ESP-IDF functions directly.

This module provides a client for communicating with ESP devices using the
ESP Local Control protocol. It handles connection management, security,
and property operations.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from typing import Any

from .esp_iot_client_utils import (
    convert_values_to_esp_format,
    parse_property_count_response,
    parse_property_values_response,
)
from .esp_iot_spec import (
    CONFIG_PROPERTY_INDEX,
    DEFAULT_PORT,
    DEFAULT_SECURITY_MODE,
    PARAMS_PROPERTY_INDEX,
)

# Import ESP-IDF functions from local bundled library
from .esp_local_ctrl_lib import esp_local_ctrl, proto_lc

_LOGGER = logging.getLogger(__name__)

# Import HTTPMessageListener from esp_local_ctrl
HTTPMessageListener = esp_local_ctrl.HTTPMessageListener
MessageSource = esp_local_ctrl.MessageSource


class ESPLocalCtrlClient:
    """ESP Local Control client using ESP-IDF functions directly.

    This client provides a high-level interface for communicating with ESP
    devices using the ESP Local Control protocol. It manages connections,
    security contexts, and property operations.

    Attributes:
        node_id: Unique identifier for the ESP device.
        ip: IP address of the ESP device.
        port: Port number for ESP Local Control (default: 8080).
        pop: Proof of Possession for security (optional).
        security_mode: Security mode (0=no security, 1=with security).
        transport: ESP-IDF transport object for communication.
        security_ctx: ESP-IDF security context for encrypted communication.
        session_established: Whether a secure session has been established.
    """

    def __init__(
        self,
        node_id: str,
        ip: str,
        port: int = DEFAULT_PORT,
        pop: str | None = None,
        security_mode: int = DEFAULT_SECURITY_MODE,
    ) -> None:
        """Initialize client with ESP device information.

        Args:
            node_id: Unique identifier for the ESP device.
            ip: IP address of the ESP device.
            port: Port number for ESP Local Control. Defaults to 8080.
            pop: Proof of Possession (security credentials). Optional.
            security_mode: Security mode (0=no security, 1=with security).
                Defaults to 1.
        """
        self.node_id = node_id
        self.ip = ip
        self.port = port
        self.pop = pop  # Proof of Possession (security credentials)
        self.security_mode = security_mode

        self.transport: Any | None = None
        self.security_ctx: Any | None = None
        self.session_established: bool = False
        self.security_config: dict[str, Any] = {}

        self._control_lock: asyncio.Lock = asyncio.Lock()
        self._last_successful_call: float = 0
        self._connection_attempts: int = 0

        # HTTP Message Listener for receiving active reports and responses
        self._http_listener: HTTPMessageListener | None = None
        self._message_callbacks: list[callable] = []

    async def has_capability(self, capability: str = "none") -> bool:
        """Check if device has a specific capability using ESP-IDF function.

        Args:
            capability: Capability name to check (e.g., "no_sec", "no_pop").
                Defaults to "none".

        Returns:
            True if device has the capability, False otherwise.
        """
        if not self.transport:
            return False

        try:
            return await esp_local_ctrl.has_capability(
                self.transport, capability, verbose=False
            )
        except Exception:  # noqa: BLE001
            return False

    async def connect(self) -> bool:
        """Establish connection to ESP device.

        The connection includes:
        1. TCP socket connection via get_transport()
        2. TLS session establishment via establish_session()
        3. HTTP message listener startup with callbacks already in place

        Returns:
            True if connection successful, False otherwise
        """
        try:
            _LOGGER.debug(
                "connect(): Starting connection for %s at %s:%s",
                self.node_id,
                self.ip,
                self.port,
            )

            # Check if already connected
            if await self.is_connected():
                _LOGGER.debug("connect(): Device %s already connected", self.node_id)
                return True

            # Reset connection state if previous session exists
            if not self.session_established:
                await self._cleanup_session()

            # Create transport
            service_name = f"{self.ip}:{self.port}"
            _LOGGER.debug("connect(): Creating transport for %s", service_name)
            self.transport = await esp_local_ctrl.get_transport(
                sel_transport="http", service_name=service_name, check_hostname=False
            )

            if not self.transport:
                _LOGGER.error("Failed to create transport for %s", service_name)
                return False

            _LOGGER.debug("connect(): Transport created successfully")

            # Debug logging for pop parameter
            _LOGGER.debug(
                "connect(): pop=%r (type=%s, len=%s)",
                self.pop,
                type(self.pop).__name__ if self.pop else "NoneType",
                len(self.pop) if isinstance(self.pop, str) else 0,
            )

            # Create security context
            _LOGGER.debug(
                "connect(): Creating security context with security_mode=%s",
                self.security_mode,
            )
            self.security_ctx = esp_local_ctrl.get_security(
                secver=self.security_mode,
                sec_patch_ver=None,
                username=None,
                password=None,
                pop=self.pop or "",
                verbose=False,
            )

            if not self.security_ctx:
                _LOGGER.error("Failed to create security context")
                return False

            _LOGGER.debug("connect(): Security context created successfully")

            # Establish session
            _LOGGER.debug("connect(): Establishing session")
            if not await esp_local_ctrl.establish_session(
                self.transport, self.security_ctx
            ):
                _LOGGER.error("Failed to establish session with device")
                return False

            _LOGGER.debug("connect(): Session established successfully")

            self.session_established = True
            self._last_successful_call = time.time()

            # Enable TCP keepalive for automatic connection monitoring
            # This allows the kernel to detect broken connections automatically
            await self._enable_tcp_keepalive()

            # Start HTTP message listener with callbacks already in place
            # This prevents race condition where ESP32 sends message
            # before callback is registered
            await self._start_message_listener()

        except Exception:  # Broad exception acceptable for connection cleanup
            _LOGGER.exception("Connection failed for device %s", self.node_id)
            await self.disconnect()
            return False
        else:
            return True

    async def is_connected(self) -> bool:
        """Check if connection is valid (socket still open).

        For persistent connections, checks if the underlying socket is still open
        and valid. Also attempts to detect broken connections by checking socket state.

        Returns:
            True if connection is valid, False otherwise.
        """
        if not self.transport or not self.session_established:
            return False

        # For persistent connections, check if socket is actually valid
        try:
            if hasattr(self.transport, "conn") and hasattr(self.transport.conn, "sock"):
                sock = self.transport.conn.sock
                
                # Check if socket exists
                if sock is None:
                    _LOGGER.info("Socket is None for device %s - connection lost", self.node_id)
                    return False
                
                # Check if socket is closed
                try:
                    # Try to get socket file descriptor - will fail if closed
                    fileno = sock.fileno()
                    if fileno == -1:
                        _LOGGER.info("Socket fileno is -1 for device %s - connection lost", self.node_id)
                        return False
                    
                    # 🔍 Connection health check using multiple methods
                    # TCP keepalive automatically detects broken connections in background
                    # Parameters: idle=10s, interval=5s, count=3
                    # Total detection time: 10-25 seconds maximum
                    
                    # Method 1: Check for errors detected by TCP keepalive
                    # When ESP32 restarts, keepalive probes will fail and set SO_ERROR
                    # Note: SO_ERROR is cleared after reading (one-shot), so check it first
                    try:
                        errcode = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        if errcode != 0:
                            _LOGGER.info(
                                "Socket error detected by TCP keepalive for device %s: errno %d",
                                self.node_id,
                                errcode,
                            )
                            return False
                    except OSError as err:
                        _LOGGER.debug(
                            "Failed to check socket error for device %s: %s",
                            self.node_id,
                            err,
                        )
                        return False
                    
                    # Method 2: Try to peek at receive buffer
                    # If connection is broken, recv will return empty or raise exception
                    try:
                        data = sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
                        if data == b'':
                            # Empty recv with MSG_PEEK means connection closed
                            _LOGGER.info(
                                "Socket closed by remote (recv returned empty) for device %s",
                                self.node_id,
                            )
                            return False
                    except BlockingIOError:
                        # No data available - this is normal, socket is fine
                        pass
                    except (ConnectionResetError, ConnectionRefusedError, BrokenPipeError) as conn_err:
                        # Connection broken
                        _LOGGER.info(
                            "Socket connection error for device %s: %s",
                            self.node_id,
                            conn_err,
                        )
                        return False
                    except OSError as recv_err:
                        # Other socket errors
                        _LOGGER.info(
                            "Socket recv error for device %s: %s",
                            self.node_id,
                            recv_err,
                        )
                        return False
                    
                except OSError as err:
                    # Socket is closed or invalid
                    _LOGGER.debug(
                        "Socket error for device %s: %s", self.node_id, err
                    )
                    return False
                
                # Socket appears valid
                return True
                
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Error checking socket state for device %s: %s", self.node_id, err
            )
            return False

        # Fallback: assume connected if we have transport and session
        return True

    def get_security_config(self) -> dict[str, Any]:
        """Get the security configuration.

        Returns:
            Dictionary containing security configuration.
        """
        return self.security_config

    async def get_property_count(self) -> int:
        """Get the number of properties available on the device.

        Returns:
            Number of properties available on the device, or 0 on error.
        """
        async with self._control_lock:
            try:
                if not self.transport or not self.session_established:
                    if not await self.connect():
                        return 0

                # ✅ Query property count using HTTPMessageListener
                if not self._http_listener:
                    _LOGGER.warning("Listener not available for property count query")
                    return 0

                # Create the property count request
                query_request = proto_lc.get_prop_count_request(self.security_ctx)

                # Send query and wait for response via listener
                msg_source, parsed_data = await self._http_listener.send_query_and_wait(
                    self.transport, query_request, timeout=10
                )

                if msg_source is None:
                    _LOGGER.warning("Query timeout for property count")
                    return 0

                if msg_source != MessageSource.QUERY_RESPONSE:
                    _LOGGER.warning(
                        "Unexpected response type for property count: %s", msg_source
                    )
                    return 0

                # Parse property count using utility function
                count = parse_property_count_response(parsed_data)
                if count > 0:
                    self._last_successful_call = time.time()
                return count

            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed to get property count: %s", err)
                return 0

    async def get_property_values(self) -> list[dict[str, Any]]:
        """Get all property values.

        Uses HTTPMessageListener when available (after connection established).

        Returns:
            List of property dictionaries, or empty list on error.
        """
        async with self._control_lock:
            try:
                if not self.transport or not self.session_established:
                    if not await self.connect():
                        return []

                # Use direct query instead of listener (listener not available)
                # Return empty for now - monitoring loop will keep trying
                props = await self.get_all_property_values_via_listener(timeout=10)

                if props:
                    self._last_successful_call = time.time()
                else:
                    _LOGGER.warning("No properties returned from device")

                return props

            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed to get property values: %s", err)
                return []

    async def get_all_property_values_via_listener(
        self, timeout: int = 10
    ) -> list[dict[str, Any]]:
        """Get all property values using HTTPMessageListener (unified message handling).

        This method:
        1. First queries the property count
        2. Then requests all properties by index
        3. Waits for responses through HTTPMessageListener

        Args:
            timeout: Timeout in seconds for waiting for response (default 10)

        Returns:
            List of property dictionaries, or empty list on error/timeout.

        Note:
            This method is called from get_property_values() which already holds the lock,
            so we don't acquire the lock again here to avoid deadlock.
        """
        try:
            _LOGGER.debug(
                "Checking query readiness: transport=%s, session=%s, listener=%s",
                bool(self.transport),
                self.session_established,
                bool(self._http_listener),
            )
            _LOGGER.debug(
                "Property query check: transport=%s, session=%s, listener=%s",
                bool(self.transport),
                self.session_established,
                bool(self._http_listener),
            )
            if (
                not self.transport
                or not self.session_established
                or not self._http_listener
            ):
                _LOGGER.warning("Client not ready for listener-based query")
                return []

            # Step 1: Query property count
            _LOGGER.debug(
                "About to send property count query for device %s", self.node_id
            )
            count_request = proto_lc.get_prop_count_request(self.security_ctx)
            msg_source, count_data = await self._http_listener.send_query_and_wait(
                self.transport, count_request, timeout=timeout
            )

            if msg_source is None or msg_source != MessageSource.QUERY_RESPONSE:
                _LOGGER.warning("Failed to get property count")
                return []

            # Extract count from response
            prop_count = 0
            if isinstance(count_data, dict) and "count" in count_data:
                prop_count = count_data["count"]

            if prop_count <= 0:
                _LOGGER.debug("Device has no properties")
                return []

            # Step 2: Query all property values
            indices = list(range(prop_count))
            props_request = proto_lc.get_prop_vals_request(self.security_ctx, indices)

            msg_source, props_data = await self._http_listener.send_query_and_wait(
                self.transport, props_request, timeout=timeout
            )

            if msg_source is None or msg_source != MessageSource.QUERY_RESPONSE:
                _LOGGER.warning("Failed to get property values")
                return []

            # Parse property values using utility function with debug logging
            properties = parse_property_values_response(props_data, log_details=True)
            if properties:
                self._last_successful_call = time.time()
            return properties

        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to get property values via listener: %s", err)
            return []

    async def set_property_values(self, indices: list[int], values: list[Any]) -> bool:
        """Set property values using ESP-IDF function.

        Converts values to the format expected by ESP-IDF and sends them
        to the device. Automatically redirects config property (index 0)
        to params property (index 1) for ESP-RainMaker compatibility.

        Args:
            indices: List of property indices to set.
            values: List of values corresponding to the indices.

        Returns:
            True if properties were set successfully, False otherwise.
        """
        async with self._control_lock:
            try:
                _LOGGER.debug(
                    "set_property_values called, transport=%s, session_established=%s",
                    self.transport is not None,
                    self.session_established,
                )
                _LOGGER.debug(
                    "Setting properties: indices=%s, values=%s", indices, values
                )

                if not self.transport or not self.session_established:
                    _LOGGER.debug("No transport or session, attempting to connect...")
                    if not await self.connect():
                        return False

                # Convert values to ESP-IDF byte format using utility function
                esp_values = convert_values_to_esp_format(values)

                # For ESP-RainMaker: redirect config(0) to params(1)
                if indices and indices[0] == CONFIG_PROPERTY_INDEX:
                    indices = [PARAMS_PROPERTY_INDEX]

                # Use ESP-IDF function directly
                success = await esp_local_ctrl.set_property_values(
                    self.transport,
                    self.security_ctx,
                    props=None,  # We don't need property definitions for basic setting
                    indices=indices,
                    values=esp_values,
                    check_readonly=False,
                    listener=self._http_listener,  # Use unified async flow
                )

                if not success:
                    _LOGGER.warning("ESP32 rejected property setting request")

                return success

            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed to set property values: %s", err)
                return False

    async def disconnect(self) -> None:
        """Disconnect from ESP device.

        Cleans up transport and security context, and resets session state.
        """
        _LOGGER.debug("Disconnecting from device %s", self.node_id)
        await self._cleanup_session()

    async def _cleanup_session(self) -> None:
        """Clean up resources.

        Resets transport, security context, session state, and connection attempts.
        """
        # Stop HTTP message listener
        await self._stop_message_listener()

        if self.transport:
            # Close HTTP connection and send FIN packet
            with contextlib.suppress(Exception):
                if hasattr(self.transport, "close"):
                    self.transport.close()
                # Fallback: try to close conn directly
                elif hasattr(self.transport, "conn"):
                    with contextlib.suppress(Exception):
                        self.transport.conn.close()

            self.transport = None

        self.session_established = False
        self.security_ctx = None
        self._connection_attempts = 0

    async def _enable_tcp_keepalive(self) -> None:
        """Enable TCP keepalive on the socket for automatic connection monitoring.
        
        TCP keepalive parameters:
        - TCP_KEEPIDLE: 10 seconds - Start probing after 10s of inactivity
        - TCP_KEEPINTVL: 5 seconds - Send probe every 5 seconds
        - TCP_KEEPCNT: 3 probes - Consider dead after 3 failed probes
        
        Total detection time: 10s (idle) + 5s × 3 (probes) = 25 seconds maximum
        This is much faster than default TCP timeout (often 2+ hours).
        """
        try:
            if not self.transport or not hasattr(self.transport, "conn"):
                return
            
            if not hasattr(self.transport.conn, "sock"):
                return
            
            sock = self.transport.conn.sock
            if sock is None:
                return
            
            # Enable TCP keepalive
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            # Configure keepalive timing (Linux/Unix)
            # Note: These constants might not exist on all platforms
            if hasattr(socket, 'TCP_KEEPIDLE'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            if hasattr(socket, 'TCP_KEEPINTVL'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            if hasattr(socket, 'TCP_KEEPCNT'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            
            _LOGGER.debug(
                "TCP keepalive enabled for device %s (idle=10s, interval=5s, count=3)",
                self.node_id
            )
            
        except OSError as err:
            # Non-fatal: keepalive is optional optimization
            _LOGGER.debug(
                "Failed to enable TCP keepalive for device %s: %s",
                self.node_id,
                err,
            )

    async def _start_message_listener(self) -> None:
        """Start the HTTP message listener for receiving active reports and responses.

        Creates an HTTPMessageListener instance and starts listening for incoming messages.
        Adds all pre-registered callbacks BEFORE starting to prevent race condition.
        Called automatically after session is established.
        """
        try:
            if self._http_listener is not None:
                # Already listening
                return

            if not self.transport or not self.security_ctx:
                _LOGGER.warning(
                    "Cannot start listener: no transport or security context"
                )
                return

            # Create listener
            self._http_listener = HTTPMessageListener(
                self.transport, self.security_ctx, verbose=False
            )

            # Add all pre-registered callbacks BEFORE starting listener
            # This prevents race condition where ESP32 sends message
            # before callback is registered
            for callback in self._message_callbacks:
                self._http_listener.add_callback(callback)

            # Start listening
            await self._http_listener.start()
            _LOGGER.debug("HTTP message listener started for device %s", self.node_id)

        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to start HTTP message listener: %s", err)
            self._http_listener = None

    async def _stop_message_listener(self) -> None:
        """Stop the HTTP message listener.

        Called during disconnect/cleanup to ensure resources are released.
        """
        try:
            if self._http_listener:
                await self._http_listener.stop()
                self._http_listener = None
                _LOGGER.debug(
                    "HTTP message listener stopped for device %s", self.node_id
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error stopping HTTP message listener: %s", err)
            self._http_listener = None

    def add_message_callback(self, callback: callable) -> None:
        """Register a callback to be invoked when HTTP messages are received.

        The callback will be called with (message_source, message_data) arguments.

        Args:
            callback: Async or sync callable to invoke on message receipt
        """
        if callback not in self._message_callbacks:
            self._message_callbacks.append(callback)

            # If listener is already running, add callback to it
            if self._http_listener:
                self._http_listener.add_callback(callback)
