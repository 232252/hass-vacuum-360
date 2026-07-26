"""The 360 Robot Vacuum integration.

This is the entry point Home Assistant loads. It builds the cloud API
client from the user-supplied qid/sid, creates one coordinator per vacuum
discovered on the account, and forwards the vacuum platform.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    Robot360CloudAPI,
    extract_device_name,
    extract_device_sn,
)
from .const import DOMAIN
from .coordinator import Robot360Coordinator
from .exceptions import Vacuum360AuthError, Vacuum360Error

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["vacuum", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a 360 Robot Vacuum config entry.

    Unlike the previous (broken) integration, we let first-refresh errors
    propagate: auth failures trigger re-auth, transient failures trigger
    retry via ConfigEntryNotReady. Nothing is swallowed.
    """
    session = async_get_clientsession(hass)
    api = Robot360CloudAPI(
        session,
        qid=entry.data["qid"],
        sid=entry.data["sid"],
    )

    # 1. Fetch the device list. This validates the sid before we commit.
    try:
        devices = await api.async_get_devices()
    except Vacuum360AuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except Vacuum360Error as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    if not devices:
        raise ConfigEntryNotReady("No devices found on this 360 account")

    _LOGGER.info("Found %d 360 device(s) on account qid=%s", len(devices), api.qid)

    # 2. Build one coordinator per device with a real status poll.
    coordinators: dict[str, Robot360Coordinator] = {}
    for dev in devices:
        sn = extract_device_sn(dev)
        if not sn:
            _LOGGER.warning("Skipping device without serial number: %s", dev)
            continue
        name = extract_device_name(dev, sn)
        coordinator = Robot360Coordinator(hass, api, sn, name, dev)
        # Let first-refresh failures abort setup for this device cleanly.
        await coordinator.async_config_entry_first_refresh()
        coordinators[sn] = coordinator

    if not coordinators:
        raise ConfigEntryNotReady("No devices with a valid serial number")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinators
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and tear down its coordinators."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
