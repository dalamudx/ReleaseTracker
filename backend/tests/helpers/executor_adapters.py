from typing import Any

from releasetracker.executors.base import BaseRuntimeAdapter, RuntimeMutationError, RuntimeUpdateResult


class MutableFakeRuntimeAdapter(BaseRuntimeAdapter):
    def __init__(
        self,
        runtime_connection,
        *,
        current_image: str,
        invalid_target_key: str | None = None,
        invalid_target_value: Any = None,
        invalid_target_message: str = "invalid target",
        include_runtime_type_in_snapshot: bool = False,
        storage=None,
        executor_id: int | None = None,
        invalid_snapshot: bool = False,
        fail_after_destructive_update: bool = False,
        recovery_should_fail: bool = False,
        recovery_old_image: str | None = None,
        recovery_message: str | None = None,
    ):
        super().__init__(runtime_connection)
        self.current_image: str = current_image
        self.invalid_target_key = invalid_target_key
        self.invalid_target_value = invalid_target_value
        self.invalid_target_message = invalid_target_message
        self.include_runtime_type_in_snapshot = include_runtime_type_in_snapshot
        self.storage = storage
        self.executor_id = executor_id
        self.invalid_snapshot = invalid_snapshot
        self.snapshot_seen_before_update = None
        self.fail_after_destructive_update = fail_after_destructive_update
        self.recovery_should_fail = recovery_should_fail
        self.recovery_old_image = recovery_old_image
        self.recovery_message = recovery_message
        self.update_calls: list[str] = []
        self.recovery_calls: list[dict] = []

    async def discover_targets(self):
        return []

    async def validate_target_ref(self, target_ref):
        if self.invalid_target_key is not None and (
            target_ref.get(self.invalid_target_key) == self.invalid_target_value
        ):
            raise ValueError(self.invalid_target_message)

    async def get_current_image(self, target_ref) -> str:
        return self.current_image

    async def capture_snapshot(self, target_ref, current_image: str):
        if self.invalid_snapshot:
            return {}
        snapshot = {"image": current_image, "target_ref": target_ref}
        if self.include_runtime_type_in_snapshot:
            snapshot["runtime_type"] = self.runtime_connection.type
        return snapshot

    async def validate_snapshot(self, target_ref, snapshot):
        if not isinstance(snapshot, dict) or not snapshot:
            raise ValueError("snapshot must be a non-empty dict")
        if snapshot.get("target_ref") != target_ref:
            raise ValueError("snapshot target_ref mismatch")

    async def update_image(self, target_ref, new_image: str):
        if self.storage is not None and self.executor_id is not None:
            snapshot = await self.storage.get_executor_snapshot(self.executor_id)
            self.snapshot_seen_before_update = snapshot

        self.update_calls.append(new_image)
        if self.fail_after_destructive_update:
            self.current_image = "broken:partial"
            raise RuntimeMutationError(
                "simulated update failure after destructive steps",
                destructive_started=True,
            )

        if new_image == self.current_image:
            return RuntimeUpdateResult(
                updated=False,
                old_image=self.current_image,
                new_image=new_image,
            )

        old_image = self.current_image
        self.current_image = new_image
        return RuntimeUpdateResult(updated=True, old_image=old_image, new_image=new_image)

    async def recover_from_snapshot(self, target_ref, snapshot):
        self.recovery_calls.append(snapshot)
        if self.recovery_should_fail:
            raise RuntimeError("simulated recovery failure")

        recovered_image = snapshot.get("image")
        if not isinstance(recovered_image, str):
            raise ValueError("snapshot.image must be a string")

        self.current_image = recovered_image
        return RuntimeUpdateResult(
            updated=True,
            old_image=self.recovery_old_image,
            new_image=recovered_image,
            message=self.recovery_message,
        )
