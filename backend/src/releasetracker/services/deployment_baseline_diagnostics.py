"""Finite baseline diagnostics; raw SDK messages and configuration never leave here."""


def artifact_reason(task, evidence):
    from .deployment_version_evidence import targets_verified
    from .deployment_baseline import chart_digest

    if targets_verified(task, evidence):
        return None
    targets = task["payload"].get("targets") or []
    if evidence.recovery == "helm_revision":
        current = evidence.configuration.get("current_chart_version")
        if not current:
            return "chart_version_unavailable"
        if any(t.get("chart_version") != current for t in targets):
            return "chart_version_mismatch"
        actual = evidence.configuration.get("current_chart_digest")
        if any(t.get("chart_digest") for t in targets):
            if not actual:
                return "chart_receipt_unavailable"
            if any(
                t.get("chart_digest") and chart_digest(t["chart_digest"]) != chart_digest(actual)
                for t in targets
            ):
                return "chart_digest_mismatch"
    return "artifact_unverified"


def inspection_reason(exc):
    import asyncio

    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "inspection_timeout"
    return "inspection_failed"
