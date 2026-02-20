"""调度器模块"""

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import TrackerConfig
from .models import TrackerStatus
from .notifiers import WebhookNotifier
from .notifiers.base import BaseNotifier, NotificationEvent
from .storage.sqlite import SQLiteStorage
from .trackers import GitHubTracker, GitLabTracker, HelmTracker
from .trackers.base import BaseTracker

logger = logging.getLogger(__name__)


class ReleaseScheduler:
    """版本检查调度器"""

    def __init__(self, storage: SQLiteStorage):
        self.storage = storage
        self.scheduler = AsyncIOScheduler()
        self.trackers: dict[str, BaseTracker] = {}
        self.notifiers: list[BaseNotifier] = []

    async def initialize(self):
        """初始化调度器"""
        # 从数据库加载追踪器配置
        tracker_configs = await self.storage.get_all_tracker_configs()

        for tracker_config in tracker_configs:
            await self._add_or_update_tracker_job(tracker_config)

        # 从数据库加载通知器
        await self._refresh_notifiers()

    async def _refresh_notifiers(self):
        """刷新通知器列表"""
        self.notifiers = []
        try:
            db_notifiers = await self.storage.get_notifiers()
            for n in db_notifiers:
                if n.enabled and n.type == "webhook":
                    notifier = WebhookNotifier(
                        name=n.name,
                        url=n.url,
                        events=n.events,
                    )
                    self.notifiers.append(notifier)
        except Exception as e:
            logger.error(f"Failed to load notifiers: {e}")

    async def refresh_tracker(self, name: str):
        """刷新单个追踪器（用于配置更新后）"""
        tracker_config = await self.storage.get_tracker_config(name)
        if tracker_config:
            await self._add_or_update_tracker_job(tracker_config)

    async def remove_tracker(self, name: str):
        """移除追踪器"""
        if name in self.trackers:
            del self.trackers[name]

        job_id = f"tracker_{name}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    async def _add_or_update_tracker_job(self, tracker_config):
        """添加或更新追踪器任务"""
        tracker = await self._create_tracker(tracker_config)
        self.trackers[tracker_config.name] = tracker

        # 添加或更新定时任务
        # interval 单位为分钟，转换为秒
        interval_seconds = tracker_config.interval * 60

        job_id = f"tracker_{tracker_config.name}"
        # 如果任务已存在，先移除
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        self.scheduler.add_job(
            self._check_tracker,
            "interval",
            seconds=interval_seconds,
            args=[tracker_config.name],
            id=job_id,
        )

    async def start(self):
        """启动调度器"""
        self.scheduler.start()
        logger.info("Scheduler started")

        # 首次启动时立即检查所有追踪器
        await self.check_all()

    async def check_all(self):
        """检查所有追踪器"""
        tasks = [self._check_tracker(name) for name in self.trackers.keys()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_tracker(self, tracker_name: str):
        """检查单个追踪器"""
        tracker = self.trackers.get(tracker_name)
        if not tracker:
            return None

        # 从数据库获取最新配置，确保状态同步
        tracker_config = await self.storage.get_tracker_config(tracker_name)
        tracker_type = tracker_config.type if tracker_config else "unknown"

        # 如果配置启用了才检查（可选，或者在这里 check enable 状态）
        if tracker_config and not tracker_config.enabled:
            # 如果禁用，更新状态并不执行检查
            status = TrackerStatus(
                name=tracker_name,
                type=tracker_type,
                enabled=False,
                last_check=datetime.now(),
                last_version=None,  # 或者保留上一次的
                error="追踪器已禁用",
            )
            await self.storage.update_tracker_status(status)
            return status

        try:
            # 获取版本列表，以便能分配渠道名称
            releases = await tracker.fetch_all(limit=10)

            if not releases:
                # 降级策略
                single = await tracker.fetch_latest()
                if single:
                    releases = [single]

            latest_version = None

            if releases and tracker_config:
                # 为每个渠道找到匹配的最新版本，并分配渠道名称
                releases_to_save = []

                if tracker_config.channels:
                    for channel in tracker_config.channels:
                        match = next(
                            (r for r in releases if tracker.should_include_in_channel(r, channel)),
                            None,
                        )
                        if match:
                            # 设置渠道名称
                            match.channel_name = channel.name
                            releases_to_save.append(match)
                else:
                    # 无渠道配置时的回退逻辑
                    match = next((r for r in releases if tracker._should_include(r)), None)
                    if match:
                        # 根据 prerelease 推断渠道名称
                        match.channel_name = "prerelease" if match.prerelease else "stable"
                        releases_to_save.append(match)

                # 去重并保存
                unique_releases = {r.version: r for r in releases_to_save}.values()

                for release in unique_releases:
                    result = await self.storage.save_release(release)

                    # 如果是新版本，发送通知
                    if result["is_new"]:
                        logger.info(f"New release found: {tracker_name} -> {release.version}")
                        await self._send_notifications(NotificationEvent.NEW_RELEASE, release)
                    elif result["is_republish"]:
                        old_commit_short = (
                            result["old_commit"][:7] if result["old_commit"] else "unknown"
                        )
                        new_commit_short = (
                            release.commit_sha[:7] if release.commit_sha else "unknown"
                        )
                        logger.info(
                            f"Republish detected: {tracker_name} -> {release.version} (commit: {old_commit_short} → {new_commit_short})"
                        )
                        await self._send_notifications(NotificationEvent.REPUBLISH, release)

                # 选出最新版本
                valid_releases = [r for r in releases if r.published_at]
                if valid_releases:
                    best_release = max(valid_releases, key=lambda r: r.published_at.timestamp())
                    latest_version = best_release.version

                # 更新追踪器状态
                status = TrackerStatus(
                    name=tracker_name,
                    type=tracker_type,
                    enabled=True,
                    last_check=datetime.now(),
                    last_version=latest_version,
                    error=None,
                )
            else:
                status = TrackerStatus(
                    name=tracker_name,
                    type=tracker_type,
                    enabled=True,
                    last_check=datetime.now(),
                    last_version=None,
                    error="未找到版本信息",
                )

            await self.storage.update_tracker_status(status)
            return status

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Check failed for {tracker_name}: {error_msg}")

            # 更新错误状态
            status = TrackerStatus(
                name=tracker_name,
                type=tracker_type,
                enabled=True,
                last_check=datetime.now(),
                last_version=None,
                error=error_msg,
            )
            await self.storage.update_tracker_status(status)
            return status

    async def _send_notifications(self, event: str, release):
        """发送通知（每次实时从数据库加载通知器，避免内存缓存重复发送）"""
        logger.info(
            f"Preparing to send notifications for event: {event}, release: {release.version}"
        )

        active_notifiers = []

        # 直接从数据库实时加载（不使用 self.notifiers 内存缓存，避免重复加载导致重复发送）
        try:
            db_notifiers = await self.storage.get_notifiers()
            logger.debug(f"Found {len(db_notifiers)} notifiers in DB")

            for n in db_notifiers:
                logger.debug(
                    f"Checking notifier: {n.name}, enabled: {n.enabled}, type: {n.type}, events: {n.events}"
                )
                if n.enabled and n.type == "webhook":
                    active_notifiers.append(
                        WebhookNotifier(name=n.name, url=n.url, events=n.events)
                    )
        except Exception as e:
            logger.error(f"Failed to load notifiers from DB: {e}")

        logger.info(f"Active notifiers count: {len(active_notifiers)}")

        if not active_notifiers:
            logger.warning("No active notifiers found to send notification")
            return

        tasks = [notifier.notify(event, release) for notifier in active_notifiers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _create_tracker(self, config: TrackerConfig) -> BaseTracker:
        """创建追踪器实例"""
        # 获取实际 token
        token = None
        if config.credential_name:
            credential = await self.storage.get_credential_by_name(config.credential_name)
            if credential:
                token = credential.token
            else:
                logger.warning(
                    f"Credential '{config.credential_name}' referenced by tracker {config.name} not found, using anonymous access"
                )

        # 从 channels 构建 filter（使用第一个启用的 channel）
        filter_dict = {}
        if config.channels:
            valid_channels = [ch for ch in config.channels if ch and ch.enabled]
            if valid_channels:
                first_channel = valid_channels[0]
                filter_dict = {
                    "include_prerelease": first_channel.type == "prerelease",
                    "include_pattern": first_channel.include_pattern,
                    "exclude_pattern": first_channel.exclude_pattern,
                }

        if config.type == "github":
            return GitHubTracker(
                name=config.name,
                repo=config.repo,
                token=token,
                filter=filter_dict,
                channels=config.channels,
            )
        elif config.type == "gitlab":
            return GitLabTracker(
                name=config.name,
                project=config.project,
                instance=config.instance or "https://gitlab.com",
                token=token,
                filter=filter_dict,
                channels=config.channels,
            )
        elif config.type == "helm":
            return HelmTracker(
                name=config.name,
                repo=config.repo,
                chart=config.chart,
                token=token,
                filter=filter_dict,
                channels=config.channels,
            )
        else:
            raise ValueError(f"不支持的追踪器类型: {config.type}")

    async def check_tracker_now_v2(self, name: str) -> TrackerStatus:
        """立即检查指定追踪器 (V2)"""
        config = await self.storage.get_tracker_config(name)
        if not config:
            raise ValueError(f"Tracker {name} not found")

        try:
            tracker = await self._create_tracker(config)

            # 使用 fetch_all 获取一批数据，以便支持多渠道和历史版本回溯
            # limit=30 是为了覆盖 k8s 这种高频发布场景
            releases = await tracker.fetch_all(limit=30)

            # 如果 fetch_all 为空，尝试降级策略
            if not releases:
                single_latest = await tracker.fetch_latest()
                if single_latest:
                    releases = [single_latest]

            # 获取当前状态以保留 last_version
            current_status = await self.storage.get_tracker_status(name)
            latest_version = current_status.last_version if current_status else None

            if releases:
                releases_to_save = []

                # 这里的逻辑是：只保存"对用户有意义"的版本
                # 即每个渠道的"最新"版本。
                # 避免将列表中的几十个历史版本全部入库。

                if config.channels:
                    for channel in config.channels:
                        # 假定 releases 已经是倒序（API通常如此，或 fetch_all 里可排个序确保）
                        # 找到该渠道下的第一个（也就是最新的）匹配项
                        match = next(
                            (r for r in releases if tracker.should_include_in_channel(r, channel)),
                            None,
                        )
                        if match:
                            # 设置渠道名称
                            match.channel_name = channel.name
                            releases_to_save.append(match)
                else:
                    # 无渠道配置时的回退逻辑：只存一个最新的
                    match = next((r for r in releases if tracker._should_include(r)), None)
                    if match:
                        # 根据 prerelease 推断渠道名称
                        match.channel_name = "prerelease" if match.prerelease else "stable"
                        releases_to_save.append(match)

                # 去重（因为Stable版本可能同时也满足Pre-release规则，或者反之）
                unique_releases = {r.version: r for r in releases_to_save}.values()

                # 保存精选出的版本
                for release in unique_releases:
                    result = await self.storage.save_release(release)

                    if result["is_new"]:
                        logger.info(f"New release found: {name} -> {release.version}")
                        await self._send_notifications(NotificationEvent.NEW_RELEASE, release)
                    elif result["is_republish"]:
                        old_commit_short = (
                            result["old_commit"][:7] if result["old_commit"] else "unknown"
                        )
                        new_commit_short = (
                            release.commit_sha[:7] if release.commit_sha else "unknown"
                        )
                        logger.info(
                            f"Republish detected: {name} -> {release.version} (commit: {old_commit_short} → {new_commit_short})"
                        )
                        await self._send_notifications(NotificationEvent.REPUBLISH, release)

                # 选出最新版本展示 (timestamp 比较)
                valid_releases = [r for r in releases if r.published_at]
                if valid_releases:
                    best_release = max(valid_releases, key=lambda r: r.published_at.timestamp())
                    latest_version = best_release.version

            status = TrackerStatus(
                name=name,
                type=config.type,
                enabled=config.enabled,
                last_check=datetime.now(),
                last_version=latest_version,
                error=None,
                channel_count=len(config.channels) if config.channels else 0,
            )
            await self.storage.update_tracker_status(status)

            return status

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Manual check failed for {name}: {error_msg}")

            # 更新错误状态到数据库
            status = TrackerStatus(
                name=name,
                type=config.type,
                enabled=config.enabled,
                last_check=datetime.now(),
                last_version=None,
                error=error_msg,
                channel_count=len(config.channels) if config.channels else 0,
            )
            await self.storage.update_tracker_status(status)

            # 返回带有错误信息的 status，而不是抛出异常
            return status
