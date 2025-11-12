"""ESP HA Number platform for threshold controls."""

from __future__ import annotations

from collections.abc import Callable
import datetime
import logging
import time
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import (
    CONF_NODE_ID,
    DOMAIN,
    get_device_info,
)
from .esp_iot import (
    THRESHOLD_SENSOR_TYPES,
    get_esp32_threshold_param_name,
    get_number_device_class_and_unit,
    get_number_range_config,
    get_number_sensor_display_name,
    get_threshold_icon,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: Callable[[list[NumberEntity]], None],
) -> None:
    """Set up ESP HA number platform for threshold controls."""

    api = config_entry.runtime_data
    if not api:
        return None

    node_id = config_entry.data.get(CONF_NODE_ID)
    if not node_id:
        return None

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    entry_key = f"{node_id}_{config_entry.entry_id}"
    hass.data[DOMAIN][f"add_number_entities_{entry_key}"] = async_add_entities

    entities = []

    cache_key = f"discovered_numbers_{node_id}"
    if cache_key not in hass.data[DOMAIN]:
        hass.data[DOMAIN][cache_key] = {}
    discovered_entities = hass.data[DOMAIN][cache_key]

    # Listen for battery parameter discovery events
    async def handle_battery_param_discovered_for_numbers(event):
        """Handle battery parameter discovery event."""
        try:
            event_data = event.data
            event_node_id = event_data.get("node_id", "").replace(":", "").lower()

            if event_node_id != node_id.lower():
                _LOGGER.debug(
                    "Battery parameter discovery event for different device (%s != %s), ignoring",
                    event_node_id,
                    node_id.lower(),
                )
                return

            device_info = event_data.get("device_info", {})
            param_name = event_data.get("param_name", "")

            # Extract device_name from device_info if not provided in event
            device_name = event_data.get("device_name", "") or device_info.get(
                "name", f"ESP {event_node_id}"
            )

            if not event_node_id or not device_info or not param_name:
                _LOGGER.warning(
                    "Incomplete battery parameter discovery data: %s", event_data
                )
                return

            # Check for battery configuration parameters
            if param_name == "Battery Low Threshold":
                entity_key = f"{event_node_id}_battery_low_threshold"
                if entity_key not in discovered_entities:
                    battery_threshold_entity = ESPHomeBatteryConfigNumber(
                        hass=hass,
                        node_id=event_node_id,
                        device_name=device_name,
                        device_info=device_info,
                        param_type="battery_low_threshold",
                        param_name=param_name,
                    )
                    discovered_entities[entity_key] = battery_threshold_entity
                    new_entities = [battery_threshold_entity]
                    async_add_entities(new_entities)
                    _LOGGER.info("Created battery low threshold entity: %s", entity_key)

            elif param_name == "Battery Sampling Interval":
                entity_key = f"{event_node_id}_battery_sampling_interval"
                if entity_key not in discovered_entities:
                    battery_sampling_entity = ESPHomeBatteryConfigNumber(
                        hass=hass,
                        node_id=event_node_id,
                        device_name=device_name,
                        device_info=device_info,
                        param_type="battery_sampling_interval",
                        param_name=param_name,
                    )
                    discovered_entities[entity_key] = battery_sampling_entity
                    new_entities = [battery_sampling_entity]
                    async_add_entities(new_entities)

        except Exception:
            _LOGGER.exception("Failed to handle battery parameter discovery event")

    # Listen for number platform discovery events to create battery config number entities
    async def handle_number_platform_discovered(event):
        """Handle number platform discovery event to create battery config number entities."""
        try:
            event_data = event.data
            event_node_id = event_data.get("node_id", "").replace(":", "").lower()
            device_info = event_data.get("device_info", {})
            platform = event_data.get("platform", "")

            if platform != "number" or not event_node_id or not device_info:
                return

            # Check for battery config entities
            entities = event_data.get("entities", [])
            for entity in entities:
                if entity.get("entity_type") == "battery_config":
                    param_name = entity.get("param_name", "")

                    if param_name == "Battery Low Threshold":
                        entity_key = f"{event_node_id}_battery_low_threshold"
                        if entity_key not in discovered_entities:
                            battery_threshold_entity = ESPHomeBatteryConfigNumber(
                                hass=hass,
                                node_id=event_node_id,
                                device_name=device_info.get("name", ""),
                                device_info=device_info,
                                param_type="battery_low_threshold",
                                param_name=param_name,
                            )
                            discovered_entities[entity_key] = battery_threshold_entity
                            new_entities = [battery_threshold_entity]
                            async_add_entities(new_entities)

                    elif param_name == "Battery Sampling Interval":
                        entity_key = f"{event_node_id}_battery_sampling_interval"
                        if entity_key not in discovered_entities:
                            battery_sampling_entity = ESPHomeBatteryConfigNumber(
                                hass=hass,
                                node_id=event_node_id,
                                device_name=device_info.get("name", ""),
                                device_info=device_info,
                                param_type="battery_sampling_interval",
                                param_name=param_name,
                            )
                            discovered_entities[entity_key] = battery_sampling_entity
                            new_entities = [battery_sampling_entity]
                            async_add_entities(new_entities)

        except Exception:
            _LOGGER.exception("Failed to handle number platform discovery event")

    # Listen for sensor discovery events to create corresponding threshold number entities
    async def handle_sensor_discovered_for_numbers(event):
        """Handle sensor discovery event to create corresponding threshold number entities."""
        try:
            # 优先使用设备信息中的node_id
            device_info = event.data.get("device_info", {})
            event_node_id = (
                device_info.get("node_id")
                or event.data.get("node_id")
                or event.data.get("mac")
            )

            if not event_node_id:
                _LOGGER.error("Sensor discovery event missing node_id and mac")
                return

            # Normalize node_id before comparison
            event_node_id = str(event_node_id).replace(":", "").strip().lower()

            # Filter: only process devices managed by this config entry
            if event_node_id != node_id.lower():
                _LOGGER.debug(
                    "Sensor discovery event for different device (%s != %s), skipping",
                    event_node_id,
                    node_id.lower(),
                )
                return

            # 使用事件中的node_id (already normalized)
            processed_node_id = event_node_id
            raw_type = event.data.get("raw_type", "")  # 优先使用原始类型
            sensor_type = raw_type or event.data.get(
                "sensor_type", ""
            )  # 如果没有原始类型则使用 sensor_type

            if not sensor_type:
                _LOGGER.error("Sensor discovery event missing sensor_type")
                return

            sensor_name = event.data.get("sensor_name", "") or event.data.get(
                "device_name", ""
            )
            device_info = event.data.get("device_info", {})

            if not device_info:
                device_info = {"node_id": processed_node_id, "name": sensor_name}

            # 过滤掉不需要阈值的传感器类型
            if sensor_type not in THRESHOLD_SENSOR_TYPES:
                return

            # Check API availability before proceeding
            if not api:
                _LOGGER.error("API instance is None, cannot create Number entity")
                return

            _LOGGER.debug("API instance available, creating Number entity: %s", type(api).__name__)

            # Create min and max threshold number entities
            new_entities = []
            for threshold_type in ["min", "max"]:
                try:
                    # Generate unique entity key for each sensor
                    entity_key = (
                        f"{processed_node_id}_{sensor_type}_{threshold_type}_threshold"
                    )

                    # Check if entity already exists
                    if entity_key in discovered_entities:
                        continue

                    # 使用事件中的完整 device_info
                    device_info = event.data.get("device_info", {}).copy()
                    if not device_info.get("node_id"):
                        device_info["node_id"] = processed_node_id
                    if not device_info.get("name"):
                        device_info["name"] = f"ESP {processed_node_id}"

                    # 创建阈值数字实体 - 修复：传递正确的参数
                    _LOGGER.debug(
                        "正在创建阈值实体: type=%s, threshold=%s, device=%s",
                        sensor_type,
                        threshold_type,
                        device_info,
                    )

                    # Check if API is available
                    if not api:
                        _LOGGER.error(
                            "API instance unavailable, cannot create threshold number entity: %s", entity_key
                        )
                        continue

                    threshold_entity = ESPHomeThresholdNumber(
                        hass=hass,  # 添加hass参数
                        api=api,  # 使用从entry_data获取的api实例
                        device_info=device_info,
                        sensor_type=sensor_type,
                        threshold_type=threshold_type,
                        sensor_name=sensor_name,
                    )

                    # Add to discovered entities list
                    discovered_entities[entity_key] = threshold_entity
                    new_entities.append(threshold_entity)
                except Exception:
                    _LOGGER.exception(
                        "Failed to create threshold entity: %s - %s",
                        sensor_type,
                        threshold_type,
                    )

            # Immediately add newly created Number entities to Home Assistant
            if new_entities:
                async_add_entities(new_entities)

        except Exception:
            _LOGGER.exception("Failed to handle sensor discovery event for number entities")

    # 注册事件监听器 - 使用正确的事件名称
    event_types = [
        f"{DOMAIN}_sensor_discovered",
        f"{DOMAIN}_sensor_discovered",
        f"{DOMAIN}_sensor_imu_gesture_discovered",
    ]

    for event_type in event_types:
        hass.bus.async_listen(event_type, handle_sensor_discovered_for_numbers)

    # 注册电池参数发现事件监听器
    hass.bus.async_listen(
        f"{DOMAIN}_battery_param_discovered",
        handle_battery_param_discovered_for_numbers,
    )
    # 注册number平台发现事件监听器
    hass.bus.async_listen(
        f"{DOMAIN}_platform_discovered", handle_number_platform_discovered
    )

    # 补偿机制：平台启动后回放已有的已发现传感器，创建对应的阈值数字实体
    # Use per-device sensor cache (only replay this device's sensors)
    try:
        sensor_cache_key = f"discovered_sensors_{node_id}"
        discovered_sensors = hass.data.get(DOMAIN, {}).get(sensor_cache_key, {})
        if discovered_sensors:
            for entity_key, entity in list(discovered_sensors.items()):
                try:
                    # 兼容两种存储形式：直接实体或包含实体的字典（IMU）
                    sensor_entity = (
                        entity.get("entity") if isinstance(entity, dict) else entity
                    )
                    if not sensor_entity:
                        continue
                    sensor_type = getattr(sensor_entity, "_sensor_type", "").lower()
                    entity_node_id = getattr(sensor_entity, "_device_info", {}).get(
                        "node_id", ""
                    )
                    sensor_name = getattr(sensor_entity, "name", None) or getattr(
                        sensor_entity, "_attr_name", ""
                    )
                    if not entity_node_id or not sensor_type:
                        continue

                    # Per-device cache already filtered, but double-check for safety
                    if str(entity_node_id).replace(":", "").lower() != node_id.lower():
                        continue
                    # 仅为需要阈值的类型创建
                    if sensor_type not in [
                        "temperature",
                        "ambient_temperature",
                        "humidity",
                        "ambient_humidity",
                        "pressure",
                        "illuminance",
                        "co2",
                        "voc",
                        "pm25",
                    ]:
                        continue
                    # 触发与实时相同的发现事件，驱动创建Number实体
                    hass.bus.async_fire(
                        f"{DOMAIN}_sensor_discovered",
                        {
                            "node_id": str(entity_node_id).replace(":", "").lower(),
                            "sensor_type": sensor_type,
                            "sensor_name": sensor_name,
                            "device_info": {
                                "node_id": entity_node_id,
                                "name": f"ESP {entity_node_id!s}",
                            },
                        },
                    )
                except Exception:
                    _LOGGER.exception("Failed to replay sensor: key=%s", entity_key)
        else:
            _LOGGER.debug("No discovered sensors to replay")
    except Exception:
        _LOGGER.exception("Failed to replay discovered sensors")

    # Note: ESP32 threshold data updates are now handled by each threshold entity
    # in their async_added_to_hass() method. No global listener needed to avoid
    # duplicate event processing (previously caused 15 handlers per event).

    # Register service: manually sync all thresholds to ESP32
    if not hass.services.has_service(DOMAIN, "sync_all_thresholds_to_esp32"):

        async def sync_all_thresholds_to_esp32(call):
            """Sync all threshold number entity values to ESP32 devices."""
            try:
                target_node_id = (
                    call.data.get("node_id", "").lower()
                    if call.data.get("node_id")
                    else None
                )
                synced_count = 0
                threshold_config = {}

                # 遍历所有阈值数字实体
                for entity_key, threshold_entity in discovered_entities.items():
                    try:
                        # 如果指定了node_id，只同步该设备的阈值
                        if target_node_id and not entity_key.startswith(target_node_id):
                            continue

                        # 发送阈值到ESP32
                        await threshold_entity.async_send_to_esp32()
                        synced_count += 1

                        # 记录同步的配置
                        threshold_config[threshold_entity.entity_id] = {
                            "sensor_type": threshold_entity._sensor_type,
                            "threshold_type": threshold_entity._threshold_type,
                            "value": threshold_entity.native_value,
                            "node_id": threshold_entity._device_info["node_id"],
                        }

                    except Exception:
                        _LOGGER.exception("Failed to sync threshold entity: %s", entity_key)

                return {
                    "status": "success",
                    "synced_count": synced_count,
                    "target_node_id": target_node_id or "all_devices",
                    "threshold_config": threshold_config,
                }

            except Exception:
                _LOGGER.exception("Failed to sync all thresholds to ESP32")
                return {"status": "error", "message": "Sync failed"}

        hass.services.async_register(
            DOMAIN, "sync_all_thresholds_to_esp32", sync_all_thresholds_to_esp32
        )

    # If initial load, immediately add already created entities
    if entities:
        async_add_entities(entities)

    # Return True to indicate platform setup success
    return True

class ESPHomeThresholdNumber(NumberEntity):
    """ESP HA threshold number entity."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_mode = NumberMode.BOX  # Use input box mode for precise input

    def __init__(
        self,
        hass: HomeAssistant,
        api: Any,
        device_info: dict,
        sensor_type: str,
        threshold_type: str,  # "min" or "max"
        sensor_name: str,
    ) -> None:
        """Initialize the threshold number entity."""
        # Store hass instance
        NumberEntity.__init__(self)
        self._hass = hass
        # Note: api parameter is not stored to avoid JSON serialization issues
        self._device_info = device_info
        self._sensor_type = sensor_type
        self._threshold_type = threshold_type
        self._sensor_name = sensor_name

        # 生成唯一ID - 保持node_id的原始格式
        node_id = (
            str(device_info.get("node_id", "unknown")).replace(":", "").strip().lower()
        )

        # 使用新版本unique_id
        self._attr_unique_id = (
            f"{DOMAIN}_{node_id}_{sensor_type}_{threshold_type}_threshold"
        )

        # 设置实体名称 - 使用has_entity_name，让HA自动组合设备名
        sensor_display_name = get_number_sensor_display_name(sensor_type)
        threshold_display_name = "Min" if threshold_type == "min" else "Max"
        self._attr_name = f"{sensor_display_name} {threshold_display_name} Threshold"
        self._attr_has_entity_name = True

        # 设置设备类和单位
        self._setup_device_class_and_unit()

        # 设置数值范围和步长
        self._setup_number_range()

        # 设置设备关联 - 使用导入的get_device_info函数
        self._attr_device_info = get_device_info(
            device_info["node_id"], device_info["name"]
        )

        # 不设置entity_category，让阈值实体显示在主要控制区域（类似传感器）
        # 这样它们会出现在Overview和主要实体列表中

        # 初始状态
        self._attr_available = True
        self._last_esp32_sync = None

        _LOGGER.debug("创建阈值数字实体: %s", self._attr_unique_id)

    def _setup_device_class_and_unit(self):
        """Set up device class and unit."""
        device_class, unit = get_number_device_class_and_unit(self._sensor_type)
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit

        # 确保温度相关实体使用摄氏度
        if self._sensor_type in ["temperature", "ambient_temperature"]:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            # 强制设置单位属性确保UI显示正确
            self._attr_unit_of_measurement = "°C"

        # 确保气压相关实体使用hPa
        if self._sensor_type in ["pressure"]:
            self._attr_device_class = NumberDeviceClass.PRESSURE
            # 强制设置单位属性确保UI显示正确
            self._attr_unit_of_measurement = "hPa"

    def _setup_number_range(self):
        """Set up number value range and step."""
        # Get range configuration based on sensor type and threshold type
        range_config = get_number_range_config(self._sensor_type, self._threshold_type)

        self._attr_native_min_value = range_config["min"]
        self._attr_native_max_value = range_config["max"]
        self._attr_native_step = range_config["step"]

        # 设置初始值
        self._attr_native_value = range_config["default"]

        # 设置图标 (使用 _attr_ 而不是 @property 以避免deprecation警告)
        self._attr_icon = get_threshold_icon(self._sensor_type, self._threshold_type)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value and sync to ESP32."""
        try:
            # Validate value range
            if (
                value < self._attr_native_min_value
                or value > self._attr_native_max_value
            ):
                _LOGGER.error(
                    "Invalid threshold value %f for %s (range: %f - %f)",
                    value,
                    self._attr_name,
                    self._attr_native_min_value,
                    self._attr_native_max_value,
                )
                return

            # Get current value for comparison
            old_value = self._attr_native_value

            _LOGGER.info(
                "Number entity value change: %s = %s (old value: %s)",
                self.entity_id,
                value,
                old_value,
            )

            # Update the value
            self._attr_native_value = value

            # 检查是否为Battery Sampling Interval实体
            if (
                "battery_sampling_interval" in self.entity_id.lower()
                or "battery sampling interval" in self._attr_name.lower()
            ):
                # 这是Battery Sampling Interval实体，直接发送采样间隔事件
                node_id = self._device_info.get("node_id", "").replace(":", "").lower()
                self.hass.bus.async_fire(
                    f"{DOMAIN}_set_sampling_interval",
                    {
                        "node_id": node_id,
                        "interval_sec": int(value),
                        "reason": "manual_adjustment",
                        "source": "battery_energy",
                    },
                )
            elif (
                "battery_low_threshold" in self.entity_id.lower()
                or "battery low threshold" in self._attr_name.lower()
            ):
                # 这是Battery Low Threshold实体，发送阈值事件
                node_id = self._device_info.get("node_id", "").replace(":", "").lower()
                self.hass.bus.async_fire(
                    f"{DOMAIN}_set_battery_low_threshold",
                    {"node_id": node_id, "low_threshold": int(value)},
                )
            else:
                # 普通传感器阈值，使用原有逻辑
                await self.async_send_to_esp32()

            # Update state
            self.async_write_ha_state()

        except Exception:
            _LOGGER.exception("Failed to set threshold number entity value: %s", self.entity_id)
            raise

    async def async_send_to_esp32(self) -> None:
        """Send current threshold to ESP32 device."""
        try:
            node_id = self._device_info["node_id"].lower()

            param_name = get_esp32_threshold_param_name(self._sensor_type, self._threshold_type)
            value = self._attr_native_value

            self.hass.bus.async_fire(
                f"{DOMAIN}_sync_single_threshold",
                {
                    "sensor_type": self._sensor_type,
                    "param_name": param_name,
                    "value": value,
                    "number_entity_id": self.entity_id,
                    "source": "number_entity",
                },
            )

            self.hass.bus.async_fire(
                f"{DOMAIN}_threshold_update_to_esp32",
                {
                    "node_id": node_id,
                    "param_name": param_name,
                    "value": value,
                    "sensor_type": self._sensor_type,
                    "threshold_type": self._threshold_type,
                    "entity_id": self.entity_id,
                    "source": "number_entity",
                },
            )

            # Update sync time
            self._last_esp32_sync = datetime.datetime.now()

        except Exception:
            _LOGGER.exception("Failed to send threshold to ESP32: %s", self.entity_id)
            raise

    async def async_set_esp32_value(self, value: float) -> None:
        """Set value from ESP32 without triggering callback to ESP32."""
        try:
            old_value = self._attr_native_value
            value_changed = old_value != value

            # Update value and sync time
            self._attr_native_value = value
            self._last_esp32_sync = datetime.datetime.now()

            # Always write state to update last_updated timestamp
            # This is important for reconnection scenarios where the value
            # hasn't changed but we still want to show the entity is in sync
            self.async_write_ha_state()

            if value_changed:
                _LOGGER.info(
                    "Threshold value changed for %s: %s -> %s",
                    self.entity_id, old_value, value
                )
            else:
                _LOGGER.debug(
                    "Threshold value synced (unchanged) for %s: %s",
                    self.entity_id, value
                )

        except Exception:
            _LOGGER.exception("Failed to set threshold value from ESP32: %s", self.entity_id)

    async def async_added_to_hass(self) -> None:
        """Handle entity added to Home Assistant."""
        await super().async_added_to_hass()

        # Initialize state with instance variables
        self._last_update = time.time()

        # Register ESP32 threshold data update listener
        @callback
        def handle_esp32_threshold_update(event):
            """Handle ESP32 threshold data update."""
            try:
                # Normalize node_id to lowercase for comparison
                event_node_id = str(event.data.get("node_id", "")).replace(":", "").strip().lower()
                device_node_id = str(self._device_info.get("node_id", "")).replace(":", "").strip().lower()

                # Filter: only process events for this device
                if not event_node_id or not device_node_id or event_node_id != device_node_id:
                    return

                event_param = event.data.get("param_name", "")
                expected_param = f"{self._sensor_type}_{self._threshold_type}_threshold"

                # Normalize ESP32's abbreviated param names to match entity's full sensor_type
                # ESP32 uses: temp_*, humidity_*, but entities may use: ambient_temperature_*, ambient_humidity_*
                normalized_event_param = event_param
                if event_param.startswith("temp_"):
                    # Check if this entity uses "ambient_temperature" or "temperature"
                    if self._sensor_type in ["ambient_temperature", "temperature"]:
                        normalized_event_param = event_param.replace("temp_", f"{self._sensor_type}_")
                elif event_param.startswith("humidity_"):
                    # Check if this entity uses "ambient_humidity"
                    if self._sensor_type == "ambient_humidity":
                        normalized_event_param = event_param.replace("humidity_", "ambient_humidity_")

                if normalized_event_param == expected_param:
                    new_value = float(event.data.get("value", self._attr_native_value))

                    # Update value asynchronously
                    self.hass.async_create_task(self.async_set_esp32_value(new_value))

                    _LOGGER.debug(
                        "Threshold entity %s received ESP32 update: %s = %s",
                        self._attr_unique_id, expected_param, new_value
                    )

            except Exception:
                _LOGGER.exception("Failed to handle ESP32 threshold update event for %s", self._attr_unique_id)

        # Listen for ESP32 threshold data update events
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_threshold_data_received", handle_esp32_threshold_update
            )
        )

        # Listen for device availability changes
        @callback
        def handle_device_availability_change(event):
            """Handle device availability change."""
            try:
                event_node_id = str(event.data.get("node_id", "")).replace(":", "").strip().lower()
                device_node_id = str(self._device_info.get("node_id", "")).replace(":", "").strip().lower()

                if event_node_id == device_node_id:
                    available = event.data.get("available", False)
                    _LOGGER.debug(
                        "Device %s availability changed to %s, updating threshold entity %s",
                        device_node_id,
                        "available" if available else "unavailable",
                        self._attr_unique_id,
                    )
                    self.async_write_ha_state()
            except Exception:
                _LOGGER.exception("Failed to handle device availability change for %s", self._attr_unique_id)

        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_device_availability_changed", handle_device_availability_change
            )
        )

        _LOGGER.debug("Threshold number entity added to HA: %s", self._attr_unique_id)

    @property
    def available(self) -> bool:
        """Return True if entity is available (device is connected)."""
        api = self._hass.data.get(DOMAIN, {}).get("shared_api")
        if api:
            node_id = str(self._device_info["node_id"]).lower()
            return api.is_device_available(node_id)
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {}
        if self._last_esp32_sync:
            # This ensures last_updated is refreshed after reconnection
            attrs["last_update"] = self._last_esp32_sync.timestamp()
        return attrs

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal from Home Assistant."""
        await super().async_will_remove_from_hass()
        _LOGGER.debug("Threshold number entity removed from HA: %s", self._attr_unique_id)

class ESPHomeBatteryConfigNumber(NumberEntity):
    """Battery configuration number entity (Battery Low Threshold and Battery Sampling Interval)."""

    def __init__(
        self,
        hass: HomeAssistant,
        node_id: str,
        device_name: str,
        device_info: dict,
        param_type: str,
        param_name: str,
    ) -> None:
        """Initialize battery configuration number entity."""
        NumberEntity.__init__(self)
        self._hass = hass
        self._node_id = str(node_id).replace(":", "").lower()
        self._device_name = device_name
        self._device_info = device_info
        self._param_type = param_type
        self._param_name = param_name

        # Entity attributes - 使用has_entity_name，让HA自动组合设备名
        self._attr_name = param_name
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{DOMAIN}_{self._node_id}_{param_type}"
        self._attr_device_info = get_device_info(self._node_id, device_name)

        # Set number properties based on parameter type
        if param_type == "battery_low_threshold":
            self._attr_native_min_value = 0
            self._attr_native_max_value = 100
            self._attr_native_step = 1
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_device_class = NumberDeviceClass.BATTERY
        else:  # battery_sampling_interval
            self._attr_native_min_value = 60  # 1 minute
            self._attr_native_max_value = 86400  # 24 hours
            self._attr_native_step = 60
            self._attr_native_unit_of_measurement = "seconds"
            self._attr_device_class = NumberDeviceClass.DURATION

        self._attr_mode = NumberMode.SLIDER
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return self._attr_device_info

    @property
    def available(self) -> bool:
        """Return True if entity is available (device is connected)."""
        api = self._hass.data.get(DOMAIN, {}).get("shared_api")
        if api:
            return api.is_device_available(self._node_id)
        return False

    async def async_set_native_value(self, value: float) -> None:
        """Set value and sync with ESP32."""
        try:
            _LOGGER.debug(
                "Battery config value changing: %s = %s", self.entity_id, value
            )

            # Update state directly
            old_value = self._attr_native_value
            self._attr_native_value = float(value)
            self._last_update = time.time()

            # Fire appropriate event through event bus
            if self._param_type == "battery_low_threshold":
                event_name = f"{DOMAIN}_battery_low_threshold_changed"
                event_data = {"node_id": self._node_id, "low_threshold": int(value)}
            else:  # battery_sampling_interval
                event_name = f"{DOMAIN}_battery_sampling_interval_changed"
                event_data = {
                    "node_id": self._node_id,
                    "interval_sec": int(value),
                    "reason": "manual_adjustment",
                    "source": "battery_energy",
                }

            self.hass.bus.async_fire(event_name, event_data)

            # Service call to update device through event bus
            self.hass.bus.async_fire(
                f"{DOMAIN}_update_battery_config",
                {
                    "node_id": self._node_id,
                    "param_type": self._param_type,
                    "value": value,
                },
            )

            _LOGGER.info(
                "Battery configuration updated: %s = %s (old: %s)",
                self.entity_id,
                value,
                old_value,
            )

        except Exception:
            _LOGGER.exception("Failed to set battery config value: %s", self.entity_id)
            raise

    async def async_added_to_hass(self) -> None:
        """Entity added to Home Assistant."""
        await super().async_added_to_hass()

        # Listen for device availability changes
        @callback
        def handle_device_availability_change(event):
            """Handle device availability change."""
            try:
                event_node_id = str(event.data.get("node_id", "")).replace(":", "").strip().lower()
                device_node_id = str(self._device_info.get("node_id", "")).replace(":", "").strip().lower()

                if event_node_id == device_node_id:
                    available = event.data.get("available", False)
                    _LOGGER.debug(
                        "Device %s availability changed to %s, updating battery config entity %s",
                        device_node_id,
                        "available" if available else "unavailable",
                        self._attr_unique_id,
                    )
                    self.async_write_ha_state()
            except Exception:
                _LOGGER.exception("Failed to handle device availability change for %s", self._attr_unique_id)

        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_device_availability_changed", handle_device_availability_change
            )
        )

        _LOGGER.debug("Battery config number entity added: %s", self._attr_unique_id)

    async def async_will_remove_from_hass(self) -> None:
        """Entity removed from Home Assistant."""
        await super().async_will_remove_from_hass()
        _LOGGER.debug("Battery config number entity removed: %s", self._attr_unique_id)
