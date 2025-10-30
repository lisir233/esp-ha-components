"""Custom panel for IMU Gesture big icon display."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN
from .esp_iot import (
    DEFAULT_DEVICE_NAME,
    DEFAULT_GESTURE_STATE,
    PANEL_AUTO_REFRESH_INTERVAL,
    PANEL_GESTURE_ICON,
    PANEL_HTML_STYLES,
    build_gesture_panel_html,
    filter_gesture_sensors,
    generate_panel_title,
    generate_panel_url_path,
    get_device_node_id_from_identifiers,
    get_gesture_sensors_for_device,
)

_LOGGER = logging.getLogger(__name__)


class GestureImageView(HomeAssistantView):
    """View to serve gesture images from ESP_HA component."""

    url = "/api/esp_ha/gesture_images/{filename}"
    name = "api:esp_ha:gesture_images"
    requires_auth = False

    async def get(self, request, filename):
        """Serve gesture image file."""
        component_dir = Path(__file__).parent
        image_path = component_dir / "gesture_images" / filename

        if not image_path.is_file():
            return web.Response(status=404, text="Image not found")

        return web.FileResponse(image_path)


class IMUGesturePanel(HomeAssistantView):
    """Custom panel view for IMU Gesture display."""

    requires_auth = False
    url = "/api/panel/esp_imu_gesture/{device_id}"
    name = "api:panel:esp_imu_gesture"

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request, device_id):
        """Serve the panel HTML."""
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)

        gesture_sensors = get_gesture_sensors_for_device(entity_reg, device_id, DOMAIN)

        if not gesture_sensors:
            return self.json_message("No gesture sensor found", 404)

        entity_id = gesture_sensors[0].entity_id
        device_entry = device_reg.async_get(device_id)
        device_name = device_entry.name if device_entry else DEFAULT_DEVICE_NAME

        state = self.hass.states.get(entity_id)

        gesture_key = state.attributes.get("gesture", DEFAULT_GESTURE_STATE) if state else DEFAULT_GESTURE_STATE
        gesture_display = state.state if state else "Unknown"

        image_url = f"/api/esp_ha/gesture_images/{gesture_key}.svg"

        html = build_gesture_panel_html(
            device_name=device_name,
            gesture_key=gesture_key,
            gesture_display=gesture_display,
            image_url=image_url,
            refresh_interval=PANEL_AUTO_REFRESH_INTERVAL,
            styles=PANEL_HTML_STYLES,
        )
        
        return web.Response(text=html, content_type="text/html")


@callback
def async_register_panel(hass: HomeAssistant) -> None:
    """Register IMU Gesture panels for each device."""

    if not hasattr(hass.data.setdefault(DOMAIN, {}), "_panel_views_registered"):
        hass.http.register_view(GestureImageView())
        hass.http.register_view(IMUGesturePanel(hass))
        hass.data[DOMAIN]["_panel_views_registered"] = True

    async def async_load_panel():
        """Load the panel configuration."""
        await asyncio.sleep(1)

        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)

        gesture_sensors = filter_gesture_sensors(entity_reg, DOMAIN)

        if not gesture_sensors:
            return

        device_sensors: dict[str, dict[str, Any]] = {}
        for sensor_entry in gesture_sensors:
            if sensor_entry.device_id:
                device_entry = device_reg.async_get(sensor_entry.device_id)
                if device_entry:
                    node_id = get_device_node_id_from_identifiers(device_entry, DOMAIN)

                    device_name = (
                        device_entry.name_by_user
                        or device_entry.name
                        or node_id
                        or DEFAULT_DEVICE_NAME
                    )

                    if device_entry.id not in device_sensors:
                        device_sensors[device_entry.id] = {
                            "device_name": device_name,
                            "node_id": node_id,
                        }

        if "_registered_panels" not in hass.data[DOMAIN]:
            hass.data[DOMAIN]["_registered_panels"] = set()

        registered_panels = hass.data[DOMAIN]["_registered_panels"]

        try:
            for device_id, device_info in device_sensors.items():
                node_id = device_info["node_id"]
                device_name = device_info["device_name"]

                panel_title = generate_panel_title(node_id, device_name)
                url_path = generate_panel_url_path(node_id, device_id, device_name)

                if url_path in registered_panels:
                    continue

                async_register_built_in_panel(
                    hass,
                    component_name="iframe",
                    sidebar_title=panel_title,
                    sidebar_icon=PANEL_GESTURE_ICON,
                    frontend_url_path=url_path,
                    config={"url": f"/api/panel/esp_imu_gesture/{device_id}"},
                    require_admin=False,
                )

                registered_panels.add(url_path)

        except Exception:
            _LOGGER.exception("Failed to register IMU Gestures panel")

    @callback
    def entity_registry_updated(event):
        """Handle entity registry update events."""
        action = event.data.get("action")
        entity_id = event.data.get("entity_id", "")

        if action == "create" and "_imu_gesture" in entity_id:
            hass.async_create_task(async_load_panel())

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, entity_registry_updated)

    hass.async_create_task(async_load_panel())
