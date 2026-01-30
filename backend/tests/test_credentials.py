import pytest


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
