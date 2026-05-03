"""Notifier base module"""

from abc import ABC, abstractmethod
from typing import Any


class NotificationEvent:
    """Notification events"""

    NEW_RELEASE = "new_release"
    REPUBLISH = "republish"
    EXECUTOR_RUN_SUCCESS = "executor_run_success"
    EXECUTOR_RUN_FAILED = "executor_run_failed"
    EXECUTOR_RUN_SKIPPED = "executor_run_skipped"
    ERROR = "error"


class BaseNotifier(ABC):
    """Notifier abstract base class"""

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.config = kwargs

    @abstractmethod
    async def notify(self, event: str, payload: Any):
        """Send a notification"""
        pass
