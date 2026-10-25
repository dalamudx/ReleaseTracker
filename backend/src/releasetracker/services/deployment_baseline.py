"""Baseline completion is independent of the remote deployment outcome."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BaselineResult:
    status: Literal["verified", "pending", "review_required", "superseded"]
    reason: str | None = None

    def __bool__(self):
        return self.status == "verified"


def configured_target(target):
    """Remove only runtime receipts, keeping operator-selected target coordinates."""
    target = dict(target or {})
    if target.get("container_name"):
        target.pop("container_id", None)
    if target.get("mode") == "helm_release":
        for key in ("chart_version", "chart_digest", "chart_revision", "app_version"):
            target.pop(key, None)
    return target


def chart_digest(value):
    """Helm index digests identify SHA256 chart archives, not OCI manifests."""
    import re

    if not isinstance(value, str):
        return None
    value = value.strip().lower().removeprefix("sha256:")
    return "sha256:" + value if re.fullmatch(r"[0-9a-f]{64}", value) else None
