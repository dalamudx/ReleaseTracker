"""Guard opt-in CI isolation; parse contracts without launching engines or workflows."""

from pathlib import Path
import re

import pytest
import yaml


@pytest.fixture
def workflow():
    path = Path(__file__).parents[2] / ".github" / "workflows" / "runtime-acceptance.yml"
    return yaml.safe_load(path.read_text())


def test_runtime_workflow_is_manual_and_least_privilege(workflow):
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["dind"]["type"] == "boolean"
    assert inputs["dind"]["default"] is True
    assert inputs["public_registry"]["default"] is False
    assert inputs["portainer"]["type"] == "boolean"
    assert inputs["portainer"]["default"] is False
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False


@pytest.mark.parametrize("job_name", ["dind", "portainer", "public-registry"])
def test_runtime_jobs_are_bounded_fresh_runners_without_persisted_token(workflow, job_name):
    job = workflow["jobs"][job_name]
    assert job["runs-on"] == "ubuntu-24.04"
    assert 0 < job["timeout-minutes"] <= 15
    assert job["if"] == f"inputs.{job_name.replace('-', '_')}"
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    assert "secrets." not in str(job)
    assert "self-hosted" not in str(job)


def test_dind_exact_fixture_and_pinned_cache_share_root_engine(workflow):
    job = workflow["jobs"]["dind"]
    assert re.fullmatch(
        r"data\.forgejo\.org/oci/docker@sha256:[0-9a-f]{64}", job["env"]["DIND_IMAGE"]
    )
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert 'sudo podman pull "$DIND_IMAGE"' in commands
    assert 'sudo podman tag "$DIND_IMAGE" data.forgejo.org/oci/docker:dind' in commands
    assert "sudo env RT_RUN_REAL_DIND_TESTS=1 timeout 420" in commands
    assert "tests/test_real_dind_acceptance.py" in commands
    assert "-o faulthandler_timeout=0" in commands
    assert "pytest -q tests\n" not in commands
    assert "system prune" not in commands
    assert "/var/run/docker.sock" not in commands
    assert "${{ inputs." not in commands


def test_public_probe_preserves_budgets_and_never_enrolls_production_endpoints(workflow):
    job = workflow["jobs"]["public-registry"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "scripts/probe_registry_manifest.py" in commands
    assert "--registry reg.aoodc.com --image fawney19/aether --mode auto" in commands
    assert "--timeout 10 --observation-timeout 240" in commands
    assert "timeout 270" in commands
    assert "RT_RUN_REAL_DIND_TESTS" not in commands
    assert "podman" not in commands
    assert "${{ inputs." not in commands


def test_portainer_fixture_is_independent_opt_in_and_pinned(workflow):
    from real_portainer_fixture import DIND_IMAGE

    job = workflow["jobs"]["portainer"]
    assert job["env"]["DIND_IMAGE"] == DIND_IMAGE
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert 'sudo podman pull "$DIND_IMAGE"' in commands
    assert "sudo env RT_RUN_REAL_PORTAINER_TESTS=1 timeout 600" in commands
    assert "tests/test_real_portainer_acceptance.py" in commands
    assert "/var/run/docker.sock" not in commands
    assert "system prune" not in commands
    assert "RT_RUN_REAL_DIND_TESTS" not in commands
    assert "${{ inputs." not in commands
