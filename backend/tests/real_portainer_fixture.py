"""Disposable nested Portainer. Never accepts an existing endpoint or host socket."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import subprocess
import sys
import time
import uuid

import httpx

from releasetracker.executors.portainer import PortainerResourceNotFoundError
from releasetracker.executors import portainer_recovery

DIND_IMAGE = "data.forgejo.org/oci/docker@sha256:686d2c5464787b0ca42420e0aa03a94fc9781ff9bf8b5ff947614b9fa4c3c3e0"
PORTAINER_IMAGE = "docker.m.daocloud.io/portainer/portainer-ce@sha256:4d616db18cfeb5dd41a69c0958bc825c84483ea9cde1106eb82a5d26f3bd8b0e"
OLD_IMAGE = "docker.m.daocloud.io/library/alpine:3.20"
NEW_IMAGE = "docker.m.daocloud.io/library/alpine:3.21"


async def podman(*args, timeout=120, check=True):
    return await asyncio.to_thread(
        subprocess.run,
        ["podman", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


@dataclass
class IsolatedPortainer:
    engine_name: str
    base_url: str
    api: httpx.AsyncClient


@asynccontextmanager
async def isolated_portainer():
    name = "rt-owned-portainer-" + uuid.uuid4().hex[:10]
    try:
        await podman(
            "run",
            "-d",
            "--name",
            name,
            "--privileged",
            "-e",
            "DOCKER_TLS_CERTDIR=",
            "-p",
            "127.0.0.1::9000",
            DIND_IMAGE,
            "dockerd",
            "--host=unix:///var/run/docker.sock",
        )
        published = await podman("port", name, "9000/tcp")
        port = int(published.stdout.strip().rsplit(":", 1)[1])
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while True:
            ready = await podman("exec", name, "docker", "info", timeout=10, check=False)
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("Owned DinD did not become ready")
            await asyncio.sleep(1)
        for image in (PORTAINER_IMAGE, OLD_IMAGE, NEW_IMAGE):
            await podman("exec", name, "docker", "pull", image, timeout=180)
        # This socket is INSIDE the new disposable DinD, never a host volume.
        await podman(
            "exec",
            name,
            "docker",
            "run",
            "-d",
            "--name",
            "rt-fixture-portainer",
            "-p",
            "9000:9000",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            PORTAINER_IMAGE,
            "--http-enabled",
            "--no-setup-token",
        )
        async with httpx.AsyncClient(base_url=base, timeout=10) as api:
            deadline = time.monotonic() + 60
            while True:
                try:
                    status = await api.get("/api/status")
                    status.raise_for_status()
                    break
                except (httpx.ConnectError, httpx.ReadTimeout):
                    if time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(1)
            assert status.json()["Version"] == "2.45.1"
            yield IsolatedPortainer(name, base, api)
    finally:
        original = sys.exc_info()[1]
        try:
            await podman("rm", "--force", "--time", "0", "--volumes", name, check=False)
            remaining = await podman("container", "exists", name, check=False)
            if remaining.returncode != 1:
                raise AssertionError("Owned Portainer engine cleanup not verified")
        except BaseException as cleanup_error:
            if original is None:
                raise
            original.add_note(f"Owned fixture cleanup failed: {type(cleanup_error).__name__}")


async def wait_native_stack(adapter, ref, images, image_ids=None):
    deadline = time.monotonic() + 60
    prefix = f"/api/endpoints/{ref['endpoint_id']}/docker/containers/"
    while True:
        try:
            groups = await portainer_recovery.containers(adapter, ref)
        except PortainerResourceNotFoundError as exc:
            # Only a disappearing listed-container inspect is an eventual race.
            if not (
                exc.resource_path.startswith(prefix)
                and exc.resource_path.endswith("/json")
                and exc.resource_path != f"{prefix}json"
            ):
                raise
            groups = {}
        if set(groups) == set(images) and all(
            len(groups[service]) == 1
            and groups[service][0]["image"] == image
            and groups[service][0]["state"].get("Running") is True
            and (image_ids is None or groups[service][0]["image_id"] == image_ids[service])
            for service, image in images.items()
        ):
            return
        if time.monotonic() >= deadline:
            raise AssertionError("Owned Stack native rollout did not converge")
        await asyncio.sleep(0.2)
