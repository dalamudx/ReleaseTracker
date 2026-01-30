import pytest


@pytest.mark.asyncio
async def test_settings_crud(authed_client, storage):
    """测试系统设置 CRUD"""

    # 1. 设置值
    response = authed_client.post("/api/settings", json={"key": "TEST_KEY", "value": "test_value"})
    assert response.status_code == 200

    # 2. 获取所有设置
    response = authed_client.get("/api/settings")
    assert response.status_code == 200
    items = response.json()

    found = next((i for i in items if i["key"] == "TEST_KEY"), None)
    assert found is not None
    assert found["value"] == "test_value"

    # 3. 删除设置
    response = authed_client.delete("/api/settings/TEST_KEY")
    assert response.status_code == 200

    # 验证删除
    response = authed_client.get("/api/settings")
    items = response.json()
    found = next((i for i in items if i["key"] == "TEST_KEY"), None)
    assert found is None


@pytest.mark.asyncio
async def test_env_info(authed_client):
    """测试环境变量接口"""
    response = authed_client.get("/api/settings/env")
    assert response.status_code == 200
    info = response.json()

    # 检查是否包含预定义的三大变量
    keys = [item["key"] for item in info]
    assert "ENCRYPTION_KEY" in keys
    assert "LOG_LEVEL" in keys
    assert "TZ" in keys

    # 检查加密密钥是否脱敏
    enc_key = next(i for i in info if i["key"] == "ENCRYPTION_KEY")
    val = enc_key["value"]
    if val != "(Not Set)":
        assert "****" in val or "..." in val


@pytest.mark.asyncio
async def test_system_config(authed_client, storage):
    """测试系统配置聚合接口 (/api/config)"""

    response = authed_client.get("/api/config")
    assert response.status_code == 200
    config = response.json()

    assert "storage" in config
    assert "trackers" in config
    assert "notifiers" in config

    assert config["storage"]["type"] == "sqlite"
