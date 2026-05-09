"""Helm release status health probe.

Dispatches ``helm status <release> --namespace <ns> --output json`` via the
Kubernetes adapter and maps ``info.status`` to probe outcomes. The probe is
intentionally strict: any failure to parse the JSON response or to invoke
the Helm CLI is treated as unhealthy without aborting the phase, so
transient kubeconfig or network issues retry on the next interval.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .probe import HealthCheckProbe
from .types import ProbeAttemptResult

if TYPE_CHECKING:
    from .types import HealthCheckContext


# Mapping from Helm ``info.status`` to how the runner should react.
_HELM_STATUS_HEALTHY = {"deployed"}
_HELM_STATUS_PENDING = {"pending-install", "pending-upgrade", "pending-rollback"}
_HELM_STATUS_FAILED = {"failed"}
# Everything else (``superseded``, ``uninstalled``, ``uninstalling``,
# ``unknown``, and any future status name) is treated as unhealthy but
# non-terminal so the probe retries.


class HelmStatusProbe(HealthCheckProbe):
    """Invoke ``helm status`` and classify the response."""

    async def attempt(self, ctx: "HealthCheckContext") -> ProbeAttemptResult:
        target_ref = ctx.executor_config.target_ref
        namespace = target_ref.get("namespace")
        release_name = target_ref.get("release_name")

        if not isinstance(namespace, str) or not isinstance(release_name, str):
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="target_ref missing namespace/release_name",
            )

        adapter = ctx.adapter
        runner = getattr(adapter, "_run_helm_command", None)
        if not callable(runner):
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error=f"{adapter.__class__.__name__} does not expose _run_helm_command",
            )

        try:
            output = runner(
                [
                    "status",
                    release_name,
                    "--namespace",
                    namespace,
                    "--output",
                    "json",
                ]
            )
        except Exception as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error=f"helm status failed: {exc}",
            )

        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error=f"helm status output is not JSON: {exc}",
            )

        status = self._extract_status(payload)
        detail: dict[str, Any] = {"helm_status": status}

        if status in _HELM_STATUS_HEALTHY:
            return ProbeAttemptResult(healthy=True, detail=detail)

        if status in _HELM_STATUS_PENDING:
            return ProbeAttemptResult(
                healthy=False,
                error_category="helm_pending",
                detail=detail,
                last_error=f"helm release status is {status!r}; retrying",
            )

        if status in _HELM_STATUS_FAILED:
            # ``failed`` short-circuits the probe window: the runner bails
            # out immediately instead of waiting for the grace period.
            return ProbeAttemptResult(
                healthy=False,
                error_category="helm_failed",
                detail=detail,
                last_error=f"helm release status is 'failed'",
                terminate_phase=True,
            )

        # Unknown / unmapped status — keep retrying with the status
        # recorded for diagnostics.
        return ProbeAttemptResult(
            healthy=False,
            error_category="helm_unknown_status",
            detail=detail,
            last_error=f"helm release status is {status!r}",
        )

    @staticmethod
    def _extract_status(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        info = payload.get("info")
        if not isinstance(info, dict):
            return None
        status = info.get("status")
        if isinstance(status, str) and status.strip():
            return status.strip()
        return None


__all__ = ["HelmStatusProbe"]
