"""Constants for the 360 Robot Vacuum integration."""
from __future__ import annotations

from homeassistant.components.vacuum import VacuumActivity

DOMAIN = "vacuum_360"

# --- API endpoints (verified working 2026-07) ---
API_BASE = "https://q.smart.360.cn"
API_USER_LOGIN = f"{API_BASE}/common/user/login"
API_DEVICES = f"{API_BASE}/common/dev/GetList"
API_CMD_SEND = f"{API_BASE}/clean/cmd/send"

# --- Protocol constants (from ioBroker.botslab360 reverse engineering) ---
# devType=3 is the device type code for 360 robot vacuums on the cloud.
DEV_TYPE = "3"
# App identifier used in form fields, mirrors the iOS super-app client.
FROM_IOS = "mpc_ios"
# Polling cadence for the coordinator.
SCAN_INTERVAL_SECONDS = 30
# Request timeout for every cloud call.
REQUEST_TIMEOUT_SECONDS = 15

# --- infoType command codes ---
# Each cloud command is identified by an integer "infoType". These were
# extracted from the official 360 app traffic and the ioBroker adapter.
INFO_STATUS = "20001"        # poll device status (battery / mode / errors)
INFO_START_CLEAN = "21005"   # start cleaning, carries mode in `data`
INFO_RETURN_HOME = "21012"   # return to dock / start charging
INFO_PAUSE_RESUME = "21017"  # pause or resume, distinguished by `data.cmd`
INFO_FAN_MODE = "21022"      # switch fan power mode
INFO_CONSUMABLE = "21015"    # query consumable wear (filters / brushes)
INFO_MAP = "30000"           # fetch map data (batch, not used in MVP)

# --- `data` payloads per command ---
DATA_START_SMART = '{"mode":"smartClean","globalCleanTimes":1}'
DATA_RETURN_HOME = '{"cmd":"start"}'
DATA_PAUSE = '{"cmd":"pause"}'
DATA_RESUME = '{"cmd":"continue"}'

# --- Config field keys ---
CONF_QID = "qid"
CONF_SID = "sid"

# --- Default region / language sent to the cloud ---
DEFAULT_COUNTRY_ID = "DE"
DEFAULT_LANG = "de_DE"

# Minimal iOS user-agent strings observed in the wild. The cloud keys some
# rate-limiting / fingerprinting off these, so keep them verbatim.
UA_DEVICE_LIST = "QihooSuperApp_NoPods/11.1.0 (iPhone; iOS 14.8; Scale/3.00)"
UA_USER_LOGIN = "qhsa-iphone-11.1.0"

# --- 360 cloud mode -> HA VacuumActivity ---
# Built directly to VacuumActivity to avoid the error-prone double string
# mapping that the previous (broken) integration used. Unknown modes fall
# through to None so protocol drift is visible instead of masked as IDLE.
MODE_TO_ACTIVITY: dict[str, VacuumActivity] = {
    # charging / docked
    "charge": VacuumActivity.DOCKED,
    "charging": VacuumActivity.DOCKED,
    "charged": VacuumActivity.DOCKED,
    "fullcharge": VacuumActivity.DOCKED,
    # cleaning
    "smartclean": VacuumActivity.CLEANING,
    "aroundclean": VacuumActivity.CLEANING,
    "spotclean": VacuumActivity.CLEANING,
    "totalclean": VacuumActivity.CLEANING,
    "cleaning": VacuumActivity.CLEANING,
    "sweeping": VacuumActivity.CLEANING,
    # paused
    "pause": VacuumActivity.PAUSED,
    "paused": VacuumActivity.PAUSED,
    # idle
    "idle": VacuumActivity.IDLE,
    "standby": VacuumActivity.IDLE,
    "sleep": VacuumActivity.IDLE,
    # returning to dock
    "chargeback": VacuumActivity.RETURNING,
    "return": VacuumActivity.RETURNING,
    "gocharge": VacuumActivity.RETURNING,
    "returning": VacuumActivity.RETURNING,
    # error
    "error": VacuumActivity.ERROR,
}

# 360 fan mode names reported by the cloud -> display names exposed to HA.
# The numeric/string identifiers come from INFO_FAN_MODE traffic.
FAN_MODE_TO_NAME: dict[str, str] = {
    "auto": "Auto",
    "quiet": "Quiet",
    "strong": "Strong",
}
