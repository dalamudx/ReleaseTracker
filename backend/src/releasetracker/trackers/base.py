"""追踪器基类"""

from abc import ABC, abstractmethod

from ..models import Release
import logging

logger = logging.getLogger(__name__)


class BaseTracker(ABC):
    """追踪器抽象基类"""

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.config = kwargs

    @abstractmethod
    async def fetch_latest(self) -> Release | None:
        """获取最新版本"""
        pass

    @abstractmethod
    async def fetch_all(self, limit: int = 10) -> list[Release]:
        """获取所有版本（限制数量）"""
        pass

    def _should_include(self, release: Release) -> bool:
        """判断是否应包含该版本（根据过滤规则）"""
        # 优先使用 channels 配置
        channels_data = self.config.get("channels", [])
        if channels_data:
            for ch in channels_data:
                # 兼容 Pydantic 对象或字典
                if hasattr(ch, "enabled") and not ch.enabled:
                    continue
                if isinstance(ch, dict) and not ch.get("enabled", True):
                    continue
                if self.should_include_in_channel(release, ch):
                    return True
            return False

        filter_config = self.config.get("filter", {})

        # 步骤1: 预发布版本过滤
        if not filter_config.get("include_prerelease", False):
            # 1. 检查 GitHub/GitLab API 标记的 prerelease 状态
            if release.prerelease:
                return False
            
            # 2. 检查版本号中的预发布关键字 (alpha, beta, rc, pre, etc.)
            version_lower = release.version.lower()
            prerelease_keywords = ["alpha", "beta", "rc", "pre", "dev", "snapshot"]
            
            for keyword in prerelease_keywords:
                if keyword in version_lower:
                    return False

        # 步骤2: 包含模式筛选（include_pattern）
        # 如果定义了包含模式，版本必须匹配才能通过
        include_pattern = filter_config.get("include_pattern")
        if include_pattern:
            import re
            try:
                # 使用完整匹配（search），允许部分匹配
                if not re.search(include_pattern, release.tag_name):
                    return False
            except re.error as e:
                # 正则表达式错误，记录并跳过此规则
                logger.error(f"Invalid include_pattern regex: {include_pattern}, error: {e}")
                pass

        # 步骤3: 排除模式筛选（exclude_pattern）
        # 如果定义了排除模式，匹配的版本将被排除（优先级高于包含）
        exclude_pattern = filter_config.get("exclude_pattern")
        if exclude_pattern:
            import re
            try:
                # 如果匹配排除模式，立即排除
                if re.search(exclude_pattern, release.tag_name):
                    return False
            except re.error as e:
                # 正则表达式错误，记录并跳过此规则
                logger.error(f"Invalid exclude_pattern regex: {exclude_pattern}, error: {e}")
                pass

        return True

    def filter_by_channels(self, releases: list[Release]) -> dict[str, list[Release]]:
        """
        按渠道筛选版本
        
        Args:
            releases: 所有可用的版本列表
            
        Returns:
            字典，key 为渠道标识（name 或 type），value 为筛选后的版本列表
        """
        from ..config import Channel
        
        channels = self.config.get("channels", [])
        result = {}
        
        for channel in channels:
            if isinstance(channel, dict):
                channel = Channel(**channel)
            
            if not channel.enabled:
                continue
            
            filtered = []
            for release in releases:
                if self.should_include_in_channel(release, channel):
                    filtered.append(release)
            
            # 使用渠道名称或类型作为 key
            channel_key = channel.name or channel.type
            result[channel_key] = filtered
        
        return result

    def should_include_in_channel(self, release: Release, channel) -> bool:
        """
        判断版本是否属于该渠道
        
        Args:
            release: 版本信息
            channel: 渠道配置（Channel 对象或字典）
            
        Returns:
            True 如果版本属于该渠道，False 否则
        """
        from ..config import Channel
        import re
        
        if isinstance(channel, dict):
            channel = Channel(**channel)
        
        # 步骤1: 根据平台类型过滤（type）
        if channel.type is not None:
            if channel.type == "release":
                # 只包含 release 类型（prerelease=False）
                if release.prerelease:
                    return False
            elif channel.type == "prerelease":
                # 只包含 pre-release 类型（prerelease=True）
                if not release.prerelease:
                    return False
        
        # 步骤2: 应用包含模式
        if channel.include_pattern:
            try:
                if not re.search(channel.include_pattern, release.tag_name):
                    return False
            except re.error as e:
                logger.error(f"Invalid include_pattern regex for channel '{channel.name}': {channel.include_pattern}, error: {e}")
                pass
        
        # 步骤3: 应用排除模式（优先级最高）
        if channel.exclude_pattern:
            try:
                if re.search(channel.exclude_pattern, release.tag_name):
                    return False
            except re.error as e:
                logger.error(f"Invalid exclude_pattern regex for channel '{channel.name}': {channel.exclude_pattern}, error: {e}")
                pass
        
        return True

