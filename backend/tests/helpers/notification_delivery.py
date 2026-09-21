"""Explicitly drive asynchronous delivery in isolated notification integration tests."""

from releasetracker.services.executor_notification_outbox import ExecutorNotificationOutbox


async def deliver_notifications(storage):
    outbox = ExecutorNotificationOutbox(storage)
    await outbox.initialize()
    try:
        assert await outbox.expand_one()
        assert await outbox.deliver_one()
    finally:
        await outbox.shutdown()
