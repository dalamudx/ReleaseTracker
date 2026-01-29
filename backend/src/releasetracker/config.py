"""配置管理模块"""

from pathlib import Path
from typing import Literal


from pydantic import BaseModel, Field, field_validator







class Channel(BaseModel):
    """发布渠道配置"""
    
    # 渠道名称：用于显示和国际化（4个固定选项）
    name: Literal["stable", "prerelease", "beta", "canary"]
    
    # 平台类型过滤（可选）：只包含 release 或 pre-release
    # None 表示包含两者
    type: Literal["release", "prerelease"] | None = None
    
    # 包含模式（正则表达式）- 只包含匹配此规则的版本
    include_pattern: str | None = None
    
    # 排除模式（正则表达式）- 排除匹配此规则的版本（优先级高于包含）
    exclude_pattern: str | None = None
    
    # 是否启用此渠道
    enabled: bool = True
    



class TrackerConfig(BaseModel):
    """追踪器配置"""

    name: str
    type: Literal["github", "gitlab", "helm"]
    enabled: bool = True
    repo: str | None = None  # GitHub: "owner/repo"
    instance: str | None = None  # GitLab 实例地址
    project: str | None = None  # GitLab: "group/project"
    chart: str | None = None  # Helm chart 名称
    interval: int = 360  # 检查间隔 (分钟)
    credential_name: str | None = None  # 凭证名称引用 (替代直接存储 token)
    
    # 多渠道配置
    channels: list[Channel] = Field(default_factory=list)


class NotifierConfig(BaseModel):
    """通知器配置"""

    name: str
    type: Literal["webhook", "email"]
    url: str | None = None
    events: list[str] = Field(default_factory=lambda: ["new_release"])





