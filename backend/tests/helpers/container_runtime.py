"""Scripted Docker/Podman clients shared by adapter tests."""

from __future__ import annotations

from collections.abc import Callable
import json
from json import JSONDecodeError
from types import SimpleNamespace


class FakeContainerNotFound(KeyError):
    response = SimpleNamespace(status_code=404)


RECOVERY_IMAGE_ID = "sha256:" + "a" * 64
DOCKER_ENGINE_ID = "11111111-2222-4333-8444-555555555555"


def docker_engine_identity():
    return {"schema": 1, "kind": "docker", "daemon_id": DOCKER_ENGINE_ID}


def recovery_evidence():
    return {
        "schema": 1,
        "image_id": RECOVERY_IMAGE_ID,
        "healthcheck": None,
        "config_observed": True,
    }


class FakeImage:
    def __init__(self, tags=None, image_id=None):
        self.tags = tags or []
        self.attrs = {"Config": {}}
        self.id = image_id or (
            self.tags[0] if self.tags and self.tags[0].startswith("sha256:") else RECOVERY_IMAGE_ID
        )


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
        self.attrs.setdefault("State", {})["Running"] = True
        if self._event_log is not None:
            self._event_log.append(f"start:{self.name}")

    def stop(self) -> None:
        self.stop_calls.append(True)
        if self._event_log is not None:
            self._event_log.append(f"stop:{self.name}")

    def remove(self, **kwargs) -> None:
        self.remove_calls.append(kwargs or True)
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
        raise FakeContainerNotFound("container not found")

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
            container_id=(
                "docker-low-level-id"
                if not self._created
                else f"docker-low-level-id-{len(self._created) + 1}"
            ),
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
    def info(self):
        return {"ID": DOCKER_ENGINE_ID}

    def __init__(self, containers):
        self.containers = FakeContainerManager(containers)
        self.update_container_image_calls = []

    def update_container_image(self, container_id: str, new_image: str) -> None:
        self.update_container_image_calls.append((container_id, new_image))


class FakeDockerImageManager:
    def __init__(self, should_fail: bool = False):
        self.pull_calls: list[str] = []
        self._should_fail = should_fail

    def get(self, image: str):
        return FakeImage(image_id=image)

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
    def info(self):
        return {"ID": DOCKER_ENGINE_ID}

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

    def get(self, image: str):
        return FakeImage(image_id=image)

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
        self.fail_remove_for_names: set[str] = set()
        self.inject_empty_network_mode_when_omitted = False
        for container in self._containers:
            self._bind_container_lifecycle_hooks(container)

    def list(self, all=True):
        return list(self._containers)

    def get(self, identifier: str):
        for container in self._containers:
            if container.id == identifier or container.name == identifier:
                return container
        raise FakeContainerNotFound("container not found")

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

    def _bind_container_lifecycle_hooks(self, container: FakeContainer) -> None:
        container._event_log = self.event_log

        def on_remove(container=container) -> None:
            if container.name in self.fail_remove_for_names:
                raise FakePodmanServerError(f"remove failed: {container.name}")
            self._forget_container(container)

        container._on_remove = on_remove

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
        self._bind_container_lifecycle_hooks(created)
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
