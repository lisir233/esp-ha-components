#!/usr/bin/env python
#
# SPDX-FileCopyrightText: 2018-2025 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
import argparse
import asyncio
from getpass import getpass
import json
import struct
import sys
import textwrap

# Use local imports from the bundled library
from . import proto_lc
from . import esp_prov
from .esp_prov import security

# Set this to true to allow exceptions to be thrown
config_throw_except = False


# Property types enum
PROP_TYPE_TIMESTAMP = 0
PROP_TYPE_INT32 = 1
PROP_TYPE_BOOLEAN = 2
PROP_TYPE_STRING = 3


# Property flags enum
PROP_FLAG_READONLY = 1 << 0


class MessageSource:
    """Enum for message source type"""

    ACTIVE_REPORT = "active_report"  # ESP32 sends proactively
    QUERY_RESPONSE = "query_response"  # Response to our query
    UNKNOWN = "unknown"


def prop_typestr(prop):
    if prop["type"] == PROP_TYPE_TIMESTAMP:
        return "TIME(us)"
    if prop["type"] == PROP_TYPE_INT32:
        return "INT32"
    if prop["type"] == PROP_TYPE_BOOLEAN:
        return "BOOLEAN"
    if prop["type"] == PROP_TYPE_STRING:
        return "STRING"
    return "UNKNOWN"


def encode_prop_value(prop, value):
    try:
        if prop["type"] == PROP_TYPE_TIMESTAMP:
            return struct.pack("q", value)
        if prop["type"] == PROP_TYPE_INT32:
            return struct.pack("i", value)
        if prop["type"] == PROP_TYPE_BOOLEAN:
            return struct.pack("?", value)
        if prop["type"] == PROP_TYPE_STRING:
            return bytes(value, encoding="latin-1")
        return value
    except struct.error as e:
        print(e)
    return None


def decode_prop_value(prop, value):
    try:
        prop_name = prop.get("name", "").lower()

        # Handle system properties with type collision
        # ESP32 declares 'config' as type=1 and 'params' as type=2
        # but sends JSON strings instead of INT32/BOOLEAN
        if prop_name in ("config", "params"):
            return value.decode("latin-1")

        # Standard type-based parsing
        if prop["type"] == PROP_TYPE_TIMESTAMP:
            return struct.unpack("q", value)[0]
        if prop["type"] == PROP_TYPE_INT32:
            return struct.unpack("i", value)[0]
        if prop["type"] == PROP_TYPE_BOOLEAN:
            return struct.unpack("?", value)[0]
        if prop["type"] == PROP_TYPE_STRING:
            return value.decode("latin-1")
        return value
    except struct.error as e:
        print(e)
    return None


def str_to_prop_value(prop, strval):
    try:
        if prop["type"] == PROP_TYPE_TIMESTAMP or prop["type"] == PROP_TYPE_INT32:
            return int(strval)
        if prop["type"] == PROP_TYPE_BOOLEAN:
            return bool(strval)
        if prop["type"] == PROP_TYPE_STRING:
            return strval
        return strval
    except ValueError as e:
        print(e)
        return None


def prop_is_readonly(prop):
    return (prop["flags"] & PROP_FLAG_READONLY) != 0


def on_except(err):
    if config_throw_except:
        raise RuntimeError(err)
    print(err)


def get_security(secver, sec_patch_ver, username, password, pop="", verbose=False):
    if secver == 2:
        return security.Security2(sec_patch_ver, username, password, verbose)
    if secver == 1:
        return security.Security1(pop, verbose)
    if secver == 0:
        return security.Security0(verbose)
    return None


def parse_http_headers(raw_data, offset=0):
    """Parse HTTP headers in EVENT/1.0 format.

    Extracts HTTP headers, payload, and determines message type by analyzing:
    - HTTP status line (EVENT/1.0 vs HTTP/1.1)
    - Content-Type header (application/hap+json vs text/html)

    Message type determination:
    - Active Report: "EVENT/1.0" status line + "application/hap+json" Content-Type
    - Query Response: "HTTP/1.1" status line + "text/html" Content-Type

    Args:
        raw_data: Raw bytes from socket containing HTTP headers and payload
        offset: Number of bytes already skipped from the original buffer (for recursion)

    Returns:
        Tuple of (status_line, headers_dict, payload_bytes, message_source, headers_end_offset)
        - headers_end_offset: Position in ORIGINAL buffer where headers end (including \r\n\r\n)
        or (None, None, None, None, 0) if headers are incomplete or invalid

    Example:
        >>> status, headers, payload, source, offset = parse_http_headers(raw_data)
        >>> if status:
        ...     print(f"Content-Length: {headers.get('Content-Length')}")
        ...     print(f"Message source: {source}")
        ...     print(f"Headers end at offset: {offset}")
    """
    try:
        # Validate buffer starts with valid HTTP status line
        if len(raw_data) < 20:
            return (None, None, None, None, 0)
        
        # Check if buffer starts with valid HTTP status line
        try:
            prefix = raw_data[:20].decode('ascii', errors='ignore')
        except:
            prefix = ""
        
        # If buffer doesn't start with valid headers, search for next valid header position
        if not prefix.startswith(("HTTP/", "EVENT/")):
            for pattern in [b"EVENT/", b"HTTP/"]:
                next_header_pos = raw_data.find(pattern)
                if next_header_pos > 0:
                    return parse_http_headers(raw_data[next_header_pos:], offset + next_header_pos)
            return (None, None, None, None, 0)
        
        # Find header/body separator
        separator = b"\r\n\r\n"
        sep_index = raw_data.find(separator)

        if sep_index == -1:
            return (None, None, None, None, 0)

        # Extract headers and payload
        headers_raw = raw_data[:sep_index].decode("latin-1")
        payload = raw_data[sep_index + 4 :]

        # Parse status line and headers
        lines = headers_raw.split("\r\n")
        status_line = lines[0]

        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = value.strip()

        # Determine message source from HTTP headers
        message_source = MessageSource.UNKNOWN
        content_type = headers.get("Content-Type", "").lower()

        if "EVENT/1.0" in status_line and "application/hap+json" in content_type:
            message_source = MessageSource.ACTIVE_REPORT
        elif "HTTP/1.1" in status_line and "text/html" in content_type:
            message_source = MessageSource.QUERY_RESPONSE

        # Calculate the absolute position where headers end in the original buffer
        headers_end_offset = offset + sep_index + 4
        return (status_line, headers, payload, message_source, headers_end_offset)

    except Exception as err:
        print(f"Error parsing HTTP headers: {err}")
        return (None, None, None, None, 0)


def get_content_length(headers):
    """Extract Content-Length from HTTP headers.

    Args:
        headers: Dictionary of HTTP headers

    Returns:
        Content length as integer, or 0 if not found or invalid
    """
    try:
        content_length = headers.get("Content-Length", "0")
        return int(content_length)
    except (ValueError, KeyError):
        return 0


async def handle_incoming_http_message(raw_message_data, security_ctx, verbose=False):
    """Handle incoming HTTP message from ESP32 device.

    Parses HTTP headers to extract content-length and message type,
    then decrypts and deserializes the protobuf payload.

    Message type is determined from HTTP headers:
    - If "EVENT/1.0" in status line AND "application/hap+json" in Content-Type -> Active Report
    - If "HTTP/1.1" in status line AND "text/html" in Content-Type -> Query Response

    Args:
        raw_message_data: Raw bytes from socket
        security_ctx: Security context for decryption
        verbose: Enable verbose logging

    Returns:
        Tuple of (message_source, parsed_data, message_size) or (None, None, 0) on error
        where message_source is:
            - MessageSource.ACTIVE_REPORT (unsolicited device update from ESP32)
            - MessageSource.QUERY_RESPONSE (response to our query/control request)
            - 'set_response' (response to SET command)
            - 'count_response' (response to COUNT command)
        and message_size is the total bytes consumed (headers + payload)

    Example:
        >>> msg_source, data, msg_size = await handle_incoming_http_message(raw_bytes, sec_ctx)
        >>> if msg_source == MessageSource.ACTIVE_REPORT:
        ...     properties = data.get('properties', [])
        ...     for prop in properties:
        ...         print(f"Property update: {prop}")
    """
    try:
        # Parse HTTP headers and determine message type
        status_line, headers, payload, message_source, headers_end_offset = parse_http_headers(
            raw_message_data
        )

        if not status_line:
            return (None, None, 0)

        if verbose:
            print(f"HTTP Status: {status_line}")
            print(f"Message Source: {message_source}")
            print(f"Content-Length: {get_content_length(headers)}")

        content_length = get_content_length(headers)
        
        if len(payload) < content_length:
            return (None, None, 0)

        # Extract exact Content-Length bytes
        actual_payload = payload[:content_length]
        message_size = headers_end_offset + content_length

        # Decrypt and parse protobuf
        parsed_data = proto_lc.parse_payload(message_source, security_ctx, actual_payload)

        if verbose:
            print(f"Parsed Data: {parsed_data}")

        return (message_source, parsed_data, message_size)

    except Exception as e:
        print(f"Error handling incoming HTTP message: {e}")
        return (None, None, 0)


async def get_transport(sel_transport, service_name, check_hostname):
    try:
        tp = None
        loop = asyncio.get_event_loop()

        if sel_transport == "http":
            tp = await loop.run_in_executor(
                None, esp_prov.transport.Transport_HTTP, service_name, None
            )
        return tp
    except RuntimeError as e:
        on_except(e)
        return None


async def get_sec_patch_ver(tp, verbose=False):
    try:
        # Use send_data_and_receive() during setup phase before HTTPMessageListener starts
        response = await tp.send_data_and_receive("esp_local_ctrl/version", "---")

        if verbose:
            print("esp_local_ctrl/version response : ", response)

        try:
            # Interpret this as JSON structure containing
            # information with security version information
            info = json.loads(response)
            try:
                sec_patch_ver = info["local_ctrl"]["sec_patch_ver"]
            except KeyError:
                sec_patch_ver = 0
            return sec_patch_ver

        except ValueError:
            # If decoding as JSON fails, we assume default patch level
            return 0

    except Exception as e:
        on_except(e)
        return None


async def version_match(tp, protover, verbose=False):
    try:
        # Use send_data_and_receive() during setup phase
        response = await tp.send_data_and_receive("esp_local_ctrl/version", protover)

        if verbose:
            print("esp_local_ctrl/version response : ", response)

        # First assume this to be a simple version string
        if response.lower() == protover.lower():
            return True

        try:
            # Else interpret this as JSON structure containing
            # information with versions and capabilities of both
            # provisioning service and application
            info = json.loads(response)
            if info["local_ctrl"]["ver"].lower() == protover.lower():
                return True

        except ValueError:
            # If decoding as JSON fails, it means that capabilities
            # are not supported
            return False

    except Exception as e:
        on_except(e)
        return None


async def has_capability(tp, capability="none", verbose=False):
    # Note : default value of `capability` argument cannot be empty string
    # because protocomm_httpd expects non zero content lengths
    try:
        # Use send_data_and_receive() during setup phase
        response = await tp.send_data_and_receive("esp_local_ctrl/version", capability)

        if verbose:
            print("esp_local_ctrl/version response : ", response)

        try:
            # Interpret this as JSON structure containing
            # information with versions and capabilities of both
            # provisioning service and application
            info = json.loads(response)
            try:
                supported_capabilities = info["local_ctrl"]["cap"]
                if capability.lower() == "none":
                    # No specific capability to check, but capabilities
                    # feature is present so return True
                    return True

                # Check if the capability is supported
                if capability.lower() in (
                    cap.lower() for cap in supported_capabilities
                ):
                    return True
                return False

            except KeyError:
                # If 'cap' is not present, no capabilities are supported
                return False

        except ValueError:
            # If decoding as JSON fails, assume no capabilities supported
            return False

    except Exception as e:
        on_except(e)
        return None


async def establish_session(tp, sec):
    """Establish secure session (Session Handshake).

    Uses send_data_and_receive() for multi-round handshake.
    Handshake occurs before HTTPMessageListener starts,
    so it's safe to use conn.getresponse() to get response.

    Warning: After handshake completes, the socket in transport may be polluted.
    Caller needs to call reset_connection() before starting HTTPMessageListener.
    """
    try:
        response = None
        while True:
            request = sec.security_session(response)
            if request is None:
                break
            response = await tp.send_data_and_receive("esp_local_ctrl/session", request)
            if response is None:
                return False

        return True
    except RuntimeError as e:
        on_except(e)
        return None


async def get_all_property_values(tp, security_ctx):
    try:
        props = []
        message = proto_lc.get_prop_count_request(security_ctx)
        response = await tp.send_data_and_receive("esp_local_ctrl/control", message)
        count = proto_lc.get_prop_count_response(security_ctx, response)
        if count == 0:
            raise RuntimeError("No properties found!")
        indices = [i for i in range(count)]
        message = proto_lc.get_prop_vals_request(security_ctx, indices)
        response = await tp.send_data_and_receive("esp_local_ctrl/control", message)
        props = proto_lc.get_prop_vals_response(security_ctx, response)
        if len(props) != count:
            raise RuntimeError("Incorrect count of properties!", len(props), count)
        for p in props:
            p["value"] = decode_prop_value(p, p["value"])
        return props
    except RuntimeError as e:
        on_except(e)
        return []


class HTTPMessageListener:
    """Listener for continuous HTTP messages from ESP32 device.

    Monitors the persistent HTTP connection for both active reports and query responses.
    Supports waiting for specific query responses while routing active reports to callbacks.
    """

    def __init__(self, transport, security_ctx, client=None, verbose=False):
        """Initialize HTTP message listener.

        Args:
            transport: ESP-IDF transport object with socket
            security_ctx: Security context for decryption
            client: Optional ESPLocalCtrlClient to notify of connection errors
            verbose: Enable verbose logging
        """
        self.transport = transport
        self.security_ctx = security_ctx
        self.client = client  # Store client reference for error notification
        self.verbose = verbose
        self.callbacks = []
        self._running = False
        self._listen_task = None
        self._buffer = bytearray()
        self._buffer_lock = asyncio.Lock()

        # Query response waiting mechanism
        self._query_futures: dict[str, asyncio.Future] = {}
        self._query_counter = 0
        self._query_lock = asyncio.Lock()

    def add_callback(self, callback):
        """Add callback to be invoked when message is received.

        Args:
            callback: Async function that accepts (message_source, message_data)
        """
        if callback not in self.callbacks:
            self.callbacks.append(callback)

    async def send_query_and_wait(self, transport, query_request, timeout=10.0):
        """Send a query request and wait for the response.

        This method sends the query through the same socket that the listener
        is monitoring, so the response comes through the unified message handling
        system rather than a direct transport call.

        Args:
            transport: Transport object to send request through
            query_request: The query request data to send
            timeout: Timeout in seconds for waiting for response

        Returns:
            Tuple of (message_source, parsed_data) or (None, None) on timeout/error
        """
        async with self._query_lock:
            self._query_counter += 1
            query_id = f"query_{self._query_counter}"

        # Create a future to hold the response
        response_future: asyncio.Future = asyncio.Future()
        self._query_futures[query_id] = response_future

        try:
            await transport.send_data("esp_local_ctrl/control", query_request)
            msg_source, data = await asyncio.wait_for(response_future, timeout=timeout)
            return (msg_source, data)

        except TimeoutError:
            return (None, None)
        except Exception:
            return (None, None)
        finally:
            self._query_futures.pop(query_id, None)

    async def start(self):
        """Start listening for incoming messages."""
        if self._running:
            print("Listener already running")
            return False

        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        print("HTTP message listener started")
        return True

    async def stop(self):
        """Stop listening for messages."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        print("HTTP message listener stopped")

    async def _listen_loop(self):
        """Main loop that continuously listens for HTTP messages."""
        while self._running:
            try:
                # Get socket from transport
                sock = self._get_socket()
                if not sock:
                    print("No socket available, stopping listener")
                    break

                # Receive data with timeout (60s)
                # Timeout is normal during idle periods - TCP keepalive maintains connection at kernel level
                try:
                    data = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, sock.recv, 4096),
                        timeout=60.0,
                    )
                except asyncio.TimeoutError:
                    # Timeout is normal when ESP32 is idle (no active reports)
                    # TCP keepalive maintains connection in background
                    continue
                except (ConnectionResetError, ConnectionRefusedError, BrokenPipeError, OSError) as e:
                    # Real socket errors - connection is broken
                    if self.client and hasattr(self.client, 'mark_connection_error'):
                        self.client.mark_connection_error()
                    await self._cancel_all_queries()
                    break
                except Exception as e:
                    # Unexpected errors - log but don't mark connection as failed
                    await asyncio.sleep(1.0)
                    continue

                if not data:
                    # Notify client of connection closure
                    if self.client and hasattr(self.client, 'mark_connection_error'):
                        if not getattr(self.client, '_connection_error', False):
                            self.client.mark_connection_error()
                    break

                async with self._buffer_lock:
                    self._buffer.extend(data)

                await self._process_buffer()

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1.0)

    async def _process_buffer(self):
        """Extract and process complete HTTP messages from buffer.

        For long-lived HTTP connections, process all complete messages in the buffer.
        Continue until buffer is empty or incomplete message is encountered.
        """
        error_count = 0
        max_consecutive_errors = 10
        
        while len(self._buffer) > 0 and error_count < max_consecutive_errors:
            try:
                msg_source, data, message_size = await handle_incoming_http_message(
                    bytes(self._buffer), self.security_ctx, verbose=False
                )

                if msg_source is None:
                    if message_size > 0:
                        async with self._buffer_lock:
                            self._buffer = bytearray(self._buffer[message_size:])
                        error_count = 0
                        continue
                    
                    # Special case: SET responses may be headerless
                    if len(self._buffer) >= 6 and self._buffer[:2] == b"\r\n":
                        msg_source = "set_response"
                        data = {"status": 0}
                        async with self._buffer_lock:
                            self._buffer = bytearray(self._buffer[6:])
                    else:
                        error_count = 0
                        break
                else:
                    error_count = 0

                # Remove processed message from buffer
                if msg_source not in ("set_response",):
                    if message_size > 0 and len(self._buffer) >= message_size:
                        async with self._buffer_lock:
                            self._buffer = bytearray(self._buffer[message_size:])
                    elif message_size > 0:
                        break
                    else:
                        break

                # Route message based on type
                if msg_source == "set_response":
                    pass  # Silently succeed
                elif msg_source == MessageSource.QUERY_RESPONSE:
                    if len(self._query_futures) > 0:
                        try:
                            query_id = next(iter(self._query_futures))
                            future = self._query_futures.pop(query_id, None)
                            if future and not future.done():
                                future.set_result((msg_source, data))
                        except Exception:
                            await self._invoke_callbacks(msg_source, data)
                    else:
                        await self._invoke_callbacks(msg_source, data)
                else:
                    await self._invoke_callbacks(msg_source, data)

            except Exception:
                error_count += 1
                await asyncio.sleep(0.1)
                continue

    async def _invoke_callbacks(self, msg_source, data):
        """Invoke all registered callbacks with message."""
        for callback in self.callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(msg_source, data)
                else:
                    callback(msg_source, data)
            except Exception as e:
                print(f"Error in callback: {e}")

    async def _cancel_all_queries(self):
        """Cancel all waiting queries (called when connection error detected)."""
        for query_id, future in list(self._query_futures.items()):
            if not future.done():
                future.set_exception(ConnectionError("Connection lost"))
        self._query_futures.clear()

    def _get_socket(self):
        """Extract socket from transport."""
        try:
            if self.transport and hasattr(self.transport, "conn"):
                if hasattr(self.transport.conn, "sock"):
                    return self.transport.conn.sock
        except Exception as e:
            print(f"Failed to get socket: {e}")
        return None


async def set_property_values(
    tp, security_ctx, props, indices, values, check_readonly=False, listener=None
):
    """Set property values on the device.

    Args:
        tp: Transport object
        security_ctx: Security context for encryption
        props: Property definitions (for readonly check)
        indices: List of property indices to set
        values: List of values to set
        check_readonly: Whether to check readonly flags
        listener: HTTPMessageListener instance (required for proper operation)

    Returns:
        True if successful, False otherwise
    """
    try:
        if check_readonly:
            for index in indices:
                if prop_is_readonly(props[index]):
                    raise RuntimeError("Cannot set value of Read-Only property")

        # Create the SET request
        message = proto_lc.set_prop_vals_request(security_ctx, indices, values)

        # Listener is required for unified message handling
        if not listener:
            raise RuntimeError(
                "Listener is required for set_property_values"
            )

        # Send through listener with Future-based waiting
        msg_source, parsed_data = await listener.send_query_and_wait(
            tp, message, timeout=10
        )

        if msg_source is None:
            print("Set property request timed out")
            return False

        # Extract status from response
        if isinstance(parsed_data, dict):
            return parsed_data.get("status", 0) == 0

        # Fallback: try to parse as set response
        return proto_lc.set_prop_vals_response(security_ctx, parsed_data)

    except RuntimeError as e:
        on_except(e)
        return False


def desc_format(*args):
    desc = ""
    for arg in args:
        desc += textwrap.fill(replace_whitespace=False, text=arg) + "\n"
    return desc


async def main():
    parser = argparse.ArgumentParser(add_help=False)

    parser = argparse.ArgumentParser(
        description="Control an ESP32 running esp_local_ctrl service"
    )

    parser.add_argument(
        "--version", dest="version", type=str, help="Protocol version", default=""
    )

    parser.add_argument(
        "--transport",
        dest="transport",
        type=str,
        help="transport i.e http/https/ble",
        default="https",
    )

    parser.add_argument(
        "--name",
        dest="service_name",
        type=str,
        help="BLE Device Name / HTTP Server hostname or IP",
        default="",
    )

    parser.add_argument(
        "--sec_ver",
        dest="secver",
        type=int,
        default=None,
        help=desc_format(
            "Protocomm security scheme used for secure "
            "session establishment. Accepted values are :",
            "\t- 0 : No security",
            "\t- 1 : X25519 key exchange + AES-CTR encryption",
            "\t- 2 : SRP6a + AES-GCM encryption",
            "\t      + Authentication using Proof of Possession (PoP)",
        ),
    )

    parser.add_argument(
        "--pop",
        dest="pop",
        type=str,
        default="",
        help=desc_format(
            "This specifies the Proof of possession (PoP) when security scheme 1 "
            "is used"
        ),
    )

    parser.add_argument(
        "--sec2_username",
        dest="sec2_usr",
        type=str,
        default="",
        help=desc_format("Username for security scheme 2 (SRP6a)"),
    )

    parser.add_argument(
        "--sec2_pwd",
        dest="sec2_pwd",
        type=str,
        default="",
        help=desc_format("Password for security scheme 2 (SRP6a)"),
    )

    parser.add_argument(
        "--sec2_gen_cred",
        help="Generate salt and verifier for security scheme 2 (SRP6a)",
        action="store_true",
    )

    parser.add_argument(
        "--sec2_salt_len",
        dest="sec2_salt_len",
        type=int,
        default=16,
        help=desc_format("Salt length for security scheme 2 (SRP6a)"),
    )

    parser.add_argument(
        "--dont-check-hostname",
        action="store_true",
        # If enabled, the certificate won't be rejected for hostname mismatch.
        # This option is hidden because it should be used only for testing purposes.
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        help="increase output verbosity",
        action="store_true",
    )

    args = parser.parse_args()

    if args.secver == 2 and args.sec2_gen_cred:
        if not args.sec2_usr or not args.sec2_pwd:
            raise ValueError(
                "Username/password cannot be empty for security scheme 2 (SRP6a)"
            )

        print("==== Salt-verifier for security scheme 2 (SRP6a) ====")
        security.sec2_gen_salt_verifier(
            args.sec2_usr, args.sec2_pwd, args.sec2_salt_len
        )
        sys.exit()

    if args.version != "":
        print(f"==== Esp_Ctrl Version: {args.version} ====")

    if args.service_name == "":
        args.service_name = "my_esp_ctrl_device"
        if args.transport == "http" or args.transport == "https":
            args.service_name += ".local"

    obj_transport = await get_transport(
        args.transport, args.service_name, not args.dont_check_hostname
    )
    if obj_transport is None:
        raise RuntimeError("Failed to establish connection")

    sec_patch_ver = 0
    # If security version not specified check in capabilities
    if args.secver is None:
        # First check if capabilities are supported or not
        if not await has_capability(obj_transport):
            print(
                'Security capabilities could not be determined, please specify "--sec_ver" explicitly'
            )
            raise ValueError("Invalid Security Version")

        # When no_sec is present, use security 0, else security 1
        args.secver = int(not await has_capability(obj_transport, "no_sec"))
        print(f"==== Security Scheme: {args.secver} ====")

    if args.secver == 1:
        if not await has_capability(obj_transport, "no_pop"):
            if len(args.pop) == 0:
                print("---- Proof of Possession argument not provided ----")
                exit(2)
        elif len(args.pop) != 0:
            print("---- Proof of Possession will be ignored ----")
            args.pop = ""

    if args.secver == 2:
        sec_patch_ver = await get_sec_patch_ver(obj_transport, args.verbose)
        if len(args.sec2_usr) == 0:
            args.sec2_usr = input("Security Scheme 2 - SRP6a Username required: ")
        if len(args.sec2_pwd) == 0:
            prompt_str = "Security Scheme 2 - SRP6a Password required: "
            args.sec2_pwd = getpass(prompt_str)

    obj_security = get_security(
        args.secver, sec_patch_ver, args.sec2_usr, args.sec2_pwd, args.pop, args.verbose
    )
    if obj_security is None:
        raise ValueError("Invalid Security Version")

    if args.version != "":
        print("\n==== Verifying protocol version ====")
        if not await version_match(obj_transport, args.version, args.verbose):
            raise RuntimeError("Error in protocol version matching")
        print("==== Verified protocol version successfully ====")

    print("\n==== Starting Session ====")
    if not await establish_session(obj_transport, obj_security):
        print(
            "Failed to establish session. Ensure that security scheme and proof of possession are correct"
        )
        raise RuntimeError("Error in establishing session")
    print("==== Session Established ====")

    # Create a listener for continuous message processing
    http_listener = HTTPMessageListener(obj_transport, obj_security, client=None, verbose=args.verbose)
    
    # Track active reports for display
    active_reports = []
    
    async def on_active_report(msg_source, data):
        """Handle incoming active reports from ESP32.
        
        Active reports are unsolicited messages sent by ESP32 when property values change.
        """
        if msg_source == MessageSource.ACTIVE_REPORT:
            # Store for display
            active_reports.append(data)
            # Always show active reports for visibility
            properties = data.get("properties", [])
            if properties:
                # Decode property values (same as get_all_property_values)
                for prop in properties:
                    if "value" in prop:
                        prop["value"] = decode_prop_value(prop, prop["value"])
                
                print(f"\n[Active Report] ESP32 sent {len(properties)} property update(s):")
                for prop in properties:
                    print(f"  - {prop.get('name', 'unknown')}: {prop.get('value')}")
    
    # Register callback to handle active reports
    http_listener.add_callback(on_active_report)
    
    # Start listener
    await http_listener.start()

    while True:
        # Create property count request (for unified handling)
        query_request = proto_lc.get_prop_vals_request(
            obj_security, list(range(10))
        )

        try:
            # Send query through listener and wait for response
            msg_source, parsed_data = await http_listener.send_query_and_wait(
                obj_transport, query_request, timeout=10
            )

            if msg_source is None:
                print("Query timeout or error")
                # Fallback to direct method
                properties = await get_all_property_values(obj_transport, obj_security)
            elif msg_source == MessageSource.QUERY_RESPONSE:
                # Extract properties from parsed data
                properties = (
                    parsed_data.get("properties", [])
                    if isinstance(parsed_data, dict)
                    else []
                )
                # Decode property values
                for prop in properties:
                    if "value" in prop and isinstance(prop["value"], bytes):
                        prop["value"] = decode_prop_value(prop, prop["value"])
            else:
                print(f"Unexpected message source: {msg_source}")
                properties = []
        except Exception as e:
            print(f"Error getting properties via listener: {e}")
            # Fallback to direct method
            properties = await get_all_property_values(obj_transport, obj_security)

        if len(properties) == 0:
            raise RuntimeError("Error in reading property value")

        print("\n==== Available Properties ====")
        print(
            "{0: >4} {1: <16} {2: <10} {3: <16} {4: <16}".format(
                "S.N.", "Name", "Type", "Flags", "Value"
            )
        )
        for i in range(len(properties)):
            print(
                "[{0: >2}] {1: <16} {2: <10} {3: <16} {4: <16}".format(
                    i + 1,
                    properties[i]["name"],
                    prop_typestr(properties[i]),
                    ["", "Read-Only"][prop_is_readonly(properties[i])],
                    str(properties[i]["value"]),
                )
            )

        select = 0
        while True:
            try:
                inval = input(
                    "\nSelect properties to set (0 to re-read, 'q' to quit, 'l' to listen for messages) : "
                )
                if inval.lower() == "q":
                    print("Quitting...")
                    await http_listener.stop()
                    exit(0)
                if inval.lower() == "l":
                    print("Listening for incoming messages... (press Ctrl+C to stop)")
                    # Listener is already collecting messages via callback
                    # Keep listening for 30 seconds
                    try:
                        await asyncio.sleep(30)
                    except KeyboardInterrupt:
                        print("Stopped listening")
                    break
                invals = inval.split(",")
                selections = [int(val) for val in invals]
                if min(selections) < 0 or max(selections) > len(properties):
                    raise ValueError("Invalid input")
                break
            except ValueError as e:
                print(str(e) + "! Retry...")

        if len(selections) == 1 and selections[0] == 0:
            continue

        set_values = []
        set_indices = []
        for select in selections:
            while True:
                inval = input(
                    "Enter value to set for property ("
                    + properties[select - 1]["name"]
                    + ") : "
                )
                value = encode_prop_value(
                    properties[select - 1],
                    str_to_prop_value(properties[select - 1], inval),
                )
                if value is None:
                    print("Invalid input! Retry...")
                    continue
                break
            set_values += [value]
            set_indices += [select - 1]

        if not await set_property_values(
            obj_transport,
            obj_security,
            properties,
            set_indices,
            set_values,
            listener=http_listener,
        ):
            print("Failed to set values!")


if __name__ == "__main__":
    asyncio.run(main())
