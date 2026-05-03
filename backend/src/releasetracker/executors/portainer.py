from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import yaml

from ..config import normalize_executor_target_ref
from .base import BaseRuntimeAdapter, RuntimeTarget, RuntimeUpdateResult

_SUPPORTED_PORTAINER_STACK_TYPES = {"standalone"}
_PORTAINER_STACK_TYPE_MAP = {
    1: "swarm",
    2: "standalone",
    3: "kubernetes",
}
_PORTAINER_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)
_PORTAINER_STACK_UPDATE_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=90.0, pool=5.0)


class PortainerRequestTimeoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortainerStackServiceUpdateResult:
    updated_services: list[str]
    message: str


class PortainerRuntimeAdapter(BaseRuntimeAdapter):
    def __init__(self, runtime_connection, client: Any | None = None):
        super().__init__(runtime_connection)
        self._client = client

    async def discover_targets(self) -> list[RuntimeTarget]:
        endpoint_id = self._runtime_endpoint_id()
        payload = await self._request_payload(
            "GET",
            "/api/stacks",
            params={"endpointId": endpoint_id},
        )
        if not isinstance(payload, list):
            raise RuntimeError("Portainer API response payload must be an array")

        targets: list[RuntimeTarget] = []
        for item in payload:
            if not isinstance(item, dict):
                continue

            stack_type = self._resolve_stack_type(item)
            if stack_type not in _SUPPORTED_PORTAINER_STACK_TYPES:
                continue

            stack_id = self._as_positive_int(item.get("Id") or item.get("id"))
            stack_name = self._as_text(item.get("Name") or item.get("name"))
            stack_endpoint_id = self._as_positive_int(
                item.get("EndpointId") or item.get("endpointId")
            )
            if stack_endpoint_id is None:
                stack_endpoint_id = endpoint_id

            if stack_endpoint_id != endpoint_id:
                continue
            if stack_id is None or stack_name is None:
                continue

            stack_file = await self.fetch_stack_file(
                endpoint_id=stack_endpoint_id, stack_id=stack_id
            )
            service_metadata = self._extract_stack_service_metadata(stack_file)
            image = self._resolve_stack_image(service_metadata)
            target_ref: dict[str, Any] = {
                "mode": "portainer_stack",
                "endpoint_id": stack_endpoint_id,
                "stack_id": stack_id,
                "stack_name": stack_name,
                "stack_type": stack_type,
                "services": service_metadata,
                "service_count": len(service_metadata),
            }

            entrypoint = self._as_text(
                item.get("EntryPoint") or item.get("entryPoint") or item.get("entrypoint")
            )
            if entrypoint:
                target_ref["entrypoint"] = entrypoint

            project_path = self._as_text(
                item.get("ProjectPath") or item.get("projectPath") or item.get("project_path")
            )
            if project_path:
                target_ref["project_path"] = project_path

            targets.append(
                RuntimeTarget(
                    runtime_type=self.runtime_connection.type,
                    name=stack_name,
                    target_ref=target_ref,
                    image=image,
                )
            )

        return targets

    async def validate_target_ref(self, target_ref: dict[str, Any]) -> None:
        normalized_target_ref = normalize_executor_target_ref(target_ref, runtime_type="portainer")
        endpoint_id = normalized_target_ref["endpoint_id"]
        stack_id = normalized_target_ref["stack_id"]
        stack_name = normalized_target_ref["stack_name"]
        declared_stack_type = normalized_target_ref["stack_type"].strip().lower()

        if endpoint_id != self._runtime_endpoint_id():
            raise ValueError(
                "target_ref.endpoint_id must match runtime connection config.endpoint_id"
            )

        if declared_stack_type not in _SUPPORTED_PORTAINER_STACK_TYPES:
            raise ValueError(
                "unsupported Portainer stack type for v1: "
                f"{declared_stack_type}; only standalone stacks are supported"
            )

        stack = await self.fetch_stack_detail(endpoint_id=endpoint_id, stack_id=stack_id)

        resolved_name = self._as_text(stack.get("Name") or stack.get("name"))
        if resolved_name and resolved_name != stack_name:
            raise ValueError(
                "target_ref.stack_name does not match Portainer stack identity "
                f"({stack_name} != {resolved_name})"
            )

        resolved_endpoint_id = self._as_positive_int(
            stack.get("EndpointId") or stack.get("endpointId")
        )
        if resolved_endpoint_id is not None and resolved_endpoint_id != endpoint_id:
            raise ValueError(
                "target_ref endpoint does not match Portainer stack endpoint "
                f"({endpoint_id} != {resolved_endpoint_id})"
            )

        resolved_stack_type = self._resolve_stack_type(stack)
        if resolved_stack_type and resolved_stack_type not in _SUPPORTED_PORTAINER_STACK_TYPES:
            raise ValueError(
                "unsupported Portainer stack type for v1: "
                f"{resolved_stack_type}; only standalone stacks are supported"
            )

        unsupported_reason = self._resolve_unsupported_stack_kind_reason(stack)
        if unsupported_reason is not None:
            raise ValueError(unsupported_reason)

    async def fetch_stack_service_images(self, target_ref: dict[str, Any]) -> dict[str, str | None]:
        normalized_target_ref = normalize_executor_target_ref(target_ref, runtime_type="portainer")
        endpoint_id = normalized_target_ref["endpoint_id"]
        stack_id = normalized_target_ref["stack_id"]

        stack = await self.fetch_stack_detail(endpoint_id=endpoint_id, stack_id=stack_id)
        unsupported_reason = self._resolve_unsupported_stack_kind_reason(stack)
        if unsupported_reason is not None:
            raise ValueError(unsupported_reason)

        stack_file = await self.fetch_stack_file(endpoint_id=endpoint_id, stack_id=stack_id)
        metadata = self._extract_stack_service_metadata(stack_file)
        service_images: dict[str, str | None] = {}
        for item in metadata:
            service_name = item.get("service")
            if isinstance(service_name, str) and service_name:
                service_images[service_name] = item.get("image")
        return service_images

    async def update_stack_services(
        self,
        target_ref: dict[str, Any],
        service_target_images: dict[str, str],
    ) -> PortainerStackServiceUpdateResult:
        normalized_target_ref = normalize_executor_target_ref(target_ref, runtime_type="portainer")
        endpoint_id = normalized_target_ref["endpoint_id"]
        stack_id = normalized_target_ref["stack_id"]

        if not service_target_images:
            return PortainerStackServiceUpdateResult(
                updated_services=[],
                message="runtime already at target image",
            )

        stack = await self.fetch_stack_detail(endpoint_id=endpoint_id, stack_id=stack_id)
        unsupported_reason = self._resolve_unsupported_stack_kind_reason(stack)
        if unsupported_reason is not None:
            raise ValueError(unsupported_reason)

        stack_file = await self.fetch_stack_file(endpoint_id=endpoint_id, stack_id=stack_id)
        patched_stack_file, updated_services = self._patch_stack_file_service_images(
            stack_file,
            service_target_images,
        )

        if not updated_services:
            return PortainerStackServiceUpdateResult(
                updated_services=[],
                message="runtime already at target image",
            )

        await self._update_stack(
            endpoint_id=endpoint_id,
            stack_id=stack_id,
            stack=stack,
            stack_file_content=patched_stack_file,
        )
        updated_service_names = sorted(updated_services)
        return PortainerStackServiceUpdateResult(
            updated_services=updated_service_names,
            message=(
                "Portainer stack updated via API for services: "
                f"{', '.join(updated_service_names)}"
            ),
        )

    async def get_current_image(self, target_ref: dict[str, Any]) -> str:
        raise NotImplementedError("Portainer image resolution is not implemented yet")

    async def capture_snapshot(
        self, target_ref: dict[str, Any], current_image: str
    ) -> dict[str, Any]:
        raise NotImplementedError("Portainer snapshot capture is not implemented yet")

    async def validate_snapshot(self, target_ref: dict[str, Any], snapshot: dict[str, Any]) -> None:
        raise NotImplementedError("Portainer snapshot validation is not implemented yet")

    async def update_image(self, target_ref: dict[str, Any], new_image: str) -> RuntimeUpdateResult:
        raise NotImplementedError("Portainer update is not implemented yet")

    async def fetch_stack_detail(self, *, endpoint_id: int, stack_id: int) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            f"/api/stacks/{stack_id}",
            params={"endpointId": endpoint_id},
            not_found_message=(
                "Portainer stack target not found or deleted "
                f"for endpoint_id={endpoint_id}, stack_id={stack_id}"
            ),
        )

    async def fetch_stack_file(self, *, endpoint_id: int, stack_id: int) -> str:
        payload = await self._request_json(
            "GET",
            f"/api/stacks/{stack_id}/file",
            params={"endpointId": endpoint_id},
            not_found_message=(
                "Portainer stack file not found for "
                f"endpoint_id={endpoint_id}, stack_id={stack_id}"
            ),
        )

        stack_file = self._as_text(
            payload.get("StackFileContent")
            or payload.get("stackFileContent")
            or payload.get("FileContent")
            or payload.get("fileContent")
        )
        if not stack_file:
            raise ValueError(
                "Portainer stack file payload is missing stack content for "
                f"endpoint_id={endpoint_id}, stack_id={stack_id}"
            )
        return stack_file

    async def _update_stack(
        self,
        *,
        endpoint_id: int,
        stack_id: int,
        stack: dict[str, Any],
        stack_file_content: str,
    ) -> None:
        env_payload = stack.get("Env")
        if not isinstance(env_payload, list):
            env_payload = []

        try:
            response = await self._get_client().request(
                "PUT",
                f"/api/stacks/{stack_id}",
                params={"endpointId": endpoint_id},
                json={
                    "stackFileContent": stack_file_content,
                    "env": env_payload,
                    "prune": True,
                    "pullImage": True,
                },
                timeout=_PORTAINER_STACK_UPDATE_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise PortainerRequestTimeoutError(
                "Portainer API request timed out during stack update: "
                f"PUT /api/stacks/{stack_id}"
            ) from exc
        if response.status_code == 404:
            raise ValueError(
                "Portainer stack target not found or deleted during update "
                f"for endpoint_id={endpoint_id}, stack_id={stack_id}"
            )
        if response.status_code >= 400:
            detail = response.text.strip()
            if detail:
                raise RuntimeError(
                    f"Portainer stack update failed ({response.status_code}): {detail}"
                )
            raise RuntimeError(f"Portainer stack update failed ({response.status_code})")

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        not_found_message: str,
    ) -> dict[str, Any]:
        payload = await self._request_payload(
            method,
            path,
            params=params,
            not_found_message=not_found_message,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Portainer API response payload must be an object")
        return payload

    async def _request_payload(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        not_found_message: str | None = None,
    ) -> Any:
        try:
            response = await self._get_client().request(
                method,
                path,
                params=params,
                timeout=_PORTAINER_DEFAULT_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise PortainerRequestTimeoutError(
                f"Portainer API request timed out: {method} {path}"
            ) from exc
        if response.status_code == 404:
            raise ValueError(not_found_message or f"Portainer API resource not found: {path}")
        if response.status_code >= 400:
            detail = response.text.strip()
            if detail:
                raise RuntimeError(
                    f"Portainer API request failed ({response.status_code}): {detail}"
                )
            raise RuntimeError(f"Portainer API request failed ({response.status_code})")
        return response.json()

    @classmethod
    def _extract_stack_service_metadata(cls, stack_file: str) -> list[dict[str, str | None]]:
        try:
            parsed = yaml.safe_load(stack_file)
        except yaml.YAMLError:
            return []
        if not isinstance(parsed, dict):
            return []

        services = parsed.get("services")
        if not isinstance(services, dict):
            return []

        metadata: list[dict[str, str | None]] = []
        for service_name, service_config in services.items():
            normalized_service_name = cls._as_text(service_name)
            if normalized_service_name is None:
                continue

            image: str | None = None
            if isinstance(service_config, dict):
                image = cls._as_text(service_config.get("image"))

            metadata.append({"service": normalized_service_name, "image": image})

        return sorted(metadata, key=lambda item: item["service"] or "")

    @classmethod
    def _patch_stack_file_service_images(
        cls,
        stack_file: str,
        service_target_images: dict[str, str],
    ) -> tuple[str, list[str]]:
        try:
            parsed = yaml.safe_load(stack_file)
        except yaml.YAMLError as exc:
            raise ValueError("Portainer stack file is not valid YAML") from exc

        if not isinstance(parsed, dict):
            raise ValueError("Portainer stack file payload must be a YAML object")

        services = parsed.get("services")
        if not isinstance(services, dict):
            raise ValueError("Portainer stack file is missing a services map")

        updated_services: list[str] = []
        for service_name in sorted(service_target_images):
            service_cfg = services.get(service_name)
            if not isinstance(service_cfg, dict):
                raise ValueError(f"Portainer stack service not found in stack file: {service_name}")

            next_image = cls._as_text(service_target_images.get(service_name))
            if next_image is None:
                raise ValueError(
                    f"target image must be a non-empty string for service: {service_name}"
                )

            current_image = cls._as_text(service_cfg.get("image"))
            if current_image == next_image:
                continue

            service_cfg["image"] = next_image
            updated_services.append(service_name)

        if not updated_services:
            return stack_file, []

        patched_stack_file = yaml.safe_dump(parsed, sort_keys=False)
        return patched_stack_file, updated_services

    @staticmethod
    def _resolve_stack_image(service_metadata: list[dict[str, str | None]]) -> str | None:
        images = {
            item.get("image")
            for item in service_metadata
            if isinstance(item.get("image"), str) and item.get("image")
        }
        if len(images) != 1:
            return None
        return next(iter(images))

    @classmethod
    def _resolve_unsupported_stack_kind_reason(cls, stack: dict[str, Any]) -> str | None:
        stack_type = cls._resolve_stack_type(stack)
        if stack_type not in _SUPPORTED_PORTAINER_STACK_TYPES:
            return (
                "unsupported Portainer stack type for v1: "
                f"{stack_type}; only standalone stacks are supported"
            )

        git_config = stack.get("GitConfig")
        if not isinstance(git_config, dict):
            git_config = stack.get("gitConfig")
        if isinstance(git_config, dict):
            repository_url = cls._as_text(git_config.get("URL") or git_config.get("url"))
            if repository_url:
                return (
                    "unsupported Portainer stack kind for v1: "
                    "git-backed standalone stacks are not supported"
                )
        return None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._runtime_base_url(),
                headers={"X-API-Key": self._runtime_api_key()},
            )
        return self._client

    def _runtime_base_url(self) -> str:
        base_url = self.runtime_connection.config.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("runtime connection config.base_url must be a non-empty string")
        return base_url.strip().rstrip("/")

    def _runtime_endpoint_id(self) -> int:
        endpoint_id = self.runtime_connection.config.get("endpoint_id")
        if not isinstance(endpoint_id, int) or endpoint_id <= 0:
            raise ValueError("runtime connection config.endpoint_id must be a positive integer")
        return endpoint_id

    def _runtime_api_key(self) -> str:
        api_key = self.runtime_connection.secrets.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("runtime connection secrets.api_key must be a non-empty string")
        return api_key.strip()

    @staticmethod
    def _resolve_stack_type(stack: dict[str, Any]) -> str | None:
        raw = stack.get("Type")
        if raw is None:
            raw = stack.get("type")

        if isinstance(raw, int):
            return _PORTAINER_STACK_TYPE_MAP.get(raw)

        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"1", "2", "3"}:
                return _PORTAINER_STACK_TYPE_MAP.get(int(normalized))
            if normalized:
                return normalized
        return None

    @staticmethod
    def _as_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @staticmethod
    def _as_positive_int(value: Any) -> int | None:
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            if parsed > 0:
                return parsed
        return None
