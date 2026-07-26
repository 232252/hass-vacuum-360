"""360 cloud API client.

This client speaks the 360 robot vacuum cloud protocol used by the
"360 Smart Home" mobile app (q.smart.360.cn). It deliberately avoids the
passport.360.cn login flow (and its slide captcha) by accepting a session
id (`sid`) plus the account id (`qid`) that the user extracts once from the
app via packet capture. The session is forwarded verbatim as cookies, which
is exactly what the official app does after login.

The protocol shape (endpoints, infoType codes, cookie layout) was confirmed
against the ioBroker.botslab360 adapter and live-tested against the cloud
in 2026-07.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import aiohttp

from .const import (
    API_CMD_SEND,
    API_DEVICES,
    DATA_PAUSE,
    DATA_RESUME,
    DATA_RETURN_HOME,
    DATA_START_SMART,
    DEFAULT_COUNTRY_ID,
    DEFAULT_LANG,
    DEV_TYPE,
    FROM_IOS,
    INFO_FAN_MODE,
    INFO_PAUSE_RESUME,
    INFO_RETURN_HOME,
    INFO_START_CLEAN,
    INFO_STATUS,
    REQUEST_TIMEOUT_SECONDS,
    UA_DEVICE_LIST,
)
from .exceptions import (
    Vacuum360ApiError,
    Vacuum360AuthError,
    Vacuum360ConnectionError,
    Vacuum360Error,
)

_LOGGER = logging.getLogger(__name__)

# errno values returned by the cloud that indicate the sid/qid is no longer
# valid. Anything in this set is raised as Vacuum360AuthError so the entry
# moves into re-auth rather than endlessly retrying.
_AUTH_ERRNOS = {102, 401, 403, 10001, 10002}


class Robot360CloudAPI:
    """Thin async wrapper around the 360 cloud HTTP API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        qid: str,
        sid: str,
    ) -> None:
        """Store the shared HA aiohttp session and the user credentials.

        The session id (`sid`) is sensitive; it is only ever placed in the
        Cookie header and is never written to the log.
        """
        self._session = session
        self.qid = qid.strip()
        self.sid = sid.strip()
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _cookie(self) -> str:
        """Build the Cookie header the cloud expects for every request.

        `q` and `t` are app-level cookies that the official client sets to
        short fixed strings; they are not auth tokens and the server does
        not validate their contents when a valid sid is present. Only qid
        and sid carry identity.
        """
        return f"q=u=&t=1;t=&v=2.0&a=1;qid={self.qid};sid={self.sid}"

    def _headers(self, user_agent: str = UA_DEVICE_LIST) -> dict[str, str]:
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Cookie": self._cookie(),
            "User-Agent": user_agent,
            "Accept-Language": "de-DE;q=1, en-DE;q=0.8",
        }

    def _form(self, extra: dict[str, Any]) -> dict[str, Any]:
        """Common form fields appended to every device-level request."""
        return {
            "countryId": DEFAULT_COUNTRY_ID,
            "devType": DEV_TYPE,
            "from": FROM_IOS,
            "lang": DEFAULT_LANG,
            "taskid": str(uuid.uuid4()),
            **extra,
        }

    async def _post(
        self,
        url: str,
        form: dict[str, Any],
        user_agent: str = UA_DEVICE_LIST,
    ) -> dict[str, Any]:
        """POST form data and return the parsed JSON envelope.

        Translates every transport / parsing failure into a
        Vacuum360*Error so callers only need to handle our own hierarchy.
        """
        try:
            async with self._session.post(
                url,
                headers=self._headers(user_agent),
                data=self._form(form),
                timeout=self._timeout,
            ) as resp:
                # 5xx / unexpected status -> treat as transient connection error.
                if resp.status >= 500:
                    raise Vacuum360ConnectionError(
                        f"cloud returned HTTP {resp.status}"
                    )
                if resp.status >= 400:
                    # 4xx on this API almost always means the session is bad.
                    raise Vacuum360AuthError(
                        f"cloud returned HTTP {resp.status}"
                    )
                try:
                    result = await resp.json(content_type=None)
                except aiohttp.ContentTypeError as exc:
                    text = await resp.text()
                    raise Vacuum360ApiError(
                        f"non-JSON response ({len(text)} bytes)"
                    ) from exc
        except asyncio.TimeoutError as exc:
            raise Vacuum360ConnectionError("request timed out") from exc
        except aiohttp.ClientError as exc:
            raise Vacuum360ConnectionError(f"network error: {exc}") from exc

        if not isinstance(result, dict):
            raise Vacuum360ApiError(f"unexpected payload type: {type(result)!r}")

        self._check_errno(result, url)
        return result

    def _check_errno(self, result: dict[str, Any], url: str) -> None:
        """Inspect the cloud errno field and raise the right error type."""
        # errno arrives as either int or str depending on the endpoint.
        try:
            errno = int(result.get("errno", -1))
        except (TypeError, ValueError):
            errno = -1

        if errno == 0:
            return

        errmsg = result.get("errmsg", "unknown error")
        _LOGGER.debug(
            "360 cloud %s returned errno=%s errmsg=%s", url, errno, errmsg
        )
        if errno in _AUTH_ERRNOS:
            raise Vacuum360AuthError(f"errno={errno} ({errmsg})")
        raise Vacuum360ApiError(f"errno={errno} ({errmsg})")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return the list of vacuums bound to this account.

        The cloud wraps the list under data.list (older firmware) or
        data directly (newer). We normalise both to a flat list.
        """
        result = await self._post(API_DEVICES, {"devType": DEV_TYPE})
        data = result.get("data") or {}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("list", "devList", "devices", "cleanDevList"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    async def _send_cmd(
        self,
        sn: str,
        info_type: str,
        data: str | None = None,
    ) -> dict[str, Any]:
        """Send a command identified by its infoType code."""
        form: dict[str, Any] = {
            "sn": sn,
            "infoType": info_type,
            "data": data if data is not None else "",
        }
        result = await self._post(API_CMD_SEND, form)
        return result.get("data") or {}

    async def async_get_status(self, sn: str) -> dict[str, Any]:
        """Poll a single device's status (battery, mode, errors)."""
        return await self._send_cmd(sn, INFO_STATUS)

    async def async_start(self, sn: str) -> None:
        """Start a smart clean."""
        await self._send_cmd(sn, INFO_START_CLEAN, DATA_START_SMART)

    async def async_pause(self, sn: str) -> None:
        """Pause the current clean."""
        await self._send_cmd(sn, INFO_PAUSE_RESUME, DATA_PAUSE)

    async def async_resume(self, sn: str) -> None:
        """Resume after a pause."""
        await self._send_cmd(sn, INFO_PAUSE_RESUME, DATA_RESUME)

    async def async_return_to_base(self, sn: str) -> None:
        """Send the robot back to its dock."""
        await self._send_cmd(sn, INFO_RETURN_HOME, DATA_RETURN_HOME)

    async def async_set_fan_mode(self, sn: str, mode: str) -> None:
        """Switch the fan power mode (auto / quiet / strong)."""
        await self._send_cmd(sn, INFO_FAN_MODE, json.dumps({"mode": mode}))


def extract_device_sn(dev: dict[str, Any]) -> str | None:
    """Pull the serial number out of a device dict across firmware variants."""
    for key in ("sn", "devSn", "deviceSn", "cleanSn"):
        value = dev.get(key)
        if value:
            return str(value)
    return None


def extract_device_name(dev: dict[str, Any], sn: str) -> str:
    """Best-effort device display name, falling back to the SN tail."""
    for key in ("title", "name", "devName", "deviceName", "cleanName"):
        value = dev.get(key)
        if value:
            return str(value)
    return f"360 Robot {sn[-4:]}"


def extract_device_model(dev: dict[str, Any]) -> str:
    """Extract the hardware/model string if the cloud exposes one."""
    for key in ("hardware", "model", "modelName", "productId"):
        value = dev.get(key)
        if value:
            return str(value)
    return "360 Robot Vacuum"
