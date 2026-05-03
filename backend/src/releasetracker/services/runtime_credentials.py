from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import RuntimeConnectionConfig
from ..models import Credential

if TYPE_CHECKING:
    from ..storage.sqlite import SQLiteStorage


_RUNTIME_CREDENTIAL_TYPES: dict[str, set[str]] = {
    "docker": {"docker_runtime"},
    "podman": {"podman_runtime", "docker_runtime"},
    "kubernetes": {"kubernetes_runtime"},
    "portainer": {"portainer_runtime"},
}


async def materialize_runtime_connection_credentials(
    storage: "SQLiteStorage",
    runtime_connection: RuntimeConnectionConfig,
) -> RuntimeConnectionConfig:
    if runtime_connection.credential_id is None:
        return runtime_connection

    credential = await storage.get_credential(runtime_connection.credential_id)
    if credential is None:
        raise ValueError(f"runtime credential {runtime_connection.credential_id} not found")

    allowed_types = _RUNTIME_CREDENTIAL_TYPES[runtime_connection.type]
    if credential.type not in allowed_types:
        allowed_display = ", ".join(sorted(allowed_types))
        raise ValueError(
            f"runtime connection {runtime_connection.name} requires credential type "
            f"{allowed_display}, got {credential.type}"
        )

    return RuntimeConnectionConfig(
        **runtime_connection.model_dump(exclude={"secrets"}),
        secrets=dict(credential.secrets),
    )
