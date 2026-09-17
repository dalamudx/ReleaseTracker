import json

import pytest

from releasetracker.services.repository_webhooks import event_reason, normalize_event


def action_payload(run):
    return {
        "action": "success",
        "run": {
            "id": 1738,
            "status": "success",
            "prettyref": "dev",
            "workflow_id": "Release.yml",
            "repository": {"html_url": "https://code.example/acme/app"},
            **run,
        },
    }


@pytest.mark.parametrize("provider", ["forgejo", "gitea"])
@pytest.mark.parametrize(
    "run,expected",
    [
        ({"ref": "refs/heads/dev"}, "dev"),
        ({"event": "push", "event_payload": json.dumps({"ref": "refs/heads/dev"})}, "dev"),
        ({"event": "push", "event_payload": {"ref": "refs/heads/dev"}}, "dev"),
        ({"ref": "dev", "event_payload": json.dumps({"ref": "refs/heads/dev"})}, "dev"),
        ({"event_payload": {"ref": "dev", "ref_type": "branch"}}, "dev"),
        ({"event": "workflow_dispatch", "event_payload": {"ref": "refs/heads/dev"}}, "dev"),
        ({"event_payload": {"ref": "refs/heads/release/3.6"}}, "release/3.6"),
        ({"prettyref": "display-only", "event_payload": {"ref": "refs/heads/dev"}}, "dev"),
        ({"event_payload": {"ref": "refs/heads/main"}}, "main"),
        ({}, ""),
        ({"ref": "dev", "event": "push"}, ""),
        ({"event_payload": {"ref": "dev"}}, ""),
        ({"ref": "refs/tags/dev"}, ""),
        ({"event_payload": {"ref": "refs/tags/dev"}}, ""),
        ({"event_payload": {"ref": "dev", "ref_type": "tag"}}, ""),
        ({"ref": "refs/heads/dev", "event_payload": {"ref": "dev", "ref_type": "tag"}}, ""),
        ({"ref": "refs/heads/dev", "event_payload": {"ref": "refs/heads/main"}}, ""),
        ({"ref": "main", "event_payload": {"ref": "refs/heads/dev"}}, ""),
        ({"ref": "refs/tags/dev", "event_payload": {"ref": "refs/heads/dev"}}, ""),
        ({"event_payload": {"ref": "refs/pull/22/head"}, "prettyref": "#22"}, ""),
        ({"event": "pull_request", "event_payload": {"ref": "refs/heads/dev"}}, ""),
        ({"trigger_event": "pull_request_target", "ref": "refs/heads/dev"}, ""),
        ({"is_fork_pull_request": True, "ref": "refs/heads/dev"}, ""),
        ({"event_payload": {"ref": "refs/heads/dev", "pull_request": {}}}, ""),
        ({"event_payload": "{invalid"}, ""),
        ({"event_payload": '["refs/heads/dev"]'}, ""),
        ({"event_payload": "null"}, ""),
        ({"event_payload": 5}, ""),
        ({"event_payload": {"ref": ["refs/heads/dev"]}}, ""),
        ({"event_payload": {"ref": "refs/heads/"}}, ""),
    ],
)
def test_action_branch_identity(provider, run, expected):
    event = normalize_event(
        provider, {f"x-{provider}-event": "action_run_success"}, action_payload(run)
    )
    assert event.kind == "workflow"
    assert event.branch == expected
    reason = event_reason(
        event, {"workflow_success": True, "branches": ["dev"], "workflows": ["Release.yml"]}
    )
    assert reason == (
        "" if expected == "dev" else "branches_mismatch" if expected else "branches_unavailable"
    )
    if expected:
        assert event.ref == "refs/heads/" + expected


def test_branch_glob_and_workflow_filters_are_preserved():
    event = normalize_event(
        "forgejo",
        {"x-forgejo-event": "action_run"},
        action_payload({"event_payload": json.dumps({"ref": "refs/heads/release/3.6"})}),
    )
    config = {"workflow_success": True, "branches": ["release/*"], "workflows": ["Release.yml"]}
    assert event_reason(event, config) == ""
    config["workflows"] = ["ci.yml"]
    assert event_reason(event, config) == "workflows_mismatch"
    event.workflow = ""
    assert event_reason(event, config) == "workflows_unavailable"
