"""Config flow for the 360 Robot Vacuum integration.

The user provides the qid (account id) and sid (session id) extracted from
the official 360 app via packet capture. We validate the format locally and
then probe the cloud with a GetList call to confirm the session works before
creating the entry.
"""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Robot360CloudAPI
from .const import CONF_QID, CONF_SID, DOMAIN
from .exceptions import Vacuum360AuthError, Vacuum360Error

# qid: numeric account id (the cloud uses ~10 digits, but we accept 6-20
# to stay forward-compatible). sid: 32-char hex session token.
_QID_RE = re.compile(r"^\d{6,20}$")
_SID_RE = re.compile(r"^[0-9a-fA-F]{32}$")

_STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_QID): str,
        vol.Required(CONF_SID): str,
    }
)


def _validate_format(qid: str, sid: str) -> str | None:
    """Return an error key if the inputs look malformed, else None."""
    if not _QID_RE.match(qid.strip()):
        return "invalid_qid"
    if not _SID_RE.match(sid.strip()):
        return "invalid_sid"
    return None


class Robot360ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for 360 Robot Vacuum."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """First step: collect qid + sid and validate against the cloud."""
        errors: dict[str, str] = {}

        if user_input is not None:
            qid = user_input[CONF_QID].strip()
            sid = user_input[CONF_SID].strip()

            fmt_error = _validate_format(qid, sid)
            if fmt_error:
                errors["base"] = fmt_error
            else:
                session = async_get_clientsession(self.hass)
                api = Robot360CloudAPI(session, qid, sid)
                try:
                    devices = await api.async_get_devices()
                except Vacuum360AuthError:
                    errors["base"] = "invalid_auth"
                except Vacuum360Error:
                    errors["base"] = "cannot_connect"
                else:
                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        await self.async_set_unique_id(qid)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"360 Vacuum ({len(devices)})",
                            data={CONF_QID: qid, CONF_SID: sid},
                        )

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "sn_example": "360CNXXXXXXXXXXXX",
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Trigger re-auth when the sid expires."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Ask only for a fresh sid; qid stays the same."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            sid = user_input[CONF_SID].strip()
            if not _SID_RE.match(sid):
                errors["base"] = "invalid_sid"
            else:
                qid = entry.data[CONF_QID]
                session = async_get_clientsession(self.hass)
                api = Robot360CloudAPI(session, qid, sid)
                try:
                    await api.async_get_devices()
                # Keep auth vs connection distinct - re-entering a wrong sid
                # should not be reported as a network problem.
                except Vacuum360AuthError:
                    errors["base"] = "invalid_auth"
                except Vacuum360Error:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_SID: sid},
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_SID): str}),
            description_placeholders={"qid": entry.data[CONF_QID]},
            errors=errors,
        )
