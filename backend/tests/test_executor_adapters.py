from collections.abc import Callable
from json import JSONDecodeError
import json
import logging

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.base import RuntimeMutationError
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter
from releasetracker.executors.compose_runtime_update import GroupedRuntimeRecreateSpec
from releasetracker.executors.podman import PodmanRuntimeAdapter


class FakeImage:
    def __init__(self, tags=None, image_id=None):
        self.tags = tags or []
        self.id = image_id


class FakeContainer:
    def __init__(
        self,
        container_id: str,
        name: str,
        image: FakeImage,
        attrs: dict | None = None,
        event_log: list[str] | None = None,
    ):
        self.id = container_id
        self.name = name
        self.image = image
        self.attrs = attrs or {}
        self._event_log = event_log
        self._on_remove: Callable[[], None] | None = None
        self.update_image_calls = []
        self.start_calls = []
        self.stop_calls = []
        self.remove_calls = []
        self.rename_calls = []

    def update_image(self, new_image: str) -> None:
        self.update_image_calls.append(new_image)

    def start(self) -> None:
        self.start_calls.append(True)
        if self._event_log is not None:
            self._event_log.append(f"start:{self.name}")

    def stop(self) -> None:
        self.stop_calls.append(True)
        if self._event_log is not None:
            self._event_log.append(f"stop:{self.name}")

    def remove(self) -> None:
        self.remove_calls.append(True)
        if self._event_log is not None:
            self._event_log.append(f"remove:{self.name}")
        if self._on_remove is not None:
            self._on_remove()

    def rename(self, name: str) -> None:
        self.rename_calls.append(name)
        if self._event_log is not None:
            self._event_log.append(f"rename:{self.name}->{name}")
        self.name = name


class FakePodmanStopDecodeErrorContainer(FakeContainer):
    def stop(self) -> None:
        super().stop()
        self.attrs.setdefault("State", {})["Running"] = False
        raise JSONDecodeError("Expecting value", "", 0)


class FakePodmanRemoveDecodeErrorContainer(FakeContainer):
    def remove(self) -> None:
        super().remove()
        raise JSONDecodeError("Expecting value", "", 0)


class FakeContainerManager:
    def __init__(self, containers):
        self._containers = list(containers)
        self.event_log: list[str] = []
        self.create_calls: list[dict] = []
        self.low_level_create_calls: list[dict] = []
        self._created: list[FakeContainer] = []
        self.fail_start_for_images: set[str] = set()
        for container in self._containers:
            container._event_log = self.event_log
            container._on_remove = lambda container=container: self._forget_container(container)

    def list(self, all=True):
        return list(self._containers)

    def get(self, identifier: str):
        for container in self._containers:
            if container.id == identifier or container.name == identifier:
                return container
        raise KeyError("container not found")

    def _forget_container(self, container: FakeContainer) -> None:
        try:
            self._containers.remove(container)
        except ValueError:
            pass

    def create(self, **kwargs) -> FakeContainer:
        self.create_calls.append(kwargs)
        host_config = {
            "PortBindings": kwargs.get("ports"),
            "Binds": kwargs.get("volumes"),
            "RestartPolicy": kwargs.get("restart_policy"),
            "NetworkMode": kwargs.get("network_mode"),
            "Init": kwargs.get("init"),
        }
        normalized_mounts = _fake_docker_inspect_mounts(kwargs.get("mounts"))
        return self._create_from_docker_payload(
            kwargs,
            container_id="docker-new-id",
            host_config=host_config,
            normalized_mounts=normalized_mounts,
        )

    def create_from_low_level(self, **kwargs) -> FakeContainer:
        self.low_level_create_calls.append(kwargs)
        host_config = kwargs.get("host_config")
        if not isinstance(host_config, dict):
            host_config = {}
        normalized_mounts = _fake_docker_inspect_mounts(host_config.get("Mounts"))
        return self._create_from_docker_payload(
            kwargs,
            container_id="docker-low-level-id",
            host_config=dict(host_config),
            normalized_mounts=normalized_mounts,
            config_ports=kwargs.get("ports"),
        )

    def _create_from_docker_payload(
        self,
        kwargs: dict,
        *,
        container_id: str,
        host_config: dict,
        normalized_mounts: list[dict],
        config_ports=None,
    ) -> FakeContainer:
        self.event_log.append(f"create:{kwargs.get('name', 'recreated')}")
        config = {
            "Env": kwargs.get("environment"),
            "Entrypoint": kwargs.get("entrypoint"),
            "Cmd": kwargs.get("command"),
            "Labels": kwargs.get("labels"),
            "Healthcheck": kwargs.get("healthcheck"),
            "Domainname": kwargs.get("domainname"),
        }
        exposed_ports = _fake_docker_exposed_ports(config_ports)
        if exposed_ports:
            config["ExposedPorts"] = exposed_ports
        if normalized_mounts:
            host_config["Mounts"] = normalized_mounts
        created = FakeContainer(
            container_id=container_id,
            name=kwargs.get("name", "recreated"),
            image=FakeImage(tags=[kwargs["image"]]),
            attrs={
                "Config": config,
                "HostConfig": host_config,
                "Mounts": _fake_docker_runtime_mounts(normalized_mounts),
            },
            event_log=self.event_log,
        )
        if kwargs["image"] in self.fail_start_for_images:

            def fail_start() -> None:
                created.start_calls.append(True)
                self.event_log.append(f"start:{created.name}")
                raise RuntimeError(f"start failed: {kwargs['image']}")

            created.start = fail_start
        self._created.append(created)
        self._containers.append(created)
        created._on_remove = lambda: self._forget_container(created)
        return created


def _fake_docker_exposed_ports(raw_ports) -> dict[str, dict]:
    if not isinstance(raw_ports, list):
        return {}
    exposed_ports: dict[str, dict] = {}
    for raw_port in raw_ports:
        if not isinstance(raw_port, tuple) or len(raw_port) != 2:
            continue
        port, protocol = raw_port
        exposed_ports[f"{port}/{protocol}"] = {}
    return exposed_ports


def _fake_docker_inspect_mounts(raw_mounts) -> list[dict]:
    if not isinstance(raw_mounts, list):
        return []
    normalized_mounts: list[dict] = []
    for mount in raw_mounts:
        if not isinstance(mount, dict):
            continue
        target = mount.get("Target") or mount.get("target") or mount.get("Destination")
        mount_type = mount.get("Type") or mount.get("type")
        if not isinstance(target, str) or not target.strip():
            continue
        if not isinstance(mount_type, str) or not mount_type.strip():
            continue
        normalized = {"Target": target, "Type": mount_type}
        if "Source" in mount or "source" in mount:
            normalized["Source"] = mount.get("Source") if "Source" in mount else mount.get("source")
        read_only = mount.get("ReadOnly") if "ReadOnly" in mount else mount.get("read_only")
        if isinstance(read_only, bool):
            normalized["ReadOnly"] = read_only
        consistency = (
            mount.get("Consistency") if "Consistency" in mount else mount.get("consistency")
        )
        if isinstance(consistency, str) and consistency.strip():
            normalized["Consistency"] = consistency
        for source_key, target_key in (
            ("BindOptions", "BindOptions"),
            ("bind_options", "BindOptions"),
            ("VolumeOptions", "VolumeOptions"),
            ("volume_options", "VolumeOptions"),
            ("TmpfsOptions", "TmpfsOptions"),
            ("tmpfs_options", "TmpfsOptions"),
        ):
            value = mount.get(source_key)
            if isinstance(value, dict):
                normalized[target_key] = dict(value)
        tmpfs_options = normalized.get("TmpfsOptions")
        if not isinstance(tmpfs_options, dict):
            tmpfs_options = {}
        tmpfs_size = mount.get("tmpfs_size")
        if isinstance(tmpfs_size, int) and tmpfs_size > 0:
            tmpfs_options["SizeBytes"] = tmpfs_size
        tmpfs_mode = mount.get("tmpfs_mode")
        if isinstance(tmpfs_mode, int) and tmpfs_mode >= 0:
            tmpfs_options["Mode"] = tmpfs_mode
        if tmpfs_options:
            normalized["TmpfsOptions"] = tmpfs_options
        normalized_mounts.append(normalized)
    return normalized_mounts


def _fake_docker_runtime_mounts(host_mounts: list[dict]) -> list[dict]:
    runtime_mounts: list[dict] = []
    for mount in host_mounts:
        target = mount.get("Target")
        mount_type = mount.get("Type")
        if not isinstance(target, str) or not isinstance(mount_type, str):
            continue
        runtime_mount = {"Destination": target, "Type": mount_type}
        if "Source" in mount:
            runtime_mount["Source"] = mount.get("Source")
        read_only = mount.get("ReadOnly")
        runtime_mount["RW"] = not read_only if isinstance(read_only, bool) else True
        tmpfs_options = mount.get("TmpfsOptions")
        if isinstance(tmpfs_options, dict):
            runtime_mount["TmpfsOptions"] = dict(tmpfs_options)
        runtime_mounts.append(runtime_mount)
    return runtime_mounts


class FakeContainerClient:
    def __init__(self, containers):
        self.containers = FakeContainerManager(containers)
        self.update_container_image_calls = []

    def update_container_image(self, container_id: str, new_image: str) -> None:
        self.update_container_image_calls.append((container_id, new_image))


class FakeDockerImageManager:
    def __init__(self, should_fail: bool = False):
        self.pull_calls: list[str] = []
        self._should_fail = should_fail

    def pull(self, image: str) -> None:
        self.pull_calls.append(image)
        if self._should_fail:
            raise RuntimeError(f"pull failed: {image}")


class FakeDockerLowLevelApi:
    def __init__(self, container_manager: FakeContainerManager):
        self._container_manager = container_manager
        self._version = "1.45"

    def create_container(self, **kwargs) -> dict[str, str]:
        created = self._container_manager.create_from_low_level(**kwargs)
        return {"Id": created.id}


class FakeDockerRecreateClient:
    def __init__(
        self,
        containers,
        *,
        pull_should_fail: bool = False,
        network_names=None,
        low_level_api: bool = False,
    ):
        self.containers = FakeContainerManager(containers)
        self.images = FakeDockerImageManager(should_fail=pull_should_fail)
        self.networks = FakeDockerNetworkManager(network_names or [])
        if low_level_api:
            self.api = FakeDockerLowLevelApi(self.containers)


class FakeDockerNetwork:
    def __init__(self, name: str, connect_calls: list[tuple], disconnect_calls: list[tuple]):
        self.name = name
        self._connect_calls = connect_calls
        self._disconnect_calls = disconnect_calls

    def connect(self, container, **kwargs) -> None:
        self._connect_calls.append((self.name, container.name, kwargs))

    def disconnect(self, container, force=False) -> None:
        self._disconnect_calls.append((self.name, container.name, force))


class FakeDockerNetworkManager:
    def __init__(self, network_names):
        self.connect_calls: list[tuple] = []
        self.disconnect_calls: list[tuple] = []
        self._networks = {
            name: FakeDockerNetwork(name, self.connect_calls, self.disconnect_calls)
            for name in network_names
        }

    def get(self, name: str) -> FakeDockerNetwork:
        if name not in self._networks:
            self._networks[name] = FakeDockerNetwork(
                name, self.connect_calls, self.disconnect_calls
            )
        return self._networks[name]


class FakePodmanNetwork:
    def __init__(self, name: str, connect_calls: list[tuple], disconnect_calls: list[tuple]):
        self.name = name
        self._connect_calls = connect_calls
        self._disconnect_calls = disconnect_calls

    def connect(self, container, **kwargs) -> None:
        self._connect_calls.append((self.name, container.name, kwargs))

    def disconnect(self, container, force=False) -> None:
        self._disconnect_calls.append((self.name, container.name, force))


class FakePodmanNetworkManager:
    def __init__(self, network_names):
        self.connect_calls: list[tuple] = []
        self.disconnect_calls: list[tuple] = []
        self._networks = {
            name: FakePodmanNetwork(name, self.connect_calls, self.disconnect_calls)
            for name in network_names
        }

    def get(self, name: str):
        if name not in self._networks:
            self._networks[name] = FakePodmanNetwork(
                name, self.connect_calls, self.disconnect_calls
            )
        return self._networks[name]


class FakePodmanPod:
    def __init__(self, name: str, pod_id: str | None = None, attrs: dict | None = None):
        self.id = pod_id or name
        self.name = name
        self.attrs = attrs or {}

    def __eq__(self, other) -> bool:
        if isinstance(other, str):
            return self.name == other or self.id == other
        return super().__eq__(other)


class FakePodmanPodManager:
    def __init__(self):
        self.create_calls: list[dict] = []
        self.get_calls: list[str] = []
        self.remove_calls: list[tuple] = []
        self._pods: dict[str, FakePodmanPod] = {}
        self.create_returns_none = False
        self.next_create_pod_id: str | None = None

    def add(
        self,
        name: str,
        pod_id: str | None = None,
        attrs: dict | None = None,
    ) -> FakePodmanPod:
        pod = FakePodmanPod(name, pod_id=pod_id, attrs=attrs)
        self._pods[pod.name] = pod
        self._pods[pod.id] = pod
        return pod

    def create(self, name: str, **kwargs):
        payload = {"name": name, **kwargs}
        self.create_calls.append(payload)
        pod_id = self.next_create_pod_id or name
        self.next_create_pod_id = None
        pod = self.add(name, pod_id=pod_id)
        if self.create_returns_none:
            return None
        return pod

    def get(self, pod_id: str):
        self.get_calls.append(pod_id)
        pod = self._pods.get(pod_id)
        if pod is None:
            raise KeyError("pod not found")
        return pod

    def remove(self, pod_id: str, force: bool | None = None) -> None:
        self.remove_calls.append((pod_id, force))
        pod = self._pods.get(pod_id)
        if pod is None:
            self._pods.pop(pod_id, None)
            return
        self._pods.pop(pod.name, None)
        self._pods.pop(pod.id, None)


class FakePodmanImageManager:
    def __init__(self, should_fail: bool = False):
        self.pull_calls: list[str] = []
        self._should_fail = should_fail

    def pull(self, image: str) -> None:
        self.pull_calls.append(image)
        if self._should_fail:
            raise RuntimeError(f"pull failed: {image}")


class FakePodmanResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self, not_found=None) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakePodmanNotFoundError(Exception):
    def __init__(self, message: str = "404 Client Error: Not Found (Not Found)"):
        super().__init__(message)
        self.response = type("Response", (), {"status_code": 404})()


class FakePodmanServerError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.response = type("Response", (), {"status_code": 500})()


class FakePodmanApi:
    def __init__(self, container_manager, pod_manager=None):
        self._container_manager = container_manager
        self._pod_manager = pod_manager
        self.post_calls: list[dict] = []
        self.pod_create_posts: list[dict] = []
        self.network_connect_posts: list[dict] = []
        self.not_found_once_paths: set[str] = set()
        self.fail_empty_network_mode_for_attempts: list[str] = []
        self.reject_compatible_kwarg = False
        self.require_compatible_false = False
        self.reject_pod_id_only_create: set[str] = set()

    def post(self, path: str, data=None, headers=None, **kwargs):
        if self.reject_compatible_kwarg and "compatible" in kwargs:
            raise TypeError("post() got an unexpected keyword argument 'compatible'")
        if self.require_compatible_false and kwargs.get("compatible") is not False:
            raise AssertionError("pod-backed create must call libpod with compatible=False")
        payload = json.loads(data) if isinstance(data, str) else data
        if path in self.not_found_once_paths:
            self.not_found_once_paths.remove(path)
            raise FakePodmanNotFoundError()
        if isinstance(path, str) and path.startswith("networks/") and path.endswith("/connect"):
            if kwargs.get("compatible") is False:
                assert isinstance(payload, dict)
                assert "container" in payload
                assert "Container" not in payload
                assert "EndpointConfig" not in payload
                assert "IPAddress" not in payload
                assert "IPAMConfig" not in payload
            self.network_connect_posts.append(
                {"path": path, "data": payload, "headers": headers or {}, "kwargs": kwargs}
            )
            return FakePodmanResponse({})
        if path in {"/pods/create", "pods/create", "libpod/pods/create"}:
            assert isinstance(payload, dict)
            pod_name = payload.get("name")
            assert isinstance(pod_name, str) and pod_name.strip()
            self.pod_create_posts.append(
                {"path": path, "data": payload, "headers": headers or {}, "kwargs": kwargs}
            )
            pod_id = pod_name
            if self._pod_manager is not None:
                self._pod_manager.create_calls.append(dict(payload))
                pod_id = self._pod_manager.next_create_pod_id or pod_name
                self._pod_manager.next_create_pod_id = None
                self._pod_manager.add(pod_name, pod_id=pod_id)
            return FakePodmanResponse({"Id": pod_id})
        if path not in {"/containers/create", "containers/create", "libpod/containers/create"}:
            raise AssertionError(f"unexpected path: {path}")
        self.post_calls.append(
            {"path": path, "data": payload, "headers": headers or {}, "kwargs": kwargs}
        )
        restart_policy = payload.get("restart_policy") if isinstance(payload, dict) else None
        if isinstance(restart_policy, dict):
            raise FakePodmanServerError(
                "decode(): json: cannot unmarshal object into Go struct field "
                "SpecGenerator.ContainerBasicConfig.restart_policy of type string"
            )
        volumes = payload.get("volumes") if isinstance(payload, dict) else None
        if isinstance(volumes, dict):
            raise FakePodmanServerError(
                "decode(): json: cannot unmarshal object into Go struct field "
                "SpecGenerator.ContainerStorageConfig.volumes of type []*specgen.NamedVolume"
            )
        if _podman_payload_has_empty_network_mode(payload):
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error: invalid empty network mode"
            )
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("pod"), str)
            and payload["pod"] in self.reject_pod_id_only_create
        ):
            raise FakePodmanServerError(
                '500 Server Error: Internal Server Error ("" is not supported: invalid network mode)'
            )
        if _podman_payload_has_empty_work_dir(payload):
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error (container directory cannot be empty)"
            )
        if _podman_payload_has_empty_storage_destination(payload):
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error (container directory cannot be empty)"
            )
        if _podman_payload_has_invalid_named_volume(payload):
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error (container directory cannot be empty)"
            )
        if _podman_payload_has_legacy_named_volume_key_shape(payload):
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error (container directory cannot be empty)"
            )
        if _podman_payload_has_volume_mount(payload):
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error "
                "(crun: mount nginx-podman-cache to var/cache/nginx: No such device)"
            )
        if _podman_payload_has_wrong_storage_destination_key(payload):
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error (container directory cannot be empty)"
            )
        if _podman_payload_has_relative_storage_destination(payload):
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error "
                "(crun: mount nginx-podman-cache to var/cache/nginx: No such device)"
            )
        if self.fail_empty_network_mode_for_attempts:
            self.fail_empty_network_mode_for_attempts.pop(0)
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error: invalid empty network mode"
            )
        create_kwargs = dict(payload or {})
        self._container_manager.create_calls.append(create_kwargs)
        created_id = (
            "new-id"
            if not self._container_manager._created
            else f"new-id-{len(self._container_manager._created) + 1}"
        )
        created = self._container_manager._create_from_kwargs({**create_kwargs, "id": created_id})
        return FakePodmanResponse({"Id": created.id})


def _podman_payload_has_empty_network_mode(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    netns = payload.get("netns")
    return isinstance(netns, dict) and netns.get("nsmode") == ""


def _podman_payload_has_empty_work_dir(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    work_dir = payload.get("work_dir")
    return isinstance(work_dir, str) and not work_dir.strip()


PODMAN_TEST_MOUNT_DESTINATION_KEYS = (
    "destination",
    "target",
    "dest",
    "Destination",
    "Target",
    "Dest",
)
PODMAN_TEST_NAMED_VOLUME_DESTINATION_KEYS = (
    "Dest",
    "dest",
    "destination",
    "Destination",
    "Target",
    "target",
)


def _podman_payload_has_empty_storage_destination(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    mounts = payload.get("mounts")
    if isinstance(mounts, list):
        for mount in mounts:
            if not isinstance(mount, dict):
                continue
            if any(
                key in mount and isinstance(mount.get(key), str) and not mount.get(key).strip()
                for key in PODMAN_TEST_MOUNT_DESTINATION_KEYS
            ):
                return True
    volumes = payload.get("volumes")
    if isinstance(volumes, list):
        for volume in volumes:
            if not isinstance(volume, dict):
                continue
            if any(
                key in volume and isinstance(volume.get(key), str) and not volume.get(key).strip()
                for key in PODMAN_TEST_NAMED_VOLUME_DESTINATION_KEYS
            ):
                return True
    return False


def _podman_payload_has_invalid_named_volume(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    volumes = payload.get("volumes")
    if not isinstance(volumes, list):
        return False
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        name = volume.get("Name") or volume.get("name")
        if not isinstance(name, str) or not name.strip():
            return True
        destination = _podman_test_storage_destination_value(
            volume, PODMAN_TEST_NAMED_VOLUME_DESTINATION_KEYS
        )
        if not isinstance(destination, str) or not destination.strip():
            return True
    return False


def _podman_payload_has_legacy_named_volume_key_shape(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    volumes = payload.get("volumes")
    if not isinstance(volumes, list):
        return False
    return any(
        isinstance(volume, dict)
        and {"name", "dest"}.issubset(volume)
        and "Name" not in volume
        and "Dest" not in volume
        for volume in volumes
    )


def _podman_payload_has_volume_mount(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    mounts = payload.get("mounts")
    if not isinstance(mounts, list):
        return False
    return any(isinstance(mount, dict) and mount.get("type") == "volume" for mount in mounts)


def _podman_payload_has_wrong_storage_destination_key(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    mounts = payload.get("mounts")
    if not isinstance(mounts, list):
        return False
    return any(
        isinstance(mount, dict)
        and any(key in mount for key in ("target", "dest", "Destination", "Target", "Dest"))
        for mount in mounts
    )


def _podman_test_storage_destination_value(payload: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _podman_payload_has_relative_storage_destination(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    mounts = payload.get("mounts")
    if not isinstance(mounts, list):
        return False
    return any(
        isinstance(mount, dict)
        and isinstance(
            destination := _podman_test_storage_destination_value(
                mount, PODMAN_TEST_MOUNT_DESTINATION_KEYS
            ),
            str,
        )
        and not destination.strip().startswith("/")
        for mount in mounts
    )


class FakePodmanContainerManager:
    def __init__(self, containers):
        self._containers = list(containers)
        self.event_log: list[str] = []
        self.create_calls: list[dict] = []
        self._created: list[FakeContainer] = []
        self.fail_start_for_images: set[str] = set()
        self.inject_empty_network_mode_when_omitted = False
        for container in self._containers:
            container._event_log = self.event_log
            container._on_remove = lambda container=container: self._forget_container(container)

    def list(self, all=True):
        return list(self._containers)

    def get(self, identifier: str):
        for container in self._containers:
            if container.id == identifier or container.name == identifier:
                return container
        raise KeyError("container not found")

    def _forget_container(self, container: FakeContainer) -> None:
        try:
            self._containers.remove(container)
        except ValueError:
            pass

    def create(self, **kwargs) -> FakeContainer:
        if self.inject_empty_network_mode_when_omitted and "network_mode" not in kwargs:
            kwargs = {**kwargs, "network_mode": ""}
        self.create_calls.append(kwargs)
        if kwargs.get("network_mode") == "":
            raise FakePodmanServerError(
                "500 Server Error: Internal Server Error: invalid empty network mode"
            )
        return self._create_from_kwargs(kwargs)

    def _create_from_kwargs(self, kwargs: dict) -> FakeContainer:
        self.event_log.append(f"create:{kwargs.get('name', 'recreated')}")
        pod_ref = kwargs.get("pod", "")
        pod_name = getattr(pod_ref, "name", pod_ref)
        created = FakeContainer(
            container_id=kwargs.get("id")
            or ("new-id" if not self._created else f"new-id-{len(self._created) + 1}"),
            name=kwargs.get("name", "recreated"),
            image=FakeImage(tags=[kwargs["image"]]),
            attrs={
                "Pod": pod_name,
                "PodName": pod_name,
                "Config": {
                    "Env": kwargs.get("environment"),
                    "Entrypoint": kwargs.get("entrypoint"),
                    "Cmd": kwargs.get("command"),
                    "Labels": kwargs.get("labels"),
                    "Healthcheck": kwargs.get("healthcheck"),
                },
                "HostConfig": {
                    "PortBindings": kwargs.get("ports"),
                    "Binds": kwargs.get("volumes"),
                    "RestartPolicy": kwargs.get("restart_policy"),
                    "NetworkMode": kwargs.get("network_mode"),
                },
            },
            event_log=self.event_log,
        )
        if kwargs["image"] in self.fail_start_for_images:

            def fail_start() -> None:
                created.start_calls.append(True)
                self.event_log.append(f"start:{created.name}")
                raise RuntimeError(f"start failed: {kwargs['image']}")

            created.start = fail_start
        self._created.append(created)
        self._containers.append(created)
        created._on_remove = lambda: self._forget_container(created)
        return created


class FakePodmanClient:
    def __init__(
        self,
        containers,
        pull_should_fail: bool = False,
        network_names=None,
        raw_api_available: bool = True,
    ):
        self.containers = FakePodmanContainerManager(containers)
        self.pods = FakePodmanPodManager()
        if raw_api_available:
            self.api = FakePodmanApi(self.containers, self.pods)
        self.images = FakePodmanImageManager(should_fail=pull_should_fail)
        self.networks = FakePodmanNetworkManager(network_names or [])


def _assert_podman_pod_member_create_kwargs(create_kwargs: dict) -> None:
    assert create_kwargs.get("pod")
    for key in (
        "network",
        "network_mode",
        "network_options",
        "cni_networks",
        "networks",
        "netns",
    ):
        assert key not in create_kwargs
    for namespace_key in ("utsns", "ipcns", "pidns", "cgroupns", "userns"):
        namespace_payload = create_kwargs.get(namespace_key)
        if isinstance(namespace_payload, dict):
            assert namespace_payload.get("nsmode") != ""
        else:
            assert namespace_payload != ""


def _assert_no_blank_podman_create_network_values(create_kwargs: dict) -> None:
    assert create_kwargs.get("network_mode") != ""
    assert create_kwargs.get("network") != ""
    networks = create_kwargs.get("networks")
    if isinstance(networks, list):
        assert all(not isinstance(network, str) or network.strip() for network in networks)
    elif isinstance(networks, dict):
        assert all(not isinstance(network, str) or network.strip() for network in networks)
    for namespace_key in ("netns", "utsns", "ipcns", "pidns", "cgroupns", "userns"):
        namespace_payload = create_kwargs.get(namespace_key)
        if isinstance(namespace_payload, dict):
            assert namespace_payload.get("nsmode") != ""
        else:
            assert namespace_payload != ""


class FakeMetadata:
    def __init__(self, name: str, *, labels=None, annotations=None):
        self.name = name
        self.labels = labels or {}
        self.annotations = annotations or {}


class FakeContainerSpec:
    def __init__(self, name: str, image: str):
        self.name = name
        self.image = image


class FakePodSpec:
    def __init__(self, containers):
        self.containers = containers


class FakeTemplateSpec:
    def __init__(self, containers):
        self.spec = FakePodSpec(containers)


class FakeTemplate:
    def __init__(self, containers):
        self.spec = FakePodSpec(containers)


class FakeWorkloadSpec:
    def __init__(self, containers):
        self.template = FakeTemplate(containers)


class FakeWorkload:
    def __init__(self, name: str, containers, *, labels=None, annotations=None):
        self.metadata = FakeMetadata(name, labels=labels, annotations=annotations)
        self.spec = FakeWorkloadSpec(containers)


class FakeList:
    def __init__(self, items):
        self.items = items


class FakeAppsApi:
    def __init__(self, deployments, statefulsets, daemonsets):
        self._deployments = deployments
        self._statefulsets = statefulsets
        self._daemonsets = daemonsets
        self.patch_calls = []
        self.list_calls = []

    def list_namespaced_deployment(self, namespace):
        self.list_calls.append(("Deployment", namespace))
        return FakeList(self._deployments)

    def list_namespaced_stateful_set(self, namespace):
        self.list_calls.append(("StatefulSet", namespace))
        return FakeList(self._statefulsets)

    def list_namespaced_daemon_set(self, namespace):
        self.list_calls.append(("DaemonSet", namespace))
        return FakeList(self._daemonsets)

    def read_namespaced_deployment(self, name, namespace):
        return next(item for item in self._deployments if item.metadata.name == name)

    def read_namespaced_stateful_set(self, name, namespace):
        return next(item for item in self._statefulsets if item.metadata.name == name)

    def read_namespaced_daemon_set(self, name, namespace):
        return next(item for item in self._daemonsets if item.metadata.name == name)

    def patch_namespaced_deployment(self, name, namespace, body):
        self.patch_calls.append(("Deployment", name, namespace, body))

    def patch_namespaced_stateful_set(self, name, namespace, body):
        self.patch_calls.append(("StatefulSet", name, namespace, body))

    def patch_namespaced_daemon_set(self, name, namespace, body):
        self.patch_calls.append(("DaemonSet", name, namespace, body))


class FakeDockerModule:
    class tls:
        class TLSConfig:
            def __init__(self, client_cert=None, ca_cert=None, verify=None):
                self.client_cert = client_cert
                self.ca_cert = ca_cert
                self.verify = verify

    class DockerClient:
        def __init__(self, base_url=None, version=None, tls=None):
            self.base_url = base_url
            self.version = version
            self.tls = tls


class FakePodmanModule:
    class tls:
        class TLSConfig:
            def __init__(self, client_cert=None, ca_cert=None, verify=None):
                self.client_cert = client_cert
                self.ca_cert = ca_cert
                self.verify = verify

    class PodmanClient:
        def __init__(self, base_url=None, version=None, tls=None):
            self.base_url = base_url
            self.version = version
            self.tls = tls


class FakeKubeConfig:
    def __init__(self):
        self.calls = []

    def load_incluster_config(self):
        self.calls.append(("incluster", None))

    def load_kube_config_from_dict(self, config_dict, context=None):
        self.calls.append(("dict", context))

    def load_kube_config(self, context=None):
        self.calls.append(("file", context))


class FakeKubeClient:
    class AppsV1Api:
        def __init__(self):
            self.created = True


@pytest.mark.asyncio
async def test_docker_adapter_discovers_and_updates_image_only():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    container = FakeContainer("abc", "sample-web", FakeImage(tags=["sample-web:1.25"]))
    client = FakeContainerClient([container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    targets = await adapter.discover_targets()
    assert len(targets) == 1
    assert targets[0].target_ref["container_id"] == "abc"
    assert targets[0].target_ref["container_name"] == "sample-web"
    assert targets[0].image == "sample-web:1.25"

    current = await adapter.get_current_image({"container_id": "abc"})
    assert current == "sample-web:1.25"

    result = await adapter.update_image({"container_id": "abc"}, "sample-web:1.26")
    assert result.updated is True
    assert result.old_image == "sample-web:1.25"
    assert result.new_image == "sample-web:1.26"
    assert client.update_container_image_calls == [("abc", "sample-web:1.26")]
    assert container.update_image_calls == []

    snapshot = await adapter.capture_snapshot({"container_id": "abc"}, "sample-web:1.25")
    await adapter.validate_snapshot({"container_id": "abc"}, snapshot)
    assert snapshot == {
        "runtime_type": "docker",
        "container_id": "abc",
        "container_name": "sample-web",
        "image": "sample-web:1.25",
    }


@pytest.mark.asyncio
async def test_docker_adapter_discovers_compose_projects_as_grouped_targets():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    standalone = FakeContainer("standalone", "sample-web", FakeImage(tags=["sample-web:1.25"]))
    web = FakeContainer(
        "web-1",
        "release-stack-web-1",
        FakeImage(tags=["ghcr.io/acme/web:1.0"]),
        attrs={
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "release-stack",
                    "com.docker.compose.service": "web",
                    "com.docker.compose.project.working_dir": "/srv/release-stack",
                    "com.docker.compose.project.config_files": "compose.yaml",
                }
            }
        },
    )
    worker = FakeContainer(
        "worker-1",
        "release-stack-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:1.0"]),
        attrs={
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "release-stack",
                    "com.docker.compose.service": "worker",
                    "com.docker.compose.project.working_dir": "/srv/release-stack",
                    "com.docker.compose.project.config_files": "compose.yaml",
                }
            }
        },
    )
    client = FakeContainerClient([standalone, web, worker])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    targets = await adapter.discover_targets()

    assert [target.name for target in targets] == ["sample-web", "release-stack"]
    compose_target = targets[1]
    assert compose_target.runtime_type == "docker"
    assert compose_target.image is None
    assert compose_target.target_ref == {
        "mode": "docker_compose",
        "project": "release-stack",
        "working_dir": "/srv/release-stack",
        "config_files": ["compose.yaml"],
        "services": [
            {"service": "web", "replica_count": 1, "image": "ghcr.io/acme/web:1.0"},
            {"service": "worker", "replica_count": 1, "image": "ghcr.io/acme/worker:1.0"},
        ],
        "service_count": 2,
    }


@pytest.mark.asyncio
async def test_docker_adapter_recreates_container_when_image_only_update_is_unavailable():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    container = FakeContainer(
        "abc",
        "sample-web",
        FakeImage(tags=["sample-web:1.25"]),
        attrs={
            "Config": {
                "Env": ["FOO=bar"],
                "Cmd": ["sample-web", "-g", "daemon off;"],
                "Entrypoint": None,
                "Labels": {"app": "web"},
            },
            "HostConfig": {
                "PortBindings": {
                    "80/tcp": [
                        {"HostIp": "", "HostPort": "8080"},
                        {"HostIp": "127.0.0.1", "HostPort": "18080"},
                    ]
                },
                "Binds": ["/host/data:/data:ro"],
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {
                "Networks": {
                    "app-net": {
                        "Aliases": ["sample-web", "abc", "abc"[:12]],
                        "IPAMConfig": {"IPv4Address": "172.30.10.5"},
                    }
                }
            },
        },
    )
    client = FakeDockerRecreateClient([container], network_names=["app-net"])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    snapshot = await adapter.capture_snapshot({"container_name": "sample-web"}, "sample-web:1.25")
    await adapter.validate_snapshot({"container_name": "sample-web"}, snapshot)

    result = await adapter.update_image({"container_name": "sample-web"}, "sample-web:1.26")

    assert result.updated is True
    assert result.old_image == "sample-web:1.25"
    assert result.new_image == "sample-web:1.26"
    assert result.new_container_id == "docker-new-id"
    assert client.images.pull_calls == ["sample-web:1.26"]
    assert len(container.stop_calls) == 1
    assert len(container.remove_calls) == 1
    assert len(client.containers.create_calls) == 1
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["image"] == "sample-web:1.26"
    assert create_kwargs["name"] == "sample-web"
    assert create_kwargs["environment"] == ["FOO=bar"]
    assert create_kwargs["command"] == ["sample-web", "-g", "daemon off;"]
    assert create_kwargs["ports"] == {"80/tcp": [("", 8080), ("127.0.0.1", 18080)]}
    assert create_kwargs["volumes"] == {"/host/data": {"bind": "/data", "mode": "ro"}}
    assert create_kwargs["restart_policy"] == {"Name": "unless-stopped", "MaximumRetryCount": 0}
    assert create_kwargs["network_mode"] == "bridge"
    assert create_kwargs["labels"] == {"app": "web"}
    assert snapshot["network_config"] == {
        "network_mode": "bridge",
        "endpoints": {
            "app-net": {
                "Aliases": ["sample-web", "abc", "abc"[:12]],
                "IPAMConfig": {"IPv4Address": "172.30.10.5"},
            }
        },
    }
    assert client.networks.connect_calls == [
        ("app-net", "sample-web", {"aliases": ["sample-web"], "ipv4_address": "172.30.10.5"})
    ]
    recreated = client.containers._created[0]
    assert len(recreated.start_calls) == 1


@pytest.mark.asyncio
async def test_docker_adapter_recreate_omits_exposed_only_ports():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    container = FakeContainer(
        "abc",
        "sample-web",
        FakeImage(tags=["sample-web:1.25"]),
        attrs={
            "Config": {
                "Image": "sample-web:1.25",
                "Domainname": "local.test",
                "ExposedPorts": {"80/tcp": {}, "443/tcp": {}},
            },
            "HostConfig": {
                "PortBindings": {"443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8443"}]},
                "NetworkMode": "bridge",
                "Init": True,
            },
        },
    )
    client = FakeDockerRecreateClient([container], low_level_api=True)
    adapter = DockerRuntimeAdapter(runtime, client=client)

    snapshot = await adapter.capture_snapshot({"container_name": "sample-web"}, "sample-web:1.25")
    result = await adapter.update_image({"container_name": "sample-web"}, "sample-web:1.26")

    assert result.updated is True
    assert snapshot["create_config"]["ports"] == {"443/tcp": ("127.0.0.1", 8443)}
    assert snapshot["create_config"]["domainname"] == "local.test"
    assert snapshot["create_config"]["init"] is True
    assert snapshot["create_config"]["_releasetracker_exposed_ports"] == ["80/tcp"]
    assert client.containers.create_calls == []
    low_level_create = client.containers.low_level_create_calls[0]
    assert low_level_create["domainname"] == "local.test"
    assert low_level_create["host_config"]["Init"] is True
    assert low_level_create["ports"] == [("443", "tcp"), ("80", "tcp")]
    assert low_level_create["host_config"]["PortBindings"] == {
        "443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8443"}]
    }
    assert "80/tcp" not in low_level_create["host_config"]["PortBindings"]
    assert "stop_timeout" not in low_level_create
    recreated = client.containers.get("sample-web")
    assert recreated.attrs["Config"]["Domainname"] == "local.test"
    assert recreated.attrs["Config"]["ExposedPorts"] == {"443/tcp": {}, "80/tcp": {}}
    assert recreated.attrs["HostConfig"]["Init"] is True


@pytest.mark.asyncio
async def test_docker_adapter_requires_recreate_metadata_when_image_only_update_is_unavailable():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    container = FakeContainer("abc", "sample-web", FakeImage(tags=["sample-web:1.25"]), attrs={})
    client = FakeDockerRecreateClient([container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    with pytest.raises(
        ValueError, match="cannot recreate container .* without a restorable create configuration"
    ):
        await adapter.update_image({"container_id": "abc"}, "sample-web:1.26")

    snapshot = await adapter.capture_snapshot({"container_id": "abc"}, "sample-web:1.25")
    with pytest.raises(ValueError, match="snapshot.create_config must be a non-empty dict"):
        await adapter.validate_snapshot({"container_id": "abc"}, snapshot)


@pytest.mark.asyncio
async def test_docker_adapter_update_cleans_up_failed_replacement_container():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    container = FakeContainer(
        "abc",
        "sample-web",
        FakeImage(tags=["sample-web:1.25"]),
        attrs={
            "Config": {"Image": "sample-web:1.25"},
            "HostConfig": {"NetworkMode": "bridge"},
        },
    )
    client = FakeDockerRecreateClient([container])
    client.containers.fail_start_for_images.add("sample-web:1.26")
    adapter = DockerRuntimeAdapter(runtime, client=client)

    with pytest.raises(
        RuntimeMutationError, match="docker update failed after destructive steps began"
    ):
        await adapter.update_image({"container_name": "sample-web"}, "sample-web:1.26")

    assert len(client.containers._created) == 1
    assert len(client.containers._created[0].remove_calls) == 1


@pytest.mark.asyncio
async def test_podman_adapter_recreates_container_with_new_image():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "def",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={
            "Pod": "",
            "Config": {
                "Env": ["FOO=bar"],
                "Cmd": ["sample-cache-server"],
                "Entrypoint": None,
                "Labels": {"app": "cache"},
            },
            "HostConfig": {
                "PortBindings": {"6379/tcp": [{"HostIp": "", "HostPort": "6379"}]},
                "Binds": ["/data:/data"],
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    snapshot = await adapter.capture_snapshot(
        {"container_name": "sample-cache"}, "sample-cache:7.2"
    )
    await adapter.validate_snapshot({"container_name": "sample-cache"}, snapshot)
    assert snapshot["container_id"] == "def"
    assert snapshot["container_name"] == "sample-cache"
    assert snapshot["image"] == "sample-cache:7.2"
    assert snapshot["create_config"]["image"] == "sample-cache:7.2"

    result = await adapter.update_image({"container_name": "sample-cache"}, "sample-cache:7.4")

    assert result.updated is True
    assert result.old_image == "sample-cache:7.2"
    assert result.new_image == "sample-cache:7.4"
    assert client.images.pull_calls == ["sample-cache:7.4"]
    assert len(container.stop_calls) == 1
    assert len(container.remove_calls) == 1
    assert len(client.api.post_calls) == 1
    assert len(client.containers.create_calls) == 1
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["image"] == "sample-cache:7.4"
    assert create_kwargs["env"] == {"FOO": "bar"}
    assert create_kwargs["command"] == ["sample-cache-server"]
    assert create_kwargs["portmappings"] == [
        {"container_port": 6379, "host_port": 6379, "protocol": "tcp"}
    ]
    assert create_kwargs["mounts"] == [
        {
            "type": "bind",
            "source": "/data",
            "destination": "/data",
        }
    ]
    assert create_kwargs["restart_policy"] == "unless-stopped"
    assert create_kwargs["restart_tries"] == 0
    assert create_kwargs["netns"] == {"nsmode": "bridge"}
    assert "network_mode" not in create_kwargs
    assert create_kwargs["labels"] == {"app": "cache"}
    assert create_kwargs.get("entrypoint") is None
    recreated = client.containers._created[0]
    assert len(recreated.stop_calls) == 0
    assert recreated.image.tags == ["sample-cache:7.4"]


@pytest.mark.asyncio
async def test_podman_adapter_recreates_container_restoring_networks_with_raw_api(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "def1234567890",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={
            "Pod": "",
            "Config": {"Image": "sample-cache:7.2"},
            "HostConfig": {"NetworkMode": "sample-net"},
            "NetworkSettings": {
                "Networks": {
                    "sample-net": {
                        "Aliases": [
                            "sample-cache.local",
                            "sample-cache",
                            "def1234567890",
                            "def123456789",
                        ],
                        "IPAddress": "10.89.20.3",
                        "GlobalIPv6Address": "fd00::10",
                        "IPAMConfig": {
                            "IPv4Address": "10.89.20.3",
                            "IPv6Address": "fd00::10",
                        },
                    }
                }
            },
        },
    )
    client = FakePodmanClient([container], network_names=["sample-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter.update_image({"container_name": "sample-cache"}, "sample-cache:7.4")

    assert result.updated is True
    assert client.networks.disconnect_calls == [
        ("sample-net", "sample-cache", True),
        ("podman", "sample-cache", True),
    ]
    assert client.networks.connect_calls == []
    assert len(client.api.network_connect_posts) == 1
    connect_post = client.api.network_connect_posts[0]
    assert connect_post["path"] == "networks/sample-net/connect"
    assert connect_post["kwargs"] == {"compatible": False}
    assert connect_post["headers"] == {"Content-Type": "application/json"}
    assert connect_post["data"] == {
        "container": "new-id",
        "aliases": ["sample-cache.local", "sample-cache"],
        "static_ips": ["10.89.20.3", "fd00::10"],
    }
    assert "endpoint_config_keys=" not in caplog.text
    assert "sample-cache.local" not in caplog.text
    assert "10.89.20.3" not in caplog.text
    assert "fd00::10" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_recreates_container_falls_back_to_sdk_network_connect():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "old-id",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={
            "Pod": "",
            "Config": {"Image": "sample-cache:7.2"},
            "HostConfig": {"NetworkMode": "sample-net"},
            "NetworkSettings": {
                "Networks": {
                    "sample-net": {
                        "Aliases": ["sample-cache.local", "old-id"],
                        "IPAMConfig": {"IPv4Address": "10.89.20.3"},
                    }
                }
            },
        },
    )
    client = FakePodmanClient(
        [container], network_names=["sample-net", "podman"], raw_api_available=False
    )
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image({"container_name": "sample-cache"}, "sample-cache:7.4")

    assert result.updated is True
    assert client.networks.connect_calls == [
        (
            "sample-net",
            "sample-cache",
            {"aliases": ["sample-cache.local"], "ipv4_address": "10.89.20.3"},
        )
    ]


@pytest.mark.asyncio
async def test_podman_adapter_recovery_restores_networks_with_raw_api():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([], network_names=["sample-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "sample-cache"},
        {
            "runtime_type": "podman",
            "container_id": "def1234567890",
            "container_name": "sample-cache",
            "image": "sample-cache:7.2",
            "create_config": {"image": "sample-cache:7.2", "name": "sample-cache"},
            "network_config": {
                "network_mode": "sample-net",
                "endpoints": {
                    "sample-net": {
                        "Aliases": ["sample-cache.local", "def1234567890"],
                        "IPAddress": "10.89.20.3",
                        "IPAMConfig": {"IPv4Address": "10.89.20.3"},
                    }
                },
            },
            "pod_id": None,
        },
    )

    assert result.updated is True
    assert client.networks.connect_calls == []
    assert client.api.network_connect_posts[0]["data"] == {
        "container": "new-id",
        "aliases": ["sample-cache.local"],
        "static_ips": ["10.89.20.3"],
    }


@pytest.mark.asyncio
async def test_podman_adapter_discovery_filters_pod_members():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    standalone = FakeContainer(
        "standalone",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={"Pod": "", "Config": {}, "HostConfig": {}},
    )
    pod_member = FakeContainer(
        "podmember",
        "sidecar",
        FakeImage(tags=["sidecar:1.0"]),
        attrs={"Pod": "mypod-abc123", "Config": {}, "HostConfig": {}},
    )
    client = FakePodmanClient([standalone, pod_member])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    targets = await adapter.discover_targets()

    assert [target.target_ref["container_id"] for target in targets] == ["standalone"]
    assert [target.name for target in targets] == ["sample-cache"]
    assert targets[0].target_ref == {
        "mode": "container",
        "container_id": "standalone",
        "container_name": "sample-cache",
    }


@pytest.mark.asyncio
async def test_podman_adapter_discovers_pod_backed_compose_projects_as_grouped_targets():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    standalone = FakeContainer(
        "standalone",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={"Pod": "", "Config": {}, "HostConfig": {}},
    )
    compose_member = FakeContainer(
        "agent-1",
        "sample-worker",
        FakeImage(tags=["docker.io/example/worker-agent:latest"]),
        attrs={
            "Pod": "pod-123",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "sample-worker",
                    "com.docker.compose.service": "sample-worker",
                    "com.docker.compose.project.working_dir": "/data/podman/sample-worker",
                    "com.docker.compose.project.config_files": "podman-compose.yaml",
                    "io.podman.compose.project": "sample-worker",
                }
            },
            "HostConfig": {},
        },
    )
    client = FakePodmanClient([standalone, compose_member])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    targets = await adapter.discover_targets()

    assert [target.name for target in targets] == ["sample-cache", "sample-worker"]
    compose_target = targets[1]
    assert compose_target.runtime_type == "podman"
    assert compose_target.image == "docker.io/example/worker-agent:latest"
    assert compose_target.target_ref == {
        "mode": "docker_compose",
        "project": "sample-worker",
        "working_dir": "/data/podman/sample-worker",
        "config_files": ["podman-compose.yaml"],
        "services": [
            {
                "service": "sample-worker",
                "replica_count": 1,
                "image": "docker.io/example/worker-agent:latest",
            }
        ],
        "service_count": 1,
    }


@pytest.mark.asyncio
async def test_docker_adapter_create_client_imports_sdk(monkeypatch):
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    adapter = DockerRuntimeAdapter(runtime, client=None)

    def fake_import(name):
        assert name == "docker"
        return FakeDockerModule

    monkeypatch.setattr("importlib.import_module", fake_import)
    client = adapter._create_client()
    assert client.base_url == "unix:///var/run/docker.sock"


@pytest.mark.asyncio
async def test_podman_adapter_create_client_imports_sdk(monkeypatch):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "tcp://localhost:8080", "api_version": "3.0"},
        secrets={},
    )
    adapter = PodmanRuntimeAdapter(runtime, client=None)

    def fake_import(name):
        assert name == "podman"
        return FakePodmanModule

    monkeypatch.setattr("importlib.import_module", fake_import)
    client = adapter._create_client()
    assert client.base_url == "tcp://localhost:8080"
    assert client.version == "3.0"


@pytest.mark.asyncio
async def test_kubernetes_adapter_create_apps_api_uses_kubeconfig(monkeypatch):
    runtime = RuntimeConnectionConfig.model_construct(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "context": "dev"},
        secrets={"kubeconfig": "apiVersion: v1"},
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=None)
    fake_config = FakeKubeConfig()

    def fake_import(name):
        if name == "kubernetes.client":
            return FakeKubeClient
        if name == "kubernetes.config":
            return fake_config
        raise ImportError(name)

    monkeypatch.setattr("importlib.import_module", fake_import)
    apps_api = adapter._create_apps_api()
    assert isinstance(apps_api, FakeKubeClient.AppsV1Api)
    assert fake_config.calls == [("dict", "dev")]


@pytest.mark.asyncio
async def test_kubernetes_adapter_create_apps_api_incluster(monkeypatch):
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=None)
    fake_config = FakeKubeConfig()

    def fake_import(name):
        if name == "kubernetes.client":
            return FakeKubeClient
        if name == "kubernetes.config":
            return fake_config
        raise ImportError(name)

    monkeypatch.setattr("importlib.import_module", fake_import)
    adapter._create_apps_api()
    assert fake_config.calls == [("incluster", None)]


@pytest.mark.asyncio
async def test_adapter_create_client_missing_dependency(monkeypatch):
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    adapter = DockerRuntimeAdapter(runtime, client=None)

    def fake_import(name):
        raise ImportError(name)

    monkeypatch.setattr("importlib.import_module", fake_import)
    with pytest.raises(RuntimeError, match="Missing Python dependency 'docker'"):
        adapter._create_client()


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_groups_containers_by_workload():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    single = FakeWorkload("api", [FakeContainerSpec("api", "api:1.0")])
    multi = FakeWorkload(
        "worker",
        [
            FakeContainerSpec("worker", "worker:1.0"),
            FakeContainerSpec("sidecar", "sidecar:1.0"),
        ],
    )
    apps_api = FakeAppsApi([single, multi], [], [])
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)

    targets = await adapter.discover_targets()
    assert [target.name for target in targets] == [
        "deployment/api",
        "deployment/worker",
    ]
    assert targets[0].target_ref == {
        "mode": "kubernetes_workload",
        "namespace": "apps",
        "kind": "Deployment",
        "name": "api",
        "services": [{"service": "api", "image": "api:1.0"}],
        "service_count": 1,
    }
    assert targets[1].target_ref == {
        "mode": "kubernetes_workload",
        "namespace": "apps",
        "kind": "Deployment",
        "name": "worker",
        "services": [
            {"service": "worker", "image": "worker:1.0"},
            {"service": "sidecar", "image": "sidecar:1.0"},
        ],
        "service_count": 2,
    }


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_groups_helm_managed_workloads_as_release_targets():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    helm_labels = {
        "app.kubernetes.io/managed-by": "Helm",
        "helm.sh/chart": "certd-0.8.0",
        "app.kubernetes.io/version": "2.0.0",
    }
    helm_annotations = {
        "meta.helm.sh/release-name": "certd",
        "meta.helm.sh/release-namespace": "apps",
    }
    helm_deployment = FakeWorkload(
        "certd-api",
        [FakeContainerSpec("api", "certd:2.0.0")],
        labels=helm_labels,
        annotations=helm_annotations,
    )
    helm_statefulset = FakeWorkload(
        "certd-db",
        [FakeContainerSpec("db", "sample-db:16")],
        labels=helm_labels,
        annotations=helm_annotations,
    )
    plain_workload = FakeWorkload("api", [FakeContainerSpec("api", "api:1.0")])
    apps_api = FakeAppsApi([helm_deployment, plain_workload], [helm_statefulset], [])
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)

    targets = await adapter.discover_targets()

    assert [target.name for target in targets] == ["helm/certd", "deployment/api"]
    assert targets[0].target_ref == {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "certd",
        "workloads": [
            {"kind": "Deployment", "name": "certd-api"},
            {"kind": "StatefulSet", "name": "certd-db"},
        ],
        "service_count": 2,
        "chart_name": "certd",
        "chart_version": "0.8.0",
        "app_version": "2.0.0",
    }
    assert targets[1].target_ref["mode"] == "kubernetes_workload"
    assert targets[1].target_ref["name"] == "api"


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_uses_helm_status_app_version_when_workload_label_missing(
    monkeypatch,
):
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    helm_labels = {
        "app.kubernetes.io/managed-by": "Helm",
        "helm.sh/chart": "sample-ci-5.8.13",
    }
    helm_annotations = {
        "meta.helm.sh/release-name": "sample-ci",
        "meta.helm.sh/release-namespace": "apps",
    }
    helm_deployment = FakeWorkload(
        "sample-ci",
        [FakeContainerSpec("sample-ci", "example/ci-server:2.528.1-jdk21")],
        labels=helm_labels,
        annotations=helm_annotations,
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([helm_deployment], [], []))

    def fake_run_helm_command(args):
        return json.dumps(
            {
                "name": "sample-ci",
                "app_version": "2.528.1",
                "chart": "sample-ci-5.8.13",
            }
        )

    monkeypatch.setattr(adapter, "_run_helm_command", fake_run_helm_command)

    targets = await adapter.discover_targets()

    assert targets[0].target_ref == {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "sample-ci",
        "workloads": [{"kind": "Deployment", "name": "sample-ci"}],
        "service_count": 1,
        "chart_name": "sample-ci",
        "chart_version": "5.8.13",
        "app_version": "2.528.1",
    }


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_uses_helm_list_app_version_when_status_lacks_it(
    monkeypatch,
):
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "sample-ci", "in_cluster": True},
        secrets={},
    )
    helm_labels = {
        "app.kubernetes.io/managed-by": "Helm",
        "helm.sh/chart": "sample-ci-5.9.17",
    }
    helm_annotations = {
        "meta.helm.sh/release-name": "sample-ci-5-1772603248",
        "meta.helm.sh/release-namespace": "sample-ci",
    }
    helm_statefulset = FakeWorkload(
        "sample-ci-5-1772603248",
        [FakeContainerSpec("sample-ci", "example/ci-server:2.528.1-jdk21")],
        labels=helm_labels,
        annotations=helm_annotations,
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([], [helm_statefulset], []))
    command_calls = []

    def fake_run_helm_command(args):
        command_calls.append(args)
        if args[0] == "status":
            return json.dumps({"name": "sample-ci-5-1772603248", "chart": "sample-ci-5.9.17"})
        if args[0] == "list":
            return json.dumps(
                [
                    {
                        "name": "sample-ci-5-1772603248",
                        "chart": "sample-ci-5.9.17",
                        "app_version": "2.528.1",
                    }
                ]
            )
        raise AssertionError(f"unexpected helm command: {args}")

    monkeypatch.setattr(adapter, "_run_helm_command", fake_run_helm_command)

    targets = await adapter.discover_targets()

    assert command_calls == [
        ["status", "sample-ci-5-1772603248", "--namespace", "sample-ci", "--output", "json"],
        [
            "list",
            "--namespace",
            "sample-ci",
            "--filter",
            "^sample-ci-5-1772603248$",
            "--output",
            "json",
        ],
    ]
    assert targets[0].target_ref == {
        "mode": "helm_release",
        "namespace": "sample-ci",
        "release_name": "sample-ci-5-1772603248",
        "workloads": [{"kind": "StatefulSet", "name": "sample-ci-5-1772603248"}],
        "service_count": 1,
        "chart_name": "sample-ci",
        "chart_version": "5.9.17",
        "app_version": "2.528.1",
    }


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_logs_optional_helm_metadata_failures_without_blocking(
    monkeypatch, caplog
):
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={"kubeconfig": "super-secret-kubeconfig"},
    )
    helm_labels = {
        "app.kubernetes.io/managed-by": "Helm",
        "helm.sh/chart": "certd-0.8.0",
    }
    helm_annotations = {
        "meta.helm.sh/release-name": "certd",
        "meta.helm.sh/release-namespace": "apps",
    }
    helm_deployment = FakeWorkload(
        "certd",
        [FakeContainerSpec("certd", "certd:2.0.0")],
        labels=helm_labels,
        annotations=helm_annotations,
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([helm_deployment], [], []))

    def fake_run_helm_command(args):
        raise RuntimeError(f"helm {args[0]} failed")

    monkeypatch.setattr(adapter, "_run_helm_command", fake_run_helm_command)

    with caplog.at_level(logging.WARNING, logger="releasetracker.executors.kubernetes"):
        targets = await adapter.discover_targets()

    assert targets[0].target_ref == {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "certd",
        "workloads": [{"kind": "Deployment", "name": "certd"}],
        "service_count": 1,
        "chart_name": "certd",
        "chart_version": "0.8.0",
    }
    assert "Optional Helm status metadata unavailable" in caplog.text
    assert "Optional Helm list metadata unavailable" in caplog.text
    assert "super-secret-kubeconfig" not in caplog.text


@pytest.mark.asyncio
async def test_kubernetes_adapter_validates_helm_release_with_status_json(monkeypatch):
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([], [], []))
    command_calls = []

    def fake_run_helm_command(args):
        command_calls.append(args)
        return json.dumps({"chart": {"metadata": {"name": "certd", "version": "0.8.0"}}})

    monkeypatch.setattr(adapter, "_run_helm_command", fake_run_helm_command)

    await adapter.validate_target_ref(
        {
            "mode": "helm_release",
            "namespace": "apps",
            "release_name": "certd",
        }
    )

    assert command_calls == [["status", "certd", "--namespace", "apps", "--output", "json"]]


@pytest.mark.asyncio
async def test_kubernetes_adapter_uses_target_ref_chart_version_when_status_lacks_metadata(
    monkeypatch,
):
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([], [], []))

    def fake_run_helm_command(args):
        return json.dumps({"name": "certd", "info": {"status": "deployed"}})

    monkeypatch.setattr(adapter, "_run_helm_command", fake_run_helm_command)
    target_ref = {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "certd",
        "chart_name": "certd",
        "chart_version": "0.8.0",
        "app_version": "2.0.0",
    }

    assert await adapter.get_helm_release_version(target_ref) == "0.8.0"
    snapshot = await adapter.capture_helm_release_snapshot(target_ref)
    await adapter.validate_helm_release_snapshot(target_ref, snapshot)

    assert snapshot["chart_name"] == "certd"
    assert snapshot["chart_version"] == "0.8.0"
    assert snapshot["app_version"] == "2.0.0"


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_requires_namespace_when_multiple_configured():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespaces": ["apps", "monitoring"], "in_cluster": True},
        secrets={},
    )
    apps_api = FakeAppsApi([FakeWorkload("api", [FakeContainerSpec("api", "api:1.0")])], [], [])
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)

    with pytest.raises(ValueError, match="namespace is required"):
        await adapter.discover_targets()


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_rejects_missing_configured_namespaces():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"in_cluster": True},
        secrets={},
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([], [], []))

    with pytest.raises(ValueError, match="no namespace is configured"):
        await adapter.discover_targets()


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_filters_to_configured_namespace():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespaces": ["apps", "monitoring"], "in_cluster": True},
        secrets={},
    )
    apps_api = FakeAppsApi([FakeWorkload("api", [FakeContainerSpec("api", "api:1.0")])], [], [])
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)

    targets = await adapter.discover_targets(namespace="apps")

    assert [target.target_ref["namespace"] for target in targets] == ["apps"]
    assert apps_api.list_calls == [
        ("Deployment", "apps"),
        ("StatefulSet", "apps"),
        ("DaemonSet", "apps"),
    ]


@pytest.mark.asyncio
async def test_kubernetes_adapter_discovery_rejects_unconfigured_namespace():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespaces": ["apps"], "in_cluster": True},
        secrets={},
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([], [], []))

    with pytest.raises(ValueError, match="not configured"):
        await adapter.discover_targets(namespace="kube-system")


@pytest.mark.asyncio
async def test_kubernetes_adapter_rejects_legacy_container_target_mode():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    multi = FakeWorkload(
        "worker",
        [
            FakeContainerSpec("worker", "worker:1.0"),
            FakeContainerSpec("sidecar", "sidecar:1.0"),
        ],
    )
    apps_api = FakeAppsApi([multi], [], [])
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)

    with pytest.raises(ValueError, match="kubernetes_workload"):
        await adapter.validate_target_ref(
            {
                "namespace": "apps",
                "kind": "Deployment",
                "name": "worker",
                "container": "worker",
            }
        )

    with pytest.raises(ValueError, match="kubernetes_workload"):
        await adapter.validate_target_ref(
            {
                "mode": "container",
                "namespace": "apps",
                "kind": "Deployment",
                "name": "worker",
                "container": "worker",
            }
        )


@pytest.mark.asyncio
async def test_kubernetes_adapter_validates_grouped_workload_target_and_namespace_scope():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespaces": ["apps"], "in_cluster": True},
        secrets={},
    )
    apps_api = FakeAppsApi(
        [FakeWorkload("worker", [FakeContainerSpec("worker", "worker:1.0")])], [], []
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)

    await adapter.validate_target_ref(
        {
            "mode": "kubernetes_workload",
            "namespace": "apps",
            "kind": "Deployment",
            "name": "worker",
        }
    )

    with pytest.raises(ValueError, match="not configured"):
        await adapter.validate_target_ref(
            {
                "mode": "kubernetes_workload",
                "namespace": "kube-system",
                "kind": "Deployment",
                "name": "worker",
            }
        )


@pytest.mark.asyncio
async def test_kubernetes_adapter_updates_image_with_patch_only():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    workload = FakeWorkload("api", [FakeContainerSpec("api", "api:1.0")])
    apps_api = FakeAppsApi([workload], [], [])
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)

    with pytest.raises(ValueError, match="kubernetes_workload"):
        await adapter.update_image(
            {
                "namespace": "apps",
                "kind": "Deployment",
                "name": "api",
                "container": "api",
            },
            "api:1.1",
        )
    assert apps_api.patch_calls == []


@pytest.mark.asyncio
async def test_kubernetes_adapter_updates_selected_container_in_multi_container_workload():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    workload = FakeWorkload(
        "worker",
        [
            FakeContainerSpec("worker", "worker:1.0"),
            FakeContainerSpec("sidecar", "sidecar:1.0"),
        ],
    )
    apps_api = FakeAppsApi([workload], [], [])
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)

    with pytest.raises(ValueError, match="kubernetes_workload"):
        await adapter.get_current_image(
            {
                "namespace": "apps",
                "kind": "Deployment",
                "name": "worker",
                "container": "sidecar",
            }
        )

    with pytest.raises(ValueError, match="kubernetes_workload"):
        await adapter.update_image(
            {
                "namespace": "apps",
                "kind": "Deployment",
                "name": "worker",
                "container": "sidecar",
            },
            "sidecar:1.1",
        )
    assert apps_api.patch_calls == []


@pytest.mark.asyncio
async def test_kubernetes_adapter_updates_bound_workload_services_in_single_patch():
    runtime = RuntimeConnectionConfig(
        name="k8s-prod",
        type="kubernetes",
        config={"namespace": "apps", "in_cluster": True},
        secrets={},
    )
    workload = FakeWorkload(
        "worker",
        [
            FakeContainerSpec("worker", "worker:1.0"),
            FakeContainerSpec("sidecar", "sidecar:1.0"),
        ],
    )
    apps_api = FakeAppsApi([workload], [], [])
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=apps_api)
    target_ref = {
        "mode": "kubernetes_workload",
        "namespace": "apps",
        "kind": "Deployment",
        "name": "worker",
    }

    assert await adapter.fetch_workload_service_images(target_ref) == {
        "worker": "worker:1.0",
        "sidecar": "sidecar:1.0",
    }

    result = await adapter.update_workload_services(
        target_ref,
        {"sidecar": "sidecar:1.1", "worker": "worker:1.1"},
    )

    assert result.updated is True
    assert apps_api.patch_calls == [
        (
            "Deployment",
            "worker",
            "apps",
            {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {"name": "sidecar", "image": "sidecar:1.1"},
                                {"name": "worker", "image": "worker:1.1"},
                            ]
                        }
                    }
                }
            },
        )
    ]

    with pytest.raises(ValueError, match="does not contain selected container"):
        await adapter.update_workload_services(target_ref, {"missing": "missing:1.0"})

    unauthorized_target_ref = {
        "mode": "kubernetes_workload",
        "namespace": "kube-system",
        "kind": "Deployment",
        "name": "worker",
    }
    with pytest.raises(ValueError, match="not configured"):
        await adapter.fetch_workload_service_images(unauthorized_target_ref)
    with pytest.raises(ValueError, match="not configured"):
        await adapter.update_workload_services(unauthorized_target_ref, {"worker": "worker:1.2"})


@pytest.mark.asyncio
async def test_podman_adapter_pull_failure_leaves_container_untouched():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "def",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={"Pod": "", "Config": {}, "HostConfig": {}},
    )
    client = FakePodmanClient([container], pull_should_fail=True)
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with pytest.raises(RuntimeError, match="pull failed"):
        await adapter.update_image({"container_name": "sample-cache"}, "sample-cache:7.4")

    assert len(container.stop_calls) == 0
    assert len(container.remove_calls) == 0
    assert len(client.containers.create_calls) == 0


@pytest.mark.asyncio
async def test_docker_adapter_recovery_removes_partial_replacement_before_recreate():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    partial_replacement = FakeContainer(
        "docker-new-id",
        "api",
        FakeImage(tags=["api:2.0"]),
        attrs={"Config": {}, "HostConfig": {}},
    )
    client = FakeDockerRecreateClient([partial_replacement])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "api"},
        {
            "runtime_type": "docker",
            "container_id": "old-id",
            "container_name": "api",
            "image": "api:1.0",
            "create_config": {"image": "api:1.0", "name": "api"},
        },
    )

    assert result.updated is True
    assert result.new_image == "api:1.0"
    assert len(partial_replacement.remove_calls) == 1
    assert client.containers.create_calls == [{"image": "api:1.0", "name": "api"}]


@pytest.mark.asyncio
async def test_docker_adapter_recovery_reuses_original_container_when_it_still_exists():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    original_container = FakeContainer(
        "old-id",
        "api",
        FakeImage(tags=["api:1.0"]),
        attrs={"Config": {}, "HostConfig": {}},
    )
    client = FakeDockerRecreateClient([original_container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "api"},
        {
            "runtime_type": "docker",
            "container_id": "old-id",
            "container_name": "api",
            "image": "api:1.0",
            "create_config": {"image": "api:1.0", "name": "api"},
        },
    )

    assert result.updated is True
    assert result.new_image == "api:1.0"
    assert result.new_container_id == "old-id"
    assert len(original_container.start_calls) == 1
    assert len(original_container.remove_calls) == 0
    assert client.containers.create_calls == []


@pytest.mark.asyncio
async def test_docker_adapter_recovery_skips_start_when_container_already_running():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    original_container = FakeContainer(
        "old-id",
        "api",
        FakeImage(tags=["api:1.0"]),
        attrs={"State": {"Running": True}, "Config": {}, "HostConfig": {}},
    )
    client = FakeDockerRecreateClient([original_container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "api"},
        {
            "runtime_type": "docker",
            "container_id": "old-id",
            "container_name": "api",
            "image": "api:1.0",
            "create_config": {"image": "api:1.0", "name": "api"},
        },
    )

    assert result.updated is True
    assert result.new_container_id == "old-id"
    assert len(original_container.start_calls) == 0
    assert len(original_container.remove_calls) == 0
    assert client.containers.create_calls == []


@pytest.mark.asyncio
async def test_docker_adapter_recovery_preserves_multi_port_create_config():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    existing_container = FakeContainer(
        "different-id",
        "api",
        FakeImage(tags=["api:2.0"]),
        attrs={"Config": {}, "HostConfig": {}},
    )
    client = FakeDockerRecreateClient([existing_container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    create_config = {
        "image": "api:1.0",
        "name": "api",
        "ports": {"80/tcp": ("", 8080), "443/tcp": ("", 8443)},
    }

    result = await adapter.recover_from_snapshot(
        {"container_name": "api"},
        {
            "runtime_type": "docker",
            "container_id": "old-id",
            "container_name": "api",
            "image": "api:1.0",
            "create_config": create_config,
        },
    )

    assert result.updated is True
    assert len(existing_container.remove_calls) == 1
    assert len(client.containers.create_calls) == 1
    assert client.containers.create_calls[0]["ports"] == {
        "80/tcp": ("", 8080),
        "443/tcp": ("", 8443),
    }


@pytest.mark.asyncio
async def test_docker_adapter_recovery_uses_snapshot_for_multi_binding_ports():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    container = FakeContainer(
        "old-id",
        "api",
        FakeImage(tags=["api:1.0"]),
        attrs={
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {
                "PortBindings": {
                    "80/tcp": [
                        {"HostIp": "", "HostPort": "8080"},
                        {"HostIp": "127.0.0.1", "HostPort": "18080"},
                    ]
                },
                "Binds": [],
                "RestartPolicy": {"Name": "", "MaximumRetryCount": 0},
                "NetworkMode": "bridge",
            },
        },
    )
    capture_client = FakeDockerRecreateClient([container])
    capture_adapter = DockerRuntimeAdapter(runtime, client=capture_client)

    snapshot = await capture_adapter.capture_snapshot({"container_name": "api"}, "api:1.0")
    await capture_adapter.validate_snapshot({"container_name": "api"}, snapshot)

    recovery_client = FakeDockerRecreateClient([])
    recovery_adapter = DockerRuntimeAdapter(runtime, client=recovery_client)

    result = await recovery_adapter.recover_from_snapshot({"container_name": "api"}, snapshot)

    assert result.updated is True
    assert recovery_client.containers.create_calls[0]["ports"] == {
        "80/tcp": [("", 8080), ("127.0.0.1", 18080)]
    }


@pytest.mark.asyncio
async def test_docker_adapter_recovery_preserves_snapshot_mounts():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    container = FakeContainer(
        "old-id",
        "api",
        FakeImage(tags=["api:1.0"]),
        attrs={
            "Config": {"Image": "api:1.0", "Labels": {"app": "api"}},
            "HostConfig": {
                "Binds": [],
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": "/srv/api/config",
                        "Target": "/etc/api",
                        "RW": False,
                        "BindOptions": {"Propagation": "rshared"},
                    },
                    {
                        "Type": "volume",
                        "Name": "api-data",
                        "Source": "api-data",
                        "Target": "/var/lib/api",
                        "RW": True,
                    },
                    {
                        "Type": "tmpfs",
                        "Target": "/run/api",
                        "TmpfsOptions": {"SizeBytes": 33554432, "Mode": 0o1777},
                    },
                ],
            },
        },
    )
    capture_client = FakeDockerRecreateClient([container])
    capture_adapter = DockerRuntimeAdapter(runtime, client=capture_client)

    snapshot = await capture_adapter.capture_snapshot({"container_name": "api"}, "api:1.0")
    recovery_client = FakeDockerRecreateClient([])
    recovery_adapter = DockerRuntimeAdapter(runtime, client=recovery_client)

    result = await recovery_adapter.recover_from_snapshot({"container_name": "api"}, snapshot)

    assert result.updated is True
    assert recovery_client.containers.create_calls[0]["mounts"] == [
        {
            "Target": "/etc/api",
            "Source": "/srv/api/config",
            "Type": "bind",
            "ReadOnly": True,
            "BindOptions": {"Propagation": "rshared"},
        },
        {
            "Target": "/var/lib/api",
            "Source": "api-data",
            "Type": "volume",
            "ReadOnly": False,
        },
        {
            "Target": "/run/api",
            "Type": "tmpfs",
            "Source": None,
            "TmpfsOptions": {"SizeBytes": 33554432, "Mode": 0o1777},
        },
    ]
    recovered = recovery_client.containers._created[0]
    assert (
        recovered.attrs["HostConfig"]["Mounts"]
        == recovery_client.containers.create_calls[0]["mounts"]
    )
    assert recovered.attrs["Mounts"] == [
        {
            "Destination": "/etc/api",
            "Type": "bind",
            "Source": "/srv/api/config",
            "RW": False,
        },
        {"Destination": "/var/lib/api", "Type": "volume", "Source": "api-data", "RW": True},
        {
            "Destination": "/run/api",
            "Type": "tmpfs",
            "Source": None,
            "RW": True,
            "TmpfsOptions": {"SizeBytes": 33554432, "Mode": 0o1777},
        },
    ]


@pytest.mark.asyncio
async def test_docker_adapter_recovery_restores_snapshot_networks():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    client = FakeDockerRecreateClient([], network_names=["app-net"])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "api"},
        {
            "runtime_type": "docker",
            "container_id": "old-id",
            "container_name": "api",
            "image": "api:1.0",
            "create_config": {"image": "api:1.0", "name": "api"},
            "network_config": {
                "network_mode": "bridge",
                "endpoints": {
                    "app-net": {
                        "Aliases": ["api", "old-id", "old-id"[:12]],
                        "IPAMConfig": {"IPv4Address": "172.30.10.8"},
                    }
                },
            },
        },
    )

    assert result.updated is True
    assert client.networks.connect_calls == [
        ("app-net", "api", {"aliases": ["api"], "ipv4_address": "172.30.10.8"})
    ]


@pytest.mark.asyncio
async def test_docker_adapter_recovery_cleans_up_failed_recovery_container():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    client = FakeDockerRecreateClient([])
    client.containers.fail_start_for_images.add("api:1.0")
    adapter = DockerRuntimeAdapter(runtime, client=client)

    with pytest.raises(RuntimeError, match="start failed: api:1.0"):
        await adapter.recover_from_snapshot(
            {"container_name": "api"},
            {
                "runtime_type": "docker",
                "container_id": "old-id",
                "container_name": "api",
                "image": "api:1.0",
                "create_config": {"image": "api:1.0", "name": "api"},
            },
        )

    assert len(client.containers._created) == 1
    assert len(client.containers._created[0].remove_calls) == 1


@pytest.mark.asyncio
async def test_podman_adapter_rejects_pod_member_before_destructive_steps():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "ghi",
        "sidecar",
        FakeImage(tags=["sidecar:1.0"]),
        attrs={"Pod": "mypod-abc123", "Config": {}, "HostConfig": {}},
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with pytest.raises(ValueError, match="Pod-member updates are not supported in phase 1"):
        await adapter.update_image({"container_name": "sidecar"}, "sidecar:2.0")

    assert client.images.pull_calls == []
    assert len(container.stop_calls) == 0
    assert len(container.remove_calls) == 0


@pytest.mark.asyncio
async def test_podman_adapter_noop_when_image_unchanged():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "def",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={"Pod": "", "Config": {}, "HostConfig": {}},
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image({"container_name": "sample-cache"}, "sample-cache:7.2")

    assert result.updated is False
    assert result.old_image == "sample-cache:7.2"
    assert result.new_image == "sample-cache:7.2"
    assert client.images.pull_calls == []
    assert len(container.stop_calls) == 0
    assert len(container.remove_calls) == 0


@pytest.mark.asyncio
async def test_podman_adapter_recovery_recreates_container_from_snapshot():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "sample-cache"},
        {
            "runtime_type": "podman",
            "container_id": "def",
            "container_name": "sample-cache",
            "image": "sample-cache:7.2",
            "create_config": {
                "image": "sample-cache:7.2",
                "name": "sample-cache",
                "environment": ["FOO=bar"],
                "network_mode": "   ",
            },
            "pod_id": None,
        },
    )

    assert result.updated is True
    assert result.new_image == "sample-cache:7.2"
    assert len(client.api.post_calls) == 1
    assert len(client.containers.create_calls) == 1
    create_payload = client.api.post_calls[0]["data"]
    assert create_payload["image"] == "sample-cache:7.2"
    assert "network_mode" not in create_payload
    assert "netns" not in create_payload


@pytest.mark.asyncio
async def test_podman_adapter_recovery_omits_blank_working_dir_from_low_level_payload(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter.recover_from_snapshot(
            {"container_name": "sample-cache"},
            {
                "runtime_type": "podman",
                "container_id": "def",
                "container_name": "sample-cache",
                "image": "sample-cache:7.2",
                "create_config": {
                    "image": "sample-cache:7.2",
                    "name": "sample-cache",
                    "working_dir": "   ",
                },
                "pod_id": None,
            },
        )

    assert result.updated is True
    assert len(client.api.post_calls) == 1
    create_payload = client.api.post_calls[0]["data"]
    create_kwargs = client.containers.create_calls[0]
    assert "working_dir" not in create_payload
    assert "work_dir" not in create_payload
    assert "working_dir" not in create_kwargs
    assert "work_dir" not in create_kwargs


@pytest.mark.asyncio
async def test_podman_adapter_recovery_preserves_nonblank_working_dir_in_low_level_payload(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter.recover_from_snapshot(
            {"container_name": "sample-cache"},
            {
                "runtime_type": "podman",
                "container_id": "def",
                "container_name": "sample-cache",
                "image": "sample-cache:7.2",
                "create_config": {
                    "image": "sample-cache:7.2",
                    "name": "sample-cache",
                    "working_dir": "/srv/app",
                },
                "pod_id": None,
            },
        )

    assert result.updated is True
    assert len(client.api.post_calls) == 1
    create_payload = client.api.post_calls[0]["data"]
    create_kwargs = client.containers.create_calls[0]
    assert create_payload["work_dir"] == "/srv/app"
    assert "working_dir" not in create_payload
    assert create_kwargs["work_dir"] == "/srv/app"
    assert "working_dir" not in create_kwargs
    assert "/srv/app" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_update_bypasses_high_level_default_empty_network_mode():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "def",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={
            "Pod": "",
            "Config": {"Image": "sample-cache:7.2", "WorkingDir": "   "},
            "HostConfig": {"NetworkMode": ""},
        },
    )
    client = FakePodmanClient([container])
    client.containers.inject_empty_network_mode_when_omitted = True
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image({"container_name": "sample-cache"}, "sample-cache:7.4")

    assert result.updated is True
    assert client.api.post_calls
    assert len(client.containers.create_calls) == 1
    create_payload = client.api.post_calls[0]["data"]
    assert "network_mode" not in create_payload
    assert "netns" not in create_payload
    assert "work_dir" not in create_payload
    assert client.containers.create_calls[0] == {
        "image": "sample-cache:7.4",
        "name": "sample-cache",
    }


@pytest.mark.asyncio
async def test_podman_adapter_update_sanitizes_blank_mount_destination_from_low_level_payload(
    caplog,
):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "storage-1",
        "storage-app",
        FakeImage(tags=["storage-app:1.0"]),
        attrs={
            "Pod": "",
            "Config": {"Image": "storage-app:1.0"},
            "HostConfig": {
                "Binds": [
                    "/srv/invalid:",
                    "/srv/valid:/data:rw",
                ],
                "RestartPolicy": {"Name": ""},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter.update_image({"container_name": "storage-app"}, "storage-app:1.1")

    assert result.updated is True
    payload = client.api.post_calls[0]["data"]
    create_kwargs = client.containers.create_calls[0]
    assert payload["mounts"] == [
        {"destination": "/data", "options": ["rw"], "source": "/srv/valid", "type": "bind"}
    ]
    assert create_kwargs["mounts"] == payload["mounts"]
    assert "/srv/invalid" not in caplog.text
    assert "/srv/valid" not in caplog.text
    assert "/data" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_snapshot_rejects_pod_member():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "pod-1",
        "api",
        FakeImage(tags=["api:1.0"]),
        attrs={
            "Pod": "pod-xyz",
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {"PortBindings": {}, "Binds": []},
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    snapshot = await adapter.capture_snapshot({"container_name": "api"}, "api:1.0")
    assert snapshot["pod_id"] == "pod-xyz"

    with pytest.raises(ValueError, match="member of pod"):
        await adapter.validate_snapshot({"container_name": "api"}, snapshot)

    with pytest.raises(ValueError, match="member of pod"):
        await adapter.recover_from_snapshot({"container_name": "api"}, snapshot)


@pytest.mark.asyncio
async def test_podman_adapter_named_volume_mode_flags_are_sanitized():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "vol1",
        "sample_base",
        FakeImage(tags=["sample-base:latest"]),
        attrs={
            "Pod": "",
            "Config": {
                "Env": None,
                "Cmd": ["tail", "-f", "/etc/hosts"],
                "Entrypoint": None,
                "Labels": None,
            },
            "HostConfig": {
                "PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "80"}]},
                "Binds": ["test-buntu:/data/:rw,rprivate,nosuid,nodev,rbind"],
                "RestartPolicy": {"Name": "", "MaximumRetryCount": 0},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image({"container_name": "sample_base"}, "sample-base:22.04")

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["volumes"] == [
        {
            "Name": "test-buntu",
            "Dest": "/data/",
            "Options": ["rw", "rprivate", "nosuid", "nodev", "rbind"],
        }
    ]
    assert create_kwargs["portmappings"] == [
        {"container_port": 80, "host_port": 80, "protocol": "tcp"}
    ]


def test_podman_fake_api_rejects_legacy_named_volume_key_shape():
    container_manager = FakePodmanContainerManager([])
    api = FakePodmanApi(container_manager)

    with pytest.raises(FakePodmanServerError, match="container directory cannot be empty"):
        api.post(
            "/containers/create",
            data=json.dumps(
                {
                    "image": "sample-base:22.04",
                    "volumes": [
                        {
                            "dest": "/data",
                            "name": "legacy-shape-volume",
                            "options": ["rw"],
                        }
                    ],
                }
            ),
        )

    with pytest.raises(FakePodmanServerError, match="crun: mount"):
        api.post(
            "/containers/create",
            data=json.dumps(
                {
                    "image": "sample-base:22.04",
                    "mounts": [
                        {
                            "type": "volume",
                            "source": "source-volume",
                            "destination": "/data",
                            "options": ["rw"],
                        }
                    ],
                }
            ),
        )

    response = api.post(
        "/containers/create",
        data=json.dumps(
            {
                "image": "sample-base:22.04",
                "volumes": [
                    {
                        "Dest": "/data",
                        "Name": "source-volume",
                        "Options": ["rw"],
                    }
                ],
            }
        ),
    )

    assert response.json()["Id"] == "new-id"
    assert container_manager.create_calls[0]["volumes"] == [
        {
            "Dest": "/data",
            "Name": "source-volume",
            "Options": ["rw"],
        }
    ]


@pytest.mark.asyncio
async def test_podman_adapter_low_level_create_converts_named_volumes_to_raw_volumes(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        adapter._create_podman_container(
            client,
            {
                "image": "sample-base:22.04",
                "name": "storage-app",
                "volumes": {
                    "test-buntu": {"bind": "/data", "mode": "rw"},
                    "/srv/config": {"bind": "/config", "mode": "ro"},
                },
                "mounts": [{"type": "tmpfs", "target": "/tmp", "size": "16m"}],
            },
        )

    payload = client.api.post_calls[0]["data"]
    assert payload["volumes"] == [{"Name": "test-buntu", "Dest": "/data", "Options": ["rw"]}]
    assert payload["mounts"] == [
        {"destination": "/tmp", "options": ["size=16m"], "type": "tmpfs"},
        {"options": ["ro"], "source": "/srv/config", "destination": "/config", "type": "bind"},
    ]
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["volumes"] == payload["volumes"]
    assert create_kwargs["mounts"] == payload["mounts"]
    assert "'volume':" not in caplog.text
    assert "/srv/config" not in caplog.text
    assert "/data" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_low_level_create_absolutizes_relative_storage_destinations(
    caplog,
):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        adapter._create_podman_container(
            client,
            {
                "image": "nginx:1.26",
                "name": "nginx-podman",
                "volumes": {
                    "nginx-podman-cache": {"bind": "var/cache/nginx", "mode": "rw"},
                    "/srv/nginx-conf": {"bind": "etc/nginx/conf.d", "mode": "ro"},
                },
                "mounts": [
                    {"type": "bind", "source": "/srv/html", "target": "usr/share/nginx/html"},
                    {"type": "tmpfs", "target": "run/nginx", "size": "16m"},
                    {"type": "bind", "source": "/srv/logs", "dest": "var/log/nginx"},
                ],
            },
        )

    payload = client.api.post_calls[0]["data"]
    assert payload["volumes"] == [
        {"Name": "nginx-podman-cache", "Dest": "/var/cache/nginx", "Options": ["rw"]}
    ]
    assert payload["mounts"] == [
        {"destination": "/usr/share/nginx/html", "source": "/srv/html", "type": "bind"},
        {"destination": "/run/nginx", "options": ["size=16m"], "type": "tmpfs"},
        {"destination": "/var/log/nginx", "source": "/srv/logs", "type": "bind"},
        {
            "options": ["ro"],
            "source": "/srv/nginx-conf",
            "destination": "/etc/nginx/conf.d",
            "type": "bind",
        },
    ]
    assert client.containers.create_calls[0]["volumes"] == payload["volumes"]
    assert client.containers.create_calls[0]["mounts"] == payload["mounts"]
    assert "var/cache/nginx" not in caplog.text
    assert "/var/cache/nginx" not in caplog.text
    assert "/srv/nginx-conf" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_low_level_create_normalizes_legacy_mount_destination_key(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        adapter._create_podman_container(
            client,
            {
                "image": "sample-base:22.04",
                "name": "storage-app",
                "mounts": [{"destination": "/config", "source": "/srv/config", "type": "bind"}],
            },
        )

    payload = client.api.post_calls[0]["data"]
    assert payload["mounts"] == [
        {"destination": "/config", "source": "/srv/config", "type": "bind"}
    ]
    assert client.containers.create_calls[0]["mounts"] == payload["mounts"]
    assert "/srv/config" not in caplog.text
    assert "/config" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_low_level_create_drops_rendered_volume_without_source_named_volume(
    caplog,
):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        adapter._create_podman_container(
            client,
            {
                "image": "sample-base:22.04",
                "name": "storage-app",
                "volumes": [
                    {"name": "generated-volume", "dest": "/image-declared", "options": ["rw"]},
                ],
                "mounts": [
                    {"type": "bind", "source": "/srv/config", "target": "/config"},
                    {"type": "tmpfs", "target": "/tmp", "size": "16m"},
                ],
            },
        )

    payload = client.api.post_calls[0]["data"]
    assert "volumes" not in payload
    assert payload["mounts"] == [
        {"destination": "/config", "source": "/srv/config", "type": "bind"},
        {"destination": "/tmp", "options": ["size=16m"], "type": "tmpfs"},
    ]
    create_kwargs = client.containers.create_calls[0]
    assert "volumes" not in create_kwargs
    assert create_kwargs["mounts"] == payload["mounts"]
    assert "/image-declared" not in caplog.text
    assert "/srv/config" not in caplog.text
    assert "/tmp" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_low_level_create_drops_blank_storage_destinations(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        adapter._create_podman_container(
            client,
            {
                "image": "sample-base:22.04",
                "name": "storage-app",
                "volumes": [
                    {"name": "bad-volume", "dest": ""},
                    {"name": "also-bad", "destination": "   "},
                    {"name": "good-volume", "dest": "/data", "options": ["rw"]},
                ],
                "mounts": [
                    {"type": "bind", "source": "/secret", "target": ""},
                    {"type": "tmpfs", "target": "   ", "size": "16m"},
                    {"type": "bind", "source": "/srv/config", "target": "/config"},
                    {"type": "tmpfs", "target": "/tmp", "size": "16m"},
                ],
            },
        )

    payload = client.api.post_calls[0]["data"]
    assert "volumes" not in payload
    assert payload["mounts"] == [
        {"destination": "/config", "source": "/srv/config", "type": "bind"},
        {"destination": "/tmp", "options": ["size=16m"], "type": "tmpfs"},
    ]
    assert "volumes" not in client.containers.create_calls[0]
    assert client.containers.create_calls[0]["mounts"] == payload["mounts"]
    assert "/secret" not in caplog.text
    assert "/srv/config" not in caplog.text
    assert "/data" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_low_level_create_drops_named_volumes_with_missing_or_blank_name(
    caplog,
):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        adapter._create_podman_container(
            client,
            {
                "image": "sample-base:22.04",
                "name": "storage-app",
                "volumes": [
                    {"dest": "/image-declared"},
                    {"name": "", "dest": "/blank-name"},
                    {"name": "   ", "destination": "/blank-name-destination"},
                    {"name": "good-volume", "target": "/data", "options": ["rw"]},
                ],
            },
        )

    payload = client.api.post_calls[0]["data"]
    assert "volumes" not in payload
    assert "volumes" not in client.containers.create_calls[0]
    assert "/image-declared" not in caplog.text
    assert "/blank-name" not in caplog.text
    assert "/blank-name-destination" not in caplog.text
    assert "/data" not in caplog.text


@pytest.mark.asyncio
async def test_podman_adapter_recovery_absolutizes_relative_storage_destinations_from_snapshot():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "nginx-podman"},
        {
            "runtime_type": "podman",
            "container_id": "old-id",
            "container_name": "nginx-podman",
            "image": "nginx:1.26",
            "create_config": {
                "image": "nginx:1.26",
                "name": "nginx-podman",
                "volumes": {"nginx-podman-cache": {"bind": "var/cache/nginx", "mode": "rw"}},
                "mounts": [
                    {"type": "bind", "source": "/srv/html", "target": "usr/share/nginx/html"},
                    {"type": "tmpfs", "destination": "run/nginx", "size": "16m"},
                ],
            },
            "pod_id": None,
        },
    )

    assert result.updated is True
    payload = client.api.post_calls[0]["data"]
    assert payload["volumes"] == [
        {"Name": "nginx-podman-cache", "Dest": "/var/cache/nginx", "Options": ["rw"]}
    ]
    assert payload["mounts"] == [
        {"destination": "/usr/share/nginx/html", "source": "/srv/html", "type": "bind"},
        {"destination": "/run/nginx", "options": ["size=16m"], "type": "tmpfs"},
    ]
    assert client.containers.create_calls[0]["volumes"] == payload["volumes"]
    assert client.containers.create_calls[0]["mounts"] == payload["mounts"]


@pytest.mark.asyncio
async def test_podman_adapter_recovery_drops_invalid_named_volumes_from_snapshot():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "storage-app"},
        {
            "runtime_type": "podman",
            "container_id": "old-id",
            "container_name": "storage-app",
            "image": "sample-base:22.04",
            "create_config": {
                "image": "sample-base:22.04",
                "name": "storage-app",
                "volumes": [
                    {"dest": "/image-declared"},
                    {"name": "", "dest": "/blank-name"},
                    {"name": "good-volume", "dest": "/data", "options": ["rw"]},
                ],
            },
            "pod_id": None,
        },
    )

    assert result.updated is True
    payload = client.api.post_calls[0]["data"]
    assert "volumes" not in payload
    assert "volumes" not in client.containers.create_calls[0]


@pytest.mark.asyncio
async def test_podman_adapter_recovery_preserves_source_named_volume_from_snapshot():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "storage-app"},
        {
            "runtime_type": "podman",
            "container_id": "old-id",
            "container_name": "storage-app",
            "image": "sample-base:22.04",
            "create_config": {
                "image": "sample-base:22.04",
                "name": "storage-app",
                "volumes": {"good-volume": {"bind": "/data", "mode": "rw"}},
            },
            "pod_id": None,
        },
    )

    assert result.updated is True
    payload = client.api.post_calls[0]["data"]
    assert payload["volumes"] == [{"Name": "good-volume", "Dest": "/data", "Options": ["rw"]}]
    assert "mounts" not in payload
    assert client.containers.create_calls[0]["volumes"] == payload["volumes"]
    assert "mounts" not in client.containers.create_calls[0]


@pytest.mark.asyncio
async def test_podman_adapter_bind_mount_ro_flag_is_preserved():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "ro1",
        "sample-web",
        FakeImage(tags=["sample-web:1.25"]),
        attrs={
            "Pod": "",
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {
                "PortBindings": {},
                "Binds": ["/etc/config:/etc/config:ro,rprivate"],
                "RestartPolicy": {"Name": "", "MaximumRetryCount": 0},
                "NetworkMode": "host",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image({"container_name": "sample-web"}, "sample-web:1.26")

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["mounts"] == [
        {
            "destination": "/etc/config",
            "options": ["ro", "rprivate"],
            "source": "/etc/config",
            "type": "bind",
        }
    ]
    assert "ports" not in create_kwargs


@pytest.mark.asyncio
async def test_podman_adapter_update_image_returns_new_container_id():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "old-id",
        "sample-cache",
        FakeImage(tags=["sample-cache:7.2"]),
        attrs={
            "Pod": "",
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {"Name": ""},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image(
        {"container_id": "old-id", "container_name": "sample-cache"}, "sample-cache:7.4"
    )

    assert result.updated is True
    assert result.new_container_id == "new-id"


@pytest.mark.asyncio
async def test_podman_adapter_preserves_tmpfs_in_snapshot_but_omits_unsupported_create_kwarg():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "tmpfs-1",
        "tmpfs-app",
        FakeImage(tags=["ghcr.io/acme/tmpfs-app:1.0"]),
        attrs={
            "Pod": "",
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "Tmpfs": {"/tmp/app": "rw,size=16m"},
                "RestartPolicy": {"Name": ""},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    snapshot = await adapter.capture_snapshot(
        {"container_name": "tmpfs-app"}, "ghcr.io/acme/tmpfs-app:1.0"
    )
    await adapter.validate_snapshot({"container_name": "tmpfs-app"}, snapshot)
    assert snapshot["create_config"]["tmpfs"] == {"/tmp/app": "rw,size=16m"}

    result = await adapter.update_image(
        {"container_id": "tmpfs-1", "container_name": "tmpfs-app"},
        "ghcr.io/acme/tmpfs-app:1.1",
    )

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert "tmpfs" not in create_kwargs


@pytest.mark.asyncio
async def test_podman_adapter_update_normalizes_relative_hostconfig_bind_and_tmpfs_destinations():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "nginx-1",
        "nginx-podman",
        FakeImage(tags=["nginx:1.25"]),
        attrs={
            "Pod": "",
            "Config": {"Image": "nginx:1.25"},
            "HostConfig": {
                "Binds": [
                    "nginx-podman-cache:var/cache/nginx:rw",
                    "/srv/nginx-conf:etc/nginx/conf.d:ro",
                ],
                "Tmpfs": {"run/nginx": "size=16m"},
                "RestartPolicy": {"Name": ""},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image({"container_name": "nginx-podman"}, "nginx:1.26")

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["volumes"] == [
        {"Name": "nginx-podman-cache", "Dest": "/var/cache/nginx", "Options": ["rw"]}
    ]
    assert create_kwargs["mounts"] == [
        {
            "destination": "/etc/nginx/conf.d",
            "options": ["ro"],
            "source": "/srv/nginx-conf",
            "type": "bind",
        },
        {"destination": "/run/nginx", "options": ["size=16m"], "type": "tmpfs"},
    ]


@pytest.mark.asyncio
async def test_podman_adapter_update_restores_custom_networks():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    old_container_id = "a0debec2a247f00dbabe"
    container = FakeContainer(
        old_container_id,
        "nginx-podman-run",
        FakeImage(tags=["nginx:1.25"]),
        attrs={
            "Pod": "",
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {"Name": ""},
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {
                "Networks": {
                    "app-net": {
                        "Aliases": [
                            "nginx.local",
                            "nginx-podman-run",
                            old_container_id,
                            old_container_id[:12],
                            "nginx-podman-run-host",
                            " nginx.local ",
                            "",
                        ],
                        "IPAddress": "10.89.20.10",
                    }
                }
            },
        },
    )
    client = FakePodmanClient([container], network_names=["app-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image(
        {"container_id": old_container_id, "container_name": "nginx-podman-run"},
        "nginx:1.26",
    )

    assert result.updated is True
    assert client.networks.connect_calls == []
    assert client.api.network_connect_posts == [
        {
            "path": "networks/app-net/connect",
            "data": {
                "container": "new-id",
                "aliases": [
                    "nginx.local",
                    "nginx-podman-run",
                    "nginx-podman-run-host",
                ],
                "static_ips": ["10.89.20.10"],
            },
            "headers": {"Content-Type": "application/json"},
            "kwargs": {"compatible": False},
        }
    ]
    assert client.networks.disconnect_calls == [
        ("app-net", "nginx-podman-run", True),
        ("podman", "nginx-podman-run", True),
    ]


@pytest.mark.asyncio
async def test_podman_adapter_snapshot_includes_non_pod_network_config():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "snapshot-net-1",
        "snapshot-net-app",
        FakeImage(tags=["ghcr.io/acme/snapshot-net-app:1.0"]),
        attrs={
            "Pod": "",
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {"Name": ""},
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {
                "Networks": {
                    "app-net": {
                        "Aliases": ["snapshot.local", "snapshot-net-app"],
                        "IPAMConfig": {"IPv4Address": "10.89.20.10"},
                    }
                }
            },
        },
    )
    client = FakePodmanClient([container], network_names=["app-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    snapshot = await adapter.capture_snapshot(
        {"container_id": "snapshot-net-1", "container_name": "snapshot-net-app"},
        "ghcr.io/acme/snapshot-net-app:1.0",
    )

    assert snapshot["network_config"] == {
        "network_mode": "bridge",
        "endpoints": {
            "app-net": {
                "Aliases": ["snapshot.local", "snapshot-net-app"],
                "IPAMConfig": {"IPv4Address": "10.89.20.10"},
            }
        },
    }


@pytest.mark.asyncio
async def test_podman_adapter_recovery_omits_tmpfs_create_kwarg_and_restores_networks():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([], network_names=["app-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "tmpfs-app"},
        {
            "runtime_type": "podman",
            "container_id": "old-id",
            "container_name": "tmpfs-app",
            "image": "ghcr.io/acme/tmpfs-app:1.0",
            "create_config": {
                "image": "ghcr.io/acme/tmpfs-app:1.0",
                "name": "tmpfs-app",
                "tmpfs": {"/tmp/app": "rw,size=16m"},
            },
            "network_config": {
                "network_mode": "bridge",
                "endpoints": {
                    "app-net": {
                        "Aliases": ["tmpfs-app", "old-id", "old-id"[:12]],
                        "IPAMConfig": {"IPv4Address": "10.89.20.10"},
                    }
                },
            },
            "pod_id": None,
        },
    )

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert "tmpfs" not in create_kwargs
    assert client.networks.connect_calls == []
    assert client.api.network_connect_posts == [
        {
            "path": "networks/app-net/connect",
            "data": {
                "container": "new-id",
                "aliases": ["tmpfs-app"],
                "static_ips": ["10.89.20.10"],
            },
            "headers": {"Content-Type": "application/json"},
            "kwargs": {"compatible": False},
        }
    ]


@pytest.mark.asyncio
async def test_podman_adapter_normalizes_extra_hosts_for_sdk_create():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "hosts-1",
        "hosts-app",
        FakeImage(tags=["ghcr.io/acme/hosts-app:1.0"]),
        attrs={
            "Pod": "",
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "ExtraHosts": [
                    "host.containers.internal:host-gateway",
                    "example.local:127.0.0.1",
                ],
                "RestartPolicy": {"Name": ""},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    snapshot = await adapter.capture_snapshot(
        {"container_name": "hosts-app"}, "ghcr.io/acme/hosts-app:1.0"
    )
    await adapter.validate_snapshot({"container_name": "hosts-app"}, snapshot)
    assert snapshot["create_config"]["extra_hosts"] == [
        "host.containers.internal:host-gateway",
        "example.local:127.0.0.1",
    ]

    result = await adapter.update_image(
        {"container_id": "hosts-1", "container_name": "hosts-app"},
        "ghcr.io/acme/hosts-app:1.1",
    )

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["hostadd"] == [
        "example.local:127.0.0.1",
        "host.containers.internal:host-gateway",
    ]


@pytest.mark.asyncio
async def test_podman_adapter_preserves_hostconfig_init_in_create_config():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "init-1",
        "init-app",
        FakeImage(tags=["ghcr.io/acme/init-app:1.0"]),
        attrs={
            "Pod": "",
            "Config": {"Env": None, "Cmd": None, "Entrypoint": None, "Labels": None},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "Init": True,
                "RestartPolicy": {"Name": ""},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image(
        {"container_id": "init-1", "container_name": "init-app"},
        "ghcr.io/acme/init-app:1.1",
    )

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["init"] is True


@pytest.mark.asyncio
async def test_podman_adapter_recovery_returns_new_container_id():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "sample-cache"},
        {
            "runtime_type": "podman",
            "container_id": "old-id",
            "container_name": "sample-cache",
            "image": "sample-cache:7.2",
            "create_config": {"image": "sample-cache:7.2", "name": "sample-cache"},
            "pod_id": None,
        },
    )

    assert result.updated is True
    assert result.new_container_id == "new-id"


@pytest.mark.asyncio
async def test_docker_compose_grouped_update_recreates_targeted_services_with_shared_specs():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    worker_labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "worker",
        "com.docker.compose.container-number": "1",
    }
    api_labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "api",
        "com.docker.compose.container-number": "1",
        "com.docker.compose.depends_on": "worker:service_started",
    }
    worker = FakeContainer(
        "worker-1",
        "release-stack-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={
            "Config": {
                "Image": "ghcr.io/acme/worker:9.1",
                "Env": ["WORKER_CONCURRENCY=4"],
                "Cmd": ["./worker"],
                "Entrypoint": ["/bin/sh", "-lc"],
                "User": "1000:1000",
                "WorkingDir": "/app",
                "Hostname": "worker-host",
                "Labels": worker_labels,
                "ExposedPorts": {"9000/tcp": {}},
            },
            "HostConfig": {
                "PortBindings": {},
                "Binds": ["/srv/worker:/app/data:rw"],
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": "/srv/worker/public",
                        "Target": "/usr/share/nginx/html",
                        "ReadOnly": True,
                        "BindOptions": {"Propagation": "rprivate"},
                    },
                    {
                        "Type": "tmpfs",
                        "Target": "/var/cache/nginx",
                        "TmpfsOptions": {"SizeBytes": 16777216, "Mode": 0o1777},
                    },
                ],
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m"}},
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "release-stack_default",
                "ExtraHosts": ["host.docker.internal:host-gateway"],
                "Dns": ["1.1.1.1", "8.8.8.8"],
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=65536k"},
                "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
                "SecurityOpt": ["no-new-privileges:true"],
                "CapAdd": ["NET_BIND_SERVICE"],
                "CapDrop": ["MKNOD"],
                "Devices": ["/dev/fuse:/dev/fuse:rwm"],
            },
            "NetworkSettings": {
                "Networks": {
                    "release-stack_default": {
                        "Aliases": ["worker", "release-stack-worker-1", "worker-1"],
                        "Links": [],
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    api = FakeContainer(
        "api-1",
        "release-stack-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.2.3"]),
        attrs={
            "Config": {
                "Image": "ghcr.io/acme/api:1.2.3",
                "Env": ["FOO=bar"],
                "Cmd": ["uvicorn", "app:app"],
                "Entrypoint": ["python", "-m"],
                "User": "1001:1001",
                "WorkingDir": "/srv/api",
                "Hostname": "api-host",
                "Labels": api_labels,
                "Healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost:8000/health"]},
                "ExposedPorts": {"8000/tcp": {}, "9000/tcp": {}},
            },
            "HostConfig": {
                "PortBindings": {
                    "8000/tcp": [
                        {"HostIp": "", "HostPort": "8080"},
                        {"HostIp": "127.0.0.1", "HostPort": "18080"},
                    ]
                },
                "Binds": ["/srv/api:/app/data:ro"],
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": "/srv/api/conf.d",
                        "Target": "/etc/nginx/conf.d",
                        "ReadOnly": True,
                    },
                    {
                        "Type": "tmpfs",
                        "Target": "/tmp/nginx-tmpfs",
                        "TmpfsOptions": {"SizeBytes": 16777216, "Mode": 0o700},
                    },
                ],
                "LogConfig": {"Type": "syslog", "Config": {"syslog-address": "udp://log:514"}},
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "release-stack_default",
                "ExtraHosts": ["db.internal:10.0.0.10"],
                "Dns": ["9.9.9.9"],
                "Tmpfs": {"/run": "rw,noexec,nosuid,size=65536k"},
                "Ulimits": [{"Name": "nproc", "Soft": 2048, "Hard": 4096}],
                "SecurityOpt": ["label=disable"],
                "CapAdd": ["NET_ADMIN"],
                "CapDrop": ["AUDIT_WRITE"],
                "Devices": ["/dev/net/tun:/dev/net/tun:rwm"],
            },
            "NetworkSettings": {
                "Networks": {
                    "frontend": {
                        "Aliases": ["api", "release-stack-api-1", "api-1"],
                        "Links": [],
                        "IPAMConfig": {"IPv4Address": "172.20.0.10"},
                    },
                    "release-stack_default": {
                        "Aliases": ["api", "release-stack-api-1", "api-1"],
                        "Links": [],
                        "IPAMConfig": None,
                    },
                }
            },
        },
    )
    client = FakeDockerRecreateClient(
        [api, worker], network_names=["frontend", "release-stack_default"]
    )
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "release-stack"},
        {
            "api": "ghcr.io/acme/api:1.2.4",
            "worker": "ghcr.io/acme/worker:9.2",
        },
    )

    assert result.updated is True
    assert result.old_image == "api=ghcr.io/acme/api:1.2.3; worker=ghcr.io/acme/worker:9.1"
    assert result.new_image == "api=ghcr.io/acme/api:1.2.4; worker=ghcr.io/acme/worker:9.2"
    assert result.message == "docker compose grouped update applied"
    assert client.images.pull_calls == ["ghcr.io/acme/api:1.2.4", "ghcr.io/acme/worker:9.2"]
    assert client.containers.event_log == [
        "stop:release-stack-api-1",
        "stop:release-stack-worker-1",
        "remove:release-stack-api-1",
        "remove:release-stack-worker-1",
        "create:release-stack-worker-1",
        "start:release-stack-worker-1",
        "create:release-stack-api-1",
        "start:release-stack-api-1",
    ]
    assert [call["name"] for call in client.containers.create_calls] == [
        "release-stack-worker-1",
        "release-stack-api-1",
    ]

    worker_create = client.containers.create_calls[0]
    assert worker_create["image"] == "ghcr.io/acme/worker:9.2"
    assert worker_create["environment"] == ["WORKER_CONCURRENCY=4"]
    assert worker_create["entrypoint"] == ["/bin/sh", "-lc"]
    assert worker_create["command"] == ["./worker"]
    assert worker_create["user"] == "1000:1000"
    assert worker_create["working_dir"] == "/app"
    assert worker_create["hostname"] == "worker-host"
    assert "ports" not in worker_create
    assert worker_create["volumes"] == {"/srv/worker": {"bind": "/app/data", "mode": "rw"}}
    assert worker_create["mounts"] == [
        {
            "Target": "/usr/share/nginx/html",
            "Source": "/srv/worker/public",
            "Type": "bind",
            "ReadOnly": True,
            "BindOptions": {"Propagation": "rprivate"},
        },
        {
            "Target": "/var/cache/nginx",
            "Type": "tmpfs",
            "Source": None,
            "TmpfsOptions": {"SizeBytes": 16777216, "Mode": 0o1777},
        },
    ]
    assert worker_create["log_config"] == {"Type": "json-file", "Config": {"max-size": "10m"}}
    assert worker_create["restart_policy"] == {"Name": "unless-stopped", "MaximumRetryCount": 0}
    assert worker_create["network_mode"] == "release-stack_default"
    assert worker_create["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert worker_create["dns"] == ["1.1.1.1", "8.8.8.8"]
    assert worker_create["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=65536k"}
    assert worker_create["ulimits"] == [{"Name": "nofile", "Soft": 1024, "Hard": 2048}]
    assert worker_create["security_opt"] == ["no-new-privileges:true"]
    assert worker_create["cap_add"] == ["NET_BIND_SERVICE"]
    assert worker_create["cap_drop"] == ["MKNOD"]
    assert worker_create["devices"] == ["/dev/fuse:/dev/fuse:rwm"]
    assert worker_create["labels"] == worker_labels

    api_create = client.containers.create_calls[1]
    assert api_create["image"] == "ghcr.io/acme/api:1.2.4"
    assert api_create["environment"] == ["FOO=bar"]
    assert api_create["entrypoint"] == ["python", "-m"]
    assert api_create["command"] == ["uvicorn", "app:app"]
    assert api_create["user"] == "1001:1001"
    assert api_create["working_dir"] == "/srv/api"
    assert api_create["hostname"] == "api-host"
    assert api_create["ports"] == {"8000/tcp": [("", 8080), ("127.0.0.1", 18080)]}
    assert api_create["volumes"] == {"/srv/api": {"bind": "/app/data", "mode": "ro"}}
    assert api_create["mounts"] == [
        {
            "Target": "/etc/nginx/conf.d",
            "Source": "/srv/api/conf.d",
            "Type": "bind",
            "ReadOnly": True,
        },
        {
            "Target": "/tmp/nginx-tmpfs",
            "Type": "tmpfs",
            "Source": None,
            "TmpfsOptions": {"SizeBytes": 16777216, "Mode": 0o700},
        },
    ]
    assert api_create["log_config"] == {
        "Type": "syslog",
        "Config": {"syslog-address": "udp://log:514"},
    }
    assert api_create["restart_policy"] == {"Name": "unless-stopped", "MaximumRetryCount": 0}
    assert api_create["network_mode"] == "release-stack_default"
    assert api_create["extra_hosts"] == ["db.internal:10.0.0.10"]
    assert api_create["dns"] == ["9.9.9.9"]
    assert api_create["tmpfs"] == {"/run": "rw,noexec,nosuid,size=65536k"}
    assert api_create["ulimits"] == [{"Name": "nproc", "Soft": 2048, "Hard": 4096}]
    assert api_create["security_opt"] == ["label=disable"]
    assert api_create["cap_add"] == ["NET_ADMIN"]
    assert api_create["cap_drop"] == ["AUDIT_WRITE"]
    assert api_create["devices"] == ["/dev/net/tun:/dev/net/tun:rwm"]
    assert api_create["labels"] == api_labels
    assert api_create["healthcheck"] == {
        "Test": ["CMD", "curl", "-f", "http://localhost:8000/health"]
    }
    worker_recreated = client.containers.get("release-stack-worker-1")
    assert worker_recreated.attrs["HostConfig"]["Mounts"] == worker_create["mounts"]
    assert worker_recreated.attrs["Mounts"] == [
        {
            "Destination": "/usr/share/nginx/html",
            "Type": "bind",
            "Source": "/srv/worker/public",
            "RW": False,
        },
        {
            "Destination": "/var/cache/nginx",
            "Type": "tmpfs",
            "Source": None,
            "RW": True,
            "TmpfsOptions": {"SizeBytes": 16777216, "Mode": 0o1777},
        },
    ]
    api_recreated = client.containers.get("release-stack-api-1")
    assert api_recreated.attrs["HostConfig"]["Mounts"] == api_create["mounts"]
    assert api_recreated.attrs["Mounts"] == [
        {
            "Destination": "/etc/nginx/conf.d",
            "Type": "bind",
            "Source": "/srv/api/conf.d",
            "RW": False,
        },
        {
            "Destination": "/tmp/nginx-tmpfs",
            "Type": "tmpfs",
            "Source": None,
            "RW": True,
            "TmpfsOptions": {"SizeBytes": 16777216, "Mode": 0o700},
        },
    ]
    assert client.networks.disconnect_calls == [
        ("release-stack_default", "release-stack-worker-1", True),
        ("frontend", "release-stack-api-1", True),
        ("release-stack_default", "release-stack-api-1", True),
    ]
    assert client.networks.connect_calls == [
        (
            "release-stack_default",
            "release-stack-worker-1",
            {"aliases": ["worker", "release-stack-worker-1"]},
        ),
        (
            "frontend",
            "release-stack-api-1",
            {
                "aliases": ["api", "release-stack-api-1"],
                "ipv4_address": "172.20.0.10",
            },
        ),
        (
            "release-stack_default",
            "release-stack-api-1",
            {"aliases": ["api", "release-stack-api-1"]},
        ),
    ]


@pytest.mark.asyncio
async def test_docker_compose_grouped_update_preserves_hostconfig_mounts_without_exposed_ports():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "web",
        "com.docker.compose.container-number": "1",
    }
    web = FakeContainer(
        "web-1",
        "release-stack-web-1",
        FakeImage(tags=["nginx:1.25"]),
        attrs={
            "Config": {
                "Image": "nginx:1.25",
                "Labels": labels,
                "ExposedPorts": {"80/tcp": {}, "8443/tcp": {}},
            },
            "HostConfig": {
                "PortBindings": {"8443/tcp": [{"HostIp": "", "HostPort": "18443"}]},
                "Binds": [],
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": "/srv/nginx/conf.d",
                        "Target": "/etc/nginx/conf.d",
                        "RW": False,
                        "BindOptions": {"Propagation": "rprivate"},
                    },
                    {
                        "Type": "volume",
                        "Name": "nginx-cache",
                        "Source": "nginx-cache",
                        "Target": "/var/cache/nginx",
                        "RW": True,
                        "VolumeOptions": {"NoCopy": True},
                    },
                    {
                        "Type": "tmpfs",
                        "Target": "/run/nginx",
                        "RW": True,
                        "TmpfsOptions": {"SizeBytes": 16777216, "Mode": 0o1777},
                    },
                ],
                "NetworkMode": "release-stack_default",
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            },
            "NetworkSettings": {"Networks": {}},
        },
    )
    client = FakeDockerRecreateClient([web])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "release-stack"},
        {"web": "nginx:1.26"},
    )

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["ports"] == {"8443/tcp": ("", 18443)}
    assert "80/tcp" not in create_kwargs["ports"]
    assert "volumes" not in create_kwargs
    assert create_kwargs["mounts"] == [
        {
            "Target": "/etc/nginx/conf.d",
            "Source": "/srv/nginx/conf.d",
            "Type": "bind",
            "ReadOnly": True,
            "BindOptions": {"Propagation": "rprivate"},
        },
        {
            "Target": "/var/cache/nginx",
            "Source": "nginx-cache",
            "Type": "volume",
            "ReadOnly": False,
            "VolumeOptions": {"NoCopy": True},
        },
        {
            "Target": "/run/nginx",
            "Type": "tmpfs",
            "Source": None,
            "ReadOnly": False,
            "TmpfsOptions": {"SizeBytes": 16777216, "Mode": 0o1777},
        },
    ]


@pytest.mark.asyncio
async def test_docker_compose_grouped_update_preserves_container_network_mode_without_network_reattach():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "sidecar",
        "com.docker.compose.container-number": "1",
    }
    sidecar = FakeContainer(
        "sidecar-1",
        "release-stack-sidecar-1",
        FakeImage(tags=["ghcr.io/acme/sidecar:1.0"]),
        attrs={
            "Config": {
                "Image": "ghcr.io/acme/sidecar:1.0",
                "Env": ["SIDECAR_MODE=watch"],
                "Labels": labels,
            },
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "container:release-stack-db-1",
            },
            "NetworkSettings": {
                "Networks": {
                    "release-stack_default": {
                        "Aliases": ["sidecar", "release-stack-sidecar-1", "sidecar-1"],
                        "Links": [],
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    client = FakeDockerRecreateClient([sidecar], network_names=["release-stack_default"])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "release-stack"},
        {"sidecar": "ghcr.io/acme/sidecar:1.1"},
    )

    assert result.updated is True
    assert client.containers.create_calls == [
        {
            "image": "ghcr.io/acme/sidecar:1.1",
            "name": "release-stack-sidecar-1",
            "environment": ["SIDECAR_MODE=watch"],
            "restart_policy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "network_mode": "container:release-stack-db-1",
            "labels": labels,
        }
    ]
    assert client.networks.disconnect_calls == []
    assert client.networks.connect_calls == []


@pytest.mark.asyncio
async def test_docker_compose_grouped_update_orders_only_targeted_specs_and_ignores_non_target_dependencies():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    db_labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "db",
        "com.docker.compose.container-number": "1",
    }
    worker_labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "worker",
        "com.docker.compose.container-number": "1",
        "com.docker.compose.depends_on": "db:service_started",
    }
    api_labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "api",
        "com.docker.compose.container-number": "1",
        "com.docker.compose.depends_on": "worker:service_started,db:service_started",
    }

    db = FakeContainer(
        "db-1",
        "release-stack-db-1",
        FakeImage(tags=["sample-db:16"]),
        attrs={
            "Config": {"Image": "sample-db:16", "Labels": db_labels},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {},
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {"Networks": {}},
        },
    )
    worker = FakeContainer(
        "worker-1",
        "release-stack-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={
            "Config": {"Image": "ghcr.io/acme/worker:9.1", "Labels": worker_labels},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {},
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {"Networks": {}},
        },
    )
    api = FakeContainer(
        "api-1",
        "release-stack-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.2.3"]),
        attrs={
            "Config": {"Image": "ghcr.io/acme/api:1.2.3", "Labels": api_labels},
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {},
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {"Networks": {}},
        },
    )
    client = FakeDockerRecreateClient([api, worker, db])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "release-stack"},
        {
            "api": "ghcr.io/acme/api:1.2.4",
            "worker": "ghcr.io/acme/worker:9.2",
        },
    )

    assert result.updated is True
    assert [call["name"] for call in client.containers.create_calls] == [
        "release-stack-worker-1",
        "release-stack-api-1",
    ]
    assert client.containers.event_log == [
        "stop:release-stack-api-1",
        "stop:release-stack-worker-1",
        "remove:release-stack-api-1",
        "remove:release-stack-worker-1",
        "create:release-stack-worker-1",
        "start:release-stack-worker-1",
        "create:release-stack-api-1",
        "start:release-stack-api-1",
    ]
    assert db.stop_calls == []
    assert db.remove_calls == []


@pytest.mark.asyncio
async def test_docker_compose_grouped_update_recovers_recreated_services_after_partial_failure():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    worker_labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "worker",
        "com.docker.compose.container-number": "1",
    }
    api_labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "api",
        "com.docker.compose.container-number": "1",
        "com.docker.compose.depends_on": "worker:service_started",
    }
    worker = FakeContainer(
        "worker-1",
        "release-stack-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={
            "Config": {
                "Image": "ghcr.io/acme/worker:9.1",
                "Labels": worker_labels,
            },
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {},
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {"Networks": {}},
        },
    )
    api = FakeContainer(
        "api-1",
        "release-stack-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.2.3"]),
        attrs={
            "Config": {
                "Image": "ghcr.io/acme/api:1.2.3",
                "Labels": api_labels,
            },
            "HostConfig": {
                "PortBindings": {},
                "Binds": [],
                "RestartPolicy": {},
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {"Networks": {}},
        },
    )
    client = FakeDockerRecreateClient([api, worker])
    client.containers.fail_start_for_images.add("ghcr.io/acme/api:1.2.4")
    adapter = DockerRuntimeAdapter(runtime, client=client)

    with pytest.raises(
        RuntimeMutationError,
        match="docker compose grouped update failed after destructive steps began and recovery succeeded best-effort",
    ) as exc_info:
        await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "release-stack"},
            {
                "api": "ghcr.io/acme/api:1.2.4",
                "worker": "ghcr.io/acme/worker:9.2",
            },
        )

    assert exc_info.value.destructive_started is True
    assert [call["image"] for call in client.containers.create_calls] == [
        "ghcr.io/acme/worker:9.2",
        "ghcr.io/acme/api:1.2.4",
        "ghcr.io/acme/worker:9.1",
        "ghcr.io/acme/api:1.2.3",
    ]
    assert client.containers.event_log == [
        "stop:release-stack-api-1",
        "stop:release-stack-worker-1",
        "remove:release-stack-api-1",
        "remove:release-stack-worker-1",
        "create:release-stack-worker-1",
        "start:release-stack-worker-1",
        "create:release-stack-api-1",
        "start:release-stack-api-1",
        "remove:release-stack-worker-1",
        "create:release-stack-worker-1",
        "start:release-stack-worker-1",
        "remove:release-stack-api-1",
        "create:release-stack-api-1",
        "start:release-stack-api-1",
    ]
    assert len(client.containers._created) == 4
    assert len(client.containers._created[0].remove_calls) == 1
    assert len(client.containers._created[1].remove_calls) == 1


@pytest.mark.asyncio
async def test_docker_compose_grouped_update_recovery_uses_snapshot_payload_not_failed_replacement():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "api",
        "com.docker.compose.container-number": "1",
    }
    api = FakeContainer(
        "api-1",
        "release-stack-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.2.3"]),
        attrs={
            "Config": {
                "Image": "ghcr.io/acme/api:1.2.3",
                "Env": ["APP_MODE=stable"],
                "Labels": labels,
            },
            "HostConfig": {
                "PortBindings": {
                    "8000/tcp": [
                        {"HostIp": "", "HostPort": "8080"},
                        {"HostIp": "127.0.0.1", "HostPort": "18080"},
                    ]
                },
                "Binds": ["/srv/api:/app/data:ro"],
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "release-stack_default",
            },
            "NetworkSettings": {
                "Networks": {
                    "release-stack_default": {
                        "Aliases": ["api", "release-stack-api-1", "api-1"],
                        "Links": [],
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    client = FakeDockerRecreateClient([api], network_names=["release-stack_default"])
    client.containers.fail_start_for_images.add("ghcr.io/acme/api:1.2.4")
    adapter = DockerRuntimeAdapter(runtime, client=client)

    with pytest.raises(RuntimeMutationError, match="recovery succeeded best-effort"):
        await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "release-stack"},
            {"api": "ghcr.io/acme/api:1.2.4"},
        )

    assert client.containers.create_calls[0]["image"] == "ghcr.io/acme/api:1.2.4"
    assert client.containers.create_calls[1] == {
        "image": "ghcr.io/acme/api:1.2.3",
        "name": "release-stack-api-1",
        "environment": ["APP_MODE=stable"],
        "ports": {"8000/tcp": [("", 8080), ("127.0.0.1", 18080)]},
        "volumes": {"/srv/api": {"bind": "/app/data", "mode": "ro"}},
        "restart_policy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        "network_mode": "release-stack_default",
        "labels": labels,
    }
    assert client.networks.disconnect_calls == [
        ("release-stack_default", "release-stack-api-1", True),
        ("release-stack_default", "release-stack-api-1", True),
    ]
    assert client.networks.connect_calls == [
        (
            "release-stack_default",
            "release-stack-api-1",
            {"aliases": ["api", "release-stack-api-1"]},
        ),
        (
            "release-stack_default",
            "release-stack-api-1",
            {"aliases": ["api", "release-stack-api-1"]},
        ),
    ]


@pytest.mark.asyncio
async def test_docker_compose_grouped_update_fails_fast_for_active_runtime_endpoint_proxy():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "tcp://127.0.0.1:2375"},
        secrets={},
    )
    labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "socket-proxy",
    }
    proxy = FakeContainer(
        "proxy-1",
        "release-stack-socket-proxy-1",
        FakeImage(tags=["ghcr.io/acme/socket-proxy:1.0"]),
        attrs={
            "Config": {"Image": "ghcr.io/acme/socket-proxy:1.0", "Labels": labels},
            "HostConfig": {
                "PortBindings": {"2375/tcp": [{"HostIp": "127.0.0.1", "HostPort": "2375"}]}
            },
        },
    )
    client = FakeDockerRecreateClient([proxy])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    with pytest.raises(ValueError, match="publishes active runtime endpoint"):
        await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "release-stack"},
            {"socket-proxy": "ghcr.io/acme/socket-proxy:1.1"},
        )

    assert client.images.pull_calls == []
    assert client.containers.create_calls == []
    assert client.containers.event_log == []


@pytest.mark.asyncio
async def test_docker_compose_grouped_update_rejects_inconsistent_replica_images_before_pull():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    labels = {
        "com.docker.compose.project": "release-stack",
        "com.docker.compose.service": "api",
    }
    containers = [
        FakeContainer(
            "api-1",
            "release-stack-api-1",
            FakeImage(tags=["ghcr.io/acme/api:1.0"]),
            attrs={"Config": {"Image": "ghcr.io/acme/api:1.0", "Labels": labels}, "HostConfig": {}},
        ),
        FakeContainer(
            "api-2",
            "release-stack-api-2",
            FakeImage(tags=["ghcr.io/acme/api:1.1"]),
            attrs={"Config": {"Image": "ghcr.io/acme/api:1.1", "Labels": labels}, "HostConfig": {}},
        ),
    ]
    client = FakeDockerRecreateClient(containers)
    adapter = DockerRuntimeAdapter(runtime, client=client)

    with pytest.raises(ValueError, match="inconsistent replica images"):
        await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "release-stack"},
            {"api": "ghcr.io/acme/api:1.2"},
        )

    assert client.images.pull_calls == []
    assert client.containers.create_calls == []


@pytest.mark.asyncio
async def test_podman_compose_fetch_images_returns_service_image_map():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "worker",
    }
    container = FakeContainer(
        "worker-1",
        "core-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={"Pod": "", "Config": {"Labels": labels}, "HostConfig": {}},
    )
    adapter = PodmanRuntimeAdapter(runtime, client=FakePodmanClient([container]))

    images = await adapter.fetch_compose_service_images(
        {"mode": "docker_compose", "project": "core"}
    )

    assert images == {"worker": "ghcr.io/acme/worker:9.1"}


@pytest.mark.asyncio
async def test_podman_compose_fetch_images_reads_attrs_level_labels():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    attrs_labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "agent",
    }
    container = FakeContainer(
        "agent-1",
        "core-agent-1",
        FakeImage(tags=["ghcr.io/acme/agent:2.1"]),
        attrs={"Pod": "", "labels": attrs_labels, "Config": {}, "HostConfig": {}},
    )
    adapter = PodmanRuntimeAdapter(runtime, client=FakePodmanClient([container]))

    images = await adapter.fetch_compose_service_images(
        {"mode": "docker_compose", "project": "core"}
    )

    assert images == {"agent": "ghcr.io/acme/agent:2.1"}


@pytest.mark.asyncio
async def test_podman_compose_fetch_images_falls_back_to_get_for_complete_inspect_labels():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    summary_container = FakeContainer(
        "47e2f891",
        "sample-worker",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "",
            "Labels": {
                "io.container.manager": "libpod",
                "io.container.image": "ghcr.io/acme/agent:3.0",
                "io.podman.compose.project": "sample-worker",
                "com.docker.compose.service": "",
            },
            "HostConfig": {},
        },
    )
    full_container = FakeContainer(
        "47e2f891",
        "sample-worker",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "",
            "Config": {
                "Labels": {
                    "io.podman.compose.project": "sample-worker",
                    "com.docker.compose.service": "agent",
                }
            },
            "HostConfig": {},
        },
    )

    class SummaryOnlyPodmanContainerManager:
        def __init__(self):
            self.get_calls: list[str] = []

        def list(self, all=True):
            return [summary_container]

        def get(self, identifier: str):
            self.get_calls.append(identifier)
            if identifier in (summary_container.id, summary_container.name):
                return full_container
            raise KeyError("container not found")

    class SummaryOnlyPodmanClient:
        def __init__(self):
            self.containers = SummaryOnlyPodmanContainerManager()

    client = SummaryOnlyPodmanClient()
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    images = await adapter.fetch_compose_service_images(
        {"mode": "docker_compose", "project": "sample-worker"}
    )

    assert images == {"agent": "ghcr.io/acme/agent:3.0"}
    assert client.containers.get_calls == ["47e2f891"]


def test_podman_compose_find_service_containers_returns_full_inspect_container_on_fallback():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    summary_container = FakeContainer(
        "47e2f891",
        "sample-worker",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "pod-sample-ci",
            "Labels": {
                "io.podman.compose.project": "sample-worker",
                "com.docker.compose.service": "",
            },
        },
    )
    full_container = FakeContainer(
        "47e2f891",
        "sample-worker",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "pod-sample-ci",
            "Config": {
                "Image": "ghcr.io/acme/agent:3.0",
                "Labels": {
                    "io.podman.compose.project": "sample-worker",
                    "com.docker.compose.service": "agent",
                },
            },
            "HostConfig": {"Binds": ["/data:/work:Z"]},
            "NetworkSettings": {"Networks": {"podman": {"IPAddress": "10.88.0.9"}}},
        },
    )

    class SummaryOnlyPodmanContainerManager:
        def __init__(self):
            self.get_calls: list[str] = []

        def list(self, all=True):
            return [summary_container]

        def get(self, identifier: str):
            self.get_calls.append(identifier)
            if identifier in (summary_container.id, summary_container.name):
                return full_container
            raise KeyError("container not found")

    class SummaryOnlyPodmanClient:
        def __init__(self):
            self.containers = SummaryOnlyPodmanContainerManager()

    client = SummaryOnlyPodmanClient()
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    containers_by_service = adapter._find_compose_service_containers("sample-worker")

    assert list(containers_by_service.keys()) == ["agent"]
    assert containers_by_service["agent"] == [full_container]
    assert "Config" in containers_by_service["agent"][0].attrs
    assert "HostConfig" in containers_by_service["agent"][0].attrs
    assert "NetworkSettings" in containers_by_service["agent"][0].attrs
    assert client.containers.get_calls == ["47e2f891"]


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_recreates_pod_backed_targets_in_same_pod():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    worker_labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "worker",
        "custom.label": "keep-me",
    }
    api_labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "api",
    }
    worker = FakeContainer(
        "worker-1",
        "core-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "Config": {"Image": "ghcr.io/acme/worker:9.1", "Labels": worker_labels},
            "HostConfig": {"Binds": ["/data/podman/sample-worker/agent:/home/sample-ci/agent:Z"]},
            "NetworkSettings": {
                "Networks": {
                    "sample-edge": {
                        "Aliases": ["worker", "core-worker-1"],
                        "Links": [],
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    api = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "Config": {"Image": "ghcr.io/acme/api:1.0", "Labels": api_labels},
            "HostConfig": {},
            "NetworkSettings": {
                "Networks": {
                    "sample-edge": {
                        "Aliases": ["api", "core-api-1"],
                        "Links": [],
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    client = FakePodmanClient([worker, api], network_names=["sample-edge", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "core"},
        {
            "worker": "ghcr.io/acme/worker:9.2",
            "api": "ghcr.io/acme/api:1.1",
        },
    )

    assert result.updated is True
    assert "pod-aware update completed" in (result.message or "")
    assert client.images.pull_calls == ["ghcr.io/acme/api:1.1", "ghcr.io/acme/worker:9.2"]
    assert worker.stop_calls == [True]
    assert worker.remove_calls == [True]
    assert api.stop_calls == [True]
    assert api.remove_calls == [True]
    assert client.api.post_calls
    assert client.containers.create_calls == [
        {
            "image": "ghcr.io/acme/api:1.1",
            "name": "core-api-1",
            "labels": api_labels,
            "pod": "core-pod",
        },
        {
            "image": "ghcr.io/acme/worker:9.2",
            "name": "core-worker-1",
            "mounts": [
                {
                    "destination": "/home/sample-ci/agent",
                    "options": ["Z"],
                    "source": "/data/podman/sample-worker/agent",
                    "type": "bind",
                }
            ],
            "labels": worker_labels,
            "pod": "core-pod",
        },
    ]
    for create_kwargs in client.containers.create_calls:
        _assert_podman_pod_member_create_kwargs(create_kwargs)
    assert client.pods.remove_calls == [("core-pod", True), ("core-pod", True)]
    assert client.pods.create_calls == [
        {
            "name": "core-pod",
            "infra": False,
            "share": "net",
            "networks": {"sample-edge": {"aliases": ["api", "core-api-1"]}},
        },
        {
            "name": "core-pod",
            "infra": False,
            "share": "net",
            "networks": {"sample-edge": {"aliases": ["worker", "core-worker-1"]}},
        },
    ]
    assert client.networks.disconnect_calls == []
    assert client.networks.connect_calls == []


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_uses_low_level_create_after_empty_network_sanitization(
    caplog,
):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "api",
    }
    api = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "0123456789abcdef",
            "PodName": "core-pod",
            "Config": {
                "Image": "ghcr.io/acme/api:1.0",
                "Env": ["API_TOKEN=super-secret-token"],
                "Labels": labels,
            },
            "HostConfig": {"NetworkMode": ""},
        },
    )
    client = FakePodmanClient([api])
    client.api.require_compatible_false = True
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "core"},
            {"api": "ghcr.io/acme/api:1.1"},
        )

    assert result.updated is True
    assert client.api.post_calls
    assert client.api.post_calls[0]["kwargs"] == {"compatible": False}
    assert len(client.containers.create_calls) == 1
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["pod"] == "core-pod"
    _assert_podman_pod_member_create_kwargs(create_kwargs)
    assert "API_TOKEN" not in caplog.text
    assert "super-secret-token" not in caplog.text


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_resolves_pod_id_to_name_for_low_level_create():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "api",
    }
    api = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "0123456789abcdef",
            "Config": {"Image": "ghcr.io/acme/api:1.0", "Labels": labels},
            "HostConfig": {},
        },
    )
    client = FakePodmanClient([api])
    client.pods.add("core-pod", pod_id="0123456789abcdef")
    client.api.reject_pod_id_only_create.add("0123456789abcdef")
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "core"},
        {"api": "ghcr.io/acme/api:1.1"},
    )

    assert result.updated is True
    assert client.pods.get_calls == ["0123456789abcdef", "0123456789abcdef"]
    assert client.api.post_calls
    assert client.containers.create_calls == [
        {
            "image": "ghcr.io/acme/api:1.1",
            "name": "core-api-1",
            "labels": labels,
            "pod": "core-pod",
        }
    ]
    create_payload = client.api.post_calls[0]["data"]
    assert create_payload["pod"] == "core-pod"
    assert "netns" not in create_payload
    _assert_podman_pod_member_create_kwargs(create_payload)


@pytest.mark.asyncio
async def test_podman_compose_grouped_recovery_uses_low_level_create_after_empty_network_sanitization():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    client.api.require_compatible_false = True
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter._recover_grouped_container_from_snapshot(
        {
            "runtime_type": "podman",
            "container_id": "api-1",
            "container_name": "core-api-1",
            "image": "ghcr.io/acme/api:1.0",
            "create_config": {
                "image": "ghcr.io/acme/api:1.0",
                "name": "core-api-1",
                "env": {"API_TOKEN": "super-secret-token"},
            },
            "pod_id": "0123456789abcdef",
            "pod_name": "core-pod",
            "pod_relation_payload": {"pod_infra_id": "abcdef0123456789"},
        }
    )

    assert result.updated is True
    assert client.api.post_calls
    assert client.api.post_calls[0]["kwargs"] == {"compatible": False}
    assert len(client.containers.create_calls) == 1
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["pod"] == "core-pod"
    _assert_podman_pod_member_create_kwargs(create_kwargs)


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_drops_blank_storage_destinations_from_payload(
    caplog,
):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "api",
    }
    api = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "Config": {"Image": "ghcr.io/acme/api:1.0", "Labels": labels},
            "HostConfig": {},
        },
    )
    client = FakePodmanClient([api])
    adapter = PodmanRuntimeAdapter(runtime, client=client)
    spec = GroupedRuntimeRecreateSpec(
        runtime_type="podman",
        container_id="api-1",
        container_name="core-api-1",
        compose_project="core",
        compose_service="api",
        current_image="ghcr.io/acme/api:1.0",
        target_image="ghcr.io/acme/api:1.1",
        create_config={
            "image": "ghcr.io/acme/api:1.1",
            "name": "core-api-1",
            "labels": labels,
            "volumes": [
                {"name": "bad-volume", "dest": ""},
                {"name": "good-volume", "dest": "/data", "options": ["rw"]},
            ],
            "mounts": [
                {"type": "tmpfs", "target": ""},
                {"type": "bind", "source": "/srv/config", "target": "/config"},
            ],
        },
        host_config={},
        network_config={},
        snapshot_payload={
            "runtime_type": "podman",
            "container_id": "api-1",
            "container_name": "core-api-1",
            "image": "ghcr.io/acme/api:1.0",
            "create_config": {
                "image": "ghcr.io/acme/api:1.0",
                "name": "core-api-1",
                "labels": labels,
            },
            "pod_id": "pod-core",
            "pod_name": "core-pod",
        },
        restore_payload={},
        pod_id="pod-core",
        pod_name="core-pod",
        pod_relation_payload={"pod_id": "pod-core", "pod_name": "core-pod"},
        dependencies=(),
    )
    adapter._build_grouped_runtime_recreate_specs = lambda service_containers, update_plan: [spec]

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "core"},
            {"api": "ghcr.io/acme/api:1.1"},
        )

    assert result.updated is True
    payload = client.api.post_calls[0]["data"]
    assert "volumes" not in payload
    assert payload["mounts"] == [
        {"destination": "/config", "source": "/srv/config", "type": "bind"}
    ]
    assert "volumes" not in client.containers.create_calls[0]
    assert client.containers.create_calls[0]["mounts"] == payload["mounts"]
    assert "/srv/config" not in caplog.text
    assert "/data" not in caplog.text


@pytest.mark.asyncio
async def test_podman_compose_grouped_recovery_drops_blank_storage_destinations_from_payload(
    caplog,
):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter._recover_grouped_container_from_snapshot(
            {
                "runtime_type": "podman",
                "container_id": "api-1",
                "container_name": "core-api-1",
                "image": "ghcr.io/acme/api:1.0",
                "create_config": {
                    "image": "ghcr.io/acme/api:1.0",
                    "name": "core-api-1",
                    "volumes": [
                        {"name": "bad-volume", "destination": ""},
                        {"name": "good-volume", "dest": "/data", "options": ["rw"]},
                    ],
                    "mounts": [
                        {"type": "tmpfs", "target": "   ", "size": "16m"},
                        {"type": "tmpfs", "target": "/tmp", "size": "16m"},
                    ],
                },
                "pod_id": "pod-core",
                "pod_name": "core-pod",
            }
        )

    assert result.updated is True
    payload = client.api.post_calls[0]["data"]
    assert "volumes" not in payload
    assert payload["mounts"] == [{"destination": "/tmp", "options": ["size=16m"], "type": "tmpfs"}]
    assert "volumes" not in client.containers.create_calls[0]
    assert client.containers.create_calls[0]["mounts"] == payload["mounts"]
    assert "/data" not in caplog.text
    assert "/tmp" not in caplog.text


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_tolerates_stop_json_decode_error_when_container_already_stopped():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "api",
    }
    api = FakePodmanStopDecodeErrorContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "State": {"Running": True},
            "Config": {"Image": "ghcr.io/acme/api:1.0", "Labels": labels},
            "HostConfig": {},
        },
    )
    client = FakePodmanClient([api])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "core"},
        {"api": "ghcr.io/acme/api:1.1"},
    )

    assert result.updated is True
    assert api.stop_calls == [True]
    assert api.remove_calls == [True]
    assert client.api.post_calls
    payloads = client.containers.create_calls
    assert payloads == [
        {
            "image": "ghcr.io/acme/api:1.1",
            "name": "core-api-1",
            "labels": labels,
            "pod": "core-pod",
        }
    ]
    for payload in payloads:
        _assert_podman_pod_member_create_kwargs(payload)


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_tolerates_remove_json_decode_error_when_container_already_removed():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "api",
    }
    api = FakePodmanRemoveDecodeErrorContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "Config": {"Image": "ghcr.io/acme/api:1.0", "Labels": labels},
            "HostConfig": {},
        },
    )
    client = FakePodmanClient([api])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "core"},
        {"api": "ghcr.io/acme/api:1.1"},
    )

    assert result.updated is True
    assert api.stop_calls == [True]
    assert api.remove_calls == [True]
    assert client.api.post_calls
    payloads = client.containers.create_calls
    assert payloads == [
        {
            "image": "ghcr.io/acme/api:1.1",
            "name": "core-api-1",
            "labels": labels,
            "pod": "core-pod",
        }
    ]
    for payload in payloads:
        _assert_podman_pod_member_create_kwargs(payload)


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_injects_fallback_compose_labels_into_replacement_create_config():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    summary_labels = {
        "io.podman.compose.project": "sample-worker",
        "com.docker.compose.service": "",
    }
    full_labels = {
        "com.docker.compose.project": "sample-worker",
        "com.docker.compose.service": "agent",
        "com.docker.compose.container-number": "1",
        "io.podman.compose.project": "sample-worker",
        "io.podman.compose.version": "1.0.6",
        "com.docker.compose.oneoff": "False",
        "custom.label": "keep-me",
    }
    listed_summary = FakeContainer(
        "agent-1",
        "sample-worker",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "pod-sample-ci",
            "PodName": "sample-ci-pod",
            "Config": {"Image": "ghcr.io/acme/agent:3.0", "Labels": summary_labels},
            "HostConfig": {},
        },
    )
    stored_full = FakeContainer(
        "agent-1",
        "sample-worker",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "pod-sample-ci",
            "PodName": "sample-ci-pod",
            "Config": {"Image": "ghcr.io/acme/agent:3.0", "Labels": full_labels},
            "HostConfig": {},
        },
    )

    client = FakePodmanClient([stored_full])
    default_list = client.containers.list
    client.containers.list = lambda all=True: [listed_summary]
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "sample-worker"},
        {"agent": "ghcr.io/acme/agent:3.1"},
    )
    client.containers.list = default_list
    images = await adapter.fetch_compose_service_images(
        {"mode": "docker_compose", "project": "sample-worker"}
    )

    assert result.updated is True
    assert client.api.post_calls
    payloads = client.containers.create_calls
    assert payloads == [
        {
            "image": "ghcr.io/acme/agent:3.1",
            "name": "sample-worker",
            "labels": full_labels,
            "pod": "sample-ci-pod",
        }
    ]
    for payload in payloads:
        _assert_podman_pod_member_create_kwargs(payload)
    assert images == {"agent": "ghcr.io/acme/agent:3.1"}


@pytest.mark.asyncio
async def test_podman_compose_grouped_spec_includes_pod_metadata_payload():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "worker",
    }
    pod_member = FakeContainer(
        "worker-1",
        "core-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "PodInfraId": "infra-123",
            "PodInfraName": "core-pod-infra",
            "Config": {
                "Image": "ghcr.io/acme/worker:9.1",
                "Labels": labels,
            },
            "HostConfig": {},
        },
    )
    adapter = PodmanRuntimeAdapter(runtime, client=FakePodmanClient([pod_member]))

    specs = adapter._build_grouped_runtime_recreate_specs(
        {"worker": [pod_member]},
        {"worker": "ghcr.io/acme/worker:9.2"},
    )

    assert len(specs) == 1
    spec = specs[0]
    assert spec.pod_id == "pod-core"
    assert spec.pod_name == "core-pod"
    assert spec.pod_relation_payload == {
        "pod_id": "pod-core",
        "pod_name": "core-pod",
        "pod_infra_id": "infra-123",
        "pod_infra_name": "core-pod-infra",
        "pod_create_infra": True,
    }
    assert spec.snapshot_payload["pod_id"] == "pod-core"
    assert spec.snapshot_payload["pod_name"] == "core-pod"
    assert spec.snapshot_payload["pod_relation_payload"] == spec.pod_relation_payload
    assert spec.restore_payload["pod_id"] == "pod-core"
    assert spec.restore_payload["pod_name"] == "core-pod"
    assert spec.restore_payload["pod_relation_payload"] == spec.pod_relation_payload


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_preserves_no_infra_when_shared_namespaces_omitted():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "nginx",
        "com.docker.compose.service": "nginx",
    }
    nginx = FakeContainer(
        "nginx-1",
        "nginx-podman-compose",
        FakeImage(tags=["docker.io/library/nginx:1.25"]),
        attrs={
            "Pod": "nginx-podman-compose",
            "PodName": "nginx-podman-compose",
            "Config": {
                "Image": "docker.io/library/nginx:1.25",
                "Labels": labels,
                "Hostname": "nginx-host",
            },
            "HostConfig": {
                "NetworkMode": "",
                "PortBindings": {"8080/tcp": [{"HostIp": "", "HostPort": "18080"}]},
                "Dns": ["1.1.1.1"],
                "DnsSearch": ["svc.local"],
                "ExtraHosts": ["api.local:10.88.0.10"],
                "ShmSize": 67108864,
            },
            "NetworkSettings": {
                "Networks": {
                    "nginx-net": {
                        "Aliases": ["nginx", "nginx-podman-compose"],
                        "IPAddress": "10.88.0.20",
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    client = FakePodmanClient([nginx], network_names=["nginx-net", "podman"])
    client.pods.add(
        "nginx-podman-compose",
        attrs={"CreateInfra": False},
    )
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "nginx"},
        {"nginx": "docker.io/library/nginx:1.26"},
    )

    assert result.updated is True
    assert client.api.pod_create_posts == [
        {
            "path": "pods/create",
            "data": {
                "name": "nginx-podman-compose",
                "no_infra": True,
            },
            "headers": {"content-type": "application/json"},
            "kwargs": {"compatible": False},
        }
    ]
    pod_create_payload = client.api.pod_create_posts[0]["data"]
    assert "infra" not in pod_create_payload
    assert "share" not in pod_create_payload
    assert "shared_namespaces" not in pod_create_payload
    for key in (
        "networks",
        "portmappings",
        "dns",
        "dns_search",
        "hostadd",
        "hostname",
        "shm_size",
    ):
        assert key not in pod_create_payload
    assert client.pods.create_calls == [
        {
            "name": "nginx-podman-compose",
            "no_infra": True,
        }
    ]
    assert client.containers.create_calls == [
        {
            "image": "docker.io/library/nginx:1.26",
            "name": "nginx-podman-compose",
            "hostname": "nginx-host",
            "portmappings": [{"container_port": 8080, "protocol": "tcp", "host_port": 18080}],
            "hostadd": ["api.local:10.88.0.10"],
            "dns_server": ["1.1.1.1"],
            "dns_search": ["svc.local"],
            "labels": labels,
            "shm_size": 67108864,
            "pod": "nginx-podman-compose",
        }
    ]
    assert client.api.network_connect_posts == [
        {
            "path": "networks/nginx-net/connect",
            "data": {
                "container": "new-id",
                "aliases": ["nginx", "nginx-podman-compose"],
                "static_ips": ["10.88.0.20"],
            },
            "headers": {"Content-Type": "application/json"},
            "kwargs": {"compatible": False},
        }
    ]


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_preserves_no_infra_empty_share_pod_topology():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "nginx",
        "com.docker.compose.service": "nginx",
    }
    nginx = FakeContainer(
        "nginx-1",
        "nginx-podman-compose",
        FakeImage(tags=["docker.io/library/nginx:1.25"]),
        attrs={
            "Pod": "nginx-podman-compose",
            "PodName": "nginx-podman-compose",
            "Config": {
                "Image": "docker.io/library/nginx:1.25",
                "Labels": labels,
                "Hostname": "nginx-host",
            },
            "HostConfig": {
                "NetworkMode": "",
                "PortBindings": {"8080/tcp": [{"HostIp": "", "HostPort": "18080"}]},
                "Dns": ["1.1.1.1"],
                "DnsSearch": ["svc.local"],
                "ExtraHosts": ["api.local:10.88.0.10"],
                "ShmSize": 67108864,
            },
            "NetworkSettings": {
                "Networks": {
                    "nginx-net": {
                        "Aliases": ["nginx", "nginx-podman-compose"],
                        "IPAddress": "10.88.0.20",
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    client = FakePodmanClient([nginx], network_names=["nginx-net", "podman"])
    client.pods.add(
        "nginx-podman-compose",
        attrs={"CreateInfra": False, "SharedNamespaces": []},
    )
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "nginx"},
        {"nginx": "docker.io/library/nginx:1.26"},
    )

    assert result.updated is True
    assert client.api.pod_create_posts == [
        {
            "path": "pods/create",
            "data": {
                "name": "nginx-podman-compose",
                "no_infra": True,
                "shared_namespaces": [],
            },
            "headers": {"content-type": "application/json"},
            "kwargs": {"compatible": False},
        }
    ]
    pod_create_payload = client.api.pod_create_posts[0]["data"]
    assert "infra" not in pod_create_payload
    assert "share" not in pod_create_payload
    for key in (
        "networks",
        "portmappings",
        "dns",
        "dns_search",
        "hostadd",
        "hostname",
        "shm_size",
    ):
        assert key not in pod_create_payload
    assert client.pods.create_calls == [
        {
            "name": "nginx-podman-compose",
            "no_infra": True,
            "shared_namespaces": [],
        }
    ]
    assert client.containers.create_calls == [
        {
            "image": "docker.io/library/nginx:1.26",
            "name": "nginx-podman-compose",
            "hostname": "nginx-host",
            "portmappings": [{"container_port": 8080, "protocol": "tcp", "host_port": 18080}],
            "hostadd": ["api.local:10.88.0.10"],
            "dns_server": ["1.1.1.1"],
            "dns_search": ["svc.local"],
            "labels": labels,
            "shm_size": 67108864,
            "pod": "nginx-podman-compose",
        }
    ]
    assert client.pods.get_calls == ["nginx-podman-compose"]
    assert client.api.network_connect_posts == [
        {
            "path": "networks/nginx-net/connect",
            "data": {
                "container": "new-id",
                "aliases": ["nginx", "nginx-podman-compose"],
                "static_ips": ["10.88.0.20"],
            },
            "headers": {"Content-Type": "application/json"},
            "kwargs": {"compatible": False},
        }
    ]


@pytest.mark.asyncio
async def test_podman_adapter_recreates_container_preserving_compose_matrix_fields():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    container = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "",
            "Config": {
                "Env": ["APP_ENV=prod"],
                "Entrypoint": ["python", "-m"],
                "Cmd": ["app.main"],
                "User": "1000:1000",
                "WorkingDir": "/srv/app",
                "Hostname": "core-api",
                "Healthcheck": {"Test": ["CMD-SHELL", "curl -f http://127.0.0.1/health || exit 1"]},
                "Labels": {"app": "api"},
                "ExposedPorts": {"8000/tcp": {}, "9000/tcp": {}},
            },
            "HostConfig": {
                "PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "8080"}]},
                "Binds": ["/srv/api:/app/data:rw"],
                "LogConfig": {
                    "Type": "k8s-file",
                    "Config": {"path": "/var/log/api.log", "max-size": "10m"},
                },
                "RestartPolicy": {"Name": "always", "MaximumRetryCount": 0},
                "NetworkMode": "bridge",
                "ExtraHosts": ["db.internal:10.88.0.2"],
                "Dns": ["8.8.4.4"],
                "Tmpfs": {"/tmp": "rw,size=64m"},
                "Ulimits": [{"Name": "nofile", "Soft": 4096, "Hard": 8192}],
                "SecurityOpt": ["label=disable"],
                "CapAdd": ["NET_ADMIN"],
                "CapDrop": ["AUDIT_WRITE"],
                "Devices": ["/dev/net/tun:/dev/net/tun:rwm"],
            },
        },
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image({"container_name": "core-api-1"}, "ghcr.io/acme/api:1.1")

    assert result.updated is True
    assert client.containers.create_calls == [
        {
            "image": "ghcr.io/acme/api:1.1",
            "name": "core-api-1",
            "env": {"APP_ENV": "prod"},
            "entrypoint": ["python", "-m"],
            "command": ["app.main"],
            "user": "1000:1000",
            "work_dir": "/srv/app",
            "hostname": "core-api",
            "healthconfig": {"Test": ["CMD-SHELL", "curl -f http://127.0.0.1/health || exit 1"]},
            "portmappings": [{"container_port": 8000, "host_port": 8080, "protocol": "tcp"}],
            "mounts": [
                {
                    "destination": "/app/data",
                    "options": ["rw"],
                    "source": "/srv/api",
                    "type": "bind",
                },
                {"destination": "/tmp", "options": ["size=64m"], "type": "tmpfs"},
            ],
            "log_configuration": {
                "driver": "k8s-file",
                "path": "/var/log/api.log",
                "options": {"max-size": "10m"},
            },
            "restart_policy": "always",
            "restart_tries": 0,
            "netns": {"nsmode": "bridge"},
            "hostadd": ["db.internal:10.88.0.2"],
            "dns_server": ["8.8.4.4"],
            "r_limits": [{"hard": 8192, "soft": 4096, "type": "nofile"}],
            "selinux_opts": ["label=disable"],
            "cap_add": ["NET_ADMIN"],
            "cap_drop": ["AUDIT_WRITE"],
            "devices": [{"path": "/dev/net/tun:/dev/net/tun:rwm"}],
            "labels": {"app": "api"},
        }
    ]


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_preserves_high_fidelity_runtime_fields():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "web",
    }
    web = FakeContainer(
        "web-1",
        "core-web-1",
        FakeImage(tags=["ghcr.io/acme/web:1.0"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "Config": {
                "Image": "ghcr.io/acme/web:1.0",
                "Labels": labels,
                "Tty": True,
                "OpenStdin": False,
                "StopSignal": "SIGQUIT",
                "StopTimeout": 10,
                "Hostname": "core-web-host",
                "ExposedPorts": {"80/tcp": {}, "8080/tcp": {}},
            },
            "HostConfig": {
                "Binds": [],
                "PortBindings": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}]},
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "bridge",
                "Dns": ["1.1.1.1", "8.8.8.8"],
                "DnsSearch": ["local.test"],
                "ExtraHosts": ["host.containers.internal:host-gateway", "example.local:127.0.0.1"],
                "SecurityOpt": ["no-new-privileges"],
                "LogConfig": {
                    "Type": "journald",
                    "Config": {"mode": "non-blocking"},
                    "Tag": "core-web-1",
                },
                "Tmpfs": {"/tmp/nginx-tmpfs": "size=16777216,rw,rprivate"},
                "Memory": 134217728,
                "MemoryReservation": 67108864,
                "NanoCpus": 500000000,
                "CpuShares": 512,
                "PidsLimit": 256,
                "ShmSize": 67108864,
                "Ulimits": [{"Name": "RLIMIT_NOFILE", "Soft": 1024, "Hard": 2048}],
            },
            "NetworkSettings": {
                "Networks": {
                    "core-net": {
                        "Aliases": ["web", "web.local", "web-1", "core-web-1"],
                        "IPAddress": "10.89.10.10",
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    client = FakePodmanClient([web], network_names=["core-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "core"},
        {"web": "ghcr.io/acme/web:1.1"},
    )

    assert result.updated is True
    assert client.api.post_calls
    payloads = client.containers.create_calls
    assert payloads == [
        {
            "image": "ghcr.io/acme/web:1.1",
            "name": "core-web-1",
            "terminal": True,
            "stdin": False,
            "stop_signal": 3,
            "stop_timeout": 10,
            "restart_policy": "unless-stopped",
            "restart_tries": 0,
            "log_configuration": {
                "driver": "journald",
                "options": {"mode": "non-blocking", "tag": "core-web-1"},
            },
            "r_limits": [{"hard": 2048, "soft": 1024, "type": "nofile"}],
            "resource_limits": {
                "cpu": {"period": 100000, "quota": 50000, "shares": 512},
                "memory": {"limit": 134217728, "reservation": 67108864},
                "pids": {"limit": 256},
            },
            "labels": labels,
            "mounts": [
                {
                    "destination": "/tmp/nginx-tmpfs",
                    "options": ["size=16777216"],
                    "type": "tmpfs",
                }
            ],
            "no_new_privileges": True,
            "pod": "core-pod",
        }
    ]
    for payload in payloads:
        _assert_podman_pod_member_create_kwargs(payload)
    assert client.pods.create_calls == [
        {
            "name": "core-pod",
            "infra": False,
            "hostname": "core-web-host",
            "shm_size": 67108864,
            "portmappings": [
                {
                    "container_port": 8080,
                    "protocol": "tcp",
                    "host_ip": "0.0.0.0",
                    "host_port": 18080,
                }
            ],
            "hostadd": ["host.containers.internal:host-gateway", "example.local:127.0.0.1"],
            "dns": ["1.1.1.1", "8.8.8.8"],
            "dns_search": ["local.test"],
            "share": "net",
            "networks": {
                "core-net": {
                    "aliases": ["web", "web.local", "core-web-1"],
                    "static_ips": ["10.89.10.10"],
                }
            },
        }
    ]
    assert client.networks.connect_calls == []


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_normalizes_null_log_config_for_sdk_create():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "worker",
    }
    worker = FakeContainer(
        "worker-1",
        "core-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "Config": {"Image": "ghcr.io/acme/worker:9.1", "Labels": labels},
            "HostConfig": {
                "Binds": [],
                "RestartPolicy": {},
                "NetworkMode": "bridge",
                "LogConfig": {"Type": "journald", "Config": None},
            },
        },
    )
    client = FakePodmanClient([worker])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "core"},
        {"worker": "ghcr.io/acme/worker:9.2"},
    )

    assert result.updated is True
    assert client.api.post_calls
    payloads = client.containers.create_calls
    assert payloads == [
        {
            "image": "ghcr.io/acme/worker:9.2",
            "name": "core-worker-1",
            "labels": labels,
            "log_configuration": {"driver": "journald"},
            "pod": "core-pod",
        }
    ]
    for payload in payloads:
        _assert_podman_pod_member_create_kwargs(payload)


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_fails_fast_for_mixed_pod_topology_before_mutation():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "worker",
    }
    worker = FakeContainer(
        "worker-1",
        "core-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={
            "Pod": "pod-core",
            "Config": {"Image": "ghcr.io/acme/worker:9.1", "Labels": labels},
            "HostConfig": {},
        },
    )
    api = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "pod-another",
            "Config": {
                "Image": "ghcr.io/acme/api:1.0",
                "Labels": {
                    "io.podman.compose.project": "core",
                    "com.docker.compose.service": "api",
                },
            },
            "HostConfig": {},
        },
    )
    client = FakePodmanClient([worker, api])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with pytest.raises(
        ValueError,
        match="only supports targets in one pod",
    ):
        await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "core"},
            {
                "worker": "ghcr.io/acme/worker:9.2",
                "api": "ghcr.io/acme/api:1.1",
            },
        )

    assert client.images.pull_calls == []
    assert client.containers.create_calls == []
    assert client.api.post_calls == []
    assert worker.stop_calls == []
    assert worker.remove_calls == []
    assert api.stop_calls == []
    assert api.remove_calls == []


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_recovers_best_effort_after_partial_failure():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    worker_labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "worker",
    }
    api_labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "api",
    }
    worker = FakeContainer(
        "worker-1",
        "core-worker-1",
        FakeImage(tags=["ghcr.io/acme/worker:9.1"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "Config": {"Image": "ghcr.io/acme/worker:9.1", "Labels": worker_labels},
            "HostConfig": {"NetworkMode": "   "},
        },
    )
    api = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "pod-core",
            "Config": {"Image": "ghcr.io/acme/api:1.0", "Labels": api_labels},
            "HostConfig": {"NetworkMode": ""},
        },
    )
    client = FakePodmanClient([worker, api])
    client.containers.fail_start_for_images.add("ghcr.io/acme/worker:9.2")
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with pytest.raises(
        RuntimeMutationError,
        match="podman grouped compose update failed after destructive steps began and recovery succeeded best-effort",
    ) as exc_info:
        await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "core"},
            {
                "worker": "ghcr.io/acme/worker:9.2",
                "api": "ghcr.io/acme/api:1.1",
            },
        )

    assert exc_info.value.destructive_started is True
    assert [call["image"] for call in client.containers.create_calls] == [
        "ghcr.io/acme/api:1.1",
        "ghcr.io/acme/worker:9.2",
        "ghcr.io/acme/api:1.0",
        "ghcr.io/acme/worker:9.1",
    ]
    assert client.containers.create_calls[0]["pod"] == "pod-core"
    assert client.containers.create_calls[1]["pod"] == "core-pod"
    assert client.containers.create_calls[2]["pod"] == "pod-core"
    assert client.containers.create_calls[3]["pod"] == "core-pod"
    assert all("network_mode" not in call for call in client.containers.create_calls)
    assert client.containers.event_log == [
        "stop:core-api-1",
        "rename:core-api-1->core-api-1-rt-backup-api-1",
        "remove:core-api-1-rt-backup-api-1",
        "create:core-api-1",
        "start:core-api-1",
        "stop:core-worker-1",
        "rename:core-worker-1->core-worker-1-rt-backup-worker-1",
        "remove:core-worker-1-rt-backup-worker-1",
        "create:core-worker-1",
        "start:core-worker-1",
        "remove:core-api-1",
        "create:core-api-1",
        "start:core-api-1",
        "remove:core-worker-1",
        "create:core-worker-1",
        "start:core-worker-1",
    ]


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_recovery_uses_snapshot_payload_not_failed_replacement():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "core",
        "com.docker.compose.service": "api",
    }
    api = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "pod-core",
            "PodName": "core-pod",
            "Config": {
                "Image": "ghcr.io/acme/api:1.0",
                "Env": ["APP_MODE=stable"],
                "Labels": labels,
                "ExposedPorts": {"8000/tcp": {}},
            },
            "HostConfig": {
                "PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "8080"}]},
                "Binds": ["/srv/api:/app/data:ro"],
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakePodmanClient([api])
    client.containers.fail_start_for_images.add("ghcr.io/acme/api:1.1")
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with pytest.raises(RuntimeMutationError, match="recovery succeeded best-effort"):
        await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "core"},
            {"api": "ghcr.io/acme/api:1.1"},
        )

    assert client.api.post_calls
    payloads = client.containers.create_calls
    assert payloads[0] == {
        "image": "ghcr.io/acme/api:1.1",
        "name": "core-api-1",
        "env": {"APP_MODE": "stable"},
        "mounts": [
            {
                "type": "bind",
                "source": "/srv/api",
                "destination": "/app/data",
                "options": ["ro"],
            }
        ],
        "restart_policy": "unless-stopped",
        "restart_tries": 0,
        "labels": labels,
        "pod": "core-pod",
    }
    assert payloads[1] == {
        "image": "ghcr.io/acme/api:1.0",
        "name": "core-api-1",
        "env": {"APP_MODE": "stable"},
        "mounts": [
            {
                "type": "bind",
                "source": "/srv/api",
                "destination": "/app/data",
                "options": ["ro"],
            }
        ],
        "restart_policy": "unless-stopped",
        "restart_tries": 0,
        "labels": labels,
        "pod": "core-pod",
    }
    low_level_payloads = [call["data"] for call in client.api.post_calls]
    assert all(not isinstance(payload.get("volumes"), dict) for payload in low_level_payloads)
    for payload in payloads:
        _assert_podman_pod_member_create_kwargs(payload)


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_drops_empty_network_values_from_pod_payloads(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "nginx",
        "com.docker.compose.service": "nginx",
    }
    nginx = FakeContainer(
        "nginx-1",
        "nginx-podman-compose",
        FakeImage(tags=["docker.io/library/nginx:1.25"]),
        attrs={
            "Pod": "nginx-podman-compose",
            "PodName": "nginx-podman-compose",
            "Config": {
                "Image": "docker.io/library/nginx:1.25",
                "Env": ["API_TOKEN=super-secret-token"],
                "Labels": labels,
            },
            "HostConfig": {"NetworkMode": ""},
            "NetworkSettings": {
                "Networks": {
                    "": {
                        "Aliases": ["should-not-be-used"],
                        "IPAddress": "10.88.0.199",
                        "IPAMConfig": None,
                    },
                    "nginx-net": {
                        "Aliases": ["nginx", "nginx-podman-compose"],
                        "IPAddress": "10.88.0.20",
                        "IPAMConfig": None,
                    },
                }
            },
        },
    )
    client = FakePodmanClient([nginx], network_names=["nginx-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.DEBUG, logger="releasetracker.executors.podman"):
        result = await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "nginx"},
            {"nginx": "docker.io/library/nginx:1.26"},
        )

    assert result.updated is True
    assert client.api.post_calls
    payloads = client.containers.create_calls
    assert payloads == [
        {
            "image": "docker.io/library/nginx:1.26",
            "name": "nginx-podman-compose",
            "env": {"API_TOKEN": "super-secret-token"},
            "labels": labels,
            "pod": "nginx-podman-compose",
        }
    ]
    for payload in payloads:
        _assert_podman_pod_member_create_kwargs(payload)
    assert all(
        "network_mode" not in call
        and "network" not in call
        and "networks" not in call
        and "netns" not in call
        for call in client.containers.create_calls
    )
    assert client.networks.connect_calls == []
    assert client.pods.create_calls == [
        {
            "name": "nginx-podman-compose",
            "infra": False,
            "share": "net",
            "networks": {
                "nginx-net": {
                    "aliases": ["nginx", "nginx-podman-compose"],
                    "static_ips": ["10.88.0.20"],
                }
            },
        }
    ]
    assert "boundary=networks.get" not in caplog.text
    assert "boundary=networks.connect" not in caplog.text
    assert "API_TOKEN" not in caplog.text
    assert "super-secret-token" not in caplog.text


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_uses_resolved_pod_object_without_container_network_kwargs():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "nginx",
        "com.docker.compose.service": "nginx",
    }
    nginx = FakeContainer(
        "nginx-1",
        "nginx-podman-compose",
        FakeImage(tags=["docker.io/library/nginx:1.25"]),
        attrs={
            "Pod": "nginx-podman-compose",
            "PodName": "nginx-podman-compose",
            "Config": {
                "Image": "docker.io/library/nginx:1.25",
                "Hostname": "nginx-host",
                "Labels": labels,
            },
            "HostConfig": {
                "PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "8080"}]},
                "NetworkMode": "",
                "Dns": ["1.1.1.1"],
                "DnsSearch": ["svc.local"],
                "ExtraHosts": ["api.local:10.88.0.10"],
                "ShmSize": 67108864,
            },
            "NetworkSettings": {
                "Networks": {
                    "nginx-podman-net": {
                        "Aliases": ["nginx", "nginx-podman-compose"],
                        "IPAddress": "10.88.0.20",
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    client = FakePodmanClient([nginx], network_names=["nginx-podman-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "nginx"},
        {"nginx": "docker.io/library/nginx:1.26"},
    )

    assert result.updated is True
    assert client.pods.create_calls == [
        {
            "name": "nginx-podman-compose",
            "infra": False,
            "hostname": "nginx-host",
            "shm_size": 67108864,
            "portmappings": [{"container_port": 80, "protocol": "tcp", "host_port": 8080}],
            "hostadd": ["api.local:10.88.0.10"],
            "dns": ["1.1.1.1"],
            "dns_search": ["svc.local"],
            "share": "net",
            "networks": {
                "nginx-podman-net": {
                    "aliases": ["nginx", "nginx-podman-compose"],
                    "static_ips": ["10.88.0.20"],
                }
            },
        }
    ]
    assert client.pods.get_calls == ["nginx-podman-compose"]
    assert client.api.post_calls
    assert len(client.containers.create_calls) == 1
    create_call = client.containers.create_calls[0]
    assert create_call["pod"] == "nginx-podman-compose"
    create_payload = client.api.post_calls[0]["data"]
    assert create_payload["pod"] == "nginx-podman-compose"
    assert "netns" not in create_call
    _assert_no_blank_podman_create_network_values(create_call)
    assert all(
        key not in create_call
        for key in (
            "ports",
            "portmappings",
            "network",
            "networks",
            "cni_networks",
            "network_mode",
            "dns",
            "dns_opt",
            "dns_search",
            "extra_hosts",
            "hostname",
            "shm_size",
        )
    )


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_uses_resolved_recreated_pod_after_none_create(
    caplog,
):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "nginx",
        "com.docker.compose.service": "nginx",
    }
    nginx = FakeContainer(
        "nginx-1",
        "nginx-podman-compose",
        FakeImage(tags=["docker.io/library/nginx:1.25"]),
        attrs={
            "Pod": "old-pod-id",
            "PodName": "nginx-podman-compose",
            "Config": {"Image": "docker.io/library/nginx:1.25", "Labels": labels},
            "HostConfig": {},
            "NetworkSettings": {"Networks": {"nginx-podman-net": {}}},
        },
    )
    client = FakePodmanClient([nginx], network_names=["nginx-podman-net", "podman"])
    client.pods.create_returns_none = True
    client.pods.next_create_pod_id = "new-pod-id"
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter.update_compose_services(
            {"mode": "docker_compose", "project": "nginx"},
            {"nginx": "docker.io/library/nginx:1.26"},
        )

    assert result.updated is True
    assert client.pods.get_calls == ["old-pod-id", "nginx-podman-compose"]
    assert len(client.containers.create_calls) == 1
    create_call = client.containers.create_calls[0]
    assert create_call["pod"] == "new-pod-id"
    assert client.api.post_calls[0]["data"]["pod"] == "new-pod-id"
    assert client.api.post_calls[0]["data"]["pod"] != "old-pod-id"


def test_podman_safe_exception_message_redacts_json_style_secret_payloads():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    adapter = PodmanRuntimeAdapter(runtime, client=FakePodmanClient([]))

    message = adapter._safe_exception_message(
        RuntimeError(
            'server echoed {"env":{"API_TOKEN":"super-secret-token",'
            '"password":"hunter2"},"access_key":"AKIA-secret"}'
        )
    )

    assert "API_TOKEN" not in message
    assert "super-secret-token" not in message
    assert "password" not in message
    assert "hunter2" not in message
    assert "access_key" not in message
    assert "AKIA-secret" not in message
    assert "credential=***REDACTED***" in message


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_recovery_drops_empty_network_values(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    labels = {
        "io.podman.compose.project": "nginx",
        "com.docker.compose.service": "nginx",
    }
    nginx = FakeContainer(
        "nginx-1",
        "nginx-podman-compose",
        FakeImage(tags=["docker.io/library/nginx:1.25"]),
        attrs={
            "Pod": "nginx-podman-compose",
            "PodName": "nginx-podman-compose",
            "Config": {
                "Image": "docker.io/library/nginx:1.25",
                "Env": ["API_TOKEN=super-secret-token"],
                "Labels": labels,
            },
            "HostConfig": {"NetworkMode": ""},
            "NetworkSettings": {
                "Networks": {
                    "": {
                        "Aliases": ["should-not-be-used"],
                        "IPAddress": "10.88.0.199",
                        "IPAMConfig": None,
                    },
                    "nginx-net": {
                        "Aliases": ["nginx", "nginx-podman-compose"],
                        "IPAddress": "10.88.0.20",
                        "IPAMConfig": None,
                    },
                }
            },
        },
    )
    client = FakePodmanClient([nginx], network_names=["nginx-net", "podman"])
    client.containers.fail_start_for_images.add("docker.io/library/nginx:1.26")
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with caplog.at_level(logging.DEBUG, logger="releasetracker.executors.podman"):
        with pytest.raises(RuntimeMutationError, match="recovery succeeded best-effort"):
            await adapter.update_compose_services(
                {"mode": "docker_compose", "project": "nginx"},
                {"nginx": "docker.io/library/nginx:1.26"},
            )

    assert [call["image"] for call in client.containers.create_calls] == [
        "docker.io/library/nginx:1.26",
        "docker.io/library/nginx:1.25",
    ]
    assert all(
        "network_mode" not in call
        and "network" not in call
        and "networks" not in call
        and "netns" not in call
        for call in client.containers.create_calls
    )
    assert client.pods.create_calls == [
        {
            "name": "nginx-podman-compose",
            "infra": False,
            "share": "net",
            "networks": {
                "nginx-net": {
                    "aliases": ["nginx", "nginx-podman-compose"],
                    "static_ips": ["10.88.0.20"],
                }
            },
        }
    ]
    assert client.networks.connect_calls == []
    assert "phase=recovery boundary=networks.get" not in caplog.text
    assert "phase=recovery boundary=networks.connect" not in caplog.text
    assert "exception_class=RuntimeError" in caplog.text
    assert "API_TOKEN" not in caplog.text
    assert "super-secret-token" not in caplog.text


def test_podman_recreate_pod_for_grouped_replacement_drops_empty_network_endpoint(caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)
    spec = GroupedRuntimeRecreateSpec(
        runtime_type="podman",
        container_id="nginx-1",
        container_name="nginx-podman-compose",
        compose_project="nginx",
        compose_service="nginx",
        current_image="docker.io/library/nginx:1.25",
        target_image="docker.io/library/nginx:1.26",
        create_config={"image": "docker.io/library/nginx:1.26", "name": "nginx-podman-compose"},
        host_config={"NetworkMode": ""},
        network_config={
            "network_mode": "",
            "endpoints": {
                "": {"Aliases": ["should-not-be-used"], "IPAddress": "10.88.0.199"},
                "nginx-net": {"Aliases": ["nginx"], "IPAddress": "10.88.0.20"},
            },
        },
        snapshot_payload={},
        restore_payload={},
        pod_id="nginx-podman-compose",
        pod_name="nginx-podman-compose",
        pod_relation_payload={"pod_id": "nginx-podman-compose"},
        dependencies=(),
    )

    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        adapter._recreate_pod_for_grouped_replacement(client, spec)

    assert client.pods.create_calls == [
        {
            "name": "nginx-podman-compose",
            "infra": False,
            "share": "net",
            "networks": {"nginx-net": {"aliases": ["nginx"], "static_ips": ["10.88.0.20"]}},
        }
    ]
    assert "should-not-be-used" not in caplog.text


@pytest.mark.asyncio
async def test_podman_grouped_snapshot_recovery_sanitizes_legacy_empty_network_payloads():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([], network_names=["nginx-net", "podman"])
    adapter = PodmanRuntimeAdapter(runtime, client=client)
    snapshot = {
        "runtime_type": "podman",
        "container_id": "nginx-1",
        "container_name": "nginx-podman-compose",
        "image": "docker.io/library/nginx:1.25",
        "pod_id": "nginx-podman-compose",
        "pod_name": "nginx-podman-compose",
        "create_config": {
            "image": "docker.io/library/nginx:1.25",
            "name": "nginx-podman-compose",
            "network_mode": "",
            "network": "",
            "networks": ["", "nginx-net"],
            "ipc_mode": "",
            "pid_mode": "",
            "cgroupns": "",
            "userns_mode": "",
            "uts_mode": "",
        },
        "host_config": {"NetworkMode": ""},
        "network_config": {
            "network_mode": "",
            "endpoints": {
                "": {
                    "Aliases": ["should-not-be-used"],
                    "IPAddress": "10.88.0.199",
                    "IPAMConfig": None,
                },
                "nginx-net": {
                    "Aliases": ["nginx", "nginx-podman-compose"],
                    "IPAddress": "10.88.0.20",
                    "IPAMConfig": None,
                },
            },
        },
    }

    result = await adapter._recover_grouped_container_from_snapshot(snapshot)

    assert result.updated is True
    assert client.api.post_calls
    assert client.containers.create_calls == [
        {
            "image": "docker.io/library/nginx:1.25",
            "name": "nginx-podman-compose",
            "pod": "nginx-podman-compose",
        }
    ]
    assert client.networks.connect_calls == []


@pytest.mark.asyncio
async def test_podman_grouped_snapshot_recovery_uses_resolved_pod_object_without_network_kwargs():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([], network_names=["nginx-podman-net", "podman"])
    client.pods.create("nginx-podman-compose", networks={"nginx-podman-net": {}})
    client.pods.create_calls.clear()
    adapter = PodmanRuntimeAdapter(runtime, client=client)
    snapshot = {
        "runtime_type": "podman",
        "container_id": "nginx-1",
        "container_name": "nginx-podman-compose",
        "image": "docker.io/library/nginx:1.25",
        "pod_id": "nginx-podman-compose",
        "pod_name": "nginx-podman-compose",
        "create_config": {
            "image": "docker.io/library/nginx:1.25",
            "name": "nginx-podman-compose",
            "hostname": "nginx-host",
            "ports": {"80/tcp": ("", 8080)},
            "network_mode": "",
            "network": "nginx-podman-net",
            "networks": ["nginx-podman-net"],
            "dns": ["1.1.1.1"],
            "dns_opt": ["ndots:0"],
            "dns_search": ["svc.local"],
            "extra_hosts": {"api.local": "10.88.0.10"},
            "shm_size": 67108864,
        },
        "host_config": {"NetworkMode": ""},
        "network_config": {
            "network_mode": "",
            "endpoints": {
                "nginx-podman-net": {
                    "Aliases": ["nginx", "nginx-podman-compose"],
                    "IPAddress": "10.88.0.20",
                    "IPAMConfig": None,
                }
            },
        },
    }

    result = await adapter._recover_grouped_container_from_snapshot(snapshot)

    assert result.updated is True
    assert client.pods.create_calls == []
    assert client.pods.get_calls == []
    assert client.api.post_calls
    assert len(client.containers.create_calls) == 1
    create_call = client.containers.create_calls[0]
    assert create_call["pod"] == "nginx-podman-compose"
    assert "netns" not in create_call
    _assert_no_blank_podman_create_network_values(create_call)
    assert all(
        key not in create_call
        for key in (
            "ports",
            "portmappings",
            "network",
            "networks",
            "cni_networks",
            "network_mode",
            "dns",
            "dns_opt",
            "dns_search",
            "extra_hosts",
            "hostname",
            "shm_size",
        )
    )
    assert client.networks.connect_calls == []
