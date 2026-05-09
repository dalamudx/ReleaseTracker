"""Host resolution for HTTP / TCP probes.

Each adapter owns the specifics of how a target is reachable from the
adapter's network context. The resolver below is a thin dispatcher that
asks the adapter to return a list of ``ProbeHost`` entries (one per
evaluated service) and passes them to HTTP / TCP probes. Adapters that
do not yet support a given runtime mode raise ``NotImplementedError``;
the probe surface translates that into ``host_unresolvable`` outcomes
so the runner retries rather than aborting the phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..base import BaseRuntimeAdapter


@dataclass(frozen=True)
class ProbeHost:
    """One concrete ``(service, host, port)`` probe target.

    ``service`` is ``None`` for single-target container executors and
    the service name for grouped modes so per-service diagnostics can
    attribute results correctly.
    """

    service: str | None
    host: str
    port: int | None = None


async def resolve_probe_hosts(
    adapter: "BaseRuntimeAdapter",
    target_ref: dict[str, Any],
    *,
    services: list[str] | None,
    default_port: int | None,
) -> list[ProbeHost]:
    """Ask the adapter for ``ProbeHost`` entries.

    Adapters implement ``resolve_probe_hosts`` returning a list with at
    least one entry; any resolution failure raises a ``ValueError`` the
    caller turns into ``host_unresolvable``.
    """
    resolver = getattr(adapter, "resolve_probe_hosts", None)
    if not callable(resolver):
        raise ValueError(
            f"{adapter.__class__.__name__} does not implement resolve_probe_hosts"
        )
    result = await resolver(
        target_ref,
        services=services,
        default_port=default_port,
    )
    if not isinstance(result, list) or not result:
        raise ValueError("resolve_probe_hosts returned an empty list")
    return result


__all__ = ["ProbeHost", "resolve_probe_hosts"]
