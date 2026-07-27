"""DataUpdateCoordinator for 360 robot vacuums.

One coordinator is created per device. It polls the cloud every
SCAN_INTERVAL_SECONDS and stores the most recent status dict so the vacuum
entity can read activity / battery / fan mode from it.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import Robot360CloudAPI
from .const import DOMAIN, SCAN_INTERVAL_SECONDS
from .exceptions import Vacuum360AuthError, Vacuum360Error

_LOGGER = logging.getLogger(__name__)


def _merge_status(device_meta: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    """Merge the GetList metadata with the per-device status payload.

    GetList gives us sn/title/hardware; /clean/cmd/send with infoType 20001
    gives us the live mode/battery/error. We flatten both into one dict so
    the entity has a single source of truth.
    """
    merged = dict(device_meta)
    # Status may be nested under "data" depending on firmware; flatten it.
    for container in (status, status.get("data") if isinstance(status, dict) else None):
        if isinstance(container, dict):
            merged.update(container)
    return merged


class Robot360Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls the status of a single 360 vacuum."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: Robot360CloudAPI,
        sn: str,
        name: str,
        device_meta: dict[str, Any],
    ) -> None:
        """Store identity and seed the base coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{sn}",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.api = api
        self.sn = sn
        self.device_name = name
        self.device_meta = device_meta

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest status for this device.

        Auth errors propagate as ConfigEntryAuthFailed so HA triggers the
        re-auth flow; everything else becomes UpdateFailed so HA retries
        with backoff and marks the entity unavailable.
        """
        try:
            status = await self.api.async_get_status(self.sn)
        except Vacuum360AuthError as exc:
            # The sid has expired - ask the user to re-enter it.
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except Vacuum360Error as exc:
            raise UpdateFailed(str(exc)) from exc
        except Exception as exc:
            # Any unexpected error (e.g. RuntimeError from a torn-down session
            # that slipped through) should become UpdateFailed so HA retries
            # on the next poll instead of crashing the coordinator.
            raise UpdateFailed(f"unexpected error: {exc}") from exc

        return _merge_status(self.device_meta, status)
