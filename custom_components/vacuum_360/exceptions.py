"""Exception hierarchy for the 360 Robot Vacuum integration.

The coordinator and config flow translate these into the Home Assistant
exceptions they actually care about (ConfigEntryAuthFailed / UpdateFailed),
keeping protocol-specific detail out of the HA-facing layers.
"""


class Vacuum360Error(Exception):
    """Base class for all errors raised by the 360 cloud client."""


class Vacuum360AuthError(Vacuum360Error):
    """Authentication failed or the session (sid) has expired.

    Mapped to ConfigEntryNotReady / ConfigEntryAuthFailed by callers so HA
    surfaces a re-auth notification instead of silently retrying forever.
    """


class Vacuum360ApiError(Vacuum360Error):
    """The cloud returned a non-zero errno or an unexpected payload.

    Raised for any business-level failure that is not an auth problem
    (e.g. device offline, command rejected, malformed response).
    """


class Vacuum360ConnectionError(Vacuum360Error):
    """A transport-level failure: timeout, DNS, TCP, TLS or HTTP 5xx.

    Distinct from Vacuum360ApiError so callers can decide to retry with
    backoff rather than fail the whole entry.
    """
