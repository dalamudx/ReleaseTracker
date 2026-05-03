from collections.abc import Callable
from json import JSONDecodeError
import json
import logging

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.base import RuntimeMutationError
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter
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
        self.event_log.append(f"create:{kwargs.get('name', 'recreated')}")
        created = FakeContainer(
            container_id="docker-new-id",
            name=kwargs.get("name", "recreated"),
            image=FakeImage(tags=[kwargs["image"]]),
            attrs={
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


class FakeDockerRecreateClient:
    def __init__(self, containers, *, pull_should_fail: bool = False, network_names=None):
        self.containers = FakeContainerManager(containers)
        self.images = FakeDockerImageManager(should_fail=pull_should_fail)
        self.networks = FakeDockerNetworkManager(network_names or [])


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


class FakePodmanImageManager:
    def __init__(self, should_fail: bool = False):
        self.pull_calls: list[str] = []
        self._should_fail = should_fail

    def pull(self, image: str) -> None:
        self.pull_calls.append(image)
        if self._should_fail:
            raise RuntimeError(f"pull failed: {image}")


class FakePodmanContainerManager:
    def __init__(self, containers):
        self._containers = list(containers)
        self.event_log: list[str] = []
        self.create_calls: list[dict] = []
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
        self.event_log.append(f"create:{kwargs.get('name', 'recreated')}")
        created = FakeContainer(
            container_id="new-id",
            name=kwargs.get("name", "recreated"),
            image=FakeImage(tags=[kwargs["image"]]),
            attrs={
                "Pod": kwargs.get("pod", ""),
                "PodName": kwargs.get("pod", ""),
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
    def __init__(self, containers, pull_should_fail: bool = False, network_names=None):
        self.containers = FakePodmanContainerManager(containers)
        self.images = FakePodmanImageManager(should_fail=pull_should_fail)
        self.networks = FakePodmanNetworkManager(network_names or [])


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
    container = FakeContainer("abc", "nginx", FakeImage(tags=["nginx:1.25"]))
    client = FakeContainerClient([container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    targets = await adapter.discover_targets()
    assert len(targets) == 1
    assert targets[0].target_ref["container_id"] == "abc"
    assert targets[0].target_ref["container_name"] == "nginx"
    assert targets[0].image == "nginx:1.25"

    current = await adapter.get_current_image({"container_id": "abc"})
    assert current == "nginx:1.25"

    result = await adapter.update_image({"container_id": "abc"}, "nginx:1.26")
    assert result.updated is True
    assert result.old_image == "nginx:1.25"
    assert result.new_image == "nginx:1.26"
    assert client.update_container_image_calls == [("abc", "nginx:1.26")]
    assert container.update_image_calls == []

    snapshot = await adapter.capture_snapshot({"container_id": "abc"}, "nginx:1.25")
    await adapter.validate_snapshot({"container_id": "abc"}, snapshot)
    assert snapshot == {
        "runtime_type": "docker",
        "container_id": "abc",
        "container_name": "nginx",
        "image": "nginx:1.25",
    }


@pytest.mark.asyncio
async def test_docker_adapter_discovers_compose_projects_as_grouped_targets():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    standalone = FakeContainer("standalone", "nginx", FakeImage(tags=["nginx:1.25"]))
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

    assert [target.name for target in targets] == ["nginx", "release-stack"]
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
        "nginx",
        FakeImage(tags=["nginx:1.25"]),
        attrs={
            "Config": {
                "Env": ["FOO=bar"],
                "Cmd": ["nginx", "-g", "daemon off;"],
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
        },
    )
    client = FakeDockerRecreateClient([container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    snapshot = await adapter.capture_snapshot({"container_name": "nginx"}, "nginx:1.25")
    await adapter.validate_snapshot({"container_name": "nginx"}, snapshot)

    result = await adapter.update_image({"container_name": "nginx"}, "nginx:1.26")

    assert result.updated is True
    assert result.old_image == "nginx:1.25"
    assert result.new_image == "nginx:1.26"
    assert result.new_container_id == "docker-new-id"
    assert client.images.pull_calls == ["nginx:1.26"]
    assert len(container.stop_calls) == 1
    assert len(container.remove_calls) == 1
    assert len(client.containers.create_calls) == 1
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["image"] == "nginx:1.26"
    assert create_kwargs["name"] == "nginx"
    assert create_kwargs["environment"] == ["FOO=bar"]
    assert create_kwargs["command"] == ["nginx", "-g", "daemon off;"]
    assert create_kwargs["ports"] == {"80/tcp": [("", 8080), ("127.0.0.1", 18080)]}
    assert create_kwargs["volumes"] == {"/host/data": {"bind": "/data", "mode": "ro"}}
    assert create_kwargs["restart_policy"] == {"Name": "unless-stopped", "MaximumRetryCount": 0}
    assert create_kwargs["network_mode"] == "bridge"
    assert create_kwargs["labels"] == {"app": "web"}
    recreated = client.containers._created[0]
    assert len(recreated.start_calls) == 1


@pytest.mark.asyncio
async def test_docker_adapter_requires_recreate_metadata_when_image_only_update_is_unavailable():
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    container = FakeContainer("abc", "nginx", FakeImage(tags=["nginx:1.25"]), attrs={})
    client = FakeDockerRecreateClient([container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    with pytest.raises(
        ValueError, match="cannot recreate container .* without a restorable create configuration"
    ):
        await adapter.update_image({"container_id": "abc"}, "nginx:1.26")

    snapshot = await adapter.capture_snapshot({"container_id": "abc"}, "nginx:1.25")
    with pytest.raises(ValueError, match="snapshot.create_config must be a non-empty dict"):
        await adapter.validate_snapshot({"container_id": "abc"}, snapshot)


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
        "redis",
        FakeImage(tags=["redis:7.2"]),
        attrs={
            "Pod": "",
            "Config": {
                "Env": ["FOO=bar"],
                "Cmd": ["redis-server"],
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

    snapshot = await adapter.capture_snapshot({"container_name": "redis"}, "redis:7.2")
    await adapter.validate_snapshot({"container_name": "redis"}, snapshot)
    assert snapshot["container_id"] == "def"
    assert snapshot["container_name"] == "redis"
    assert snapshot["image"] == "redis:7.2"
    assert snapshot["create_config"]["image"] == "redis:7.2"

    result = await adapter.update_image({"container_name": "redis"}, "redis:7.4")

    assert result.updated is True
    assert result.old_image == "redis:7.2"
    assert result.new_image == "redis:7.4"
    assert client.images.pull_calls == ["redis:7.4"]
    assert len(container.stop_calls) == 1
    assert len(container.remove_calls) == 1
    assert len(client.containers.create_calls) == 1
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["image"] == "redis:7.4"
    assert create_kwargs["environment"] == ["FOO=bar"]
    assert create_kwargs["command"] == ["redis-server"]
    assert create_kwargs["ports"] == {"6379/tcp": ("", 6379)}
    assert create_kwargs["mounts"] == [
        {
            "type": "bind",
            "source": "/data",
            "target": "/data",
        }
    ]
    assert create_kwargs["restart_policy"] == {"Name": "unless-stopped", "MaximumRetryCount": 0}
    assert create_kwargs["network_mode"] == "bridge"
    assert create_kwargs["labels"] == {"app": "cache"}
    assert create_kwargs.get("entrypoint") is None
    recreated = client.containers._created[0]
    assert len(recreated.stop_calls) == 0
    assert recreated.image.tags == ["redis:7.4"]


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
        "redis",
        FakeImage(tags=["redis:7.2"]),
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
    assert [target.name for target in targets] == ["redis"]
    assert targets[0].target_ref == {
        "mode": "container",
        "container_id": "standalone",
        "container_name": "redis",
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
        "redis",
        FakeImage(tags=["redis:7.2"]),
        attrs={"Pod": "", "Config": {}, "HostConfig": {}},
    )
    compose_member = FakeContainer(
        "agent-1",
        "jenkins-agent",
        FakeImage(tags=["docker.io/jenkins/inbound-agent:latest"]),
        attrs={
            "Pod": "pod-123",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "jenkins-agent",
                    "com.docker.compose.service": "jenkins-agent",
                    "com.docker.compose.project.working_dir": "/data/podman/jenkins-agent",
                    "com.docker.compose.project.config_files": "podman-compose.yaml",
                    "io.podman.compose.project": "jenkins-agent",
                }
            },
            "HostConfig": {},
        },
    )
    client = FakePodmanClient([standalone, compose_member])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    targets = await adapter.discover_targets()

    assert [target.name for target in targets] == ["redis", "jenkins-agent"]
    compose_target = targets[1]
    assert compose_target.runtime_type == "podman"
    assert compose_target.image == "docker.io/jenkins/inbound-agent:latest"
    assert compose_target.target_ref == {
        "mode": "docker_compose",
        "project": "jenkins-agent",
        "working_dir": "/data/podman/jenkins-agent",
        "config_files": ["podman-compose.yaml"],
        "services": [
            {
                "service": "jenkins-agent",
                "replica_count": 1,
                "image": "docker.io/jenkins/inbound-agent:latest",
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
        [FakeContainerSpec("db", "postgres:16")],
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
        "helm.sh/chart": "jenkins-5.8.13",
    }
    helm_annotations = {
        "meta.helm.sh/release-name": "jenkins",
        "meta.helm.sh/release-namespace": "apps",
    }
    helm_deployment = FakeWorkload(
        "jenkins",
        [FakeContainerSpec("jenkins", "jenkins/jenkins:2.528.1-jdk21")],
        labels=helm_labels,
        annotations=helm_annotations,
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([helm_deployment], [], []))

    def fake_run_helm_command(args):
        return json.dumps(
            {
                "name": "jenkins",
                "app_version": "2.528.1",
                "chart": "jenkins-5.8.13",
            }
        )

    monkeypatch.setattr(adapter, "_run_helm_command", fake_run_helm_command)

    targets = await adapter.discover_targets()

    assert targets[0].target_ref == {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "jenkins",
        "workloads": [{"kind": "Deployment", "name": "jenkins"}],
        "service_count": 1,
        "chart_name": "jenkins",
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
        config={"namespace": "jenkins", "in_cluster": True},
        secrets={},
    )
    helm_labels = {
        "app.kubernetes.io/managed-by": "Helm",
        "helm.sh/chart": "jenkins-5.9.17",
    }
    helm_annotations = {
        "meta.helm.sh/release-name": "jenkins-5-1772603248",
        "meta.helm.sh/release-namespace": "jenkins",
    }
    helm_statefulset = FakeWorkload(
        "jenkins-5-1772603248",
        [FakeContainerSpec("jenkins", "jenkins/jenkins:2.528.1-jdk21")],
        labels=helm_labels,
        annotations=helm_annotations,
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=FakeAppsApi([], [helm_statefulset], []))
    command_calls = []

    def fake_run_helm_command(args):
        command_calls.append(args)
        if args[0] == "status":
            return json.dumps({"name": "jenkins-5-1772603248", "chart": "jenkins-5.9.17"})
        if args[0] == "list":
            return json.dumps(
                [
                    {
                        "name": "jenkins-5-1772603248",
                        "chart": "jenkins-5.9.17",
                        "app_version": "2.528.1",
                    }
                ]
            )
        raise AssertionError(f"unexpected helm command: {args}")

    monkeypatch.setattr(adapter, "_run_helm_command", fake_run_helm_command)

    targets = await adapter.discover_targets()

    assert command_calls == [
        ["status", "jenkins-5-1772603248", "--namespace", "jenkins", "--output", "json"],
        [
            "list",
            "--namespace",
            "jenkins",
            "--filter",
            "^jenkins-5-1772603248$",
            "--output",
            "json",
        ],
    ]
    assert targets[0].target_ref == {
        "mode": "helm_release",
        "namespace": "jenkins",
        "release_name": "jenkins-5-1772603248",
        "workloads": [{"kind": "StatefulSet", "name": "jenkins-5-1772603248"}],
        "service_count": 1,
        "chart_name": "jenkins",
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
        "redis",
        FakeImage(tags=["redis:7.2"]),
        attrs={"Pod": "", "Config": {}, "HostConfig": {}},
    )
    client = FakePodmanClient([container], pull_should_fail=True)
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    with pytest.raises(RuntimeError, match="pull failed"):
        await adapter.update_image({"container_name": "redis"}, "redis:7.4")

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
        "redis",
        FakeImage(tags=["redis:7.2"]),
        attrs={"Pod": "", "Config": {}, "HostConfig": {}},
    )
    client = FakePodmanClient([container])
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_image({"container_name": "redis"}, "redis:7.2")

    assert result.updated is False
    assert result.old_image == "redis:7.2"
    assert result.new_image == "redis:7.2"
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
        {"container_name": "redis"},
        {
            "runtime_type": "podman",
            "container_id": "def",
            "container_name": "redis",
            "image": "redis:7.2",
            "create_config": {
                "image": "redis:7.2",
                "name": "redis",
                "environment": ["FOO=bar"],
            },
            "pod_id": None,
        },
    )

    assert result.updated is True
    assert result.new_image == "redis:7.2"
    assert len(client.containers.create_calls) == 1
    assert client.containers.create_calls[0]["image"] == "redis:7.2"


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
        "ubuntu",
        FakeImage(tags=["ubuntu:latest"]),
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

    result = await adapter.update_image({"container_name": "ubuntu"}, "ubuntu:22.04")

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["volumes"] == {"test-buntu": {"bind": "/data/", "mode": "rw"}}
    assert create_kwargs["ports"] == {"80/tcp": ("", 80)}


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
        "nginx",
        FakeImage(tags=["nginx:1.25"]),
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

    result = await adapter.update_image({"container_name": "nginx"}, "nginx:1.26")

    assert result.updated is True
    create_kwargs = client.containers.create_calls[0]
    assert create_kwargs["mounts"] == [
        {
            "type": "bind",
            "source": "/etc/config",
            "target": "/etc/config",
            "read_only": True,
            "propagation": "rprivate",
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
        "redis",
        FakeImage(tags=["redis:7.2"]),
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
        {"container_id": "old-id", "container_name": "redis"}, "redis:7.4"
    )

    assert result.updated is True
    assert result.new_container_id == "new-id"


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
        {"container_name": "redis"},
        {
            "runtime_type": "podman",
            "container_id": "old-id",
            "container_name": "redis",
            "image": "redis:7.2",
            "create_config": {"image": "redis:7.2", "name": "redis"},
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
    assert worker_create["ports"] == {"9000/tcp": None}
    assert worker_create["volumes"] == {"/srv/worker": {"bind": "/app/data", "mode": "rw"}}
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
    assert api_create["ports"] == {
        "8000/tcp": [("", 8080), ("127.0.0.1", 18080)],
        "9000/tcp": None,
    }
    assert api_create["volumes"] == {"/srv/api": {"bind": "/app/data", "mode": "ro"}}
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
        FakeImage(tags=["postgres:16"]),
        attrs={
            "Config": {"Image": "postgres:16", "Labels": db_labels},
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
        "jenkins-agent",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "",
            "Labels": {
                "io.container.manager": "libpod",
                "io.container.image": "ghcr.io/acme/agent:3.0",
                "io.podman.compose.project": "jenkins-agent",
                "com.docker.compose.service": "",
            },
            "HostConfig": {},
        },
    )
    full_container = FakeContainer(
        "47e2f891",
        "jenkins-agent",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "",
            "Config": {
                "Labels": {
                    "io.podman.compose.project": "jenkins-agent",
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
        {"mode": "docker_compose", "project": "jenkins-agent"}
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
        "jenkins-agent",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "pod-jenkins",
            "Labels": {
                "io.podman.compose.project": "jenkins-agent",
                "com.docker.compose.service": "",
            },
        },
    )
    full_container = FakeContainer(
        "47e2f891",
        "jenkins-agent",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "pod-jenkins",
            "Config": {
                "Image": "ghcr.io/acme/agent:3.0",
                "Labels": {
                    "io.podman.compose.project": "jenkins-agent",
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

    containers_by_service = adapter._find_compose_service_containers("jenkins-agent")

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
            "HostConfig": {"Binds": ["/data/podman/jenkins-agent/agent:/home/jenkins/agent:Z"]},
            "NetworkSettings": {
                "Networks": {
                    "traefik-external": {
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
                    "traefik-external": {
                        "Aliases": ["api", "core-api-1"],
                        "Links": [],
                        "IPAMConfig": None,
                    }
                }
            },
        },
    )
    client = FakePodmanClient([worker, api], network_names=["traefik-external", "podman"])
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
                    "type": "bind",
                    "source": "/data/podman/jenkins-agent/agent",
                    "target": "/home/jenkins/agent",
                    "relabel": "Z",
                }
            ],
            "labels": worker_labels,
            "pod": "core-pod",
        },
    ]
    assert client.networks.disconnect_calls == [
        ("traefik-external", "core-api-1", True),
        ("podman", "core-api-1", True),
        ("traefik-external", "core-worker-1", True),
        ("podman", "core-worker-1", True),
    ]
    assert client.networks.connect_calls == [
        ("traefik-external", "core-api-1", {"aliases": ["api", "core-api-1"]}),
        ("traefik-external", "core-worker-1", {"aliases": ["worker", "core-worker-1"]}),
    ]


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
    assert client.containers.create_calls == [
        {
            "image": "ghcr.io/acme/api:1.1",
            "name": "core-api-1",
            "labels": labels,
            "pod": "core-pod",
        }
    ]


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
    assert client.containers.create_calls == [
        {
            "image": "ghcr.io/acme/api:1.1",
            "name": "core-api-1",
            "labels": labels,
            "pod": "core-pod",
        }
    ]


@pytest.mark.asyncio
async def test_podman_compose_grouped_update_injects_fallback_compose_labels_into_replacement_create_config():
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    summary_labels = {
        "io.podman.compose.project": "jenkins-agent",
        "com.docker.compose.service": "",
    }
    full_labels = {
        "com.docker.compose.project": "jenkins-agent",
        "com.docker.compose.service": "agent",
        "com.docker.compose.container-number": "1",
        "io.podman.compose.project": "jenkins-agent",
        "io.podman.compose.version": "1.0.6",
        "com.docker.compose.oneoff": "False",
        "custom.label": "keep-me",
    }
    listed_summary = FakeContainer(
        "agent-1",
        "jenkins-agent",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "pod-jenkins",
            "PodName": "jenkins-pod",
            "Config": {"Image": "ghcr.io/acme/agent:3.0", "Labels": summary_labels},
            "HostConfig": {},
        },
    )
    stored_full = FakeContainer(
        "agent-1",
        "jenkins-agent",
        FakeImage(tags=["ghcr.io/acme/agent:3.0"]),
        attrs={
            "Pod": "pod-jenkins",
            "PodName": "jenkins-pod",
            "Config": {"Image": "ghcr.io/acme/agent:3.0", "Labels": full_labels},
            "HostConfig": {},
        },
    )

    client = FakePodmanClient([stored_full])
    default_list = client.containers.list
    client.containers.list = lambda all=True: [listed_summary]
    adapter = PodmanRuntimeAdapter(runtime, client=client)

    result = await adapter.update_compose_services(
        {"mode": "docker_compose", "project": "jenkins-agent"},
        {"agent": "ghcr.io/acme/agent:3.1"},
    )
    client.containers.list = default_list
    images = await adapter.fetch_compose_service_images(
        {"mode": "docker_compose", "project": "jenkins-agent"}
    )

    assert result.updated is True
    assert client.containers.create_calls == [
        {
            "image": "ghcr.io/acme/agent:3.1",
            "name": "jenkins-agent",
            "labels": full_labels,
            "pod": "jenkins-pod",
        }
    ]
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
    }
    assert spec.snapshot_payload["pod_id"] == "pod-core"
    assert spec.snapshot_payload["pod_name"] == "core-pod"
    assert spec.snapshot_payload["pod_relation_payload"] == spec.pod_relation_payload
    assert spec.restore_payload["pod_id"] == "pod-core"
    assert spec.restore_payload["pod_name"] == "core-pod"
    assert spec.restore_payload["pod_relation_payload"] == spec.pod_relation_payload


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
                "LogConfig": {"Type": "k8s-file", "Config": {"path": "/var/log/api.log"}},
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
            "environment": ["APP_ENV=prod"],
            "entrypoint": ["python", "-m"],
            "command": ["app.main"],
            "user": "1000:1000",
            "working_dir": "/srv/app",
            "hostname": "core-api",
            "healthcheck": {"Test": ["CMD-SHELL", "curl -f http://127.0.0.1/health || exit 1"]},
            "ports": {"8000/tcp": ("", 8080), "9000/tcp": None},
            "mounts": [
                {
                    "type": "bind",
                    "source": "/srv/api",
                    "target": "/app/data",
                }
            ],
            "log_config": {"Type": "k8s-file", "Config": {"path": "/var/log/api.log"}},
            "restart_policy": {"Name": "always", "MaximumRetryCount": 0},
            "network_mode": "bridge",
            "extra_hosts": ["db.internal:10.88.0.2"],
            "dns": ["8.8.4.4"],
            "tmpfs": {"/tmp": "rw,size=64m"},
            "ulimits": [{"Name": "nofile", "Soft": 4096, "Hard": 8192}],
            "security_opt": ["label=disable"],
            "cap_add": ["NET_ADMIN"],
            "cap_drop": ["AUDIT_WRITE"],
            "devices": ["/dev/net/tun:/dev/net/tun:rwm"],
            "labels": {"app": "api"},
        }
    ]


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
    assert client.containers.create_calls == [
        {
            "image": "ghcr.io/acme/worker:9.2",
            "name": "core-worker-1",
            "labels": labels,
            "log_config": {"Type": "journald", "Config": {}},
            "network_mode": "bridge",
            "pod": "core-pod",
        }
    ]


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
            "HostConfig": {},
        },
    )
    api = FakeContainer(
        "api-1",
        "core-api-1",
        FakeImage(tags=["ghcr.io/acme/api:1.0"]),
        attrs={
            "Pod": "pod-core",
            "Config": {"Image": "ghcr.io/acme/api:1.0", "Labels": api_labels},
            "HostConfig": {},
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
    assert client.containers.event_log == [
        "stop:core-api-1",
        "remove:core-api-1",
        "create:core-api-1",
        "start:core-api-1",
        "stop:core-worker-1",
        "remove:core-worker-1",
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

    assert client.containers.create_calls[0] == {
        "image": "ghcr.io/acme/api:1.1",
        "name": "core-api-1",
        "environment": ["APP_MODE=stable"],
        "ports": {"8000/tcp": ("", 8080)},
        "mounts": [
            {
                "type": "bind",
                "source": "/srv/api",
                "target": "/app/data",
                "read_only": True,
            }
        ],
        "restart_policy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        "network_mode": "bridge",
        "labels": labels,
        "pod": "core-pod",
    }
    assert client.containers.create_calls[1] == {
        "image": "ghcr.io/acme/api:1.0",
        "name": "core-api-1",
        "environment": ["APP_MODE=stable"],
        "ports": {"8000/tcp": ("", 8080)},
        "mounts": [
            {
                "type": "bind",
                "source": "/srv/api",
                "target": "/app/data",
                "read_only": True,
            }
        ],
        "restart_policy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        "network_mode": "bridge",
        "labels": labels,
        "pod": "core-pod",
    }
