"""Vacuum platform for the 360 Robot Vacuum integration."""
from __future__ import annotations

import logging

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FAN_MODE_TO_NAME, MODE_TO_ACTIVITY
from .coordinator import Robot360Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create vacuum entities for every coordinator registered for this entry."""
    coordinators: dict[str, Robot360Coordinator] = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [Robot360Vacuum(coordinator) for coordinator in coordinators.values()]
    )


class Robot360Vacuum(CoordinatorEntity[Robot360Coordinator], StateVacuumEntity):
    """Representation of a single 360 robot vacuum."""

    _attr_has_entity_name = True
    _attr_name = None
    # Only features the cloud actually supports. We deliberately omit STOP
    # (the cloud has no real stop, only pause + return) and TURN_ON/OFF.
    # Battery is exposed via a dedicated sensor (see sensor.py) because the
    # vacuum entity's battery_level property is deprecated (2026.8).
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.FAN_SPEED
    )

    def __init__(self, coordinator: Robot360Coordinator) -> None:
        """Wire the coordinator and set static entity attributes."""
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.sn
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.sn)},
            name=coordinator.device_name,
            manufacturer="Qihoo 360",
            model=coordinator.device_meta.get("hardware")
            or coordinator.device_meta.get("model")
            or "360 Robot Vacuum",
        )

    # ------------------------------------------------------------------ #
    # State properties
    # ------------------------------------------------------------------ #
    @property
    def activity(self) -> VacuumActivity | None:
        """Return the current activity, mapped from the cloud mode.

        Returns None for unknown modes so protocol drift is visible rather
        than silently masked as IDLE (the previous integration's bug).
        """
        data = self.coordinator.data or {}
        mode = (
            data.get("mode")
            or data.get("workMode")
            or data.get("runStatus")
            or data.get("cleanMode")
            or data.get("status")
        )
        if mode is None:
            return None
        # Normalise: cloud may return int, str, or mixed case.
        key = str(mode).strip().lower()
        return MODE_TO_ACTIVITY.get(key)

    @property
    def fan_speed(self) -> str | None:
        """Return the current fan speed display name."""
        data = self.coordinator.data or {}
        mode = data.get("fanMode") or data.get("waterLevel") or data.get("fan")
        if mode is None:
            return None
        return FAN_MODE_TO_NAME.get(str(mode).strip().lower())

    @property
    def fan_speed_list(self) -> list[str]:
        """Return the list of selectable fan speeds."""
        return list(FAN_MODE_TO_NAME.values())

    # ------------------------------------------------------------------ #
    # Service handlers
    # ------------------------------------------------------------------ #
    async def async_start(self) -> None:
        """Start cleaning, or resume if the robot is currently paused.

        The previous integration always called start, which restarts a full
        clean even when the user just wanted to resume.
        """
        if self.activity == VacuumActivity.PAUSED:
            await self.coordinator.api.async_resume(self.coordinator.sn)
        else:
            await self.coordinator.api.async_start(self.coordinator.sn)
        await self.coordinator.async_request_refresh()

    async def async_pause(self) -> None:
        """Pause the current clean."""
        await self.coordinator.api.async_pause(self.coordinator.sn)
        await self.coordinator.async_request_refresh()

    async def async_return_to_base(self, **kwargs) -> None:
        """Send the robot back to its dock."""
        await self.coordinator.api.async_return_to_base(self.coordinator.sn)
        await self.coordinator.async_request_refresh()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs) -> None:
        """Set the fan speed by display name."""
        # Reverse-lookup the cloud mode token from the display name.
        for mode_token, name in FAN_MODE_TO_NAME.items():
            if name == fan_speed:
                await self.coordinator.api.async_set_fan_mode(
                    self.coordinator.sn, mode_token
                )
                await self.coordinator.async_request_refresh()
                return
        _LOGGER.warning("Unknown fan speed %r for %s", fan_speed, self.coordinator.sn)
