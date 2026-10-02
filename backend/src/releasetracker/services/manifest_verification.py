"""Read pinned OCI membership using the executor's configured source policy."""

from .deployment_targets import _binding_contexts
from ..scheduler import ReleaseScheduler
from ..trackers.docker import DockerTracker


async def verify_running_manifest(storage, executor, image, actual):
    scheduler = ReleaseScheduler(storage)
    pinned = image.rsplit("@", 1)[-1]
    repository = image.rsplit("@", 1)[0]
    for binding in _binding_contexts(executor):
        resolved = await storage.get_executor_binding(binding.tracker_source_id)
        if resolved is None:
            continue
        tracker, source = resolved
        if source.source_type != "container" or not source.enabled:
            continue
        config = await storage.get_tracker_config(tracker.name)
        config = scheduler._make_source_tracker_config(
            tracker.name, source, config, tracker.primary_changelog_source_key
        )
        client = await scheduler._create_tracker(config)
        if not isinstance(client, DockerTracker):
            continue
        expected = f"{client.registry}/{client.image}"
        if repository != expected and not (
            client.registry in {"docker.io", "registry-1.docker.io"}
            and repository in {client.image, client.image.removeprefix("library/")}
        ):
            continue
        return await client.verify_running_manifest(pinned, actual)
    return "unknown"
