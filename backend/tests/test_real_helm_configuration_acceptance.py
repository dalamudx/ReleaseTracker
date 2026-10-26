"""Opt-in real Helm configuration/receipt checks in one task-owned namespace.

Uses the caller's configured Kubernetes integration only to create/delete its
unique namespace. No existing release is selected. Replicas are zero: this tests
real Helm/API evidence and writes, not application health or registry access.
"""

import hashlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
from threading import Thread
import uuid

import pytest

from releasetracker.config import RuntimeConnectionConfig, normalize_executor_target_ref
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter
from releasetracker.services import deployment_diff, task_effects
from releasetracker.services.snapshot_service import SnapshotRedactor

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RT_RUN_REAL_HELM_TESTS") != "1",
        reason="Real Helm acceptance is explicitly opt-in",
    ),
    pytest.mark.timeout(180),
]


def cli(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=60)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


async def test_real_helm_configuration_and_artifact_receipts(tmp_path):
    namespace = "rt-owned-helm-" + uuid.uuid4().hex[:10]
    repository = tmp_path / "repository"
    repository.mkdir()
    chart = tmp_path / "chart"
    (chart / "templates").mkdir(parents=True)
    (chart / "values.yaml").write_text("innocent: default\n")
    (chart / "templates" / "workload.yaml").write_text("""apiVersion: apps/v1
kind: Deployment
metadata:
  name: proof-web
  labels: {app.kubernetes.io/instance: proof}
  annotations: {meta.helm.sh/release-name: proof}
spec:
  replicas: 0
  selector: {matchLabels: {app: isolated-proof}}
  template:
    metadata: {labels: {app: isolated-proof}}
    spec:
      containers:
        - name: nginx
          image: nginx:1.27-alpine
---
apiVersion: v1
kind: Secret
metadata: {name: proof-only}
stringData:
  password: synthetic-acceptance-secret
""")
    for version in ("0.1.0", "0.2.0"):
        (chart / "Chart.yaml").write_text(
            f"apiVersion: v2\nname: proof-chart\nversion: {version}\n"
        )
        cli("helm", "package", str(chart), "--destination", str(repository))
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(repository)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    repo_url = f"http://127.0.0.1:{server.server_port}"
    cli("helm", "repo", "index", str(repository), "--url", repo_url)
    created = False
    guard_calls = []
    owner_token = task_effects._MUTATION_OWNER.set(None)
    marker_token = task_effects.MUTATION_GUARD.set(None)
    expected_token = deployment_diff.EXPECTED_UPDATE_STATE.set(None)
    try:
        cli("kubectl", "create", "namespace", namespace)
        created = True
        cli(
            "helm",
            "install",
            "proof",
            str(repository / "proof-chart-0.1.0.tgz"),
            "--namespace",
            namespace,
            "--set-string",
            "innocent=synthetic-user-value",
        )
        adapter = KubernetesRuntimeAdapter(
            RuntimeConnectionConfig(
                name="isolated-helm",
                type="kubernetes",
                credential_id=1,
                config={"namespace": namespace},
                secrets={
                    "kubeconfig": cli(
                        "kubectl", "config", "view", "--raw", "--minify", "--flatten"
                    ).stdout
                },
            )
        )
        ref = {
            "mode": "helm_release",
            "namespace": namespace,
            "release_name": "proof",
            "chart_name": "proof-chart",
        }
        before = await adapter.capture_helm_release_snapshot(ref)
        assert before["chart_version"] == "0.1.0" and before["revision"] == 1
        assert before["helm_configuration"]["values"] == {"innocent": "synthetic-user-value"}
        assert "synthetic-acceptance-secret" not in json.dumps(
            SnapshotRedactor().redact(before, runtime_type="kubernetes")[0]
        )

        async def guard():
            guard_calls.append("write")

        task_effects.MUTATION_GUARD.set(guard)
        await task_effects.prepare_deployment_mutation()
        with pytest.raises(ValueError, match="digest"):
            await adapter.upgrade_helm_release(
                ref,
                chart_ref="proof-chart",
                chart_version="0.2.0",
                repo_url=repo_url,
                expected_digest="a" * 64,
            )
        assert guard_calls == []
        assert (await adapter.capture_helm_release_snapshot(ref))["revision"] == 1
        digest = hashlib.sha256((repository / "proof-chart-0.2.0.tgz").read_bytes()).hexdigest()
        result = await adapter.upgrade_helm_release(
            ref,
            chart_ref="proof-chart",
            chart_version="0.2.0",
            repo_url=repo_url,
            expected_digest=digest,
        )
        assert result.updated and guard_calls == ["write"]
        after = await adapter.capture_helm_release_snapshot(ref)
        assert after["revision"] == 2 and after["chart_version"] == "0.2.0"
        assert after["helm_configuration"]["values"] == before["helm_configuration"]["values"]
        receipt = normalize_executor_target_ref(
            ref
            | {"chart_version": "0.2.0", "chart_revision": 2, "chart_digest": "sha256:" + digest},
            runtime_type="kubernetes",
        )
        assert await adapter.get_helm_release_digest(receipt) == "sha256:" + digest
        # Build the exact approved pre-state and prove the real native guard accepts it.
        observed, _ = deployment_diff.project(after)
        observed["chart_digest"] = await adapter.get_helm_release_digest(receipt)
        expected = await deployment_diff.observed_state(adapter, receipt, after, observed)
        deployment_diff.EXPECTED_UPDATE_STATE.set(expected)
        await deployment_diff.verify_update_state(adapter, receipt)
        # A real external values write invalidates both the pre-state and saved receipt.
        cli(
            "helm",
            "upgrade",
            "proof",
            str(repository / "proof-chart-0.2.0.tgz"),
            "--namespace",
            namespace,
            "--set-string",
            "innocent=external-change",
        )
        with pytest.raises(ValueError, match="configuration_changed"):
            await deployment_diff.verify_update_state(adapter, receipt)
        assert await adapter.get_helm_release_digest(receipt) is None
    finally:
        deployment_diff.EXPECTED_UPDATE_STATE.reset(expected_token)
        task_effects.MUTATION_GUARD.reset(marker_token)
        task_effects._MUTATION_OWNER.reset(owner_token)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if created:
            cli("kubectl", "delete", "namespace", namespace, "--wait=true", "--timeout=60s")
            gone = cli("kubectl", "get", "namespace", namespace, check=False)
            assert gone.returncode != 0 and "NotFound" in gone.stderr
