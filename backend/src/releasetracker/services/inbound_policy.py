"""Runtime inbound HTTP policy resolved from the saved administrator settings.

Trusted proxies and CORS origins are stored once and applied both to the ASGI
boundary (forwarded headers, CORS middleware) and to browser origin checks, so a
saved change takes effect without a restart and always has one source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from .instance_policy import POLICY_DEFAULTS, normalize_policy

if TYPE_CHECKING:
    from ..storage.sqlite import SQLiteStorage

CORS_ORIGINS_SETTING = "system.cors_origins"
FORWARDED_ALLOW_IPS_SETTING = "system.forwarded_allow_ips"
POLICY_SETTING_KEYS = (CORS_ORIGINS_SETTING, FORWARDED_ALLOW_IPS_SETTING)


@dataclass(frozen=True)
class InboundPolicy:
    cors_origins: tuple[str, ...] = ()
    trusted_proxies: tuple[str, ...] = ()


def _lines(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())


DEFAULT_POLICY = InboundPolicy(
    cors_origins=(),
    trusted_proxies=_lines(POLICY_DEFAULTS[FORWARDED_ALLOW_IPS_SETTING]),
)


def build_policy(values: Mapping[str, str | None]) -> InboundPolicy:
    """Normalize saved values and fall back to the default for unreadable entries."""
    normalized: dict[str, str] = {}
    for key in POLICY_SETTING_KEYS:
        default = POLICY_DEFAULTS[key]
        try:
            raw = default if values.get(key) is None else str(values[key])
            normalized[key] = normalize_policy(key, raw)
        except ValueError:
            # A denied origin stays closed and loopback proxy trust is the safe default.
            normalized[key] = default
    return InboundPolicy(
        cors_origins=_lines(normalized[CORS_ORIGINS_SETTING]),
        trusted_proxies=_lines(normalized[FORWARDED_ALLOW_IPS_SETTING]),
    )


async def load_policy(storage: "SQLiteStorage") -> InboundPolicy:
    """Read the saved inbound policy; this is also the post-save refresh path."""
    return build_policy({key: await storage.get_setting(key) for key in POLICY_SETTING_KEYS})


def inbound_policy(app: Any) -> InboundPolicy:
    """Return the policy bound to an application, or the fresh-install defaults."""
    policy = getattr(getattr(app, "state", None), "inbound_policy", None)
    return policy if isinstance(policy, InboundPolicy) else DEFAULT_POLICY
