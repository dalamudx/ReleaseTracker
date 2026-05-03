"""Configuration management module"""

from typing import Any, Literal


from pydantic import BaseModel, Field, field_validator, model_validator

from .models import ReleaseChannel

EXECUTOR_BINDABLE_SOURCE_TYPES = frozenset({"container", "helm"})
EXECUTOR_GROUPED_BINDING_TARGET_MODES = frozenset(
    {"portainer_stack", "docker_compose", "kubernetes_workload"}
)


class Settings(BaseModel):
    """Application configuration"""


class Channel(BaseModel):
    """Release channel configuration"""

    # Channel name used for display and localization (four fixed options)
    name: Literal["stable", "prerelease", "beta", "canary"]

    # Optional platform type filter: include only release or pre-release
    # None includes both
    type: Literal["release", "prerelease"] | None = None

    # Include pattern (regex): include only versions that match this rule
    include_pattern: str | None = None

    # Exclude pattern (regex): exclude versions matching this rule; takes precedence over include
    exclude_pattern: str | None = None

    # Whether this channel is enabled
    enabled: bool = True


def flatten_release_channels(release_channels: list[ReleaseChannel]) -> list[Channel]:
    return [
        Channel(
            name=release_channel.name,
            type=release_channel.type,
            include_pattern=release_channel.include_pattern,
            exclude_pattern=release_channel.exclude_pattern,
            enabled=release_channel.enabled,
        )
        for release_channel in release_channels
    ]


class TrackerConfig(BaseModel):
    """Tracker configuration"""

    name: str
    type: Literal["github", "gitlab", "gitea", "helm", "container"]
    enabled: bool = True
    repo: str | None = None  # GitHub: "owner/repo"
    instance: str | None = None  # GitLab instance URL
    project: str | None = None  # GitLab: "group/project"
    chart: str | None = None  # Helm chart name
    image: str | None = None  # Docker image name, for example "library/nginx" or "owner/image"
    registry: str | None = None  # Docker registry URL
    version_sort_mode: Literal["published_at", "semver"] = "published_at"  # Version sorting mode
    fetch_limit: int = 10  # Fetch limit per run
    fetch_timeout: int = 15  # Fetch timeout in seconds
    fallback_tags: bool = (
        False  # If normal fetching fails, such as empty GitHub Releases, fall back to extracting versions from refs/tags
    )
    github_fetch_mode: Literal["graphql_first", "rest_first"] = "rest_first"
    interval: int = 360  # Check interval in minutes
    credential_name: str | None = (
        None  # Credential name reference instead of storing tokens directly
    )

    # Multi-channel configuration
    channels: list[Channel] = Field(default_factory=list)

    @staticmethod
    def validate_tracker_name(value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("name must be a non-empty string")
        return normalized_value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return cls.validate_tracker_name(value)


class NotifierConfig(BaseModel):
    """Notifier configuration"""

    name: str
    type: Literal["webhook", "email"]
    url: str | None = None
    events: list[str] = Field(default_factory=lambda: ["new_release"])


class MaintenanceWindowConfig(BaseModel):
    timezone: str = "UTC"
    days_of_week: list[int] = Field(default_factory=list)
    start_time: str
    end_time: str


class RuntimeConnectionConfig(BaseModel):
    id: int | None = None
    name: str
    type: Literal["docker", "podman", "kubernetes", "portainer"]
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    credential_id: int | None = None
    secrets: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None

    @model_validator(mode="after")
    def validate_runtime_connection(self):
        if self.type in {"docker", "podman"}:
            self._validate_container_runtime_connection()
        elif self.type == "kubernetes":
            self._validate_kubernetes_runtime_connection()
        elif self.type == "portainer":
            self._validate_portainer_runtime_connection()
        return self

    def _validate_container_runtime_connection(self) -> None:
        allowed_config_keys = {"socket", "tls_verify", "api_version"}
        self._reject_unknown_keys(self.config, allowed_config_keys, "config")

        socket = self._optional_non_empty_string(self.config.get("socket"), "config.socket")
        if not socket:
            raise ValueError("Docker/Podman runtime connection requires config.socket")

        if not socket.startswith(("unix://", "tcp://")):
            raise ValueError("config.socket must start with unix:// or tcp://")

        if "tls_verify" in self.config and not isinstance(self.config["tls_verify"], bool):
            raise ValueError("config.tls_verify must be a boolean")

        if "api_version" in self.config:
            self._optional_non_empty_string(self.config["api_version"], "config.api_version")

    def _validate_kubernetes_runtime_connection(self) -> None:
        allowed_config_keys = {"context", "namespace", "namespaces", "in_cluster"}
        self._reject_unknown_keys(self.config, allowed_config_keys, "config")

        in_cluster = self.config.get("in_cluster", False)

        if in_cluster is not False and not isinstance(in_cluster, bool):
            raise ValueError("config.in_cluster must be a boolean")

        if not in_cluster and self.credential_id is None:
            raise ValueError(
                "Kubernetes runtime connection requires credential_id unless config.in_cluster is true"
            )

        if "context" in self.config:
            self._optional_non_empty_string(self.config["context"], "config.context")
        if "namespace" in self.config:
            self._optional_non_empty_string(self.config["namespace"], "config.namespace")
        if "namespaces" in self.config:
            namespaces = self.config["namespaces"]
            if not isinstance(namespaces, list):
                raise ValueError("config.namespaces must be an array of non-empty strings")
            normalized_namespaces = []
            for namespace in namespaces:
                normalized_namespace = self._optional_non_empty_string(
                    namespace, "config.namespaces[]"
                )
                if normalized_namespace is None:
                    raise ValueError("config.namespaces must be an array of non-empty strings")
                normalized_namespaces.append(normalized_namespace)
            self.config["namespaces"] = normalized_namespaces

    def _validate_portainer_runtime_connection(self) -> None:
        allowed_config_keys = {"base_url", "endpoint_id"}

        self._reject_unknown_keys(self.config, allowed_config_keys, "config")

        base_url = self._optional_non_empty_string(self.config.get("base_url"), "config.base_url")
        if not base_url:
            raise ValueError("Portainer runtime connection requires config.base_url")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("config.base_url must start with http:// or https://")

        endpoint_id = self.config.get("endpoint_id")
        if not isinstance(endpoint_id, int) or endpoint_id <= 0:
            raise ValueError("config.endpoint_id must be a positive integer")

        if self.credential_id is None:
            raise ValueError("Portainer runtime connection requires credential_id")

    @staticmethod
    def _reject_unknown_keys(payload: dict[str, Any], allowed_keys: set[str], label: str) -> None:
        unknown_keys = sorted(set(payload.keys()) - allowed_keys)
        if unknown_keys:
            raise ValueError(f"Unknown {label} keys: {', '.join(unknown_keys)}")

    @staticmethod
    def _optional_non_empty_string(value: Any, label: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        return value


class ContainerExecutorTargetRef(BaseModel):
    mode: Literal["container"] = "container"
    container_id: str | None = None
    container_name: str | None = None

    @field_validator("container_id", "container_name")
    @classmethod
    def _validate_optional_string(cls, value: Any, info):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"target_ref.{info.field_name} must be a non-empty string")
        return value

    @model_validator(mode="after")
    def _validate_identity(self):
        if not (self.container_id or self.container_name):
            raise ValueError("target_ref must include container_id or container_name")
        return self


class PortainerStackExecutorTargetRef(BaseModel):
    mode: Literal["portainer_stack"] = "portainer_stack"
    endpoint_id: int
    stack_id: int
    stack_name: str
    stack_type: str
    entrypoint: str | None = None
    project_path: str | None = None

    @field_validator("endpoint_id", "stack_id")
    @classmethod
    def _validate_positive_integers(cls, value: Any, info):
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"target_ref.{info.field_name} must be a positive integer")
        return value

    @field_validator("stack_name", "stack_type", "entrypoint", "project_path")
    @classmethod
    def _validate_non_empty_strings(cls, value: Any, info):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"target_ref.{info.field_name} must be a non-empty string")
        return value


class DockerComposeExecutorTargetRef(BaseModel):
    mode: Literal["docker_compose"] = "docker_compose"
    project: str
    working_dir: str | None = None
    config_files: list[str] = Field(default_factory=list)
    services: list[dict[str, Any]] = Field(default_factory=list)
    service_count: int | None = None

    @field_validator("project")
    @classmethod
    def _validate_non_empty_strings(cls, value: Any, info):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"target_ref.{info.field_name} must be a non-empty string")
        return value.strip()

    @field_validator("working_dir")
    @classmethod
    def _validate_optional_working_dir(cls, value: Any):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("target_ref.working_dir must be a non-empty string when provided")
        return value.strip()

    @field_validator("config_files")
    @classmethod
    def _validate_config_files(cls, value: Any):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("target_ref.config_files must be an array")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("target_ref.config_files entries must be non-empty strings")
            normalized.append(item.strip())
        return normalized

    @field_validator("service_count")
    @classmethod
    def _validate_service_count(cls, value: Any):
        if value is None:
            return None
        if not isinstance(value, int) or value < 0:
            raise ValueError("target_ref.service_count must be a non-negative integer")
        return value


class KubernetesWorkloadExecutorTargetRef(BaseModel):
    mode: Literal["kubernetes_workload"] = "kubernetes_workload"
    namespace: str
    kind: Literal["Deployment", "StatefulSet", "DaemonSet"]
    name: str
    services: list[dict[str, Any]] = Field(default_factory=list)
    service_count: int | None = None

    @field_validator("namespace", "name")
    @classmethod
    def _validate_non_empty_strings(cls, value: Any, info):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"target_ref.{info.field_name} must be a non-empty string")
        return value.strip()

    @field_validator("services")
    @classmethod
    def _validate_services(cls, value: Any):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("target_ref.services must be an array")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("target_ref.services entries must be objects")
            service = item.get("service")
            if not isinstance(service, str) or not service.strip():
                raise ValueError("target_ref.services[].service must be a non-empty string")
            normalized_service = service.strip().lower()
            if normalized_service in seen:
                continue
            seen.add(normalized_service)
            normalized_item = dict(item)
            normalized_item["service"] = normalized_service
            image = normalized_item.get("image")
            if image is not None and (not isinstance(image, str) or not image.strip()):
                normalized_item["image"] = None
            normalized.append(normalized_item)
        return normalized

    @field_validator("service_count")
    @classmethod
    def _validate_service_count(cls, value: Any):
        if value is None:
            return None
        if not isinstance(value, int) or value < 0:
            raise ValueError("target_ref.service_count must be a non-negative integer")
        return value


class HelmReleaseExecutorTargetRef(BaseModel):
    mode: Literal["helm_release"] = "helm_release"
    namespace: str
    release_name: str
    chart_name: str | None = None
    chart_version: str | None = None
    app_version: str | None = None
    workloads: list[dict[str, Any]] = Field(default_factory=list)
    service_count: int | None = None

    @field_validator("namespace", "release_name")
    @classmethod
    def _validate_required_strings(cls, value: Any, info):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"target_ref.{info.field_name} must be a non-empty string")
        return value.strip()

    @field_validator("chart_name", "chart_version", "app_version")
    @classmethod
    def _validate_optional_strings(cls, value: Any, info):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"target_ref.{info.field_name} must be a non-empty string when provided"
            )
        return value.strip()

    @field_validator("workloads")
    @classmethod
    def _validate_workloads(cls, value: Any):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("target_ref.workloads must be an array")
        return [item for item in value if isinstance(item, dict)]

    @field_validator("service_count")
    @classmethod
    def _validate_service_count(cls, value: Any):
        if value is None:
            return None
        if not isinstance(value, int) or value < 0:
            raise ValueError("target_ref.service_count must be a non-negative integer")
        return value


def normalize_executor_target_ref(
    target_ref: Any,
    *,
    runtime_type: Literal["docker", "podman", "kubernetes", "portainer"] | None = None,
) -> dict[str, Any]:
    if not isinstance(target_ref, dict):
        raise ValueError("target_ref must be an object")

    mode = target_ref.get("mode")

    if runtime_type == "portainer":
        if mode != "portainer_stack":
            raise ValueError("portainer runtime requires target_ref.mode 'portainer_stack'")
        return PortainerStackExecutorTargetRef(**target_ref).model_dump(exclude_none=True)

    if runtime_type == "kubernetes":
        if mode == "kubernetes_workload":
            return KubernetesWorkloadExecutorTargetRef(**target_ref).model_dump(exclude_none=True)
        if mode == "helm_release":
            return HelmReleaseExecutorTargetRef(**target_ref).model_dump(exclude_none=True)
        if mode == "container":
            raise ValueError(
                "kubernetes runtime requires target_ref.mode 'kubernetes_workload' or 'helm_release'"
            )
        if mode == "portainer_stack":
            raise ValueError(
                "target_ref.mode 'portainer_stack' is only supported when runtime_type is 'portainer'"
            )
        if mode == "docker_compose":
            raise ValueError(
                "target_ref.mode 'docker_compose' is only supported when runtime_type is 'docker' or 'podman'"
            )
        raise ValueError(
            "kubernetes runtime requires target_ref.mode 'kubernetes_workload' or 'helm_release'"
        )

    if mode is None:
        raise ValueError("target_ref.mode is required")

    if mode == "container":
        return ContainerExecutorTargetRef(**target_ref).model_dump(exclude_none=True)

    if mode == "portainer_stack":
        if runtime_type != "portainer":
            raise ValueError(
                "target_ref.mode 'portainer_stack' is only supported when runtime_type is 'portainer'"
            )
        return PortainerStackExecutorTargetRef(**target_ref).model_dump(exclude_none=True)

    if mode == "docker_compose":
        if runtime_type not in {"docker", "podman"}:
            raise ValueError(
                "target_ref.mode 'docker_compose' is only supported when runtime_type is 'docker' or 'podman'"
            )
        return DockerComposeExecutorTargetRef(**target_ref).model_dump(exclude_none=True)

    if mode == "kubernetes_workload":
        if runtime_type != "kubernetes":
            raise ValueError(
                "target_ref.mode 'kubernetes_workload' is only supported when runtime_type is 'kubernetes'"
            )
        return KubernetesWorkloadExecutorTargetRef(**target_ref).model_dump(exclude_none=True)

    if mode == "helm_release":
        if runtime_type != "kubernetes":
            raise ValueError(
                "target_ref.mode 'helm_release' is only supported when runtime_type is 'kubernetes'"
            )
        return HelmReleaseExecutorTargetRef(**target_ref).model_dump(exclude_none=True)

    raise ValueError(
        "target_ref.mode must be one of: container, portainer_stack, docker_compose, kubernetes_workload, helm_release"
    )


class ExecutorServiceBinding(BaseModel):
    service: str
    tracker_source_id: int
    channel_name: str

    @field_validator("service")
    @classmethod
    def _validate_service(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("service_bindings[].service must be a non-empty string")
        return value.strip().lower()

    @field_validator("tracker_source_id")
    @classmethod
    def _validate_tracker_source_id(cls, value: Any) -> int:
        if not isinstance(value, int) or value <= 0:
            raise ValueError("service_bindings[].tracker_source_id must be a positive integer")
        return value

    @field_validator("channel_name")
    @classmethod
    def _validate_channel_name(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("service_bindings[].channel_name must be a non-empty string")
        return value.strip()


class ExecutorConfig(BaseModel):
    id: int | None = None
    name: str
    runtime_type: Literal["docker", "podman", "kubernetes", "portainer"]
    runtime_connection_id: int
    tracker_name: str
    tracker_source_id: int | None = None
    channel_name: str | None = None
    enabled: bool = True
    image_selection_mode: Literal[
        "replace_tag_on_current_image",
        "use_tracker_image_and_tag",
    ] = "replace_tag_on_current_image"
    image_reference_mode: Literal["digest", "tag"] = "digest"
    update_mode: Literal["manual", "maintenance_window", "immediate"] = "manual"
    target_ref: dict[str, Any] = Field(default_factory=dict)
    service_bindings: list[ExecutorServiceBinding] = Field(default_factory=list)
    maintenance_window: MaintenanceWindowConfig | None = None
    description: str | None = None

    model_config = {"extra": "allow"}

    @field_validator("target_ref", mode="before")
    @classmethod
    def validate_target_ref_schema(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("target_ref must be an object")
        return value

    @model_validator(mode="after")
    def validate_target_ref_runtime_compatibility(self):
        self.target_ref = normalize_executor_target_ref(
            self.target_ref, runtime_type=self.runtime_type
        )
        return self

    @model_validator(mode="after")
    def validate_service_bindings(self):
        target_mode = self.target_ref.get("mode")

        if target_mode in EXECUTOR_GROUPED_BINDING_TARGET_MODES:
            if not self.service_bindings:
                raise ValueError(
                    f"{target_mode} executors must define at least one service binding"
                )

            seen_services: set[str] = set()
            for binding in self.service_bindings:
                if binding.service in seen_services:
                    raise ValueError(f"duplicate grouped service binding: {binding.service}")
                seen_services.add(binding.service)

            return self

        if self.service_bindings:
            raise ValueError(
                "service_bindings are only supported for grouped executors "
                f"({', '.join(sorted(EXECUTOR_GROUPED_BINDING_TARGET_MODES))})"
            )

        return self
