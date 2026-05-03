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

    def _require_target_field(self, target_ref: dict[str, Any], field: str) -> str:
        value = target_ref.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"target_ref.{field} must be a non-empty string")
        return value
