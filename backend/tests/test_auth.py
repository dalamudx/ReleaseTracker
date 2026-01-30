import pytest


@pytest.mark.asyncio
async def test_admin_can_register_user(client, auth_service):
    """管理员可以创建新用户"""
    # 1. 确保管理员用户存在
    await auth_service.ensure_admin_user()

    # 2. 管理员登录
    response = client.post("/api/auth/token", data={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    admin_token = response.json()["access_token"]

    # 3. 管理员创建新用户
    response = client.post(
        "/api/auth/register",
        json={"username": "tester", "email": "tester@example.com", "password": "password123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "tester"
    assert "id" in data

    # 4. 新用户可以登录
    response = client.post(
        "/api/auth/token", data={"username": "tester", "password": "password123"}
    )
    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # 5. 新用户获取信息
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["username"] == "tester"


@pytest.mark.asyncio
async def test_register_requires_auth(client):
    """未认证用户不能注册"""
    response = client.post(
        "/api/auth/register",
        json={"username": "unauthorized", "email": "unauth@example.com", "password": "password123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_failure(client):
    """错误的凭证返回 401"""
    response = client.post(
        "/api/auth/token", data={"username": "nonexistent", "password": "wrongpassword"}
    )
    assert response.status_code == 401
