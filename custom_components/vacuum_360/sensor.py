"""Sensor platform for the 360 Robot Vacuum.

Exposes the battery level as a dedicated battery sensor, which is the
forward-compatible approach in HA (the vacuum entity's battery_level
property is deprecated and breaks_in_ha_version=2026.8).
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Robot360Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one battery sensor per registered vacuum."""
    coordinators: dict[str, Robot360Coordinator] = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [Robot360BatterySensor(coordinator) for coordinator in coordinators.values()]
    )


class Robot360BatterySensor(
    CoordinatorEntity[Robot360Coordinator], SensorEntity
):
    """Battery percentage for a 360 vacuum."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_has_entity_name = True

    def __init__(self, coordinator: Robot360Coordinator) -> None:
        """Attach to the coordinator and set static attributes."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.sn}_battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.sn)},
        )

    @property
    def native_value(self) -> StateType:
        """Return the battery level clamped to [0, 100]."""
        data = self.coordinator.data or {}
        for key in ("elec", "battery", "batteryLevel", "power"):
            value = data.get(key)
            if value is None:
                continue
            try:
                level = int(round(float(value)))
            except (TypeError, ValueError):
                continue
            return max(0, min(100, level))  # type: ignore[return-value]
        return None
