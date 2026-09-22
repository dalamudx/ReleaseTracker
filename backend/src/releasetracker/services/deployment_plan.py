"""Immutable, data-only deployment admission contract; no remote side effects.

Runtime inspectors supply authoritative identities and managed configuration only.
Status, counters and controller-owned fields must not participate in evidence.
Raw configuration (which can contain secrets) never leaves this module.
"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

PREFIX = "releasetracker.io/"
MARKER_KEYS = (PREFIX + "managed-by", PREFIX + "target-id", PREFIX + "schema")
MANAGED_MARKERS: ContextVar[dict[str, str] | None] = ContextVar(
    "managed_deployment_markers", default=None
)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class TargetEvidence:
    runtime_identity: str
    target_identity: str
    configuration: dict
    markers: tuple[dict, ...]
    recovery: str

    def __post_init__(self):
        if not self.runtime_identity or not self.target_identity:
            raise ValueError("target_identity_unavailable")
        if not self.configuration:
            raise ValueError("target_evidence_unavailable")
        if self.recovery not in {
            "container_config",
            "compose_config",
            "stack_config",
            "workload_images",
            "helm_revision",
            "ssh_files",
        }:
            raise ValueError("recovery_not_supported")

    @property
    def identity_key(self) -> str:
        return fingerprint([self.runtime_identity, self.target_identity])

    @property
    def evidence_hash(self) -> str:
        return fingerprint(self.configuration)

    def ownership_reason(self, installation_id: str, target_id: str) -> str | None:
        missing = not self.markers
        for marker in self.markers:
            owner = marker.get(MARKER_KEYS[0])
            target = marker.get(MARKER_KEYS[1])
            schema = marker.get(MARKER_KEYS[2])
            if owner and owner != installation_id:
                return "foreign_owner"
            if target and target != target_id:
                return "target_marker_conflict"
            if schema and schema != "1":
                return "marker_schema_unsupported"
            missing |= not (owner and target and schema)
        return "marker_missing" if missing else None


def managed_markers(installation_id: str, target_id: str, task_id: int) -> dict[str, str]:
    return dict(zip(MARKER_KEYS, (installation_id, target_id, "1"))) | {
        PREFIX + "deployment-id": str(task_id)
    }


def plan_fingerprint(task: dict, evidence: TargetEvidence) -> str:
    return fingerprint(
        {
            "schema": 1,
            "task_id": task["id"],
            "config_identity": task["payload"]["config_identity"],
            "targets": task["payload"].get("targets", []),
            "identity": evidence.identity_key,
            "evidence": evidence.evidence_hash,
            "markers": sorted(
                (dict(m) for m in evidence.markers), key=lambda m: json.dumps(m, sort_keys=True)
            ),
            "recovery": evidence.recovery,
        }
    )


def public_summary(task: dict, evidence: TargetEvidence) -> dict:
    """Allowlist, not redaction: never expose runtime config, URLs or env values."""
    return {
        "schema": 1,
        "target_label": task["target_label"],
        "identity_key": evidence.identity_key,
        "configuration_fingerprint": evidence.evidence_hash,
        "recovery_scope": evidence.recovery,
        "includes_application_data": False,
        "automatic_rollback": False,
        "source_count": len(task["payload"].get("targets", [])),
    }
