from datetime import datetime

import aiosqlite
import pytest

from releasetracker.models import AggregateTracker, TrackerSource


@pytest.mark.asyncio
async def test_credentials_crud(authed_client, storage):
    """测试凭证的 CRUD 操作"""

    # 1. 创建凭证
    response = authed_client.post(
        "/api/credentials",
        json={
            "name": "gh-token",
            "type": "github",
            "token": "ghp_1234567890abcdef",
            "description": "GitHub Token",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    cred_id = data["id"]

    # 2. 获取列表
    response = authed_client.get("/api/credentials")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    items = data["items"]
    # 验证 token 是否脱敏
    created_item = next(i for i in items if i["id"] == cred_id)
    assert created_item["name"] == "gh-token"
    assert "****" in created_item["token"] or "..." in created_item["token"]
    assert created_item["token"] != "ghp_1234567890abcdef"

    # 3. 获取详情
    response = authed_client.get(f"/api/credentials/{cred_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == cred_id
    # 详情也应脱敏
    assert "****" in detail["token"] or "..." in detail["token"]

    # 4. 更新凭证
    response = authed_client.put(
        f"/api/credentials/{cred_id}",
        json={"description": "Updated Description", "token": "ghp_new_token_value"},
    )
    assert response.status_code == 200

    # 验证更新是否生效（通过 storage 直接查，或再次 get）
    updated_cred = await storage.get_credential(cred_id)
    assert updated_cred.description == "Updated Description"
    assert updated_cred.token == "ghp_new_token_value"

    # 5. 删除凭证
    response = authed_client.delete(f"/api/credentials/{cred_id}")
    assert response.status_code == 200

    # 验证删除
    response = authed_client.get(f"/api/credentials/{cred_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_duplicate_credential(authed_client):
    """测试重复创建凭证"""
    # 第一次创建
    authed_client.post(
        "/api/credentials", json={"name": "unique-cred", "type": "github", "token": "123"}
    )

    # 第二次创建同名
    response = authed_client.post(
        "/api/credentials", json={"name": "unique-cred", "type": "github", "token": "456"}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_runtime_credential_secrets_are_masked(authed_client, storage):
    response = authed_client.post(
        "/api/credentials",
        json={
            "name": "k8s-runtime",
            "type": "kubernetes_runtime",
            "secrets": {"kubeconfig": "apiVersion: v1\nclusters: []\ncontexts: []\n"},
        },
    )
    assert response.status_code == 200, response.text
    credential_id = response.json()["id"]

    detail_response = authed_client.get(f"/api/credentials/{credential_id}")
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["type"] == "kubernetes_runtime"
    assert detail["secret_keys"] == ["kubeconfig"]
    assert detail["secrets"]["kubeconfig"] != "apiVersion: v1\nclusters: []\ncontexts: []\n"

    runtime_response = authed_client.post(
        "/api/runtime-connections",
        json={
            "name": "k8s-with-runtime-credential",
            "type": "kubernetes",
            "config": {"context": "prod"},
            "credential_id": credential_id,
        },
    )
    assert runtime_response.status_code == 200, runtime_response.text

    references_response = authed_client.get(f"/api/credentials/{credential_id}/references")
    assert references_response.status_code == 200, references_response.text
    references_payload = references_response.json()
    assert references_payload["deletable"] is False
    assert references_payload["counts"]["runtime_connections"] == 1
    assert (
        references_payload["references"]["runtime_connections"][0]["name"]
        == "k8s-with-runtime-credential"
    )

    delete_response = authed_client.delete(f"/api/credentials/{credential_id}")
    assert delete_response.status_code == 409
    assert delete_response.json()["detail"]["counts"]["runtime_connections"] == 1
    assert (
        delete_response.json()["detail"]["references"]["runtime_connections"][0]["name"]
        == "k8s-with-runtime-credential"
    )

    stored = await storage.get_credential(credential_id)
    assert stored.secrets["kubeconfig"] == "apiVersion: v1\nclusters: []\ncontexts: []\n"


@pytest.mark.asyncio
async def test_credential_references_include_tracker_sources_and_legacy_trackers(
    authed_client, storage
):
    response = authed_client.post(
        "/api/credentials",
        json={"name": "shared-source-token", "type": "github", "token": "ghp_shared"},
    )
    assert response.status_code == 200, response.text
    credential_id = response.json()["id"]

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            """
            INSERT INTO trackers (name, type, enabled, repo, credential_name, channels, interval, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?, '[]', 60, ?, ?)
            """,
            (
                "legacy-credential-tracker",
                "github",
                "owner/legacy-credential-tracker",
                "shared-source-token",
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        await db.commit()
    await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-credential-tracker",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_config={"repo": "owner/aggregate-credential-tracker"},
                    credential_name="shared-source-token",
                )
            ],
        )
    )

    references_response = authed_client.get(f"/api/credentials/{credential_id}/references")
    assert references_response.status_code == 200, references_response.text
    payload = references_response.json()

    assert payload["deletable"] is False
    assert payload["counts"]["aggregate_tracker_sources"] >= 1
    assert payload["counts"]["trackers"] >= 1
    assert any(
        item["tracker_name"] == "aggregate-credential-tracker" and item["name"] == "repo"
        for item in payload["references"]["aggregate_tracker_sources"]
    )
    assert any(
        item["name"] == "legacy-credential-tracker" for item in payload["references"]["trackers"]
    )

    delete_response = authed_client.delete(f"/api/credentials/{credential_id}")
    assert delete_response.status_code == 409


@pytest.mark.asyncio
async def test_credential_can_be_deleted_after_references_are_removed(authed_client, storage):
    response = authed_client.post(
        "/api/credentials",
        json={"name": "removable-source-token", "type": "github", "token": "ghp_removable"},
    )
    assert response.status_code == 200, response.text
    credential_id = response.json()["id"]

    await storage.create_aggregate_tracker(
        AggregateTracker(
            name="removable-credential-tracker",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_config={"repo": "owner/removable-credential-tracker"},
                    credential_name="removable-source-token",
                )
            ],
        )
    )

    blocked_response = authed_client.delete(f"/api/credentials/{credential_id}")
    assert blocked_response.status_code == 409

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            "UPDATE aggregate_tracker_sources SET credential_name = NULL, updated_at = ? WHERE credential_name = ?",
            (datetime.now().isoformat(), "removable-source-token"),
        )
        await db.commit()

    references_response = authed_client.get(f"/api/credentials/{credential_id}/references")
    assert references_response.status_code == 200, references_response.text
    assert references_response.json()["deletable"] is True

    delete_response = authed_client.delete(f"/api/credentials/{credential_id}")
    assert delete_response.status_code == 200, delete_response.text
    assert await storage.get_credential(credential_id) is None
