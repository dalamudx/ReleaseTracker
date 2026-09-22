from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

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


_runtime_adapter_worker_active: ContextVar[bool] = ContextVar(
    "runtime_adapter_worker_active", default=False
)


def _run_adapter_operation_in_thread(
    operation: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    async def invoke() -> Any:
        token = _runtime_adapter_worker_active.set(True)
        try:
            return await operation(*args, **kwargs)
        finally:
            _runtime_adapter_worker_active.reset(token)

    return asyncio.run(invoke())


def offload_blocking_runtime_adapter_methods(cls):
    """Run coroutine methods of a synchronous runtime SDK adapter off the event loop.

    Docker, Podman, and Kubernetes SDKs expose blocking Python methods behind
    async adapter APIs. Nested adapter calls remain in the same worker thread;
    cancellation waits for a destructive SDK operation to complete before the
    caller can release its executor overlap guard.
    """

    for name, member in vars(cls).items():
        if not inspect.iscoroutinefunction(member):
            continue

        @wraps(member)
        async def offloaded(self, *args, __member=member, **kwargs):
            if _runtime_adapter_worker_active.get():
                return await __member(self, *args, **kwargs)

            worker = asyncio.create_task(
                asyncio.to_thread(_run_adapter_operation_in_thread, __member, (self, *args), kwargs)
            )
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(worker)
                except Exception:
                    pass
                raise

        setattr(cls, name, offloaded)
    return cls


class BaseRuntimeAdapter(ABC):
    def __init__(self, runtime_connection: RuntimeConnectionConfig):
        self.runtime_connection = runtime_connection

    @abstractmethod
    async def discover_targets(self) -> list[RuntimeTarget]:
        raise NotImplementedError

    @abstractmethod
    async def validate_target_ref(self, target_ref: dict[str, Any]) -> None:
        raise NotImplementedError

    def supports_single_image_operations(self, target_ref: dict[str, Any]) -> bool:
        """Whether the generic one-target/one-image contract applies.

        Grouped runtime targets override this to keep callers from treating a
        multi-service target as if it had one current image.
        """
        del target_ref
        return True

    @abstractmethod
    async def get_current_image(self, target_ref: dict[str, Any]) -> str:
        raise NotImplementedError

    async def get_current_image_digest(self, target_ref: dict[str, Any]) -> str | None:
        """Return a repository manifest digest when the runtime exposes one."""
        del target_ref
        return None

    async def get_managed_markers(self, target_ref: dict[str, Any]) -> tuple[dict[str, str], ...]:
        """Read ReleaseTracker ownership markers without changing the runtime."""
        del target_ref
        return ()

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

    def is_target_missing_error(self, exc: Exception) -> bool:
        """Return true only for an explicit runtime resource-not-found error."""
        del exc
        return False

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
