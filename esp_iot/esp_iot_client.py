"""Security and connection management for ESP devices."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Connection timeouts
POP_TEST_DELAY = 1.0
POP_TEST_DISCONNECT_DELAY = 10.0


class ESPSecurityManager:
    """Handle device security detection and PoP validation."""

    def __init__(self, esp_local_ctrl_module) -> None:
        """Initialize security manager.

        Args:
            esp_local_ctrl_module: The esp_local_ctrl module for transport/security
        """
        self.esp_local_ctrl = esp_local_ctrl_module
        self._cached_security_info: dict[str, Any] | None = None

    def clear_cache(self) -> None:
        """Clear cached security information."""
        self._cached_security_info = None

    async def detect_device_security(
        self, ip: str, port: int, default_port: int = 80
    ) -> dict[str, Any]:
        """Detect device security configuration.

        Args:
            ip: Device IP address
            port: Device port
            default_port: Default port if not 443

        Returns:
            Dictionary with security_version and pop_required keys
        """
        if self._cached_security_info is not None:
            return self._cached_security_info

        try:
            transport_type = "https" if port == 443 else "http"
            service_name = f"{ip}:{port}"

            transport = await self.esp_local_ctrl.get_transport(
                transport_type, service_name, check_hostname=False
            )

            if not transport:
                return {"security_version": 0, "pop_required": False}

            try:
                security_version = 1
                pop_required = True

                try:
                    version_response = await transport.send_data_and_receive(
                        "esp_local_ctrl/version", "none"
                    )

                    version_info = json.loads(version_response)

                    if (
                        "local_ctrl" in version_info
                        and "sec_ver" in version_info["local_ctrl"]
                    ):
                        detected_sec_ver = version_info["local_ctrl"]["sec_ver"]
                        security_version = detected_sec_ver

                        if security_version == 0:
                            pop_required = False
                        else:
                            has_no_pop = await self.esp_local_ctrl.has_capability(
                                transport, "no_pop", verbose=False
                            )
                            pop_required = not has_no_pop
                    else:
                        has_no_sec = await self.esp_local_ctrl.has_capability(
                            transport, "no_sec", verbose=False
                        )
                        has_no_pop = await self.esp_local_ctrl.has_capability(
                            transport, "no_pop", verbose=False
                        )

                        if has_no_sec and has_no_pop:
                            security_version = 0
                            pop_required = False
                        elif has_no_sec and not has_no_pop:
                            security_version = 1
                            pop_required = True
                        else:
                            security_version = 1
                            pop_required = not has_no_pop

                except Exception:
                    _LOGGER.exception("Error querying security for %s", service_name)

                self._cached_security_info = {
                    "security_version": security_version,
                    "pop_required": pop_required,
                }

                return self._cached_security_info

            except Exception:
                return {"security_version": 1, "pop_required": True}

        except Exception:
            return {"security_version": 0, "pop_required": False}

    async def test_pop_connection(
        self, ip: str, pop: str, port: int
    ) -> bool:
        """Test if the provided PoP works for connecting to the device.

        Args:
            ip: Device IP address
            pop: Proof of Possession string to test
            port: Device port

        Returns:
            True if PoP is valid, False otherwise
        """
        try:
            await asyncio.sleep(POP_TEST_DELAY)

            transport = None
            try:
                service_name = f"{ip}:{port}"
                transport = await self.esp_local_ctrl.get_transport(
                    sel_transport="http",
                    service_name=service_name,
                    check_hostname=False,
                )

                if not transport:
                    return False

                security_ctx = self.esp_local_ctrl.get_security(
                    secver=1,
                    sec_patch_ver=None,
                    username=None,
                    password=None,
                    pop=pop or "",
                    verbose=False,
                )

                if not security_ctx:
                    return False

                session_ok = await self.esp_local_ctrl.establish_session(
                    transport, security_ctx
                )

                return bool(session_ok)

            finally:
                if transport and hasattr(transport, "close"):
                    with contextlib.suppress(Exception):
                        transport.close()

                await asyncio.sleep(POP_TEST_DISCONNECT_DELAY)

        except Exception:
            return False
