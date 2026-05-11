from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..config import RuntimeConnectionConfig


@dataclass(frozen=True)
class RuntimeTarget:
    runtime_type: str
    name: str
    target_ref: dict[str, Any]
    image: str | None = None


@dataclass(frozen=True)
class RuntimeUpdateResult:
    updated: bool
    old_image: str | None
    new_image: str | None
    message: str | None = None
    new_container_id: str | None = None


class RuntimeMutationError(RuntimeError):
    def __init__(self, message: str, *, destructive_started: bool = False):
        super().__init__(message)
        self.destructive_started = destructive_started


class BaseRuntimeAdapter(ABC):
    def __init__(self, runtime_connection: RuntimeConnectionConfig):
        self.runtime_connection = runtime_connection

    @abstractmethod
    async def discover_targets(self) -> list[RuntimeTarget]:
        raise NotImplementedError

    @abstractmethod
    async def validate_target_ref(self, target_ref: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_current_image(self, target_ref: dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    async def capture_snapshot(
        self, target_ref: dict[str, Any], current_image: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def validate_snapshot(self, target_ref: dict[str, Any], snapshot: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def update_image(self, target_ref: dict[str, Any], new_image: str) -> RuntimeUpdateResult:
        raise NotImplementedError

    async def recover_from_snapshot(
        self, target_ref: dict[str, Any], snapshot: dict[str, Any]
    ) -> RuntimeUpdateResult:
        raise NotImplementedError("runtime adapter does not support recovery")

    async def probe_runtime_native_health(
        self,
        target_ref: dict[str, Any],
        *,
        baseline: dict[str, Any],
        services: list[str] | None = None,
    ) -> "Any":
        """Return a ``ProbeAttemptResult`` for the runtime-native strategy.

        Default implementation signals the runner to treat the attempt as
        a runtime-native "not supported" error; adapters that implement
        readiness semantics override this. Typed as ``Any`` to avoid
        circular import with ``health_check.types``; the runner consumes
        whatever dataclass subclass each adapter returns.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement runtime-native health probing"
        )

    async def resolve_probe_hosts(
        self,
        target_ref: dict[str, Any],
        *,
        services: list[str] | None = None,
        default_port: int | None = None,
    ) -> list["Any"]:
        """Return a list of ``ProbeHost`` entries reachable from the adapter.

        Concrete adapters implement this to surface the list of
        ``(service, host, port)`` tuples HTTP / TCP probes should target.
        The default raises ``NotImplementedError`` so HTTP / TCP probes
        can map it to ``host_unresolvable``.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement resolve_probe_hosts"
        )

    async def resolve_auto_probe_hosts(
        self,
        target_ref: dict[str, Any],
        *,
        services: list[str] | None = None,
        default_port: int | None = None,
    ) -> list["Any"]:
        """Return runtime-derived host targets for auto-mode fallback probes."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement auto host-port probing"
        )

    async def has_runtime_native_healthcheck(
        self,
        target_ref: dict[str, Any],
        *,
        services: list[str] | None = None,
    ) -> bool:
        """Return whether runtime-native app health is configured for auto mode."""
        del target_ref, services
        return False

    async def validate_probe_network_path(
        self,
        target_ref: dict[str, Any],
        profile: "Any",
    ) -> None:
        """Raise ``ValueError`` if the configured probe cannot be reached.

        The scheduler calls this at executor save time so operators see
        unsupported combinations as 400 responses rather than per-run
        failures. Adapters that cannot pre-flight the path simply return
        ``None`` (the default) and let runtime failures surface as
        probe-level ``host_unresolvable`` outcomes.
        """
        return None

    def _require_target_field(self, target_ref: dict[str, Any], field: str) -> str:
        value = target_ref.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"target_ref.{field} must be a non-empty string")
        return value
