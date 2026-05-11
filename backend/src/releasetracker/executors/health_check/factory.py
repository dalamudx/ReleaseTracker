"""Strategy → probe dispatcher."""

from __future__ import annotations

from .auto_probe import AutoProbe
from .helm_status_probe import HelmStatusProbe
from .http_probe import HTTPProbe
from .probe import HealthCheckProbe, RuntimeNativeProbe
from .tcp_probe import TCPProbe


class ProbeFactory:
    def build(self, strategy: str, target_mode: str) -> HealthCheckProbe:
        if strategy == "auto":
            return AutoProbe()
        if strategy == "runtime_native":
            return RuntimeNativeProbe()
        if strategy == "helm_status":
            return HelmStatusProbe()
        if strategy == "manual_http":
            return HTTPProbe(manual=True)
        if strategy == "manual_tcp":
            return TCPProbe(manual=True)
        if strategy == "http":
            return HTTPProbe()
        if strategy == "tcp":
            return TCPProbe()
        raise ValueError(f"unknown health_check strategy: {strategy!r}")


__all__ = ["ProbeFactory"]
