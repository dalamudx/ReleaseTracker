"""通知器基类"""

from abc import ABC, abstractmethod

from ..models import Release


class NotificationEvent:
    """通知事件"""

    NEW_RELEASE = "new_release"
    REPUBLISH = "republish"
    ERROR = "error"


class BaseNotifier(ABC):
    """通知器抽象基类"""

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.config = kwargs

    @abstractmethod
    async def notify(self, event: str, release: Release):
        """发送通知"""
        pass
