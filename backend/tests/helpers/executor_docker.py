class FakeDockerImage:
    def __init__(self, tags=None):
        self.tags = tags or []


class FakeDockerContainer:
    def __init__(self, container_id: str, name: str, image: str, attrs: dict | None = None):
        self.id = container_id
        self.name = name
        self.image = FakeDockerImage(tags=[image])
        self.attrs = attrs or {}
        self.stop_calls = []
        self.remove_calls = []
        self.start_calls = []

    def stop(self) -> None:
        self.stop_calls.append(True)

    def remove(self) -> None:
        self.remove_calls.append(True)

    def start(self) -> None:
        self.start_calls.append(True)


class FakeDockerImageManager:
    def __init__(self):
        self.pull_calls: list[str] = []

    def pull(self, image: str) -> None:
        self.pull_calls.append(image)


class FakeDockerContainerManager:
    def __init__(self, containers, *, create_should_fail: bool = False):
        self._containers = list(containers)
        self._remaining_failures = 1 if create_should_fail else 0
        self.create_calls: list[dict] = []
        self._created: list[FakeDockerContainer] = []

    def get(self, identifier: str):
        for container in self._containers:
            if container.id == identifier or container.name == identifier:
                return container
        raise KeyError(identifier)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RuntimeError("simulated docker recreate failure")

        container = FakeDockerContainer(
            container_id=f"recreated-{len(self._created) + 1}",
            name=kwargs.get("name", "recreated"),
            image=kwargs["image"],
            attrs={
                "Config": {
                    "Env": kwargs.get("environment"),
                    "Entrypoint": kwargs.get("entrypoint"),
                    "Cmd": kwargs.get("command"),
                    "Labels": kwargs.get("labels"),
                },
                "HostConfig": {
                    "PortBindings": kwargs.get("ports"),
                    "Binds": kwargs.get("volumes"),
                    "RestartPolicy": kwargs.get("restart_policy"),
                    "NetworkMode": kwargs.get("network_mode"),
                },
            },
        )
        self._created.append(container)
        self._containers.append(container)
        return container


class FakeDockerClient:
    def __init__(self, containers, *, create_should_fail: bool = False):
        self.containers = FakeDockerContainerManager(
            containers,
            create_should_fail=create_should_fail,
        )
        self.images = FakeDockerImageManager()
