"""Owned DinD Unix socket acceptance; never connects a host business socket."""

import asyncio
import json
import os
import subprocess
import time
import uuid

import pytest
from docker.types import Mount
from docker.utils.proxy import ProxyConfig

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.docker import DockerRuntimeAdapter

pytestmark = pytest.mark.skipif(
    os.environ.get("RT_RUN_REAL_DIND_TESTS") != "1",
    reason="Isolated privileged runtime acceptance is explicitly opt-in",
)


def podman(*args, timeout=30, check=True):
    return subprocess.run(
        ["podman", *args], capture_output=True, text=True, timeout=timeout, check=check
    )


@pytest.mark.timeout(300)
async def test_unix_socket_update_and_json_recovery_keep_observed_configuration_and_volume_data(
    tmp_path,
    monkeypatch,
):
    name = "rt-owned-fidelity-" + uuid.uuid4().hex[:10]
    runtime = RuntimeConnectionConfig(
        name="owned-unix-fidelity",
        type="docker",
        config={
            "socket": "unix://" + str(tmp_path / "docker.sock"),
            "operation_policy": {"write_timeout_seconds": 90},
        },
        secrets={},
    )
    adapter = DockerRuntimeAdapter(runtime)
    client = None
    tmp_path.chmod(0o700)
    (tmp_path / "bind").mkdir()
    try:
        await asyncio.to_thread(
            podman,
            "run",
            "-d",
            "--name",
            name,
            "--privileged",
            "--cgroupns=private",
            "--entrypoint",
            "/bin/sh",
            "-e",
            "DOCKER_TLS_CERTDIR=",
            "-v",
            f"{tmp_path}:/owned:Z",
            "data.forgejo.org/oci/docker:dind",
            "-c",
            "mkdir -p /sys/fs/cgroup/rt-owned-daemon && echo 0 > /sys/fs/cgroup/rt-owned-daemon/cgroup.procs && echo ' +cpu +cpuset +memory +pids' > /sys/fs/cgroup/cgroup.subtree_control && exec dockerd --host=unix:///owned/docker.sock --cgroup-parent=/rt-owned-workloads",
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                client = adapter._get_client()
                await asyncio.to_thread(client.ping)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise AssertionError("Owned socket engine did not start")
                await asyncio.sleep(1)
        old = "docker.m.daocloud.io/library/nginx:1.27-alpine"
        new = "docker.m.daocloud.io/library/nginx:1.28-alpine"
        for image in (old, new):
            await asyncio.to_thread(client.images.pull, image)
        network = await asyncio.to_thread(
            client.networks.create, "owned-fidelity-net", driver="bridge"
        )
        import io
        import tarfile

        dockerfile = f"FROM {new}\nENV RT_FIDELITY_UNREVIEWED=must-not-appear\n"
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo("Dockerfile")
            data = dockerfile.encode()
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        stream.seek(0)
        guarded_image = "rt-owned-unreviewed:latest"
        await asyncio.to_thread(
            client.images.build, fileobj=stream, custom_context=True, tag=guarded_image, rm=True
        )
        guard = await asyncio.to_thread(
            client.containers.run,
            old,
            ["sh", "-c", "while :; do sleep 60; done"],
            name="owned-guard",
            detach=True,
        )
        collection = type(client.images)
        native_pull = collection.pull

        def pull_local_guard(self, image, **kwargs):
            if image == guarded_image:
                return self.get(image)
            return native_pull(self, image, **kwargs)

        # DockerClient.images is a fresh collection on every property access.
        monkeypatch.setattr(collection, "pull", pull_local_guard)
        with pytest.raises(ValueError, match="unreviewed environment"):
            await adapter.update_image({"container_name": "owned-guard"}, guarded_image)
        still = await asyncio.to_thread(client.containers.get, "owned-guard")
        assert still.id == guard.id and still.attrs["State"]["Running"]
        await asyncio.to_thread(still.remove, force=True)
        target_defaults = (await asyncio.to_thread(client.images.get, new)).attrs["Config"]
        target_env = {
            item.partition("=")[0]: item.partition("=")[2]
            for item in target_defaults.get("Env", [])
        }
        from docker.models.containers import _create_container_args

        source = dict(
            image=old,
            command=["sh", "-c", "while :; do sleep 60; done"],
            name="owned-nginx",
            detach=True,
            read_only=True,
            environment={**target_env, "CUSTOM_FIDELITY": "keep"},
            labels={**target_defaults.get("Labels", {}), "audit": "owned"},
            ports={"80/tcp": ("127.0.0.1", 0)},
            volumes=["/owned/bind:/first:ro,rprivate", "/owned/bind:/second:rw,rprivate"],
            mounts=[Mount(target="/persist", source="", type="volume", no_copy=True)],
            tmpfs={"/tmp": "rw,size=8388608,mode=1777"},
            dns=["1.1.1.1"],
            dns_opt=["ndots:1"],
            group_add=["44"],
            cap_drop=["NET_RAW"],
            mem_limit=134217728,
            memswap_limit=268435456,
            stop_signal="SIGTERM",
            network=network.name,
            version=client.api._version,
        )
        request = _create_container_args(source)
        # Avoid the SDK's own complex bind-list destination bug in fixture setup.
        request["volumes"] = ["/first", "/second"]
        request["stop_timeout"] = 45
        reply = await asyncio.to_thread(client.api.create_container, **request)
        container = await asyncio.to_thread(client.containers.get, reply["Id"])
        await asyncio.to_thread(container.start)
        assert {mount["Destination"] for mount in container.attrs["Mounts"]} <= {
            "/first",
            "/second",
            "/persist",
            "/tmp",
        }
        await asyncio.to_thread(
            container.update, restart_policy={"Name": "unless-stopped", "MaximumRetryCount": 0}
        )
        await asyncio.to_thread(client.api.update_container, container.id, cpu_shares=512)
        # SDK high-level run does not accept StopTimeout; set via native update API.
        # The creation path below uses a low-level configured timeout instead.
        await asyncio.to_thread(container.reload)
        await asyncio.to_thread(
            container.exec_run, ["sh", "-c", "printf retained-data > /persist/proof"]
        )
        snapshot = await adapter.capture_snapshot({"container_name": "owned-nginx"}, old)
        snapshot = json.loads(json.dumps(snapshot))
        original = container.attrs
        original_volume = next(
            m["Name"] for m in original["Mounts"] if m["Destination"] == "/persist"
        )
        original_ports = original["NetworkSettings"]["Ports"]
        client.api._proxy_configs = ProxyConfig.from_dict(
            {"httpProxy": "http://must-not-be-injected.invalid:3128"}
        )
        result = await adapter.update_image({"container_name": "owned-nginx"}, new)
        assert result.updated
        changed = await asyncio.to_thread(client.containers.get, "owned-nginx")
        assert changed.attrs["Image"] == (await asyncio.to_thread(client.images.get, new)).id
        assert changed.attrs["Config"]["Env"] == original["Config"]["Env"]
        for field in ("AttachStdin", "AttachStdout", "AttachStderr", "StdinOnce"):
            assert changed.attrs["Config"][field] == original["Config"][field], field
        assert not any(
            v.startswith("HTTP_PROXY=") or v.startswith("http_proxy=")
            for v in changed.attrs["Config"]["Env"]
        )
        host_fields = [
            "ReadonlyRootfs",
            "Dns",
            "DnsOptions",
            "GroupAdd",
            "CapDrop",
            "Memory",
            "MemorySwap",
            "CpuShares",
            "RestartPolicy",
            "Binds",
            "Tmpfs",
        ]
        for field in host_fields:
            assert changed.attrs["HostConfig"].get(field) == original["HostConfig"].get(
                field
            ), field
        assert changed.attrs["NetworkSettings"]["Ports"] == original_ports
        assert (
            next(m["Name"] for m in changed.attrs["Mounts"] if m["Destination"] == "/persist")
            == original_volume
        )
        assert (
            await asyncio.to_thread(changed.exec_run, ["cat", "/persist/proof"])
        ).output == b"retained-data"
        recovered = await adapter.recover_from_snapshot({"container_name": "owned-nginx"}, snapshot)
        assert recovered.updated
        restored = await asyncio.to_thread(client.containers.get, "owned-nginx")
        assert restored.attrs["Image"] == original["Image"]
        for field in host_fields:
            assert restored.attrs["HostConfig"].get(field) == original["HostConfig"].get(
                field
            ), field
        assert restored.attrs["NetworkSettings"]["Ports"] == original_ports
        assert (
            next(m["Name"] for m in restored.attrs["Mounts"] if m["Destination"] == "/persist")
            == original_volume
        )
        assert (
            await asyncio.to_thread(restored.exec_run, ["cat", "/persist/proof"])
        ).output == b"retained-data"
    finally:
        await adapter.close()
        await asyncio.to_thread(podman, "rm", "-f", "-v", name, timeout=45, check=False)
        assert (
            await asyncio.to_thread(podman, "container", "exists", name, check=False)
        ).returncode != 0
