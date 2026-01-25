"""SQLite 存储模块"""

import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite

from ..models import Release, ReleaseStats, TrackerStatus



logger = logging.getLogger(__name__)


class SQLiteStorage:
    """SQLite 数据库存储"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """初始化数据库"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracker_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    tag_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    url TEXT NOT NULL,
                    prerelease INTEGER DEFAULT 0,
                    body TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(tracker_name, tag_name)
                )
                """
            )
            
            # 检查 body 列是否存在，如果不存在则添加（迁移逻辑）
            cursor = await db.execute("PRAGMA table_info(releases)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if 'body' not in column_names:
                await db.execute("ALTER TABLE releases ADD COLUMN body TEXT")
            
            
            # 检查 commit_sha 列是否存在，如果不存在则添加
            if 'commit_sha' not in column_names:
                await db.execute("ALTER TABLE releases ADD COLUMN commit_sha TEXT")
            
            # 检查 republish_count 列是否存在，如果不存在则添加
            if 'republish_count' not in column_names:
                await db.execute("ALTER TABLE releases ADD COLUMN republish_count INTEGER DEFAULT 0")
            
            # 检查 channel_name 列是否存在，如果不存在则添加
            if 'channel_name' not in column_names:
                await db.execute("ALTER TABLE releases ADD COLUMN channel_name TEXT")

            
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tracker_status (
                    name TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    last_check TEXT,
                    last_version TEXT,
                    error TEXT
                )
                """
            )
            
            # 凭证表
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    type TEXT NOT NULL,
                    token TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            
            # 追踪器配置表
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS trackers (
                    name TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    repo TEXT,
                    project TEXT,
                    instance TEXT,
                    chart TEXT,
                    credential_name TEXT,
                    channels TEXT DEFAULT '[]',
                    interval TEXT DEFAULT '1h',
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            
            # 版本历史表
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS release_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id INTEGER NOT NULL,
                    name TEXT,
                    commit_sha TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    body TEXT,
                    channel_name TEXT,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE
                )
                """
            )
            
            # 检查 release_history 表是否有 name 列
            cursor = await db.execute("PRAGMA table_info(release_history)")
            hist_columns = await cursor.fetchall()
            hist_column_names = [col[1] for col in hist_columns]
            
            if 'name' not in hist_column_names:
                await db.execute("ALTER TABLE release_history ADD COLUMN name TEXT")

            # 检查 release_history 表是否有 channel_name 列
            if 'channel_name' not in hist_column_names:
                await db.execute("ALTER TABLE release_history ADD COLUMN channel_name TEXT")
            
            await db.commit()

    async def save_tracker_config(self, config) -> None:
        """保存追踪器配置(新增或更新)"""
        import json
        from ..config import Channel
        
        # 序列化 channels 为 JSON
        if config.channels:
            # 过滤掉 None 值并序列化
            valid_channels = [ch for ch in config.channels if ch is not None]
            channels_json = json.dumps([ch.model_dump() for ch in valid_channels])
        else:
            channels_json = '[]'
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO trackers 
                (name, type, enabled, repo, project, instance, chart, credential_name, channels, interval, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config.name,
                    config.type,
                    1 if config.enabled else 0,
                    config.repo,
                    config.project,
                    config.instance,
                    config.chart,
                    config.credential_name,
                    channels_json,
                    config.interval,
                    config.description if hasattr(config, 'description') else None,
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

            
    async def get_all_tracker_configs(self) -> list:
        """Get all tracker configs."""
        from ..config import TrackerConfig, Channel
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM trackers")
            rows = await cursor.fetchall()
            return [self._row_to_tracker_config(row) for row in rows]

    async def get_tracker_config(self, name: str):
        """Get tracker config."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM trackers WHERE name = ?", (name,))
            row = await cursor.fetchone()
            return self._row_to_tracker_config(row) if row else None

    async def delete_tracker_config(self, name: str) -> None:
        """Delete tracker config."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM trackers WHERE name = ?", (name,))
            await db.commit()

    @staticmethod
    def _row_to_tracker_config(row):
        """Convert row to TrackerConfig."""
        from ..config import TrackerConfig, Channel
        import json
        
        # 加载 channels
        channels = []
        if "channels" in row.keys() and row["channels"]:
            try:
                channels_data = json.loads(row["channels"])
                valid_channels = []
                for ch in channels_data:
                    # 兼容旧数据格式
                    # 如果 type 是旧的枚举值 (stable/prerelease/beta/canary)
                    old_type = ch.get("type")
                    if old_type in ["stable", "prerelease", "beta", "canary"]:
                        ch["name"] = old_type
                        # 推断新的 type
                        if old_type == "stable":
                            ch["type"] = "release"
                        elif old_type == "prerelease":
                            ch["type"] = "prerelease"
                        elif old_type == "beta":
                            ch["type"] = "release"
                        elif old_type == "canary":
                            ch["type"] = "prerelease"
                            
                    elif old_type == "custom":
                        # 处理中文名称
                        name_map = {
                            "正式版": "stable", "Stable": "stable",
                            "预发布版": "prerelease", "Prerelease": "prerelease",
                            "测试版": "beta", "Beta": "beta",
                            "金丝雀版": "canary", "Canary": "canary"
                        }
                        raw_name = ch.get("name")
                        if raw_name in name_map:
                            ch["name"] = name_map[raw_name]
                            # 设置合理的 type 默认值
                            if ch["name"] in ["stable", "beta"]:
                                ch["type"] = "release"
                            else:
                                ch["type"] = "prerelease"
                        else:
                            # 无法识别的自定义名称，尝试直接使用 raw_name 如果它符合 Literal
                            if raw_name in ["stable", "prerelease", "beta", "canary"]:
                                ch["name"] = raw_name
                                ch["type"] = "release" if raw_name in ["stable", "beta"] else "prerelease"
                            else:
                                continue # 跳过不支持的渠道

                    try:
                        valid_channels.append(Channel(**ch))
                    except Exception:
                        pass
                
                channels = valid_channels
            except (json.JSONDecodeError, Exception):
                pass  # 如果解析失败，使用空列表
        
        return TrackerConfig(
            name=row["name"],
            type=row["type"],
            enabled=bool(row["enabled"]),
            repo=row["repo"],
            project=row["project"],
            instance=row["instance"],
            chart=row["chart"],
            credential_name=row["credential_name"],
            interval=row["interval"] or "1h",
            channels=channels,
        )
    @staticmethod
    def _row_to_tracker_status(row) -> TrackerStatus:
        """Convert row to TrackerStatus."""
        return TrackerStatus(
            name=row["name"],
            type=row["type"],
            enabled=bool(row["enabled"]),
            last_check=datetime.fromisoformat(row["last_check"]) if row["last_check"] else None,
            last_version=row["last_version"],
            error=row["error"],
        )

    async def save_release(self, release: Release) -> dict:
        """
        保存版本信息
        
        返回值：
        {
            "is_new": bool,        # 是否为新版本
            "is_republish": bool,  # 是否为重新发布
            "old_commit": str      # 旧的 commit SHA（如果是重新发布）
        }
        """
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    """
                    INSERT INTO releases 
                    (tracker_name, name, tag_name, version, published_at, url, prerelease, body, channel_name, commit_sha, republish_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        release.tracker_name,
                        release.name,
                        release.tag_name,
                        release.version,
                        release.published_at.isoformat(),
                        release.url,
                        1 if release.prerelease else 0,
                        release.body,
                        release.channel_name,
                        release.commit_sha,
                        datetime.now().isoformat(),
                    ),
                )
                await db.commit()
                return {"is_new": True, "is_republish": False, "old_commit": None}
            except aiosqlite.IntegrityError:
                # 已存在，检查是否为重新发布
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """
                    SELECT id, name, commit_sha, published_at, body, republish_count
                    FROM releases 
                    WHERE tracker_name=? AND tag_name=?
                    """,
                    (release.tracker_name, release.tag_name)
                )
                old_record = await cursor.fetchone()
                
                if not old_record:
                    return {"is_new": False, "is_republish": False, "old_commit": None}
                
                old_commit_sha = old_record["commit_sha"]
                old_published_at = old_record["published_at"]
                is_republish = False
                
                # 仅当 Commit SHA 改变时才视为重新发布
                # 如果只是修改 release notes 等元数据，不算重新发布
                if release.commit_sha and old_commit_sha and old_commit_sha != release.commit_sha:
                    is_republish = True
                
                if is_republish:
                    # 保存旧状态到历史表
                    await db.execute(
                        """
                        INSERT INTO release_history 
                        (release_id, name, commit_sha, published_at, body, recorded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            old_record["id"],
                            old_record["name"],
                            old_record["commit_sha"],
                            old_record["published_at"],
                            old_record["body"],
                            datetime.now().isoformat()
                        )
                    )
                    
                    # 更新主表，增加 republish_count
                    await db.execute(
                        """
                        UPDATE releases SET
                        name=?, version=?, published_at=?, url=?, prerelease=?, body=?, channel_name=?, commit_sha=?, republish_count=?
                        WHERE tracker_name=? AND tag_name=?
                        """,
                        (
                            release.name,
                            release.version,
                            release.published_at.isoformat(),
                            release.url,
                            1 if release.prerelease else 0,
                            release.body,
                            release.channel_name,
                            release.commit_sha,
                            old_record["republish_count"] + 1,
                            release.tracker_name,
                            release.tag_name,
                        ),
                    )
                else:
                    # 仅更新元数据（如 body, channel_name）
                    await db.execute(
                        """
                        UPDATE releases SET
                        name=?, version=?, published_at=?, url=?, prerelease=?, body=?, channel_name=?, commit_sha=?
                        WHERE tracker_name=? AND tag_name=?
                        """,
                        (
                            release.name,
                            release.version,
                            release.published_at.isoformat(),
                            release.url,
                            1 if release.prerelease else 0,
                            release.body,
                            release.channel_name,
                            release.commit_sha or old_commit_sha,  # 如果新的为空，保留旧的
                            release.tracker_name,
                            release.tag_name,
                        ),
                    )
                
                await db.commit()
                return {
                    "is_new": False, 
                    "is_republish": is_republish,
                    "old_commit": old_commit_sha if is_republish else None
                }


    async def get_releases(
        self, 
        tracker_name: str | None = None, 
        skip: int = 0,
        limit: int = 50,
        search: str | None = None,
        prerelease: bool | None = None,
        include_history: bool = True
    ) -> list[Release]:
        """Get release list with optional history."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # 构建查询条件和参数（为当前版本）
            current_conditions = []
            current_params = []
            
            if tracker_name:
                current_conditions.append("tracker_name = ?")
                current_params.append(tracker_name)
            
            if search:
                current_conditions.append("(tracker_name LIKE ? OR name LIKE ? OR tag_name LIKE ? OR version LIKE ?)")
                search_pattern = f"%{search}%"
                current_params.extend([search_pattern] * 4)
            
            if prerelease is not None:
                current_conditions.append("prerelease = ?")
                current_params.append(1 if prerelease else 0)
            
            current_where = " AND ".join(current_conditions) if current_conditions else "1=1"
            
            if include_history:
                # 构建历史版本的查询条件（使用表前缀）
                hist_conditions = []
                hist_params = []
                
                if tracker_name:
                    hist_conditions.append("r.tracker_name = ?")
                    hist_params.append(tracker_name)
                
                if search:
                    hist_conditions.append("(r.tracker_name LIKE ? OR r.name LIKE ? OR r.tag_name LIKE ? OR r.version LIKE ?)")
                    search_pattern = f"%{search}%"
                    hist_params.extend([search_pattern] * 4)
                
                if prerelease is not None:
                    hist_conditions.append("r.prerelease = ?")
                    hist_params.append(1 if prerelease else 0)
                
                hist_where = " AND ".join(hist_conditions) if hist_conditions else "1=1"
                
                # 合并当前版本和历史版本
                query = f"""
                WITH current_releases AS (
                    SELECT 
                        id, tracker_name, name, tag_name, version,
                        published_at, url, prerelease, body, channel_name,
                        commit_sha, republish_count, created_at,
                        0 as is_historical
                    FROM releases
                    WHERE {current_where}
                ),
                historical_releases AS (
                    SELECT 
                        r.id, r.tracker_name, COALESCE(h.name, r.name) as name, r.tag_name, r.version,
                        h.published_at, r.url, r.prerelease, h.body, r.channel_name,
                        h.commit_sha, r.republish_count, h.recorded_at as created_at,
                        1 as is_historical
                    FROM release_history h
                    JOIN releases r ON h.release_id = r.id
                    WHERE {hist_where}
                )
                SELECT * FROM (
                    SELECT * FROM current_releases
                    UNION ALL
                    SELECT * FROM historical_releases
                )
                ORDER BY published_at DESC 
                LIMIT ? OFFSET ?
                """
                params = current_params + hist_params + [limit, skip]
            else:
                # 仅当前版本
                query = f"""
                    SELECT 
                        id, tracker_name, name, tag_name, version,
                        published_at, url, prerelease, body, channel_name,
                        commit_sha, republish_count, created_at,
                        0 as is_historical
                    FROM releases 
                    WHERE {current_where}
                    ORDER BY published_at DESC 
                    LIMIT ? OFFSET ?
                """
                params = current_params + [limit, skip]
            
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
            return [self._row_to_release(row) for row in rows]

    async def get_total_count(
        self,
        tracker_name: str | None = None,
        search: str | None = None,
        prerelease: bool | None = None,
        include_history: bool = True
    ) -> int:
        """Get total count of records matching criteria."""
        async with aiosqlite.connect(self.db_path) as db:
            # 构建查询条件
            current_conditions = []
            current_params = []
            
            if tracker_name:
                current_conditions.append("tracker_name = ?")
                current_params.append(tracker_name)
            
            if search:
                current_conditions.append("(tracker_name LIKE ? OR name LIKE ? OR tag_name LIKE ? OR version LIKE ?)")
                search_pattern = f"%{search}%"
                current_params.extend([search_pattern] * 4)
            
            if prerelease is not None:
                current_conditions.append("prerelease = ?")
                current_params.append(1 if prerelease else 0)
            
            current_where = " AND ".join(current_conditions) if current_conditions else "1=1"
            
            if include_history:
                # 构建历史版本的查询条件
                hist_conditions = []
                hist_params = []
                
                if tracker_name:
                    hist_conditions.append("r.tracker_name = ?")
                    hist_params.append(tracker_name)
                
                if search:
                    hist_conditions.append("(r.tracker_name LIKE ? OR r.name LIKE ? OR r.tag_name LIKE ? OR r.version LIKE ?)")
                    search_pattern = f"%{search}%"
                    hist_params.extend([search_pattern] * 4)
                
                if prerelease is not None:
                    hist_conditions.append("r.prerelease = ?")
                    hist_params.append(1 if prerelease else 0)
                
                hist_where = " AND ".join(hist_conditions) if hist_conditions else "1=1"
                
                query = f"""
                SELECT COUNT(*) FROM (
                    SELECT id FROM releases WHERE {current_where}
                    UNION ALL
                    SELECT r.id FROM release_history h
                    JOIN releases r ON h.release_id = r.id
                    WHERE {hist_where}
                )
                """
                params = current_params + hist_params
            else:
                query = f"SELECT COUNT(*) FROM releases WHERE {current_where}"
                params = current_params
            
            cursor = await db.execute(query, tuple(params))
            result = await cursor.fetchone()
            return result[0] if result else 0

    async def get_latest_release(self, tracker_name: str) -> Release | None:
        """Get latest release for a tracker."""
        releases = await self.get_releases(tracker_name, limit=1)
        return releases[0] if releases else None

    async def get_latest_release_for_channels(self, tracker_name: str, channels: list) -> Release | None:
        """Get latest release across all enabled channels for a tracker."""
        import re
        
        if not channels:
            return await self.get_latest_release(tracker_name)
        
        # 只考虑启用的渠道
        enabled_channels = [ch for ch in channels if ch.enabled]
        if not enabled_channels:
            return await self.get_latest_release(tracker_name)
        
        # 获取所有版本
        all_releases = await self.get_releases(tracker_name, limit=100)
        if not all_releases:
            return None
        
        # 对每个渠道，找到符合规则的最新版本
        channel_latest_releases = []
        
        for channel in enabled_channels:
            # 根据渠道类型过滤
            filtered_releases = []
            
            for release in all_releases:
                # 1. 根据渠道类型过滤
                if channel.type == "stable" and release.prerelease:
                    continue
                elif channel.type == "prerelease" and not release.prerelease:
                    continue
                # custom 类型不根据 prerelease 过滤
                
                # 2. 应用 include_pattern
                if channel.include_pattern:
                    try:
                        if not re.search(channel.include_pattern, release.tag_name):
                            continue
                    except re.error:
                        pass  # 正则表达式错误，跳过该规则
                
                # 3. 应用 exclude_pattern
                if channel.exclude_pattern:
                    try:
                        if re.search(channel.exclude_pattern, release.tag_name):
                            continue
                    except re.error:
                        pass  # 正则表达式错误，跳过该规则
                
                filtered_releases.append(release)
            
            # 获取该渠道的最新版本（已按 published_at 降序排列）
            if filtered_releases:
                channel_latest_releases.append(filtered_releases[0])
        
        # 如果没有找到任何符合条件的版本
        if not channel_latest_releases:
            return None
        
        # 在所有渠道的最新版本中，选择发布日期最近的
        # 为了避免时区比较问题，使用时间戳进行比较
        return max(channel_latest_releases, key=lambda r: r.published_at.timestamp())


    async def update_tracker_status(self, status: TrackerStatus):
        """Update tracker status."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO tracker_status 
                (name, type, enabled, last_check, last_version, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    status.name,
                    status.type,
                    1 if status.enabled else 0,
                    status.last_check.isoformat() if status.last_check else None,
                    status.last_version,
                    status.error,
                ),
            )
            await db.commit()

    async def get_tracker_status(self, name: str) -> TrackerStatus | None:
        """Get tracker status."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tracker_status WHERE name = ?", (name,)
            )
            row = await cursor.fetchone()
            return self._row_to_tracker_status(row) if row else None

    async def get_all_tracker_status(self) -> list[TrackerStatus]:
        """Get all tracker statuses."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tracker_status")
            rows = await cursor.fetchall()
            return [self._row_to_tracker_status(row) for row in rows]

    async def delete_tracker_status(self, name: str):
        """Delete tracker status."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM tracker_status WHERE name = ?", (name,))
            await db.commit()

    async def delete_releases_by_tracker(self, tracker_name: str):
        """Delete all releases associated with a tracker."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM releases WHERE tracker_name = ?", (tracker_name,))
            await db.commit()

    async def get_stats(self) -> ReleaseStats:
        """Get statistics."""
        async with aiosqlite.connect(self.db_path) as db:
            # 总追踪器数
            cursor = await db.execute("SELECT COUNT(*) FROM tracker_status")
            total_trackers = (await cursor.fetchone())[0]

            # 总版本数（包含历史版本）
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT id FROM releases
                    UNION ALL
                    SELECT r.id FROM release_history h
                    JOIN releases r ON h.release_id = r.id
                )
                """
            )
            total_releases = (await cursor.fetchone())[0]

            # 最近24小时版本数
            yesterday = (datetime.now() - timedelta(days=1)).isoformat()
            cursor = await db.execute(
                "SELECT COUNT(*) FROM releases WHERE created_at > ?", (yesterday,)
            )
            recent_releases = (await cursor.fetchone())[0]

            # 最新更新时间
            cursor = await db.execute(
                "SELECT MAX(published_at) FROM releases"
            )
            latest_update_str = (await cursor.fetchone())[0]
            latest_update = (
                datetime.fromisoformat(latest_update_str) if latest_update_str else None
            )

            # 每日发布统计（过去7天，包括当前版本和历史版本的发布日期）
            # 每日发布统计 - Python 处理版 (支持时区转换)
            # 获取目标时区
            target_tz_name = os.getenv("TZ", "UTC")
            try:
                target_tz = ZoneInfo(target_tz_name)
            except Exception:
                target_tz = ZoneInfo("UTC")

            # 计算时间范围：获取过去10天的数据以确保覆盖足够
            # 这里的 cutoff 是 UTC 时间
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

            cursor = await db.execute(
                """
                SELECT published_at, channel_name, prerelease 
                FROM (
                    -- 当前版本
                    SELECT published_at, channel_name, prerelease
                    FROM releases 
                    WHERE published_at >= ?
                    
                    UNION ALL
                    
                    -- 历史版本
                    SELECT rh.published_at, r.channel_name, r.prerelease
                    FROM release_history rh
                    JOIN releases r ON rh.release_id = r.id
                    WHERE rh.published_at >= ?
                      AND EXISTS (SELECT 1 FROM releases r WHERE r.id = rh.release_id)
                ) AS all_published
                ORDER BY published_at ASC
                """,
                (cutoff_date, cutoff_date)
            )
            raw_rows = await cursor.fetchall()
            
            # 在 Python 中进行时区转换和分组
            stats_map = {}
            
            # 计算目标时区的"今天"和"7天前"
            now_target = datetime.now(target_tz)
            today_target = now_target.date()
            start_date_target = today_target - timedelta(days=6) # 包含今天共7天
            
            for row in raw_rows:
                pub_str = row[0]
                # 使用 channel_name 而非 channel_name
                channel = row[1]
                if not channel:
                    # 回退逻辑：根据 prerelease 状态推断类型
                    channel = "prerelease" if row[2] else "stable"
                
                try:
                    # 解析 UTC 时间 (ISO 格式)
                    # fromisoformat 处理 +00:00 格式通常需要 Python 3.11+ 或特定格式
                    # 如果存储的是标准 ISO 8601，可以直接解析
                    pub_dt = datetime.fromisoformat(pub_str)
                    
                    # 转换为目标时区
                    if pub_dt.tzinfo is None:
                         # 如果数据库存的是 naive UTC (没有时区信息的 UTC)，先设为 UTC
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    
                    local_dt = pub_dt.astimezone(target_tz)
                    local_date = local_dt.date()
                    
                    # 过滤掉不需要的日期范围
                    if local_date < start_date_target or local_date > today_target:
                        continue
                        
                    date_str = local_date.isoformat()
                    
                    if date_str not in stats_map:
                        stats_map[date_str] = {}
                    
                    stats_map[date_str][channel] = stats_map[date_str].get(channel, 0) + 1
                    
                except Exception as e:
                    logger.error(f"Error processing date {pub_str}: {e}")
                    continue

            # 填充主要日期（确保图表连续，即使某天没数据）
            # 虽然前端图表可能处理，但后端保证数据完整更好
            current_loop_date = start_date_target
            while current_loop_date <= today_target:
                d_str = current_loop_date.isoformat()
                if d_str not in stats_map:
                    stats_map[d_str] = {}  # 空日期，没有任何发布
                current_loop_date += timedelta(days=1)

            daily_stats = [
                {"date": date, "channels": channels}
                for date, channels in stats_map.items()
            ]
            daily_stats.sort(key=lambda x: x['date'])

            # 计算各渠道总版本数 (Releases per Channel - All Time)
            # 包含 release_history，逻辑应与 total_releases 一致
            cursor = await db.execute(
                """
                SELECT 
                    CASE 
                        WHEN channel_name IS NOT NULL AND channel_name != '' THEN channel_name
                        WHEN prerelease = 1 THEN 'prerelease'
                        ELSE 'stable'
                    END as ch,
                    COUNT(*)
                FROM (
                    -- 当前版本
                    SELECT channel_name, prerelease FROM releases
                    UNION ALL
                    -- 历史版本 (关联获取 prerelease)
                    SELECT h.channel_name, r.prerelease
                    FROM release_history h
                    JOIN releases r ON h.release_id = r.id
                )
                GROUP BY ch
                """
            )
            channel_rows = await cursor.fetchall()
            
            channel_stats = {}
            for row in channel_rows:
                ch_name = row[0]
                count = row[1]
                channel_stats[ch_name] = count

            # 按发布类型统计（正式版 vs 预发布版），包含历史版本
            cursor = await db.execute(
                """
                SELECT 
                    prerelease,
                    COUNT(*) as count
                FROM (
                    -- 当前版本
                    SELECT id, prerelease FROM releases
                    UNION ALL
                    -- 历史版本
                    SELECT r.id, r.prerelease
                    FROM release_history h
                    JOIN releases r ON h.release_id = r.id
                )
                GROUP BY prerelease
                """
            )
            type_rows = await cursor.fetchall()
            # 返回英文标识符，前端可根据语言设置翻译
            release_type_stats = {}
            for row in type_rows:
                prerelease_flag = row[0]
                count = row[1]
                type_name = 'prerelease' if prerelease_flag else 'stable'
                release_type_stats[type_name] = count

            return ReleaseStats(
                total_trackers=total_trackers,
                total_releases=total_releases,
                recent_releases=recent_releases,
                latest_update=latest_update,
                daily_stats=daily_stats,
                channel_stats=channel_stats,
                release_type_stats=release_type_stats,
            )


    @staticmethod
    def _row_to_release(row) -> Release:
        """Convert row to Release."""
        return Release(
            id=row["id"],
            tracker_name=row["tracker_name"],
            name=row["name"],
            tag_name=row["tag_name"],
            version=row["version"],
            published_at=datetime.fromisoformat(row["published_at"]),
            url=row["url"],
            prerelease=bool(row["prerelease"]),
            body=row["body"],
            channel_name=row["channel_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )



    # ==================== 凭证管理 ====================
    
    async def create_credential(self, credential) -> int:
        """Create credential."""
        from ..models import Credential
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                INSERT INTO credentials (name, type, token, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    credential.name,
                    credential.type,
                    credential.token,
                    credential.description,
                    credential.created_at.isoformat(),
                    credential.updated_at.isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_all_credentials(self) -> list:
        """Get all credentials."""
        from ..models import Credential
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM credentials ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [self._row_to_credential(row) for row in rows]

    async def get_credential(self, credential_id: int):
        """Get credential by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM credentials WHERE id = ?", (credential_id,)
            )
            row = await cursor.fetchone()
            return self._row_to_credential(row) if row else None

    async def get_credential_by_name(self, name: str):
        """Get credential by name."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM credentials WHERE name = ?", (name,)
            )
            row = await cursor.fetchone()
            return self._row_to_credential(row) if row else None

    async def update_credential(self, credential_id: int, credential) -> bool:
        """Update credential."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE credentials
                SET type = ?, token = ?, description = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    credential.type,
                    credential.token,
                    credential.description,
                    datetime.now().isoformat(),
                    credential_id,
                ),
            )
            await db.commit()
            return True

    async def delete_credential(self, credential_id: int) -> bool:
        """Delete credential."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
            await db.commit()
            return True

    @staticmethod
    def _row_to_credential(row):
        """Convert row to Credential object."""
        from ..models import Credential
        
        return Credential(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            token=row["token"],
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
