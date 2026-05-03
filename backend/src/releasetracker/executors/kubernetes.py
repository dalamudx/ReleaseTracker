from __future__ import annotations

from contextlib import contextmanager
from typing import Any
import importlib
import json
import logging
import os
import subprocess
import tempfile

from .base import BaseRuntimeAdapter, RuntimeMutationError, RuntimeTarget, RuntimeUpdateResult

logger = logging.getLogger(__name__)


class KubernetesRuntimeAdapter(BaseRuntimeAdapter):
    def __init__(self, runtime_connection, apps_api=None):
        super().__init__(runtime_connection)
        self._apps_api = apps_api
        self._core_api = None

    async def discover_targets(self, namespace: str | None = None) -> list[RuntimeTarget]:
        apps_api = self._get_apps_api()
        targets: list[RuntimeTarget] = []

        for discovery_namespace in self._resolve_discovery_namespaces(namespace):
            workloads = self._list_workloads(apps_api, discovery_namespace)
            helm_release_workloads = self._group_helm_release_workloads(
                workloads, discovery_namespace
            )
            for release_key in sorted(helm_release_workloads):
                release_workloads = helm_release_workloads[release_key]
                targets.append(
                    self._build_helm_release_target(discovery_namespace, release_workloads)
                )

            for workload in workloads:
                if self._get_helm_release_name(workload):
                    continue
                containers = self._workload_containers(workload)
                if not containers:
                    continue
                services = [
                    {"service": container["name"], "image": container["image"]}
                    for container in containers
                ]
                target_ref = {
                    "mode": "kubernetes_workload",
                    "namespace": discovery_namespace,
                    "kind": workload["kind"],
                    "name": workload["name"],
                    "services": services,
                    "service_count": len(services),
                }
                targets.append(
                    RuntimeTarget(
                        runtime_type="kubernetes",
                        name=f"{workload['kind'].lower()}/{workload['name']}",
                        target_ref=target_ref,
                        image=services[0]["image"] if len(services) == 1 else None,
                    )
                )
        return targets

    async def discover_namespaces(self) -> list[str]:
        core_api = self._get_core_api()
        namespaces = [
            item.metadata.name
            for item in core_api.list_namespace().items
            if getattr(item.metadata, "name", None)
        ]
        return sorted(set(namespaces))

    async def validate_target_ref(self, target_ref: dict[str, Any]) -> None:
        if target_ref.get("mode") == "helm_release":
            self._get_helm_release(target_ref)
            return

        self._require_workload_target_mode(target_ref)
        namespace = self._require_target_field(target_ref, "namespace")
        self._authorize_namespace(namespace)
        kind = self._require_target_field(target_ref, "kind")
        name = self._require_target_field(target_ref, "name")
        self._validate_workload_kind(kind)
        self._get_workload(kind, name, namespace)

    async def get_current_image(self, target_ref: dict[str, Any]) -> str:
        self._require_workload_target_mode(target_ref)
        raise ValueError(
            "Kubernetes workload targets use service_bindings, not single-container image reads"
        )

    async def capture_snapshot(
        self, target_ref: dict[str, Any], current_image: str
    ) -> dict[str, Any]:
        self._require_workload_target_mode(target_ref)
        raise ValueError(
            "Kubernetes workload targets use grouped updates and do not support single-container snapshots"
        )

    async def validate_snapshot(self, target_ref: dict[str, Any], snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict) or not snapshot:
            raise ValueError("snapshot must be a non-empty dict")
        for field in ("namespace", "kind", "name", "container", "image"):
            value = snapshot.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"snapshot.{field} must be a non-empty string")
        workload = snapshot.get("workload")
        if not isinstance(workload, dict) or not workload:
            raise ValueError("snapshot.workload must be a non-empty dict")

    async def update_image(self, target_ref: dict[str, Any], new_image: str) -> RuntimeUpdateResult:
        self._require_workload_target_mode(target_ref)
        raise ValueError("Kubernetes workload targets use grouped service updates")

    async def fetch_workload_service_images(
        self, target_ref: dict[str, Any]
    ) -> dict[str, str | None]:
        self._require_workload_target_mode(target_ref)
        namespace = self._require_target_field(target_ref, "namespace")
        self._authorize_namespace(namespace)
        kind = self._require_target_field(target_ref, "kind")
        name = self._require_target_field(target_ref, "name")
        self._validate_workload_kind(kind)
        workload = self._get_workload(kind, name, namespace)
        return {
            container["name"]: container["image"]
            for container in self._workload_containers(workload)
        }

    async def update_workload_services(
        self,
        target_ref: dict[str, Any],
        service_target_images: dict[str, str],
    ) -> RuntimeUpdateResult:
        self._require_workload_target_mode(target_ref)
        namespace = self._require_target_field(target_ref, "namespace")
        self._authorize_namespace(namespace)
        kind = self._require_target_field(target_ref, "kind")
        name = self._require_target_field(target_ref, "name")
        self._validate_workload_kind(kind)

        if not service_target_images:
            return RuntimeUpdateResult(
                updated=False,
                old_image=None,
                new_image=None,
                message="runtime already at target image",
            )

        current_images = await self.fetch_workload_service_images(target_ref)
        missing_services = sorted(
            service for service in service_target_images if service not in current_images
        )
        if missing_services:
            raise ValueError(
                "Kubernetes workload does not contain selected container(s): "
                + ", ".join(missing_services)
            )

        containers_patch = [
            {"name": service, "image": image}
            for service, image in sorted(service_target_images.items())
        ]
        patch_body = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": containers_patch,
                    }
                }
            }
        }

        apps_api = self._get_apps_api()
        if kind == "Deployment":
            apps_api.patch_namespaced_deployment(name, namespace, patch_body)
        elif kind == "StatefulSet":
            apps_api.patch_namespaced_stateful_set(name, namespace, patch_body)
        elif kind == "DaemonSet":
            apps_api.patch_namespaced_daemon_set(name, namespace, patch_body)
        else:
            raise ValueError("Unsupported Kubernetes workload kind")

        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=None,
            message=(
                "Kubernetes workload updated for containers: "
                + ", ".join(sorted(service_target_images))
            ),
        )

    async def get_helm_release_version(self, target_ref: dict[str, Any]) -> str | None:
        release = self._get_helm_release(target_ref)
        _, chart_version = self._parse_helm_release_chart(release)
        return chart_version or self._optional_target_field(target_ref, "chart_version")

    async def capture_helm_release_snapshot(self, target_ref: dict[str, Any]) -> dict[str, Any]:
        release = self._get_helm_release(target_ref)
        namespace = self._require_target_field(target_ref, "namespace")
        release_name = self._require_target_field(target_ref, "release_name")
        chart_name, chart_version = self._parse_helm_release_chart(release)
        app_version = self._parse_helm_release_app_version(release)
        return {
            "mode": "helm_release",
            "namespace": namespace,
            "release_name": release_name,
            "chart_name": chart_name or self._optional_target_field(target_ref, "chart_name"),
            "chart_version": chart_version
            or self._optional_target_field(target_ref, "chart_version"),
            "app_version": app_version or self._optional_target_field(target_ref, "app_version"),
            "release": release,
        }

    async def validate_helm_release_snapshot(
        self, target_ref: dict[str, Any], snapshot: dict[str, Any]
    ) -> None:
        if not isinstance(snapshot, dict) or not snapshot:
            raise ValueError("snapshot must be a non-empty dict")
        if snapshot.get("mode") != "helm_release":
            raise ValueError("snapshot.mode must be helm_release")
        for field in ("namespace", "release_name", "chart_version"):
            value = snapshot.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"snapshot.{field} must be a non-empty string")
        if snapshot.get("namespace") != self._require_target_field(target_ref, "namespace"):
            raise ValueError("snapshot.namespace must match target_ref.namespace")
        if snapshot.get("release_name") != self._require_target_field(target_ref, "release_name"):
            raise ValueError("snapshot.release_name must match target_ref.release_name")
        release = snapshot.get("release")
        if not isinstance(release, dict) or not release:
            raise ValueError("snapshot.release must be a non-empty dict")

    async def upgrade_helm_release(
        self,
        target_ref: dict[str, Any],
        *,
        chart_ref: str,
        chart_version: str,
        repo_url: str | None,
    ) -> RuntimeUpdateResult:
        namespace = self._require_target_field(target_ref, "namespace")
        release_name = self._require_target_field(target_ref, "release_name")
        self._authorize_namespace(namespace)
        if not isinstance(chart_ref, str) or not chart_ref.strip():
            raise ValueError("chart_ref must be a non-empty string")
        if not isinstance(chart_version, str) or not chart_version.strip():
            raise ValueError("chart_version must be a non-empty string")

        current_version = await self.get_helm_release_version(target_ref)
        if current_version == chart_version:
            return RuntimeUpdateResult(
                updated=False,
                old_image=current_version,
                new_image=chart_version,
                message="Helm release already at target chart version",
            )

        command = [
            "upgrade",
            release_name,
            chart_ref.strip(),
            "--namespace",
            namespace,
            "--version",
            chart_version.strip(),
            "--install",
        ]
        if isinstance(repo_url, str) and repo_url.strip():
            command.extend(["--repo", repo_url.strip()])
        try:
            self._run_helm_command(command)
        except Exception as exc:
            raise RuntimeMutationError(str(exc), destructive_started=True) from exc

        return RuntimeUpdateResult(
            updated=True,
            old_image=current_version,
            new_image=chart_version,
            message=f"Helm release upgraded to chart version {chart_version}",
        )

    async def recover_helm_release_from_snapshot(
        self, target_ref: dict[str, Any], snapshot: dict[str, Any]
    ) -> RuntimeUpdateResult:
        await self.validate_helm_release_snapshot(target_ref, snapshot)
        namespace = self._require_target_field(target_ref, "namespace")
        release_name = self._require_target_field(target_ref, "release_name")
        chart_name = snapshot.get("chart_name")
        chart_version = snapshot.get("chart_version")
        if not isinstance(chart_name, str) or not chart_name.strip():
            raise ValueError("snapshot.chart_name must be a non-empty string")
        if not isinstance(chart_version, str) or not chart_version.strip():
            raise ValueError("snapshot.chart_version must be a non-empty string")
        self._run_helm_command(
            [
                "upgrade",
                release_name,
                chart_name,
                "--namespace",
                namespace,
                "--version",
                chart_version,
                "--install",
            ]
        )
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=chart_version,
            message="Helm release recovered from snapshot",
        )

    async def recover_from_snapshot(
        self, target_ref: dict[str, Any], snapshot: dict[str, Any]
    ) -> RuntimeUpdateResult:
        if target_ref.get("mode") == "helm_release":
            return await self.recover_helm_release_from_snapshot(target_ref, snapshot)
        return await super().recover_from_snapshot(target_ref, snapshot)

    def _get_apps_api(self):
        if self._apps_api is None:
            self._apps_api = self._create_apps_api()
        return self._apps_api

    def _get_namespaces(self) -> list[str]:
        namespaces = self.runtime_connection.config.get("namespaces")
        if isinstance(namespaces, list):
            normalized = [
                namespace.strip()
                for namespace in namespaces
                if isinstance(namespace, str) and namespace.strip()
            ]
            if normalized:
                return normalized

        namespace = self.runtime_connection.config.get("namespace")
        if isinstance(namespace, str) and namespace.strip():
            return [namespace.strip()]
        return []

    def _resolve_discovery_namespaces(self, namespace: str | None) -> list[str]:
        configured_namespaces = self._get_namespaces()
        requested_namespace = namespace.strip() if isinstance(namespace, str) else ""

        if requested_namespace:
            if requested_namespace not in configured_namespaces:
                raise ValueError("namespace is not configured for this runtime connection")
            return [requested_namespace]

        if len(configured_namespaces) == 1:
            return configured_namespaces

        if not configured_namespaces:
            raise ValueError("no namespace is configured for this runtime connection")

        raise ValueError("namespace is required when multiple namespaces are configured")

    def _authorize_namespace(self, namespace: str) -> None:
        configured_namespaces = self._get_namespaces()
        if not configured_namespaces:
            raise ValueError("no namespace is configured for this runtime connection")
        if namespace not in configured_namespaces:
            raise ValueError("namespace is not configured for this runtime connection")

    @staticmethod
    def _require_workload_target_mode(target_ref: dict[str, Any]) -> None:
        if target_ref.get("mode") != "kubernetes_workload":
            raise ValueError("kubernetes runtime requires target_ref.mode 'kubernetes_workload'")

    @staticmethod
    def _require_helm_release_target_mode(target_ref: dict[str, Any]) -> None:
        if target_ref.get("mode") != "helm_release":
            raise ValueError("kubernetes runtime requires target_ref.mode 'helm_release'")

    @staticmethod
    def _optional_target_field(target_ref: dict[str, Any], field: str) -> str | None:
        value = target_ref.get(field)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _validate_workload_kind(kind: str) -> None:
        if kind not in {"Deployment", "StatefulSet", "DaemonSet"}:
            raise ValueError("target_ref.kind must be Deployment, StatefulSet, or DaemonSet")

    def _get_core_api(self):
        if self._core_api is None:
            self._core_api = self._create_core_api()
        return self._core_api

    def _load_kubernetes_modules(self):
        try:
            client = importlib.import_module("kubernetes.client")
            config = importlib.import_module("kubernetes.config")
        except ImportError as exc:
            raise RuntimeError(
                "Missing Python dependency 'kubernetes' required by KubernetesRuntimeAdapter"
            ) from exc
        return client, config

    def _configure_kubernetes_client(self, config_module) -> None:
        if self.runtime_connection.config.get("in_cluster"):
            config_module.load_incluster_config()
            return

        kubeconfig = self.runtime_connection.secrets.get("kubeconfig")
        context = self.runtime_connection.config.get("context")
        if kubeconfig:
            config_module.load_kube_config_from_dict(
                self._parse_kubeconfig(kubeconfig), context=context
            )
        else:
            config_module.load_kube_config(context=context)

    def _create_apps_api(self):
        client, config = self._load_kubernetes_modules()
        self._configure_kubernetes_client(config)
        return client.AppsV1Api()

    def _create_core_api(self):
        client, config = self._load_kubernetes_modules()
        self._configure_kubernetes_client(config)
        return client.CoreV1Api()

    def _parse_kubeconfig(self, kubeconfig: str) -> dict[str, Any]:
        import yaml

        data = yaml.safe_load(kubeconfig)
        if not isinstance(data, dict):
            raise ValueError("Invalid kubeconfig content")
        return data

    def _get_helm_release(self, target_ref: dict[str, Any]) -> dict[str, Any]:
        self._require_helm_release_target_mode(target_ref)
        namespace = self._require_target_field(target_ref, "namespace")
        self._authorize_namespace(namespace)
        release_name = self._require_target_field(target_ref, "release_name")
        output = self._run_helm_command(
            ["status", release_name, "--namespace", namespace, "--output", "json"]
        )
        try:
            release = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ValueError("Helm release response is not valid JSON") from exc
        if not isinstance(release, dict) or not release:
            raise ValueError("Helm release response must be a non-empty object")
        return release

    def _run_helm_command(self, args: list[str]) -> str:
        command = ["helm", *args]
        env = os.environ.copy()
        with self._temporary_kubeconfig_path() as kubeconfig_path:
            if kubeconfig_path:
                env["KUBECONFIG"] = kubeconfig_path
            try:
                completed = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "Missing Helm binary required by KubernetesRuntimeAdapter"
                ) from exc
            except subprocess.CalledProcessError as exc:
                message = (exc.stderr or exc.stdout or str(exc)).strip()
                raise RuntimeError(message or "Helm command failed") from exc
        return completed.stdout

    @staticmethod
    def _parse_helm_release_chart(release: dict[str, Any]) -> tuple[str | None, str | None]:
        chart = release.get("chart")
        if isinstance(chart, dict):
            metadata = chart.get("metadata")
            if isinstance(metadata, dict):
                chart_name = metadata.get("name")
                chart_version = metadata.get("version")
                return (
                    (
                        chart_name.strip()
                        if isinstance(chart_name, str) and chart_name.strip()
                        else None
                    ),
                    (
                        chart_version.strip()
                        if isinstance(chart_version, str) and chart_version.strip()
                        else None
                    ),
                )
        chart_value = release.get("chart")
        if isinstance(chart_value, str) and chart_value.strip() and "-" in chart_value:
            chart_name, _, chart_version = chart_value.strip().rpartition("-")
            if chart_name and chart_version:
                return chart_name, chart_version
        return None, None

    @staticmethod
    def _parse_helm_release_app_version(release: dict[str, Any]) -> str | None:
        for key in ("appVersion", "app_version"):
            value = release.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        chart = release.get("chart")
        if isinstance(chart, dict):
            for key in ("appVersion", "app_version"):
                value = chart.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

            metadata = chart.get("metadata")
            if isinstance(metadata, dict):
                for key in ("appVersion", "app_version"):
                    app_version = metadata.get(key)
                    if isinstance(app_version, str) and app_version.strip():
                        return app_version.strip()
        return None

    @contextmanager
    def _temporary_kubeconfig_path(self):
        kubeconfig = self.runtime_connection.secrets.get("kubeconfig")
        if not isinstance(kubeconfig, str) or not kubeconfig.strip():
            yield None
            return

        temp_file = tempfile.NamedTemporaryFile("w", delete=False)
        try:
            temp_file.write(kubeconfig)
            temp_file.flush()
            temp_file.close()
            yield temp_file.name
        finally:
            try:
                os.unlink(temp_file.name)
            except FileNotFoundError:
                pass

    def _list_workloads(self, apps_api, namespace: str) -> list[dict[str, Any]]:
        workloads: list[dict[str, Any]] = []

        for deployment in apps_api.list_namespaced_deployment(namespace).items:
            workloads.append(self._workload_from_obj("Deployment", deployment))
        for statefulset in apps_api.list_namespaced_stateful_set(namespace).items:
            workloads.append(self._workload_from_obj("StatefulSet", statefulset))
        for daemonset in apps_api.list_namespaced_daemon_set(namespace).items:
            workloads.append(self._workload_from_obj("DaemonSet", daemonset))

        return workloads

    def _workload_from_obj(self, kind: str, obj) -> dict[str, Any]:
        containers = obj.spec.template.spec.containers if obj.spec and obj.spec.template else []
        labels = getattr(obj.metadata, "labels", None) or {}
        annotations = getattr(obj.metadata, "annotations", None) or {}
        return {
            "kind": kind,
            "name": obj.metadata.name,
            "labels": labels if isinstance(labels, dict) else {},
            "annotations": annotations if isinstance(annotations, dict) else {},
            "containers": [
                {"name": container.name, "image": container.image} for container in containers or []
            ],
        }

    def _get_workload(self, kind: str, name: str, namespace: str) -> dict[str, Any]:
        apps_api = self._get_apps_api()
        if kind == "Deployment":
            workload = apps_api.read_namespaced_deployment(name, namespace)
        elif kind == "StatefulSet":
            workload = apps_api.read_namespaced_stateful_set(name, namespace)
        elif kind == "DaemonSet":
            workload = apps_api.read_namespaced_daemon_set(name, namespace)
        else:
            raise ValueError("Unsupported Kubernetes workload kind")
        return self._workload_from_obj(kind, workload)

    def _group_helm_release_workloads(
        self,
        workloads: list[dict[str, Any]],
        namespace: str,
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for workload in workloads:
            release_name = self._get_helm_release_name(workload)
            if not release_name:
                continue
            release_namespace = self._get_helm_release_namespace(workload) or namespace
            grouped.setdefault((release_namespace, release_name), []).append(workload)
        return grouped

    def _build_helm_release_target(
        self,
        discovery_namespace: str,
        workloads: list[dict[str, Any]],
    ) -> RuntimeTarget:
        first_workload = workloads[0]
        release_name = self._get_helm_release_name(first_workload) or "-"
        release_namespace = self._get_helm_release_namespace(first_workload) or discovery_namespace
        release_metadata = self._get_helm_release_chart_metadata(release_namespace, release_name)
        chart_name = self._get_helm_chart_name(first_workload) or release_metadata.get("chart_name")
        chart_version = self._get_helm_chart_version(first_workload) or release_metadata.get(
            "chart_version"
        )
        app_version = next(
            (
                value
                for workload in workloads
                if (value := self._get_label(workload, "app.kubernetes.io/version"))
            ),
            None,
        ) or release_metadata.get("app_version")
        workload_refs = [
            {"kind": workload["kind"], "name": workload["name"]}
            for workload in sorted(workloads, key=lambda item: (item["kind"], item["name"]))
        ]
        target_ref: dict[str, Any] = {
            "mode": "helm_release",
            "namespace": release_namespace,
            "release_name": release_name,
            "workloads": workload_refs,
            "service_count": len(workload_refs),
        }
        if chart_name:
            target_ref["chart_name"] = chart_name
        if chart_version:
            target_ref["chart_version"] = chart_version
        if app_version:
            target_ref["app_version"] = app_version
        return RuntimeTarget(
            runtime_type="kubernetes",
            name=f"helm/{release_name}",
            target_ref=target_ref,
            image=None,
        )

    def _get_helm_release_chart_metadata(self, namespace: str, release_name: str) -> dict[str, str]:
        metadata: dict[str, str] = {}
        try:
            release = self._get_helm_release(
                {
                    "mode": "helm_release",
                    "namespace": namespace,
                    "release_name": release_name,
                }
            )
        except Exception as exc:
            self._log_optional_helm_metadata_failure("status", namespace, release_name, exc)
            release = None

        if release:
            chart_name, chart_version = self._parse_helm_release_chart(release)
            app_version = self._parse_helm_release_app_version(release)
            if chart_name:
                metadata["chart_name"] = chart_name
            if chart_version:
                metadata["chart_version"] = chart_version
            if app_version:
                metadata["app_version"] = app_version

        if "app_version" not in metadata:
            metadata.update(
                {
                    key: value
                    for key, value in self._get_helm_release_list_metadata(
                        namespace, release_name
                    ).items()
                    if key not in metadata
                }
            )
        return metadata

    def _get_helm_release_list_metadata(self, namespace: str, release_name: str) -> dict[str, str]:
        try:
            output = self._run_helm_command(
                [
                    "list",
                    "--namespace",
                    namespace,
                    "--filter",
                    f"^{release_name}$",
                    "--output",
                    "json",
                ]
            )
            releases = json.loads(output)
        except Exception as exc:
            self._log_optional_helm_metadata_failure("list", namespace, release_name, exc)
            return {}
        if not isinstance(releases, list):
            logger.debug(
                "Optional Helm list metadata for release %s in namespace %s returned %s instead of list",
                release_name,
                namespace,
                type(releases).__name__,
            )
            return {}

        release = next(
            (
                item
                for item in releases
                if isinstance(item, dict) and item.get("name") == release_name
            ),
            None,
        )
        if not release:
            return {}

        chart_name, chart_version = self._parse_helm_release_chart(release)
        app_version = self._parse_helm_release_app_version(release)
        metadata: dict[str, str] = {}
        if chart_name:
            metadata["chart_name"] = chart_name
        if chart_version:
            metadata["chart_version"] = chart_version
        if app_version:
            metadata["app_version"] = app_version
        return metadata

    @staticmethod
    def _log_optional_helm_metadata_failure(
        command: str,
        namespace: str,
        release_name: str,
        exc: Exception,
    ) -> None:
        logger.warning(
            "Optional Helm %s metadata unavailable for release %s in namespace %s: %s",
            command,
            release_name,
            namespace,
            exc,
        )

    @staticmethod
    def _get_helm_release_name(workload: dict[str, Any]) -> str | None:
        labels = workload.get("labels") or {}
        annotations = workload.get("annotations") or {}
        managed_by = labels.get("app.kubernetes.io/managed-by")
        release_name = annotations.get("meta.helm.sh/release-name")
        if managed_by == "Helm" and isinstance(release_name, str) and release_name.strip():
            return release_name.strip()
        return None

    @staticmethod
    def _get_helm_release_namespace(workload: dict[str, Any]) -> str | None:
        annotations = workload.get("annotations") or {}
        namespace = annotations.get("meta.helm.sh/release-namespace")
        return namespace.strip() if isinstance(namespace, str) and namespace.strip() else None

    @staticmethod
    def _get_label(workload: dict[str, Any], key: str) -> str | None:
        labels = workload.get("labels") or {}
        value = labels.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _get_helm_chart_name(self, workload: dict[str, Any]) -> str | None:
        chart = self._get_label(workload, "helm.sh/chart")
        if not chart:
            return None
        if "-" not in chart:
            return chart
        chart_name, _, chart_version = chart.rpartition("-")
        return chart_name if chart_name and chart_version else chart

    def _get_helm_chart_version(self, workload: dict[str, Any]) -> str | None:
        chart = self._get_label(workload, "helm.sh/chart")
        if not chart or "-" not in chart:
            return None
        chart_name, _, chart_version = chart.rpartition("-")
        return chart_version if chart_name and chart_version else None

    def _workload_containers(self, workload: dict[str, Any]) -> list[dict[str, str]]:
        containers = workload.get("containers") or []
        return [
            container
            for container in containers
            if isinstance(container.get("name"), str)
            and container["name"].strip()
            and isinstance(container.get("image"), str)
            and container["image"].strip()
        ]

    def _container_by_name(
        self, workload: dict[str, Any], required_name: str
    ) -> dict[str, str] | None:
        for container in self._workload_containers(workload):
            if container.get("name") == required_name:
                return container
        return None
