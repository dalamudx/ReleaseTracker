from datetime import datetime

import pytest

from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter

VALID_RUNTIME_CONNECTIONS = [
    {
        "name": "docker-prod",
        "type": "docker",
        "config": {"socket": "unix:///var/run/docker.sock", "tls_verify": False},
        "description": "Docker runtime",
    },
    {
        "name": "podman-prod",
        "type": "podman",
        "config": {"socket": "tcp://podman.example:2375"},
        "description": "Podman runtime",
    },
    {
        "name": "k8s-prod",
        "type": "kubernetes",
        "config": {"context": "production", "namespaces": ["apps"], "in_cluster": True},
        "description": "Kubernetes runtime",
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", VALID_RUNTIME_CONNECTIONS)
async def test_runtime_connections_crud_does_not_store_inline_secrets(
    authed_client, storage, payload
):
    response = authed_client.post(
        "/api/runtime-connections",
        json={**payload, "secrets": {"token": "ignored-runtime-secret"}},
    )
    assert response.status_code == 200
    runtime_connection_id = response.json()["id"]

    response = authed_client.get("/api/runtime-connections")
    assert response.status_code == 200
    listed = next(item for item in response.json()["items"] if item["id"] == runtime_connection_id)
    assert listed["name"] == payload["name"]
    assert listed["type"] == payload["type"]
    assert listed["secrets"] == {}
    assert listed["has_inline_secrets"] is False

    response = authed_client.get(f"/api/runtime-connections/{runtime_connection_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["secrets"] == {}
    assert detail["has_inline_secrets"] is False

    stored = await storage.get_runtime_connection(runtime_connection_id)
    assert stored is not None
    assert stored.config == payload["config"]
    assert stored.secrets == {}

    updated_payload = {
        "description": f"{payload['description']} updated",
        "secrets": {"token": "still-ignored"},
    }
    response = authed_client.put(
        f"/api/runtime-connections/{runtime_connection_id}", json=updated_payload
    )
    assert response.status_code == 200

    updated = await storage.get_runtime_connection(runtime_connection_id)
    assert updated is not None
    assert updated.description == updated_payload["description"]
    assert updated.secrets == {}

    response = authed_client.delete(f"/api/runtime-connections/{runtime_connection_id}")
    assert response.status_code == 200

    response = authed_client.get(f"/api/runtime-connections/{runtime_connection_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            {
                "name": "docker-invalid",
                "type": "docker",
                "config": {},
                "secrets": {"token": "secret"},
            },
            "requires config.socket",
        ),
        (
            {
                "name": "podman-invalid",
                "type": "podman",
                "config": {"socket": "file:///tmp/podman.sock"},
                "secrets": {},
            },
            "config.socket must start with unix://",
        ),
        (
            {
                "name": "podman-ssh-invalid",
                "type": "podman",
                "config": {"socket": "ssh://podman@example"},
                "secrets": {},
            },
            "config.socket must start with unix:// or tcp://",
        ),
        (
            {
                "name": "k8s-invalid",
                "type": "kubernetes",
                "config": {"context": "prod"},
                "secrets": {"kubeconfig": "ignored"},
            },
            "requires credential_id unless config.in_cluster is true",
        ),
        (
            {
                "name": "k8s-unknown",
                "type": "kubernetes",
                "config": {"cluster": "prod"},
                "secrets": {"kubeconfig": "abc"},
            },
            "Unknown config keys: cluster",
        ),
        (
            {
                "name": "portainer-invalid-url",
                "type": "portainer",
                "config": {"base_url": "portainer.local", "endpoint_id": 1},
                "secrets": {"api_key": "secret"},
            },
            "config.base_url must start with http:// or https://",
        ),
        (
            {
                "name": "portainer-invalid-endpoint",
                "type": "portainer",
                "config": {"base_url": "https://portainer.local", "endpoint_id": "1"},
                "secrets": {"api_key": "secret"},
            },
            "config.endpoint_id must be a positive integer",
        ),
        (
            {
                "name": "portainer-missing-credential",
                "type": "portainer",
                "config": {"base_url": "https://portainer.local", "endpoint_id": 1},
                "secrets": {"api_key": "ignored"},
            },
            "requires credential_id",
        ),
    ],
)
async def test_runtime_connections_reject_invalid_payloads(authed_client, payload, expected_error):
    response = authed_client.post("/api/runtime-connections", json=payload)
    assert response.status_code == 400
    assert expected_error in response.json()["detail"]


@pytest.mark.asyncio
async def test_runtime_connection_uses_credential_for_kubernetes_discovery(
    authed_client, monkeypatch
):
    kubeconfig = """
apiVersion: v1
clusters:
  - name: prod
    cluster:
      server: https://k8s.example.com
contexts: []
""".strip()

    class FakeMetadata:
        def __init__(self, name):
            self.name = name

    class FakeNamespace:
        def __init__(self, name):
            self.metadata = FakeMetadata(name)

    class FakeCoreApi:
        def list_namespace(self):
            return type(
                "NamespaceList",
                (),
                {
                    "items": [
                        FakeNamespace("default"),
                        FakeNamespace("apps"),
                    ],
                },
            )()

    monkeypatch.setattr(KubernetesRuntimeAdapter, "_get_core_api", lambda self: FakeCoreApi())

    credential_response = authed_client.post(
        "/api/credentials",
        json={
            "name": "k8s-discovery-credential",
            "type": "kubernetes_runtime",
            "secrets": {"kubeconfig": kubeconfig},
        },
    )
    assert credential_response.status_code == 200, credential_response.text
    credential_id = credential_response.json()["id"]

    create_response = authed_client.post(
        "/api/runtime-connections",
        json={
            "name": "k8s-credential-discovery",
            "type": "kubernetes",
            "config": {"context": "production"},
            "credential_id": credential_id,
            "description": "Kubernetes runtime",
        },
    )
    assert create_response.status_code == 200, create_response.text
    runtime_connection_id = create_response.json()["id"]

    detail_response = authed_client.get(f"/api/runtime-connections/{runtime_connection_id}")
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["uses_credentials"] is True
    assert detail["credential_name"] == "k8s-discovery-credential"
    assert detail["endpoint"] == "https://k8s.example.com"
    assert detail["secrets"] == {}
    assert detail["has_inline_secrets"] is False

    list_response = authed_client.get("/api/runtime-connections")
    assert list_response.status_code == 200, list_response.text
    listed = next(
        item for item in list_response.json()["items"] if item["id"] == runtime_connection_id
    )
    assert listed["endpoint"] == "https://k8s.example.com"
    assert listed["secrets"] == {}

    response = authed_client.post(
        "/api/runtime-connections/discover-kubernetes-namespaces",
        json={"id": runtime_connection_id},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"items": ["apps", "default"]}


@pytest.mark.asyncio
async def test_runtime_connections_discover_kubernetes_namespaces(authed_client, monkeypatch):
    class FakeMetadata:
        def __init__(self, name):
            self.name = name

    class FakeNamespace:
        def __init__(self, name):
            self.metadata = FakeMetadata(name)

    class FakeCoreApi:
        def list_namespace(self):
            return type(
                "NamespaceList",
                (),
                {
                    "items": [
                        FakeNamespace("monitoring"),
                        FakeNamespace("apps"),
                        FakeNamespace("apps"),
                    ],
                },
            )()

    monkeypatch.setattr(KubernetesRuntimeAdapter, "_get_core_api", lambda self: FakeCoreApi())

    credential_response = authed_client.post(
        "/api/credentials",
        json={
            "name": "k8s-inline-discovery-credential",
            "type": "kubernetes_runtime",
            "secrets": {"kubeconfig": "apiVersion: v1\nclusters: []\ncontexts: []\n"},
        },
    )
    assert credential_response.status_code == 200, credential_response.text

    payload = {
        "name": "k8s-discovery",
        "type": "kubernetes",
        "config": {"context": "production"},
        "credential_id": credential_response.json()["id"],
        "description": "Kubernetes runtime",
    }

    response = authed_client.post(
        "/api/runtime-connections/discover-kubernetes-namespaces", json=payload
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"items": ["apps", "monitoring"]}

