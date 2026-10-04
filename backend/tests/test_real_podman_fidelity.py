"""Opt-in native Podman Socket fidelity check, with a separate owned storage root."""

import asyncio
import json
import os
import subprocess
import time

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.podman import PodmanRuntimeAdapter

pytestmark = pytest.mark.skipif(
    os.environ.get("RT_RUN_REAL_PODMAN_TESTS") != "1",
    reason="Explicit owned native Podman acceptance",
)


@pytest.mark.timeout(300)
async def test_private_podman_socket_update_and_restore_preserve_effective_config(tmp_path):
    base = [
        "podman",
        "--root",
        str(tmp_path / "storage"),
        "--runroot",
        str(tmp_path / "run"),
        "--storage-driver",
        "vfs",
        "--cgroup-manager",
        "cgroupfs",
    ]

    def cli(*args, timeout=120, check=True):
        return subprocess.run(
            [*base, *args], capture_output=True, text=True, timeout=timeout, check=check
        )

    service = None
    adapter = None
    log = (tmp_path / "service.log").open("w")
    try:
        socket = tmp_path / "podman.sock"
        service = subprocess.Popen(
            [*base, "system", "service", "--time=0", "unix://" + str(socket)],
            stdout=log,
            stderr=log,
        )
        deadline = time.monotonic() + 20
        while not socket.exists():
            if service.poll() is not None:
                raise AssertionError((tmp_path / "service.log").read_text())
            assert time.monotonic() < deadline, "owned Podman service unavailable"
            await asyncio.sleep(0.1)
        runtime = RuntimeConnectionConfig(
            name="owned-native-podman",
            type="podman",
            config={
                "socket": "unix://" + str(socket),
                "operation_policy": {"write_timeout_seconds": 90},
            },
        )
        adapter = PodmanRuntimeAdapter(runtime)
        client = adapter._get_client()
        assert await asyncio.to_thread(client.ping)
        old = "docker.m.daocloud.io/library/nginx:1.27-alpine"
        new = "docker.m.daocloud.io/library/nginx:1.28-alpine"
        for image in (old, new):
            await asyncio.to_thread(cli, "pull", image)
        image = json.loads((await asyncio.to_thread(cli, "image", "inspect", new)).stdout)[0]
        env = image["Config"]["Env"]
        bind = tmp_path / "bind"
        bind.mkdir()
        args = [
            "run",
            "-d",
            "--name",
            "owned-nginx",
            "--read-only",
            "--read-only-tmpfs=false",
            "--entrypoint",
            "/bin/sh",
            "--publish",
            "127.0.0.1::80",
            "--volume",
            f"{bind}:/first:ro,rprivate",
            "--volume",
            f"{bind}:/second:rw,rprivate",
            "--volume",
            "/persist",
            "--tmpfs",
            "/tmp:rw,size=8388608,mode=1777",
            "--dns",
            "1.1.1.1",
            "--dns-option",
            "ndots:1",
            "--group-add",
            "44",
            "--cap-drop",
            "NET_RAW",
            "--memory",
            "128m",
            "--memory-swap",
            "256m",
            "--cpu-shares",
            "512",
            "--stop-timeout",
            "37",
            "--restart",
            "unless-stopped",
            "--env",
            "CUSTOM_FIDELITY=keep",
            "--label",
            "audit=owned",
        ]
        for item in env:
            args += ["--env", item]
        for key, value in (image["Config"].get("Labels") or {}).items():
            args += ["--label", key + "=" + value]
        await asyncio.to_thread(cli, *args, old, "-c", "while :; do sleep 60; done")
        await asyncio.to_thread(
            cli, "exec", "owned-nginx", "sh", "-c", "printf retained-data > /persist/proof"
        )
        before = json.loads((await asyncio.to_thread(cli, "inspect", "owned-nginx")).stdout)[0]
        snapshot = json.loads(
            json.dumps(await adapter.capture_snapshot({"container_name": "owned-nginx"}, old))
        )
        result = await adapter.update_image({"container_name": "owned-nginx"}, new)
        assert result.updated
        updated = json.loads((await asyncio.to_thread(cli, "inspect", "owned-nginx")).stdout)[0]
        assert updated["Image"] == image["Id"]
        fields = [
            "ReadonlyRootfs",
            "Dns",
            "DnsOptions",
            "GroupAdd",
            "CapDrop",
            "Memory",
            "MemorySwap",
            "CpuShares",
            "RestartPolicy",
            "Tmpfs",
            "StopTimeout",
        ]

        def check(actual):
            assert actual["State"]["Running"]
            assert set(actual["Config"]["Env"]) == set(before["Config"]["Env"])
            assert before["Config"]["StopTimeout"] == 37
            for field in (
                "StopTimeout",
                "Entrypoint",
                "Cmd",
                "Labels",
                "User",
                "WorkingDir",
                "Tty",
                "OpenStdin",
            ):
                assert actual["Config"].get(field) == before["Config"].get(field), field
            for field in fields:
                assert actual["HostConfig"].get(field) == before["HostConfig"].get(field), field
            assert actual["NetworkSettings"]["Ports"] == before["NetworkSettings"]["Ports"]
            old_mounts = {
                m["Destination"]: (
                    m.get("Name") or m.get("Source"),
                    m["Type"],
                    m.get("RW"),
                    m.get("Propagation"),
                )
                for m in before["Mounts"]
            }
            new_mounts = {
                m["Destination"]: (
                    m.get("Name") or m.get("Source"),
                    m["Type"],
                    m.get("RW"),
                    m.get("Propagation"),
                )
                for m in actual["Mounts"]
            }
            assert new_mounts == old_mounts

        check(updated)
        assert (
            await asyncio.to_thread(cli, "exec", "owned-nginx", "cat", "/persist/proof")
        ).stdout == "retained-data"
        recovered = await adapter.recover_from_snapshot({"container_name": "owned-nginx"}, snapshot)
        assert recovered.updated
        restored = json.loads((await asyncio.to_thread(cli, "inspect", "owned-nginx")).stdout)[0]
        assert restored["Image"] == before["Image"]
        check(restored)
        assert (
            await asyncio.to_thread(cli, "exec", "owned-nginx", "cat", "/persist/proof")
        ).stdout == "retained-data"
    finally:
        if adapter:
            await adapter.close()
        await asyncio.to_thread(cli, "rm", "-a", "-f", "--volumes", check=False)
        remaining = await asyncio.to_thread(cli, "ps", "-a", "--format", "{{.Names}}", check=False)
        assert not remaining.stdout.strip(), remaining.stdout
        if service:
            service.terminate()
            try:
                await asyncio.to_thread(service.wait, timeout=10)
            except subprocess.TimeoutExpired:
                service.kill()
                await asyncio.to_thread(service.wait, timeout=5)
        log.close()
