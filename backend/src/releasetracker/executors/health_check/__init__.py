"""Post-update health check subsystem.

This subpackage hosts the Health Check Phase runtime: the lifecycle runner,
the strategy probe abstraction, and the concrete probe implementations.

Phase C ships:
- ``types`` — dataclasses shared across the subsystem.
- ``runtime_native_probe`` — the zero-config probe backed by adapter-native
  readiness queries.
- ``helm_status_probe`` — the Helm release status strategy.
- ``runner`` — the Health Check Phase lifecycle loop.
- ``factory`` — strategy → probe dispatcher.

Phase D will add ``http_probe``, ``tcp_probe``, ``host_resolver``, and
``recovery_hook``.
"""

from .types import (
    HealthCheckContext,
    HealthCheckResult,
    ProbeAttemptResult,
    ProbeErrorCategory,
    ServiceHealthResult,
    redact_for_log,
    truncate_error,
)

__all__ = [
    "HealthCheckContext",
    "HealthCheckResult",
    "ProbeAttemptResult",
    "ProbeErrorCategory",
    "ServiceHealthResult",
    "redact_for_log",
    "truncate_error",
]
