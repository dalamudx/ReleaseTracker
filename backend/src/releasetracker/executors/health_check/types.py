"""Shared dataclasses for the Health Check Phase runtime.

Kept tiny and import-cheap so the scheduler, runner, and individual probes
can all consume the same vocabulary without pulling in FastAPI, httpx, or
other heavyweight deps.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

# Maximum length of a persisted ``last_error`` string before truncation so
# diagnostics rows stay well under the SQLite TEXT column budget (Req 8.4).
MAX_LAST_ERROR_LENGTH = 500


# Broad classification of per-attempt probe failure causes. The taxonomy is
# deliberately flat so Phase E log/metric queries can bucket outcomes without
# parsing free-form strings. Additional categories land in Phase D alongside
# HTTP / TCP probes; Phase C keeps the essentials.
ProbeErrorCategory = Literal[
    "ok",
    "timeout",
    "connection_refused",
    "dns_failure",
    "tls_error",
    "network_unreachable",
    "host_unresolvable",
    "status_mismatch",
    "body_mismatch",
    "runtime_api_error",
    "helm_failed",
    "helm_pending",
    "helm_unknown_status",
    "cancelled",
    "other",
]


HealthOutcome = Literal["healthy", "unhealthy", "skipped", "error"]


@dataclass(frozen=True)
class HealthCheckContext:
    """Run-scoped context handed to the runner and each probe attempt.

    ``baseline`` is a free-form dict filled by
    ``ExecutorScheduler._capture_update_phase_baseline`` — it carries
    adapter-specific state the runtime-native probe needs, for example
    Kubernetes ``metadata.generation`` or a container restart count at the
    end of the Update Phase (Req 3.2, Req 3.5).
    """

    executor_config: Any  # ExecutorConfig; typed as Any to avoid import cycle
    adapter: Any  # BaseRuntimeAdapter; typed as Any to avoid import cycle
    run_id: int
    update_phase_end_at: datetime
    baseline: dict[str, Any]


@dataclass(frozen=True)
class ProbeAttemptResult:
    """Outcome of a single probe attempt.

    ``per_service`` is populated only for grouped target modes where the
    probe evaluates every service independently (Req 3.3, 4.6, 5.4). The
    aggregate attempt is healthy only when every per-service entry is.
    """

    healthy: bool
    error_category: ProbeErrorCategory = "ok"
    detail: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None
    per_service: dict[str, "ProbeAttemptResult"] | None = None
    # Causes the runner to short-circuit the probe window without further
    # retries (Req 6.4 — Helm ``failed`` status).
    terminate_phase: bool = False


@dataclass(frozen=True)
class ServiceHealthResult:
    """Per-service summary persisted inside ``diagnostics.health_check.services``."""

    service: str
    outcome: HealthOutcome
    attempt_count: int
    last_error: str | None = None


@dataclass
class HealthCheckResult:
    """Aggregate outcome of a Health Check Phase, ready for persistence.

    The exact shape is defined by Req 8.2 (top-level diagnostics fields) and
    Req 8.3 (per-service entries for grouped modes). ``to_dict`` emits the
    JSON form the scheduler merges into ``executor_run_history.diagnostics``.
    """

    strategy: str
    outcome: HealthOutcome
    attempt_count: int = 0
    first_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    duration_seconds: int = 0
    failure_policy: str = "mark_failed"
    last_error: str | None = None
    services: list[ServiceHealthResult] | None = None
    probe_diagnostics: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        truncated_now = False
        last_error = self.last_error
        if last_error is not None and len(last_error) > MAX_LAST_ERROR_LENGTH:
            last_error = last_error[:MAX_LAST_ERROR_LENGTH]
            truncated_now = True

        services_payload: list[dict[str, Any]] | None = None
        if self.services is not None:
            services_payload = []
            for entry in self.services:
                svc_last = entry.last_error
                if svc_last is not None and len(svc_last) > MAX_LAST_ERROR_LENGTH:
                    svc_last = svc_last[:MAX_LAST_ERROR_LENGTH]
                    truncated_now = True
                services_payload.append(
                    {
                        "service": entry.service,
                        "outcome": entry.outcome,
                        "attempt_count": entry.attempt_count,
                        "last_error": svc_last,
                    }
                )

        payload: dict[str, Any] = {
            "strategy": self.strategy,
            "outcome": self.outcome,
            "attempt_count": self.attempt_count,
            "first_attempt_at": (
                self.first_attempt_at.isoformat() if self.first_attempt_at else None
            ),
            "last_attempt_at": (
                self.last_attempt_at.isoformat() if self.last_attempt_at else None
            ),
            "duration_seconds": self.duration_seconds,
            "failure_policy": self.failure_policy,
            "last_error": last_error,
        }
        if services_payload is not None:
            payload["services"] = services_payload
        if self.probe_diagnostics:
            payload["probe_diagnostics"] = dict(self.probe_diagnostics)
        if truncated_now or self.truncated:
            payload["truncated"] = True
        return payload


def truncate_error(value: str | None) -> str | None:
    """Return ``value`` truncated to ``MAX_LAST_ERROR_LENGTH`` characters."""
    if value is None:
        return None
    if len(value) <= MAX_LAST_ERROR_LENGTH:
        return value
    return value[:MAX_LAST_ERROR_LENGTH]


# Case-insensitive names of header keys and config keys whose values we MUST
# strip before they ever reach a log record. Kept conservative; Phase E
# adds more specific patterns when snapshot redaction lands.
_SECRET_KEY_NAMES = (
    "authorization",
    "auth",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "token",
    "access-token",
    "bearer",
    "password",
    "secret",
)

# Header values that still fall through should get their token-looking parts
# masked. Anything looking like ``Bearer <opaque>`` or a JWT triple stays
# behind a fixed marker (Req 13.5).
_TOKEN_PATTERNS = (
    re.compile(r"(Bearer|Basic|Token)\s+\S+", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
)

REDACTED_MARKER = "***REDACTED***"


def redact_for_log(payload: Any) -> Any:
    """Recursively redact sensitive values in a JSON-safe payload.

    Rules:
    - Any dict key matching a known secret name has its value replaced.
    - Any string value matching a token-looking pattern is masked.
    - Lists / dicts are walked structurally.
    - Non-container values other than str pass through unchanged.
    """
    if isinstance(payload, dict):
        redacted: dict[Any, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in _SECRET_KEY_NAMES:
                redacted[key] = REDACTED_MARKER
            else:
                redacted[key] = redact_for_log(value)
        return redacted
    if isinstance(payload, list):
        return [redact_for_log(entry) for entry in payload]
    if isinstance(payload, str):
        return _mask_tokens(payload)
    return payload


def _mask_tokens(value: str) -> str:
    masked = value
    for pattern in _TOKEN_PATTERNS:
        masked = pattern.sub(REDACTED_MARKER, masked)
    return masked


# Re-exports for convenience in tests.
__all__ = [
    "HealthCheckContext",
    "HealthCheckResult",
    "ProbeAttemptResult",
    "ProbeErrorCategory",
    "ServiceHealthResult",
    "REDACTED_MARKER",
    "MAX_LAST_ERROR_LENGTH",
    "redact_for_log",
    "truncate_error",
    "asdict",
]
