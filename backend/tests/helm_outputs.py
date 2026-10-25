"""Helm command output shapes captured from Helm v3.17.3.

These documents mirror real output. `status -o json` has no chart block, `get metadata`
reports the chart name plus chart version and revision, and `list` reports `chart` as
`name-version`. Tests must not invent a richer shape than Helm actually emits: doing so
hides the fallbacks that production depends on.
"""

import json


def status_document(
    revision: int,
    *,
    name: str = "demo",
    namespace: str = "apps",
    status: str = "deployed",
) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "version": revision,
        "info": {"status": status},
        "config": {},
        "manifest": "",
    }


def metadata_document(
    chart: str,
    chart_version: str,
    revision: int,
    *,
    name: str = "demo",
    namespace: str = "apps",
    app_version: str | None = None,
    status: str = "deployed",
) -> dict:
    document = {
        "name": name,
        "namespace": namespace,
        "chart": chart,
        "version": chart_version,
        "revision": revision,
        "status": status,
        "annotations": {},
        "dependencies": [],
        "deployedAt": "2026-01-01T00:00:00Z",
    }
    if app_version:
        document["appVersion"] = app_version
    return document


def list_document(
    chart: str,
    chart_version: str,
    revision: int,
    *,
    name: str = "demo",
    namespace: str = "apps",
    app_version: str | None = None,
    status: str = "deployed",
) -> dict:
    document = {
        "name": name,
        "namespace": namespace,
        "chart": f"{chart}-{chart_version}",
        "revision": str(revision),
        "status": status,
        "updated": "2026-01-01 00:00:00.000000 +0000 UTC",
    }
    if app_version:
        document["app_version"] = app_version
    return document


def respond(args, state, *, name="demo", namespace="apps", metadata=True, listing=True):
    """Answer a Helm command from one release state, in each command's real shape."""
    if args[0] == "status":
        return json.dumps(
            status_document(
                state["revision"],
                name=name,
                namespace=namespace,
                status=state.get("status", "deployed"),
            )
        )
    if args[:2] == ["get", "metadata"]:
        if not metadata:
            return "{}"
        return json.dumps(
            metadata_document(
                state["chart"],
                state["chart_version"],
                state["revision"],
                name=name,
                namespace=namespace,
                app_version=state.get("app_version"),
                status=state.get("status", "deployed"),
            )
        )
    if args[0] == "list":
        if not listing:
            return "[]"
        return json.dumps(
            [
                list_document(
                    state["chart"],
                    state["chart_version"],
                    state["revision"],
                    name=name,
                    namespace=namespace,
                    app_version=state.get("app_version"),
                    status=state.get("status", "deployed"),
                )
            ]
        )
    raise AssertionError(f"unexpected helm command: {args}")
