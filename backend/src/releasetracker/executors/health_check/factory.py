"""Strategy → probe dispatcher.

Phase C maps ``runtime_native`` and ``helm_status`` to real probes and
raises ``NotImplementedError`` for ``http`` / ``tcp``. The pydantic
validator already rejects those strategies until Phase D flips the
``_PHASE_D_ENABLED`` flag in ``config``, so in practice the factory only
sees the supported strategies at run time. We still guard defensively
so future phases can wire HTTP / TCP without touching the scheduler.
"""

from __future__ import annotations

from .helm_status_probe import HelmStatusProbe
from .http_probe import HTTPProbe
from .probe import HealthCheckProbe, RuntimeNativeProbe
from .tcp_probe import TCPProbe


class ProbeFactory:
    def build(self, strategy: str, target_mode: str) -> HealthCheckProbe:
        if strategy == "runtime_native":
            return RuntimeNativeProbe()
        if strategy == "helm_status":
            return HelmStatusProbe()
        if strategy == "http":
            return HTTPProbe()
        if strategy == "tcp":
            return TCPProbe()
        raise ValueError(f"unknown health_check strategy: {strategy!r}")


__all__ = ["ProbeFactory"]
