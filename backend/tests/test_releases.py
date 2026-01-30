import pytest
from datetime import datetime
from releasetracker.models import Release


@pytest.mark.asyncio
async def test_releases_list(authed_client, storage):
    """测试获取版本列表"""

    # 准备数据
    release = Release(
        tracker_name="test-tracker",
        version="v1.0.0",
        name="Release v1.0.0",
        tag_name="v1.0.0",
        channel_name="stable",
        url="http://example.com/v1.0.0",
        published_at=datetime.now(),
        prerelease=False,
    )
    await storage.save_release(release)

    # 测试获取列表
    response = authed_client.get("/api/releases")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["version"] == "v1.0.0"


@pytest.mark.asyncio
async def test_releases_stats(authed_client, storage):
    """测试获取统计信息"""

    # 确保有数据
    release = Release(
        tracker_name="stats-tracker",
        version="v2.0.0",
        name="Release v2.0.0",
        tag_name="v2.0.0",
        channel_name="stable",
        url="http://example.com/v2.0.0",
        published_at=datetime.now(),
        prerelease=False,
    )
    await storage.save_release(release)

    response = authed_client.get("/api/stats")
    assert response.status_code == 200
    stats = response.json()

    # 检查基本字段结构
    assert "total_releases" in stats
    assert "recent_releases" in stats
    assert "daily_stats" in stats
    assert stats["total_releases"] >= 1


@pytest.mark.asyncio
async def test_latest_releases(authed_client, storage):
    """测试获取最新版本"""

    # 插入数据
    release = Release(
        tracker_name="latest-tracker",
        version="v3.0.0",
        name="Release v3.0.0",
        tag_name="v3.0.0",
        channel_name="stable",
        url="http://example.com/v3.0.0",
        published_at=datetime.now(),
        prerelease=False,
    )
    await storage.save_release(release)

    response = authed_client.get("/api/releases/latest")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    # 之前插入了数据，应该能查到
    should_have_data = len(items) > 0
    assert should_have_data
