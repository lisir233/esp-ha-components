"""ESP HA Light platform."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import CONF_NODE_ID, DOMAIN, get_device_info
from .esp_iot import (
    LIGHT_COLOR_TEMP_MAX_KELVIN,
    LIGHT_COLOR_TEMP_MIN_KELVIN,
    LIGHT_EFFECT_MODES,
    convert_brightness_to_esp,
    convert_temp_kelvin_to_percent,
    parse_light_mode,
    parse_light_params,
    parse_light_update,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigType,
    async_add_entities: Callable[[list[LightEntity]], None],
) -> None:
    """Set up the ESPHome light platform."""

    api = config_entry.runtime_data
    if not api:
        return

    node_id = config_entry.data.get(CONF_NODE_ID)
    if not node_id:
        return

    entities = []
    discovered_entities = {}

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    entry_key = f"{node_id}_{config_entry.entry_id}"
    hass.data[DOMAIN][f"add_light_entities_{entry_key}"] = async_add_entities

    async def handle_light_discovered(event) -> None:
        """Handle light device discovery."""
        try:
            event_node_id = str(event.data.get("node_id", "")).lower()
            device_name = event.data.get("device_name", "")
            light_params = event.data.get("light_params", {})

            if event_node_id != node_id.lower():
                return

            entity_key = f"{event_node_id}_light"

            if entity_key not in discovered_entities:
                device_info = {
                    "node_id": event_node_id,
                    "name": device_name or f"ESP {event_node_id}",
                }

                light_entity = ESPHomeLight(
                    api=api,
                    device_info=device_info,
                    light_name=device_name,
                    light_params=light_params,
                    is_discovery=True,
                )

                discovered_entities[entity_key] = light_entity
                async_add_entities([light_entity])

        except Exception:  # noqa: BLE001
            pass

    # Subscribe to discovery events through event manager
    unsub = hass.bus.async_listen(f"{DOMAIN}_light_discovered", handle_light_discovered)
    config_entry.async_on_unload(unsub)

    async_add_entities(entities)


class ESPHomeLight(LightEntity):
    """ESP-RainMaker light entity."""

    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.HS, ColorMode.COLOR_TEMP}
    _attr_color_mode = ColorMode.HS
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_min_color_temp_kelvin = LIGHT_COLOR_TEMP_MIN_KELVIN
    _attr_max_color_temp_kelvin = LIGHT_COLOR_TEMP_MAX_KELVIN

    def __init__(
        self,
        api: Any,
        device_info: dict,
        light_name: str,
        light_params: dict | None = None,
        is_discovery: bool = False,
    ) -> None:
        """Initialize the light entity."""
        self._api = api
        self._device_info = device_info
        self._light_name = light_name
        self._is_discovery = is_discovery

        node_id = str(device_info.get("node_id", "")).replace(":", "").lower()

        self._attr_name = "Light"  # Simple name, HA combines with device name
        self._attr_has_entity_name = True  # Enable automatic naming
        if is_discovery:
            self._attr_unique_id = f"{DOMAIN}_{node_id}_light"
        else:
            self._attr_unique_id = f"{DOMAIN}_{node_id}_light_legacy"

        self._attr_device_info = get_device_info(
            device_info["node_id"], device_info["name"]
        )

        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_hs_color = (0, 0)
        self._attr_color_temp_kelvin = 4000

        self._intensity = 25
        self._light_mode = 0
        self._color_temperature = 25.4

        if light_params:
            parsed = parse_light_params(light_params)
            self._attr_is_on = parsed["is_on"]
            self._attr_brightness = parsed["brightness"]
            self._attr_hs_color = parsed["hs_color"]
            self._intensity = parsed["intensity"]
            self._light_mode = parsed["light_mode"]
            self._color_temperature = parsed["color_temp"]
            if "color_temp_kelvin" in parsed:
                self._attr_color_temp_kelvin = parsed["color_temp_kelvin"]

        self._unsub_update = None

    @property
    def effect_list(self) -> list[str]:
        """Return the list of supported effects (light modes)."""
        return LIGHT_EFFECT_MODES

    @property
    def effect(self) -> str:
        """Return the current effect (light mode)."""
        return f"Mode {self._light_mode}"

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return additional state attributes showing ESP32 light parameters."""
        # Always show ESP32 reported data - CCT value is preserved unless explicitly updated
        return {
            "intensity": self._intensity,
            "light_mode": self._light_mode,
            "color_temperature_celsius": self._color_temperature if self._color_temperature is not None else 25.4,
        }

    async def async_added_to_hass(self) -> None:
        """Register update listener."""
        await super().async_added_to_hass()

        self._unsub_update = self.hass.bus.async_listen(
            f"{DOMAIN}_light_update", self._handle_light_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up resources."""
        if self._unsub_update:
            self._unsub_update()

    async def _handle_light_update(self, event) -> None:
        """Handle light state updates."""
        try:
            event_id = str(event.data.get("node_id", "")).lower()
            device_id = str(self._device_info["node_id"]).lower()

            if event_id != device_id:
                return

            light_data = event.data.get("light_data", {})
            if not light_data:
                return

            old_state = self._get_current_state()

            current_power = self._attr_is_on
            update_power = light_data.get("power", current_power)

            if current_power and not update_power and current_power != update_power:
                return

            self._update_state_from_data(light_data)

            new_state = self._get_current_state()
            if old_state != new_state:
                self.async_write_ha_state()

        except Exception:
            _LOGGER.exception("Failed to handle light update")

    def _get_current_state(self) -> tuple:
        """Get current state as a comparable tuple."""
        return (
            self._attr_is_on,
            self._attr_brightness,
            self._attr_hs_color,
            self._intensity,
            self._light_mode,
            self._color_temperature,
        )

    def _update_state_from_data(self, data: dict) -> None:
        """Update entity state from received data."""
        # Get current state for parsing
        current_state = {
            "is_on": self._attr_is_on,
            "brightness": self._attr_brightness,
            "hs_color": self._attr_hs_color,
            "intensity": self._intensity,
            "light_mode": self._light_mode,
            "color_temp": self._color_temperature,
            "color_temp_kelvin": self._attr_color_temp_kelvin,
        }

        # Parse updates using utility function
        updates = parse_light_update(data, current_state)

        # Apply updates to entity attributes
        if "is_on" in updates:
            self._attr_is_on = updates["is_on"]
        if "brightness" in updates:
            self._attr_brightness = updates["brightness"]
        if "hs_color" in updates:
            self._attr_hs_color = updates["hs_color"]
        if "intensity" in updates:
            self._intensity = updates["intensity"]
        if "light_mode" in updates:
            self._light_mode = updates["light_mode"]
        if "color_temp" in updates:
            self._color_temperature = updates["color_temp"]
        if "color_temp_kelvin" in updates:
            self._attr_color_temp_kelvin = updates["color_temp_kelvin"]

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on light using ESP-RainMaker local control."""
        node_id = str(self._device_info["node_id"]).lower()

        try:
            # First ensure the light is powered on
            if not await self._set_property("Power", True):
                _LOGGER.error("Failed to turn on light: %s", node_id)
                return

            self._attr_is_on = True

            # Handle brightness
            if ATTR_BRIGHTNESS in kwargs:
                brightness_esp = convert_brightness_to_esp(kwargs[ATTR_BRIGHTNESS])
                if await self._set_property("Brightness", brightness_esp):
                    self._attr_brightness = kwargs[ATTR_BRIGHTNESS]

            # Handle color
            if ATTR_HS_COLOR in kwargs:
                hue, saturation = kwargs[ATTR_HS_COLOR]
                if await self._set_property(
                    "Hue", int(hue)
                ) and await self._set_property("Saturation", int(saturation)):
                    self._attr_hs_color = (hue, saturation)

            # Handle color temperature
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
                temp_percent = convert_temp_kelvin_to_percent(temp_kelvin)

                if await self._set_property("cct", temp_percent):
                    self._attr_color_temp_kelvin = temp_kelvin
                    self._color_temperature = temp_percent

            # Handle effect/mode
            if ATTR_EFFECT in kwargs:
                effect = kwargs[ATTR_EFFECT]
                mode_num = parse_light_mode(effect)
                if mode_num is not None:
                    if await self._set_property("Light Mode", mode_num):
                        self._light_mode = mode_num
                else:
                    _LOGGER.warning("Invalid light mode format: %s", effect)

            # Always update state after turn on command
            self.async_write_ha_state()

        except Exception:
            _LOGGER.exception("Failed to turn on light %s", node_id)

    async def _set_property(self, property_name: str, value: Any) -> bool:
        """Set a property on the device with error handling."""
        try:
            success = await self._api.set_local_ctrl_property(
                self._device_info["node_id"], property_name, value
            )
        except Exception:
            _LOGGER.exception(
                "Error setting property %s=%s for %s",
                property_name,
                value,
                self._device_info["node_id"],
            )
            return False
        else:
            if success:
                _LOGGER.debug(
                    "Property set successfully: %s=%s for %s",
                    property_name,
                    value,
                    self._device_info["node_id"],
                )
            else:
                _LOGGER.warning(
                    "Failed to set property: %s=%s for %s",
                    property_name,
                    value,
                    self._device_info["node_id"],
                )
            return success

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off light using ESP-RainMaker local control."""
        try:
            if await self._set_property("Power", False):
                self._attr_is_on = False
                self.async_write_ha_state()
            else:
                _LOGGER.warning(
                    "Failed to turn off light: %s", self._device_info["node_id"]
                )

        except Exception:
            _LOGGER.exception(
                "Error turning off light %s",
                self._device_info["node_id"],
            )

    async def async_update(self) -> None:
        """Update light state.

        This method is not used as ESP32 uses push reporting mode,
        no active polling needed.
        """
