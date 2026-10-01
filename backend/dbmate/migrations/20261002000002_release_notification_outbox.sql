-- migrate:up
-- Durable release notifications: delivery is retried and de-duplicated per
-- notifier instead of being sent inline (and lost) by the fetch task.
CREATE TABLE release_notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notifier_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    release TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','sending','delivered','failed','discarded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL,
    UNIQUE(notifier_id, dedupe_key)
);
CREATE INDEX release_notification_outbox_due ON release_notification_outbox(status, available_at, id);

-- migrate:down
DROP INDEX release_notification_outbox_due;
DROP TABLE release_notification_outbox;
