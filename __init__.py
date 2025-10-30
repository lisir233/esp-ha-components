"""The ESP HA integration."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CONF_NODE_ID, DEFAULT_PORT, DEVICE_MANUFACTURER, DOMAIN
from .esp_iot import ESPHomeAPI
from .panel import async_register_panel

_LOGGER = logging.getLogger(__name__)

# Standard Home Assistant config schema
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Supported platforms
PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SENSOR,
]

type ESPHomeConfigEntry = ConfigEntry[ESPHomeAPI]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the ESP HA integration.

    Args:
        hass: Home Assistant instance
        config: Configuration dictionary

    Returns:
        True to indicate successful setup
    """
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ESPHomeConfigEntry) -> bool:
    """Set up ESP HA device from a config entry.

    Args:
        hass: Home Assistant instance
        entry: Config entry for this device

    Returns:
        True if setup was successful

    Raises:
        ConfigEntryNotReady: If device connection fails
    """
    if CONF_NODE_ID not in entry.data:
        _LOGGER.error("Configuration entry missing required node_id")
        return False

    node_id: str = entry.data[CONF_NODE_ID]
    host: str = entry.data.get(CONF_HOST, "")
    port: int = entry.data.get(CONF_PORT, DEFAULT_PORT)

    if not host:
        _LOGGER.error("Configuration entry missing host address")
        return False

    hass.data.setdefault(DOMAIN, {})

    if "shared_api" not in hass.data[DOMAIN]:
        api = ESPHomeAPI(hass, DOMAIN)
        hass.data[DOMAIN]["shared_api"] = api

        try:
            await api.start_services()
        except Exception as err:
            raise ConfigEntryNotReady(f"Failed to initialize services: {err}") from err

    api = hass.data[DOMAIN]["shared_api"]
    entry.runtime_data = api

    try:
        if not await api.register_device(node_id, host, port):
            raise ConfigEntryNotReady(f"Failed to connect to device at {host}:{port}")

        api.register_config_entry(node_id, entry.entry_id)

    except (ConnectionError, OSError) as err:
        raise ConfigEntryNotReady(f"Connection error: {err}") from err
    except TimeoutError as err:
        raise ConfigEntryNotReady(f"Connection timed out: {err}") from err

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, node_id.lower())},
        name=entry.title or f"ESP Device {node_id}",
        manufacturer=DEVICE_MANUFACTURER,
        model="ESP32 Device",
        configuration_url=f"http://{host}:{port}",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_register_panel(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ESPHomeConfigEntry) -> bool:
    """Unload an ESP HA config entry.

    Args:
        hass: Home Assistant instance
        entry: Config entry to unload

    Returns:
        True if unload was successful
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    if api := entry.runtime_data:
        node_id = entry.data.get(CONF_NODE_ID)
        if node_id:
            await api.unregister_device(node_id)

    remaining_entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]

    if not remaining_entries and DOMAIN in hass.data:
        if "shared_api" in hass.data[DOMAIN]:
            try:
                await hass.data[DOMAIN]["shared_api"].cleanup()
            finally:
                hass.data[DOMAIN].pop("shared_api", None)
                if not hass.data[DOMAIN]:
                    hass.data.pop(DOMAIN, None)

    return True
